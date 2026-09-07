#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/bin"

cat >"$fixture/bin/python3" <<'MOCK'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$@" >"$MOCK_PYTHON_LOG"
MOCK
chmod 0755 "$fixture/bin/python3"

for script in install-input.sh install-emulation.sh install-wiliwili-input.sh \
    install-wiliwili-launcher.sh; do
    : >"$fixture/python.log"
    PATH="$fixture/bin:$PATH" MOCK_PYTHON_LOG="$fixture/python.log" \
        "$repo_root/scripts/$script"
    [[ $(wc -l <"$fixture/python.log" | tr -d '[:space:]') -eq 1 ]]
    [[ $(cat "$fixture/python.log") == \
        "$repo_root/scripts/install-game-input-stack.py" ]]
done

: >"$fixture/python.log"
set +e
PATH="$fixture/bin:$PATH" MOCK_PYTHON_LOG="$fixture/python.log" \
    "$repo_root/scripts/install-input.sh" --activate-joymouse >/dev/null 2>&1
status=$?
set -e
[[ $status -eq 2 && ! -s $fixture/python.log ]]

echo '  [OK] legacy installers delegate once or refuse obsolete partial options'
