#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
launcher="$repo_root/components/steam/launch-steam.sh"
respawn="$repo_root/components/system/20-pocketds-plasmashell-respawn.conf"
installer="$repo_root/scripts/install.sh"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT

steam_root="$fixture/home/.local/share/Steam"
runtime="$steam_root/steamrtarm64/pv-runtime/steam-runtime-steamrt-arm64/steamrt3c_platform_mock/files"
mkdir -p "$fixture/bin" "$runtime/lib/aarch64-linux-gnu" "$runtime/lib"
: >"$steam_root/.switchdeck-initial-launch"

cat >"$steam_root/steamrtarm64/steam" <<'MOCK'
#!/usr/bin/env bash
sleep 0.3
exit 0
MOCK
cat >"$fixture/bin/pgrep" <<'MOCK'
#!/usr/bin/env bash
exit 1
MOCK
chmod 0755 "$steam_root/steamrtarm64/steam" "$fixture/bin/pgrep"

python3 - "$launcher" "$fixture" <<'PY'
import os
from pathlib import Path
import subprocess
import sys

launcher = sys.argv[1]
fixture = Path(sys.argv[2])
environment = os.environ | {
    "HOME": str(fixture / "home"),
    "USER": "pocketds",
    "PATH": f"{fixture / 'bin'}:/usr/bin:/bin",
}
subprocess.run(["/bin/bash", launcher], env=environment, check=True, timeout=3)
PY

source=$(<"$launcher")
[[ $source == *'trap cleanup_switchdeck_watcher EXIT'* ]]
[[ $source == *'switchdeck_watcher_pid=$!'* ]]
[[ $source == *'plasma_stop_marker='* ]]
[[ $source == *'restore_switchdeck_plasma'* ]]
[[ $source == *'cleanup_switchdeck_watcher()'*'restore_switchdeck_plasma'* ]]
[[ $(grep -c '^Restart=always$' "$respawn") -eq 1 ]]
[[ $(grep -c '^RestartSec=1s$' "$respawn") -eq 1 ]]
grep -Fq 'plasma-plasmashell.service.d/20-pocketds-respawn.conf' "$installer"

echo '  [OK] Switchdeck watcher and Plasma respawn policy cannot strand a grey desktop'
