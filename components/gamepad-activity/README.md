# Gamepad activity bridge

Counts intentional input from the managed virtual Xbox gamepad as KDE activity.
It does not change the 300-second AC/battery idle policy or emulate keyboard or
mouse input. This fixes a gap where games receive controller input but KWin's
normal libinput path does not see it.

Requires the existing Python evdev, dbus-python and PyGObject packages. Runs as
the desktop user, using the existing device ACL, never as root. The installer
adds `pocketds-gamepad-activity.service` to `graphical-session.target`.

## Boundaries

- Non-exclusive read of exactly one managed **virtual** Xbox Elite 2 device;
  no physical-controller grab, profile mutation, FF writes or input remapping.
- Significant button, stick, trigger and hat transitions count. Initial held
  snapshots, neutral drift, duplicate/repeat packets and feedback do not.
  An intentional sustained hold counts only while revalidated in kernel state.
- At most one notification every two seconds, and none without new input or
  a revalidated hold. Stale events, queue overrun, unplug and suspend clear intent.
- Only an active local Wayland session, unlocked, lid open, both backlights on,
  not preparing for sleep, and the managed InputPlumber profile are eligible.
- The ScreenSaver D-Bus owner must be KWin. The unique owner is pinned across
  inspection and the single `SimulateUserActivity()` call; other owners fail closed.
- No inhibitors, DPMS/wakeup, PowerDevil refresh, lock, suspend, reboot, synthesized
  keystrokes or compositor plugin changes. Intentionally does not wake a dark screen.
- Logs only attachment/status and aggregate notifications, never raw controls.

Read-only readiness:

```sh
python3 -B ~/.local/libexec/pocketds/pocketds-gamepad-activity --check
systemctl --user status pocketds-gamepad-activity.service
```

`activity_allowed=false` is expected while the screen is off or the lid closed.
This check sends zero activity notifications.

## Tests and acceptance

`python3 -B tests/gamepad-activity.py` runs the pure/mocked regression suite.
`tools/gamepad-activity/wayland-idle-probe.py` only observes a short independent
KDE idle detector. `verify-idle-reset.py --watcher PATH
--confirm-one-activity-reset` explicitly sends one diagnostic activity reset
when all production gates pass; it requires an IDLE → RESUMED → IDLE sequence.
Neither diagnostic is installed as a service or changes the normal idle timeout.

That API test is **not** physical gameplay acceptance. After installation,
verify controller-only play beyond five minutes, release all controls and verify
normal idle-off, then verify manual screen-off and lid behavior remain intact.
Do not enable deep suspend as part of this test.

Rollback immediately stops the observer without changing power settings:

```sh
systemctl --user disable --now pocketds-gamepad-activity.service
```

For complete removal, archive only the two newly installed files after checking
their identities, then reload the user service definitions. No previous file was
overwritten in the initial scoped deployment.

See [PDS-055 evidence](../../docs/research/2026-09-05-gamepad-idle-activity.md).
