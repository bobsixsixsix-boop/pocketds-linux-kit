#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
runtime="$repo_root/components/game-runtime/pocketds-game-runtime"
inner="$repo_root/components/game-runtime/pocketds-gamescope-inner"
limiter="$repo_root/components/game-runtime/pocketds-game-limit"
kwin_script="$repo_root/components/game-runtime/kwin-gamescope-top-screen.js"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/bin" "$fixture/run"

cat >"$fixture/bin/qdbus" <<'MOCK'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >>"$MOCK_QDBUS_LOG"
case " $* " in
    *" org.kde.kwin.Scripting.loadScript "*) printf '23\n' ;;
esac
MOCK
cat >"$fixture/bin/kscreen-doctor" <<'MOCK'
#!/usr/bin/env bash
set -eu
mode=$(sed -n '1p' "$MOCK_KSCREEN_STATE")
if [ "${1-}" = -j ]; then
    if [ "${MOCK_KSCREEN_NO_120:-0}" = 1 ]; then
        printf '{"outputs":[{"name":"DSI-1","enabled":true,"currentModeId":"%s","modes":[{"id":"5","refreshRate":90,"size":{"width":1080,"height":1920}},{"id":"6","refreshRate":60,"size":{"width":1080,"height":1920}}]}]}\n' "$mode"
    else
        printf '{"outputs":[{"name":"DSI-1","enabled":true,"currentModeId":"%s","modes":[{"id":"4","refreshRate":120,"size":{"width":1080,"height":1920}},{"id":"5","refreshRate":90,"size":{"width":1080,"height":1920}},{"id":"6","refreshRate":60,"size":{"width":1080,"height":1920}}]}]}\n' "$mode"
    fi
    exit 0
fi
printf '%s\n' "$*" >>"$MOCK_KSCREEN_LOG"
case "${1-}" in
    output.DSI-1.mode.*)
        new_mode=${1##*.}
        if [ "$new_mode" = 4 ] && [ "${MOCK_KSCREEN_SWITCH_FAIL:-0}" = 1 ]; then
            exit 1
        fi
        if [ "$new_mode" = 4 ] && [ "${MOCK_KSCREEN_IGNORE_SWITCH:-0}" = 1 ]; then
            exit 0
        fi
        printf '%s\n' "$new_mode" >"$MOCK_KSCREEN_STATE"
        ;;
    *) exit 64 ;;
esac
MOCK
cat >"$fixture/bin/gamescope" <<'MOCK'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$@" >"$MOCK_GAMESCOPE_LOG"
while [ "$1" != -- ]; do shift; done
shift
export GAMESCOPE_WAYLAND_DISPLAY=gamescope-test
export DISPLAY=:77
exec "$@"
MOCK
cat >"$fixture/bin/observer" <<'MOCK'
#!/usr/bin/env bash
printf 'observer\n' >"$MOCK_OBSERVER_LOG"
exit 0
MOCK
cat >"$fixture/bin/flock" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
cat >"$fixture/bin/app" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$@" >"$MOCK_APP_LOG"
printf 'DISPLAY=%s\n' "$DISPLAY" >>"$MOCK_APP_LOG"
MOCK
chmod 0755 "$fixture/bin/"*
printf '5\n' >"$fixture/kscreen-mode"
: >"$fixture/kscreen.log"

common_env=(
    HOME="$fixture"
    XDG_RUNTIME_DIR="$fixture/run"
    WAYLAND_DISPLAY=wayland-test
    POCKETDS_GAMESCOPE="$fixture/bin/gamescope"
    POCKETDS_GAMESCOPE_INNER="$inner"
    POCKETDS_GAME_LIMIT="$limiter"
    POCKETDS_GAMESCOPE_OBSERVER="$fixture/bin/observer"
    POCKETDS_QDBUS="$fixture/bin/qdbus"
    POCKETDS_KSCREEN_DOCTOR="$fixture/bin/kscreen-doctor"
    POCKETDS_JQ="$(command -v jq)"
    POCKETDS_FLOCK="$fixture/bin/flock"
    POCKETDS_GAMESCOPE_KWIN_SCRIPT="$kwin_script"
    MOCK_GAMESCOPE_LOG="$fixture/gamescope.log"
    MOCK_QDBUS_LOG="$fixture/qdbus.log"
    MOCK_KSCREEN_LOG="$fixture/kscreen.log"
    MOCK_KSCREEN_STATE="$fixture/kscreen-mode"
    MOCK_OBSERVER_LOG="$fixture/observer.log"
    MOCK_APP_LOG="$fixture/app.log"
)

env "${common_env[@]}" "$runtime" -- "$fixture/bin/app" 'argument with space'
grep -Fqx -- '--backend' "$fixture/gamescope.log"
grep -Fqx -- 'wayland' "$fixture/gamescope.log"
grep -Fqx -- '-W' "$fixture/gamescope.log"
grep -Fqx -- '1920' "$fixture/gamescope.log"
grep -Fqx -- '-r' "$fixture/gamescope.log"
grep -Fqx -- '120' "$fixture/gamescope.log"
grep -Fqx -- '--force-windows-fullscreen' "$fixture/gamescope.log"
grep -Fqx -- 'argument with space' "$fixture/app.log"
grep -Fqx -- 'DISPLAY=:77' "$fixture/app.log"
grep -Fqx -- 'observer' "$fixture/observer.log"
grep -Fq 'org.kde.kwin.Scripting.loadScript' "$fixture/qdbus.log"
grep -Fq 'org.kde.kwin.Scripting.unloadScript' "$fixture/qdbus.log"
[[ $(sed -n '1p' "$fixture/kscreen.log") == output.DSI-1.mode.4 ]]
[[ $(sed -n '2p' "$fixture/kscreen.log") == output.DSI-1.mode.5 ]]
[[ $(wc -l <"$fixture/kscreen.log" | tr -d ' ') -eq 2 ]]
[[ $(sed -n '1p' "$fixture/kscreen-mode") == 5 ]]
[[ ! -e $fixture/run/pocketds-gamescope-limit ]]
[[ ! -e $fixture/run/pocketds-gamescope-fps ]]

