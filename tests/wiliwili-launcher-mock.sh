#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
launcher="$repo_root/components/wiliwili/pocketds-wiliwili"
validator="$repo_root/scripts/pocketds-wiliwili-gamecontrollerdb.py"
source_db="$repo_root/components/wiliwili/gamecontrollerdb.txt"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT

mkdir -p "$fixture/home" "$fixture/bin"
cp "$source_db" "$fixture/source.txt"
cp "$source_db" "$fixture/live.txt"

cat >"$fixture/bin/supervisor" <<'MOCK'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$@" >"$MOCK_SUPERVISOR_LOG"
exit "${MOCK_SUPERVISOR_STATUS:-0}"
MOCK
chmod 0755 "$fixture/bin/supervisor"

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

run_launcher() {
    : >"$fixture/supervisor.log"
    HOME="$fixture/home" \
    MOCK_SUPERVISOR_LOG="$fixture/supervisor.log" \
    POCKETDS_GAME_SESSION_SUPERVISOR="$fixture/bin/supervisor" \
    POCKETDS_WILIWILI_MAPPING_VALIDATOR="$validator" \
    POCKETDS_WILIWILI_MAPPING_SOURCE="$fixture/source.txt" \
    POCKETDS_WILIWILI_MAPPING_LIVE="$fixture/live.txt" \
        "$launcher" "$@"
}

run_launcher --debug 'value with space'
assert_argv "$fixture/supervisor.log" \
    run --backend flatpak --name wiliwili --app-id \
    cn.xfangfang.wiliwili -- --debug 'value with space'

cp "$source_db" "$fixture/live.txt"
sed -i.bak 's/a:b0/a:b9/' "$fixture/live.txt"
if run_launcher >/dev/null 2>&1; then
    echo 'launcher accepted a stale Wiliwili controller database' >&2
    exit 1
fi
[[ ! -s $fixture/supervisor.log ]]

set +e
HOME="$fixture/home" \
POCKETDS_GAME_SESSION_SUPERVISOR="$fixture/bin/missing" \
POCKETDS_WILIWILI_MAPPING_VALIDATOR="$validator" \
POCKETDS_WILIWILI_MAPPING_SOURCE="$fixture/source.txt" \
POCKETDS_WILIWILI_MAPPING_LIVE="$fixture/source.txt" \
    "$launcher" >/dev/null 2>&1
status=$?
set -e
[[ $status -ne 0 ]]

echo '  [OK] Wiliwili launcher performs mapping preflight then delegates exactly once'
