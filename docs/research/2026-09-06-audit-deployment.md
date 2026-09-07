# 2026-09-06 audit repair deployment

Code commit: `ffe2b2d20df63d7630695ad3585224f013db806e` (parent `14a4cfb0ba3ef5b62b9e838e52d42fd642c6f393`).
Deployed on Pocket DS at 2026-09-06T03:18:58.753761+09:00; user authorized installation and remote push.

## Installed changes

Exactly four installed artifacts were replaced. Every preimage matched the audited base, and every installed result matches the repaired source byte-for-byte with the expected mode and owner/group. The device repository was fast-forwarded to the same code commit. Installer, rollback, policy-generation and regression changes are available there; the daily-suspend installer was not invoked.

| Artifact | Installed path | Mode / UID:GID | SHA-256 |
|---|---|---|---|
| `components/brightness/pocketds-brightness.py` | `/home/pocketds/.local/libexec/pocketds/pocketds-brightness` | `0o755 / 1000:1000` | `433714228ab1932aaeb32721b4449bc70db8c23973ac79839f9f4beec1e69524` |
| `components/gamepad-activity/pocketds-gamepad-activity.py` | `/home/pocketds/.local/libexec/pocketds/pocketds-gamepad-activity` | `0o755 / 1000:1000` | `f317b5db19d1c7485cd2ee48c766ed96df371763eb1f14c03fae8d095381b2f8` |
| `components/control-panel/plasmoid/contents/ui/main.qml` | `/home/pocketds/.local/share/plasma/plasmoids/org.pocketds.controlpanel.v3/contents/ui/main.qml` | `0o644 / 1000:1000` | `e9c70a55efab4b4cd6270daa3af394697729a6ce33114c715824e3de4b9ae73d` |
| `components/system/90-pocketds-hardware-protection.conf` | `/etc/dnf/libdnf5.conf.d/90-pocketds-hardware-protection.conf` | `0o644 / 0:0` | `b764575ccad00ea87b9d8319bd54261397e0e8b81d94c99bf5fc83d517739c13` |

## Activation and checks

| Service | Previous PID | New PID | Result |
|---|---:|---:|---|
| `pocketds-brightness.service` | 1362 | 105800 | active/running; no automatic restart |
| `pocketds-gamepad-activity.service` | 49794 | 105798 | active/running; no automatic restart |
| `plasma-plasmashell.service` | 1589 | 105892 | active/running; no automatic restart |

- Plasma was reloaded once using the existing bounded shell recovery implementation. D-Bus reports the Pocket DS Panel applet (ID 28) on screen 1; no Pocket DS Panel QML error was observed in the activation log.
- The gamepad observer attached to the managed virtual controller. Its read-only readiness check passed with zero activity notifications.
- Both outputs are enabled and connected in the existing 120/60 Hz layout. At the post-install snapshot both DPMS states are on, the lid is open, stored brightness remains 23%/49%, and hardware matches with no drift. This snapshot is not a new physical lid-cycle test.
- The focused update-protection installer and real DNF5 effective configuration check passed with all 39 ordered exclusion patterns. No package transaction was run.
- Native `scripts/check.sh` completed with all device runtime checks passing except the existing external Codex CLI missing warning. The lint phase passed and also emitted its existing binary-header/null-byte shell warning.
- The stock KDE brightness applet logged `PopupDialog.qml:96` TypeError during shell startup; the identical message exists in the pre-deployment journal. No new failure of either updated Python service was observed. The pre-existing failed Brotato app unit was unchanged.
- The prior exact-source Fedora aarch64 full `make test` run covered 1,154 unittest cases across 83 invocations, with one external battery-driver fixture skip; all executed tests passed. Native deployment checks complement that run.

## Preserved state and remaining acceptance

Boot ID `72a9286e-7eae-46cf-966a-6afc7d2102be` and running kernel `7.1.12-pdsdiag.20260905.aarch64` did not change. Installed `/boot/boot/Image`, brightness preferences and PowerDevil configuration retained their pre-install hashes and metadata. KWin, keyboard, GPU telemetry, lid helper and InputPlumber retained their PIDs and service states.

Suspend counters remain 3 successful / 0 failed. The daily-suspend opt-in remains absent and logind `CanSuspend` remains `no`. There was no reboot, OS switch, kernel installation or suspend test.

Previously confirmed ordinary close/open and RTC-return-then-open successes remain valid. Direct Hall wake from deep sleep, darkness while still closed after RTC return, Android/Linux round-trip, and physical controller-only play followed by hands-off idle remain separate acceptance items; this deployment does not claim to complete them.

## Backups and evidence

Device backup: `/home/pocketds/.local/state/pocketds-linux-kit/backups/20260906-031844-audit-ffe2b2d`.

Files `0`, `1`, `2`, `3` contain the brightness helper, gamepad observer, Panel QML and DNF configuration preimages, respectively. `deployment.json` records both generations, hashes and preserved state. The directory also contains `runtime-check.log`, `activation-journal.log` and `activation-check.json`. Restore helpers as mode 0755/user-owned, QML as 0644/user-owned, and DNF configuration as 0644/root-owned; then restart the two affected services and perform the bounded Plasma reload. The deployment wrapper would restore these preimages if installation or postchecks failed; no rollback was needed.

The focused DNF installer also saved its root-owned preimage in `/var/lib/pocketds-linux-kit/backups/20260906-031844-audit-update-protection`.

This record describes device activation of the audited code. Remote synchronization is verified separately after committing this deployment record.
