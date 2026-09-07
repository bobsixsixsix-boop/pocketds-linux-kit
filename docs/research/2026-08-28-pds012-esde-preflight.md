# PDS-012 ES-DE configuration and launcher preflight

Date: 2026-08-28. Scope: source/fixture work and one read-only live-config
check. ES-DE was not started, no ROM content was opened, and no installed file,
input mode, backup or GUI state was changed.

## Upstream format and policy

ES-DE's current `Settings::saveFile()` appends `bool`, `int`, `float` and
`string` nodes directly to a pugixml document. `loadFile()` reads those flat
top-level nodes and also reads the future standards-compliant `<settings>` root;
the source explicitly describes this as a transition path:
<https://gitlab.com/es-de/emulationstation-de/-/raw/master/es-core/src/Settings.cpp>.

The official installation guide documents `--gamelist-only` as skipping ROM
search and persisting `ParseGamelistOnly`; the user guide says that mode is
primarily for testing/debugging, so the firmware does not use it as a default
workaround:
<https://gitlab.com/es-de/emulationstation-de/-/blob/master/INSTALL.md> and
<https://gitlab.com/es-de/emulationstation-de/-/blob/master/USERGUIDE.md>.

## Closed launcher/config risks

The preparation helper now understands both upstream settings layouts while
rejecting duplicates across the flat and wrapped scopes, malformed XML,
DTD/entity declarations, unsupported elements, non-UTF-8, files over 1 MiB,
symlinks and multiply-linked files. It parses every settings/systems input
before the first write, preserves comments and processing instructions, escapes
XML values, uses unique microsecond backup roots, and never enumerates ROMs.

`--check-only` runs that exact preflight and reports only would-change booleans.
The launcher now refuses an unavailable/unmanaged InputPlumber status. Its
restoration trap is installed before requesting gamepad mode and restores the
exact previous `joymouse` or `gamepad` state even if the setter changes state
and then returns failure; ES-DE is never started on either failure path.

Fixture coverage includes current flat settings, the future wrapped layout,
cross-scope duplicates, invalid/truncated input, DTD, unsupported nodes,
symlink, hardlink, oversized input, paths needing XML escaping, byte-preserved
authoritative user paths, no-write planning and launcher status/set failures.

## Device evidence and remaining boundary

Source commits: local `b3e9f02` + `a2d79e6`; device `28b71d9` + `27fb943`.
The live settings file is the expected upstream flat layout. Read-only preflight
reported:

```json
{"check_only":true,"rom_content_touched":false,"settings_would_change":false,"systems_would_change":false}
```

SHA-256 of both live config files and the backup-file count were identical
before/after. Targeted tests and full device `make test` passed. The active
24-hour telemetry service retained its original PID and zero restarts.

The follow-up `pds012-esde-log-summary.py` reads a single-link UTF-8 regular
file through `O_NOFOLLOW`, caps input at 8 MiB, and emits only aggregate JSON.
It never emits a log path, source line, system name or ROM name; optional
reports are mode 0600 and never overwrite an existing file. Five adversarial
tests cover privacy canaries, error-only counts, symlink/hardlink/non-UTF-8/
oversized rejection, private report creation and the absence of raw-line
output paths. The same tests and the full device suite passed at device commit
`e3e7aec`.

The current 53,306-byte live log contains 453 lines and one complete session:
one 1,624 ms startup and one clean shutdown, with 405 DEBUG, 48 INFO, zero
WARNING/ERROR/FATAL and zero parse-failure events. The aggregate matcher also
found 23 broad “missing-reference” lexical events; these are not classified as
errors and no source lines were disclosed. This evidence describes the saved
log only and does not prove interactive responsiveness or a successful scan of
the user's real library.

This closes configuration corruption and input-mode lifecycle as candidate
causes; it does not claim the reported scan hang is fixed. Synthetic GUI and
the user's real library remain explicit, attended tests after the telemetry
soak, with startup ≤10 seconds and launcher shutdown ≤3 seconds.
