# PDS-004 keyboard visibility state

## Scope

The first PDS-004 layer separates visibility policy from GTK, GLib, D-Bus,
AT-SPI and systemd. It does not yet replace the live adapter, restart the user
service, or claim that SIGUSR1 startup and XWayland disconnects are fixed.

## Root cause isolated

The live keyboard currently sends every `show()` call through a 900 ms
editable-focus validation. A manual request therefore depends on an unrelated
AT-SPI probe and can hide itself after the timer expires. Several independent
GLib sources also mutate `visible`, `manual_hidden` and `focused_editable`, so a
cancelled callback can race a newer decision.

## State contract

`visibility_state.py` defines four explicit states:

- `HIDDEN_AUTO`
- `HIDDEN_BY_USER`
- `MANUAL_VISIBLE`
- `AUTO_VISIBLE`

Only `AUTO_VISIBLE` schedules the 900 ms validation. Manual visibility stays
active until an explicit manual hide/toggle. Every scheduled validation has a
cancellable handle and a generation token, because removing a GLib source is
not enough when its callback has already been queued.

Manual hide suppresses duplicate focus events from the same editable object;
focus loss or a different editable object starts a new automatic-focus cycle.

## Verification

`tests/keyboard-visibility-state.py` uses a deterministic clock that can force
delivery of cancelled callbacks. Its 13 cases include stale focus loss,
focus-switch races, explicit hide suppression, shutdown invalidation, and 50
manual show/hide cycles that each cross the old 900 ms window.

## Next layer

Add a thin GLib scheduler and stable AT-SPI focus identifier adapter to
`pocketds-keyboard.py`. Window mapping and geometry must be consequences of the
controller snapshot. SIGUSR1 readiness and XWayland process failure remain
separate acceptance items and must retain independent rollback paths.
