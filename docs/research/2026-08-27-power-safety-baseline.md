# Pocket DS power safety baseline

Date: 2026-08-27 (Asia/Tokyo)

Scope: PDS-001/PDS-019 failure isolation. This is not evidence that suspend,
lid wake, or resume authentication is release-ready.

## Policy

- systemd suspend is restricted to `SuspendState=mem` and
  `MemorySleepMode=deep`; the known-bad s2idle/freeze path is not an allowed
  fallback.
- Device suspend callbacks remain serialized with `pm_async=0` to avoid the
  observed Goodix/Qualcomm I2C ordering failure.
- logind and all three PowerDevil profiles ignore lid close and the power key.
- `LockOnResume=false` avoids an authentication path that currently has no
  trustworthy touch keyboard.
- The old custom lid/power-button services are disabled and removed by the
  installer so they cannot restart logind or race PowerDevil.

## Real-device evidence

- `/sys/power/mem_sleep` selected `deep` and `/sys/power/pm_async` was `0`.
- `systemd-analyze cat-config` resolved the installed deep-only and logind
  safe-ignore drop-ins exactly as managed by the repository.
- Hall events still reached logind (`Lid closed` then `Lid opened`) without a
  suspend transition.
- Both legacy custom services were `not-found` and inactive.
- `tests/suspend-preflight.sh` passed in read-only mode. No suspend was executed
  in this verification batch.

## Lock-screen prototype quarantine

The experimental `VirtualKeyboardLoader.qml` had overwritten an RPM-owned
Plasma file. Its saved pre-change copy matched the file digest recorded by
`plasma-workspace-6.7.4-1.fc44.aarch64`. The device was restored from that copy,
`rpm -V` became clean for the file, and the experimental version was preserved
under `/var/lib/pocketds-linux-kit/quarantine/` for later isolated tests. The
normal installer no longer writes this file.

## Rollback and next test

Installer writes are backed up under `~/.local/state/pocketds-linux-kit/backups/`
and `/var/lib/pocketds-linux-kit/backups/`. Re-enabling lid or power-key actions
must be a separate tested state-machine change, not a blind deletion of one
drop-in. Before that change, use RTC/SSH/console recovery and validate deep
suspend across AC/battery plus sound, Wi-Fi, touch, controller, both displays,
and independent brightness after resume. Do not automatically test s2idle.
