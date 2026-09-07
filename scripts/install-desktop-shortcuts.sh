#!/usr/bin/env bash
# Install launchers only for applications that are actually present.
set -Eeuo pipefail

if [[ $EUID -eq 0 ]]; then
    echo 'Run this script as the desktop user, not root.' >&2
    exit 2
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
desktop_dir=${POCKETDS_DESKTOP_DIR:-$(xdg-user-dir DESKTOP 2>/dev/null || true)}
app_dir=${POCKETDS_APPLICATION_DIR:-$HOME/.local/share/applications}
flatpak_dir=${POCKETDS_FLATPAK_EXPORT_DIR:-$HOME/.local/share/flatpak/exports/share/applications}
state_root=${POCKETDS_SHORTCUT_STATE_ROOT:-$HOME/.local/state/pocketds-linux-kit/desktop-shortcut-transactions}
tailscale_bin=${POCKETDS_TAILSCALE_BIN:-/usr/bin/tailscale}
chromium_runtime_bin=${POCKETDS_CHROMIUM_RUNTIME_BIN:-/opt/pocketds-chromium-v4l2-151.0.7922.137/usr/lib/chromium/chromium}
steam_session_bin=${POCKETDS_STEAM_SESSION_BIN:-$HOME/.local/bin/pocketds-steam-session}
android_switch_bin=${POCKETDS_ANDROID_SWITCH_BIN:-/usr/local/libexec/pocketds-switch-to-android}

[[ -n $desktop_dir && $desktop_dir == "$HOME"/* && ! -L $desktop_dir ]] || {
    echo "Refusing unsafe desktop directory: $desktop_dir" >&2
    exit 1
}
mkdir -p "$desktop_dir" "$state_root"
[[ -d $desktop_dir && -O $desktop_dir ]] || {
    echo "Desktop directory is not owned by the current user: $desktop_dir" >&2
    exit 1
}

stamp=$(date -u +%Y%m%dT%H%M%SZ)
umask 077
transaction=$(mktemp -d "$state_root/desktop-shortcuts-$stamp.XXXXXX")
: >"$transaction/installed.tsv"

install_shortcut() {
    local name=$1 source=$2 resolved target temporary
    [[ $name != */ && $name == *.desktop ]] || return 2
    [[ -f $source ]] || return 0
    resolved=$(readlink -f -- "$source")
    [[ -n $resolved && -f $resolved && ! -L $resolved ]] || {
        echo "Refusing unsafe launcher source: $source" >&2
        exit 1
    }
    target="$desktop_dir/$name"
    if [[ -e $target || -L $target ]]; then
        cp -a --no-dereference -- "$target" "$transaction/$name.before"
    fi
    temporary=$(mktemp "$desktop_dir/.pocketds-shortcut.XXXXXX")
    install -m 0755 -- "$resolved" "$temporary"
    mv -f -- "$temporary" "$target"
    printf '%s\t%s\n' "$name" "$resolved" >>"$transaction/installed.tsv"
}

install_shortcut Steam.desktop "$app_dir/Steam.desktop"
install_shortcut Moonlight.desktop "$flatpak_dir/com.moonlight_stream.Moonlight.desktop"
install_shortcut Wiliwili.desktop "$app_dir/cn.xfangfang.wiliwili.desktop"
install_shortcut ES-DE.desktop "$app_dir/ES-DE.desktop"
if [[ -x $android_switch_bin && ! -L $android_switch_bin ]]; then
    install_shortcut '切换到 Android.desktop' "$app_dir/pocketds-switch-to-android.desktop"
fi
if [[ -x $steam_session_bin && ! -L $steam_session_bin ]]; then
    [[ ! -f "$app_dir/Stardew Valley.desktop" ]] || \
        install_shortcut 'Stardew Valley.desktop' \
            "$repo_root/components/steam/Stardew Valley.desktop"
    [[ ! -f "$app_dir/土豆兄弟(Brotato).desktop" ]] || \
        install_shortcut '土豆兄弟.desktop' \
            "$repo_root/components/steam/Brotato.desktop"
fi
if [[ -x $chromium_runtime_bin && ! -L $chromium_runtime_bin ]]; then
    install_shortcut Chromium.desktop "$app_dir/chromium-browser.desktop"
fi
install_shortcut melonDS.desktop "$flatpak_dir/net.kuribo64.melonDS.desktop"
if [[ -x $tailscale_bin && ! -L $tailscale_bin ]]; then
    install_shortcut Tailscale.desktop "$repo_root/components/desktop/tailscale.desktop"
fi

count=$(wc -l <"$transaction/installed.tsv")
((count > 0)) || {
    echo 'No installed application launchers were found.' >&2
    exit 1
}
printf 'Installed %d desktop shortcuts.\n' "$count"
printf 'Transaction: %s\n' "$transaction"
