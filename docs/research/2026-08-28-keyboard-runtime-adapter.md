# PDS-004 keyboard runtime adapter

## Scope

This phase connects the previously tested pure visibility state machine to the
GTK/GLib/AT-SPI keyboard. It fixes the 900 ms manual-hide race and the Panel's
service-start readiness race. It does not claim that XWayland loss or fixed
window geometry is solved.

## Single state owner

`KeyboardVisibilityAdapter` owns the current AT-SPI proxy, stable focus ID and
voice hold. `VisibilityController` remains the only visibility policy owner.
The former `manual_hidden`, `hide_source`, `focus_validation_source` and
separate window `visible` fields are removed. GTK `show_all()`/`hide()` and
geometry calls each have one audited owner driven by controller snapshots.

Stable focus identity uses the AT-SPI application bus name and object path.
Mutable labels, roles and user-entered text never enter the identity. A stale
unfocus from the old Chromium tree cannot hide a newer editable field.

Voice recording/recognition temporarily holds focus validity. When ASR ends,
the adapter revalidates the stored proxy and schedules normal focus-loss
hiding even if AT-SPI omitted the loss event.

The GTK runtime also revalidates the one stored editable proxy every 500 ms.
This is not a second visibility owner: it only feeds a missing focus-loss into
the same controller and retains the existing 380 ms debounce. It closes the
observed Chromium shutdown path where the application tree vanished without a
focus-loss event; voice input keeps suppressing that revalidation while busy.
When a proxy becomes defunct its bus/path metadata can disappear before loss
handling. Release therefore uses the focus ID captured on gain; it does not
recompute an unrelated fallback hash and accidentally reject the current loss
as a stale-object event.

## Readiness protocol

The user service is now `Type=notify`. The process sends `READY=1` only after
its window, visibility controller, AT-SPI listener, hardware listener and
toggle owner exist. The Panel starts the service and then requests its
`ExecReload`; systemd waits for readiness before accepting the reload. This
removes the old path where `systemctl kill --signal=SIGUSR1` could deliver the
signal while Python still had its default terminate action.

An in-process parity gate remains as defence in depth for direct signals that
arrive after the minimal handler exists but before GTK is ready. Two queued
toggles cancel each other.

## Verification boundary

Unit/static tests cover timer cancellation, stable and defunct identities,
Chromium focus ordering, voice loss, 50 manual cycles, single GTK visibility
owner, module installation and the systemd notify/reload contract. Live Panel
50-round, Konsole single-round and isolated Chromium page-entry 5-round paths
now pass across the old 900 ms window. Chromium close-to-hide measured
0.375--0.470 seconds with the same service PID and zero restarts.

PDS-004 stays in verification until XWayland disconnect behavior is isolated;
PDS-005 separately owns dynamic lower-screen geometry.
