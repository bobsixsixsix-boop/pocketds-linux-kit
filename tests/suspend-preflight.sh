#!/usr/bin/env bash
set -Eeuo pipefail

case "${1:-}" in
    '') ;;
    -h|--help)
        echo 'usage: make test-suspend  # permanently read-only'
        exit 0
        ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
esac

if [[ ! -r /sys/power/state ]]; then
    echo '[suspend] skipped outside Linux/Pocket DS'
    exit 0
fi

failures=0
check() {
    local description=$1
    shift
    if "$@"; then
        echo "  [OK] $description"
    else
        echo "  [FAIL] $description" >&2
        failures=$((failures + 1))
    fi
}

echo '[suspend] read-only blocked-baseline preflight'
check 'kernel supports mem' grep -qw mem /sys/power/state
check 'deep is selected' grep -qw '\[deep\]' /sys/power/mem_sleep
check 'callbacks are serialized' test "$(cat /sys/power/pm_async 2>/dev/null)" = 0
check 'deep-only policy remains fail-closed' \
    sh -c "grep -qs '^AllowSuspend=no$' /etc/systemd/sleep.conf.d/80-pocketds-sleep.conf &&
        grep -qs '^SuspendState=mem$' /etc/systemd/sleep.conf.d/80-pocketds-sleep.conf &&
        grep -qs '^MemorySleepMode=deep$' /etc/systemd/sleep.conf.d/80-pocketds-sleep.conf"
check 'lid is currently in safe-ignore mode' \
    grep -qs '^HandleLidSwitch=ignore$' /etc/systemd/logind.conf.d/80-pocketds-lid-safety.conf
check 'power key is currently in safe-ignore mode' \
    grep -qs '^HandlePowerKey=ignore$' /etc/systemd/logind.conf.d/80-pocketds-lid-safety.conf
check 'root guard reports either safely blocked or execution-ready' \
    sudo -n /usr/local/libexec/pocketds-panel-root deep-suspend-check

(( failures == 0 )) || exit 1
echo '[suspend] blocked-baseline PASS; this script has no execution path'
