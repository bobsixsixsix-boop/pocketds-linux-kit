# PDS-002 read-only GPU/KWin baseline

## Current evidence

The active 165/60 session reproduced a new `Timeout waiting for GMU OOB`
event during a six-second read-only probe. At the time of the probe this boot
already contained 24 exact KWin atomic EBUSY events, 21 GMU OOB timeouts, two
GPU lockups, four recovery messages, two offending-task records, ten SMMU
faults and one fenced-register delay. PDS-002 is active, not historical.

## Collector contract

`scripts/pds002-gpu-kwin-baseline.py` emits JSONL only to stdout. It creates no
device file and has no display, power, service, backlight, devfreq or debugfs
write path. A client such as the development Mac owns durable output.

It consumes the existing low-overhead GPU telemetry cache instead of starting
a second DRM fdinfo scan, and samples PSI plus D-state wait channels. A journal
follower precisely classifies KWin EBUSY, GMU OOB, hangcheck lockup/recovery,
offender, HFI failure, SMMU fault, fenced-register delay, dma-fence and hung
task messages. Full-boot counts before and after the run cross-check follower
integrity.

The display signature includes KWin backend/output state and DRM connectors.
It is checked at the start, at the end and every 60 seconds, so a transient
display/KWin failure cannot silently pass merely because the final state later
matches the initial state.

Every run header also hashes the bounded, read-only `/sys/kernel/notes`
payload. This identity is mandatory for kernel A/B work because the locked
baseline and IFPC-only candidate intentionally use the same release string;
`uname -r` alone cannot distinguish them. The collector refuses symlinks,
writable or non-regular note files, changing metadata, empty content and more
than 4096 bytes. A missing or rejected identity remains an explicit error in
the header and must make an A/B run inadmissible rather than silently falling
back to the release string.

D-state records deliberately exclude command-line arguments. PID, process
name, UID and wait channel are sufficient for this investigation and avoid
capturing tokens, URLs or other process arguments.

## Interpretation

A run is `observed` when it sees a critical GPU/fence/hung-task event or exact
KWin atomic EBUSY. It is `inconclusive` when boot/display changes, journal
following loses events or sampling stalls beyond twice the requested interval.
No event in a single run is not enough to declare the GPU fixed; display-mode
and kernel A/B results require matched duration and workload.

## Baseline command

Stream the committed script over SSH and redirect stdout on the client. The
normal baseline is 45 minutes at a five-second sample interval. Record the
collector SHA-256, system version, repository commit, display matrix and test
conditions alongside the JSONL result.

Formal IFPC A/B runs additionally pass `--workload-sha256` and are evaluated
by `tools/kernel-ab/ab-evaluate.py`. Its locked matrix requires 18 private
reports: baseline/candidate, three distinct boot rounds, and dual 165/60, dual
60/60 plus upper-only 165 in every boot round. KWin, both GPU firmware files and
the installed fan controller are captured in every header and rehashed at the
end. The locked TuneD and fan profiles are captured in every five-second sample,
so package, power or cooling-control drift closes the acceptance gate.
