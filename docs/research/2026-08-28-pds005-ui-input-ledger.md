# PDS-004/005/010 supervised UI/input ledger

Date: 2026-08-28. This work adds an offline evaluator and blank-ledger generator.
It does not stage or deploy UI files, activate a service, restart XWayland or
Plasma, inject input, take screenshots, or perform a physical interaction.

## Purpose

The remaining Panel, keyboard and joymouse gates were spread across prose and
past conversations. That makes a missing round easy to mistake for a pass and
makes results hard to bind to the exact code under test. The new evaluator
turns those gates into one strict supervised ledger while keeping subjective
free text and private application/device information out of the report.

The eleven required cases are:

- lower-screen Panel geometry, desktop round-trip and active-window fullscreen
  round-trip;
- graceful Plasma recovery plus separately supervised SIGTERM/SIGKILL fallback;
- keyboard cross-application focus, isolated XWayland failure/restart and touch
  layout accuracy;
- RB/RT click, LB/LT scroll and 100 gamepad/joymouse mode cycles with the Panel
  help page matching the observed mode.

The policy requires 3 geometry observations, 20 desktop and 20 fullscreen
round-trips, 5 graceful recoveries, 3 SIGTERM plus 3 SIGKILL fallbacks, 10
cross-application rounds across at least 3 application classes, 3 isolated
XWayland server-kill/client-restart rounds, 50 keyboard touch attempts, 50 attempts for each physical shoulder
mapping, and 100 mode cycles. All attempts must pass, the observer must confirm
the result, keyboard/GPU telemetry/InputPlumber restart deltas must remain zero,
and each case has its own no-stuck/no-mismatch/reversibility gates.

The XWayland case deliberately does not demand in-place survival: GTK3 with a
dead X11 connection cannot attach its existing GDK process to a replacement
server. Each round must therefore kill only a nested test server, observe the
test client fail, restart that isolated client within the harness bound, leave
the host X11 session untouched and avoid stuck visibility. The live compositor
must never be killed for this case.

## Deployment binding

Evaluation begins by checking the current Git checkout is clean and equals the
revision recorded by an applied PDS-005 six-file transaction. It validates the
private manifest and applied marker, rejects a rolled-back marker, and hashes
each current repository source, staged payload, live target and rollback
preimage. It also requires the repository and live InputPlumber joymouse
profiles to be byte-identical. Only then is the supervised ledger compared with
the resulting revision, manifest, UI bundle and joymouse digests.

This makes a report fail closed after a source edit, incomplete deployment,
unknown live edit, missing rollback material or input-profile drift. The report
contains hashes and aggregate counters, but no absolute path, timestamp,
application name, device identifier or free-text note.

## Intended post-soak workflow

After the PDS-008 soak passes, stage/apply/verify the exact six-file UI
transaction and activate the new Panel/keyboard in a separate controlled step.
Then create a new mode-0600 ledger with:

```sh
TRANSACTION=<applied-name> \
LEDGER=test-results/pds005-ui-input-evidence.json \
make prepare-ui-input-ledger
```

Fill only the predefined counts, booleans and final mode while performing the
supervised rounds. Evaluate without any live action:

```sh
TRANSACTION=<applied-name> \
EVIDENCE=test-results/pds005-ui-input-evidence.json \
OUTPUT=test-results/pds005-ui-input-evaluation.json \
make evaluate-ui-input
```

The template and report use exclusive creation and mode 0600. Exit 0 means all
eleven source-bound physical gates passed; exit 1 means valid but incomplete;
exit 2 means malformed, unsafe or drifted evidence/deployment.

## Source validation

Eleven fixture/static tests cover an exact deployment, live/payload/backup/
joymouse drift, rollback refusal, binding mismatch, missing cases, physical
faults, thresholds, strict JSON/types/outcome arithmetic, private files,
no-overwrite output and absence of live-action/free-text collection paths. No
UI transaction exists on the device yet, so no template or physical result was
created in this source phase.

The implementation is local commit
`28b0257da3d4f04cec7d9f69e8e9ef0fce6cf1c7` and device commit
`3db391107eda92fe591a4b7707796a892032bd23`; both resolve to exact tree
`7d51eab47c3fc4b3b3b6f54101ec8b51aee96e32`. Targeted fixtures, lint and the
complete test suite passed independently in both checkouts, and both were
clean afterward. The device transaction root remained absent. The concurrent
PDS-008 soak retained invocation `e6d19f0a75fe4c54ad46c8eb2d9a7faa`, PID
`162791` and zero restarts across sync and device tests.
