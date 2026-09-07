# PDS-005 Plasma geometry acceptance — 2026-08-28

## Scope and measurements

This result covers only the existing Plasma widget geometry. It does not claim
that exit-to-desktop, re-entry, fullscreen control or shell recovery is done.

- KWin reports DSI-2 at logical `(283,720) 819x614`, scale `1.25`, corresponding
  to the oriented 1024x768 physical panel.
- KDE Spectacle captured the 1280x1334 logical desktop at 2x, so the exact DSI-2
  crop is `(566,1440) 1638x1228` in the capture.
- The live containment 2 entry was `Applet-33:0,0,816,608,0;`: a real gap of
  three logical pixels on the right and six on the bottom. The much larger black
  regions in the combined screenshot were outside DSI-2 and were not Panel
  margins.

## Controlled correction

With only `plasma-plasmashell.service` stopped, both active geometry keys were
changed to `Applet-33:0,0,819,614,0;`, then the shell was started again. The
original config is backed up at
`~/.local/state/pocketds-linux-kit/backups/20260828-pds005-plasma-geometry/` with
SHA-256 `1507de2001af790cf16343cf1571cf505e1c4b49beed1eb7be1014939e091aa0`.

The setting survived the first controlled shell restart. Pixel inspection of
the exact DSI-2 crop found content at all four inside corners, transparent
pixels immediately outside the left/right output boundary, and no black border
line on any crop edge. This accepts exact coverage for the current session.

The later dynamic geometry helper independently dry-ran against this live
state, uniquely resolved containment 2 / applet 33 from the plugin owner, read
819x614 from fresh telemetry and reported both tokens already exact. It made no
live write. Its active-shell apply gate was also exercised and refused before
creating a backup.

## Newly exposed stop-path defect

A three-cycle restart check completed one normal stop/start, then hit the
vendor 40-second stop timeout on each of the next two cycles. Plasma entered
`deactivating (stop-sigterm)`; one cycle logged that a
`/usr/local/bin/pocketds-panelctl` QProcess was destroyed while still running.
The telemetry daemon remained PID 126678 with zero restarts. Systemd eventually
recovered Plasma after both timeouts; the final shell PID was 142168. No hard
kill of the graphical session was issued directly by the test.

This is a PDS-005 failure, not a geometry regression: exact geometry remained
stored, but graceful shell restart is not yet an acceptable recovery action.
Further restart cycling is paused until a bounded, confirmation-gated recovery
path exists.

## Acceptance boundary

- PASS: current-session exact DSI-2 widget coverage and persistence through all
  three shell restart results.
- FAIL: graceful shell restart latency; two of three cycles took approximately
  40 seconds and ended with a systemd timeout result.
- NOT RUN: cold boot persistence, logout/login, exit-to-desktop, re-entry,
  fullscreen application control, GPU-hang recovery and 24-hour stability.
- Standalone LayerShell remains a default-disabled, unbuilt experiment. Qt and
  LayerShell development packages were not installed for this result.
