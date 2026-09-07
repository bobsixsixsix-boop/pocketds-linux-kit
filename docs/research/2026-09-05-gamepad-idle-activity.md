# PDS-055 — gamepad input and five-minute idle-off, 2026-09-05

## Finding

The user reports the display turning off after roughly five minutes of play.
On this Fedora device, libinput lists the touchscreens, managed touchpad and
keyboards, but not InputPlumber's virtual Xbox Elite 2 controller. The virtual
gamepad is present as a joystick, has a desktop-user ACL, and is open in Steam.
KWin's gamecontroller plugin is available but not loaded. The normal display-off
timeout is still AC/battery 300 seconds, low-battery 120 seconds, with no inhibitor.
Thus the managed gamepad path has a confirmed missing idle-activity bridge.
Whether the user's particular occurrence also involves touch input remains
unconfirmed; no physical five-minute game session has been observed yet.

## Implementation

The new independent user service observes the existing virtual controller
non-exclusively and sends rate-limited activity notifications only for meaningful
input or an intentionally established, revalidated hold. It uses the existing
KWin ScreenSaver API rather than generating artificial keyboard/mouse events.
The active session, lid, lock, sleep, backlights and InputPlumber profile are gated.
Notifications pin the inspected unique D-Bus owner to avoid owner-switch races.
It never wakes displays, requests inhibitors or changes the normal power policy.
Service restarts, drift, stale events and initial snapshots cannot establish holds.
See the [component contract](../../components/gamepad-activity/README.md).

No kernel, boot image, partition, Android, sleep opt-in, lid helper, desktop
configuration, InputPlumber mapping or existing service is changed by this fix.
The broader installer is updated for future installations but is not run here.

## Primary-source review and native compatibility

Observed packages: KWin 6.7.3, KIdleTime 6.28.0, Fedora ARM64. The ScreenSaver
service owner resolves to the live `kwin_wayland` process. Its
`GetSessionIdleTime` method is unsupported on this platform, so it cannot be used
as evidence of activity delivery.

