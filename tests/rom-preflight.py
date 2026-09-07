#!/usr/bin/env python3
"""Tests for the bounded metadata-only ROM preflight."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts" / "pocketds-rom-preflight.py"
spec = importlib.util.spec_from_file_location("pocketds_rom_preflight", SOURCE)
assert spec and spec.loader
preflight = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = preflight
spec.loader.exec_module(preflight)


class MetadataScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="pocketds-rom-preflight-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_counts_candidates_without_reading_contents_or_names_in_output(self) -> None:
        (self.root / "nes").mkdir()
        (self.root / "nes" / "private-title.nes").touch()
        (self.root / "nes" / "another-title.zip").touch()
        (self.root / "nes" / "systeminfo.txt").touch()
        result = preflight.scan(self.root, {"nes", "zip"})
        self.assertEqual(result.regular_files, 3)
        self.assertEqual(result.candidate_roms, 2)
        self.assertEqual(result.candidate_extensions, {"nes": 1, "zip": 1})
        self.assertNotIn("private-title", repr(result))
        self.assertEqual(result.stopped, "complete")

    def test_symlink_loop_is_counted_but_never_followed(self) -> None:
        (self.root / "a").mkdir()
        os.symlink(self.root, self.root / "a" / "loop")
        os.symlink(self.root / "missing", self.root / "a" / "broken")
        result = preflight.scan(self.root, {"zip"})
        self.assertEqual(result.symlinks, 2)
        self.assertEqual(result.directories, 2)
        self.assertEqual(result.stopped, "complete")

    def test_symbolic_link_root_is_rejected(self) -> None:
        target = self.root / "target"
        target.mkdir()
        link = self.root / "root-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            preflight.scan(link, {"zip"})

    def test_entry_limit_fails_closed(self) -> None:
        for number in range(20):
            (self.root / f"{number}.zip").touch()
        result = preflight.scan(self.root, {"zip"}, max_entries=4)
        self.assertEqual(result.entries, 4)
        self.assertEqual(result.stopped, "entry_limit")

    def test_depth_limit_is_observable(self) -> None:
        (self.root / "one").mkdir()
        (self.root / "one" / "two").mkdir()
        result = preflight.scan(self.root, {"zip"}, max_depth=1)
        self.assertEqual(result.depth_limited_directories, 1)
        self.assertEqual(result.stopped, "depth_limit")

    def test_timeout_is_observable(self) -> None:
        for number in range(20):
            (self.root / f"{number}.zip").touch()
        result = preflight.scan(
            self.root, {"zip"}, timeout_seconds=0.008, delay_per_entry=0.003
        )
        self.assertEqual(result.stopped, "timeout")
        self.assertLess(result.entries, 20)

    def test_five_thousand_entry_fixture_completes_within_guard(self) -> None:
        for system in range(5):
            directory = self.root / f"system-{system}"
            directory.mkdir()
            for item in range(1_000):
                (directory / f"game-{item}.zip").touch()
        result = preflight.scan(self.root, {"zip"}, timeout_seconds=5)
        self.assertEqual(result.candidate_roms, 5_000)
        self.assertEqual(result.stopped, "complete")

    def test_source_has_no_regular_file_content_read_path(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for token in ("read_text(", "read_bytes(", "follow_symlinks=True"):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
