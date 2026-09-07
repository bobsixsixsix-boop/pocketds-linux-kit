#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
wrapper="$repo_root/components/steam/pocketds-steam-game-fps"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
chmod 0700 "$fixture"
runtime="$fixture/runtime"
shared="$fixture/shared"
mkdir -m 0700 "$runtime" "$shared"

mangohud="$fixture/mangohud"
cat >"$mangohud" <<'MANGOHUD'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ ${MANGOHUD_CONFIG:-} == *alpha=0* ]]
exec env MANGOHUD=1 "$@"
MANGOHUD
chmod 0700 "$mangohud"

mock="$fixture/mock-game"
cat >"$mock" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ ${MANGOHUD:-} == 1 ]]
[[ ${MANGOHUD_LOG_LEVEL:-} == off ]]
[[ ${MANGOHUD_CONFIG:-} != *no_display* ]]
[[ ${MANGOHUD_CONFIG:-} == *autostart_log=1* ]]
[[ ${MANGOHUD_CONFIG:-} == *log_interval=1000* ]]
[[ ${MANGOHUD_CONFIG:-} == *permit_upload=0* ]]
output=
IFS=',' read -ra fields <<<"$MANGOHUD_CONFIG"
for field in "${fields[@]}"; do
    case "$field" in
        output_folder=*) output=${field#output_folder=} ;;
    esac
done
[[ -n $output && -d $output && ! -L $output ]]
[[ $(dirname "$output") == "${POCKETDS_STEAM_FPS_ROOT:?}" ]]
[[ $(stat -c '%a' "$output") == 700 ]]
record=${POCKETDS_RUNTIME_DIR:?}/pocketds-steam-fps.active
[[ -f $record && ! -L $record ]]
[[ $(<"$record") == "$output" ]]
printf 'fps,frametime\n60,16.7\n' >"$output/VampireSurvivors_test.csv"
[[ ${1:-} == --screen-width && ${2:-} == 1920 ]]
exit "${MOCK_EXIT_STATUS:-0}"
MOCK
chmod 0700 "$mock"

POCKETDS_RUNTIME_DIR="$runtime" \
POCKETDS_STEAM_FPS_ROOT="$shared" \
POCKETDS_MANGOHUD="$mangohud" \
"$wrapper" "$mock" --screen-width 1920
[[ ! -e $runtime/pocketds-steam-fps.active ]]
if find "$shared" -maxdepth 1 -type d -name 'session.*' | grep -q .; then
    echo 'Steam FPS session directory leaked after normal exit' >&2
    exit 1
fi

set +e
POCKETDS_RUNTIME_DIR="$runtime" \
POCKETDS_STEAM_FPS_ROOT="$shared" \
POCKETDS_MANGOHUD="$mangohud" \
MOCK_EXIT_STATUS=23 \
"$wrapper" "$mock" --screen-width 1920
status=$?
set -e
[[ $status == 23 ]]
[[ ! -e $runtime/pocketds-steam-fps.active ]]

fallback="$fixture/fallback"
cat >"$fallback" <<'FALLBACK'
#!/usr/bin/env bash
[[ -z ${MANGOHUD:-} ]]
[[ ${1:-} == direct ]]
FALLBACK
chmod 0700 "$fallback"
POCKETDS_RUNTIME_DIR="$runtime" \
POCKETDS_STEAM_FPS_ROOT="$shared" \
POCKETDS_MANGOHUD="$fixture/missing" \
"$wrapper" "$fallback" direct

mkdir "$runtime/pocketds-steam-fps.active"
POCKETDS_RUNTIME_DIR="$runtime" \
POCKETDS_STEAM_FPS_ROOT="$shared" \
POCKETDS_MANGOHUD="$mangohud" \
"$wrapper" "$fallback" direct
[[ -d $runtime/pocketds-steam-fps.active ]]
if find "$shared" -maxdepth 1 -type d -name 'session.*' | grep -q .; then
    echo 'Steam FPS session directory leaked after marker failure' >&2
    exit 1
fi
rmdir "$runtime/pocketds-steam-fps.active"

blocked="$fixture/blocked"
ln -s "$shared" "$blocked"
POCKETDS_RUNTIME_DIR="$runtime" \
POCKETDS_STEAM_FPS_ROOT="$blocked" \
POCKETDS_MANGOHUD="$mangohud" \
"$wrapper" "$fallback" direct
[[ ! -e $runtime/pocketds-steam-fps.active ]]

blocked_file="$fixture/blocked-file"
printf 'occupied\n' >"$blocked_file"
POCKETDS_RUNTIME_DIR="$runtime" \
POCKETDS_STEAM_FPS_ROOT="$blocked_file" \
POCKETDS_MANGOHUD="$mangohud" \
"$wrapper" "$fallback" direct
[[ -f $blocked_file && ! -e $runtime/pocketds-steam-fps.active ]]

echo '  [OK] Steam game FPS wrapper is private, reversible, and non-blocking'
