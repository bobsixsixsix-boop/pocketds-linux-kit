#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
launcher="$repo_root/components/steam/pocketds-steam-session"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p \
    "$fixture/bin" \
    "$fixture/home/.local/bin" \
    "$fixture/home/.local/share/Steam/steamrtarm64" \
    "$fixture/home/.local/share/Steam/steamrtarm64/pv-runtime/steam-runtime-steamrt-arm64/steamrt3c_platform_mock/files/lib/aarch64-linux-gnu" \
    "$fixture/home/.local/share/Steam/steamrtarm64/pv-runtime/steam-runtime-steamrt-arm64/steamrt3c_platform_mock/files/lib"

cat >"$fixture/bin/supervisor" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ ${1:-} == probe ]]; then
    printf 'arg=%s\n' "$@" >"$MOCK_PROBE_LOG"
    exit "${MOCK_PROBE_STATUS:-3}"
fi
printf 'mode=%s\n' "${STEAMDECK_MODE:-}" >"$MOCK_SUPERVISOR_LOG"
printf 'arg=%s\n' "$@" >>"$MOCK_SUPERVISOR_LOG"
if [[ ${MOCK_SUPERVISOR_BUSY:-0} == 1 ]]; then
    exit 75
fi
if [[ ${1:-} == stop ]]; then
    exit 0
