#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
#
# Switchdeck by SildurFX | https://github.com/SildurFX/Switchdeck
# Upstream license: GPLv3. Pocket DS adaptation, verified 2026-09-07 against
# Azkali/Switchdeck@2b7f9e2fbe1e79a92789b62e29562751d4f717e5.
# This reference identifies the verified upstream baseline, not the unknown
# original copy revision. Runtime statements are retained during this review.
# Full license/notice and adaptation record: licenses/Switchdeck-SOURCES.json,
# licenses/Switchdeck-GPL-3.0.txt, licenses/Switchdeck-NOTICE.txt.

# Managed Gamescope sessions keep the original Steam Deck UI.  Only the
# separate KDE desktop-return service overrides this with false.
STEAMDECK_MODE="${STEAMDECK_MODE:-true}"

# Wine
export WINEESYNC=0
export STAGING_WRITECOPY=1
export STAGING_SHARED_MEMORY=1
export __GL_THREADED_OPTIMIZATIONS=1

# DXVK
export DXVK_ALL_CORES=1

# Disable logging
export WINEDEBUG=-all
export DXVK_LOG_LEVEL=none

# Steam launch flags
STEAM_FLAGS=""
STEAM_FLAGS+=" -vrskip"
STEAM_FLAGS+=" -fasthtml"
STEAM_FLAGS+=" -vrdisable"
STEAM_FLAGS+=" -noverifyfiles"
STEAM_FLAGS+=" -nocrashmonitor"
STEAM_FLAGS+=" -no-cef-sandbox"
STEAM_FLAGS+=" -cef-disable-sandbox"
STEAM_FLAGS+=" -cef-single-process"
STEAM_FLAGS+=" -cef-in-process-gpu"
STEAM_FLAGS+=" -cef-disable-breakpad"
STEAM_FLAGS+=" -cef-disable-js-logging"
STEAM_FLAGS+=" -cef-disable-seccomp-sandbox"

if [ "$STEAMDECK_MODE" = "true" ]; then
    STEAM_FLAGS+=" -steampal"
    STEAM_FLAGS+=" -gamepadui"
    STEAM_FLAGS+=" -steamdeck"
fi

set -o pipefail
shopt -s failglob
set -u

log () { echo "launch-steam.sh[$$]: $*" >&2 || :; }

if [ -t 1 ]; then
    echo "Debug Mode Active (Terminal Detected)"
    set -x
    export BOX64_LOG=1
    export WINEDEBUG=""
    export DXVK_LOG_LEVEL=info
else
    exec > /dev/null 2>&1
fi

export TEXTDOMAIN=steam
export TEXTDOMAINDIR=/usr/share/locale

MAGIC_RESTART_EXITCODE=42
STEAMROOT="$HOME/.local/share/Steam"
STEAMHOME="$HOME/.steam"
CEF_PATH="$STEAMROOT/steamrtarm64/steamwebhelper"

# Steam's gamepad UI does not use its inherited PATH for "Switch to Desktop".
# steamui rebuilds PATH from SYSTEM_PATH before executing
# `steamos-session-select desktop`; the container runtime normally supplies a
# host-only SYSTEM_PATH that omits ~/.local/bin.  Keep the user selector first
# so the action stops only the nested managed Steam/Gamescope session instead
# of reaching the packaged selector that logs the whole KDE user out.
host_system_path=${SYSTEM_PATH:-${PATH:-/usr/local/bin:/usr/bin:/bin}}
case ":$host_system_path:" in
    *":$HOME/.local/bin:"*) ;;
    *) host_system_path="$HOME/.local/bin:$host_system_path" ;;
esac
export SYSTEM_PATH=$host_system_path

if [ ! -f "$STEAMROOT/.switchdeck-initial-launch" ]; then
    log "creating initial symlinks"
    ln -fsn "$STEAMROOT" "$STEAMHOME/root"
    ln -fsn "$STEAMROOT" "$STEAMHOME/steam"
    ln -fsn "$STEAMROOT/linux32" "$STEAMHOME/sdk32"
    ln -fsn "$STEAMROOT/linux64" "$STEAMHOME/sdk64"
    ln -fsn "$STEAMROOT/linuxarm64" "$STEAMHOME/sdkarm64"
    ln -fsn "$STEAMROOT/ubuntu12_32" "$STEAMHOME/bin32"
    ln -fsn "$STEAMROOT/ubuntu12_64" "$STEAMHOME/bin64"
    ln -fsn "$STEAMHOME/bin32" "$STEAMHOME/bin"
    ln -fsn "$STEAMROOT/steamrtarm64" "$STEAMROOT/steamrtarm32"

    mkdir -p "$HOME/.local/bin"
    ln -fsn "$STEAMROOT/launch-steam.sh" "$HOME/.local/bin/steam"

    MENU_DIR="$HOME/.local/share/applications"
    mkdir -p "$MENU_DIR"

    DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
    mkdir -p "$DESKTOP_DIR"

    DESKTOP_FILE="$MENU_DIR/Steam.desktop"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=Steam
Comment=Launch Steam in the unified Pocket DS game runtime
TryExec=$HOME/.local/bin/pocketds-steam-session
Exec=env STEAMDECK_MODE=true $HOME/.local/bin/pocketds-steam-session %U
Icon=$STEAMROOT/public/steam_tray_48.tga
Terminal=false
Type=Application
Categories=Game;
MimeType=x-scheme-handler/steam;
Actions=GamepadUI;

