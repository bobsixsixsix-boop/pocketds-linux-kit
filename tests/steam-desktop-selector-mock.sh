#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
selector="$repo_root/components/steam/steamos-session-select"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/bin"

cat >"$fixture/bin/supervisor" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'arg=%s\n' "$@" >>"$MOCK_SUPERVISOR_LOG"
if [[ ${1:-} == probe ]]; then
    exit "${MOCK_PROBE_STATUS:-0}"
fi
[[ ${1:-} == stop ]]
MOCK

cat >"$fixture/bin/system-selector" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'arg=%s\n' "$@" >"$MOCK_SYSTEM_LOG"
MOCK

cat >"$fixture/bin/systemd-run" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'arg=%s\n' "$@" >"$MOCK_SYSTEMD_RUN_LOG"
MOCK

cat >"$fixture/bin/pgrep" <<'MOCK'
#!/usr/bin/env bash
exit "${MOCK_PGREP_STATUS:-1}"
MOCK

cat >"$fixture/bin/steam-desktop" <<'MOCK'
#!/usr/bin/env bash
printf 'mode=%s\n' "${STEAMDECK_MODE:-unset}" >"$MOCK_DESKTOP_LOG"
MOCK

chmod 0755 "$fixture/bin/"*
common_env=(
    MOCK_SUPERVISOR_LOG="$fixture/supervisor.log"
    MOCK_SYSTEM_LOG="$fixture/system.log"
    MOCK_SYSTEMD_RUN_LOG="$fixture/systemd-run.log"
    MOCK_DESKTOP_LOG="$fixture/desktop.log"
    POCKETDS_GAME_SESSION_SUPERVISOR="$fixture/bin/supervisor"
    POCKETDS_SYSTEM_SESSION_SELECTOR="$fixture/bin/system-selector"
    POCKETDS_SYSTEMD_RUN="$fixture/bin/systemd-run"
    POCKETDS_PGREP="$fixture/bin/pgrep"
    POCKETDS_STEAM_DESKTOP_LAUNCHER="$fixture/bin/steam-desktop"
)

env "${common_env[@]}" MOCK_PROBE_STATUS=0 "$selector" desktop
python3 - "$fixture" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
assert (root / "supervisor.log").read_text().splitlines() == [
    "arg=probe", "arg=--name", "arg=steam", "arg=stop"
]
assert not (root / "system.log").exists()
run_args = (root / "systemd-run.log").read_text().splitlines()
assert "arg=--unit=pocketds-steam-desktop" in run_args
assert "arg=--setenv=STEAMDECK_MODE=false" in run_args
assert run_args[-1] == "arg=--pocketds-desktop-restart"
PY

rm -f "$fixture/supervisor.log"
env "${common_env[@]}" MOCK_PROBE_STATUS=3 "$selector" desktop
python3 - "$fixture" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
assert (root / "supervisor.log").read_text().splitlines() == [
    "arg=probe", "arg=--name", "arg=steam"
]
assert (root / "system.log").read_text().splitlines() == ["arg=desktop"]
PY

rm -f "$fixture/supervisor.log" "$fixture/system.log"
env "${common_env[@]}" "$selector" gaming
[[ ! -e $fixture/supervisor.log ]]
[[ $(<"$fixture/system.log") == arg=gaming ]]

rm -f "$fixture/system.log"
set +e
env "${common_env[@]}" MOCK_PROBE_STATUS=75 "$selector" desktop >/dev/null 2>&1
status=$?
set -e
[[ $status -eq 75 && ! -e $fixture/system.log ]]

rm -f "$fixture/supervisor.log"
env "${common_env[@]}" MOCK_PROBE_STATUS=3 MOCK_PGREP_STATUS=1 \
    "$selector" --pocketds-desktop-restart
[[ $(<"$fixture/desktop.log") == mode=false ]]

echo '  [OK] managed Steam restarts as the KDE desktop client without logging out'
