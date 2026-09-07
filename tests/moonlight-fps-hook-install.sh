#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
setup="$repo_root/components/moonlight/pocketds-moonlight-fps-setup"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/home" "$fixture/bin"
app_id=com.moonlight_stream.Moonlight
user_override="$fixture/home/.local/share/flatpak/overrides/$app_id"
mkdir -p "$(dirname "$user_override")"

cat >"$fixture/system-override" <<'EOF'
[Environment]
SYSTEM_ONLY=keep-me-too
LD_PRELOAD=/opt/system.so
EOF

cat >"$fixture/bin/flatpak" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$*" >>"$MOCK_FLATPAK_LOG"
case "$1 ${2:-} ${3:-}" in
    'info --user '*) exit 1 ;;
    'info --system '*) exit 0 ;;
    'override --user --show')
        if [[ -e $MOCK_USER_OVERRIDE ]]; then
            cat "$MOCK_USER_OVERRIDE"
        fi
        ;;
    'override --system --show') cat "$MOCK_SYSTEM_OVERRIDE" ;;
    'run --system --command=sh') exit 0 ;;
    'override --user --env='*)
        mkdir -p "$(dirname "$MOCK_USER_OVERRIDE")"
        {
            printf '%s\n' '[Environment]'
            for argument in "$@"; do
                if [[ $argument == --env=* ]]; then
                    printf '%s\n' "${argument#--env=}"
                fi
            done
        } >"$MOCK_USER_OVERRIDE"
        chmod 0644 "$MOCK_USER_OVERRIDE"
        ;;
    'override --user --reset')
        rm -f -- "$MOCK_USER_OVERRIDE"
        ;;
    *)
        printf 'unexpected mock flatpak invocation: %s\n' "$*" >&2
        exit 90
        ;;
esac
MOCK
chmod 0755 "$fixture/bin/flatpak"

common_env=(
    HOME="$fixture/home"
    XDG_RUNTIME_DIR="$fixture/runtime"
    POCKETDS_FLATPAK="$fixture/bin/flatpak"
    MOCK_FLATPAK_LOG="$fixture/flatpak.log"
    MOCK_USER_OVERRIDE="$user_override"
    MOCK_SYSTEM_OVERRIDE="$fixture/system-override"
)
env "${common_env[@]}" "$setup" install >"$fixture/install.out"

app_root="$fixture/home/.var/app/com.moonlight_stream.Moonlight"
library="$app_root/data/pocketds-linux-kit/moonlight-fps/libpocketds-moonlight-fps-hook.so"
output="$fixture/runtime/app/com.moonlight_stream.Moonlight/pocketds-rendered-fps"
[[ -f $library && $(stat -c '%a' "$library") == 600 ]]
[[ $(stat -c '%a' "$(dirname "$library")") == 700 ]]
grep -Fq -- "--env=LD_PRELOAD=/opt/system.so:$library" \
    "$fixture/flatpak.log"
grep -Fq -- "--env=POCKETDS_MOONLIGHT_FPS_FILE=$output" \
    "$fixture/flatpak.log"
grep -Fq -- '--command=sh' "$fixture/flatpak.log"
grep -Fq -- '/proc/$$/maps' "$fixture/flatpak.log"
if grep -Eq -- '--reset|--nofilesystem|--nosocket|--nodevice' \
    "$fixture/flatpak.log"; then
    echo 'installer touched unrelated Flatpak override classes' >&2
    exit 1
fi

cat >"$fixture/bin/readelf-too-new" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' '  0x0010: Name: GLIBC_2.43  Flags: none  Version: 2'
MOCK
chmod 0755 "$fixture/bin/readelf-too-new"
: >"$fixture/flatpak.log"
if env "${common_env[@]}" HOME="$fixture/home-too-new" \
    POCKETDS_READELF="$fixture/bin/readelf-too-new" \
    "$setup" install >"$fixture/too-new.out" 2>"$fixture/too-new.err"; then
    echo 'installer accepted a hook requiring GLIBC 2.43' >&2
    exit 1
fi
grep -Fq 'newer than Moonlight runtime 2.42' "$fixture/too-new.err"
if grep -Fq 'override --user --env=' "$fixture/flatpak.log"; then
    echo 'installer changed overrides before rejecting incompatible GLIBC' >&2
    exit 1
fi

: >"$fixture/flatpak.log"
env "${common_env[@]}" "$setup" uninstall >"$fixture/uninstall.out"
[[ ! -e $library ]]
[[ ! -e $user_override ]]
grep -Fq -- 'override --user --reset' "$fixture/flatpak.log"
if grep -Fq -- '--unset-env=' "$fixture/flatpak.log"; then
    echo 'uninstaller left an explicit unset instead of restoring absence' >&2
    exit 1
fi

: >"$fixture/flatpak.log"
env "${common_env[@]}" "$setup" install >"$fixture/reinstall.out"
printf '\nUNRELATED=added-later\n' >>"$user_override"
cp "$user_override" "$fixture/drift-snapshot"
if env "${common_env[@]}" "$setup" uninstall \
    >"$fixture/drift-uninstall.out" 2>"$fixture/drift-uninstall.err"; then
    echo 'uninstaller ignored a later override change' >&2
    exit 1
fi
grep -Fq 'changed after installation' "$fixture/drift-uninstall.err"
cmp -s "$user_override" "$fixture/drift-snapshot"
[[ -f $library ]]
if grep -Fq -- 'override --user --reset' "$fixture/flatpak.log"; then
    echo 'drifted override was reset' >&2
    exit 1
fi

echo '  [OK] Moonlight setup restores an absent override exactly and refuses drift'
