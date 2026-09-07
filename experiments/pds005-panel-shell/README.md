# PDS-005 standalone Panel experiment

This directory is review-only. No default installer, autostart path or live
migration invokes it; the Make target runs pure temporary-file fixtures only.
The device currently lacks the Qt development
packages needed to compile it, and this experiment must not install packages.
The review snapshot was replayed against authoritative repository commit
`e565373`; rebase and rerun every pure test before any later integration.

## LayerShell geometry boundary

The shell binds only `QScreen::name() == "DSI-2"`; it never falls back to the
upper display. LayerShellQt anchors all four edges and lets KWin provide the
current logical output rectangle. No observed `283,720`, `819x614`, `816x608`
or historical `820x615` value is embedded.

The process owns `org.pocketds.Panel` and exports only `Show`, `Hide`, and
`Toggle`. Hiding keeps the standalone process alive so a desktop launcher or a
future hardware binding can reopen it. The placeholder does not yet reuse the
production Panel body, switch application fullscreen, or recover plasmashell;
those remain separate review slices.

## Applet-33 migration contract

`migration/applet_migration.py` is a pure byte model. It targets exactly:

* containment `2`;
* applet `33`;
* plugin `org.pocketds.controlpanel.v3`.

It refuses missing, duplicate, or differently owned targets. The planned
document is produced by removing only the exact applet section and descendants,
plus exact `Applet-33:` tokens from containment-2 `ItemGeometries*` values.
Other applets, geometry tokens, comments, line endings, wallpaper and desktop
settings stay byte-for-byte intact.

## Existing applet geometry repair contract

`migration/panel_geometry.py` is a separate, privileged-free helper for the
current Plasma applet. It discovers the unique `org.pocketds.controlpanel.v3`
owner instead of fixing containment/applet IDs, then obtains the active DSI-2
logical width and height from the fresh telemetry cache. It changes only that
containment's matching `ItemGeometries-WxH` and `ItemGeometriesHorizontal`
Applet token. The default action prints a dry-run JSON plan.

`--apply` and `--restore` require an inactive plasmashell through a mockable
process-state probe. They reject symlinks, non-regular/wrong-owner/unsafe-mode
configs, duplicated owners/keys/tokens and config races. Apply stores mode-0600
`original.appletsrc`, `applied.appletsrc` and `manifest.json` below
`~/.local/state/pocketds-panel-geometry/<original-sha256>/`, never overwrites a
conflicting bundle, and atomically replaces the config in its own directory
with file and directory fsync. Restore requires the live config hash to equal
the saved applied hash exactly.

An eventual privileged-free I/O adapter must implement all of these steps:

1. Require explicit interactive confirmation and a stopped `plasmashell`; the
   model never stops or starts Plasma itself.
2. `lstat` the live appletsrc: reject symlinks/non-regular files, wrong owner,
   unsafe permissions or a path outside the expected user's `.config`.
3. Read exact bytes and mode. Build the plan. Create a new, non-reused directory
   below `~/.local/state/pocketds-panel-migrations/<original-sha256>/`.
4. Atomically write `original.appletsrc`, `migrated.appletsrc`, and canonical
   `manifest.json` as mode 0600; fsync every file and the directory. Never
   overwrite an existing bundle unless every byte/hash is already identical.
5. Re-read the live file immediately before apply. Its hash must still equal
   `original.sha256`. Write the staged migrated bytes to a same-directory temp,
   preserve mode/owner, fsync, then `rename` and fsync `.config`.
6. Restore only when bundle integrity passes and the live hash exactly equals
   `migrated.sha256`. Restore the original bytes atomically. If Plasma or the
   user changed any other desktop setting after migration, return `CONFLICT` and
   require a manual three-way reconciliation—never silently overwrite it.
7. Starting plasmashell and health checks are a separate orchestrator step. A
   failed start does not change or delete the backup bundle.

The old `*.pre-pocketds-panel-v1` snapshot is historical evidence, not a safe
restore input: restoring it wholesale would discard unrelated desktop changes
made since that snapshot.

## Review tests

From repository root:

```sh
python3 tests/pds005-applet-migration.py
python3 tests/pds005-panel-geometry.py
python3 tests/pds005-standalone-contract.py
```

Qt/LayerShell runtime behavior, focus preservation, output reconnect and 50 live
show/hide cycles remain unverified. XWayland behavior is not claimed.
