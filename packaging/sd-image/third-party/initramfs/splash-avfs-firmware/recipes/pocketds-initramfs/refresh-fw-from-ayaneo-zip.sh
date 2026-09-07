#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Refresh initramfs/usr/lib/firmware/qcom/ from a TurboX AR11 flat-build flash
# zip. Run when AYANEO ships a new image and you want to bump the AYANEO-
# signed Adreno 740 zap shader (per-OEM signed) plus the matching SQE
# microcode and GMU image baked into the kernel-builtin initramfs.
#
# The same files live in copr/pocketds-gpu-firmware (rootfs RPM); keep the
# two in sync. After running this script here, also run
# copr/pocketds-gpu-firmware/refresh-from-ayaneo-zip.sh on the same zip.
#
# Usage:
#   ./refresh-fw-from-ayaneo-zip.sh <flat-build.zip>
#
# Required tools: unzip, debugfs (e2fsprogs), file.

set -eu

ZIP="${1:-}"
if [ -z "$ZIP" ]; then
    ZIP=$(ls -1 "$HOME/Downloads"/AR11_FlatBuild_*.zip 2>/dev/null | tail -n1 || :)
fi
[ -n "$ZIP" ] && [ -f "$ZIP" ] || {
    echo "usage: $0 <AR11_FlatBuild_*.zip>" >&2
    echo "       (or drop the zip in ~/Downloads and re-run with no args)" >&2
    exit 1
}

REPO="$(cd "$(dirname "$0")" && pwd)"
DEST="$REPO/initramfs/usr/lib/firmware/qcom"
mkdir -p "$DEST"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

unzip -j -q -o "$ZIP" "*ufs/super_*.img" -d "$STAGE"
VENDOR_IMG=
for f in "$STAGE"/super_*.img; do
    label=$(file "$f" | sed -nE 's/.*volume name "([^"]+)".*/\1/p')
    if [ "$label" = "vendor" ]; then
        VENDOR_IMG="$f"
        break
    fi
done
[ -n "$VENDOR_IMG" ] || {
    echo "error: no super_*.img in $ZIP has volume label 'vendor'" >&2
    exit 1
}

# See drivers/gpu/drm/msm/adreno/a6xx_catalog.c, chip 0x43050a01:
#   ADRENO_FW_SQE  = "a740_sqe.fw"
#   ADRENO_FW_GMU  = "gmu_gen70200.bin"
#   .zapfw         = "a740_zap.mdt"   (qcom_mdt_load -> .bNN)
for f in a740_sqe.fw gmu_gen70200.bin \
         a740_zap.mdt a740_zap.b00 a740_zap.b01 a740_zap.b02 a740_zap.mbn; do
    debugfs -R "dump /firmware/$f $DEST/$f" "$VENDOR_IMG" >/dev/null 2>&1
    [ -s "$DEST/$f" ] || {
        echo "error: vendor partition is missing /firmware/$f" >&2
        exit 1
    }
    chmod 0644 "$DEST/$f"
done

echo "Refreshed $DEST from $(basename "$ZIP")"
( cd "$REPO" && git status --short initramfs/usr/lib/firmware 2>/dev/null ) || :
