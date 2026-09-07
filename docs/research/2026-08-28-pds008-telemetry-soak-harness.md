# PDS-008 telemetry soak harness — 2026-08-28

## Purpose and safety boundary

The earlier 60-minute cache observation proved useful but was produced by a
one-off command.  `scripts/pds008-telemetry-soak.py` makes the same class of
evidence reproducible before starting the 24-hour gate.  It is intentionally a
reader, not another telemetry producer: it reads only the existing private
`%t/pocketds-gpu-status.json` cache and the fixed
`pocketds-gpu-telemetry.service` accounting properties.

The harness has no service restart/stop/kill, KWin command, Panel action,
privileged helper, sysfs access, workload generator or power/display mutation.
It accepts no service name or command from the caller.  The cache must be a
regular, user-owned, 0600, single-link file no larger than 256 KiB and is opened
with `O_NOFOLLOW` where available.

## Continuity and resource gates

Every cache interval records successful attempts, failure categories, distinct
GPU timestamps, maximum timestamp gap, regressions, stale reads and maximum
sample age.  Display tracking separately records distinct KWin timestamps,
status counts and physical refresh pairs.  Any non-null `display_app_fps`
fails the run; output refresh must not be relabeled as application FPS.

Schema 3 also records the total refresh-pair transition count and the first 32
timestamped from/to events. This keeps configuration drift diagnosable without
allowing an oscillating compositor to grow the report without bound. The
acceptance rule remains exactly one refresh pair; transitions are evidence,
not a relaxation of that gate.

Start/end systemd accounting supplies the service PID, restart counter, current
and peak memory, and CPU nanoseconds.  The default 24-hour acceptance requires:

- at least 99% of scheduled attempts, zero read failures, one distinct GPU
  timestamp per read, zero regression/stale reads and no gap above 5 seconds;
- only `display_status=ok`, at least the conservative 60-second display cadence,
  exactly one observed refresh pair and application FPS always null;
- the same active telemetry PID, zero restart delta, at most 0.5% of one CPU,
  at most 8 MiB current-memory growth; and
- at most 0.1% of one CPU for the soak reader itself.

These are 24-hour gates.  A very short smoke can legitimately exceed the
reader CPU percentage because interpreter startup dominates its denominator.

## Crash and interruption evidence

The output must be a new caller-selected path.  A nonblocking flock prevents a
second writer.  Private 0600 JSON checkpoints are atomically replaced and
fsynced every 60 seconds, so a power loss leaves either the prior complete
checkpoint or the new one.  SIGINT/SIGTERM produces a final `complete=false`,
`completion_reason=signal` report and exit 4.  A final service-query failure is
also written as explicit terminal evidence rather than discarding the run.
Existing reports are never overwritten on startup.

## Acceptance so far

Six fixture/static paths pass and the complete device `make test` passes.  An
8.056-second live smoke read 4/4 samples with four distinct timestamps, 2000 ms
maximum gap, zero failures/regressions/stale reads, only `display_status=ok`,
one 165.0/59.999 pair and null application FPS.  Telemetry remained PID 126678
with zero restarts and used 0.2478% of one CPU during the short window.  The
reader measured 0.1486% because startup dominated eight seconds, so the short
report correctly did not claim 24-hour acceptance.  Its temporary report was
removed after inspection.

The formal 24-hour run remains NOT COMPLETE until a final atomic report exists
and every gate is evaluated from the whole interval.
