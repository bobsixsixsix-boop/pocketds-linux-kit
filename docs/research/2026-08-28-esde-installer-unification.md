# PDS-012 ES-DE installer unification

## Finding

The live ES-DE 3.4.1 launch chain is healthy for the current placeholder-only
library, but the main application installer still deployed settings and custom
systems to the obsolete `~/.emulationstation` tree. The dedicated
`install-emulation.sh` already deployed the tested launcher, prepare helper,
exact InputPlumber mode helper and managed system definitions correctly.

That split meant a later full install could appear successful while omitting
the helpers required by `pocketds-es-de` and repopulating a path ES-DE 3.x does
not use.

## Policy

The main installer now delegates the entire emulation phase to the dedicated
installer. There is one authoritative path for both focused and full installs.
It installs:

- the ES-DE and RetroArch launchers;
- `pocketds-es-de-prepare` and `pocketds-input-mode`;
- the desktop entry and managed system/settings templates;
- a default RetroArch config only when the user has no existing config.

The prepare helper alone updates `~/ES-DE`. It preserves a non-empty user ROM
directory, backs up changes, is idempotent and does not enumerate ROM content.
`import-live.sh` imports implementation files but deliberately does not import
the user's active ES-DE settings or custom systems into Git.

## Current evidence boundary

Recent real launches entered ES-DE in 1.406 and 1.624 seconds with no new ES-DE
coredump. The current ROM tree contains only placeholder text files, so this
does not validate a real or synthetic game library. Clean-install and 10-cycle
GUI acceptance remain required after the GPU observation window.
