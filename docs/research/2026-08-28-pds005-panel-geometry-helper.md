# PDS-005 Plasma applet geometry helper

Status: source and fixture PASS at authoritative base `e565373`; copied into
the device repository and live dry-run PASS, but not installed, not applied to
the live config, and not release-accepted.

## Reused safety model

The existing Applet-33 migration established three useful boundaries: locate
one exact plugin owner, transform bytes without normalizing the rest of the
KConfig file, and make apply/restore conditional on exact SHA-256 states. The
geometry helper reuses that model but intentionally does not remove the applet
and does not assume containment `2` or applet `33`.

The target size comes from fresh `display_dsi2_logical_width` and
`display_dsi2_logical_height` values in `%t/pocketds-gpu-status.json`. Physical
1024x768 pixels, scale, KWin placement and historical rounded values are not
substituted for the current logical geometry.

## Mutation boundary

For the unique `org.pocketds.controlpanel.v3` owner, the pure planner requires
exactly one active `ItemGeometries-<width>x<height>` key and one
`ItemGeometriesHorizontal` key in the matching containment. Each must contain
exactly one target Applet token. Only that token's x/y/width/height fields become
`0,0,<width>,<height>`; its suffix, every other token, inactive geometry keys,
line endings, comments and unrelated KConfig bytes remain intact.

The CLI is dry-run by default. Apply/restore refuse an active plasmashell,
unsafe config files, duplicated/missing ownership, stale telemetry and detected
read-to-commit races. A successful apply creates a private, hash-addressed
original/applied/manifest bundle and uses same-directory atomic replacement,
file fsync and directory fsync while preserving the original file mode. Restore
requires the live file to equal the recorded applied hash; post-apply desktop
changes are never overwritten.

## Fixture evidence

`make test-panel-geometry` covers dynamic dimensions, owner/key/token
ambiguity, unrelated-byte preservation, CRLF preservation, no-op planning,
active-shell refusal at both probes, symlink and unsafe-mode rejection,
concurrent changes, conflicting backup refusal, private file modes, exact
apply/restore round trips, strict restore hash and default dry-run behavior.

Live application, plasmashell stop/start orchestration, screenshot geometry,
output reconnect and repeated desktop return/re-entry remain NOT RUN.

The device dry-run consumed the live telemetry cache as 819x614, uniquely found
containment 2 / applet 33, matched both target tokens and reported
`changed_tokens=0`, `would_change=false`. No backup or state directory was
created. A separate live `--apply` attempt while plasmashell was active failed
closed with `PlasmaActive`, exit 2, and likewise created no state. The complete
device `make test` then passed.
