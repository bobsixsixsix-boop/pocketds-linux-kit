#!/usr/bin/env python3
"""Tests for the read-only PDS-005 UI deployment audit."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds005-ui-deployment-audit.py"
SPEC = importlib.util.spec_from_file_location("pds005_ui_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


class AuditTests(unittest.TestCase):
    def test_matching_fixture_and_candidate_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-") as temporary:
            root = Path(temporary)
            source = root / "source"
            live = root / "live"
            write(source, "same", 0o644)
            write(live, "same", 0o644)
            artifact = MODULE.Artifact("fixture", source, live, os.getuid(), 0o644)
            candidate = root / "candidate"
            binary = root / "system/usr/local/bin/pocketds-panelctl"
            write(candidate, "binary", 0o755)
            write(binary, "binary", 0o755)
            report = MODULE.evaluate(
                [artifact],
                candidate,
                root / "system",
                panelctl_live_uid=os.getuid(),
            )
        self.assertTrue(report["complete"])
        self.assertEqual(report["counts"]["MATCH"], 2)

    def test_drift_missing_mode_and_link_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-") as temporary:
            root = Path(temporary)
            source = root / "source"
            write(source, "source", 0o644)
            missing = MODULE.Artifact(
                "missing", source, root / "missing", os.getuid(), 0o644
            )
            drift_path = root / "drift"
            write(drift_path, "live", 0o644)
            drift = MODULE.Artifact("drift", source, drift_path, os.getuid(), 0o644)
            wrong_mode_path = root / "mode"
            write(wrong_mode_path, "source", 0o600)
            wrong_mode = MODULE.Artifact(
                "mode", source, wrong_mode_path, os.getuid(), 0o644
            )
            link = root / "link"
            link.symlink_to(drift_path)
            linked = MODULE.Artifact("link", source, link, os.getuid(), 0o644)
            states = {
                item.artifact_id: MODULE.audit_artifact(item)["state"]
                for item in (missing, drift, wrong_mode, linked)
            }
        self.assertEqual(states["missing"], "MISSING")
        self.assertEqual(states["drift"], "DRIFT")
        self.assertEqual(states["mode"], "UNSAFE")
        self.assertEqual(states["link"], "UNSAFE")

    def test_plan_binds_deep_suspend_guard_polkit_then_dispatcher(self) -> None:
        repo = Path("/fixture/repo")
        home = Path("/fixture/home")
        system = Path("/fixture/system")
        artifacts = MODULE.artifact_plan(repo, home, system)
        by_id = {item.artifact_id: item for item in artifacts}
        self.assertEqual(len(artifacts), 15)
        self.assertEqual(
            [item.artifact_id for item in artifacts[:3]],
            ["panel-controller-diagram", "panel-controller-session", "panel-qml"],
        )
        self.assertEqual(
            [
                item.artifact_id
                for item in artifacts
                if item.artifact_id
                in {
                    "deep-suspend-guard",
                    "deep-suspend-polkit",
                    "panel-root-helper",
                }
            ],
            ["deep-suspend-guard", "deep-suspend-polkit", "panel-root-helper"],
        )
        self.assertEqual(
            by_id["deep-suspend-guard"].source,
            repo / "components/system/pocketds-deep-suspend.py",
        )
        self.assertEqual(
            by_id["deep-suspend-guard"].live,
            system / "usr/local/libexec/pocketds-deep-suspend",
        )
        self.assertEqual(by_id["deep-suspend-guard"].live_mode, 0o755)
        self.assertEqual(
            by_id["deep-suspend-polkit"].live,
            system / "etc/polkit-1/rules.d/90-pocketds-deep-suspend.rules",
        )
        self.assertEqual(by_id["deep-suspend-polkit"].live_mode, 0o644)

    def test_no_candidate_is_explicitly_incomplete(self) -> None:
        report = MODULE.evaluate([], None, Path("/"))
        self.assertFalse(report["complete"])
        self.assertEqual(report["artifacts"][0]["state"], "NOT_EVALUATED")
        self.assertEqual(report["artifacts"][0]["source_binding"], "EXTERNAL_BUILD_REQUIRED")

    def test_report_contains_ids_and_hashes_but_no_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-") as temporary:
            root = Path(temporary)
            source = root / "PRIVATE-SOURCE"
            live = root / "PRIVATE-LIVE"
            write(source, "same", 0o644)
            write(live, "same", 0o644)
            report = MODULE.evaluate(
                [MODULE.Artifact("public-id", source, live, os.getuid(), 0o644)],
                None,
                Path("/"),
            )
        serialized = json.dumps(report)
        self.assertIn("public-id", serialized)
        self.assertNotIn("PRIVATE-SOURCE", serialized)
        self.assertNotIn("PRIVATE-LIVE", serialized)


class FileAndStaticTests(unittest.TestCase):
    def test_report_is_private_new_and_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-report-") as temporary:
            output = Path(temporary) / "report.json"
            MODULE.write_report({"complete": False}, str(output))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_report({"complete": True}, str(output))

    def test_auditor_never_builds_installs_or_restarts(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "os.system",
            "sudo",
            " install ",
            "systemctl",
            "plasmashell",
            "c++",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