- [KScreenLocker 6.7.3 interface](https://raw.githubusercontent.com/KDE/kscreenlocker/v6.7.3/interface.cpp)
  forwards `SimulateUserActivity()` to KIdleTime. `GetActive` alone does not
  cover lock acquisition, so the bridge also latches `AboutToLock`.
- [KWin 6.7.3 startup](https://raw.githubusercontent.com/KDE/kwin/v6.7.3/src/main_wayland.cpp)
  imports its static idle poller; the
  [poller implementation](https://raw.githubusercontent.com/KDE/kwin/v6.7.3/src/plugins/idletime/poller.cpp)
  calls KWin's real input activity path. This differs from the generic external
  Wayland KIdleTime poller's no-op implementation.
- [KWin input implementation](https://raw.githubusercontent.com/KDE/kwin/v6.7.3/src/input.cpp)
  resets idle detectors. A separate
  [DPMS input filter](https://raw.githubusercontent.com/KDE/kwin/v6.7.3/src/dpmsinputeventfilter.cpp)
  handles physical-input wakeup; the new bridge does not synthesize those events.
- [PowerDevil DPMS action](https://raw.githubusercontent.com/KDE/powerdevil/v6.7.3/daemon/actions/bundled/dpms.cpp)
  receives the shared idle-resume path. The bridge additionally refuses activity
  on a dark screen so it cannot undo a manual blank or closed-lid transition.
- [KWin gamecontroller implementation](https://raw.githubusercontent.com/KDE/kwin/v6.7.3/src/plugins/gamecontroller/gamecontroller.cpp)
  also supplies keyboard/mouse emulation. Enabling that plugin would be a
  materially different input behavior, so it remains unchanged and disabled.

Native Python evdev lacks `InputDevice.set_clockid`; the bridge instead uses
Linux's per-reader `EVIOCSCLOCKID` ioctl, verified against the device header.
dbus-python bus objects likewise lack `get_unix_process_id`; owner validation
uses the actual `GetConnectionUnixProcessID` D-Bus method. Neither workaround
writes input events or requires new packages.

## Verified tests, with acceptance limits

- All **54** component tests pass on both Mac and the target Fedora Python 3.14.
  The first target attempt omitted the service fixture and failed one test with
  FileNotFoundError; copying that fixture produced the full passing target run.
- Before diagnostics, both displays had naturally idled off. `--check` reported
  observer ready, gate false and **zero notifications**, with both `bl_power=4`.
- One invocation of the existing managed display-wake helper restored the lit
  baseline. The new service itself never invokes that helper. Read-only check
  then reported observer ready, gate true, still zero notifications.
- A paired native five-second idle detector observed the following monotonic
  sequence without changing PowerDevil's real 300-second setting:

| Time (seconds) | Event |
| --- | --- |
| 8822.927282 | Independent detector ready |
| 8828.000567 | IDLE |
| 8828.010156 | One diagnostic activity notification |
| 8828.010177 | RESUMED |
| 8833.001100 | IDLE again with no further activity |
| 8840.928845 | Probe exited successfully; PASS, one notification |

This proves that the actual KWin API resets idle and permits normal idle to
return afterwards. It does not prove physical controller delivery or five-minute
gameplay. An earlier manually coordinated attempt sent its one notification
after the probe had ended; it is not counted as paired-test evidence. In total
there were two diagnostic API notifications before deployment, not one.

The full repository `make test` cannot run on macOS because existing Linux-only
C++ uses `stat.st_mtim`. This is not a regression in the new Python component;
the full suite must be checked on the Fedora target before acceptance.

## Deployment and remaining acceptance

At **19:49:53 JST**, code commit `3c66e33` was deployed by adding exactly two
new files: the observer under `~/.local/libexec/pocketds/` and its user systemd
service. Both destination paths were absent, including symlink checks. The
service was enabled for the graphical session and started; no other service
was restarted. Installed files compare byte-for-byte with the committed source.

- Observer SHA-256: `f6b2c4f954a38b56e7cf86a0f9084319a280fb21792ba511528d8a4a9581515e`.
- Unit SHA-256: `4200b2b6eb363e0ac5b029bcbc00818d22aa9657b8e18aacbd8ce47d9b13630a`.
- Full Fedora `make test` **PASS** before installation. Private log SHA-256:
  `1060498f22d00449f8ce6d57a3a7bf8f404c7b78399736571327761157acc3cf`.
- After deployment, `make check` completed with only the existing external
  Codex CLI warning; it includes the new service's active-state check and the
  existing sleep guard checks.
- The observer attached, remained active with zero restarts and around 18 MiB
  memory (peak 19,156,992 bytes, below its 64 MiB limit). No controller activity
  was reported during this initially hands-off observation.
- Both backlights remained `4`; read-only readiness reports attached, gate
  false, zero notifications. Installation did not wake the idle device.
- Existing KWin wrapper PID1434, Plasma PID1589, PowerDevil PID1608 and
  InputPlumber PID573 stayed active with zero restarts. Suspend counters remain3/0.
- Installed v3 image SHA `a136aeb0…`, lid-helper SHA `244bdf83…`,
  `powerdevilrc` SHA `fe8d5dd5…` and KWin output JSON SHA `0bcd7440…`
  match the immediately-before baseline. No deep sleep was requested or enabled.
- Mac and device repositories were synchronized by a fast-forward-only Git
  bundle after checking the device worktree was clean at `3df552f`.

Remaining: controller-only play beyond five minutes, subsequent hands-off
idle-off, physical manual blank/lid behavior, and a normal-boot startup check.
Do not mark PDS-055 fully accepted without those observations. Deep-sleep Hall
acceptance remains separate and pending as recorded in the prior report.
The deliberately conservative gate also declines when either screen is manually
disabled or InputPlumber uses another profile; those configurations are not yet
covered by this dual-screen managed-profile fix.

Private baseline/deployment evidence is stored outside Git in the local
`pds-gamepad-idle-uA1bC3` evidence directory. Raw controls are never captured.
