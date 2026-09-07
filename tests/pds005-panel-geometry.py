#!/usr/bin/env python3
"""Pure/temporary-file fixtures for the fail-closed PDS-005 geometry helper."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "experiments/pds005-panel-shell/migration"
sys.path.insert(0, str(MODULE))

from panel_geometry import (  # noqa: E402
    BundleConflict,
    ConcurrentChange,
    DisplayGeometry,
    GeometryError,
    GeometryTargetAmbiguous,
    PlasmaActive,
    UnsafeConfig,
    apply_geometry,
    dimensions_from_telemetry,
    load_bundle,
    locate_owner,
    plan_geometry,
    restore_geometry,
)


GEOMETRY = DisplayGeometry(777, 555, 1_000_000)
FIXTURE = b"""# preserve-leading-comment\r
[Containments][91]\r
ItemGeometries-777x555=Applet-2:1,2,3,4,0;Applet-7:9,8,700,500,0;Applet-70:5,6,7,8,0;\r
ItemGeometriesHorizontal=Applet-7:-1,2,3,4,0;Applet-2:4,3,2,1,0;\r
ItemGeometries-900x600=Applet-7:20,21,22,23,0;\r
activityId=preserve-me\r
plugin=org.kde.plasma.folder\r
\r
[Containments][91][Applets][7]\r
immutability=1\r
plugin=org.pocketds.controlpanel.v3\r
\r
[Containments][91][Applets][7][Configuration][General]\r
private=preserve-this-too\r
\r
[Containments][92]\r
ItemGeometries-777x555=Applet-7:30,31,32,33,0;\r
\r
[Containments][92][Applets][70]\r
plugin=org.example.keep\r
"""

EXPECTED = FIXTURE.replace(
    b"Applet-7:9,8,700,500,0", b"Applet-7:0,0,777,555,0", 1
).replace(
    b"Applet-7:-1,2,3,4,0", b"Applet-7:0,0,777,555,0", 1
)


def telemetry(
    *, now: int = 1_000_500, width: int = 777, height: int = 555,
    status: str = "ok", enabled: bool = True, sample: int = 1_000_000,
) -> tuple[bytes, int]:
    payload = {
        "display_status": status,
        "display_sample_unix_ms": sample,
        "display_dsi2_enabled": enabled,
        "display_dsi2_logical_width": width,
        "display_dsi2_logical_height": height,
    }
    return json.dumps(payload).encode("utf-8"), now


def write_config(path: Path, content: bytes = FIXTURE, mode: int = 0o600) -> None:
    path.write_bytes(content)
    path.chmod(mode)


class PureTransformTests(unittest.TestCase):
    def test_dimensions_are_dynamic_and_require_fresh_ok_dsi2(self):
        content, now = telemetry(width=913, height=601)
        self.assertEqual(
            dimensions_from_telemetry(content, now_unix_ms=now),
            DisplayGeometry(913, 601, 1_000_000),
        )
        for bad, bad_now in (
            telemetry(status="partial"),
            telemetry(enabled=False),
            telemetry(sample=1, now=1_000_500),
            telemetry(sample=1_010_000, now=1_000_500),
        ):
            with self.assertRaises(GeometryError):
                dimensions_from_telemetry(bad, now_unix_ms=bad_now)

    def test_only_two_target_tokens_change(self):
        plan = plan_geometry(FIXTURE, GEOMETRY)
        self.assertEqual(plan.owner.containment_id, "91")
        self.assertEqual(plan.owner.applet_id, "7")
        self.assertEqual(plan.matched_tokens, 2)
        self.assertEqual(plan.changed_tokens, 2)
        self.assertEqual(plan.applied, EXPECTED)
        self.assertIn(b"Applet-70:5,6,7,8,0", plan.applied)
        self.assertIn(b"ItemGeometries-900x600=Applet-7:20,21,22,23,0", plan.applied)
        self.assertIn(b"[Containments][92]\r\nItemGeometries-777x555=Applet-7:30", plan.applied)
        self.assertEqual(plan.applied.count(b"\r\n"), FIXTURE.count(b"\r\n"))

    def test_already_exact_is_a_noop(self):
        plan = plan_geometry(EXPECTED, GEOMETRY)
        self.assertEqual(plan.original, plan.applied)
        self.assertEqual(plan.matched_tokens, 2)
        self.assertEqual(plan.changed_tokens, 0)

    def test_missing_duplicate_or_unexpected_owner_fails_closed(self):
        absent = FIXTURE.replace(
            b"plugin=org.pocketds.controlpanel.v3", b"plugin=org.example.absent"
        )
        with self.assertRaises(GeometryError):
            locate_owner(absent)
        duplicate = FIXTURE + (
            b"\r\n[Containments][93][Applets][9]\r\n"
            b"plugin=org.pocketds.controlpanel.v3\r\n"
        )
        with self.assertRaises(GeometryTargetAmbiguous):
            plan_geometry(duplicate, GEOMETRY)
        duplicate_key = FIXTURE.replace(
            b"ItemGeometriesHorizontal=",
            b"ItemGeometriesHorizontal=Applet-7:1,1,1,1,0;\r\n"
            b"ItemGeometriesHorizontal=",
            1,
        )
        with self.assertRaises(GeometryTargetAmbiguous):
            plan_geometry(duplicate_key, GEOMETRY)

    def test_duplicate_or_missing_target_token_fails_closed(self):
        duplicate = FIXTURE.replace(
            b"Applet-7:9,8,700,500,0;",
            b"Applet-7:9,8,700,500,0;Applet-7:1,2,3,4,0;",
            1,
        )
        with self.assertRaises(GeometryTargetAmbiguous):
            plan_geometry(duplicate, GEOMETRY)
        missing = FIXTURE.replace(b"Applet-7:-1,2,3,4,0;", b"", 1)
        with self.assertRaises(GeometryTargetAmbiguous):
            plan_geometry(missing, GEOMETRY)


class SafeIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pds005-geometry-")
        self.root = Path(self.temporary.name)
        self.config = self.root / "plasma-org.kde.plasma.desktop-appletsrc"
        self.state = self.root / "state/pocketds-panel-geometry"
        write_config(self.config)

    def tearDown(self):
        self.temporary.cleanup()

    def test_roundtrip_is_byte_exact_private_and_mode_preserving(self):
        plan, bundle = apply_geometry(
            self.config, self.state, GEOMETRY, plasma_probe=lambda: False
        )
        self.assertIsNotNone(bundle)
        self.assertEqual(self.config.read_bytes(), EXPECTED)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        assert bundle is not None
        self.assertEqual(bundle.directory.parent, self.state)
        self.assertEqual(bundle.directory.name, plan.original_sha256)
        for name in ("original.appletsrc", "applied.appletsrc", "manifest.json"):
            path = bundle.directory / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        loaded = load_bundle(self.state, plan.original_sha256)
        self.assertEqual(loaded.original, FIXTURE)
        self.assertEqual(loaded.applied, EXPECTED)
        restore_geometry(
            self.config,
            self.state,
            plan.original_sha256,
            plasma_probe=lambda: False,
        )
        self.assertEqual(self.config.read_bytes(), FIXTURE)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)

    def test_active_shell_refuses_before_backup_or_write(self):
        with self.assertRaises(PlasmaActive):
            apply_geometry(
                self.config, self.state, GEOMETRY, plasma_probe=lambda: True
            )
        self.assertEqual(self.config.read_bytes(), FIXTURE)
        self.assertFalse(self.state.exists())

    def test_shell_becoming_active_before_commit_refuses_write(self):
        states = iter((False, True))
        with self.assertRaises(PlasmaActive):
            apply_geometry(
                self.config,
                self.state,
                GEOMETRY,
                plasma_probe=lambda: next(states),
            )
        self.assertEqual(self.config.read_bytes(), FIXTURE)

    def test_concurrent_config_race_is_detected_without_overwrite(self):
        raced = FIXTURE + b"# concurrent user edit\r\n"

        def change(path: Path) -> None:
            path.write_bytes(raced)

        with self.assertRaises(ConcurrentChange):
            apply_geometry(
                self.config,
                self.state,
                GEOMETRY,
                plasma_probe=lambda: False,
                precommit_hook=change,
            )
        self.assertEqual(self.config.read_bytes(), raced)

    def test_symlink_and_unsafe_permissions_are_rejected(self):
        link = self.root / "linked-appletsrc"
        link.symlink_to(self.config)
        with self.assertRaises(UnsafeConfig):
            apply_geometry(link, self.state, GEOMETRY, plasma_probe=lambda: False)
        self.config.chmod(0o622)
        with self.assertRaises(UnsafeConfig):
            apply_geometry(
                self.config, self.state, GEOMETRY, plasma_probe=lambda: False
            )

    def test_existing_conflicting_bundle_is_never_overwritten(self):
        plan, bundle = apply_geometry(
            self.config, self.state, GEOMETRY, plasma_probe=lambda: False
        )
        assert bundle is not None
        restore_geometry(
            self.config,
            self.state,
            plan.original_sha256,
            plasma_probe=lambda: False,
        )
        conflict = bundle.directory / "applied.appletsrc"
        conflict.write_bytes(b"conflict")
        conflict.chmod(0o600)
        with self.assertRaises(BundleConflict):
            apply_geometry(
                self.config, self.state, GEOMETRY, plasma_probe=lambda: False
            )
        self.assertEqual(conflict.read_bytes(), b"conflict")
        self.assertEqual(self.config.read_bytes(), FIXTURE)

    def test_restore_requires_exact_applied_hash(self):
        plan, _bundle = apply_geometry(
            self.config, self.state, GEOMETRY, plasma_probe=lambda: False
        )
        changed = EXPECTED + b"# desktop changed after apply\r\n"
        self.config.write_bytes(changed)
        with self.assertRaises(ConcurrentChange):
            restore_geometry(
                self.config,
                self.state,
                plan.original_sha256,
                plasma_probe=lambda: False,
            )
        self.assertEqual(self.config.read_bytes(), changed)

    def test_default_cli_is_dry_run_and_creates_no_state(self):
        cache = self.root / "telemetry.json"
        now = int(time.time() * 1000)
        payload, _ = telemetry(now=now, sample=now)
        cache.write_bytes(payload)
        result = subprocess.run(
            [
                sys.executable,
                str(MODULE / "panel_geometry.py"),
                "--config",
                str(self.config),
                "--telemetry",
                str(cache),
                "--state-root",
                str(self.state),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        output = json.loads(result.stdout)
        self.assertEqual(output["action"], "dry-run")
        self.assertTrue(output["would_change"])
        self.assertEqual(self.config.read_bytes(), FIXTURE)
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
