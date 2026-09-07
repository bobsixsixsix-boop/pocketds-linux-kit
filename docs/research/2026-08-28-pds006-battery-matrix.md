# PDS-006 private battery acceptance matrix

## Evidence gap

The existing Panel battery fixture proves units, range rejection, missing-field
behavior and the choice of `voltage_now × abs(current_now)` over the implausible
device `power_now`. One live full/external-power snapshot also agrees with
UPower. Neither fact proves repeated unplug/charge transitions or 24-hour
continuity, so PDS-006 remains VERIFICATION.

The currently running PDS-008 telemetry soak reads the GPU/display cache, not
battery status. It must not be relabeled as PDS-006 evidence.

## Ledger contract

`pds006-battery-matrix-evaluate.py` is an offline evaluator only. It never reads
sysfs, calls panelctl, changes external power, or starts/restarts a service. A
new evidence template is mode 0600 and `O_EXCL`; evaluation accepts only a
current-user, single-link, private, bounded UTF-8 JSON file with unique keys and
finite exact types.

The evidence is bound to one Git revision and SHA-256 of both
`pocketds-panelctl.cpp` and the Panel `main.qml`. It has no notes, paths,
timestamps, raw battery values or device identifiers. The required supervised
matrix is:

- five unplug-to-discharging attempts;
- five plug-to-charging attempts, performed below full charge;
- two full-on-external observations;
- an independent 24-hour run with at least 2880 samples, maximum gap at most
  60 seconds, zero read/invalid-sample failures and zero Plasma restarts.

Every transition must pass, expose the expected state/external-power boolean,
have complete bounded telemetry, and be confirmed by the observer. The soak
also checks that sample count, maximum gap and duration can describe a
continuous run. Missing cases remain INCOMPLETE; the evaluator never fabricates
a pass.

## Current verification

Twelve fixture/static tests cover a complete matrix, missing evidence, every
transition/soak gate, impossible counts, source drift, strict JSON, private
file/link/size boundaries, no-clobber output and the absence of any live
hardware/service/power path. The real supervised transition ledger and battery
soak remain **NOT RUN**. No power cable action or battery probe was performed
during the PDS-008 soak.

Implementation commit `8505e387c2e7b21c71294e695e67ef7175031a4b`
was transferred as device commit
`3f8b471df83f16cc636e2876aaacfe6322b27fc6`; both resolve to tree
`db9603816f881b01e3346f002d5d3f08c52ef5e0`. Focused 12/12 tests, lint,
`git diff --check` and the full `make test` passed on both hosts. The device
matrix also found all expected live services healthy. After verification, the
PDS-008 soak still had its original invocation
`e6d19f0a75fe4c54ad46c8eb2d9a7faa`, PID `162791`, zero restarts and
2026-08-28 03:59:35 JST start; no UI update transaction existed.
