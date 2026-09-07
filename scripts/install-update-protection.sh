#!/usr/bin/env bash
# Install only the DNF5 hardware/graphics/boot-stack exclusion. This does not refresh
# metadata, resolve a transaction, download packages or update the system.
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
source_file="$repo_root/components/system/90-pocketds-hardware-protection.conf"
target=${POCKETDS_DNF_PROTECTION_TARGET:-/etc/dnf/libdnf5.conf.d/90-pocketds-hardware-protection.conf}

if (($#)); then
    echo "usage: $0" >&2
    exit 2
fi
if [[ $EUID -eq 0 ]]; then
    echo 'Run this script as the desktop user, not root.' >&2
    exit 1
fi
for command in cmp dnf5 grep install python3 sudo; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
sudo -n true
python3 "$repo_root/tests/update-policy.py"
canonical_excludes=$(
    python3 - "$source_file" <<'PY'
from configparser import ConfigParser
from pathlib import Path
import sys

parser = ConfigParser(interpolation=None, strict=True)
parser.read_string(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(parser["main"]["excludepkgs"])
PY
)
expected_effective="excludepkgs = $canonical_excludes"

stamp=$(date +%Y%m%d-%H%M%S)
backup=${POCKETDS_UPDATE_BACKUP_PATH:-/var/lib/pocketds-linux-kit/backups/$stamp-update-protection}
had_target=0
changed=0
if sudo -n test -e "$target"; then
    had_target=1
fi
if ((had_target)) && ! sudo -n cmp -s "$source_file" "$target"; then
    sudo -n mkdir -p "$backup/etc/dnf/libdnf5.conf.d"
    sudo -n cp -a "$target" "$backup/etc/dnf/libdnf5.conf.d/"
fi

validate_effective() {
    local effective
    effective=$(dnf5 --dump-main-config) || return
    grep -Fxq -- "$expected_effective" <<<"$effective"
}

install_and_validate() {
    sudo -n install -D -m 0644 "$source_file" "$target" || return
    sudo -n cmp -s "$source_file" "$target" || return
    validate_effective
}

rollback() {
    if ((had_target)); then
        sudo -n cp -a \
            "$backup/etc/dnf/libdnf5.conf.d/90-pocketds-hardware-protection.conf" \
            "$target" || return
    else
        sudo -n rm -f "$target" || return
    fi
}

if ((had_target)) && sudo -n cmp -s "$source_file" "$target"; then
    if ! validate_effective; then
        echo 'Existing DNF5 protection is exact, but DNF5 did not load it.' >&2
        exit 1
    fi
else
    changed=1
    if ! install_and_validate; then
        echo 'DNF5 protection validation failed; restoring the pre-install state.' >&2
        if rollback; then
            echo 'DNF5 protection rollback completed.' >&2
        else
            echo 'CRITICAL: DNF5 protection rollback failed.' >&2
        fi
        exit 1
    fi
fi

if ((changed == 0)); then
    echo '[install-update-protection] exact file was already installed'
fi

echo '[install-update-protection] exact DNF5 hardware/graphics/boot exclusion is active'
echo "[install-update-protection] root backup: $backup"
