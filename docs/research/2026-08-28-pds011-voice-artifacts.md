# PDS-011 voice artifact lifecycle hardening

Date: 2026-08-28. Scope: source and fixtures only. No microphone, recording,
ASR process, clipboard, key injection, keyboard restart or GUI reload was used.
This does not claim any speech-recognition quality improvement.

## Deterministic defects

The keyboard reused a fixed WXASR `summary.json` without deleting it or
checking creation time. A backend that exited successfully without replacing
that file could paste a transcript from an earlier recording. The voice WAV
and summary also remained in the user runtime directory after recognition.
Finally, `arecord` wrote stderr to an unread pipe; sufficiently verbose output
could fill the pipe and delay its shutdown path.

## Closed lifecycle risks

`voice_artifacts.py` now owns deletion and summary ingestion. It removes only
the current user's single-link regular file or the symlink itself. A result is
accepted only when it is a fresh, current-user, owner-private, single-link,
non-linked UTF-8 JSON regular file no larger than 64 KiB with a non-empty
`final_text` no longer than 4,096 characters. The keyboard sets umask 077,
deletes any safe old summary before WXASR, records the start timestamp, and
cleans both WAV and summary in a `finally` block. Cleanup refusal cannot follow
a link or delete a multiply-linked file.

The recording child now discards quiet-mode stderr rather than creating an
unread pipe. Expected backend failures are reduced to stable reason classes so
private paths or recognizer output are not copied into the UI/service log. An
unexpected recognition-thread exception schedules the normal failure/reset
path. Recording and recognition failures use the designed danger state until
the timed reset.

## Verification and remaining boundary

Five artifact tests cover fresh normalization, stale/public/symlink/hardlink,
invalid/non-UTF-8/empty/oversized summaries, missing-safe cleanup, symlink-only
cleanup and directory/hardlink refusal. The keyboard adapter now has 28 tests,
including installer ordering and runtime lifecycle static contracts. Local
full `make test` passed. Source commits are local `6c9c1a1` and device
`6f610ca`; device target tests and full `make test` also passed. The installed
keyboard remained the prior build, active as PID 136145 with zero restarts,
while the PDS-008 soak retained its original PID and zero restarts.

PDS-011 remains OPEN. These changes do not select an ASR backend, measure
Chinese character error rate, latency or memory, validate the physical mic, or
decide whether the final interaction belongs in the standalone keyboard or a
Panel page. Deployment and one bounded screenshot/touch batch remain after the
PDS-008 soak.

## Privacy-minimal quality evaluator

`pds011-asr-evaluate.py` now defines the next measurement boundary without
running a backend. It accepts separate mode-0600 corpus and backend-result JSON
files, locks `zh-CN`, case IDs and audio SHA-256 values, and requires the result
case set and every audio digest to match. NFKC/casefold normalization retains
Unicode letters/numbers while ignoring spacing and punctuation, then computes
micro character error rate, mean/p95 case CER, p50/p95 latency, maximum peak
RSS and backend failure count. A backend failure is an empty hypothesis and
always fails the zero-failure gate.

Every acceptance limit is explicit at invocation; the project does not invent
an unmeasured default. The aggregate report is new mode 0600 and never contains
reference text, transcripts, audio hashes or paths. Seven tests cover exact
normalization, CER rejection, backend failure, duplicate/corpus/audio drift,
bounded edit-distance work, private input boundaries and report no-clobber.
Local full `make test` passed.
No real corpus/result exists yet, so there is no backend ranking or quality
claim.

Evaluator source commits are local `d4d9fd6` and device `64c24a0`. Device
fixture 7/7 and full `make test` passed while the telemetry soak retained its
original PID and zero restarts.
