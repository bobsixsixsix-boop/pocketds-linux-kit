# Physical lid resume: upper display unavailable — 2026-09-05

## Current outcome

**16:45 JST update:** the user authorized a normal reboot. Both displays,
desktop and lower Panel have recovered in the resulting screenshot and
hardware-state checks. Deep sleep stays disabled. This supersedes the earlier
"not recovered" statements below; the underlying resume defect is not fixed.

User report: “盒盖后开盖 上屏不显示了”. This is a failed physical-lid
acceptance, despite successful kernel suspend/resume counters. Daily deep
sleep was withdrawn at 16:36 JST through the existing reversible installer.
Root CanSuspend=no / safely_blocked=true and native KDE CanSuspend=false were
verified. The baseline light-standby helper remains active; RTC alarm is empty.

**The upper display is not recovered yet.** No reboot, graphical-session
restart, kernel/module/Android/partition write or filesystem repair was done.
Do not re-enable daily deep sleep on the strength of earlier RTC passes.

Disable backup:
`/var/lib/pocketds-linux-kit/daily-suspend-20260905T073614Z-67816`.
Evidence is outside Git in the Mac private `pds001-lid-display-MEnar0`
directory (journals, screenshot, DRM state, recovery-command outputs and the
exact running build tree's `dpu_rm.c`).

## Observed sequence (same boot 17966808-9976-45e6-80d0-45b5dc920081)

- 13:54:43: native idle suspend began. It resumed at 16:29:13, alongside a
  recorded short power-key event; measured sleep 9266.885 s, counters 2/0.
  This establishes a long actual sleep, not independently confirmed display
  or battery acceptance.
- 16:29:21: lid closed; systemd-suspend began at 16:29:22.
- 16:29:36: lid opened, systemd thawed the desktop and returned successfully;
  measured sleep 10.495 s, counters 3/0.
- The lid helper repeatedly found only DSI-2 in DPMS state and correctly
  rejected its incomplete wake. PowerDevil wakeup, refreshStatus and global
  DPMS on cannot re-enable a disabled connector.
- DSI-1: connected, disabled, current saved 1080x1920@120 mode, scale 1.5,
  rotation 2, position 0,0; bl_power=4, target brightness 1515.
  DSI-2: connected/enabled, 768x1024@60, scale 1.25, rotation 2,
  position 283,720; bl_power=0, brightness 2529.
- DRM state independently showed upper CRTC disabled and lower CRTC active.
  KWin/Plasma were still running. No new UFS/EXT4/block-I/O error was observed.

The screenshot contains only the surviving lower output, with desktop content
moved there and a kscreen-doctor crash-report popup. It is **not** evidence of
the upper panel recovering. No automatic crash report was submitted.

## Bounded recovery attempts and limits

1. Enable DSI-1 while preserving its saved mode/layout: rejected by the driver.
   New kernel errors: `_dpu_rm_make_reservation: unable to find appropriate
   DSC`, `dpu_rm_reserve: failed to reserve hw resources: -119`.
2. Global DPMS off, then enable upper and return DPMS on: same rejection.
3. One atomic lower-disable/upper-enable transition, followed by lower restore:
   same rejection.
4. A separate lower disable was rejected by KWin because it would disable all
   outputs; the following upper enable still failed. No all-off configuration
   was applied. The lower output remains usable.

The lid helper was stopped only within the bounded recovery attempts and
restarted in `finally`. No recovery loop or new daemon was installed. Existing
KScreen enable commands can return status zero while printing apply failure;
any eventual fix must verify actual output state, not just process exit status.
Lower enabled-output priority was normalized to 1 by KScreen while upper is
disabled; restore upper priority 1 / lower priority 2 after dual recovery.
Modes, scales and positions remain unchanged.

## Source lead, not a proven root-cause fix

The exact build tree's `_dpu_rm_dsc_alloc` returns `-ENAVAIL` immediately when
the first available DSC's parity does not match the assigned pingpong index;
it does not try the next DSC there. DRM state showed the surviving lower
pipeline using mixer 0. This makes resource ordering/parity a concrete lead,
but does not prove the exact runtime allocation or exclude other causes.
No speculative kernel patch or rebuild was deployed.

KWin also keeps separate lid-open/lid-closed output configurations in its
[6.7.3 output configuration store](https://raw.githubusercontent.com/KDE/kwin/v6.7.3/src/outputconfigurationstore.cpp).
That explains why lid transitions exercise a different configuration path;
it does not establish whether KWin or the driver originated this failure.

The earlier root postcheck validates counters and storage, not physical
display restoration. Its `resume=passed` must not be presented as full-device
acceptance. Further work requires restoring the graphical session/device with
user approval (applications will close), then validating the physical-lid
display path and any proposed fix before re-enabling daily deep sleep.

## User-authorized reboot recovery

Following “嗯重启吧”, normal `systemctl reboot` was requested at approximately
16:43 JST after verifying root/boot rw, no observed storage errors and the
disabled sleep gate. No forced reboot or kernel/partition write was used.
SSH returned by 16:44:46 with new boot
`2b2d4f79-4cc6-4557-a8ce-c1ebe600e6c1`, still on the installed v3 release.

- Both outputs connected/enabled: upper 120 Hz, scale 1.5, position 0,0,
  priority 1; lower 60 Hz, scale 1.25, position 283,720, priority 2.
  No post-reboot layout adjustment was needed.
- Both bl_power=0; exact raw brightness 1515/2529.
- Personally inspected `lid-reboot-recovered.png`: upper original desktop,
  wallpaper/icons and lower live Panel, with 37%/62% brightness values.
- Wi-Fi connected; root and boot rw; no failed system/user units; KWin and
  Plasma NRestarts=0. No new DSC allocation or UFS/EXT4/block-I/O error observed.
- Native KDE CanSuspend=false and root safely_blocked=true. The opt-out
  survived reboot. No automatic maintenance sleep hold or new sleep test added.

Evidence `reboot-recovery.log` and the screenshot are in the same private Mac
directory. This confirms reboot recovery, not physical lid/power/touch
acceptance or a repair of the underlying DSC issue.
