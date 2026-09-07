#!/usr/bin/env bash
# Explicit, reversible live acceptance test for the three Pocket DS TuneD modes.
set -Eeuo pipefail

stress=0
if [[ $# -eq 1 && $1 == --apply ]]; then
    switches=3
elif [[ $# -eq 4 && $1 == --apply && $2 == --stress-100 && $3 == --confirm \
    && $4 == POCKETDS-POWER-100 && ${POCKETDS_ALLOW_POWER_STRESS:-} == YES ]]; then
    switches=100
    stress=1
else
    echo 'usage: power-profile-live.sh --apply [--stress-100 --confirm POCKETDS-POWER-100]' >&2
    echo 'This changes the active TuneD/fan profile temporarily and restores it.' >&2
    echo 'The 100-switch matrix also requires POCKETDS_ALLOW_POWER_STRESS=YES.' >&2
    exit 2
fi
if [[ $EUID -eq 0 ]]; then
    echo 'Run as the desktop user, not root.' >&2
    exit 2
fi

panelctl=${POCKETDS_PANELCTL:-/usr/local/bin/pocketds-panelctl}
tuned_adm=${POCKETDS_TUNED_ADM:-/usr/sbin/tuned-adm}
root=${POCKETDS_TEST_ROOT:-}
active_file="$root/etc/tuned/active_profile"
fan_file="$root/etc/pocketds-fancontrol/profile"

for command in "$panelctl" "$tuned_adm"; do
    [[ -x $command ]] || { echo "required executable missing: $command" >&2; exit 1; }
done
for path in "$active_file" "$fan_file"; do
    [[ -r $path ]] || { echo "required state file missing: $path" >&2; exit 1; }
done

original_profile=$(tr -d '[:space:]' < "$active_file")
case "$original_profile" in
    pocketds-powersave) restore_mode=powersave ;;
    pocketds-balanced) restore_mode=balanced ;;
    pocketds-performance) restore_mode=performance ;;
    *)
        echo "Refusing to replace unmanaged TuneD profile: $original_profile" >&2
        exit 1
        ;;
esac

restore_needed=0
wait_value() {
    local path=$1 expected=$2
    for _attempt in $(seq 1 100); do
        [[ -r $path ]] && [[ $(tr -d '[:space:]' < "$path") == "$expected" ]] && return 0
        sleep 0.1
    done
    echo "timeout waiting for $path to become $expected" >&2
    return 1
}

restore_original() {
    local status=$?
    trap - EXIT INT TERM
    if [[ $restore_needed -eq 1 ]]; then
        if "$panelctl" power "$restore_mode" >/dev/null 2>&1 \
            && wait_value "$active_file" "$original_profile" \
            && "$tuned_adm" verify >/dev/null 2>&1; then
            echo "[restore] $original_profile"
        else
            echo "ERROR: failed to restore $original_profile" >&2
            status=1
        fi
    fi
    exit "$status"
}
trap restore_original EXIT INT TERM

expect_value() {
    local label=$1 path=$2 expected=$3 actual
    [[ -r $path ]] || { echo "FAIL $label: unreadable $path" >&2; return 1; }
    actual=$(tr -d '[:space:]' < "$path")
    if [[ $actual != "$expected" ]]; then
        echo "FAIL $label: expected $expected, got $actual" >&2
        return 1
    fi
}

check_panel_status() {
    local expected=$1 status_json
    status_json=$("$panelctl" status)
    POCKETDS_EXPECTED_POWER="$expected" python3 -c '
import json, os, sys
value = json.load(sys.stdin).get("power_profile")
expected = os.environ["POCKETDS_EXPECTED_POWER"]
if value != expected:
    raise SystemExit(f"Panel reports {value!r}, expected {expected!r}")
' <<< "$status_json"
}

run_mode() {
    local sequence=$1 mode=$2 profile=$3 cpu_governor=$4 cpu0=$5 cpu3=$6 cpu7=$7
    local gpu_governor=$8 gpu_max=$9 fan=${10}
    "$panelctl" power "$mode"
    restore_needed=1
    wait_value "$active_file" "$profile"
    wait_value "$fan_file" "$fan"
    "$tuned_adm" verify >/dev/null
    expect_value cpu-governor "$root/sys/devices/system/cpu/cpufreq/policy0/scaling_governor" "$cpu_governor"
    expect_value cpu0-max "$root/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq" "$cpu0"
    expect_value cpu3-max "$root/sys/devices/system/cpu/cpufreq/policy3/scaling_max_freq" "$cpu3"
    expect_value cpu7-max "$root/sys/devices/system/cpu/cpufreq/policy7/scaling_max_freq" "$cpu7"
    expect_value gpu-governor "$root/sys/class/devfreq/3d00000.gpu/governor" "$gpu_governor"
    expect_value gpu-max "$root/sys/class/devfreq/3d00000.gpu/max_freq" "$gpu_max"
    check_panel_status "$mode"
    echo "[PASS] $sequence/$switches $mode"
}

for ((index = 0; index < switches; index++)); do
    sequence=$((index + 1))
    case $((index % 3)) in
        0) run_mode "$sequence" powersave pocketds-powersave schedutil 1670400 2188800 2476800 simple_ondemand 550000000 quiet ;;
        1) run_mode "$sequence" balanced pocketds-balanced schedutil 2016000 2803200 2956800 simple_ondemand 1000000000 moderate ;;
        2) run_mode "$sequence" performance pocketds-performance performance 2016000 2803200 2956800 performance 1000000000 aggressive ;;
    esac
done

if [[ $stress -eq 1 ]]; then
    echo '[PASS] exact 100-switch matrix; restoration pending EXIT trap'
fi