printf '5\n' >"$fixture/kscreen-mode"
: >"$fixture/gamescope.log"
: >"$fixture/kscreen.log"
env "${common_env[@]}" STEAMDECK_MODE=false \
    "$runtime" --steam -- "$fixture/bin/app" steam-desktop
! grep -Fqx -- '-e' "$fixture/gamescope.log"

printf '5\n' >"$fixture/kscreen-mode"
: >"$fixture/gamescope.log"
: >"$fixture/kscreen.log"
env "${common_env[@]}" STEAMDECK_MODE=true \
    "$runtime" --steam -- "$fixture/bin/app" steam-gamepad
grep -Fqx -- '-e' "$fixture/gamescope.log"

: >"$fixture/qdbus.log"
: >"$fixture/gamescope.log"
: >"$fixture/kscreen.log"
env "${common_env[@]}" POCKETDS_DISABLE_GAMESCOPE=1 \
    "$runtime" -- "$fixture/bin/app" bypass
grep -Fqx bypass "$fixture/app.log"
[[ ! -s $fixture/qdbus.log && ! -s $fixture/gamescope.log && ! -s $fixture/kscreen.log ]]

printf '4\n' >"$fixture/kscreen-mode"
: >"$fixture/qdbus.log"
: >"$fixture/gamescope.log"
: >"$fixture/kscreen.log"
env "${common_env[@]}" "$runtime" -- "$fixture/bin/app" already-120
grep -Fqx -- '120' "$fixture/gamescope.log"
[[ ! -s $fixture/kscreen.log ]]
[[ $(sed -n '1p' "$fixture/kscreen-mode") == 4 ]]

printf '5\n' >"$fixture/kscreen-mode"
: >"$fixture/gamescope.log"
: >"$fixture/kscreen.log"
set +e
env "${common_env[@]}" MOCK_KSCREEN_NO_120=1 \
    "$runtime" -- "$fixture/bin/app" no-120 >/dev/null 2>&1
status=$?
set -e
[[ $status -eq 1 ]]
[[ ! -s $fixture/gamescope.log && ! -s $fixture/kscreen.log ]]
[[ $(sed -n '1p' "$fixture/kscreen-mode") == 5 ]]

: >"$fixture/gamescope.log"
: >"$fixture/kscreen.log"
set +e
env "${common_env[@]}" MOCK_KSCREEN_SWITCH_FAIL=1 \
    "$runtime" -- "$fixture/bin/app" failed-switch >/dev/null 2>&1
status=$?
set -e
[[ $status -eq 1 ]]
[[ ! -s $fixture/gamescope.log ]]
[[ $(sed -n '1p' "$fixture/kscreen.log") == output.DSI-1.mode.4 ]]
[[ $(sed -n '2p' "$fixture/kscreen.log") == output.DSI-1.mode.5 ]]
[[ $(sed -n '1p' "$fixture/kscreen-mode") == 5 ]]

: >"$fixture/gamescope.log"
: >"$fixture/kscreen.log"
set +e
env "${common_env[@]}" MOCK_KSCREEN_IGNORE_SWITCH=1 \
    "$runtime" -- "$fixture/bin/app" failed-verification >/dev/null 2>&1
status=$?
set -e
[[ $status -eq 1 ]]
[[ ! -s $fixture/gamescope.log ]]
[[ $(sed -n '1p' "$fixture/kscreen.log") == output.DSI-1.mode.4 ]]
[[ $(sed -n '2p' "$fixture/kscreen.log") == output.DSI-1.mode.5 ]]
[[ $(sed -n '1p' "$fixture/kscreen-mode") == 5 ]]

env HOME="$fixture" XDG_RUNTIME_DIR="$fixture/run" "$limiter" 30 >/dev/null
grep -Fqx 30 "$fixture/.config/pocketds-linux-kit/game-fps-limit"
[[ ! -e $fixture/run/pocketds-gamescope-limit ]]
[[ $(env HOME="$fixture" XDG_RUNTIME_DIR="$fixture/run" "$limiter" status) == 30 ]]
printf '0\n' >"$fixture/run/pocketds-gamescope-limit"
chmod 0600 "$fixture/run/pocketds-gamescope-limit"
env HOME="$fixture" XDG_RUNTIME_DIR="$fixture/run" "$limiter" 40 >/dev/null
grep -Fqx 40 "$fixture/.config/pocketds-linux-kit/game-fps-limit"
grep -Fqx 40 "$fixture/run/pocketds-gamescope-limit"
set +e
env HOME="$fixture" XDG_RUNTIME_DIR="$fixture/run" "$limiter" 55 >/dev/null 2>&1
status=$?
set -e
[[ $status -eq 64 ]]

echo '  [OK] unified Gamescope runtime preserves argv, 120 Hz mode restore, placement, and limiter controls'
