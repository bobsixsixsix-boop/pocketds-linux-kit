#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
launcher="$repo_root/components/emulation/pocketds-es-de"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/home/Applications" "$fixture/home/.local/share/pocketds-linux-kit/emulation" "$fixture/bin"
: >"$fixture/home/.local/share/pocketds-linux-kit/emulation/es_systems.xml"
: >"$fixture/home/.local/share/pocketds-linux-kit/emulation/es_systems-shared.xml"

cat >"$fixture/bin/supervisor" <<'MOCK'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$@" >"$MOCK_SUPERVISOR_LOG"
printf 'POCKETDS_HOST_WAYLAND_DISPLAY=%s\n' \
    "${POCKETDS_HOST_WAYLAND_DISPLAY-UNSET}" >"$MOCK_SUPERVISOR_ENV_LOG"
MOCK
cat >"$fixture/bin/runtime" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
cat >"$fixture/bin/prepare" <<'MOCK'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$@" >"$MOCK_PREPARE_LOG"
[[ ${MOCK_PREPARE_FAIL:-0} != 1 ]]
MOCK
cat >"$fixture/home/Applications/ES-DE_aarch64.AppImage" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
cat >"$fixture/bin/inhibit" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
cat >"$fixture/bin/mountpoint" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
chmod 0755 "$fixture/bin/supervisor" "$fixture/bin/runtime" "$fixture/bin/prepare" \
    "$fixture/bin/inhibit" "$fixture/bin/mountpoint" \
    "$fixture/home/Applications/ES-DE_aarch64.AppImage"

assert_argv() {
    local log=$1
    shift
    python3 - "$log" "$@" <<'PY'
from pathlib import Path
import sys

actual = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
expected = sys.argv[2:]
assert actual == expected, (actual, expected)
PY
}

common_env=(
    HOME="$fixture/home"
    MOCK_SUPERVISOR_LOG="$fixture/supervisor.log"
    MOCK_SUPERVISOR_ENV_LOG="$fixture/supervisor.env"
    MOCK_PREPARE_LOG="$fixture/prepare.log"
    POCKETDS_GAME_SESSION_SUPERVISOR="$fixture/bin/supervisor"
    POCKETDS_GAME_RUNTIME="$fixture/bin/runtime"
    POCKETDS_ESDE_PREPARE="$fixture/bin/prepare"
    PATH="$fixture/bin:$PATH"
)

env "${common_env[@]}" POCKETDS_SKIP_INHIBIT=1 POCKETDS_ESDE_DEBUG=1 \
    WAYLAND_DISPLAY=wayland-host \
    "$launcher" 'value with space'
grep -Fqx -- --require-rom-directory "$fixture/prepare.log"
grep -Fqx -- "$fixture/home/ROMs" "$fixture/prepare.log"
assert_argv "$fixture/supervisor.log" \
    run --backend native --name es-de -- \
    "$fixture/bin/runtime" -- \
    "$fixture/home/Applications/ES-DE_aarch64.AppImage" --debug 'value with space'
grep -Fqx 'POCKETDS_HOST_WAYLAND_DISPLAY=wayland-host' \
    "$fixture/supervisor.env"

shared_root="$fixture/mnt/pocketds-games"
mkdir -p "$fixture/home/.config/pocketds-linux-kit" \
    "$shared_root/Roms" "$shared_root/PocketDS/Manifests" \
    "$shared_root/PocketDS/Frontends/ES-DE"
printf '%s\n' "$shared_root" >"$fixture/home/.config/pocketds-linux-kit/shared-library-root"
: >"$shared_root/PocketDS/Manifests/shared-library.json"
env "${common_env[@]}" POCKETDS_SKIP_INHIBIT=1 \
    POCKETDS_SHARED_LIBRARY_MOUNT="$shared_root" "$launcher"
assert_argv "$fixture/prepare.log" \
    --esde-home "$shared_root/PocketDS/Frontends" \
    --managed-systems "$fixture/home/.local/share/pocketds-linux-kit/emulation/es_systems-shared.xml" \
    --previous-managed-systems "$fixture/home/.local/share/pocketds-linux-kit/emulation/es_systems.xml" \
    --force-managed-systems \
    --set-rom-directory "$shared_root/Roms" \
    --require-rom-directory "$shared_root/Roms" \
    --enable-collection 精选集 --parse-gamelist-only
assert_argv "$fixture/supervisor.log" \
    run --backend native --name es-de -- \
    "$fixture/bin/runtime" -- \
    "$fixture/home/Applications/ES-DE_aarch64.AppImage" \
    --home "$shared_root/PocketDS/Frontends"
rm -f "$fixture/home/.config/pocketds-linux-kit/shared-library-root"

env "${common_env[@]}" POCKETDS_SYSTEMD_INHIBIT="$fixture/bin/inhibit" \
    "$launcher"
assert_argv "$fixture/supervisor.log" \
    run --backend native --name es-de -- \
    "$fixture/bin/inhibit" --what=idle:sleep \
    '--who=Pocket DS ES-DE' '--why=Game frontend is active' --mode=block \
    "$fixture/bin/runtime" -- \
    "$fixture/home/Applications/ES-DE_aarch64.AppImage"

: >"$fixture/supervisor.log"
set +e
env "${common_env[@]}" MOCK_PREPARE_FAIL=1 POCKETDS_SKIP_INHIBIT=1 \
    "$launcher" >/dev/null 2>&1
status=$?
set -e
[[ $status -ne 0 && ! -s $fixture/supervisor.log ]]

echo '  [OK] ES-DE owns one Gamescope session and preserves the host Wayland socket'
