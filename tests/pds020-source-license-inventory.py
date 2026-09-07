#!/usr/bin/env python3
"""Tests for the non-inferential PDS-020 tracked-source inventory."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds020-source-license-inventory.py"
SPEC = importlib.util.spec_from_file_location("pds020_source_inventory", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class InventoryTests(unittest.TestCase):
    def test_text_binary_hash_and_spdx_headers_are_factual_only(self) -> None:
        tagged = MODULE.classify(
            PurePosixPath("tagged.py"),
            b"# SPDX-License-Identifier: MIT\nprint('ok')\n",
            0o644,
        )
        binary = MODULE.classify(PurePosixPath("asset.bin"), b"\x00private", 0o644)
        self.assertEqual(tagged["spdx_header"], "MIT")
        self.assertEqual(tagged["content_kind"], "utf8-text")
        self.assertEqual(binary["content_kind"], "binary")
        self.assertIsNone(binary["spdx_header"])
        compound = MODULE.classify(
            PurePosixPath("compound.txt"),
            b"SPDX-License-Identifier: MIT OR Apache-2.0\n",
            0o644,
        )
        self.assertEqual(compound["spdx_header"], "MIT OR Apache-2.0")

    def test_fixture_inventory_reports_missing_legal_files_without_inference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-inventory-") as temporary:
            root = Path(temporary)
            (root / "tagged.py").write_text(
                "# SPDX-License-Identifier: MIT\n", encoding="utf-8"
            )
            (root / "plain.txt").write_text("no declaration\n", encoding="utf-8")
            report = MODULE.inventory(
                root,
                [PurePosixPath("plain.txt"), PurePosixPath("tagged.py")],
                revision="a" * 40,
                dirty_change_count=0,
            )
        self.assertTrue(report["inventory_complete"])
        self.assertEqual(report["schema"], "pocketds.source-license-inventory.v2")
        self.assertFalse(report["release_ready"])
        self.assertEqual(report["legal_conclusion"], "NOT_DETERMINED")
        self.assertEqual(report["spdx_header_counts"], {"MIT": 1})
        self.assertFalse(any(report["release_evidence_files_tracked"].values()))

    def test_asset_manifest_uses_its_authoritative_component_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-inventory-") as temporary:
            root = Path(temporary)
            (root / "components/assets").mkdir(parents=True)
            (root / "ASSETS.json").write_text("{}\n", encoding="utf-8")
            manifest = root / "components/assets/ASSETS.json"
            manifest.write_text("{}\n", encoding="utf-8")
            report = MODULE.inventory(
                root,
                [
                    PurePosixPath("ASSETS.json"),
                    PurePosixPath("components/assets/ASSETS.json"),
                ],
                revision="a" * 40,
                dirty_change_count=0,
            )
        self.assertEqual(
            tuple(report["release_evidence_files_tracked"]),
            MODULE.RELEASE_EVIDENCE_FILES,
        )
        self.assertTrue(
            report["release_evidence_files_tracked"][
                "components/assets/ASSETS.json"
            ]
        )
        self.assertNotIn("ASSETS.json", report["release_evidence_files_tracked"])

    def test_tracked_reader_rejects_symlink_hardlink_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-inventory-") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("content", encoding="utf-8")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(MODULE.InventoryError):
                MODULE.read_tracked(root, PurePosixPath("link"))
            hardlink = root / "hard"
            os.link(target, hardlink)
            with self.assertRaises(MODULE.InventoryError):
                MODULE.read_tracked(root, PurePosixPath("target"))
            hardlink.unlink()
            target.unlink()
            with target.open("wb") as stream:
                stream.truncate(MODULE.MAX_FILE_BYTES + 1)
            with self.assertRaises(MODULE.InventoryError):
                MODULE.read_tracked(root, PurePosixPath("target"))

    def test_report_is_private_new_and_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-report-") as temporary:
            output = Path(temporary) / "report.json"
            MODULE.write_report({"inventory_complete": True}, str(output))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_report({"inventory_complete": False}, str(output))


class StaticTests(unittest.TestCase):
    def test_git_boundary_is_read_only_and_no_legal_claim_is_possible(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "git add",
            "git commit",
            "git checkout",
            "git clean",
            "curl",
            "wget",
            "requests.",
            "redistribution_inferred\": True",
            "release_ready\": True",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"legal_conclusion": "NOT_DETERMINED"', source)
        self.assertIn('"rights_or_redistribution_inferred": False', source)
        self.assertNotIn("root_legal_files_tracked", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
