<!--
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Moonlight presentation FPS hook

`pocketds-moonlight-fps-hook.c` intercepts `SDL_GL_SwapWindow()` after the
real function returns. It waits for two consecutive approximately one-second
windows containing at least eight swaps at no less than 8 FPS before publishing,
then replaces the telemetry file atomically about once a second. The
locale-independent file format is deliberately just one decimal number and a
newline, for example `59.8\n`.

The hook counts client-side GL swaps. It does not claim to be the remote game's
engine FPS or the physical panel refresh rate. A stopped or low-frequency SDL
window stops refreshing the file, allowing the consumer's age limit to expire.

Install for the current user with:

```sh
components/moonlight/pocketds-moonlight-fps-setup install
```

The setup script builds the library before changing Flatpak configuration,
rejects GLIBC requirements newer than the current KDE 6.11 Flatpak runtime's
GLIBC 2.42 ceiling, verifies the exact library path in a sandbox `/proc` process
map, and modifies
only the per-app `LD_PRELOAD` and `POCKETDS_MOONLIGHT_FPS_FILE` environment
overrides. A fresh install refuses an existing per-user app override instead of
guessing how to merge it. The exact installed override is snapshotted; uninstall
uses `flatpak override --reset` only while that file is unchanged, restoring the
original absent state without leaving explicit-unset keys. Any later drift makes
rollback fail closed.
It stores the library in Moonlight's existing app-private data tree and writes
telemetry to the Flatpak-managed shared runtime directory at
`/run/user/UID/app/com.moonlight_stream.Moonlight/pocketds-rendered-fps`, so it
does not grant Moonlight additional host filesystem access.

Use `status` to inspect the configured paths and `uninstall` to remove only this
hook. Moonlight must be restarted after installation or removal.

The files in this component use `GPL-3.0-or-later`, matching the installed
Moonlight package's `GPL-3.0+` metadata. This per-file identifier does not select
a root license for the rest of this repository.
