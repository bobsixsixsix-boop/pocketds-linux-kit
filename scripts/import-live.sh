#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)

if [[ ${1:-} != --yes ]]; then
    echo 'This overwrites tracked source snapshots with the installed live files.' >&2
    echo "Run '$0 --yes', then inspect git diff before committing." >&2
    exit 2
fi

# Reject a contaminated session snapshot before changing any tracked source.
# environment.d accepts assignments, not shell programs: do not source it.
python3 - "$HOME/.config/environment.d/90-fcitx5.conf" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
if source.is_file():
    for line in source.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key.strip() in {"GTK_IM_MODULE", "QT_IM_MODULE"}:
            if value.strip() not in {"", "''", '""'}:
                raise SystemExit(f"Refusing forced Wayland input-method environment in {source}: {key.strip()}")
PY

copy_live() {
    local source=$1 target=$2
    if [[ -r $source ]]; then
        cp -p "$source" "$target"
        echo "  $source"
    else
        echo "  WARN: cannot read $source" >&2
    fi
}

echo '[import] user components'
copy_live "$HOME/.local/bin/pocketds-keyboard.py" \
    "$repo_root/components/keyboard/pocketds-keyboard.py"
copy_live "$HOME/.local/bin/visibility_state.py" \
    "$repo_root/components/keyboard/visibility_state.py"
copy_live "$HOME/.local/bin/keyboard_adapter.py" \
    "$repo_root/components/keyboard/keyboard_adapter.py"
copy_live "$HOME/.local/bin/screen_geometry.py" \
    "$repo_root/components/keyboard/screen_geometry.py"
copy_live "$HOME/.config/systemd/user/pocketds-keyboard.service" \
    "$repo_root/components/keyboard/pocketds-keyboard.service"
copy_live "$HOME/.local/bin/pocketds-codex-quota" \
    "$repo_root/components/codex-quota/pocketds-codex-quota"
copy_live "$HOME/.config/systemd/user/pocketds-codex-quota.service" \
    "$repo_root/components/codex-quota/pocketds-codex-quota.service"
copy_live "$HOME/.config/systemd/user/pocketds-codex-quota.timer" \
    "$repo_root/components/codex-quota/pocketds-codex-quota.timer"
copy_live "$HOME/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/metadata.json" \
    "$repo_root/components/control-panel/plasmoid/metadata.json"
for ui_file in ControllerDiagram.qml ControllerTestSession.qml main.qml; do
    copy_live "$HOME/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui/$ui_file" \
        "$repo_root/components/control-panel/plasmoid/contents/ui/$ui_file"
done
copy_live /usr/local/libexec/pocketds-panel-root \
    "$repo_root/components/control-panel/pocketds-panel-root"
copy_live /usr/local/libexec/pocketds-deep-suspend \
    "$repo_root/components/system/pocketds-deep-suspend.py"
copy_live /etc/polkit-1/rules.d/90-pocketds-deep-suspend.rules \
    "$repo_root/components/system/90-pocketds-deep-suspend.rules"
copy_live /usr/local/bin/pocketds-plasma-recovery \
    "$repo_root/scripts/pocketds-plasma-recovery.py"
copy_live "$HOME/.config/systemd/user/pocketds-plasma-recovery.service" \
    "$repo_root/components/control-panel/pocketds-plasma-recovery.service"
copy_live /usr/local/bin/pocketds-chromium-v4l2 \
    "$repo_root/components/chromium/pocketds-chromium-v4l2"
copy_live "$HOME/.local/share/applications/chromium-browser.desktop" \
    "$repo_root/components/chromium/chromium-browser.desktop"
copy_live "$HOME/.local/bin/pocketds-steam-session" \
    "$repo_root/components/steam/pocketds-steam-session"
copy_live "$HOME/.local/bin/steamos-session-select" \
    "$repo_root/components/steam/steamos-session-select"
copy_live "$HOME/.local/share/applications/Steam.desktop" \
    "$repo_root/components/steam/Steam.desktop"
copy_live "$HOME/.local/share/Steam/launch-steam.sh" \
    "$repo_root/components/steam/launch-steam.sh"
copy_live "$HOME/.local/bin/pocketds-steam-game-fps" \
    "$repo_root/components/steam/pocketds-steam-game-fps"
