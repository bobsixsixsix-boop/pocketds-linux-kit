# Reversible installed kernel trial and Android desktop entry — 2026-09-05

Subsequent explicit user request: daily deep sleep was later enabled with a
native KDE pre/post gate. See `2026-09-05-daily-deep-standby.md`; disabled-policy
statements below describe the installation turn, not that subsequent opt-in.

## Scope and current result

After both attended v3 RTC tests and their separate physical confirmations,
the user accepted a reversible installation and requested the existing Android
switch on the desktop. The v3 image now boots normally from internal storage.
This is an installed **trial**, not acceptance of unattended deep suspend.
`AllowSuspend=no` remains active. No new suspend cycle, Android boot, partition
table edit, Android partition write, SD-library change or reinstall occurred.

This record supersedes the earlier RAM-only status in
`2026-09-05-orphan-trip-candidate.md`. The old PDS-002 three-alias/e4e0 recovery
lock describes a different installation and must not be used for this machine.
The recovery exercise below is not a result of that historical IFPC A/B matrix.

## Locked inputs and backup

The only active Linux image is `/boot/boot/Image` on `/dev/sda12`, VFAT rw.
Root is `/dev/sda13`, ext4 rw. The five alternative known image paths checked
by the transaction are absent. Another filename on this same FAT volume is
not treated as an independent recovery path.

| Identity | Boot image SHA-256 | Runtime release |
| --- | --- | --- |
| Baseline, 17,086,464 bytes | `85b41b68738090e5e04aa9672eecf8b08d9ef189d7c9207d5ec99ef8c7252673` | `7.1.0-100.20260717114522.pocketds.fc44.aarch64` |
| v3, 19,077,120 bytes | `a136aeb060d38f9a305336af36577c5971e7c7e81e78839a608a60d30ff24c71` | `7.1.12-pdsdiag.20260905.aarch64` |

Runtime notes SHA-256 are respectively `2b7ae7949422c36be686322fb12a8f8340127596accccc133716f2c420bae6f1`
and `044509cbbdd347135937ee3de42fb2bebe099a17bfa939e5a0093bd4760c64d0`.
Because v2 and v3 have the same kernel binary, notes alone do not identify
the v3 DT; the image hash and live thermal-tree checks are also required.

Root-private `/var/lib/pocketds-linux-kit/kernel-trial-20260905` contains
both read-only images, the locked transaction, all intent/result receipts and
`baseline-modules.tar.gz` (SHA-256
`e863735c69c8dd9964a1a29d79a4fdc4ecd477c581df9c22790dd0284ff3f3ca`).
The original module tree is still installed. The 317 candidate modules remain
in their separate diagnostic release directory and matched the retained v2
stage before installation; no module replacement was required in this turn.

`tools/kernel-ab/install-current-trial.py` defaults to read-only inspection.
Execution requires the fixed model, image hashes/sizes, runtime notes,
mount identities, trusted disabled sleep guard, empty RTC, no observed storage
errors, a process lock, a new receipt name and explicit confirmation. It uses
same-directory write/fsync/rename/readback and restores the exact known
preimage once on write/readback failure. It never reboots or changes BootMode.
Seven hermetic transaction tests passed. Source transaction SHA-256:
`181715b132024f7e4c575f5417f3c383cc43bd2008db9a17f41a83eacffba85f`.

## Recovery and normal-boot sequence actually performed

1. Receipt `recovery-stage`: while running the previously tested v3 RAM boot
   `7816e2dc-fb13-48fd-a08f-dd1ef766a37e`, replace the original on-disk image
   with the exact candidate and verify readback.
2. Enter ABL fastboot. Recheck serial `678cef50`, ROCKNIX-ABL, slot a,
   unlocked and non-userspace fastboot. Use `fastboot boot` with the exact
   off-device original image, **not flash**. Baseline RAM boot
   `408baabf-313d-4f56-add4-11feae5e5321` has the original runtime notes while
   the disk still hashes to v3. Root rw, desktop and required services passed.
   This asymmetry is the independent recovery-path proof.
3. Receipt `recovery-restore`: restore the baseline image from that RAM boot,
   verify the original hash, then perform a normal reboot. Boot
   `bcf8321b-8fb2-436b-8eaf-1070cbd45e6d` has original runtime notes and the
   original on-disk image, with healthy desktop services and no failed units.
4. Receipt `final-install`: install v3 again only after that normal baseline
   boot passed. A normal reboot, without fastboot dispatch, produces boot
   `17966808-9976-45e6-80d0-45b5dc920081`, candidate notes and candidate disk
   hash. This proves the final installation is not merely a RAM session.

