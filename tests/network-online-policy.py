#!/usr/bin/env python3
"""Static checks for the Pocket DS non-blocking NetworkManager boot policy."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
obsolete = ROOT / "components/system/10-pocketds-fast-online.conf"
if obsolete.exists():
    raise SystemExit("[FAIL] timeout-based wait-online override still exists")

installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
importer = (ROOT / "scripts/import-live.sh").read_text(encoding="utf-8")
if "enable NetworkManager-wait-online.service" not in installer:
    raise SystemExit("[FAIL] installer does not restore the vendor wait-online unit")
if "Refusing to remove an unrecognized wait-online drop-in" not in installer:
    raise SystemExit("[FAIL] installer can delete an unrecognized user drop-in")
if "start NetworkManager-wait-online.service" not in installer:
    raise SystemExit("[FAIL] installer does not validate the restored vendor unit")
if "reset-failed NetworkManager-wait-online.service" not in installer:
    raise SystemExit("[FAIL] installer does not clear the historical failure")
if "10-pocketds-fast-online.conf" in importer:
    raise SystemExit("[FAIL] live import can resurrect the obsolete override")

managed_units = list((ROOT / "components").rglob("*.service"))
managed_units += list((ROOT / "components").rglob("*.timer"))
for unit in managed_units:
    text = unit.read_text(encoding="utf-8", errors="replace")
    if "network-online.target" in text:
        raise SystemExit(f"[FAIL] managed unit has unreviewed network-online dependency: {unit}")

print("  [OK] vendor wait-online semantics are restored without a forced timeout")
