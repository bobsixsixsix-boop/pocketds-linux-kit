# PDS-012 bounded ROM metadata preflight

`pocketds-rom-preflight.py` diagnoses filesystem shapes that can make a game
frontend appear stuck before any GUI test. It reads directory entries and
`lstat` metadata only. It never opens a regular file, follows a symbolic link,
or emits a ROM filename.

The JSON report contains aggregate directory/file/candidate-extension counts,
symlink and special-file counts, metadata errors, maximum observed depth and
elapsed time. Traversal stops with a distinct non-zero result on wall-clock,
entry-count or depth limits. A symlink root is rejected.

The scanner is deliberately not an ES-DE parser or benchmark. Candidate files
are a broad extension heuristic, archives are not opened, and an aggregate
clean report cannot prove that every game is valid. Its purpose is to make
path loops, unexpectedly large trees and permission/metadata trouble visible
without touching copyrighted content.

Unit tests cover privacy, symlink loops, invalid roots, timeout, entry/depth
limits and a 5,000-file synthetic tree. The isolated research prototype also
scanned 20,000 zero-byte files in 0.073 seconds on the development Mac; ARM
device performance must be recorded separately before setting a release
budget.
