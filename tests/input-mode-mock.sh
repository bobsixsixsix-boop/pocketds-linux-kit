#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
helper="$repo_root/components/emulation/pocketds-input-mode"
toggle="$repo_root/components/inputplumber/pocketds-toggle-joymouse"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
state="$fixture/device-state"
calls="$fixture/calls"
sys_input="$fixture/sys/class/input"
runtime_state="$fixture/run/pocketds-input-mode"
mkdir -p "$state" "$sys_input/event7/device" "$sys_input/event8/device" \
    "$runtime_state"
: >"$fixture/input-mode.lock"
: >"$calls"
printf '%s\n' /usr/share/inputplumber/profiles/pocketds-joymouse.yaml >"$state/profile"
printf '%s\n' 'mouse keyboard dbus' >"$state/targets"
printf '%s\n' 'Microsoft X-Box One Elite 2 pad' >"$sys_input/event7/device/name"
printf '%s\n' 'InputPlumber Mouse' >"$sys_input/event8/device/name"

mock_busctl="$fixture/busctl"
cat >"$mock_busctl" <<'MOCK'
#!/usr/bin/env bash
set -eu
state=${POCKETDS_MOCK_STATE:?}
operation=$1
shift
printf '%s %s\n' "$operation" "$*" >>"${POCKETDS_MOCK_CALLS:?}"
if [[ $operation == call && ${POCKETDS_MOCK_BLOCK_FIRST_CALL:-0} == 1 && \
      ! -e ${POCKETDS_MOCK_BLOCK_MARKER:?} ]]; then
    : >"$POCKETDS_MOCK_BLOCK_MARKER"
    while [[ ! -e ${POCKETDS_MOCK_BLOCK_RELEASE:?} ]]; do sleep 0.02; done
fi
fail_pattern="$state/fail-pattern"
fail_always_pattern="$state/fail-always-pattern"
if [[ -f $fail_always_pattern ]]; then
    pattern=$(cat "$fail_always_pattern")
    if [[ "$operation $*" == *"$pattern"* ]]; then
        exit 56
    fi
fi
if [[ -f $fail_pattern && ! -e $state/fail-used ]]; then
    pattern=$(cat "$fail_pattern")
    if [[ "$operation $*" == *"$pattern"* ]]; then
        : >"$state/fail-used"
        exit 55
    fi
fi
case "$operation" in
    introspect)
        [[ ${POCKETDS_MOCK_FF_INTROSPECT_FAIL:-0} != 1 ]] || exit 57
        printf '%s\n' 'NAME TYPE SIGNATURE RESULT/VALUE FLAGS'
        if [[ ${POCKETDS_MOCK_FF:-1} == 1 ]]; then
            printf '%s\n' 'org.shadowblip.Output.ForceFeedback interface - - -'
        fi
        ;;
    get-property)
        object_path=$2
        property=$4
        if [[ $property == ProfilePath && \
              ${POCKETDS_MOCK_PROFILE_UNAVAILABLE_CALLS:-0} -gt 0 ]]; then
            counter_file=${POCKETDS_MOCK_PROFILE_COUNTER:?}
            counter=0
            [[ ! -f $counter_file ]] || counter=$(cat "$counter_file")
            counter=$((counter + 1))
            printf '%s\n' "$counter" >"$counter_file"
            if [[ $counter -le $POCKETDS_MOCK_PROFILE_UNAVAILABLE_CALLS ]]; then
                exit 58
            fi
        fi
        profile=$(cat "$state/profile")
        case "$property" in
            ProfilePath) printf 's "%s"\n' "$profile" ;;
            TargetDevices)
                read -r -a targets <"$state/targets"
                printf 'as %d' "${#targets[@]}"
                for target in "${targets[@]}"; do
                    case "$target" in
                        xbox-elite|xbox-series) target=gamepad ;;
                    esac
                    printf ' "/target/%s0"' "$target"
                done
                printf '\n'
                ;;
            DeviceType)
                case "$object_path" in
                    */gamepad*)
                        if [[ ${POCKETDS_MOCK_GENERIC_GAMEPAD_TYPE:-0} == 1 ]]; then
                            printf 's "gamepad"\n'
                        else
                            targets=$(cat "$state/targets")
                            if [[ $targets == *xbox-series* ]]; then
                                printf 's "xbox-series"\n'
                            else
                                printf 's "xbox-elite"\n'
                            fi
                        fi
                        ;;
                    */mouse*) printf 's "mouse"\n' ;;
                    */keyboard*) printf 's "keyboard"\n' ;;
                    */dbus*) printf 's "dbus"\n' ;;
                    *) exit 1 ;;
                esac
                ;;
            Name)
                targets=$(cat "$state/targets")
                case "$object_path" in
                    */gamepad*)
                        if [[ $targets == *xbox-series* ]]; then
                            printf 's "Microsoft Xbox Series S|X Controller"\n'
                        else
                            printf 's "Microsoft X-Box One Elite pad"\n'
                        fi
                        ;;
                    */mouse*) printf 's "InputPlumber Mouse"\n' ;;
                    */keyboard*) printf 's "InputPlumber Keyboard"\n' ;;
                    */dbus*) printf 's "DBus Device"\n' ;;
                    *) exit 1 ;;
                esac
                ;;
            *) exit 1 ;;
        esac
        ;;
    call)
        method=$4
        shift 4
        case "$method" in
            SetTargetDevices)
                [[ $1 == as ]]
                count=$2
                shift 2
                [[ $# -eq $count ]]
                printf '%s\n' "$*" >"$state/targets"
                ;;
            LoadProfilePath)
                [[ $1 == s ]]
                printf '%s\n' "$2" >"$state/profile"
                ;;
            Stop) [[ $# -eq 0 ]] ;;
            *) exit 1 ;;
        esac
        ;;
    *) exit 1 ;;
