#!/usr/bin/env bash
# Compatibility entrypoint: the GLFW database cannot drift from its launcher.
set -Eeuo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    echo "usage: $0"
    echo 'Installs the complete transactional game/input stack.'
    exit 0
fi
if (($#)); then
    echo 'Partial Wiliwili mapping installation is unsupported; no files were changed.' >&2
    exit 2
fi
exec python3 "$repo_root/scripts/install-game-input-stack.py"