[Desktop Action GamepadUI]
Name=大屏幕模式（实验性）
Exec=env STEAMDECK_MODE=true $HOME/.local/bin/pocketds-steam-session -gamepadui
EOF
    chmod +x "$DESKTOP_FILE"
    ln -fs "$DESKTOP_FILE" "$DESKTOP_DIR/Steam.desktop"
    update-desktop-database "$MENU_DIR" 2>/dev/null

    touch "$STEAMROOT/.switchdeck-initial-launch"
fi

# Pocket DS: pin Proton 11 ARM64 as the default Steam Play tool before
# Steam starts (idempotent — exits 0 once the marker is set).
if command -v pocketds-steam-default-proton11 >/dev/null 2>&1; then
    pocketds-steam-default-proton11 || \
        log "default-proton11 failed (continuing); see stderr above"
fi

# Reapply title-specific FEX workarounds after Steam updates or file checks.
arm64_workarounds="$HOME/.local/bin/pocketds-steam-arm64-workarounds"
if [ -x "$arm64_workarounds" ] && [ ! -L "$arm64_workarounds" ]; then
    "$arm64_workarounds" || \
        log "ARM64 game workaround failed (continuing); see stderr above"
fi

# Switchdeck Gamemode. Keep the watcher owned by this launcher: upstream leaves
# its background loop alive after Steam exits, which makes the next managed
# launch look like an unmanaged Steam client and can leave a second virtual
# controller behind.
if [ -f "${CEF_PATH}.bak" ]; then
    if file "$CEF_PATH" | grep -q "shell script"; then
        mv -f "${CEF_PATH}.bak" "$CEF_PATH"
    fi
fi

switchdeck_watcher_pid=
plasma_stop_marker="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/pocketds-switchdeck-plasma-stopped.$$"

restore_switchdeck_plasma() {
    [[ -e $plasma_stop_marker ]] || return 0
    rm -f -- "$plasma_stop_marker"
    systemctl --user reset-failed plasma-plasmashell.service 2>/dev/null || true
    systemctl --user start plasma-plasmashell.service 2>/dev/null || true
}

cleanup_switchdeck_watcher() {
    local pid=${switchdeck_watcher_pid:-}
    switchdeck_watcher_pid=
    if [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
    restore_switchdeck_plasma
}
trap cleanup_switchdeck_watcher EXIT

(
    while true; do
        until pgrep -u $USER -x steam > /dev/null; do sleep 60; done

        RAW_MATCH=$(pgrep -af "SWITCHDECK_GAMEMODE=" | grep -v "$$" | head -n1)

        if [[ "$RAW_MATCH" == *"SWITCHDECK_GAMEMODE="* ]] && [ ! -f "${CEF_PATH}.bak" ]; then
            case "$RAW_MATCH" in *"=2"*) FLAG=2 ;; *) FLAG=1 ;; esac

            if file "$CEF_PATH" | grep -q "ELF"; then
                cp -p "$CEF_PATH" "${CEF_PATH}.bak"
                echo -e "#!/bin/bash\nexit 0" > "${CEF_PATH}.tmp"
                chmod +x "${CEF_PATH}.tmp"
                mv -f "${CEF_PATH}.tmp" "$CEF_PATH"
                killall -9 steamwebhelper 2>/dev/null
            fi

            if [ "$FLAG" -eq 2 ]; then
                : > "$plasma_stop_marker"
                if ! systemctl --user stop plasma-plasmashell.service 2>/dev/null; then
                    rm -f -- "$plasma_stop_marker"
                fi
                killall -9 krunner kded5 kdeconnectd DiscoverNotifie onboard 2>/dev/null
            fi

            while pgrep -f "SWITCHDECK_GAMEMODE=" | grep -v "$$" > /dev/null; do sleep 20; done
            sleep 5

            if [ -f "${CEF_PATH}.bak" ] && file "${CEF_PATH}.bak" | grep -q "ELF"; then
                mv -f "${CEF_PATH}.bak" "$CEF_PATH"
                sync
            fi

            if [ "$FLAG" -eq 2 ]; then
                restore_switchdeck_plasma
                { kstart5 krunner & } >/dev/null 2>&1
            fi
        fi
        sleep 15
    done
) &
switchdeck_watcher_pid=$!

if [ -x "$STEAMROOT/steamrtarm64/steam" ]; then
    log "Starting Steam"
    _rtarm=$(ls -d "$STEAMROOT/steamrtarm64/pv-runtime/steam-runtime-steamrt-arm64"/steamrt3c_platform_*/files 2>/dev/null | head -1)
    export LD_LIBRARY_PATH="$STEAMROOT/steamrtarm64${_rtarm:+:$_rtarm/lib/aarch64-linux-gnu:$_rtarm/lib}:${LD_LIBRARY_PATH-}"

    "$STEAMROOT/steamrtarm64/steam" "$@" $STEAM_FLAGS

    STATUS=$?

    if [ $STATUS -eq $MAGIC_RESTART_EXITCODE ] ; then
        log "Restarting Steam by request"
        cleanup_switchdeck_watcher
        trap - EXIT
        exec "$0" "$@"
    fi
    exit $STATUS
fi
