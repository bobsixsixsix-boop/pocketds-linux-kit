# Native daily deep standby opt-in — 2026-09-05

**Superseded at 16:36 JST:** physical lid reopen left the upper display disabled
and the driver rejected re-enabling it. Daily deep sleep has been withdrawn
using the scoped disable installer. See `2026-09-05-lid-resume-display-failure.md`.
The enabled-policy statements below are historical, not the current setting.

## Latest authority and policy

The user explicitly requested “那实装深度待机吧” after the reversible v3
kernel installation. This authorizes daily deep sleep, superseding the earlier
disabled-policy boundary. No kernel, modules, Android, partitions or game
library were changed in this turn. No new reboot was performed in this turn.

Native KDE PowerDevil/logind own all ordinary sleep requests:

| Entry | AC / Battery | Low battery |
| --- | --- | --- |
| Short power press | Deep suspend | Deep suspend |
| Lid close | Deep suspend | Deep suspend |
| Idle display off | 300 s | 120 s |
| Idle deep suspend | 600 s | 180 s |
| Lock on resume / automatic lock | Disabled | Disabled |

Hibernate, hybrid sleep and suspend-then-hibernate are explicitly disabled.
The daily path never arms an RTC deadline: the 90-second wake in the acceptance
below belongs only to its attended rescue wrapper.

The old light-standby daemon yields closed-lid DPMS/profile/shutdown-timer
ownership while daily eligibility is true. Lid open retains the verified
dual-display wake/rearm path. When eligibility is false it retains its old
light-standby behavior. The native/fallback Hall sensor bridge is unchanged.

## Fail-closed implementation and persistence

`components/system/pocketds-daily-suspend.py` does not request sleep. It
provides the ordinary-session eligibility check and the mandatory pre/post
commands on `systemd-suspend.service`. The precheck locks:

- AYANEO Pocket DS, exact `7.1.12-pdsdiag.20260905.aarch64` runtime notes and
  installed v3 boot image (`a136aeb0…`).
- The entire 866-property live thermal-zone tree, SHA-256
  `922d6bea11837b70b162d53a50a0bc2153adf3dd412421ba5bdf91f8b70fc68f`.
- Read-write `/dev/sda13` root and `/dev/sda12` VFAT boot, with no observed
  UFS/EXT4/block-I/O errors. Kernel evidence is inspected before disk reads.
- Deep-only effective sleep policy, serial device callbacks, no device-debug
  test, no debug messages, enabled power-button and lid wake sources, and zero
  failed suspends on the current boot.

The root-owned opt-in token is
`/etc/pocketds-linux-kit/daily-suspend.enabled`. Baseline
`80-pocketds-sleep.conf` remains disabled; the explicit
`90-pocketds-daily-suspend.conf` enables only deep sleep. The earlier attended
guard and its hash are unchanged. A separate rule grants only ordinary suspend
to the active local `pocketds` session after runtime eligibility; bypassing
application inhibitors and multiple-session authorization stays denied.

A failed unit precheck aborts before systemd-sleep executes. Post-resume checks
require exactly one new successful cycle and no new failure or storage error.
A failed result leaves a block in `/run/pocketds-daily-suspend/blocked.json`
and preserves the pending evidence, preventing another ordinary request on
this boot. On storage faults, no persistent filesystem repair/write is attempted.
This is containment, not a cure for the historical UFS resume fault.

The dedicated installer backs up scoped root/user files, installs the guard
before enabling policy, and never requests sleep or restarts logind/KWin/Plasma.
Generic installation selects `powerdevilrc.deep` when the token is present;
live import keeps the enabled and baseline profiles separate. Normal future
boots read these persistent files; an additional reboot was not used as an
acceptance claim here.

## Native acceptance and the initially blocked dispatch

The whole ARM64 suite passed before deployment. Root verification and both
wake-source flags passed on installed boot
`17966808-9976-45e6-80d0-45b5dc920081`, with counters 0/0. The initial backup is
`/var/lib/pocketds-linux-kit/daily-suspend-20260905T041756Z-38598`.

The first native request at 13:22:30 JST did **not** enter sleep. No
PrepareForSleep true occurred, counters stayed 0/0, and the attended wrapper
timed out at 13:24:30. It is a failed dispatch, not a kernel suspend failure.
The operator had initialized PowerDevil while a root-owned `sleep:idle` block
inhibitor was active; with ignore-inhibit deliberately denied, KDE cached
CanSuspend=false. Repeating deployment under the finalization lock reproduced
the false capability even though a direct ordinary-action polkit check passed.
Therefore the first tentative “polkit reload timing” explanation was incomplete:
the maintenance sleep blocker was an observed triggering condition.

