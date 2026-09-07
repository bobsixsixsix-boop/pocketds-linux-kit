#!/bin/sh
# Build the pocketds-firmware tarball from a checked-out ROCKNIX clone, with
# the AYANEO-overlay tree (committed in this repo at ayaneo-overlay/) layered
# on top. The overlay carries the per-device blobs ROCKNIX doesn't ship --
# right now: AYANEO-signed Adreno 740 zap shader, plus the SQE microcode and
# GMU image needed to bring the GPU up.
#
# Usage: ./make-tarball.sh [<rocknix-clone-path>] [<output-version>]
#
# Defaults to ../../_ref/rocknix and a date-based version. The output is
# pocketds-firmware-<version>.tar.xz, suitable for use as Source0 in
# pocketds-firmware.spec.
set -eu

REPO="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$(cd "$REPO/../../_ref/rocknix" && pwd)}"
VER="${2:-$(date +%Y%m%d)}"

ROCKNIX_FW="${SRC}/projects/ROCKNIX/devices/SM8550/filesystem/usr/lib/kernel-overlays/base/lib/firmware"
AYANEO_OVERLAY="$REPO/ayaneo-overlay"

if [ ! -d "$ROCKNIX_FW" ]; then
  echo "error: ROCKNIX firmware tree not found under ${SRC}" >&2
  echo "       expected projects/ROCKNIX/devices/SM8550/filesystem/usr/lib/kernel-overlays/base/lib/firmware/..." >&2
  exit 1
fi
if [ ! -d "$AYANEO_OVERLAY/usr/lib/firmware" ]; then
  echo "error: ayaneo-overlay/usr/lib/firmware missing in ${REPO}" >&2
  echo "       run ./refresh-ayaneo-overlay.sh against an AR11 flat-build first" >&2
  exit 1
fi

OUT="${REPO}/pocketds-firmware-${VER}.tar.xz"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

DEST="${STAGE}/pocketds-firmware-${VER}"
mkdir -p "$DEST"

# Layer 1: ROCKNIX SM8550 firmware overlay (audio topologies, ADSP/CDSP for
# the rocknix-supported devices, ATH12K WCN7850, vpu30_p4.mbn, etc.).
( cd "$ROCKNIX_FW" && find . -type f -print0 ) | \
while IFS= read -r -d '' f; do
  rel="${f#./}"
  install -D -m 0644 \
    "${ROCKNIX_FW}/${rel}" \
    "${DEST}/usr/lib/firmware/${rel}"
done

# Layer 2: AYANEO Pocket DS overlay (this repo's ayaneo-overlay/, refreshed
# from a TurboX AR11 flat-build via ./refresh-ayaneo-overlay.sh). Wins over
# ROCKNIX on collisions because it's the per-device-signed copy.
( cd "$AYANEO_OVERLAY" && find . -type f -print0 ) | \
while IFS= read -r -d '' f; do
  rel="${f#./}"
  install -D -m 0644 \
    "${AYANEO_OVERLAY}/${rel}" \
    "${DEST}/${rel}"
done

tar -C "${STAGE}" -cJf "${OUT}" "pocketds-firmware-${VER}"
echo "Wrote ${OUT}"
echo "Files inside: $(tar tJf "${OUT}" | wc -l)"
echo "AYANEO overlay contents:"
tar tJf "${OUT}" | grep -E '/qcom/[^/]+$|/qcom/sm8550/ayaneo/' | head
