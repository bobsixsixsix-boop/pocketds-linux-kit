# ES-DE scan boundary audit — 2026-08-29

## Result

No current live evidence supports the earlier report that ES-DE is stuck while
scanning games. The live library is still an effectively empty placeholder:
the bounded read-only scan saw 174 regular files, zero recognized games and
zero errors in about 0.003 seconds. The latest privacy-safe log summary reports
one 1.691-second startup, one clean shutdown and no warning, error, fatal or
parse event. It emits no original log lines, paths, ROM names or game names.

The isolated real-library candidate passed only the existing fast inventory
gate: 43,874 regular files, 31,122,917,449 logical bytes, and no unexpected
entry, type or size mismatch. It has not been switched live, and that fast gate
does not bind every file's content. It is therefore neither scan-performance
nor interactive acceptance evidence.

## Harness defect and fix

The attended synthetic GUI harness built its fixture below
`$HOME/SyntheticLibrary`, while the production launcher refuses to run unless
the effective ROM directory is exactly `$HOME/ROMs`. The harness would fail in
launcher preflight before ES-DE started, producing a false test failure.

Commit `17e67ee4c9c070bea79d2863760dc72029e1a2d3` places the isolated fixture
under `$HOME/ROMs` and adds a no-GUI regression that runs the real launcher
against bounded fake application and input-mode endpoints. It does not change
the production launcher, live configuration or live library.

The 13 synthetic tests, five privacy-log tests, eight ROM preflight tests,
configuration migration, library stage and library verification fixtures, and
the 5000-item prepare-only path pass. Device `main` includes the exact commit
and passes lint. No GUI was started.

## Next gate

With the user present, run the 5000-item synthetic library for ten new isolated
homes and require startup within ten seconds, shutdown within three seconds,
clean bounded log summaries and exact input-mode restoration. Only after that
passes should the real candidate receive per-content binding, a reversible
configuration switch and ten supervised startup/interaction rounds.