copy_live "$HOME/.local/bin/pocketds-game-runtime" \
    "$repo_root/components/game-runtime/pocketds-game-runtime"
copy_live "$HOME/.local/bin/pocketds-game-limit" \
    "$repo_root/components/game-runtime/pocketds-game-limit"
copy_live "$HOME/.local/libexec/pocketds-gamescope-inner" \
    "$repo_root/components/game-runtime/pocketds-gamescope-inner"
copy_live "$HOME/.local/share/pocketds-linux-kit/game-runtime/kwin-gamescope-top-screen.js" \
    "$repo_root/components/game-runtime/kwin-gamescope-top-screen.js"
copy_live /usr/local/libexec/pocketds-gamescope-observer \
    "$repo_root/components/game-runtime/pocketds-gamescope-observer"

copy_live "$HOME/.local/bin/pocketds-es-de" \
    "$repo_root/components/emulation/pocketds-es-de"
copy_live "$HOME/.local/bin/pocketds-retroarch" \
    "$repo_root/components/emulation/pocketds-retroarch"
copy_live "$HOME/.local/bin/pocketds-melonds" \
    "$repo_root/components/emulation/pocketds-melonds"
copy_live "$HOME/.local/libexec/pocketds-input-mode" \
    "$repo_root/components/emulation/pocketds-input-mode-user"
copy_live "$HOME/.local/libexec/pocketds-es-de-prepare" \
    "$repo_root/components/emulation/pocketds-es-de-prepare.py"
copy_live "$HOME/.local/libexec/pocketds-melonds-prepare" \
    "$repo_root/components/emulation/pocketds-melonds-prepare.py"
copy_live "$HOME/.local/libexec/pocketds-melonds-fullscreen" \
    "$repo_root/components/emulation/pocketds-melonds-fullscreen.py"
copy_live "$HOME/.local/share/pocketds-linux-kit/emulation/kwin-melonds-dualscreen.js" \
    "$repo_root/components/emulation/kwin-melonds-dualscreen.js"
copy_live "$HOME/.local/share/applications/ES-DE.desktop" \
    "$repo_root/components/emulation/ES-DE.desktop"
copy_live "$HOME/.local/share/pocketds-linux-kit/emulation/es_systems.xml" \
    "$repo_root/components/emulation/es_systems.xml"
copy_live "$HOME/.local/share/pocketds-linux-kit/emulation/es_settings.xml" \
    "$repo_root/components/emulation/es_settings.xml"
copy_live "$HOME/.local/bin/pocketds-wiliwili" \
    "$repo_root/components/wiliwili/pocketds-wiliwili"
copy_live "$HOME/.local/libexec/pocketds-wiliwili-gamecontrollerdb" \
    "$repo_root/scripts/pocketds-wiliwili-gamecontrollerdb.py"
copy_live "$HOME/.local/share/pocketds-linux-kit/wiliwili/gamecontrollerdb.txt" \
    "$repo_root/components/wiliwili/gamecontrollerdb.txt"
copy_live "$HOME/.local/share/applications/cn.xfangfang.wiliwili.desktop" \
    "$repo_root/components/wiliwili/cn.xfangfang.wiliwili.desktop"

copy_live "$HOME/.config/environment.d/90-fcitx5.conf" \
    "$repo_root/components/locale/90-fcitx5.conf"
copy_live "$HOME/.config/plasma-workspace/env/90-fcitx5-wayland.sh" \
    "$repo_root/components/locale/90-fcitx5-wayland.sh"
copy_live "$HOME/.config/autostart/org.fcitx.Fcitx5.desktop" \
    "$repo_root/components/locale/org.fcitx.Fcitx5.desktop"
copy_live "$HOME/.config/fcitx5/config" "$repo_root/components/locale/config"
copy_live "$HOME/.config/fcitx5/profile" "$repo_root/components/locale/profile"
copy_live "$HOME/.config/fcitx5/conf/classicui.conf" \
    "$repo_root/components/locale/classicui.conf"
copy_live "$HOME/.local/share/fcitx5/rime/default.custom.yaml" \
    "$repo_root/components/locale/default.custom.yaml"
