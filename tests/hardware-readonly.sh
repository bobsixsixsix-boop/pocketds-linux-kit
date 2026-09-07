#!/usr/bin/env bash
set -Eeuo pipefail

failures=0
warnings=0
pass() { printf '  [OK] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; warnings=$((warnings + 1)); }
fail() { printf '  [FAIL] %s\n' "$*" >&2; failures=$((failures + 1)); }

if [[ ! -d /sys/class/drm ]]; then
    echo '[hardware] skipped outside Linux'
    exit 0
fi

echo '[hardware] displays and brightness'
for connector in card0-DSI-1 card0-DSI-2; do
    path="/sys/class/drm/$connector"
    if [[ -r $path/status && $(<"$path/status") == connected ]]; then
        modes=$(tr '\n' ',' <"$path/modes")
        pass "$connector connected; modes=${modes%,}"
    else
        fail "$connector is not connected"
    fi
done

for spec in 'top:/sys/class/backlight/ae94000.dsi.0' \
            'bottom:/sys/class/backlight/sy7758-backlight'; do
    label=${spec%%:*}
    path=${spec#*:}
    if [[ -r $path/brightness && -r $path/max_brightness ]]; then
        value=$(<"$path/brightness")
        maximum=$(<"$path/max_brightness")
        percent=$((value * 100 / maximum))
        pass "$label backlight ${value}/${maximum} (${percent}%)"
        (( percent == 100 )) && warn "$label booted at 100% (PDS-003)"
    else
        fail "$label backlight node missing"
    fi
done

echo '[hardware] power and battery'
if grep -qw '\[deep\]' /sys/power/mem_sleep 2>/dev/null; then
    pass 'deep is the selected memory sleep mode'
else
    fail 'deep is not selected'
fi
if [[ -r /sys/power/pm_async && $(</sys/power/pm_async) == 0 ]]; then
    pass 'device suspend callbacks are serialized'
else
    warn 'pm_async is not disabled'
fi

battery=/sys/class/power_supply/battery
if [[ -r $battery/capacity && -r $battery/status ]]; then
    pass "battery $(<"$battery/capacity")%, $(<"$battery/status")"
else
    fail 'battery capacity/status unavailable'
fi
[[ -r $battery/temp ]] && pass "battery temperature $(( $(<"$battery/temp") / 10 ))°C"

echo '[hardware] GPU'
gpu=/sys/devices/platform/soc@0/3d00000.gpu/devfreq/3d00000.gpu
if [[ -r $gpu/cur_freq ]]; then
    pass "GPU current frequency $(<"$gpu/cur_freq") Hz"
else
    fail 'GPU devfreq current frequency missing'
fi
if [[ -r $gpu/busy_time && -r $gpu/total_time ]]; then
    pass 'GPU busy/total counters available'
else
    warn 'GPU utilization counters not exposed at the devfreq path (PDS-007)'
fi

echo "[hardware] complete: $failures failure(s), $warnings warning(s)"
(( failures == 0 )) || exit 1
