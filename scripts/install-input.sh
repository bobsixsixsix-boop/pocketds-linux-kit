#!/usr/bin/env bash
# Compatibility entrypoint: partial input installs are intentionally retired.
set -Eeuo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    echo "usage: $0"
    echo 'Installs the complete transactional game/input stack.'
    exit 0
fi
if (($#)); then
    echo 'Partial input activation is retired; no files were changed.' >&2
    echo "Run without arguments to install the complete game/input stack." >&2
    exit 2
fi
exec python3 "$repo_root/scripts/install-game-input-stack.py"