fi
while [[ $# -gt 0 && $1 != -- ]]; do
    shift
done
[[ $# -gt 1 ]]
shift
exec "$@"
MOCK

cat >"$fixture/bin/game-runtime" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'arg=%s\n' "$@" >"$MOCK_RUNTIME_LOG"
[[ ${1:-} == --steam && ${2:-} == -- ]]
shift 2
exec "$@"
MOCK

cat >"$fixture/home/.local/bin/steam" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'mode=%s\n' "${STEAMDECK_MODE:-}" >"$MOCK_STEAM_LOG"
printf 'arg=%s\n' "$@" >>"$MOCK_STEAM_LOG"
MOCK

cat >"$fixture/home/.local/share/Steam/steamrtarm64/steam" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'ld=%s\n' "${LD_LIBRARY_PATH:-}" >"$MOCK_CLIENT_LOG"
printf 'arg=%s\n' "$@" >>"$MOCK_CLIENT_LOG"
MOCK

cat >"$fixture/bin/pgrep-inactive" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'arg=%s\n' "$@" >"$MOCK_PGREP_LOG"
exit 1
MOCK

cat >"$fixture/bin/pgrep-active" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'arg=%s\n' "$@" >"$MOCK_PGREP_LOG"
exit 0
MOCK

cat >"$fixture/bin/systemd-run" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'arg=%s\n' "$@" >"$MOCK_SYSTEMD_RUN_LOG"
MOCK

cat >"$fixture/bin/systemctl" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'arg=%s\n' "$@" >>"$MOCK_SYSTEMCTL_LOG"
if [[ " $* " == *" is-active "* ]]; then
    exit "${MOCK_DESKTOP_UNIT_STATUS:-0}"
fi
MOCK

chmod 0755 \
    "$fixture/bin/supervisor" \
    "$fixture/bin/game-runtime" \
    "$fixture/bin/pgrep-inactive" \
    "$fixture/bin/pgrep-active" \
    "$fixture/bin/systemd-run" \
    "$fixture/bin/systemctl" \
    "$fixture/home/.local/bin/steam" \
    "$fixture/home/.local/share/Steam/steamrtarm64/steam"

common_env=(
    HOME="$fixture/home"
    MOCK_SUPERVISOR_LOG="$fixture/supervisor.log"
    MOCK_RUNTIME_LOG="$fixture/runtime.log"
    MOCK_STEAM_LOG="$fixture/steam.log"
    MOCK_CLIENT_LOG="$fixture/client.log"
    MOCK_PGREP_LOG="$fixture/pgrep.log"
    MOCK_PROBE_LOG="$fixture/probe.log"
    MOCK_SYSTEMD_RUN_LOG="$fixture/systemd-run.log"
    MOCK_SYSTEMCTL_LOG="$fixture/systemctl.log"
    POCKETDS_GAME_SESSION_SUPERVISOR="$fixture/bin/supervisor"
    POCKETDS_GAME_RUNTIME="$fixture/bin/game-runtime"
    POCKETDS_STEAM_LAUNCHER="$fixture/home/.local/bin/steam"
    POCKETDS_STEAM_ROOT="$fixture/home/.local/share/Steam"
    POCKETDS_STEAM_CLIENT="$fixture/home/.local/share/Steam/steamrtarm64/steam"
    POCKETDS_SYSTEMD_RUN="$fixture/bin/systemd-run"
    POCKETDS_SYSTEMCTL="$fixture/bin/systemctl"
    POCKETDS_STEAM_UID=1000
)

env "${common_env[@]}" \
    POCKETDS_PGREP="$fixture/bin/pgrep-inactive" \
    STEAMDECK_MODE=false \
    "$launcher" 'steam://rungameid/123'

python3 - "$fixture" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
assert (root / "supervisor.log").read_text().splitlines() == [
    "mode=true",
    "arg=run",
    "arg=--backend",
    "arg=native",
    "arg=--name",
    "arg=steam",
    "arg=--",
    f"arg={root}/bin/game-runtime",
    "arg=--steam",
    "arg=--",
    f"arg={root}/home/.local/bin/steam",
    "arg=steam://rungameid/123",
]
assert (root / "runtime.log").read_text().splitlines() == [
    "arg=--steam",
    "arg=--",
    f"arg={root}/home/.local/bin/steam",
    "arg=steam://rungameid/123",
]
assert (root / "steam.log").read_text().splitlines() == [
    "mode=true",
    "arg=steam://rungameid/123",
]
assert not (root / "client.log").exists()
assert (root / "probe.log").read_text().splitlines() == [
    "arg=probe", "arg=--name", "arg=steam"
]
assert (root / "pgrep.log").read_text().splitlines() == [
    "arg=-u", "arg=1000", "arg=-x", "arg=steam"
]
PY

rm -f "$fixture/supervisor.log" "$fixture/runtime.log" \
    "$fixture/steam.log" "$fixture/client.log" "$fixture/pgrep.log" \
    "$fixture/probe.log"
env "${common_env[@]}" \
    POCKETDS_PGREP="$fixture/bin/pgrep-active" \
    MOCK_PROBE_STATUS=0 \
    LD_LIBRARY_PATH=original-library \
    STEAMDECK_MODE=true \
    "$launcher" 'steam://open/games'

python3 - "$fixture" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
assert not (root / "supervisor.log").exists()
assert not (root / "runtime.log").exists()
assert not (root / "steam.log").exists()
client = (root / "client.log").read_text().splitlines()
assert client[1:] == ["arg=-ifrunning", "arg=steam://open/games"]
assert f"{root}/home/.local/share/Steam/steamrtarm64" in client[0]
assert "steamrt3c_platform_mock/files/lib/aarch64-linux-gnu" in client[0]
assert client[0].endswith(":original-library")
assert (root / "probe.log").read_text().splitlines() == [
    "arg=probe", "arg=--name", "arg=steam"
]
assert (root / "pgrep.log").read_text().splitlines() == [
    "arg=-u", "arg=1000", "arg=-x", "arg=steam"
]
PY

rm -f "$fixture/supervisor.log" "$fixture/runtime.log" \
    "$fixture/steam.log" "$fixture/client.log" "$fixture/pgrep.log" \
    "$fixture/probe.log" "$fixture/systemd-run.log"
env "${common_env[@]}" \
    POCKETDS_PGREP="$fixture/bin/pgrep-active" \
    MOCK_PROBE_STATUS=0 \
    STEAMDECK_MODE=true \
    "$launcher" -gamepadui

python3 - "$fixture" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
assert not (root / "runtime.log").exists()
assert not (root / "steam.log").exists()
assert not (root / "client.log").exists()
assert (root / "supervisor.log").read_text().splitlines() == ["mode=true", "arg=stop"]
run_args = (root / "systemd-run.log").read_text().splitlines()
assert "arg=--unit=pocketds-steam-gamepad" in run_args
assert "arg=--property=SuccessExitStatus=143" in run_args
assert "arg=--setenv=STEAMDECK_MODE=true" in run_args
assert run_args[-1] == "arg=--pocketds-gamepad-restart"
PY

rm -f "$fixture/supervisor.log" "$fixture/runtime.log" \
    "$fixture/steam.log" "$fixture/client.log" "$fixture/pgrep.log" \
    "$fixture/probe.log" "$fixture/systemd-run.log"
env "${common_env[@]}" \
    POCKETDS_PGREP="$fixture/bin/pgrep-inactive" \
    MOCK_PROBE_STATUS=3 \
    "$launcher" --pocketds-gamepad-restart

python3 - "$fixture" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
assert (root / "supervisor.log").read_text().splitlines()[0] == "mode=true"
assert (root / "steam.log").read_text().splitlines() == [
    "mode=true", "arg=-gamepadui"
]
assert not (root / "systemd-run.log").exists()
PY

rm -f "$fixture/supervisor.log" "$fixture/runtime.log" \
    "$fixture/steam.log" "$fixture/client.log" "$fixture/pgrep.log" \
    "$fixture/probe.log" "$fixture/systemd-run.log" "$fixture/systemctl.log"
env "${common_env[@]}" \
    POCKETDS_PGREP="$fixture/bin/pgrep-active" \
    MOCK_PROBE_STATUS=3 \
    STEAMDECK_MODE=true \
    "$launcher" -gamepadui

python3 - "$fixture" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
assert not (root / "supervisor.log").exists()
assert not (root / "runtime.log").exists()
assert not (root / "steam.log").exists()
assert not (root / "client.log").exists()
run_args = (root / "systemd-run.log").read_text().splitlines()
assert "arg=--unit=pocketds-steam-gamepad" in run_args
assert "arg=--property=SuccessExitStatus=143" in run_args
systemctl_args = (root / "systemctl.log").read_text().splitlines()
assert systemctl_args == [
    "arg=--user", "arg=is-active", "arg=--quiet",
    "arg=pocketds-steam-desktop.service", "arg=--user", "arg=stop",
    "arg=pocketds-steam-desktop.service",
]
PY

rm -f "$fixture/supervisor.log" "$fixture/runtime.log" \
    "$fixture/steam.log" "$fixture/client.log" "$fixture/pgrep.log" \
    "$fixture/probe.log"
set +e
env "${common_env[@]}" \
    POCKETDS_PGREP="$fixture/bin/pgrep-active" \
    MOCK_PROBE_STATUS=3 \
    "$launcher" 'steam://rungameid/456' >/dev/null 2>&1
status=$?
set -e
[[ $status -eq 75 ]]
[[ ! -e $fixture/supervisor.log && ! -e $fixture/runtime.log \
    && ! -e $fixture/steam.log && ! -e $fixture/client.log ]]

rm -f "$fixture/pgrep.log" "$fixture/probe.log"
set +e
env "${common_env[@]}" \
    POCKETDS_PGREP="$fixture/bin/pgrep-inactive" \
    MOCK_PROBE_STATUS=75 \
    "$launcher" 'steam://rungameid/789' >/dev/null 2>&1
status=$?
set -e
[[ $status -eq 75 && ! -e $fixture/pgrep.log ]]

echo '  [OK] Steam first launch, IPC and desktop-to-gamepad restart are managed'
