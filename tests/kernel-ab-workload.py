#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "workload" / "preflight.py"
SPEC = importlib.util.spec_from_file_location("pocketds_kernel_ab_workload_test", SOURCE)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest, self.manifest_sha256 = preflight.load_manifest()
        self.manifest = copy.deepcopy(self.manifest)
        self.files = {
            "wrapper": b"#!/bin/sh\nexec chromium \"$@\"\n",
            "binary": b"synthetic fixed Chromium binary\n",
        }
        paths = preflight.EXPECTED_BROWSER_PATHS
        for name, content in self.files.items():
            path = root / paths[name].lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(0o555)
            self.manifest["browser"][name] = {
                "path": paths[name],
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }


class WorkloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_locked_offline_webgl_workload_and_browser_pass_without_execution(self) -> None:
        report = preflight.collect(
            self.fixture.manifest,
            self.fixture.manifest_sha256,
            self.fixture.root,
        )
        self.assertTrue(report["gates"]["formal_run_ready"])
        self.assertFalse(report["gates"]["workload_execution_started"])
        self.assertFalse(report["gates"]["candidate_install_authorized"])
        self.assertFalse(report["workload"]["profile_specific_content"])

    def test_browser_drift_link_and_writable_file_are_rejected(self) -> None:
        binary = self.fixture.root / self.fixture.manifest["browser"]["binary"]["path"].lstrip("/")
        binary.chmod(0o644)
        binary.write_bytes(b"drift")
        binary.chmod(0o555)
        with self.assertRaisesRegex(preflight.PreflightError, "identity differs"):
            preflight.collect(self.fixture.manifest, self.fixture.manifest_sha256, self.fixture.root)
        binary.chmod(0o644)
        binary.write_bytes(self.fixture.files["binary"])
        binary.chmod(0o666)
        with self.assertRaisesRegex(preflight.PreflightError, "metadata is unsafe"):
            preflight.collect(self.fixture.manifest, self.fixture.manifest_sha256, self.fixture.root)
        binary.unlink()
        binary.symlink_to("missing")
        with self.assertRaisesRegex(preflight.PreflightError, "unavailable or linked"):
            preflight.collect(self.fixture.manifest, self.fixture.manifest_sha256, self.fixture.root)

    def test_asset_identity_and_semantics_are_bound(self) -> None:
        changed = copy.deepcopy(self.fixture.manifest)
        changed["asset"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(preflight.PreflightError, "asset identity differs"):
            preflight.collect(changed, self.fixture.manifest_sha256, self.fixture.root)
        content = (ROOT / "tools/kernel-ab/workload/webgl-compositor.html").read_bytes()
        self.assertIn(b'getContext("webgl2"', content)
        self.assertIn(b"requestAnimationFrame", content)
        self.assertNotIn(b"http://", content)
        self.assertNotIn(b"https://", content)

    def test_manifest_is_strict_and_cli_has_no_execution_or_override_path(self) -> None:
        manifest, digest = preflight.load_manifest()
        self.assertEqual(manifest["duration_seconds"], 2700)
        self.assertEqual(len(digest), 64)
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            path = Path(temporary) / "manifest.json"
            raw = json.dumps(manifest)[:-1] + ',"unknown":true}'
            path.write_text(raw, encoding="utf-8")
            path.chmod(0o444)
            with self.assertRaisesRegex(preflight.PreflightError, "fields differ"):
                preflight.load_manifest(path)
        completed = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--help"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        for forbidden in ("--execute", "--root", "--manifest", "--device", "--reboot", "--install"):
            self.assertNotIn(forbidden, completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
