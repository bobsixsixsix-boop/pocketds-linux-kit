# PDS-005 Panel action boundary — 2026-08-28

## Scope

This change stages touch controls for collapsing the lower-screen Panel,
showing dynamic handheld mappings, toggling the active window's fullscreen
state, and invoking the already accepted bounded Plasma recovery worker.  It
does not remove the applet, restart KWin, synthesize keyboard input or expose a
general command runner.

## Desktop and fullscreen behavior

The Panel remains the existing Plasma desktop applet.  "Return to desktop"
makes its opaque surface invisible and stops the two-second status timer, while
leaving one 240x68 reopen button at the upper-right of DSI-2.  This is local,
instant and reversible; it does not edit Plasma layout or kill a process.  The
state deliberately resets to expanded after a shell restart so a stale hidden
state cannot strand the controls.

On the live KDE 6.7.4 session, `org.kde.kglobalaccel` is owned by KWin and its
`/component/kwin` object exports
`org.kde.kglobalaccel.Component.invokeShortcut`.  `shortcutNames` includes the
exact action `Window Fullscreen`.  The helper therefore invokes that registered
KWin action with an argv vector through `/usr/bin/busctl`; it does not emulate a
key, guess a KDE 5 command or accept user-supplied D-Bus names.

## Recovery UI and result contract

The recovery button first opens a modal explanation.  Confirming sends only
`desktop-recover CONFIRM` to `pocketds-panelctl`; that closed action supplies
the recovery helper's exact unit and action confirmations.  The Panel process
may disappear immediately afterward, so the UI never treats successful queueing
as successful recovery.  Instead, normal status polling reads the private
worker result and exposes only a fresh two-minute result as `ok` or `failed`.
Older results are `stale`, and request/result IDs remain available for SSH
diagnosis.

An orphan request is never overwritten.  A separate confirmed cleanup can
unlink only the exact private regular file when all of these are true:

- its link count is one and its owner/mode pass the existing 0600 gate;
- its mtime is at least 90 seconds old but not implausibly older than one day;
- the independent worker is inactive/failed with MainPID zero; and
- the inode is unchanged while the same recovery mutex is held.

Fresh requests, active workers, symlinks, hard links, bad permissions, inode
races and implausible timestamps fail closed.  Cleanup never mutates Plasma.

## Dynamic mapping and resource boundary

The status helper reads InputPlumber's exact `ProfilePath` and reports one of
`joymouse`, `gamepad`, `other` or `unavailable`.  The help dialog shows the
RB-left-click, RT-right-click and LB/LT-scroll mapping only in `joymouse`; in
gamepad mode it explicitly says that the game frontend owns the controls.

A first direct D-Bus query on every two-second Panel sample increased the live
40-sample median from roughly 9 ms to 64.88 ms.  That version was rejected
before deployment.  A five-second private atomic cache reduced the next live
40-sample run to 9.099 ms median, 17.388 ms p95 and 64.355 ms maximum (the one
cold D-Bus refresh).  The fixed query has a one-second bus timeout and caches
`unavailable` for the same five seconds, so a missing or wedged InputPlumber
cannot turn each two-second status refresh into the bus default timeout.
A final installed 40-sample cached run measured 7.756 ms median, 12.523 ms p95
and 14.676 ms maximum.  Collapsed mode stops the timer entirely.

## Live acceptance

- recovery state machine: 19 mock paths pass;
- action helper: strict C++ compile and closed-action test pass;
- result/request telemetry fixture passes, including fresh success and a
  100-second stale request;
- missing cleanup confirmation exits 3 without creating or removing a request;
- an isolated 120-second private fixture was removed only while the real worker
  was inactive, and reported `plasma_mutations_executed=false`.

The source and installed artifacts now agree:

- QML SHA-256: `9cb400038ee6f7300e5679728edfccbfcac72a600971f630cb0939f5ceb8db38`;
- compiled `pocketds-panelctl` SHA-256: `ea601505a14d28dd624ef50e2828d1ec00ad9d4ece1744dd43ab50b940499d1d`;
- recovery helper SHA-256: `aabd869c87637a06fc6b9f325dc5327d0a635b6f7f7a67b0b5f26094b2cfe534`.

The installed files were backed up below the private user and root project
backup trees before replacement.  The complete device `make test` passes.
Five bounded live recovery requests have now completed through KDE's graceful
quit path.  The last four used the exact installed `pocketds-panelctl
desktop-recover CONFIRM` action.  Each result was healthy in about one second;
KWin stayed PID 1011, the keyboard stayed PID 136145 and GPU telemetry stayed
PID 126678 with zero restarts.  No Pocket DS QML load errors were logged.

The first action screenshot exposed a real regression hidden by the exact
stored KConfig tokens: Plasma's scripting API reported live Widget geometry
`816x608`, and its official writable geometry setter was immediately clamped
back to that size even when asked for `819x614`.  This produced a 12-physical-
pixel right strip and an 18-pixel bottom strip in Spectacle's 2x combined
capture.  The QML now derives its painted surface from
`Plasmoid.screenGeometry` instead of the clamped representation size.  After
bounded reload, the scripting API reported `819x614`.  Pixel inspection of the
exact `(566,1440) 1638x1228` DSI-2 crop found the Panel color in every pixel of
all four edges and all four corners, while the immediately adjacent left and
right pixels were transparent.  This accepts current-session exact coverage.

Spectacle must be forced onto the existing Wayland session when launched from
SSH.  One unqualified Spectacle probe and one unqualified `kscreen-doctor`
probe aborted in their own diagnostic processes; neither restarted or crashed
Plasma/KWin.  These are tool-launch defects, not Panel acceptance evidence.

## Remaining live gate

Physical touch collapse/reopen, both modal dialogs and an active-window
fullscreen round trip remain NOT RUN.  Cold login/boot persistence, repeated
forced fallback, SIGTERM/SIGKILL and start-failure recovery also remain open.