copy_live "$HOME/.config/kwinrc" "$repo_root/components/system/kwinrc.current"
copy_live "$HOME/.config/kwinrulesrc" "$repo_root/components/system/kwinrulesrc.current"
if cmp -s /etc/pocketds-linux-kit/daily-suspend.enabled \
    "$repo_root/components/system/daily-suspend.enabled"; then
    copy_live "$HOME/.config/powerdevilrc" "$repo_root/components/system/powerdevilrc.deep"
else
    copy_live "$HOME/.config/powerdevilrc" "$repo_root/components/system/powerdevilrc"
fi
copy_live "$HOME/.config/kscreenlockerrc" "$repo_root/components/system/kscreenlockerrc"
copy_live "$HOME/Pictures/Wallpapers/neko-bass-upper.png" \
    "$repo_root/assets/wallpapers/neko-bass-upper.png"
copy_live "$HOME/Pictures/Wallpapers/tendou-kei-lower.png" \
    "$repo_root/assets/wallpapers/tendou-kei-lower.png"

echo '[import] system components'
copy_live /usr/share/alsa/ucm2/Qualcomm/sm8550/APS/HiFi.conf \
    "$repo_root/components/audio/HiFi.conf"
copy_live /usr/share/alsa/ucm2/Qualcomm/sm8550/APS/SM8550-APS.conf \
    "$repo_root/components/audio/SM8550-APS.conf"
copy_live /usr/share/inputplumber/profiles/pocketds-gamepad.yaml \
    "$repo_root/components/inputplumber/pocketds-gamepad.yaml"
copy_live /usr/share/inputplumber/profiles/pocketds-joymouse.yaml \
    "$repo_root/components/inputplumber/pocketds-joymouse.yaml"
copy_live /usr/share/inputplumber/capability_maps/ayaneo_mcu_xbox.yaml \
    "$repo_root/components/inputplumber/ayaneo_mcu_xbox.yaml"
copy_live /usr/local/libexec/pocketds-input-mode \
    "$repo_root/components/emulation/pocketds-input-mode"
copy_live /usr/local/libexec/pocketds-game-session \
    "$repo_root/components/emulation/pocketds-game-session.py"
copy_live /usr/bin/pocketds-toggle-joymouse \
    "$repo_root/components/inputplumber/pocketds-toggle-joymouse"
copy_live /usr/bin/pocketds-mode-listener \
    "$repo_root/components/inputplumber/pocketds-mode-listener.py"
copy_live /usr/local/libexec/pocketds-input-held-modifiers \
    "$repo_root/scripts/pocketds-input-held-modifiers.py"
copy_live /etc/systemd/system/inputplumber.service.d/10-pocketds-manage-all-devices.conf \
    "$repo_root/components/inputplumber/10-pocketds-manage-all-devices.conf"
copy_live /etc/systemd/system/pocketds-mode-listener.service.d/20-input-state.conf \
    "$repo_root/components/inputplumber/20-pocketds-mode-listener-input-state.conf"
copy_live /usr/lib/tmpfiles.d/pocketds-input-mode.conf \
    "$repo_root/components/inputplumber/pocketds-input-mode.conf"
copy_live /etc/tuned/ppd.conf "$repo_root/components/fan/ppd.conf"
for profile in pocketds-balanced pocketds-performance pocketds-powersave; do
    copy_live "/etc/tuned/profiles/$profile/tuned.conf" \
        "$repo_root/components/fan/profiles/$profile/tuned.conf"
    copy_live "/etc/tuned/profiles/$profile/fan-profile.sh" \
        "$repo_root/components/fan/profiles/$profile/fan-profile.sh"
done

copy_live /etc/systemd/system/user@.service.d/90-pocketds-stop-timeout.conf \
    "$repo_root/components/system/90-pocketds-stop-timeout.conf"
copy_live /etc/tmpfiles.d/80-pocketds-pm.conf \
    "$repo_root/components/system/80-pocketds-pm.conf"
for rule in 60-pocketds-i2c-ddc.rules 61-pocketds-touchscreens.rules 70-uinput.rules; do
    copy_live "/etc/udev/rules.d/$rule" "$repo_root/components/system/$rule"
done

echo '[import] complete; review with: git diff'
