Name:           pocketds-firmware
# Version: dnf does rpmvercmp on this. The pre-AYANEO-overlay release was
# 20260430-... ; if we used the AR11 zip's date "20251012" as Version,
# dnf saw the rebuild as a downgrade and refused to install it (mkosi
# images kept landing with the old 20260430 firmware, missing the
# ayaneo-overlay GPU + ath12k bits). Rule of thumb when bumping: Version
# must monotonically increase. Use today's UTC date; keep the AR11 zip
# date in the changelog only.
Version:        20260811
Release:        20260811175327%{?dist}
Summary:        Vendor firmware blobs for the Ayaneo Pocket DS (SM8550)

# These are Qualcomm-signed proprietary firmware blobs (ADSP/CDSP/VPU images,
# audio topologies, ATH12K WiFi firmware, GPU SQE/GMU/zap microcode, sound
# calibration json) redistributed from the device's stock Android image.
# ROCKNIX redistributes the SoC-generic bits under Qualcomm OEM
# redistributable terms; the AYANEO-signed Adreno 740 zap shader comes
# direct from the AYANEO Pocket DS stock Android (TurboX AR11 flat-build).
License:        LicenseRef-Qualcomm-redistributable AND LicenseRef-Atheros-redistributable
URL:            https://github.com/ROCKNIX/distribution

# Built by ./make-tarball.sh, which merges the ROCKNIX SM8550 firmware
# overlay with this repo's ayaneo-overlay/ tree. Refresh the AYANEO part
# from a new flat-build via ./refresh-ayaneo-overlay.sh, then bump
# Version: to today's UTC date (NOT the AR11 zip date -- see Version
# comment above for why). AR11 dates can move backwards; rpmvercmp
# requires Version to only ever go up.
Source0:        pocketds-firmware-%{version}.tar.xz

ExclusiveArch:  aarch64

# Firmware blobs only -- no compilation, no debuginfo
%global debug_package %{nil}

# linux-firmware / qcom-firmware ship the SoC-generic Qualcomm bits in
# compressed form (qcom/a740_sqe.fw.xz etc.). We ship our own uncompressed
# copies of the GPU triplet under /usr/lib/firmware/qcom/ -- different
# extensions, no file conflict -- because the kernel here is built without
# CONFIG_FW_LOADER_COMPRESS_XZ (compressed firmware triggered a probe oops
# on this device). Keep them as Recommends so the rest of the qcs8550
# blob set stays available.
Recommends:     linux-firmware
Recommends:     qcom-firmware

# We deliberately do *not* package the AYN Odin 2 firmware sub-tree even though
# ROCKNIX co-bundles it; it is for a different device. See the %%prep section.

%description
Per-device firmware needed at boot on the AYANEO Pocket DS (Snapdragon 8 Gen 2,
qcs8550):

  * /usr/lib/firmware/qcom/a740_sqe.fw       -- Adreno 740 SQE microcode
  * /usr/lib/firmware/qcom/gmu_gen70200.bin  -- Adreno 740 GMU image
  * /usr/lib/firmware/qcom/a740_zap.{mdt,bNN,mbn} -- AYANEO-signed zap shader
  * /usr/lib/firmware/qcom/sm8550/ayaneo/    -- ADSP, CDSP, sensor JSON
  * /usr/lib/firmware/qcom/sm8550/SM8550-APS-tplg.bin -- ALSA SOF topology
  * /usr/lib/firmware/qcom/vpu/vpu30_p4.mbn  -- video processor firmware
  * /usr/lib/firmware/ath12k/WCN7850/hw2.0/  -- WiFi (board, AMSS, M3, regdb)
    (also under .../updates/ so it wins the firmware search path and a
     linux-firmware update can't clobber it -- this kernel has no XZ
     firmware support and must load our uncompressed copies)
  * /usr/lib/firmware/renesas_usb_fw.mem     -- USB controller firmware

