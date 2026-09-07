#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
helper="$repo_root/components/control-panel/pocketds-panel-root"
failures=0

expect_rejected() {
    local label=$1
    shift
    set +e
    "$helper" "$@" >/dev/null 2>&1
    rc=$?
    set -e
    if [[ $rc -eq 2 ]]; then
        printf '  [OK] %s rejected\n' "$label"
    else
        printf '  [FAIL] %s returned %s, expected 2\n' "$label" "$rc" >&2
        failures=$((failures + 1))
    fi
}

expect_rejected 'unknown action' unknown
expect_rejected 'manual fan below safe floor' fan-manual 19
expect_rejected 'manual fan above range' fan-manual 101
expect_rejected 'manual fan non-numeric' fan-manual not-a-number
expect_rejected 'manual fan shell text' fan-manual '$(touch /tmp/pocketds-test-should-not-exist)'
expect_rejected 'unknown power profile' power turbo
expect_rejected 'power profile extra argument' power balanced extra
expect_rejected 'brightness below safe floor' brightness top 4
expect_rejected 'brightness above range' brightness bottom 101
expect_rejected 'unknown display' brightness side 60
expect_rejected 'brightness extra argument' brightness top 60 extra
expect_rejected 'blanking unknown display' backlight-blank top
expect_rejected 'blanking extra argument' backlight-blank bottom extra
expect_rejected 'fan manual extra argument' fan-manual 50 extra
expect_rejected 'deep suspend below RTC floor' deep-suspend 59
expect_rejected 'deep suspend above RTC bound' deep-suspend 301
expect_rejected 'deep suspend non-numeric' deep-suspend not-a-number
expect_rejected 'deep suspend extra argument' deep-suspend 60 extra
expect_rejected 'deep suspend check extra argument' deep-suspend-check extra
expect_rejected 'Android boot without confirmation' boot-android
expect_rejected 'Android boot with wrong confirmation' boot-android yes
expect_rejected 'Android boot with extra argument' boot-android CONFIRM extra

if [[ -e /tmp/pocketds-test-should-not-exist ]]; then
    echo '  [FAIL] shell text caused a side effect' >&2
    failures=$((failures + 1))
fi

(( failures == 0 )) || exit 1