After removal of that blocking sleep inhibitor and a bounded PowerDevil
restart, the desktop's own CanSuspend returned true. The installer now checks
ListInhibitors before mutation, rejects blocking sleep owners, waits for the
ordinary active-session authorization and verifies KDE's final capability.
An idle-only maintenance inhibitor can pause automatic idle actions without
making suspend capability unavailable. No ignore-inhibit permission was added.

The second native request used the actual
`org.kde.Solid.PowerManagement.Actions.SuspendSession.suspendToRam` method,
not a root-only suspend bypass. A private adapter reused the unchanged attended
guard's delay inhibitor, true/false subscription, RTC arming and cleanup;
only dispatch was routed through the desktop service. Its logs explicitly name
the native method. The 90-second RTC alarm was test-only.

- Request and PrepareForSleep true: 13:26:57 JST.
- Production precheck passed at monotonic 2047.509 with counters 0/0;
  systemd froze user.slice and entered suspend.
- Actual platform sleep, from CLOCK_BOOTTIME minus CLOCK_MONOTONIC deltas:
  **89.722 seconds**. The production postcheck returned success with counters
  1/0 at monotonic 2051.377.
- PrepareForSleep false and verified RTC cleanup: 13:28:31 JST. Both the
  native test unit and systemd-suspend finished successfully. The boot ID
  remained unchanged, root rw and no storage errors were observed.
- Both backlights returned to `bl_power=0`, exact raw 1515 / 2529. A fresh,
  personally inspected screenshot shows the original desktop and lower Panel.
  KWin and Plasma remained at zero restarts. Wi-Fi/SSH returned; the SM8550-APS
  physical speaker and enhanced sinks are present. Audible playback is not
  asserted from sink presence.
- No new physical power-button/lid/touch acceptance was received during this
  turn. The screenshot and native timed cycle do not substitute for it.

The first failed dispatch was archived before resetting only that test unit's
failed status. The kernel's failed-suspend count remains zero.

## Rollback

As the desktop user, use the scoped installer with `--disable --confirm
POCKETDS-DAILY-DEEP-20260905`. It moves the opt-in, enabling override and
additional authorization/unit drop-ins into a recoverable backup, restores
the baseline PowerDevil profile and refreshes the two relevant services.
It does not roll back the kernel.

A real disable check on this turn returned `AllowSuspend=no`, root
CanSuspend=no and safely_blocked=true. The existing kernel transaction's
read-only baseline plan then passed without writing a boot image. Disable
daily sleep **before** using that kernel rollback transaction; otherwise its
deliberately blocked-policy precondition will refuse. Daily policy was then
re-enabled; the final installed state is the enabled policy, not the rollback.
The final corrected enable from source `3638290` completed successfully with
an idle-only maintenance hold. Its preimages are in
`/var/lib/pocketds-linux-kit/daily-suspend-20260905T043649Z-44746`.
The complete on-device ARM64 suite passed again on this final source, including
all 17 daily-suspend tests. There were no failed system or user units in the
final read-only check; the unchanged image and attended-guard hashes matched.
The final runtime check passed with one unrelated existing warning: the
optional Codex CLI external dependency is absent. Maintenance inhibitors were
removed after verification, the managed dual-display wake path succeeded,
and native KDE CanSuspend remained true with no blocking sleep/idle hold.

## Evidence and remaining acceptance

Private initial deployment-backup archive SHA-256:
`d713e29fb0cda2efe7888988bd000d3d5d816da086e7fae13a7bc91f89af75bf`.
Raw journals, screenshots, test logs and preimages remain outside Git and are
copied to the Mac private recovery directory. Source/installer/docs are in Git.
The copied initial backup was independently hashed on the Mac and matched the
device archive above. The final full test and runtime-check logs are included.

One native timed cycle passed; the prior one-minute/five-minute v3 cycles had
separate user physical confirmations. Long standby, discharge measurements,
battery/charging combinations, repeated physical key/lid wake and the actual
ten-minute idle deadline remain unaccepted, as do the historical UFS/GPU root
causes. Enabling the user's requested trial does not erase these limits.

Primary API references used for this deployment:
[KDE 6.7.3 action/sleep enums](https://raw.githubusercontent.com/KDE/powerdevil/v6.7.3/daemon/powerdevilenums.h),
[native suspend dispatch](https://raw.githubusercontent.com/KDE/powerdevil/v6.7.3/daemon/actions/bundled/suspendsession.cpp),
[startup capability cache](https://raw.githubusercontent.com/KDE/plasma-workspace/v6.7.3/libkworkspace/sessionmanagementbackend.cpp),
[systemd 259 service pre/post semantics](https://raw.githubusercontent.com/systemd/systemd/v259/man/systemd.service.xml).
