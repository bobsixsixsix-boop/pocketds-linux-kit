#!/usr/bin/env python3
"""Tests for the privacy-minimal ES-DE log summary."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds012-esde-log-summary.py"
SPEC = importlib.util.spec_from_file_location("pds012_esde_log_summary", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SummaryTests(unittest.TestCase):
    def test_summary_has_only_aggregate_evidence(self):
        canary = (
            'INFO Loading system "PRIVATE-SYSTEM" from '
            '"/home/private/ROMs/PRIVATE-GAME.rom"\n'
            "DEBUG Parsing gamelist for PRIVATE-GAME\n"
            "INFO Application startup time: 1624 ms\n"
            "INFO ES-DE cleanly shutting down\n"
        )
        report = MODULE.summarize(canary, len(canary.encode("utf-8")))
        serialized = json.dumps(report)
        for secret in ("PRIVATE-SYSTEM", "PRIVATE-GAME", "/home/private"):
            self.assertNotIn(secret, serialized)
        self.assertTrue(report["complete_session_observed"])
        self.assertFalse(report["summary_has_errors"])
        self.assertEqual(report["startup"]["latest_ms"], 1624)
        self.assertEqual(report["event_counts"]["gamelist_events"], 1)

    def test_errors_and_parse_failures_are_counts_not_lines(self):
        canary = (
            'ERROR Couldn\'t parse "/home/private/PRIVATE-GAME.xml"\n'
            "FATAL PRIVATE-CRASH\n"
        )
        report = MODULE.summarize(canary, len(canary.encode("utf-8")))
        self.assertTrue(report["summary_has_errors"])
        self.assertEqual(report["level_counts"]["error"], 1)
        self.assertEqual(report["level_counts"]["fatal"], 1)
        self.assertEqual(report["event_counts"]["parse_failures"], 1)
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_log_reader_rejects_links_non_utf8_and_oversize(self):
        with tempfile.TemporaryDirectory(prefix="pds012-log-") as temporary:
            root = Path(temporary)
            target = root / "target.log"
            target.write_text("INFO clean\n", encoding="utf-8")
            link = root / "link.log"
            link.symlink_to(target)
            with self.assertRaises(RuntimeError):
                MODULE.read_log(link)

            hardlink = root / "hardlink.log"
            os.link(target, hardlink)
            with self.assertRaises(RuntimeError):
                MODULE.read_log(target)

            invalid = root / "invalid.log"
            invalid.write_bytes(b"\xff")
            with self.assertRaises(RuntimeError):
                MODULE.read_log(invalid)

            oversized = root / "oversized.log"
            with oversized.open("wb") as stream:
                stream.truncate(MODULE.MAX_LOG_BYTES + 1)
            with self.assertRaises(RuntimeError):
                MODULE.read_log(oversized)

    def test_report_is_private_new_and_never_overwritten(self):
        report = MODULE.summarize("", 0)
        with tempfile.TemporaryDirectory(prefix="pds012-summary-") as temporary:
            output = Path(temporary) / "summary.json"
            MODULE.write_report(report, str(output))
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
            with self.assertRaises(FileExistsError):
                MODULE.write_report(report, str(output))

    def test_default_log_follows_only_the_exact_shared_root(self):
        with tempfile.TemporaryDirectory(prefix="pds012-shared-log-") as temporary:
            root = Path(temporary)
            config = root / "shared-library-root"
            config.write_text(str(root) + "\n", encoding="utf-8")
            self.assertEqual(
                MODULE.default_log_path(config, root),
                root / "PocketDS/Frontends/ES-DE/logs/es_log.txt",
            )
            config.write_text(str(root) + "\nextra\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                MODULE.default_log_path(config, root)

    def test_default_log_rejects_a_symlinked_shared_root_config(self):
        with tempfile.TemporaryDirectory(prefix="pds012-shared-log-link-") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text(str(root) + "\n", encoding="utf-8")
            config = root / "shared-library-root"
            config.symlink_to(target)
            with self.assertRaises(RuntimeError):
                MODULE.default_log_path(config, root)

    def test_source_has_no_raw_line_or_path_output(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("print(line", source)
        self.assertNotIn("repr(line", source)
        self.assertIn('"raw_lines_emitted": False', source)
        self.assertIn('"rom_names_emitted": False', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
