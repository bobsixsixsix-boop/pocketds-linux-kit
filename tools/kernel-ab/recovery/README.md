# PDS-002 independent fastboot RAM recovery

The Pocket DS does not boot through UEFI, systemd-boot, GRUB, or BLS. The
ROCKNIX-signed Qualcomm ABL scans a FAT partition for Linux boot artifacts and
loads the first usable one. A second filename on that same FAT partition is not
an independent fallback after a kernel has already started and failed.

The recovery design therefore uses the ABL's retained fastboot implementation
to RAM-boot the exact locked live baseline image from a separate Mac. It never
flashes a partition. The current device has ROCKNIX ABL v1.1.8 on both ABL
slots: a read-only prefix audit found that each slot's first 258,048 bytes has
SHA-256 `5f101821...`, equal to the official v1.1.8 SM8550 payload. The private
host also has official Android Platform Tools fastboot 36.0.2.

## Evidence stages

1. `preflight.py` checks the locked baseline and candidate runtime boot images,
   official ABL payload, real fastboot executable and exact version. It has no
   device or execution interface.
2. `transaction.py` defaults to plan-only and has no reboot path. It binds the
   exact machine, kernel release, `/dev/sda12` read-write VFAT mount, both locked
   images and all three aliases. Candidate staging requires an all-baseline
   preimage; baseline restoration accepts candidate or a known mixed state.
   Execution requires root, an action-specific confirmation and a pre-reserved
   private intent report. Each file is replaced atomically in its own directory;
   any failure reinspects every alias and restores its exact preimage.
3. `dispatch.py` defaults to plan-only. The execution path requires the exact
   confirmation, exactly one fastboot device and an explicit `unlocked: yes`.
   Its only state-changing command is `fastboot boot <baseline>`; it has no
   partition command. It revalidates and holds the exact baseline file descriptor
   across the fastboot subprocess, preventing a path swap after preflight. The
   new private report never records the device serial.
4. `runtime-attest.py` distinguishes the same-release kernels by the GNU build
   ID in `/sys/kernel/notes`. Independent recovery is observed only when the
   running build ID is baseline `8669714a...`, all three on-disk aliases remain
   candidate `b29b7c34...`, and uptime is within 900 seconds.
5. `evaluate.py` is offline-only. It requires private reports for static and
   host preflight, dispatch, baseline-over-candidate RAM boot, same-boot disk
   restoration and a subsequent new normal baseline boot. The RAM and restore
   observations must share one hashed boot token in increasing uptime order;
   the normal boot must have a different token. Only this aggregate report may
   set rollback prevalidation true, and it still keeps candidate installation
   false.

The locked host-material plan is:

```sh
ARTIFACT_DIR=/private/pds002/recovery \
ABL_PAYLOAD=/private/pds002/abl_signed-SM8550.elf \
FASTBOOT=/real/path/to/platform-tools/fastboot \
make plan-kernel-recovery
```

On the device, with both locked images stored outside `/boot`, first run the
read-only alias plan. Candidate staging and baseline restoration use separate
confirmations and output files; neither command reboots:

```sh
BASELINE_IMAGE=/private/pds002/pds002-live-baseline-rollback-v2.img \
CANDIDATE_IMAGE=/private/pds002/ifpc-native-lid-runtime-boot-v1.img \
TARGET=candidate make plan-kernel-boot-alias

sudo --preserve-env=BASELINE_IMAGE,CANDIDATE_IMAGE,CONFIRM,OUTPUT \
  make stage-kernel-candidate

sudo --preserve-env=BASELINE_IMAGE,CANDIDATE_IMAGE,CONFIRM,OUTPUT \
  make restore-kernel-baseline
```

The two execution targets require respectively
`PDS002-STAGE-IFPC-CANDIDATE-V1` and
`PDS002-RESTORE-LIVE-BASELINE-V1`. An intent left without a final report means
the three aliases must be re-attested before any reboot; baseline restoration
is deliberately able to repair a known mixed baseline/candidate state.

Only after the candidate is intentionally staged on disk, the user is present,
the device is physically in ABL fastboot mode, and the plan is rechecked:

```sh
ARTIFACT_DIR=/private/pds002/recovery \
ABL_PAYLOAD=/private/pds002/abl_signed-SM8550.elf \
FASTBOOT=/real/path/to/platform-tools/fastboot \
CONFIRM=PDS002-TEMP-BOOT-BASELINE-V1 \
OUTPUT=/private/pds002/recovery/dispatch-01.json \
make dispatch-kernel-recovery
```

Within 15 minutes of SSH returning:

```sh
RUNNING=baseline DISK=candidate make attest-kernel-recovery
```

Create each JSON input with `umask 077`, keep the RAM-boot and restored reports
from the same boot, and transfer the device reports into one private host
directory. After restoring all aliases and observing a new normal baseline boot:

```sh
STATIC_PREFLIGHT=/private/pds002/static.json \
HOST_PREFLIGHT=/private/pds002/host.json \
DISPATCH_REPORT=/private/pds002/dispatch.json \
RAM_ATTESTATION=/private/pds002/ram.json \
RESTORED_ATTESTATION=/private/pds002/restored.json \
NORMAL_ATTESTATION=/private/pds002/normal.json \
OUTPUT=/private/pds002/recovery-validation.json \
make evaluate-kernel-recovery
```

Passing the first three stages proves only that the independent RAM-boot route
worked. The aggregate validation cannot pass until the baseline is restored to
all three disk aliases and a new normal boot is observed. At least three matched
A/B rounds remain mandatory after that. No tool in this directory ever sets
`candidate_install_authorized` true.