All three transactions succeeded without automatic rollback. An initial SSH
timeout during baseline restart was followed by normal network availability;
there was no forced-power recovery. Do not classify one timeout as a boot hang.

For a future rollback, first recheck device identity and volatile storage
health. If normal Linux is unavailable, use the verified off-device original
image through the same checked ABL RAM-boot path. The root-private transaction
can then inspect `baseline`, and execute only with a **new** receipt name and
`--confirm POCKETDS-CURRENT-BOOT-TRIAL-20260905`. Verify its result and exact
baseline disk hash before a normal reboot. Do not reuse historical aliases,
erase/flash partitions, or continue disk writes after new UFS/I/O errors.

## Final installed-boot checks

- Required system and user services are active; Plasma, KWin and PowerDevil
  have zero restarts. Initial system/user failed-unit inventories are empty.
- Wi-Fi/SSH returned. SM8550-APS plus the physical speaker and enhanced sinks
  are present. This does not assert audible playback or a game/audio matrix.
- The live cpuss0–3 trip directories contain only the retained reset-monitor
  and thermal-engine nodes; all 28 fan nodes are absent and 22 critical trip
  types remain. No thermal-protection policy was relaxed.
- Root remains rw. Transaction inspections found no UFS/EXT4/block-I/O errors.
- A personally inspected Spectacle capture shows the original wallpaper,
  taskbar, desktop launchers and lower Panel. Both outputs retain the upper
  120 Hz / lower 60 Hz layout. Screenshot acceptance is not a new physical
  touch or long-standby acceptance.
- `pm_test=none`, debug messages 0, RTC empty and the guard safely blocked.
  The normal display-off policy was not replaced with automatic deep sleep.
- The complete `make test` passed on ARM64 Fedora. `make check` completed
  with only the pre-existing external Codex CLI warning. Both suspend counters
  are zero on this new normal boot: the checks did not initiate another cycle.

## Android desktop entry

The new `切换到 Android.desktop` is executable on `/home/pocketds/Desktop`,
also registered as `pocketds-switch-to-android.desktop` in the application
menu. The stock `smartphone` icon is present and visible in the screenshot.
It runs `/usr/local/libexec/pocketds-switch-to-android`, which shows a Chinese
save-work/restart confirmation and then invokes the **existing**
`pocketds-panelctl boot-android CONFIRM` backend. No duplicated BootMode writer
or direct reboot shortcut was introduced. Failure gets a visible error dialog.

The installer and installed-app-only desktop shortcut transaction now include
this launcher. The GUI runtime must exist before a desktop shortcut is added.
Six hermetic tests cover cancel, confirmation, dialog failure, backend failure,
argument bypass and the desktop command. The shortcut transaction and root
install-order tests also passed on ARM64 Fedora. The Mac repeat-install
fixture encounters the existing GNU `cp --no-dereference` dependency; its
failure is not treated as a passing test or a device failure.

The real installed confirmation window was launched in a bounded user service,
captured and personally inspected, then the preview service was stopped without
confirming. That exercises safe dismissal, not a physical Cancel-button click.
The boot ID did not change and full devinfo SHA-256 remained
`fb560ce40bf17c7fc0dbda4b7e17748eb6e2fd30bbc8281ee0fc95acb28e19da`.
The existing privileged helper and BootMode helper hashes are unchanged.
An actual round trip to Android was deliberately not run during installation.

## Off-device evidence

The entire root-private recovery directory was archived and copied to the
Mac's private `pds001-deep-recovery-current-v1/installed-trial-evidence`
directory. The original standalone RAM-boot recovery image also remains in
that parent directory. Raw logs, screenshots and boot/module payloads are not
Git content. Archive contents and the enclosed module backup hash were checked.

- Recovery archive: `dafbfec1975ce0f93254700da6c483528bb76726eb5c30aa2e601712715f5519`.
- Installed-boot dmesg snapshot: `46bd6c3bc6b5b19d9dd851fad1b8dd0cdf8d926f58feb0cbfcb9be7e09d2e4d7`.
- Complete ARM64 test log: `22f150065d69aa1ca2797ccc792b0ee30069732e5e5e4484e5129cc3827729f5`.

Separate off-device text proofs preserve the asymmetric recovery, the normal
baseline boot and the final normal candidate boot. Source and sanitized
documentation are committed; the device repository is synchronized by a
checked fast-forward bundle, without running the broad installer again.

## Remaining boundary

The prior one-minute/five-minute RTC tests passed, but repetitive standby,
battery/charging combinations, overnight duration, power consumption and the
historical UFS/GPU issues remain unclosed. The installed kernel does not turn
those missing tests into daily-use acceptance. Automatic deep suspend is still
disabled; the current deploy is explicitly reversible.
