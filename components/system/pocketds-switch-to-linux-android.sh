#!/system/bin/sh
# AYANEO Pocket DS / ROCKNIX ABL v1.1.8: switch the next boot to Linux.
# Run this file with AYANEO Settings' built-in "run script as root" action.
set -eu

NORMALIZED_SHA256=fb560ce40bf17c7fc0dbda4b7e17748eb6e2fd30bbc8281ee0fc95acb28e19da
BOOT_MODE_OFFSET=2612
BOOT_SOURCE_OFFSET=2706

if [ "$(getprop ro.product.model)" != "AYANEO Pocket DS" ]; then
    echo "Refusing: this is not an AYANEO Pocket DS" >&2
    exit 1
fi

device=
for candidate in /dev/block/by-name/devinfo /dev/block/bootdevice/by-name/devinfo; do
    if [ -b "$candidate" ]; then
        device=$candidate
        break
    fi
done
if [ -z "$device" ]; then
    echo "Refusing: devinfo partition not found" >&2
    exit 1
fi

work=/data/local/tmp/pocketds-boot-switch.$$
umask 077
mkdir "$work"
trap 'rm -rf "$work"' EXIT HUP INT TERM

before=$work/devinfo-before.img
normalized=$work/devinfo-normalized.img
expected=$work/devinfo-expected.img
readback=$work/devinfo-readback.img

dd if="$device" of="$before" bs=4096 count=1 2>/dev/null
if [ "$(wc -c < "$before" | tr -d ' ')" != 4096 ]; then
    echo "Refusing: devinfo is not exactly 4096 bytes" >&2
    exit 1
fi

mode=$(od -An -tu1 -j "$BOOT_MODE_OFFSET" -N 1 "$before" | tr -d ' ')
source_mode=$(od -An -tu1 -j "$BOOT_SOURCE_OFFSET" -N 1 "$before" | tr -d ' ')
case "$mode" in 0|1) ;; *) echo "Refusing: unexpected BootMode" >&2; exit 1 ;; esac
case "$source_mode" in 0|1) ;; *) echo "Refusing: unexpected BootSourceMode" >&2; exit 1 ;; esac

cp "$before" "$normalized"
printf '\000' | dd of="$normalized" bs=1 seek="$BOOT_MODE_OFFSET" count=1 conv=notrunc 2>/dev/null
printf '\000' | dd of="$normalized" bs=1 seek="$BOOT_SOURCE_OFFSET" count=1 conv=notrunc 2>/dev/null
normalized_hash=$(sha256sum "$normalized" | cut -d ' ' -f 1)
if [ "$normalized_hash" != "$NORMALIZED_SHA256" ]; then
    echo "Refusing: devinfo does not match the verified Pocket DS ABL v1.1.8 template" >&2
    exit 1
fi

backup_dir=/sdcard/PocketDS-boot-switch-backups
mkdir -p "$backup_dir"
backup="$backup_dir/devinfo-before-$(date +%Y%m%d-%H%M%S).img"
cp "$before" "$backup"

cp "$before" "$expected"
printf '\000' | dd of="$expected" bs=1 seek="$BOOT_MODE_OFFSET" count=1 conv=notrunc 2>/dev/null
if [ "$mode" != 0 ]; then
    if ! printf '\000' | dd of="$device" bs=1 seek="$BOOT_MODE_OFFSET" count=1 conv=notrunc,sync 2>/dev/null; then
        echo "BootMode write failed" >&2
        exit 1
    fi
    sync
fi

dd if="$device" of="$readback" bs=4096 count=1 2>/dev/null
if ! cmp -s "$expected" "$readback"; then
    printf "\\$(printf '%03o' "$mode")" | dd of="$device" bs=1 seek="$BOOT_MODE_OFFSET" count=1 conv=notrunc,sync 2>/dev/null || true
    sync
    echo "Readback failed; attempted to restore the original BootMode" >&2
    exit 1
fi

echo "BootMode verified as Linux; backup: $backup"
sync
reboot
