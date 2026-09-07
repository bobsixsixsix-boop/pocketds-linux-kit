#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
launcher="$repo_root/components/emulation/pocketds-retroarch"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/home/.local/bin" "$fixture/home/.config/retroarch" \
    "$fixture/bin"
: >"$fixture/home/.config/retroarch/retroarch.cfg"
: >"$fixture/home/.config/retroarch/pocketds.cfg"

cat >"$fixture/bin/supervisor" <<'MOCK'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$@" >"$MOCK_SUPERVISOR_LOG"
printf 'WAYLAND_DISPLAY=%s\n' "${WAYLAND_DISPLAY-UNSET}" >"$MOCK_SUPERVISOR_ENV_LOG"
printf 'DISPLAY=%s\n' "${DISPLAY-UNSET}" >>"$MOCK_SUPERVISOR_ENV_LOG"
MOCK
cat >"$fixture/bin/runtime" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
cat >"$fixture/home/.local/bin/retroarch" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
cat >"$fixture/bin/inhibit" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
chmod 0755 "$fixture/bin/supervisor" "$fixture/bin/runtime" "$fixture/bin/inhibit" \
    "$fixture/home/.local/bin/retroarch"

assert_argv() {
    local log=$1
    shift
    python3 - "$log" "$@" <<'PY'
from pathlib import Path
import sys

actual = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
expected = sys.argv[2:]
assert actual == expected, (actual, expected)
PY
}

common_env=(
    HOME="$fixture/home"
    MOCK_SUPERVISOR_LOG="$fixture/supervisor.log"
    MOCK_SUPERVISOR_ENV_LOG="$fixture/supervisor.env"
    POCKETDS_GAME_SESSION_SUPERVISOR="$fixture/bin/supervisor"
    POCKETDS_GAME_RUNTIME="$fixture/bin/runtime"
)

env "${common_env[@]}" POCKETDS_SKIP_INHIBIT=1 \
    WAYLAND_DISPLAY=wayland-test DISPLAY=:9 \
    "$launcher" 'game with space.rom'
assert_argv "$fixture/supervisor.log" \
    run --backend native --name retroarch --performance -- \
    "$fixture/bin/runtime" -- \
    "$fixture/home/.local/bin/retroarch" --config \
    "$fixture/home/.config/retroarch/retroarch.cfg" --appendconfig \
    "$fixture/home/.config/retroarch/pocketds.cfg" 'game with space.rom'
python3 - "$fixture/supervisor.env" <<'PY'
from pathlib import Path
import sys

assert Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() == [
    "WAYLAND_DISPLAY=wayland-test", "DISPLAY=:9"
]
PY

token=11111111-1111-4111-8111-111111111111
env "${common_env[@]}" POCKETDS_SKIP_INHIBIT=1 \
    POCKETDS_GAME_SESSION_TOKEN="$token" "$launcher" sample.rom
assert_argv "$fixture/supervisor.log" \
    join --token "$token" -- \
    "$fixture/home/.local/bin/retroarch" --config \
    "$fixture/home/.config/retroarch/retroarch.cfg" --appendconfig \
    "$fixture/home/.config/retroarch/pocketds.cfg" sample.rom

env "${common_env[@]}" POCKETDS_SYSTEMD_INHIBIT="$fixture/bin/inhibit" "$launcher"
assert_argv "$fixture/supervisor.log" \
    run --backend native --name retroarch --performance -- \
    "$fixture/bin/inhibit" --what=idle:sleep \
    '--who=Pocket DS RetroArch' '--why=Emulation session in progress' --mode=block \
    "$fixture/bin/runtime" -- \
    "$fixture/home/.local/bin/retroarch" --config \
    "$fixture/home/.config/retroarch/retroarch.cfg" --appendconfig \
    "$fixture/home/.config/retroarch/pocketds.cfg"

env "${common_env[@]}" POCKETDS_SYSTEMD_INHIBIT="$fixture/bin/inhibit" \
    POCKETDS_GAME_SESSION_TOKEN="$token" "$launcher" nested.rom
assert_argv "$fixture/supervisor.log" \
    join --token "$token" -- \
    "$fixture/bin/inhibit" --what=idle:sleep \
    '--who=Pocket DS RetroArch' '--why=Emulation session in progress' --mode=block \
    "$fixture/home/.local/bin/retroarch" --config \
    "$fixture/home/.config/retroarch/retroarch.cfg" --appendconfig \
    "$fixture/home/.config/retroarch/pocketds.cfg" nested.rom

rm "$fixture/home/.config/retroarch/retroarch.cfg"
: >"$fixture/supervisor.log"
set +e
env "${common_env[@]}" POCKETDS_SKIP_INHIBIT=1 "$launcher" >/dev/null 2>&1
status=$?
set -e
[[ $status -ne 0 && ! -s $fixture/supervisor.log ]]

grep -Fqx 'aspect_ratio_index = "21"' \
    "$repo_root/components/emulation/retroarch.cfg"
if grep -Eq '^custom_viewport_' "$repo_root/components/emulation/retroarch.cfg"; then
    echo 'managed RetroArch config must not pin a stale custom viewport' >&2
    exit 1
fi
grep -Fqx 'input_quit_gamepad_combo = "0"' \
    "$repo_root/components/emulation/retroarch-pocketds.cfg"
grep -Fqx 'quit_press_twice = "true"' \
    "$repo_root/components/emulation/retroarch-pocketds.cfg"
grep -Fqx 'ppsspp_internal_resolution = "1440x816"' \
    "$repo_root/components/emulation/PPSSPP.opt"
grep -Fq 'pocketds-game-runtime' "$launcher"
if grep -Fq 'MANGOHUD_CONFIG=' "$launcher"; then
    echo 'RetroArch must use the unified Gamescope observer, not injection' >&2
    exit 1
fi

echo '  [OK] standalone RetroArch owns Gamescope; ES-DE children reuse it directly'
