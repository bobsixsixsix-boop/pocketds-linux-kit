#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
warnings=0

pass() { printf '  [OK] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; warnings=$((warnings + 1)); }

lid_switch_present() {
    local capability low
    local -a words
    for capability in /sys/class/input/event*/device/capabilities/sw; do
        [[ -r $capability ]] || continue
        read -r -a words <"$capability" || continue
        ((${#words[@]})) || continue
        low=${words[-1]}
        [[ $low =~ ^[0-9a-fA-F]+$ ]] || continue
        if (((16#$low & 1) == 1)); then
            return 0
        fi
    done
    return 1
}

"$repo_root/scripts/lint.sh"

echo '[check] repository'
if git -C "$repo_root" rev-parse --git-dir >/dev/null 2>&1; then
    branch=$(git -C "$repo_root" branch --show-current)
    pass "Git repository, branch ${branch:-detached}"
    if [[ -n $(git -C "$repo_root" status --porcelain) ]]; then
        warn 'working tree has uncommitted changes'
    else
        pass 'working tree is clean'
    fi
else
    warn 'Git repository has not been initialized yet'
fi

if [[ ! -e /sys/class/backlight/ae94000.dsi.0 ]]; then
    echo '[check] not running on Pocket DS; source checks complete'
    exit 0
fi

echo '[check] Pocket DS runtime'
[[ $(uname -m) == aarch64 ]] && pass 'aarch64 host' || warn "unexpected architecture: $(uname -m)"

for path in \
    /sys/class/backlight/ae94000.dsi.0 \
    /sys/class/backlight/sy7758-backlight; do
    if [[ -r "$path/brightness" && -r "$path/max_brightness" ]]; then
        pass "backlight $(basename "$path") $(<"$path/brightness")/$(<"$path/max_brightness")"
    else
        warn "missing backlight $path"
    fi
done

if [[ -r /sys/power/pm_async ]]; then
    [[ $(</sys/power/pm_async) == 0 ]] && \
        pass 'device suspend callbacks are serialized' || \
        warn 'asynchronous device suspend is enabled; Goodix may abort sleep'
fi

if grep -qs '^SuspendState=mem$' \
    /etc/systemd/sleep.conf.d/80-pocketds-sleep.conf &&
   grep -qs '^MemorySleepMode=deep$' \
    /etc/systemd/sleep.conf.d/80-pocketds-sleep.conf; then
    pass 'suspend is restricted to deep sleep (no unsafe s2idle fallback)'
else
    warn 'deep-only suspend policy is missing; an aborted suspend may fall back to s2idle'
fi

daily_sleep=0
if [[ -x /usr/local/libexec/pocketds-daily-suspend ]] &&
   /usr/local/libexec/pocketds-daily-suspend check >/dev/null 2>&1; then
    daily_sleep=1
    if sudo -n /usr/local/libexec/pocketds-daily-suspend verify >/dev/null 2>&1; then
        pass 'opted-in native deep sleep passes the tested-kernel and storage gate'
    else
        warn 'daily deep-sleep precheck rejects current runtime; do not force sleep'
    fi
elif [[ -e /etc/pocketds-linux-kit/daily-suspend.enabled ]]; then
    warn 'daily deep-sleep opt-in exists but runtime eligibility is blocked'
elif grep -qs '^AllowSuspend=no$' \
    /etc/systemd/sleep.conf.d/80-pocketds-sleep.conf; then
    pass 'deep suspend remains fail-closed'
else
    warn 'deep suspend is not blocked by the baseline policy'
fi

if sudo -n /usr/local/libexec/pocketds-panel-root deep-suspend-check \
    >/dev/null 2>&1; then
    pass 'deep suspend lifecycle guard preflight responds'
else
    warn 'deep suspend lifecycle guard preflight failed'
fi

if grep -qs '^HandleLidSwitch=ignore$' \
    /etc/systemd/logind.conf.d/80-pocketds-lid-safety.conf; then
    pass 'logind lid fallback is ignored; the configured desktop action owns the lid'
else
    warn 'safe lid-ignore policy is missing'
fi

if lid_switch_present; then
    pass 'standard SW_LID is available to UPower and KDE'
else
    warn 'no native or fallback SW_LID is available; lid display-off cannot trigger'
fi

critical_action=$(upower -d 2>/dev/null | sed -n \
    's/^[[:space:]]*critical-action:[[:space:]]*//p')
if [[ $critical_action == PowerOff ]]; then
    pass 'critical battery action is clean poweroff'
else
    warn "critical battery action is not clean poweroff: ${critical_action:-unavailable}"
fi

if grep -qs '^HandlePowerKey=ignore$' \
    /etc/systemd/logind.conf.d/80-pocketds-lid-safety.conf; then
    pass 'logind power-key fallback is ignored; the configured desktop action owns it'
else
    warn 'safe power-key-ignore policy is missing'
fi

powerdevil_rc="$HOME/.config/powerdevilrc"
powerdevil_idle_ok=1
if command -v kreadconfig6 >/dev/null 2>&1; then
    for profile_timeout in 'AC 300' 'Battery 300' 'LowBattery 120'; do
        read -r profile timeout <<<"$profile_timeout"
        [[ $(kreadconfig6 --file "$powerdevil_rc" --group "$profile" \
            --group Display --key TurnOffDisplayWhenIdle) == true ]] || \
            powerdevil_idle_ok=0
        [[ $(kreadconfig6 --file "$powerdevil_rc" --group "$profile" \
            --group Display --key TurnOffDisplayIdleTimeoutSec) == "$timeout" ]] || \
            powerdevil_idle_ok=0
        [[ $(kreadconfig6 --file "$powerdevil_rc" --group "$profile" \
            --group Display --key LockBeforeTurnOffDisplay) == false ]] || \
            powerdevil_idle_ok=0
    done
else
    powerdevil_idle_ok=0
fi
if ((powerdevil_idle_ok)); then
    pass 'idle display-off is enabled without a password lock'
else
    warn 'idle display-off policy is missing or has drifted'
fi

if ((daily_sleep)); then
    daily_actions_ok=1
    for profile_timeout in 'AC 600' 'Battery 600' 'LowBattery 180'; do
        read -r profile timeout <<<"$profile_timeout"
        for key in AutoSuspendAction LidAction PowerButtonAction SleepMode; do
            [[ $(kreadconfig6 --file "$powerdevil_rc" --group "$profile" \
                --group SuspendAndShutdown --key "$key") == 1 ]] || daily_actions_ok=0
        done
        [[ $(kreadconfig6 --file "$powerdevil_rc" --group "$profile" \
            --group SuspendAndShutdown --key AutoSuspendIdleTimeoutSec) == "$timeout" ]] || daily_actions_ok=0
    done
    if ((daily_actions_ok)); then
        pass 'native KDE lid, power-button and idle deep-sleep actions are configured'
    else
        warn 'daily deep-sleep desktop actions drifted'
    fi
fi

if systemctl --user is-active --quiet plasma-powerdevil.service; then
    pass 'PowerDevil is running and can enforce idle display-off'
else
    warn 'PowerDevil is inactive; automatic idle display-off cannot run'
fi

if ! systemctl is-enabled --quiet pocketds-power-button.service 2>/dev/null; then
    pass 'unsafe custom power-button service is disabled'
else
    warn 'unsafe custom power-button service is still enabled'
fi

renesas_xhci=/sys/bus/pci/devices/0001:01:00.0
if [[ -r $renesas_xhci/vendor && -r $renesas_xhci/device &&
      -r $renesas_xhci/power/wakeup ]] &&
   [[ $(<"$renesas_xhci/vendor") == 0x1912 ]] &&
   [[ $(<"$renesas_xhci/device") == 0x0014 ]] &&
   [[ $(<"$renesas_xhci/power/wakeup") == disabled ]]; then
    pass 'internal Renesas xHCI wake source is disabled after driver bind'
else
    warn 'internal Renesas xHCI wake source is not disabled'
fi

if sudo -n firewall-cmd --state >/dev/null 2>&1; then
    pass 'firewalld reports running rather than active-but-failed'
    if [[ $(sudo -n firewall-cmd --get-default-zone 2>/dev/null) == public ]]; then
        pass 'unknown networks default to the public firewall zone'
    else
        warn 'firewalld default zone is not public'
    fi
    if sudo -n firewall-cmd --zone=pocketds-home --query-service=kdeconnect \
        >/dev/null 2>&1 &&
       ! sudo -n firewall-cmd --zone=public --query-service=kdeconnect \
        >/dev/null 2>&1; then
        pass 'KDE Connect is limited to the Pocket DS trusted-home zone'
    else
        warn 'KDE Connect firewall zone isolation is incorrect'
    fi
else
    warn 'firewalld is not in a usable running state'
fi

if ! systemctl is-enabled --quiet NetworkManager-wait-online.service 2>/dev/null; then
    warn 'vendor NetworkManager-wait-online unit is unexpectedly disabled'
elif systemctl is-failed --quiet NetworkManager-wait-online.service 2>/dev/null; then
    warn 'NetworkManager-wait-online still has a failed state'
elif systemctl cat NetworkManager-wait-online.service 2>/dev/null |
     grep -q '10-pocketds-fast-online.conf'; then
    warn 'obsolete five-second wait-online override is still installed'
else
    pass 'vendor NetworkManager-wait-online semantics are restored without failed state'
fi

if [[ -x /usr/local/bin/pocketds-panelctl ]]; then
    if status_json=$(/usr/local/bin/pocketds-panelctl status 2>/dev/null) &&
       python3 -c '
import json, sys
data = json.load(sys.stdin)
assert data.get("brightness_write_status") == "ok"
assert data.get("power_profile") in {"powersave", "balanced", "performance"}
' <<<"$status_json"; then
        pass 'control-panel helper reports writable brightness and a managed power profile'
    else
        warn 'control-panel helper or a required control capability failed'
    fi
else
    warn '/usr/local/bin/pocketds-panelctl is not installed'
fi

if modinfo rfcomm >/dev/null 2>&1; then
    pass 'Bluetooth RFCOMM support is available for headset microphones'
else
    warn 'Bluetooth RFCOMM is missing from this kernel/module tree; HFP headset microphones cannot connect'
fi

for audio_command in pactl parecord arecord; do
    if command -v "$audio_command" >/dev/null 2>&1; then
        pass "voice input command $audio_command is available"
    else
        audio_package=pulseaudio-utils
        [[ $audio_command != arecord ]] || audio_package=alsa-utils
        warn "voice input command $audio_command is missing; install $audio_package"
    fi
done

for unit in pocketds-brightness.service pocketds-keyboard.service \
    pocketds-touchpad.service pocketds-gpu-telemetry.service \
    pocketds-light-standby.service pocketds-gamepad-activity.service \
    pocketds-codex-quota.timer; do
    if systemctl --user is-active --quiet "$unit"; then
        pass "$unit active"
    else
        warn "$unit inactive"
    fi
done

for unit in tuned.service pocketds-tuned-ppd.service pocketds-fancontrol.service \
    pocketds-mode-listener.service inputplumber.service; do
    if systemctl is-active --quiet "$unit"; then
        pass "$unit active"
    else
        warn "$unit inactive"
    fi
done

active_tuned_profile=$(cat /etc/tuned/active_profile 2>/dev/null || true)
case "$active_tuned_profile" in
    pocketds-powersave) expected_ppd_profile=power-saver ;;
    pocketds-balanced) expected_ppd_profile=balanced ;;
    pocketds-performance) expected_ppd_profile=performance ;;
    *) expected_ppd_profile=unknown ;;
esac
if [[ $(systemctl is-enabled power-profiles-daemon.service 2>/dev/null || true) == masked ]] &&
   ! systemctl is-active --quiet power-profiles-daemon.service &&
   [[ $expected_ppd_profile != unknown ]] &&
   ppd_profile=$(busctl --system --auto-start=no --timeout=2s call \
       org.freedesktop.UPower.PowerProfiles /org/freedesktop/UPower/PowerProfiles \
       org.freedesktop.DBus.Properties Get ss \
       org.freedesktop.UPower.PowerProfiles ActiveProfile 2>/dev/null) &&
   [[ $ppd_profile == "v s \"$expected_ppd_profile\"" ]]; then
    pass 'KDE power profiles and Panel agree through the TuneD bridge'
else
    warn 'KDE power-profile bridge is missing, mismatched, or has a competing daemon'
fi

if rpm -q plasma-milou >/dev/null 2>&1 &&
   [[ -r /usr/lib64/qt6/qml/org/kde/milou/qmldir ]]; then
    pass 'KDE overview/search Milou QML module installed'
else
    warn 'KDE overview/search Milou QML module missing'
fi

# A package record alone does not prove the password dictionary can be loaded.
# Use a fixed, non-secret strong probe that reaches the dictionary lookup.
dictionary_probe='PdsDictionaryProbe!7vQ93mZ'
if rpm -q cracklib-dicts >/dev/null 2>&1 &&
   [[ -x /usr/bin/cracklib-check ]] &&
   dictionary_result=$(LC_ALL=C /usr/bin/cracklib-check <<<"$dictionary_probe" 2>/dev/null) &&
   [[ $dictionary_result == "$dictionary_probe: OK" ]]; then
    pass 'password quality dictionary is installed and loads successfully'
else
    warn 'password quality dictionary is missing or unusable; restore cracklib-dicts before changing passwords'
fi

lid_helper="$HOME/.local/libexec/pocketds/pocketds-light-standby"
if [[ -x $lid_helper ]] &&
   lid_status=$($lid_helper check 2>/dev/null || true) &&
   python3 -c '
import json, sys
data = json.load(sys.stdin)
assert isinstance(data.get("lid_closed"), bool)
' <<<"$lid_status"; then
    pass 'lid helper exposes a readable Pocket DS lid state'
else
    warn 'Pocket DS lid state is not readable through the lid helper'
fi

if [[ -d "$HOME/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3" ]]; then
    pass 'Plasma control panel package installed'
else
    warn 'Plasma control panel package missing'
fi

if grep -q 'org.pocketds.controlpanel.v3' \
    "$HOME/.config/plasma-org.kde.plasma.desktop-appletsrc" 2>/dev/null; then
    pass 'control panel is present in Plasma layout'
else
    warn 'control panel package is not placed on a desktop'
fi

if [[ -x /opt/pocketds-chromium-v4l2-151.0.7922.137/usr/lib/chromium/chromium ]]; then
    pass 'Chromium ARM64 V4L2 runtime present'
else
    warn 'Chromium ARM64 V4L2 runtime missing (external dependency)'
fi

if python3 - "$repo_root/components/codex-quota/pocketds-codex-quota" <<'PY'
import runpy
import sys

# Reuse the collector's explicit override, local, PATH and bundled lookup.
# Importing it does not query an account, launch Codex or read its credentials.
collector = runpy.run_path(sys.argv[1], run_name="pocketds_quota_discovery")
raise SystemExit(0 if collector["find_codex"]() else 1)
PY
then
    pass 'Codex CLI present'
else
    warn 'Codex CLI missing (external dependency)'
fi

if [[ -x "$HOME/Applications/ES-DE_aarch64.AppImage" ]]; then
    pass 'ES-DE AppImage present'
else
    warn 'ES-DE AppImage missing (external dependency)'
fi

printf '[check] complete: %d warning(s)\n' "$warnings"
