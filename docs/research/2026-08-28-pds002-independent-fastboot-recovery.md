# PDS-002 independent fastboot recovery design — 2026-08-28

## Actual boot chain

Read-only device inspection disproved a PC-style recovery design:

- `/sys/firmware/efi` is absent and `bootctl` reports no EFI boot;
- the 512 MiB VFAT `ROCKNIX` partition is mounted at `/boot`;
- no systemd-boot, GRUB or BLS entry is installed;
- the active loader consumes `\boot\Image`, which maps to Linux path
  `/boot/boot/Image` on the mounted FAT filesystem.

The upstream ROCKNIX ABL analysis says that a usable Linux artifact is loaded
and started; a later filename is not retried after that kernel panics. Merely
keeping a second image on the same FAT partition is therefore not an independent
recovery path.

## Bound ABL and host

The current device was previously flashed with the official ROCKNIX ABL v1.1.8
SM8550 payload. A new read-only audit measured the first 258,048 bytes of both
1 MiB ABL partitions. Both prefixes are SHA-256 `5f101821...`, exactly equal to
the retained official payload. Different full-partition hashes are expected
because the updater writes the payload with `notrunc`; old tail bytes are not
executed and are not used as identity.

The extracted v1.1.8 LinuxLoader contains the retained fastboot implementation,
including the explicit locked-device rejection for its boot command. The Mac
has official Android Platform Tools fastboot 36.0.2; the exact executable is
locked by SHA-256. Private 0600 copies of the baseline rollback image
`e4e0a38a...` and candidate runtime image `b29b7c34...` are present off-device.
Host material preflight passes while all live/recovery gates remain false.

## Recovery proof

The supervised experiment is intentionally asymmetric:

1. candidate `b29b7c34...` remains in all three on-disk aliases;
2. the user enters ABL fastboot mode with the Mac attached;
3. the dispatcher requires exactly one device and `unlocked: yes`;
4. it sends only `fastboot boot e4e0a38a...`, never a partition command;
5. after SSH returns, runtime attestation requires uptime ≤900 seconds;
6. `/sys/kernel/notes` must contain baseline build ID `8669714a...` while all
   three disk aliases still hash to candidate `b29b7c34...`.

`uname` is deliberately insufficient because both kernels have the same release.
The candidate's build ID is `2757c6af...`, so the notes hash and build-ID match
cryptographically distinguish the running kernel from the disk image. A normal
baseline-on-baseline boot and candidate-on-candidate boot never satisfy the
independent recovery gate.

The dispatcher revalidates and holds the baseline file descriptor across the
fastboot subprocess, writes a new 0600 report without the fastboot serial, and
cannot be redirected to a post-preflight path replacement. The runtime report
domain-separates and hashes the boot ID, and emits no path or raw UUID.

## Remaining gate

This design has 56 passing fixture cases and a passing real host-material
preflight, but no real fastboot RAM boot has occurred. After the RAM boot is
proved, the disk must be restored to baseline, rehashed, and normally booted.
The offline aggregate evaluator then requires same-token ordered RAM/restore
observations and a new-token normal boot before it can raise successful
boot/recovery counts or rollback prevalidation. No current evidence does so,
and candidate installation authorization remains false.

The live three-alias write is separately constrained by a fixed-root
transaction. Candidate staging accepts only an all-baseline preimage; baseline
restoration accepts candidate or a known mixed state. Each same-directory
rename is rehashed, failures re-enumerate all aliases and restore their exact
individual preimages, and a private intent exists before the first write. This
transaction also remains unexecuted on the live boot mount.
