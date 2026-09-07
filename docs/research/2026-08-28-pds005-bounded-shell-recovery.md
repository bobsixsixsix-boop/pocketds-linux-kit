# PDS-005 bounded Plasma shell recovery — 2026-08-28

## Scope

This change implements, installs and tests a recovery boundary.  The root-owned
helper and static user oneshot are deployed on the development device, but no
Panel button is exposed yet and the component is not release-accepted.

The device runs systemd 259.  Its Fedora
`plasma-plasmashell.service` is `Type=dbus`, owns
`org.kde.plasmashell`, uses `KillMode=control-group`, and has a 40-second stop
timeout.  Fedora's user-service drop-in changes stop-timeout failure mode to
`abort`.  Two of three earlier shell restarts reached that full timeout, while
the lower-panel geometry remained exact and the independent telemetry process
did not restart.  `/usr/bin/kquitapp6` and the Plasma Qt quit interface are
available on the device.

## Process boundary

A command launched by the Plasma Panel is itself a child of
`plasma-plasmashell.service`.  Such a process cannot safely stop or kill its own
cgroup and then remain alive to start the shell.  The design therefore has two
parts:

1. The CLI is read-only by default.  `--recover` requires the exact pair
   `--confirm-unit plasma-plasmashell.service` and
   `--confirm-action BOUNDED-PLASMASHELL-RECOVERY`.  It creates one private,
   30-second request and queues `pocketds-plasma-recovery.service` with
   `systemctl --user start --no-block`.
2. The static oneshot worker runs in its own `app.slice` cgroup.  It has no
   `PartOf`, `BindsTo` or `Requires` relation to Plasma and never restarts
   itself.  A direct manual start without a valid request exits nonzero before
   changing Plasma.

The request is an `O_EXCL`, no-symlink, owner-only runtime file.  It carries the
bounded timeout values and a random 128-bit request ID, and is consumed before
the first destructive action, so
restarting a failed worker cannot repeat recovery.  A nonblocking flock covers
the full worker action.  During trigger handoff the request itself excludes a
second trigger: the trigger releases the worker mutex before asking systemd to
start the oneshot, so a worker that starts immediately cannot lose a lock race.
The mocked suite deterministically exercises that exact ordering.
The worker permits a bounded two-second mutex handoff wait so a simultaneous
read-only status probe cannot strand an otherwise valid request.
The queued response returns the request ID and every worker result repeats it;
the Panel must ignore any result with a different ID.  This prevents an older
successful result from being mistaken for the current recovery.  Internal
snapshot/command exceptions also replace the result with fresh failure JSON.

## One-shot state machine

The worker is fixed to `plasma-plasmashell.service`; there is no user-supplied
unit argument.

1. Snapshot Plasma plus `pocketds-keyboard.service` and
   `pocketds-gpu-telemetry.service`.
2. Prefer KDE's own `kquitapp6 plasmashell` request and wait up to two seconds.
3. If KDE quit fails or remains live, issue only
   `systemctl --user stop --no-block plasma-plasmashell.service`, then enforce a
   five-second SIGTERM deadline outside systemd's 40-second timeout.
4. On that deadline only, send SIGKILL with `--kill-whom=all` to that exact unit
   and wait two seconds for an inactive/failed state with `MainPID=0`.
5. Run `reset-failed` once, request `start --no-block` once, and wait at most 12
   seconds for `active/running`, a positive MainPID, and the
   `org.kde.plasmashell` bus owner.
6. Verify keyboard and GPU telemetry active/sub states, PIDs and restart counts
   are unchanged.  Never stop, start or signal either protected service.

There is no retry loop and no KWin, graphical-session target or user-manager
operation.  Success and failure are JSON.  Failure exits nonzero and includes
bounded SSH/TTY fallback commands.  The oneshot writes its last JSON result by
write-all, file fsync, atomic replace and runtime-directory fsync.

All five timeouts are configurable within hard upper bounds.  Even a `failed`
unit is not accepted as stopped while `MainPID` is nonzero.

## Mock acceptance

`tests/pds005-plasma-recovery.py` covers:

- normal KDE quit, one reset and one start;
- KDE quit command failure with controlled SIGTERM fallback;
- full quit/SIGTERM timeout followed by exact-unit cgroup SIGKILL;
- already-inactive startup and a failed single start;
- lock conflict and the immediate-worker-start handoff race;
- invalid/missing double confirmations and one-shot request consumption;
- fresh failure output for malformed requests and request-correlated internal
  worker exceptions;
- read-only status/dry-run and protected-service invariants;
- failed-with-live-MainPID rejection; and
- the independent, non-restarting oneshot unit contract.

All 16 mocked paths pass on both the staging host and the aarch64 device.  The
complete device `make test` and lint suites also pass.

## First live acceptance

At 2026-08-28 03:02:54 JST, one confirmation-gated live recovery ran through
request ID `7c9fbae12f601736a7ea1bfaf1a13353`.  It took the preferred KDE quit
path: `kquitapp6` returned zero, the inactive state was observed, and the
worker issued one `reset-failed` and one nonblocking start.  Neither SIGTERM nor
SIGKILL was needed.  The result was correlated to the request ID and reported
healthy with no protected-service violations.

Before/after evidence:

- Plasma changed PID 142168 to 148448 and regained `active/running` plus the
  `org.kde.plasmashell` bus owner;
- KWin stayed PID 1011;
- keyboard stayed PID 136145 with zero restarts;
- GPU telemetry stayed PID 126678 with zero restarts;
- the worker returned to `inactive/dead`, `Result=success`, zero restarts;
- the private request was consumed and did not remain in the runtime directory;
- the exact `Applet-33:0,0,819,614,0;` geometry remained present; and
- the system plasmashell coredump count stayed at five.

The root helper is `root:root` mode 0755 and the user unit is mode 0644.  Their
installed SHA-256 hashes match the repository sources.  The unit is `static`,
not enabled, and was inactive before the explicit request.

## Remaining risk and live gate

- The main installer now backs up and installs the root-owned executable plus
  user unit, performs its existing daemon-reload, and deliberately does not
  enable or start the oneshot.  Selective first install passed; repeated full
  installer idempotence and backup restoration remain untested.
- The preferred live `kquitapp6` and bus-owner recovery path passed once.
  Repeated recovery plus controlled SIGTERM, SIGKILL and failed-start paths
  remain untested on real hardware and require SSH/TTY recovery.
- Panel UI integration remains intentionally absent.  It must present the two
  confirmations and poll the worker result rather than assume that a queued
  start succeeded.
- The existing Steam launcher has a separate direct Plasma stop/start path and
  is not silently redirected by this staging.
- A stale request intentionally blocks another trigger instead of being
  overwritten.  A reviewed exact-file cleanup flow is needed before exposing
  the action in the Panel.