Without this package the modem firmware loaders silently fail, audio /
sensors / wifi do not come up, and (because of the missing zap shader)
Adreno never reaches GPU-running state -- which means no Wayland
compositor and the desktop never paints.

%prep
%setup -q -n pocketds-firmware-%{version}

# Drop the AYN Odin 2 / RetroidPocket firmware -- different devices, only here
# because ROCKNIX builds a single image for all SM8550 handhelds.
rm -rf usr/lib/firmware/qcom/sm8550/ayn
rm -f  usr/lib/firmware/qcom/sm8550/AYN-Odin2-tplg.bin

%build
# nothing to build

%install
mkdir -p %{buildroot}
cp -a usr %{buildroot}/

# Harden the WiFi firmware against linux-firmware clobbering.
#
# This kernel is built without CONFIG_FW_LOADER_COMPRESS, so it can only
# load *uncompressed* firmware. Fedora's linux-firmware ships the WCN7850
# ath12k blobs as .xz, and across linux-firmware updates the uncompressed
# copies we drop in /usr/lib/firmware/ath12k/ have been observed to get
# clobbered/removed (shared-path churn), leaving ath12k unable to load
# board/AMSS firmware -> "qmi failed to load board data file:-110" and no
# WiFi, persisting across reboots.
#
# Also install our uncompressed copies under /usr/lib/firmware/updates/,
# which request_firmware() searches *before* /usr/lib/firmware/ and which
# linux-firmware never owns -- so the kernel always finds our blobs first
# and a linux-firmware update can't take WiFi down. We keep the copies in
# the original location too (belt and braces).
mkdir -p %{buildroot}/usr/lib/firmware/updates
cp -a %{buildroot}/usr/lib/firmware/ath12k \
      %{buildroot}/usr/lib/firmware/updates/ath12k

%files
/usr/lib/firmware/qcom/a740_sqe.fw
/usr/lib/firmware/qcom/gmu_gen70200.bin
/usr/lib/firmware/qcom/a740_zap.mdt
/usr/lib/firmware/qcom/a740_zap.b00
/usr/lib/firmware/qcom/a740_zap.b01
/usr/lib/firmware/qcom/a740_zap.b02
/usr/lib/firmware/qcom/a740_zap.mbn
/usr/lib/firmware/qcom/sm8550/ayaneo/
/usr/lib/firmware/qcom/sm8550/SM8550-APS-tplg.bin
/usr/lib/firmware/qcom/vpu/vpu30_p4.mbn
/usr/lib/firmware/ath12k/WCN7850/hw2.0/
/usr/lib/firmware/updates/ath12k/WCN7850/hw2.0/
/usr/lib/firmware/renesas_usb_fw.mem

%changelog
* Tue Aug 11 2026 Pocket DS Maintainers <noreply@gitlab.com/linux-pocketds> - %{version}-%{release}
- ath12k: ship the Quectel NCM865 module-specific AMSS firmware
  (WLAN.IOE_HMT.1.1-00018, built 2025-08-20) as amss.bin via
  ayaneo-overlay, replacing the ROCKNIX generic WCN7850 build
  (WLAN.HMT.1.1.c5-00302, built 2025-06-25). The generic build drops TX
  frames under WiFi 7 3-link MLO ("dp_tx: failed to find the peer with
  peer_id NNNN" bursts, RX drops, throughput collapse); the module
  firmware has the fixed MLO peer lookup -- verified clean on-device
  under sustained bidirectional load.

* Sun Jun 07 2026 Pocket DS Maintainers <noreply@gitlab.com/linux-pocketds> - %{version}-%{release}
- Also ship the WCN7850 ath12k blobs under /usr/lib/firmware/updates/ so
  they win the firmware search path and a linux-firmware update can no
  longer clobber them (was: "qmi failed to load board data file:-110",
  no WiFi after a linux-firmware upgrade, persisting across reboots).

* Wed May 06 2026 Pocket DS Maintainers <noreply@gitlab.com/linux-pocketds> - %{version}-%{release}
- Release auto-stamped from build_timestamp; full history in git log