esac
MOCK

mock_timeout="$fixture/timeout"
cat >"$mock_timeout" <<'MOCK'
#!/usr/bin/env bash
set -eu
[[ ${1:-} == -k ]]
shift 2
shift
exec "$@"
MOCK

mock_uuid="$fixture/uuid"
cat >"$mock_uuid" <<'MOCK'
#!/usr/bin/env bash
set -eu
counter_file=${POCKETDS_MOCK_UUID_COUNTER:?}
counter=0
[[ ! -f $counter_file ]] || counter=$(cat "$counter_file")
counter=$((counter + 1))
printf '%s\n' "$counter" >"$counter_file"
printf '00000000-0000-4000-8000-%012d\n' "$counter"
MOCK

mock_flock="$fixture/flock"
cat >"$mock_flock" <<'MOCK'
#!/usr/bin/env bash
set -eu
if [[ ${1:-} == -n ]]; then
    exit 96
elif [[ ${1:-} == -E ]]; then
    [[ ${2:-} == 75 && ${3:-} == -n && ${4:-} == 9 && $# -eq 4 ]]
    [[ ${POCKETDS_MOCK_FLOCK_ERROR:-0} != 1 ]] || exit 94
    [[ ${POCKETDS_MOCK_FLOCK_BUSY:-0} != 1 ]] || exit 75
    policy=nonblocking
else
    [[ ${1:-} == -w && ${2:-} == 8 && ${3:-} == 9 && $# -eq 3 ]]
    policy=blocking
fi
python3 - "$policy" 9 <<'PY'
import fcntl
import sys

flags = fcntl.LOCK_EX
if sys.argv[1] == "nonblocking":
    flags |= fcntl.LOCK_NB
try:
    fcntl.flock(int(sys.argv[2]), flags)
except BlockingIOError:
    raise SystemExit(1)
PY
MOCK

mock_supervisor="$fixture/game-session-supervisor"
cat >"$mock_supervisor" <<'MOCK'
#!/usr/bin/env bash
set -eu
[[ $1 == stop && $# -eq 1 ]]
[[ -f ${POCKETDS_GAME_SESSION_STATE:?} ]] || exit 0
printf '%s\n' stop >>"${POCKETDS_MOCK_SIGNALS:?}"
MOCK

chmod 0755 "$mock_busctl" "$mock_timeout" "$mock_uuid" "$mock_flock" \
    "$mock_supervisor"

run_helper() {
    POCKETDS_BUSCTL="$mock_busctl" \
    POCKETDS_TIMEOUT="$mock_timeout" \
    POCKETDS_MOCK_STATE="$state" \
    POCKETDS_MOCK_CALLS="$calls" \
    POCKETDS_SYS_INPUT="$sys_input" \
    POCKETDS_SLEEP=true \
    POCKETDS_INPUT_LOCK="$fixture/input-mode.lock" \
    POCKETDS_INPUT_STATE_DIR="$runtime_state" \
    POCKETDS_FLOCK="$mock_flock" \
    POCKETDS_UUID_GENERATOR="$mock_uuid" \
    POCKETDS_MOCK_UUID_COUNTER="$fixture/uuid-counter" \
    POCKETDS_MOCK_BLOCK_MARKER="$fixture/block-marker" \
    POCKETDS_MOCK_BLOCK_RELEASE="$fixture/block-release" \
    POCKETDS_MOCK_PROFILE_UNAVAILABLE_CALLS="${POCKETDS_MOCK_PROFILE_UNAVAILABLE_CALLS:-0}" \
    POCKETDS_MOCK_PROFILE_COUNTER="$fixture/profile-counter" \
    POCKETDS_MOCK_GENERIC_GAMEPAD_TYPE="${POCKETDS_MOCK_GENERIC_GAMEPAD_TYPE:-0}" \
    POCKETDS_MOCK_FF="${POCKETDS_MOCK_FF:-1}" \
    POCKETDS_MOCK_FF_INTROSPECT_FAIL="${POCKETDS_MOCK_FF_INTROSPECT_FAIL:-0}" \
        "$helper" "$@"
}

run_toggle() {
    POCKETDS_INPUT_MODE_CANONICAL="$helper" \
    POCKETDS_GAME_SESSION_STATE="$fixture/game-session.json" \
    POCKETDS_BUSCTL="$mock_busctl" \
    POCKETDS_TIMEOUT="$mock_timeout" \
    POCKETDS_MOCK_STATE="$state" \
    POCKETDS_MOCK_CALLS="$calls" \
    POCKETDS_SYS_INPUT="$sys_input" \
    POCKETDS_SLEEP=true \
    POCKETDS_INPUT_LOCK="$fixture/input-mode.lock" \
    POCKETDS_INPUT_STATE_DIR="$runtime_state" \
    POCKETDS_FLOCK="$mock_flock" \
    POCKETDS_UUID_GENERATOR="$mock_uuid" \
    POCKETDS_MOCK_UUID_COUNTER="$fixture/uuid-counter" \
    POCKETDS_MOCK_BLOCK_MARKER="$fixture/block-marker" \
    POCKETDS_MOCK_BLOCK_RELEASE="$fixture/block-release" \
    POCKETDS_MOCK_GENERIC_GAMEPAD_TYPE="${POCKETDS_MOCK_GENERIC_GAMEPAD_TYPE:-0}" \
    POCKETDS_MOCK_FF="${POCKETDS_MOCK_FF:-1}" \
    POCKETDS_MOCK_FF_INTROSPECT_FAIL="${POCKETDS_MOCK_FF_INTROSPECT_FAIL:-0}" \
    POCKETDS_GAME_SESSION_SUPERVISOR="$mock_supervisor" \
    POCKETDS_MOCK_SIGNALS="$fixture/signals" \
        "$toggle" "$@"
}

assert_state() {
    local mode=$1 expected_profile expected_targets
    case "$mode" in
        gamepad)
            expected_profile=/usr/share/inputplumber/profiles/pocketds-gamepad.yaml
            expected_targets='xbox-elite keyboard dbus'
            ;;
        joymouse)
            expected_profile=/usr/share/inputplumber/profiles/pocketds-joymouse.yaml
            expected_targets='mouse keyboard dbus'
            ;;
        *) return 2 ;;
    esac
    [[ $(cat "$state/profile") == "$expected_profile" ]]
    [[ $(cat "$state/targets") == "$expected_targets" ]]
}

inject_failure() {
    printf '%s\n' "$1" >"$state/fail-pattern"
    rm -f "$state/fail-used"
}

clear_failure() {
    rm -f "$state/fail-pattern" "$state/fail-always-pattern" "$state/fail-used"
}

read_receipt() {
    local receipt=$1
    read -r receipt_protocol receipt_previous receipt_token receipt_extra <<<"$receipt"
    [[ $receipt_protocol == pds-input-v1 && -z ${receipt_extra:-} ]]
    [[ $receipt_token =~ ^[0-9a-f-]{36}$ ]]
}

[[ $(run_helper status) == joymouse ]]

# Listener bootstrap is idempotent in a stable custom mode and must preserve a
# live generation token. A missing state record is repaired without D-Bus writes.
run_helper reconcile
bootstrap_token=$(awk '{print $2}' "$runtime_state/state")
before_bootstrap=$(grep -c '^call ' "$calls" || true)
run_helper bootstrap
[[ $(awk '{print $2}' "$runtime_state/state") == "$bootstrap_token" ]]
[[ $(grep -c '^call ' "$calls" || true) -eq $before_bootstrap ]]
rm -f "$runtime_state/state"
run_helper bootstrap
[[ $(awk '{print $3}' "$runtime_state/state") == joymouse ]]
[[ $(grep -c '^call ' "$calls" || true) -eq $before_bootstrap ]]

# Session-journal recovery keeps the receipt token retryable until its target
# is stable. A failed first repair must retain that token; the same call then
# succeeds after the injected D-Bus fault is removed.
receipt=$(run_helper acquire gamepad)
read_receipt "$receipt"
recovery_token=$receipt_token
printf '%s\n' dbus >"$state/targets"
printf 'pds-input-v1 %s transitioning\n' "$recovery_token" \
    >"$runtime_state/state"
printf '%s\n' 'SetTargetDevices as 1 dbus' >"$state/fail-always-pattern"
if run_helper recover-token "$recovery_token" joymouse \
        >"$fixture/recover-failed.out" 2>/dev/null; then
    echo '  [FAIL] failed journal recovery was accepted' >&2
    exit 1
fi
[[ ! -s $fixture/recover-failed.out ]]
grep -Fqx "pds-input-v1 $recovery_token unknown" "$runtime_state/state"
clear_failure
recovery_outcome=$(run_helper recover-token "$recovery_token" joymouse)
[[ $recovery_outcome == 'pds-input-recover-v1 restored' ]]
assert_state joymouse
[[ $(awk '{print $2}' "$runtime_state/state") != "$recovery_token" ]]

# Retrying after a crash between final state publication and journal removal is
# an idempotent success. A newer explicit other mode supersedes the old journal
# without issuing any D-Bus write.
before_retry=$(grep -c '^call ' "$calls" || true)
retry_outcome=$(run_helper recover-token "$recovery_token" joymouse)
after_retry=$(grep -c '^call ' "$calls" || true)
if [[ $retry_outcome != 'pds-input-recover-v1 already-target' ]] || \
        [[ $before_retry -ne $after_retry ]]; then
    echo '  [FAIL] completed journal retry was not read-only/idempotent' >&2
    exit 1
fi
receipt=$(run_helper acquire gamepad)
read_receipt "$receipt"
superseded_token=$receipt_token
run_helper set gamepad
before_superseded=$(grep -c '^call ' "$calls" || true)
superseded_outcome=$(run_helper recover-token "$superseded_token" joymouse)
after_superseded=$(grep -c '^call ' "$calls" || true)
if [[ $superseded_outcome != 'pds-input-recover-v1 superseded' ]] || \
        [[ $before_superseded -ne $after_superseded ]]; then
    echo '  [FAIL] superseded journal recovery issued a D-Bus write' >&2
    exit 1
fi
assert_state gamepad
run_helper set joymouse

# Only the exact vendor default profile and exact xbox-series/mouse/keyboard/
# dbus target identities may bootstrap into the managed gamepad profile.
printf '%s\n' /usr/share/inputplumber/profiles/default.yaml >"$state/profile"
printf '%s\n' 'xbox-series mouse keyboard dbus' >"$state/targets"
rm -f "$runtime_state/state"

# A generic DeviceType paired with the same friendly controller name is not an
# exact InputPlumber target identity. Reject it before any D-Bus mutation so a
# future schema change cannot silently masquerade as the vendor default.
before_generic_type=$(grep -c '^call ' "$calls" || true)
if POCKETDS_MOCK_GENERIC_GAMEPAD_TYPE=1 run_helper bootstrap \
        >"$fixture/generic-type.out" 2>"$fixture/generic-type.err"; then
    echo '  [FAIL] generic gamepad DeviceType was accepted as vendor default' >&2
    exit 1
fi
after_generic_type=$(grep -c '^call ' "$calls" || true)
if [[ $after_generic_type -ne $before_generic_type ]]; then
    echo '  [FAIL] generic gamepad rejection issued a D-Bus write' >&2
    exit 1
fi
if [[ -e $runtime_state/state ]]; then
    echo '  [FAIL] generic gamepad rejection published managed state' >&2
    exit 1
fi

run_helper bootstrap
assert_state gamepad
[[ $(awk '{print $3}' "$runtime_state/state") == gamepad ]]

# A freshly started InputPlumber can own the systemd unit before its composite
# D-Bus profile is readable. Bootstrap waits through that bounded, read-only
# interval instead of making the listener enter a failed/restart state.
printf '%s\n' /usr/share/inputplumber/profiles/default.yaml >"$state/profile"
printf '%s\n' 'xbox-series mouse keyboard dbus' >"$state/targets"
rm -f "$runtime_state/state" "$fixture/profile-counter"
POCKETDS_MOCK_PROFILE_UNAVAILABLE_CALLS=3 run_helper bootstrap
assert_state gamepad
[[ $(cat "$fixture/profile-counter") -gt 3 ]]

# Ordinary set/toggle remain fail-closed in the unmanaged vendor default state.
printf '%s\n' /usr/share/inputplumber/profiles/default.yaml >"$state/profile"
printf '%s\n' 'xbox-series mouse keyboard dbus' >"$state/targets"
before_default_set=$(grep -c '^call ' "$calls" || true)
if run_helper set joymouse >/dev/null 2>&1; then
    echo '  [FAIL] ordinary set accepted the vendor default profile' >&2
    exit 1
fi
[[ $(grep -c '^call ' "$calls" || true) -eq $before_default_set ]]

# A failed default bootstrap restores the exact queried vendor target set and
# publishes unknown instead of pretending the generation is stable.
inject_failure 'LoadProfilePath s /usr/share/inputplumber/profiles/pocketds-gamepad.yaml'
if run_helper bootstrap >/dev/null 2>&1; then
    echo '  [FAIL] failed default bootstrap was accepted' >&2
    exit 1
fi
[[ $(cat "$state/profile") == /usr/share/inputplumber/profiles/default.yaml ]]
[[ $(cat "$state/targets") == 'xbox-series mouse keyboard dbus' ]]
[[ $(awk '{print $3}' "$runtime_state/state") == unknown ]]
clear_failure
run_helper bootstrap
assert_state gamepad
run_helper set joymouse

# Matching only a /gamepad object path is insufficient: the managed profile
# requires the exact xbox-elite target identity used by Wiliwili's GLFW GUID.
printf '%s\n' /usr/share/inputplumber/profiles/pocketds-gamepad.yaml >"$state/profile"
printf '%s\n' 'xbox-series keyboard dbus' >"$state/targets"
before_wrong_gamepad=$(grep -c '^call ' "$calls" || true)
if run_helper set joymouse >/dev/null 2>&1; then
    echo '  [FAIL] xbox-series target was accepted as managed xbox-elite mode' >&2
    exit 1
fi
[[ $(grep -c '^call ' "$calls" || true) -eq $before_wrong_gamepad ]]
printf '%s\n' /usr/share/inputplumber/profiles/pocketds-joymouse.yaml >"$state/profile"
printf '%s\n' 'mouse keyboard dbus' >"$state/targets"
run_helper bootstrap

# Acquire is one atomic status+transition transaction and returns its lease.
receipt=$(run_helper acquire gamepad)
read_receipt "$receipt"
[[ $receipt_previous == joymouse ]]
assert_state gamepad
grep -Fqx "pds-input-v1 $receipt_token gamepad" "$runtime_state/state"
run_helper restore-token "$receipt_token" gamepad joymouse
assert_state joymouse
restored_token=$(awk '{print $2}' "$runtime_state/state")
[[ $restored_token != "$receipt_token" ]]

# Remove the game target before Stop, then use Stop as an output-queue barrier
# before loading a profile or installing replacement targets.
python3 - "$calls" <<'PY'
from pathlib import Path
import sys

lines = Path(sys.argv[1]).read_text().splitlines()
remove = (
    "call org.shadowblip.InputPlumber "
    "/org/shadowblip/InputPlumber/CompositeDevice0 "
    "org.shadowblip.Input.CompositeDevice SetTargetDevices as 1 dbus"
)
inspect = (
    "introspect org.shadowblip.InputPlumber "
    "/org/shadowblip/InputPlumber/CompositeDevice0"
)
stop = (
    "call org.shadowblip.InputPlumber "
    "/org/shadowblip/InputPlumber/CompositeDevice0 "
    "org.shadowblip.Output.ForceFeedback Stop"
)
load_prefix = (
    "call org.shadowblip.InputPlumber "
    "/org/shadowblip/InputPlumber/CompositeDevice0 "
    "org.shadowblip.Input.CompositeDevice LoadProfilePath"
)
assert any(
    lines[index - 2] == inspect
    and lines[index - 1] == remove
    and lines[index] == stop
    and lines[index + 1].startswith(load_prefix)
    for index in range(2, len(lines) - 1)
), "FF probe/detach/Stop/load ordering is not safe"
PY

# The distro rollback binary has no ForceFeedback interface and still changes
# modes.  If the interface exists but introspection or Stop fails, the same
# transaction must fail and restore the previous stable mode.
before_absent=$(grep -c 'ForceFeedback Stop' "$calls" || true)
POCKETDS_MOCK_FF=0 run_helper set gamepad
after_absent=$(grep -c 'ForceFeedback Stop' "$calls" || true)
[[ $before_absent -eq $after_absent ]]
assert_state gamepad
run_helper set joymouse
printf '%s\n' 'org.shadowblip.Output.ForceFeedback Stop' >"$state/fail-always-pattern"
if run_helper set gamepad >/dev/null 2>&1; then
    echo '  [FAIL] transition accepted a failed force-feedback Stop' >&2
    exit 1
fi
assert_state joymouse
[[ $(awk '{print $3}' "$runtime_state/state") == joymouse ]]
clear_failure
if POCKETDS_MOCK_FF_INTROSPECT_FAIL=1 \
        run_helper set gamepad >/dev/null 2>&1; then
    echo '  [FAIL] transition accepted failed FF introspection' >&2
    exit 1
fi
assert_state joymouse
clear_failure

# A killed acquire can leave InputPlumber between its three D-Bus mutations.
# Possession of the still-current receipt token must repair that exact partial
# generation to the pre-session mode; publishing unknown alone would leave the
# desktop without a usable virtual mouse/gamepad target.
receipt=$(run_helper acquire gamepad)
read_receipt "$receipt"
partial_token=$receipt_token
printf '%s\n' dbus >"$state/targets"
printf 'pds-input-v1 %s transitioning\n' "$partial_token" \
    >"$runtime_state/state"
run_helper restore-token "$partial_token" gamepad joymouse
assert_state joymouse
[[ $(awk '{print $2}' "$runtime_state/state") != "$partial_token" ]]
[[ $(awk '{print $3}' "$runtime_state/state") == joymouse ]]

# Complete ABA: the old token must not write D-Bus after two newer choices end
# back at the same gamepad value.
receipt=$(run_helper acquire gamepad)
read_receipt "$receipt"
aba_token=$receipt_token
run_helper set joymouse
run_helper set gamepad
before_restore=$(wc -l <"$calls" | tr -d '[:space:]')
run_helper restore-token "$aba_token" gamepad joymouse
after_restore=$(wc -l <"$calls" | tr -d '[:space:]')
[[ $before_restore -eq $after_restore ]]
assert_state gamepad

# An explicit same-mode set also invalidates an older lease.
run_helper set joymouse
receipt=$(run_helper acquire gamepad)
read_receipt "$receipt"
same_mode_token=$receipt_token
run_helper set gamepad
run_helper restore-token "$same_mode_token" gamepad joymouse
assert_state gamepad

run_toggle
assert_state joymouse
run_toggle
assert_state gamepad

# Bootstrap calls are inert while a managed ES-DE session owns gamepad mode.
# Logical Guide is also inert: only the explicit second Menu+View chord may
# ask the bounded test backend to stop.
calls_before_session_guard=$(wc -l <"$calls" | tr -d '[:space:]')
printf '%s\n' '{"name":"es-de","phase":"running"}' >"$fixture/game-session.json"
run_toggle
assert_state gamepad
[[ $(wc -l <"$calls" | tr -d '[:space:]') -eq $calls_before_session_guard ]]
[[ ! -e $fixture/signals ]]
run_toggle --guide
[[ ! -e $fixture/signals ]]
run_toggle --exit-game
grep -Fqx -- stop "$fixture/signals"
assert_state gamepad
[[ $(wc -l <"$calls" | tr -d '[:space:]') -eq $calls_before_session_guard ]]
rm -f "$fixture/game-session.json"
rm -f "$fixture/signals"
run_toggle --exit-game
[[ ! -e $fixture/signals ]]
assert_state gamepad

# The actual advisory lock must serialize two writers, not merely satisfy a
# mocked return code. The second transaction cannot issue any bus call while
# the first is deliberately paused after publishing its acquire receipt.
run_helper set joymouse
rm -f "$fixture/block-marker" "$fixture/block-release" "$fixture/receipt"
POCKETDS_MOCK_BLOCK_FIRST_CALL=1 run_helper acquire gamepad \
    >"$fixture/receipt" 2>"$fixture/acquire.err" &
first_pid=$!
for _attempt in $(seq 1 250); do
    [[ -s $fixture/receipt && -e $fixture/block-marker ]] && break
    sleep 0.02
done
[[ -s $fixture/receipt && -e $fixture/block-marker ]]
calls_while_blocked=$(wc -l <"$calls" | tr -d '[:space:]')
run_helper set joymouse >"$fixture/second.out" 2>"$fixture/second.err" &
second_pid=$!
sleep 0.2
[[ $(wc -l <"$calls" | tr -d '[:space:]') -eq $calls_while_blocked ]]
: >"$fixture/block-release"
wait "$first_pid"
wait "$second_pid"
assert_state joymouse

# A failed transition that rolls back still consumes a fresh generation.
run_helper set joymouse
old_token=$(awk '{print $2}' "$runtime_state/state")
inject_failure 'LoadProfilePath s /usr/share/inputplumber/profiles/pocketds-gamepad.yaml'
if run_helper set gamepad >/dev/null 2>&1; then
    echo '  [FAIL] profile-load failure was accepted' >&2
    exit 1
fi
assert_state joymouse
new_token=$(awk '{print $2}' "$runtime_state/state")
[[ $new_token != "$old_token" ]]
grep -Fqx "pds-input-v1 $new_token joymouse" "$runtime_state/state"
clear_failure

# This injected target-detach failure is now rolled back to the prior stable
# joymouse generation; the old unknown-state expectation is obsolete.
printf '%s\n' 'SetTargetDevices as 1 dbus' >"$state/fail-always-pattern"
if run_helper set gamepad >/dev/null 2>&1; then
    echo '  [FAIL] double transition/rollback failure was accepted' >&2
    exit 1
fi
[[ $(awk '{print $3}' "$runtime_state/state") == joymouse ]]
clear_failure
run_helper bootstrap
[[ $(awk '{print $3}' "$runtime_state/state") == joymouse ]]

# Initial instability and unsafe state identities fail before any D-Bus write.
printf '%s\n' dbus >"$state/targets"
before_unstable=$(wc -l <"$calls" | tr -d '[:space:]')
if run_helper set gamepad >/dev/null 2>&1; then
    echo '  [FAIL] unstable initial state was accepted' >&2
    exit 1
fi
after_unstable=$(wc -l <"$calls" | tr -d '[:space:]')
if sed -n "$((before_unstable + 1)),${after_unstable}p" "$calls" | grep -q '^call '; then
    echo '  [FAIL] unstable initial state was mutated' >&2
    exit 1
fi
printf '%s\n' 'mouse keyboard dbus' >"$state/targets"

mv "$runtime_state/state" "$runtime_state/state.real"
ln -s "$runtime_state/state.real" "$runtime_state/state"
before_unsafe=$(wc -l <"$calls" | tr -d '[:space:]')
if run_helper set gamepad >/dev/null 2>&1; then
    echo '  [FAIL] symlinked generation state was accepted' >&2
    exit 1
fi
after_unsafe=$(wc -l <"$calls" | tr -d '[:space:]')
[[ $before_unsafe -eq $after_unsafe ]]
rm -f "$runtime_state/state"
mv "$runtime_state/state.real" "$runtime_state/state"

if run_helper set 'gamepad;touch /tmp/no' >/dev/null 2>&1; then
    echo '  [FAIL] invalid mode accepted' >&2
    exit 1
fi

# The input wrapper intentionally drops a press when the canonical lock is busy.
POCKETDS_MOCK_FLOCK_BUSY=1 run_toggle
set +e
POCKETDS_MOCK_FLOCK_ERROR=1 run_toggle >/dev/null 2>&1
flock_error_status=$?
set -e
[[ $flock_error_status -eq 94 ]]

# The canonical helper must actually route every D-Bus access through a hard
# timeout. This enforcing fixture kills a hung mock and its descendant group.
cat >"$fixture/busctl-hang" <<'MOCK'
#!/usr/bin/env bash
set -eu
(sleep 30) &
child=$!
printf '%s\n' "$child" >"${POCKETDS_MOCK_HANG_CHILD:?}"
wait "$child"
MOCK
cat >"$fixture/timeout-enforcing" <<'MOCK'
#!/usr/bin/env python3
import os
import signal
import subprocess
import sys

assert sys.argv[1] == "-k"
command = sys.argv[4:]
process = subprocess.Popen(command, start_new_session=True)
try:
    raise SystemExit(process.wait(timeout=0.5))
except subprocess.TimeoutExpired:
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=0.2)
    raise SystemExit(124)
MOCK
chmod 0755 "$fixture/busctl-hang" "$fixture/timeout-enforcing"
set +e
POCKETDS_BUSCTL="$fixture/busctl-hang" \
POCKETDS_TIMEOUT="$fixture/timeout-enforcing" \
POCKETDS_MOCK_HANG_CHILD="$fixture/hang-child.pid" \
POCKETDS_INPUT_LOCK="$fixture/input-mode.lock" \
POCKETDS_INPUT_STATE_DIR="$runtime_state" \
POCKETDS_FLOCK="$mock_flock" \
POCKETDS_UUID_GENERATOR="$mock_uuid" \
POCKETDS_MOCK_UUID_COUNTER="$fixture/uuid-counter" \
POCKETDS_SYS_INPUT="$sys_input" \
    "$helper" set gamepad >/dev/null 2>&1
timeout_status=$?
set -e
[[ $timeout_status -ne 0 ]]
if [[ ! -s $fixture/hang-child.pid ]]; then
    echo '  [FAIL] hung D-Bus fixture never reached its descendant probe' >&2
    exit 1
fi
hang_child=$(cat "$fixture/hang-child.pid")
for _attempt in $(seq 1 50); do
    kill -0 "$hang_child" 2>/dev/null || break
    sleep 0.02
done
if kill -0 "$hang_child" 2>/dev/null; then
    echo '  [FAIL] bounded D-Bus call left a hung descendant' >&2
    exit 1
fi

echo '  [OK] canonical InputPlumber writer uses atomic token leases and rejects ABA restore'
