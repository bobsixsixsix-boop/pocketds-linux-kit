#!/usr/bin/env bash
set -Eeuo pipefail

failures=0

check_user() {
    local unit=$1
    if systemctl --user is-active --quiet "$unit"; then
        echo "  [OK] user $unit active"
    else
        echo "  [FAIL] user $unit inactive" >&2
        failures=$((failures + 1))
    fi
}

check_system() {
    local unit=$1
    if systemctl is-active --quiet "$unit"; then
        echo "  [OK] system $unit active"
    else
        echo "  [FAIL] system $unit inactive" >&2
        failures=$((failures + 1))
    fi
}

check_user pocketds-keyboard.service
check_user pocketds-codex-quota.timer
check_user pocketds-gpu-telemetry.service
check_user pocketds-brightness.service
check_system pocketds-fancontrol.service
check_system inputplumber.service
check_system pocketds-controller-test.service
check_system pocketds-mode-listener.service
check_system tuned.service

if ! systemctl is-enabled --quiet NetworkManager-wait-online.service 2>/dev/null; then
    echo '  [FAIL] vendor NetworkManager-wait-online.service unexpectedly disabled' >&2
    failures=$((failures + 1))
elif systemctl is-failed --quiet NetworkManager-wait-online.service 2>/dev/null; then
    echo '  [FAIL] NetworkManager-wait-online.service has stale failed state' >&2
    failures=$((failures + 1))
else
    echo '  [OK] system NetworkManager-wait-online.service enabled and not failed'
fi

(( failures == 0 )) || exit 1
