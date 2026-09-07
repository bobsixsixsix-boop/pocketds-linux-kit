# PDS-004 live automatic-focus acceptance

Date: 2026-08-28
Source baseline: `cf0f88c`

## Read-only live evidence

The deployed keyboard remained `active/running` with PID `92076` and zero
systemd restarts throughout the checks. Activating the existing Konsole through
`org.freedesktop.Application.Activate` caused the XWayland keyboard window to
map without creating a terminal or injecting input. It remained mapped after
the 900 ms validation window.

Konsole 26.08.0 on Qt/Plasma 6.7.4 exposed a stale AT-SPI state boundary. Its
tree retained 14 objects marked `FOCUSED`, including invisible page tabs and a
visible terminal, after focus moved away. `showDesktop(true)` emitted no AT-SPI
focus event. A transient, noneditable GTK/Wayland probe took real application
focus, but the keyboard remained mapped beyond the 380 ms loss delay because
the old terminal proxy still reported focused, showing and visible.

A focused event from a different AT-SPI application is stronger evidence than
the old proxy's stale Qt state. The staged adapter therefore releases the old
editable on a cross-application noneditable event. A noneditable event from
the same application retains the existing Chromium document-node safeguard.
Unknown application metadata falls back to the old validation behavior.

## Acceptance boundary

- Konsole editable focus automatic show across 900 ms: live pass.
- Cross-application automatic hide: live fail on the deployed baseline;
  deterministic staged coverage added, not deployed.
- Chromium editable focus: not run because Chromium was not running and this
  earlier audit did not create or modify a browser profile. A later isolated
  runtime-profile probe reached a focused, visible Chromium `entry` at 1.631 s;
  the keyboard mapped at 2.930 s and remained mapped past 1.1 s. Closing that
  profile produced no timely focus-loss event and the keyboard remained mapped
  for more than 7 s, motivating the bounded watchdog in the runtime adapter.
- The first watchdog attempt still failed because a defunct proxy lost its
  bus/path metadata. Recomputing identity yielded a fallback hash, so the
  controller rejected the current loss as stale. Release now uses the stable
  focus ID captured on gain. The same held-proxy probe observed `DEFUNCT=true`
  and accessibility-bus owner loss immediately after Chromium exit.
- Final isolated Chromium page-entry acceptance passed 5/5 rounds. Every round
  saw the focused/showing/visible `PDS004` entry and kept the keyboard mapped
  beyond 1.1 s; close-to-hide was 0.388, 0.463, 0.375, 0.403 and 0.470 s.
  Service PID 136145 and `NRestarts=0` remained unchanged. All temporary
  runtime profiles were deleted and background networking was disabled.
- XWayland server reconnect: not run. Killing the compositor-owned XWayland
  would disrupt every X11 client, and a GTK3 process using `GDK_BACKEND=x11`
  cannot establish a replacement GDK display connection in place. This needs
  an isolated nested-display test or a non-X11 keyboard shell.
- The earlier baseline did not restart Plasma/KWin, install packages, inject
  input, play sound, record or request network content. The later Chromium
  probe used only a local `data:` page, disabled background networking and
  removed each temporary runtime profile.

## Deployment and rollback

The adapter/watchdog was deployed to the user service with two explicit
backups: `20260828-pds004-chromium-watchdog` preserves the pre-watchdog runtime,
and `20260828-pds004-defunct-identity` preserves the first watchdog attempt.
The final service reached `READY=1` as PID 136145 with `NRestarts=0`; repository
and installed module hashes matched. Rollback restores the two files under
`home/pocketds/.local/bin/` from the selected backup and restarts only
`pocketds-keyboard.service`.
