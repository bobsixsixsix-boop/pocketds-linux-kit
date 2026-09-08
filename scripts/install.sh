#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
mode=apps
asset_profile=personal-assets
mode_seen=0
asset_profile_seen=0

usage() {
    echo "usage: $0 [--apps|--all] [--personal-assets|--asset-free]"
}

while (($#)); do
    case "$1" in
        --apps|--all)
            if ((mode_seen)); then
                echo 'Install scope may be selected only once.' >&2
                usage >&2
                exit 2
            fi
            mode=${1#--}
            mode_seen=1
            ;;
        --personal-assets|--asset-free)
            if ((asset_profile_seen)); then
                echo 'Asset profile may be selected only once.' >&2
                usage >&2
                exit 2
            fi
            asset_profile=${1#--}
            asset_profile_seen=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [[ $EUID -eq 0 ]]; then
    echo 'Run this script as the desktop user, not root.' >&2
    exit 1
fi

for command in arecord busctl c++ cmp install kdialog kwriteconfig6 pactl parecord python3 rpm sha256sum systemctl sudo tuned-adm; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
if [[ $mode == all ]]; then
    for command in gpioget gpiomon; do
        command -v "$command" >/dev/null 2>&1 || {
            echo "Missing required command: $command (install libgpiod-utils)" >&2
            exit 1
        }
    done
fi
sudo -n true

# The custom haptics backend is an externally built, hash-pinned payload and is
# therefore not copied by this repository-only installer.  Never let a clean
# reinstall look complete while silently falling back to Fedora InputPlumber,
# which has no Pocket DS Pulse method.
haptics_ready=1
haptics_binary=/usr/local/libexec/pocketds-inputplumber-haptics
haptics_sha256=4dbb8a7dc494e27abdbe3b38191dabfaef54caa8f6f2c7357bc84008bbfb488b
if sudo test -f "$haptics_binary" && sudo test -x "$haptics_binary"; then
    read -r live_haptics_sha256 _ < <(sudo sha256sum -- "$haptics_binary")
    [[ $live_haptics_sha256 == "$haptics_sha256" ]] || haptics_ready=0
else
    haptics_ready=0
fi
for haptics_file in \
    /etc/inputplumber/devices.d/01-ayaneo-controller.yaml \
    /etc/systemd/system/inputplumber.service.d/30-pocketds-haptics-candidate.conf \
    /usr/share/polkit-1/actions/org.pocketds.InputPlumber.Haptics.policy; do
    sudo test -f "$haptics_file" || haptics_ready=0
done
if ((haptics_ready == 0)); then
    echo '  WARN  verified Pocket DS haptics sidecar is absent or incomplete' >&2
    echo '        restore it with scripts/pocketds-inputplumber-haptics.py before calling this installation complete' >&2
fi

for package in tuned plasma-milou cracklib-dicts; do
    rpm -q "$package" >/dev/null 2>&1 || {
        echo "Missing required Fedora package: $package" >&2
        exit 1
    }
done

python3 - <<'PY'
import importlib

required = ("dbus", "evdev", "gi", "pyatspi", "tuned.ppd.controller")
missing = []
for module in required:
    try:
        importlib.import_module(module)
    except ModuleNotFoundError:
        missing.append(module)

if missing:
    raise SystemExit(
        "Missing required Python runtime module(s): "
        + ", ".join(missing)
        + ". On Fedora install python3-evdev, python3-dbus, "
          "python3-gobject, python3-pyatspi, and tuned before retrying."
    )
PY

python3 "$repo_root/scripts/pds020-install-asset-preflight.py" \
    --profile "$asset_profile" \
    --repository-root "$repo_root"

stamp=$(date +%Y%m%d-%H%M%S)
user_backup="$HOME/.local/state/pocketds-linux-kit/backups/$stamp"
root_backup="/var/lib/pocketds-linux-kit/backups/$stamp"
activation_journal="$HOME/.local/state/pocketds-linux-kit/pending-user-service-files"

backup_user_file() {
    local target=$1 rel
    [[ -e $target || -L $target ]] || return 0
    rel=${target#"$HOME"/}
    mkdir -p "$user_backup/$(dirname "$rel")"
    cp -a "$target" "$user_backup/$rel"
}

install_user_file() {
    local source=$1 target=$2 mode_bits=${3:-0644} expected_metadata
    [[ ! -L $target && ( ! -e $target || -f $target ) ]] || {
        echo "Refusing non-regular install target: $target" >&2; return 1;
    }
    expected_metadata="$(printf '%o' "$((8#$mode_bits))"):$(id -u):$(id -g)"
    if [[ -f $target ]] && cmp -s "$source" "$target" &&
        [[ $(stat -c '%a:%u:%g' "$target") == "$expected_metadata" ]]; then
        return 0
    fi
    backup_user_file "$target"
    mkdir -p "$(dirname "$target")"
    # Write ahead: a failed/interrupted install must still activate these files
    # on retry even if their bytes already match on disk by then.
    [[ ! -L $activation_journal && ( ! -e $activation_journal || -f $activation_journal ) ]]
    mkdir -p "$(dirname "$activation_journal")"
    (umask 077; printf '%s\n' "$target" >> "$activation_journal")
    install -m "$mode_bits" -g "$(id -g)" "$source" "$target"
    [[ $(stat -c '%a:%u:%g' "$target") == "$expected_metadata" ]]
    echo "  user  $target"
}

install_user_if_missing() {
    local source=$1 target=$2 mode_bits=${3:-0644}
    [[ -e $target ]] && return 0
    mkdir -p "$(dirname "$target")"
    install -m "$mode_bits" "$source" "$target"
    echo "  new   $target"
}

backup_root_file() {
    local target=$1 rel
    sudo test -e "$target" || return 0
    rel=${target#/}
    sudo mkdir -p "$root_backup/$(dirname "$rel")"
    sudo cp -a "$target" "$root_backup/$rel"
}

install_root_file() {
    local source=$1 target=$2 mode_bits=${3:-0644} expected_metadata
    sudo test ! -L "$target" && { ! sudo test -e "$target" || sudo test -f "$target"; } || {
        echo "Refusing non-regular install target: $target" >&2; return 1;
    }
    expected_metadata="$(printf '%o' "$((8#$mode_bits))"):0:0"
    if sudo test -f "$target" && sudo cmp -s "$source" "$target" &&
        [[ $(sudo stat -c '%a:%u:%g' "$target") == "$expected_metadata" ]]; then
        return 0
    fi
    backup_root_file "$target"
    sudo mkdir -p "$(dirname "$target")"
    sudo install -m "$mode_bits" -o 0 -g 0 "$source" "$target"
    [[ $(sudo stat -c '%a:%u:%g' "$target") == "$expected_metadata" ]]
    echo "  root  $target"
}

start_updated_user_service() {
    local service=$1 target match_status action=start
    shift
    [[ ! -L $activation_journal && ( ! -e $activation_journal || -f $activation_journal ) ]]
    for target in "$@"; do
        if [[ -f $activation_journal ]]; then
            if grep -Fxq -- "$target" "$activation_journal"; then
                action=restart
            else
                match_status=$?
                [[ $match_status == 1 ]] || return "$match_status"
            fi
        fi
    done
    systemctl --user "$action" "$service"
    systemctl --user is-active --quiet "$service"
}

install_apps() {
    local active_profile ppd_enable_state tuned_verified target
    local expected_ppd_profile ppd_profile bridge_ready
    echo '[install] control panel'
    # Install the fixed lifecycle and non-root logind deny rule before the
    # narrow validating dispatcher can reference it.
    install_root_file "$repo_root/components/system/pocketds-deep-suspend.py" \
        /usr/local/libexec/pocketds-deep-suspend 0755
    install_root_file "$repo_root/components/system/90-pocketds-deep-suspend.rules" \
        /etc/polkit-1/rules.d/90-pocketds-deep-suspend.rules 0644
    install_root_file "$repo_root/components/control-panel/pocketds-panel-root" \
        /usr/local/libexec/pocketds-panel-root 0755
    install_root_file "$repo_root/components/system/pocketds-boot-mode.py" \
        /usr/local/libexec/pocketds-boot-mode 0755
    install_root_file "$repo_root/components/system/pocketds-switch-to-android.sh" \
        /usr/local/libexec/pocketds-switch-to-android 0755
    install_user_file "$repo_root/components/system/pocketds-switch-to-android.desktop" \
        "$HOME/.local/share/applications/pocketds-switch-to-android.desktop" 0755
    install_root_file "$repo_root/components/system/pocketds-switch-to-linux-android.sh" \
        /boot/PocketDS-Switch-to-Linux.sh 0644
    sudo visudo -cf "$repo_root/components/control-panel/90-pocketds-linux-kit" >/dev/null
    install_root_file "$repo_root/components/control-panel/90-pocketds-linux-kit" \
        /etc/sudoers.d/90-pocketds-linux-kit 0440

    echo '[install] Pocket DS performance profiles'
    if sudo systemctl is-active --quiet pocketds-tuned-ppd.service; then
        sudo systemctl stop pocketds-tuned-ppd.service
    fi
    install_root_file "$repo_root/components/fan/ppd.conf" /etc/tuned/ppd.conf
    for profile in pocketds-balanced pocketds-performance pocketds-powersave; do
        install_root_file "$repo_root/components/fan/profiles/$profile/tuned.conf" \
            "/etc/tuned/profiles/$profile/tuned.conf"
        install_root_file "$repo_root/components/fan/profiles/$profile/fan-profile.sh" \
            "/etc/tuned/profiles/$profile/fan-profile.sh" 0755
    done
    sudo systemctl daemon-reload

    # pocketds-base requires the power-profiles-daemon package, but its unit
    # conflicts with TuneD and is pulled in by graphical.target.  Merely
    # enabling TuneD leaves a boot race: whichever daemon starts second stops
    # the other.  Disable the graphical enablement and mask D-Bus activation
    # before making TuneD the single performance authority.
    sudo systemctl disable --now power-profiles-daemon.service
    sudo systemctl mask power-profiles-daemon.service
    sudo systemctl enable --now tuned.service
    ppd_enable_state=$(sudo systemctl is-enabled power-profiles-daemon.service 2>/dev/null || true)
    if [[ $ppd_enable_state != masked ]] || \
       ! sudo systemctl is-active --quiet tuned.service || \
       sudo systemctl is-active --quiet power-profiles-daemon.service; then
        echo 'TuneD could not become the sole Pocket DS performance authority.' >&2
        exit 1
    fi
    active_profile=$(sudo cat /etc/tuned/active_profile 2>/dev/null || true)
    case "$active_profile" in
        pocketds-balanced|pocketds-performance|pocketds-powersave) ;;
        *) sudo /usr/sbin/tuned-adm profile pocketds-balanced ;;
    esac
    tuned_verified=0
    for _ in {1..10}; do
        if sudo /usr/sbin/tuned-adm verify >/dev/null 2>&1; then
            tuned_verified=1
            break
        fi
        sleep 1
    done
    if ((tuned_verified == 0)); then
        echo 'TuneD could not verify the active Pocket DS profile.' >&2
        exit 1
    fi

    # Reuse TuneD's packaged official PPD controller without replacing the
    # power-profiles-daemon RPM required by pocketds-base. Local activation
    # files take precedence over that RPM's /usr/share entries; its unit stays
    # masked. Both desktop controls therefore reach the same TuneD authority.
    install_root_file "$repo_root/components/fan/pocketds-tuned-ppd.py" \
        /usr/local/libexec/pocketds/pocketds-tuned-ppd 0755
    install_root_file "$repo_root/components/fan/pocketds-tuned-ppd.service" \
        /etc/systemd/system/pocketds-tuned-ppd.service
    install_root_file "$repo_root/components/fan/org.pocketds.TunedPowerProfiles.policy" \
        /usr/share/polkit-1/actions/org.pocketds.TunedPowerProfiles.policy
    for target in org.freedesktop.UPower.PowerProfiles net.hadess.PowerProfiles; do
        install_root_file "$repo_root/components/fan/$target.service" \
            "/usr/local/share/dbus-1/system-services/$target.service"
    done
    sudo systemctl daemon-reload
    sudo busctl --system --timeout=5s call org.freedesktop.DBus /org/freedesktop/DBus \
        org.freedesktop.DBus ReloadConfig
    sudo systemctl enable --now pocketds-tuned-ppd.service
    sudo systemctl is-active --quiet pocketds-tuned-ppd.service
    case "$active_profile" in
        pocketds-powersave) expected_ppd_profile=power-saver ;;
        pocketds-performance) expected_ppd_profile=performance ;;
        *) expected_ppd_profile=balanced ;;
    esac
    bridge_ready=0
    for _ in {1..20}; do
        if ppd_profile=$(busctl --system --timeout=2s get-property \
            org.freedesktop.UPower.PowerProfiles /org/freedesktop/UPower/PowerProfiles \
            org.freedesktop.UPower.PowerProfiles ActiveProfile 2>/dev/null) &&
           [[ $ppd_profile == "s \"$expected_ppd_profile\"" ]]; then
            bridge_ready=1
            break
        fi
        sleep 0.1
    done
    if ((bridge_ready == 0)); then
        echo 'KDE power-profile bridge did not report the preserved TuneD profile.' >&2
        exit 1
    fi

    mkdir -p "$repo_root/build"
    c++ -std=c++17 -O2 -Wall -Wextra \
        "$repo_root/components/control-panel/pocketds-panelctl.cpp" \
        -o "$repo_root/build/pocketds-panelctl"
    install_root_file "$repo_root/build/pocketds-panelctl" \
        /usr/local/bin/pocketds-panelctl 0755
    install_root_file "$repo_root/scripts/pocketds-plasma-recovery.py" \
        /usr/local/bin/pocketds-plasma-recovery 0755
    install_user_file \
        "$repo_root/components/control-panel/pocketds-plasma-recovery.service" \
        "$HOME/.config/systemd/user/pocketds-plasma-recovery.service"

    echo '[install] speaker-only audio enhancement'
    install_user_file \
        "$repo_root/components/audio/90-pocketds-speaker-enhancement.conf" \
        "$HOME/.config/pipewire/filter-chain.conf.d/90-pocketds-speaker-enhancement.conf"
    install_user_file \
        "$repo_root/components/audio/pocketds-speaker-enhancement.py" \
        "$HOME/.local/libexec/pocketds/pocketds-speaker-enhancement" 0755
    install_user_file \
        "$repo_root/components/audio/pocketds-speaker-enhancement.service" \
        "$HOME/.config/systemd/user/pocketds-speaker-enhancement.service"
    install_user_file \
        "$repo_root/components/audio/filter-chain-pocketds-speaker.conf" \
        "$HOME/.config/systemd/user/filter-chain.service.d/90-pocketds-speaker-enhancement.conf"
    install_user_file \
        "$repo_root/components/audio/pocketds-audio-card-ready.py" \
        "$HOME/.local/libexec/pocketds/pocketds-audio-card-ready" 0755
    install_user_file \
        "$repo_root/components/audio/90-pocketds-audio-card-ready.conf" \
        "$HOME/.config/systemd/user/wireplumber.service.d/90-pocketds-audio-card-ready.conf"

    local plasmoid_target="$HOME/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3"
    install_user_file "$repo_root/components/control-panel/plasmoid/metadata.json" \
        "$plasmoid_target/metadata.json"
    for ui_file in ControllerDiagram.qml ControllerTestSession.qml main.qml; do
        install_user_file "$repo_root/components/control-panel/plasmoid/contents/ui/$ui_file" \
            "$plasmoid_target/contents/ui/$ui_file"
    done

    echo '[install] GPU and display telemetry'
    install_user_file "$repo_root/components/telemetry/pocketds-gpu-telemetry.py" \
        "$HOME/.local/libexec/pocketds/pocketds-gpu-telemetry.py" 0755
    install_user_file "$repo_root/scripts/pocketds-gpu-observer.py" \
        "$HOME/.local/libexec/pocketds/pocketds-gpu-observer.py" 0755
    install_user_file "$repo_root/components/telemetry/pocketds-gpu-telemetry.service" \
        "$HOME/.config/systemd/user/pocketds-gpu-telemetry.service"

    echo '[install] keyboard runtime transaction'
    # Keyboard Shift chords observe the same non-grab Type-B decoder as the
    # touchpad, so make it available before the keyboard service is activated.
    install_user_file "$repo_root/components/touchpad/touchpad_raw.py" \
        "$HOME/.local/bin/touchpad_raw.py"
    # Deploy runtime files only; users configure their own API separately.
    # An unconfigured public checkout still supports ordinary keyboard input.
    local asr_transaction="asr-install-$stamp-$BASHPID"
    python3 "$repo_root/scripts/install-asr-api.sh" \
        --stage "$asr_transaction" \
        --confirm POCKETDS-STAGE-ASR-API-SEVEN-FILES
    python3 "$repo_root/scripts/install-asr-api.sh" \
        --apply "$asr_transaction" --activate \
        --confirm POCKETDS-APPLY-ASR-API-SEVEN-FILES
    python3 "$repo_root/scripts/install-asr-api.sh" \
        --verify "$asr_transaction"

    echo '[install] lower-screen touchpad'
    install_user_file "$repo_root/components/touchpad/pocketds-touchpad.py" \
        "$HOME/.local/bin/pocketds-touchpad.py" 0755
    install_user_file "$repo_root/components/touchpad/touchpad_gestures.py" \
        "$HOME/.local/bin/touchpad_gestures.py"
    install_user_file "$repo_root/components/touchpad/touchpad_raw.py" \
        "$HOME/.local/bin/touchpad_raw.py"
    install_user_file "$repo_root/components/touchpad/pocketds-touchpad.service" \
        "$HOME/.config/systemd/user/pocketds-touchpad.service"

    echo '[install] lid light standby'
    install_user_file \
        "$repo_root/components/system/pocketds-lid-mode.py" \
        "$HOME/.local/libexec/pocketds/pocketds-lid-mode" 0755
    install_user_file \
        "$repo_root/components/light-standby/pocketds-light-standby.py" \
        "$HOME/.local/libexec/pocketds/pocketds-light-standby" 0755
    install_user_file \
        "$repo_root/components/light-standby/pocketds-light-standby.service" \
        "$HOME/.config/systemd/user/pocketds-light-standby.service"

    echo '[install] gamepad idle activity (no input remapping)'
    install_user_file "$repo_root/components/gamepad-activity/pocketds-gamepad-activity.py" \
        "$HOME/.local/libexec/pocketds/pocketds-gamepad-activity" 0755
    install_user_file "$repo_root/components/gamepad-activity/pocketds-gamepad-activity.service" \
        "$HOME/.config/systemd/user/pocketds-gamepad-activity.service"

    echo '[install] independent display brightness'
    install_user_file "$repo_root/components/brightness/pocketds-brightness.py" \
        "$HOME/.local/libexec/pocketds/pocketds-brightness" 0755
    install_user_file "$repo_root/components/brightness/pocketds-brightness.service" \
        "$HOME/.config/systemd/user/pocketds-brightness.service"
    "$HOME/.local/libexec/pocketds/pocketds-brightness" initialize --capture-current

    echo '[install] Codex quota'
    install_user_file "$repo_root/components/codex-quota/pocketds-codex-quota" \
        "$HOME/.local/bin/pocketds-codex-quota" 0755
    install_user_file "$repo_root/components/codex-quota/pocketds-codex-quota.service" \
        "$HOME/.config/systemd/user/pocketds-codex-quota.service"
    install_user_file "$repo_root/components/codex-quota/pocketds-codex-quota.timer" \
        "$HOME/.config/systemd/user/pocketds-codex-quota.timer"

    echo '[install] locale and desktop launchers'
    install_user_file "$repo_root/components/locale/90-fcitx5.conf" \
        "$HOME/.config/environment.d/90-fcitx5.conf"
    install_user_file "$repo_root/components/locale/90-fcitx5-wayland.sh" \
        "$HOME/.config/plasma-workspace/env/90-fcitx5-wayland.sh"
    install_user_file "$repo_root/components/locale/org.fcitx.Fcitx5.desktop" \
        "$HOME/.config/autostart/org.fcitx.Fcitx5.desktop"
    for target in "$HOME/.config/gtk-3.0/settings.ini" "$HOME/.config/kwinrc"; do
        [[ ! -L $target && ( ! -e $target || -f $target ) ]] || {
            echo "Refusing non-regular input-method configuration: $target" >&2
            return 1
        }
        backup_user_file "$target"
    done
    mkdir -p "$HOME/.config/gtk-3.0"
    kwriteconfig6 --file "$HOME/.config/gtk-3.0/settings.ini" \
        --group Settings --key gtk-im-module fcitx
    if [[ -f /usr/share/applications/org.fcitx.Fcitx5.desktop ]]; then
        kwriteconfig6 --file kwinrc --group Wayland --key InputMethod \
            /usr/share/applications/org.fcitx.Fcitx5.desktop
        kwriteconfig6 --file kwinrc --group Wayland --key VirtualKeyboardEnabled true
    fi
    echo '  Fcitx Wayland login policy installed; an existing session needs a new login'
    install_user_file "$repo_root/components/locale/config" "$HOME/.config/fcitx5/config"
    install_user_file "$repo_root/components/locale/profile" "$HOME/.config/fcitx5/profile"
    install_user_file "$repo_root/components/locale/classicui.conf" \
        "$HOME/.config/fcitx5/conf/classicui.conf"
    if [[ -d $HOME/.local/share/fcitx5/rime ]]; then
        install_user_file "$repo_root/components/locale/default.custom.yaml" \
            "$HOME/.local/share/fcitx5/rime/default.custom.yaml"
        install_user_file "$repo_root/components/locale/rime_ice.custom.yaml" \
            "$HOME/.local/share/fcitx5/rime/rime_ice.custom.yaml"
    else
        echo '  WARN  Rime Ice data is absent; skipped its local override'
    fi

    install_root_file "$repo_root/components/chromium/pocketds-chromium-v4l2" \
        /usr/local/bin/pocketds-chromium-v4l2 0755
    install_user_file "$repo_root/components/chromium/chromium-browser.desktop" \
        "$HOME/.local/share/applications/chromium-browser.desktop" 0755
    if [[ ! -x /opt/pocketds-chromium-v4l2-151.0.7922.137/usr/lib/chromium/chromium ]]; then
        echo '  WARN  Chromium V4L2 runtime is absent; wrapper was installed only'
    fi

    if [[ $asset_profile == personal-assets ]]; then
        install_user_file "$repo_root/assets/wallpapers/neko-bass-upper.png" \
            "$HOME/Pictures/Wallpapers/neko-bass-upper.png"
        install_user_file "$repo_root/assets/wallpapers/tendou-kei-lower.png" \
            "$HOME/Pictures/Wallpapers/tendou-kei-lower.png"
    else
        echo '  asset-free profile: personal wallpapers are not installed or removed'
    fi
    install_user_file "$repo_root/components/system/pocketds-fancontrol-indicator.desktop" \
        "$HOME/.config/autostart/pocketds-fancontrol-indicator.desktop"
    install_user_file \
        "$repo_root/components/system/org.freedesktop.problems.applet.desktop" \
        "$HOME/.config/autostart/org.freedesktop.problems.applet.desktop"
    install_user_file \
        "$repo_root/components/system/org.freedesktop.problems.applet.service" \
        "$HOME/.local/share/dbus-1/services/org.freedesktop.problems.applet.service"
    if [[ -f $HOME/.config/powerdevilrc ]]; then
        echo '  preserving existing lid, power-button and idle preferences'
    elif sudo test -f /etc/pocketds-linux-kit/daily-suspend.enabled && \
       sudo cmp -s "$repo_root/components/system/daily-suspend.enabled" \
           /etc/pocketds-linux-kit/daily-suspend.enabled; then
        install_user_file "$repo_root/components/system/powerdevilrc.deep" \
            "$HOME/.config/powerdevilrc"
    else
        install_user_file "$repo_root/components/system/powerdevilrc" \
            "$HOME/.config/powerdevilrc"
    fi
    if systemctl --user is-active --quiet plasma-powerdevil.service; then
        busctl --user call \
            org.kde.Solid.PowerManagement \
            /org/kde/Solid/PowerManagement \
            org.kde.Solid.PowerManagement \
            refreshStatus >/dev/null
    fi
    install_user_file "$repo_root/components/system/kscreenlockerrc" \
        "$HOME/.config/kscreenlockerrc"
    install_user_file \
        "$repo_root/components/system/20-pocketds-plasmashell-respawn.conf" \
        "$HOME/.config/systemd/user/plasma-plasmashell.service.d/20-pocketds-respawn.conf"
    install_user_file \
        "$repo_root/components/system/20-pocketds-powerdevil-respawn.conf" \
        "$HOME/.config/systemd/user/plasma-powerdevil.service.d/20-pocketds-respawn.conf"
    install_user_file "$repo_root/components/system/kwinrulesrc.current" \
        "$HOME/.config/kwinrulesrc"

    if [[ -d $HOME/.local/share/Steam ]]; then
        install_user_file "$repo_root/components/steam/launch-steam.sh" \
            "$HOME/.local/share/Steam/launch-steam.sh" 0755
        install_user_file \
            "$repo_root/components/steam/pocketds-steam-game-fps" \
            "$HOME/.local/bin/pocketds-steam-game-fps" 0755
        install_user_file \
            "$repo_root/components/steam/pocketds-steam-arm64-workarounds" \
            "$HOME/.local/bin/pocketds-steam-arm64-workarounds" 0755
    else
        echo '  WARN  Steam runtime is absent; skipped Steam user files'
    fi

    # Recovery is a confirmation-gated oneshot.  Load it, but never enable or
    # start it as part of installation.
    systemctl --user daemon-reload
    # Retire the vendor Onboard listener before enabling the project keyboard.
    # A mask is deterministic on upgraded images and harmless when the legacy
    # unit is absent on a fresh pds2 composition.
    systemctl --user mask --now pocketds-osk-listener.service
    # Fedora replays every retained coredump through DrKonqi on login.  Keep
    # coredump collection and the live-crash socket, but skip historical GUI
    # replay and the redundant ABRT notification applet on this handheld.
    systemctl --user mask --now drkonqi-coredump-pickup.service
    systemctl --user stop 'drkonqi-coredump-launcher@*.service' 2>/dev/null || true
    systemctl --user stop \
        app-org.freedesktop.problems.applet@autostart.service 2>/dev/null || true
    systemctl --user enable pocketds-gpu-telemetry.service
    systemctl --user restart pocketds-gpu-telemetry.service
    systemctl --user enable pocketds-speaker-enhancement.service
    start_updated_user_service filter-chain.service \
        "$HOME/.config/pipewire/filter-chain.conf.d/90-pocketds-speaker-enhancement.conf" \
        "$HOME/.config/systemd/user/filter-chain.service.d/90-pocketds-speaker-enhancement.conf"
    start_updated_user_service pocketds-speaker-enhancement.service \
        "$HOME/.local/libexec/pocketds/pocketds-speaker-enhancement" \
        "$HOME/.config/systemd/user/pocketds-speaker-enhancement.service" \
        "$HOME/.config/pipewire/filter-chain.conf.d/90-pocketds-speaker-enhancement.conf" \
        "$HOME/.config/systemd/user/filter-chain.service.d/90-pocketds-speaker-enhancement.conf"
    # The seven-file transaction above already started the exact deployed
    # keyboard generation. Enabling it here must not introduce another restart.
    systemctl --user enable pocketds-keyboard.service
    systemctl --user enable pocketds-touchpad.service pocketds-light-standby.service pocketds-gamepad-activity.service
    start_updated_user_service pocketds-touchpad.service \
        "$HOME/.local/bin/pocketds-touchpad.py" \
        "$HOME/.local/bin/touchpad_gestures.py" \
        "$HOME/.local/bin/touchpad_raw.py" \
        "$HOME/.config/systemd/user/pocketds-touchpad.service"
    start_updated_user_service pocketds-light-standby.service \
        "$HOME/.local/libexec/pocketds/pocketds-light-standby" \
        "$HOME/.config/systemd/user/pocketds-light-standby.service"
    start_updated_user_service pocketds-gamepad-activity.service \
        "$HOME/.local/libexec/pocketds/pocketds-gamepad-activity" \
        "$HOME/.config/systemd/user/pocketds-gamepad-activity.service"
    rm -f -- "$activation_journal"
    systemctl --user enable pocketds-brightness.service
    systemctl --user restart pocketds-brightness.service
    systemctl --user enable --now pocketds-codex-quota.timer
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$HOME/.local/share/applications" || true
    command -v kbuildsycoca6 >/dev/null 2>&1 && \
        kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
    "$repo_root/scripts/install-desktop-shortcuts.sh"
}

install_system() {
    echo '[install] privileged system layer'
    install_root_file "$repo_root/components/audio/HiFi.conf" \
        /usr/share/alsa/ucm2/Qualcomm/sm8550/APS/HiFi.conf
    install_root_file "$repo_root/components/audio/SM8550-APS.conf" \
        /usr/share/alsa/ucm2/Qualcomm/sm8550/APS/SM8550-APS.conf

    install_root_file "$repo_root/components/system/umtprd.conf" \
        /etc/umtprd/umtprd.conf

    wait_online_dropin=/etc/systemd/system/NetworkManager-wait-online.service.d/10-pocketds-fast-online.conf
    if sudo test -e "$wait_online_dropin"; then
        if ! sudo grep -qs \
            '^ExecStart=/usr/bin/nm-online -s -q --timeout=5$' \
            "$wait_online_dropin"; then
            echo "Refusing to remove an unrecognized wait-online drop-in: $wait_online_dropin" >&2
            exit 1
        fi
        backup_root_file "$wait_online_dropin"
        sudo rm -f "$wait_online_dropin"
        sudo rmdir "$(dirname "$wait_online_dropin")" 2>/dev/null || true
    fi
    install_root_file "$repo_root/components/system/90-pocketds-stop-timeout.conf" \
        /etc/systemd/system/user@.service.d/90-pocketds-stop-timeout.conf
    install_root_file "$repo_root/components/system/80-pocketds-pm.conf" \
        /etc/tmpfiles.d/80-pocketds-pm.conf
    install_root_file "$repo_root/components/system/80-pocketds-sleep.conf" \
        /etc/systemd/sleep.conf.d/80-pocketds-sleep.conf
    install_root_file "$repo_root/components/system/80-pocketds-lid-safety.conf" \
        /etc/systemd/logind.conf.d/80-pocketds-lid-safety.conf
    install_root_file "$repo_root/components/system/90-pocketds-critical-power.conf" \
        /etc/UPower/UPower.conf.d/90-pocketds-critical-power.conf
    install_root_file "$repo_root/components/system/pocketds-lid-switch.py" \
        /usr/local/libexec/pocketds-lid-switch 0755
    install_root_file "$repo_root/components/system/pocketds-lid-switch.service" \
        /etc/systemd/system/pocketds-lid-switch.service
    install_root_file "$repo_root/components/system/90-pocketds-hardware-protection.conf" \
        /etc/dnf/libdnf5.conf.d/90-pocketds-hardware-protection.conf
    install_root_file "$repo_root/components/system/pocketds-gmu-runtime" \
        /usr/local/libexec/pocketds-gmu-runtime 0755
    install_root_file "$repo_root/components/system/pocketds-gmu-runtime.service" \
        /etc/systemd/system/pocketds-gmu-runtime.service
    install_root_file "$repo_root/components/system/kwinrulesrc.current" \
        /etc/skel/.config/kwinrulesrc
    install_root_file "$repo_root/components/network/pocketds-home.xml" \
        /etc/firewalld/zones/pocketds-home.xml
    install_root_file "$repo_root/components/system/pocketds-usb-wake-policy" \
        /usr/local/libexec/pocketds-usb-wake-policy 0755
    for rule in 60-pocketds-i2c-ddc.rules 61-pocketds-touchscreens.rules \
        70-uinput.rules 71-pocketds-usb-wakeup.rules; do
        install_root_file "$repo_root/components/system/$rule" "/etc/udev/rules.d/$rule"
    done

    sudo systemctl disable --now pocketds-power-button.service 2>/dev/null || true
    sudo systemctl enable NetworkManager-wait-online.service
    sudo systemctl reset-failed NetworkManager-wait-online.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/pocketds-power-button.service \
        /usr/local/libexec/pocketds-power-button
    sudo systemctl daemon-reload
    sudo systemctl enable --now pocketds-lid-switch.service
    sudo systemctl enable --now pocketds-gmu-runtime.service
    sudo systemctl restart upower.service
    if [[ $(upower -d | sed -n \
        's/^[[:space:]]*critical-action:[[:space:]]*//p') != PowerOff ]]; then
        echo 'UPower did not select the clean critical-battery poweroff action.' >&2
        exit 1
    fi
    sudo systemctl start NetworkManager-wait-online.service
    sudo udevadm control --reload-rules
    sudo /usr/local/libexec/pocketds-usb-wake-policy
    sudo systemd-tmpfiles --create /etc/tmpfiles.d/80-pocketds-pm.conf
    command -v firewall-offline-cmd >/dev/null 2>&1 || {
        echo 'Missing required command: firewall-offline-cmd' >&2
        exit 1
    }
    backup_root_file /etc/firewalld/firewalld.conf
    if [[ $(sudo firewall-offline-cmd --get-default-zone) != public ]]; then
        sudo firewall-offline-cmd --set-default-zone=public
    fi
    sudo firewall-offline-cmd --check-config
    sudo systemctl restart firewalld
    sudo firewall-cmd --state >/dev/null
    echo '  System files installed. Reboot to apply logind/input/audio/session changes safely.'
}

"$repo_root/scripts/lint.sh"
# InputPlumber state, its system listener, all three launchers, the Wiliwili
# database, and both desktop entries form one generation.  Never install any
# subset from the generic application/system phases. Publish the trusted
# controller-test gate before the keyboard generation imports it.
echo '[install] transactional game/input stack'
python3 "$repo_root/scripts/install-game-input-stack.py"
install_apps
if [[ $mode == all ]]; then
    install_system
fi

echo "[install] complete; user backup: $user_backup"
if [[ $mode == all ]]; then
    echo "[install] system backup: $root_backup"
fi
if ((haptics_ready == 0)); then
    echo '[install] INCOMPLETE: keyboard and game vibration need the pinned InputPlumber haptics sidecar' >&2
fi
