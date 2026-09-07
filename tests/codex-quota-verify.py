#!/usr/bin/env python3
"""Offline privacy and fail-closed tests for live quota cache verification."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts/pocketds-codex-quota-verify.py"
SPEC = importlib.util.spec_from_file_location("pocketds_quota_verify", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def payload() -> dict[str, object]:
    return {
        "available": True,
        "fresh": True,
        "source": "app-server",
        "synced_at": 1000,
        "source_timestamp": 1000,
        "limit_id": "codex",
        "limit_name": "Codex",
        "plan_type": "test",
        "primary_used_percent": 74.0,
        "primary_reset": 2000,
        "primary_window_minutes": 300,
        "secondary_used_percent": None,
        "secondary_reset": None,
        "secondary_window_minutes": None,
    }


assert MODULE.parse_args([]).maximum_age == 360
accepted = MODULE.validate(payload(), now=1030, maximum_age=120)
assert accepted == {
    "accepted": True,
    "source": "app-server",
    "fresh": True,
    "age_seconds": 30,
    "primary_present": True,
    "secondary_present": False,
}

edge = MODULE.validate(payload(), now=1360, maximum_age=360)
assert edge["age_seconds"] == 360
try:
    MODULE.validate(payload(), now=1361, maximum_age=360)
except MODULE.QuotaError:
    pass
else:
    raise AssertionError("quota older than the five-minute timer allowance was accepted")

for mutation in (
    {"available": False},
    {"fresh": False},
    {"source": "session-log"},
    {"synced_at": 1},
    {"primary_used_percent": None},
    {"primary_used_percent": 101},
):
    candidate = payload()
    candidate.update(mutation)
    try:
        MODULE.validate(candidate, now=1030, maximum_age=120)
    except MODULE.QuotaError:
        pass
    else:
        raise AssertionError(f"unsafe quota payload accepted: {mutation}")

with tempfile.TemporaryDirectory(prefix="quota-verify-") as name:
    path = Path(name) / "quota.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")
    path.chmod(0o600)
    assert MODULE.read_private(path) == payload()
    path.chmod(0o644)
    try:
        MODULE.read_private(path)
    except MODULE.QuotaError:
        pass
    else:
        raise AssertionError("world-readable quota cache was accepted")

print("  [OK] quota verification requires a private fresh app-server cache")
