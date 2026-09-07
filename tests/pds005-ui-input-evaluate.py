#!/usr/bin/env python3
"""Tests for the source-bound supervised Pocket DS UI/input evaluator."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds005-ui-input-evaluate.py"
SPEC = importlib.util.spec_from_file_location("pds005_ui_input", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

REVISION = "a" * 40
BINDING = {
    "repo_revision": REVISION,
    "ui_manifest_sha256": "b" * 64,
    "ui_payload_bundle_sha256": "c" * 64,
    "joymouse_sha256": "d" * 64,
}


def complete_evidence(binding: dict[str, str] | None = None) -> dict[str, object]:
    evidence = MODULE.empty_evidence(BINDING.copy() if binding is None else binding.copy())
    for case_id, (minimum, _extras) in MODULE.CASE_POLICY.items():
        record = evidence["cases"][case_id]
        record.update(
            {
                "attempts": minimum,
                "passes": minimum,
                "observer_confirmed": True,
            }
        )
    evidence["cases"]["panel-lower-screen-geometry"].update(
        lower_screen_only=True,
        all_edges_flush=True,
        touch_regions_aligned=True,
    )
    evidence["cases"]["panel-desktop-roundtrip"].update(
        desktop_reachable=True,
        panel_reentry_available=True,
    )
    evidence["cases"]["panel-fullscreen-roundtrip"].update(
        active_window_only=True,
        fullscreen_state_reversible=True,
    )
    evidence["cases"]["panel-recovery-graceful"].update(
        plasma_restart_delta=5,
        kwin_restart_delta=0,
        panel_returned=True,
        bounded_recovery=True,
    )
    evidence["cases"]["panel-recovery-signal-fallback"].update(
        sigterm_attempts=3,
        sigkill_attempts=3,
        plasma_restart_delta=6,
        kwin_restart_delta=0,
        panel_returned=True,
        bounded_recovery=True,
    )
    evidence["cases"]["keyboard-cross-app"].update(
        application_class_count=3,
        maximum_show_ms=600,
        maximum_hide_ms=700,
        text_commit_failures=0,
        visibility_stuck_count=0,
        lower_screen_only=True,
    )
    evidence["cases"]["keyboard-isolated-xwayland-failure"].update(
        isolated_server_kill_count=3,
        host_x11_disruption_count=0,
        client_failure_exit_count=3,
        bounded_restart_count=3,
        visibility_stuck_count=0,
    )
    evidence["cases"]["keyboard-touch-layout"].update(
        miskey_count=0,
        visual_hitbox_mismatch_count=0,
        minimum_touch_target_px=48,
        lower_screen_only=True,
    )
    evidence["cases"]["joymouse-clicks"].update(
        rb_attempts=50,
        rt_attempts=50,
        rb_failures=0,
        rt_failures=0,
        wrong_button_count=0,
        stuck_button_count=0,
    )
    evidence["cases"]["joymouse-scroll"].update(
        lb_attempts=50,
        lt_attempts=50,
        lb_failures=0,
        lt_failures=0,
        wrong_direction_count=0,
        stuck_scroll_count=0,
    )
    evidence["cases"]["input-mode-cycle"].update(
        duplicate_input_count=0,
        mode_mismatch_count=0,
        panel_help_matched_all_states=True,
        final_mode="joymouse",
    )
    return evidence


class DeploymentFixture:
    def __init__(self, root: Path) -> None:
        self.repo = root / "repo"
        self.home = root / "home"
        self.system = root / "system"
        self.transaction = root / "transaction"
        for directory in (
            self.repo,
            self.home,
            self.system,
            self.transaction,
            self.transaction / "payload",
            self.transaction / "backup",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        for directory in (
            self.transaction,
            self.transaction / "payload",
            self.transaction / "backup",
        ):
            directory.chmod(0o700)

        records = []
        for artifact_id, source_relative, live_relative, mode, privileged in MODULE.ARTIFACTS:
            missing_preimage = artifact_id in {
                "deep-suspend-guard",
                "deep-suspend-polkit",
            }
            content = f"candidate:{artifact_id}\n".encode()
            before = f"preimage:{artifact_id}\n".encode()
            source = self.repo / source_relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(content)
            source.chmod(mode)
            live_root = self.system if privileged else self.home
            live = live_root / live_relative
            live.parent.mkdir(parents=True, exist_ok=True)
            live.write_bytes(content)
            live.chmod(mode)
            payload = self.transaction / "payload" / artifact_id
            payload.write_bytes(content)
            payload.chmod(0o600)
            if not missing_preimage:
                backup = self.transaction / "backup" / artifact_id
                backup.write_bytes(before)
                backup.chmod(0o600)
            records.append(
                {
                    "artifact_id": artifact_id,
                    "privileged": privileged,
                    "target_mode": f"{mode:04o}",
                    "source_sha256": hashlib.sha256(content).hexdigest(),
                    "source_bytes": len(content),
                    "source_mode": f"{mode:04o}",
                    "before_state": "MISSING" if missing_preimage else "PRESENT",
                    "before_sha256": None if missing_preimage else hashlib.sha256(before).hexdigest(),
                    "before_bytes": None if missing_preimage else len(before),
                    "before_mode": None if missing_preimage else f"{mode:04o}",
                }
            )

        joy = b"fixture joymouse profile\n"
        joy_source = self.repo / MODULE.JOY_SOURCE
        joy_source.parent.mkdir(parents=True, exist_ok=True)
        joy_source.write_bytes(joy)
        joy_source.chmod(0o644)
        joy_live = self.system / MODULE.JOY_LIVE
        joy_live.parent.mkdir(parents=True, exist_ok=True)
        joy_live.write_bytes(joy)
        joy_live.chmod(0o644)

        manifest = {
            "schema": MODULE.TRANSACTION_SCHEMA,
            "repository_revision": REVISION,
            "artifact_count": len(records),
            "artifacts": records,
            "activation_deferred": True,
            "services_restarted": False,
            "absolute_paths_recorded": False,
        }
        manifest_content = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        manifest_path = self.transaction / "manifest.json"
        manifest_path.write_bytes(manifest_content)
        manifest_path.chmod(0o600)
        marker = {
            "schema": MODULE.MARKER_SCHEMA,
            "state": "APPLIED",
            "repository_revision": REVISION,
            "manifest_sha256": hashlib.sha256(manifest_content).hexdigest(),
            "activation_deferred": True,
            "services_restarted": False,
        }
        marker_path = self.transaction / "applied.json"
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        marker_path.chmod(0o600)

    def expected(self) -> dict[str, str]:
        with mock.patch.object(MODULE, "repository_revision", return_value=REVISION):
            return MODULE.expected_binding(
                self.repo,
                self.transaction,
                self.home,
                self.system,
                root_uid=os.getuid(),
            )


class EvaluationTests(unittest.TestCase):
    def test_complete_matrix_passes_without_private_or_free_text(self) -> None:
        binding, cases = MODULE.parse_evidence(complete_evidence())
        report = MODULE.evaluate(binding, cases, BINDING)
        self.assertTrue(report["complete"])
        self.assertEqual(report["passed_case_count"], len(MODULE.CASE_POLICY))
        serialized = json.dumps(report)
        for private in ("/home/private", "org.secret.App", "SERIAL-PRIVATE", "2026-08-28"):
            self.assertNotIn(private, serialized)

    def test_missing_case_is_incomplete_and_never_fabricated(self) -> None:
        raw = complete_evidence()
        del raw["cases"]["keyboard-isolated-xwayland-failure"]
        binding, cases = MODULE.parse_evidence(raw)
        report = MODULE.evaluate(binding, cases, BINDING)
        self.assertFalse(report["complete"])
        missing = next(
            item
            for item in report["cases"]
            if item["case_id"] == "keyboard-isolated-xwayland-failure"
        )
        self.assertFalse(missing["present"])
        self.assertEqual(missing["attempts"], 0)

    def test_physical_faults_thresholds_and_final_mode_fail_exact_gates(self) -> None:
        raw = complete_evidence()
        raw["cases"]["joymouse-clicks"]["wrong_button_count"] = 1
        raw["cases"]["keyboard-cross-app"]["maximum_show_ms"] = 901
        raw["cases"]["input-mode-cycle"]["final_mode"] = "gamepad"
        binding, cases = MODULE.parse_evidence(raw)
        report = MODULE.evaluate(binding, cases, BINDING)
        by_id = {item["case_id"]: item for item in report["cases"]}
        self.assertFalse(by_id["joymouse-clicks"]["gates"]["no_wrong_button"])
        self.assertFalse(by_id["keyboard-cross-app"]["gates"]["show_within_900_ms"])
        self.assertFalse(by_id["input-mode-cycle"]["gates"]["final_mode_is_joymouse"])
        self.assertFalse(report["complete"])

    def test_binding_mismatch_never_completes(self) -> None:
        binding, cases = MODULE.parse_evidence(complete_evidence())
        expected = {**BINDING, "joymouse_sha256": "e" * 64}
        report = MODULE.evaluate(binding, cases, expected)
        self.assertFalse(report["binding_matches_current_deployment"])
        self.assertFalse(report["complete"])

    def test_unknown_fields_wrong_types_bad_counts_and_modes_are_rejected(self) -> None:
        raw = complete_evidence()
        raw["cases"]["invented"] = {}
        with self.assertRaises(MODULE.InteractionError):
            MODULE.parse_evidence(raw)
        raw = complete_evidence()
        raw["cases"]["joymouse-clicks"]["attempts"] = True
        with self.assertRaises(MODULE.InteractionError):
            MODULE.parse_evidence(raw)
        raw = complete_evidence()
        raw["cases"]["joymouse-scroll"]["passes"] = 99
        with self.assertRaises(MODULE.InteractionError):
            MODULE.parse_evidence(raw)
        raw = complete_evidence()
        raw["cases"]["input-mode-cycle"]["final_mode"] = "mystery"
        with self.assertRaises(MODULE.InteractionError):
            MODULE.parse_evidence(raw)

    def test_empty_template_is_strictly_parseable_but_incomplete(self) -> None:
        raw = MODULE.empty_evidence(BINDING)
        binding, cases = MODULE.parse_evidence(raw)
        report = MODULE.evaluate(binding, cases, BINDING)
        self.assertEqual(set(cases), set(MODULE.CASE_POLICY))
        self.assertFalse(report["complete"])
        self.assertEqual(report["passed_case_count"], 0)


class BindingTests(unittest.TestCase):
    def test_exact_applied_source_payload_live_and_rollback_binding_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-input-") as temporary:
            fixture = DeploymentFixture(Path(temporary))
            binding = fixture.expected()
            self.assertEqual(binding["repo_revision"], REVISION)
            for field in MODULE.BINDING_FIELDS - {"repo_revision"}:
                self.assertRegex(binding[field], r"^[0-9a-f]{64}$")

    def test_exact_ten_artifacts_bind_components_and_privileged_dispatcher(self) -> None:
        self.assertEqual(len(MODULE.ARTIFACTS), 10)
        self.assertEqual(
            [artifact[0] for artifact in MODULE.ARTIFACTS[:3]],
            ["panel-controller-diagram", "panel-controller-session", "panel-qml"],
        )
        privileged = [artifact for artifact in MODULE.ARTIFACTS if artifact[4]]
        self.assertEqual(
            [artifact[0] for artifact in privileged],
            ["deep-suspend-guard", "deep-suspend-polkit", "panel-root-helper"],
        )
        self.assertEqual(
            privileged[0][1:4],
            (
                "components/system/pocketds-deep-suspend.py",
                "usr/local/libexec/pocketds-deep-suspend",
                0o755,
            ),
        )
        self.assertEqual(
            privileged[1][1:4],
            (
                "components/system/90-pocketds-deep-suspend.rules",
                "etc/polkit-1/rules.d/90-pocketds-deep-suspend.rules",
                0o644,
            ),
        )

        with tempfile.TemporaryDirectory(prefix="pds005-ui-input-") as temporary:
            fixture = DeploymentFixture(Path(temporary))
            fixture.expected()

    def test_live_payload_backup_joymouse_and_rollback_drift_fail_closed(self) -> None:
        mutations = ("live", "payload", "backup", "joymouse", "rolled-back")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory(prefix="pds005-ui-input-") as temporary:
                    fixture = DeploymentFixture(Path(temporary))
                    if mutation == "live":
                        (fixture.home / MODULE.ARTIFACTS[0][2]).write_text("drift", encoding="utf-8")
                    elif mutation == "payload":
                        (fixture.transaction / "payload" / MODULE.ARTIFACTS[0][0]).write_text(
                            "drift", encoding="utf-8"
                        )
                    elif mutation == "backup":
                        (fixture.transaction / "backup" / MODULE.ARTIFACTS[0][0]).write_text(
                            "drift", encoding="utf-8"
                        )
                    elif mutation == "joymouse":
                        (fixture.system / MODULE.JOY_LIVE).write_text("drift", encoding="utf-8")
                    else:
                        path = fixture.transaction / "rolled-back.json"
                        path.write_text("{}", encoding="utf-8")
                        path.chmod(0o600)
                    with self.assertRaises(MODULE.InteractionError):
                        fixture.expected()


class FileAndStaticTests(unittest.TestCase):
    def test_private_reader_rejects_public_links_duplicates_nonfinite_and_non_utf8(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-input-") as temporary:
            root = Path(temporary)
            path = root / "evidence.json"
            path.write_text(json.dumps(complete_evidence()), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(MODULE.InteractionError):
                MODULE.read_private_json(path)
            path.chmod(0o600)
            hardlink = root / "hardlink.json"
            os.link(path, hardlink)
            with self.assertRaises(MODULE.InteractionError):
                MODULE.read_private_json(path)
            hardlink.unlink()
            path.unlink()
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o600)
            path.symlink_to(target)
            with self.assertRaises(MODULE.InteractionError):
                MODULE.read_private_json(path)
            for content in (
                b'{"schema":1,"schema":2}',
                b'{"value":NaN}',
                b"\xff",
            ):
                with self.assertRaises(MODULE.InteractionError):
                    MODULE.strict_json(content)

    def test_reports_and_templates_are_private_new_and_never_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds005-ui-input-") as temporary:
            output = Path(temporary) / "report.json"
            MODULE.write_json({"complete": False}, str(output), stdout_allowed=False)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_json({"complete": False}, str(output), stdout_allowed=False)
            with self.assertRaises(MODULE.InteractionError):
                MODULE.write_json({"complete": False}, "-", stdout_allowed=False)

    def test_evaluator_has_no_live_action_or_free_text_collection_path(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "shell=True",
            "sudo",
            "systemctl",
            "busctl",
            "loginctl",
            "os.remove",
            ".unlink(",
            "requests.",
            "urllib",
            '"notes"',
            '"comment"',
            '"application_name"',
        ):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("subprocess.run("), 1)
        self.assertIn('(\"git\", \"-C\", str(repo_root), *arguments)', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
