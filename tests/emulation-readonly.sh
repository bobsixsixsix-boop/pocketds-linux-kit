#!/usr/bin/env bash
# Read-only emulation frontend preflight. It never starts a GUI or reads ROM
# contents; only aggregate file counts are reported.
set -Eeuo pipefail

if [[ $(uname -s) != Linux ]]; then
    echo '[emulation] skipped outside Linux'
    exit 0
fi

failures=0
warnings=0
ok() { echo "  [OK] $*"; }
warn() { echo "  [WARN] $*"; warnings=$((warnings + 1)); }
fail() { echo "  [FAIL] $*"; failures=$((failures + 1)); }

appimage="$HOME/Applications/ES-DE_aarch64.AppImage"
settings="$HOME/ES-DE/settings/es_settings.xml"
systems="$HOME/ES-DE/custom_systems/es_systems.xml"
input_mode="$HOME/.local/libexec/pocketds-input-mode"
shared_root_file="$HOME/.config/pocketds-linux-kit/shared-library-root"
expected_shared_root=/mnt/pocketds-games

if [[ -e $shared_root_file || -L $shared_root_file ]]; then
    if [[ ! -f $shared_root_file || -L $shared_root_file ]]; then
        fail "shared game library configuration is unsafe"
    else
        shared_root=
        shared_root_extra=
        exec 3<"$shared_root_file"
        IFS= read -r shared_root <&3 || true
        if IFS= read -r shared_root_extra <&3 || [[ -n $shared_root_extra ]]; then
            fail "shared game library root must contain exactly one line"
        elif [[ $shared_root != "$expected_shared_root" ]]; then
            fail "shared game library root is not the managed mount"
        elif [[ ! -d $shared_root/Roms \
            || ! -d $shared_root/PocketDS/Frontends/ES-DE \
            || ! -f $shared_root/PocketDS/Manifests/shared-library.json \
            || -L $shared_root/PocketDS/Frontends \
            || -L $shared_root/PocketDS/Frontends/ES-DE ]]; then
            fail "shared game library is incomplete or unsafe"
        elif ! command -v mountpoint >/dev/null 2>&1 \
            || ! mountpoint -q -- "$shared_root"; then
            fail "shared game library is not mounted"
        else
            settings="$shared_root/PocketDS/Frontends/ES-DE/settings/es_settings.xml"
            systems="$shared_root/PocketDS/Frontends/ES-DE/custom_systems/es_systems.xml"
            ok "shared game library is mounted at the managed root"
        fi
        exec 3<&-
    fi
fi

echo '[emulation] ES-DE runtime and current paths'
if [[ -x $appimage ]]; then
    ok "ES-DE AppImage executable; sha256=$(sha256sum "$appimage" | cut -d' ' -f1)"
else
    fail "ES-DE AppImage missing or not executable: $appimage"
fi

rom_directory=''
if [[ -r $settings ]]; then
    rom_directory=$(sed -n \
        's/.*<string name="ROMDirectory" value="\([^"]*\)".*/\1/p' \
        "$settings" | head -1)
    if [[ -n $rom_directory && -d $rom_directory ]]; then
        ok "ROMDirectory is configured and exists"
    elif [[ -n $rom_directory ]]; then
        warn 'ROMDirectory is configured but absent'
    else
        fail 'ROMDirectory is empty or missing from ES-DE 3.x settings'
    fi
else
    fail "ES-DE settings missing: $settings"
fi

if [[ -s $systems ]]; then
    system_count=$(grep -c '<system>' "$systems" || true)
    (( system_count > 0 )) && ok "$system_count custom systems installed" || \
        fail 'custom systems file contains no systems'
else
    fail "custom systems missing: $systems"
fi

if [[ -x $input_mode ]]; then
    mode=$($input_mode status || true)
    case $mode in
        gamepad|joymouse) ok "InputPlumber mode is $mode" ;;
        *) fail "InputPlumber mode unavailable or unmanaged: ${mode:-empty}" ;;
    esac
else
    fail "input mode helper missing: $input_mode"
fi

if [[ -d $rom_directory ]]; then
    regular_count=$(find "$rom_directory" -type f 2>/dev/null | wc -l)
    non_text_count=$(find "$rom_directory" -type f ! -name '*.txt' 2>/dev/null | wc -l)
    ok "$regular_count regular ROM-tree files found by metadata only"
    (( non_text_count > 0 )) || warn 'no non-.txt game files are currently available for launch testing'
fi

echo "[emulation] complete: $failures failure(s), $warnings warning(s)"
(( failures == 0 ))
