#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
installer="$repo_root/scripts/install-update-protection.sh"
source_file="$repo_root/components/system/90-pocketds-hardware-protection.conf"
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
mock_bin="$fixture/bin"
mkdir -p "$mock_bin"
ln -s "$repo_root/tests/fixtures/update-protection-command" "$mock_bin/sudo"
ln -s "$repo_root/tests/fixtures/update-protection-command" "$mock_bin/dnf5"
ln -s "$repo_root/tests/fixtures/update-protection-command" "$mock_bin/install"

run_installer() {
    local result=$1 target=$2 backup=$3
    PATH="$mock_bin:$PATH" \
    POCKETDS_MOCK_DNF_RESULT="$result" \
    POCKETDS_DNF_PROTECTION_TARGET="$target" \
    POCKETDS_UPDATE_BACKUP_PATH="$backup" \
        "$installer"
}

target="$fixture/success/etc/dnf/90-pocketds-hardware-protection.conf"
mkdir -p "$(dirname "$target")"
run_installer good "$target" "$fixture/success/backup" >/dev/null
cmp -s "$source_file" "$target"

target="$fixture/restore/etc/dnf/90-pocketds-hardware-protection.conf"
backup="$fixture/restore/backup"
mkdir -p "$(dirname "$target")"
printf '%s\n' 'old protected config' > "$target"
restore_log="$fixture/restore/installer.log"
if run_installer bad "$target" "$backup" >"$restore_log" 2>&1; then
    echo '  [FAIL] invalid effective configuration was accepted' >&2
    exit 1
fi
if ! grep -Fxq 'old protected config' "$target"; then
    echo "  [FAIL] previous file was not restored: $(tr '\n' ' ' < "$target")" >&2
    sed 's/^/    /' "$restore_log" >&2
    exit 1
fi
grep -Fxq 'old protected config' \
    "$backup/etc/dnf/libdnf5.conf.d/90-pocketds-hardware-protection.conf"

for partial in partial partial-boot; do
target="$fixture/$partial/etc/dnf/90-pocketds-hardware-protection.conf"
backup="$fixture/$partial/backup"
mkdir -p "$(dirname "$target")"
printf '%s\n' 'old protected config' > "$target"
partial_log="$fixture/$partial/installer.log"
if run_installer "$partial" "$target" "$backup" >"$partial_log" 2>&1; then
    echo '  [FAIL] incomplete graphics/boot protection was accepted' >&2
    exit 1
fi
if ! grep -Fxq 'old protected config' "$target"; then
    echo '  [FAIL] partial-config failure did not restore the old file' >&2
    sed 's/^/    /' "$partial_log" >&2
    exit 1
fi
grep -Fxq 'old protected config' \
    "$backup/etc/dnf/libdnf5.conf.d/90-pocketds-hardware-protection.conf"
done

target="$fixture/remove/etc/dnf/90-pocketds-hardware-protection.conf"
mkdir -p "$(dirname "$target")"
if run_installer bad "$target" "$fixture/remove/backup" >/dev/null 2>&1; then
    echo '  [FAIL] invalid new configuration was accepted' >&2
    exit 1
fi
[[ ! -e $target ]]

target="$fixture/exact/etc/dnf/90-pocketds-hardware-protection.conf"
mkdir -p "$(dirname "$target")"
install -m 0644 "$source_file" "$target"
if run_installer bad "$target" "$fixture/exact/backup" >/dev/null 2>&1; then
    echo '  [FAIL] unloaded existing configuration was accepted' >&2
    exit 1
fi
cmp -s "$source_file" "$target"

echo '  [OK] focused DNF protection install restores every failed mutation'
