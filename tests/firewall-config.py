#!/usr/bin/env python3
"""Validate the non-secret Pocket DS firewalld zone policy."""

from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
ZONE = ROOT / "components/network/pocketds-home.xml"

tree = ET.parse(ZONE)
root = tree.getroot()
if root.tag != "zone":
    raise SystemExit("[FAIL] pocketds-home root element is not zone")

services = {node.attrib.get("name") for node in root.findall("service")}
required = {"ssh", "mdns", "dhcpv6-client", "kdeconnect"}
if services != required:
    raise SystemExit(f"[FAIL] unexpected trusted-zone services: {sorted(services)}")
if services & {"samba-client", "samba", "cockpit"}:
    raise SystemExit("[FAIL] trusted zone reintroduces unsupported or broad services")

installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
checks = (ROOT / "scripts/check.sh").read_text(encoding="utf-8")
if "--set-default-zone=public" not in installer:
    raise SystemExit("[FAIL] installer does not make public the safe default")
if "--get-default-zone" not in installer:
    raise SystemExit("[FAIL] installer does not make the default-zone update idempotent")
if "pocketds-home.xml" not in installer:
    raise SystemExit("[FAIL] installer does not manage the trusted zone")
if "FedoraWorkstation" in installer:
    raise SystemExit("[FAIL] installer activates the incompatible stock zone")
if "--zone=public --query-service=kdeconnect" not in checks:
    raise SystemExit("[FAIL] runtime check does not enforce public-zone isolation")

print("  [OK] trusted zone is minimal and unknown networks remain public")
