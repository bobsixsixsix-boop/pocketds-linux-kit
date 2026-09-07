#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Refresh the ayaneo-overlay/ tree in this repo from a TurboX AR11 flat-build
# flash zip. Run when AYANEO ships a new image and you want to bump the
# AYANEO-signed Adreno 740 zap shader (per-OEM signed) plus the matching SQE
# microcode and GMU image.
#
# After this returns, `git diff ayaneo-overlay/` shows what changed; commit
# it and bump pocketds-firmware.spec Version: to YYYYMMDD from the AR11
# filename, then re-run make-tarball.sh.
#
# Usage:
#   ./refresh-ayaneo-overlay.sh <AR11_FlatBuild_*.zip>
#   (or drop the zip in ~/Downloads and run with no args)
#
# Required tools: unzip, debugfs (e2fsprogs), file.

set -eu

ZIP="${1:-}"
if [ -z "$ZIP" ]; then
    ZIP=$(ls -1 "$HOME/Downloads"/AR11_FlatBuild_*.zip 2>/dev/null | tail -n1 || :)
fi
[ -n "$ZIP" ] && [ -f "$ZIP" ] || {
    echo "usage: $0 <AR11_FlatBuild_*.zip>" >&2
    exit 1
}

REPO="$(cd "$(dirname "$0")" && pwd)"
QCOM_DEST="$REPO/ayaneo-overlay/usr/lib/firmware/qcom"
ATH_DEST="$REPO/ayaneo-overlay/usr/lib/firmware/ath12k/WCN7850/hw2.0"
mkdir -p "$QCOM_DEST" "$ATH_DEST"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

unzip -j -q -o "$ZIP" "*ufs/super_*.img" "*ufs/NON-HLOS.bin" -d "$STAGE"

# --- Adreno 740 GPU triplet from the vendor partition --------------------
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

# drivers/gpu/drm/msm/adreno/a6xx_catalog.c, chip 0x43050a01:
#   ADRENO_FW_SQE = "a740_sqe.fw"
#   ADRENO_FW_GMU = "gmu_gen70200.bin"
#   .zapfw        = "a740_zap.mdt"   (qcom_mdt_load -> .bNN)
for f in a740_sqe.fw gmu_gen70200.bin \
         a740_zap.mdt a740_zap.b00 a740_zap.b01 a740_zap.b02 a740_zap.mbn; do
    debugfs -R "dump /firmware/$f $QCOM_DEST/$f" "$VENDOR_IMG" >/dev/null 2>&1
    [ -s "$QCOM_DEST/$f" ] || {
        echo "error: vendor partition is missing /firmware/$f" >&2
        exit 1
    }
    chmod 0644 "$QCOM_DEST/$f"
done

# --- ath12k WCN7850 board.bin + regdb.bin from NON-HLOS ------------------
# The Pocket DS's WCN7850 reports qmi-board-id=0xff (= "no OTP burned"),
# which doesn't map to any entry in upstream linux-firmware's board-2.bin
# manifest -- so ath12k falls back to looking for plain board.bin. Drop
# bdwlan.e3c (the AYANEO BSP's reference / highest-id BDF) under that
# fallback name. AYANEO also ships their own regdb.bin with PocketDS-
# specific channel limits; prefer it over the upstream copy.
mkdir -p "$STAGE/non-hlos"
( cd "$STAGE/non-hlos" && 7z x -y "$STAGE/NON-HLOS.bin" \
    'image/kiwi/bdwlan.e3c' 'image/kiwi/regdb.bin' >/dev/null 2>&1 ) || true
[ -s "$STAGE/non-hlos/image/kiwi/bdwlan.e3c" ] || {
    echo "error: NON-HLOS is missing image/kiwi/bdwlan.e3c" >&2
    exit 1
}
install -m 0644 "$STAGE/non-hlos/image/kiwi/bdwlan.e3c" "$ATH_DEST/board.bin"
install -m 0644 "$STAGE/non-hlos/image/kiwi/regdb.bin"  "$ATH_DEST/regdb.bin"

echo "Refreshed $QCOM_DEST + $ATH_DEST from $(basename "$ZIP")"
( cd "$REPO" && git status --short ayaneo-overlay 2>/dev/null ) || :
