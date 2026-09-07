#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
launcher="$repo_root/components/emulation/pocketds-melonds"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/home/config" "$fixture/home/share" "$fixture/bin" \
    "$fixture/mnt/pocketds-games"
: >"$fixture/home/share/arrange.js"

cat >"$fixture/bin/supervisor" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$@" >"$MOCK_SUPERVISOR_LOG"
MOCK
cat >"$fixture/bin/flatpak" <<'MOCK'
#!/usr/bin/env bash
if [[ $1 == info ]]; then exit 0; fi
if [[ $1 == kill ]]; then exit 0; fi
exit 2
MOCK
cat >"$fixture/bin/qdbus" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$@" >>"$MOCK_QDBUS_LOG"
if [[ " $* " == *".loadScript "* ]]; then printf '7\n'; fi
MOCK
cat >"$fixture/bin/prepare" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$@" >"$MOCK_PREPARE_LOG"
MOCK
cat >"$fixture/bin/fullscreen" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
chmod 0755 "$fixture/bin/supervisor" "$fixture/bin/flatpak" \
    "$fixture/bin/qdbus" "$fixture/bin/prepare" "$fixture/bin/fullscreen"

common_env=(
    HOME="$fixture/home"
    MOCK_SUPERVISOR_LOG="$fixture/supervisor.log"
    MOCK_QDBUS_LOG="$fixture/qdbus.log"
    MOCK_PREPARE_LOG="$fixture/prepare.log"
    POCKETDS_GAME_SESSION_SUPERVISOR="$fixture/bin/supervisor"
    POCKETDS_FLATPAK="$fixture/bin/flatpak"
    POCKETDS_QDBUS="$fixture/bin/qdbus"
    POCKETDS_MELONDS_PREPARE="$fixture/bin/prepare"
    POCKETDS_MELONDS_CONFIG="$fixture/home/config/melonDS.toml"
    POCKETDS_MELONDS_SAVE_DIR="$fixture/home/Saves/melonDS"
    POCKETDS_MELONDS_STATE_DIR="$fixture/home/States/melonDS"
    POCKETDS_MELONDS_FULLSCREEN="$fixture/bin/fullscreen"
    POCKETDS_MELONDS_KWIN_SCRIPT="$fixture/home/share/arrange.js"
    POCKETDS_SHARED_LIBRARY_MOUNT="$fixture/mnt/pocketds-games"
    POCKETDS_SKIP_INHIBIT=1
    WAYLAND_DISPLAY=/nonexistent/pocketds-gamescope-wayland
)

token=11111111-1111-4111-8111-111111111111
env "${common_env[@]}" POCKETDS_GAME_SESSION_TOKEN="$token" \
    POCKETDS_HOST_WAYLAND_DISPLAY=wayland-host \
    "$launcher" 'Mario game.nds'

python3 - "$fixture" "$token" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
token = sys.argv[2]
actual = (root / "supervisor.log").read_text().splitlines()
expected = [
    "join", "--token", token, "--", "/usr/bin/env",
    "WAYLAND_DISPLAY=wayland-host", "XDG_SESSION_TYPE=wayland",
    "QT_QPA_PLATFORM=wayland",
    str(root / "bin/flatpak"), "run", "--user",
    f"--filesystem={root / 'mnt/pocketds-games'}:ro",
    "net.kuribo64.melonDS",
    "Mario game.nds",
]
assert actual == expected, (actual, expected)
assert (root / "prepare.log").read_text().splitlines() == [
    "--config", str(root / "home/config/melonDS.toml"),
    "--save-dir", str(root / "home/Saves/melonDS"),
    "--state-dir", str(root / "home/States/melonDS"),
]
assert not (root / "home/config/melonDS.toml").exists()
qdbus = (root / "qdbus.log").read_text()
assert "loadScript" in qdbus and "/Scripting/Script7" in qdbus
assert "unloadScript" in qdbus
PY

: >"$fixture/supervisor.log"
set +e
env "${common_env[@]}" "$launcher" sample.nds >/dev/null 2>&1
status=$?
set -e
[[ $status -ne 0 && ! -s $fixture/supervisor.log ]]

echo '  [OK] melonDS escapes to the saved host Wayland socket or fails closed'
