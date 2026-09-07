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
SOURCE = ROOT / "tools" / "kernel-ab" / "ab-evaluate.py"
SPEC = importlib.util.spec_from_file_location("pocketds_kernel_ab_evaluate_test", SOURCE)
assert SPEC and SPEC.loader
evaluate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evaluate
SPEC.loader.exec_module(evaluate)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock, self.lock_sha256 = evaluate.load_lock()
        self.workloads = {profile: digest("workload:" + profile) for profile in self.lock["profiles"]}
        self.runs: list[dict[str, object]] = []
        for variant in self.lock["variants"]:
            for round_number in range(1, self.lock["minimum_rounds_per_variant"] + 1):
                boot_id = f"boot-{variant}-{round_number}"
                for profile in self.lock["profiles"]:
                    filename = f"{variant}-{round_number}-{profile}.jsonl"
                    self._write_report(filename, variant, profile, boot_id)
                    self.runs.append(
                        {
                            "variant": variant,
                            "profile": profile,
                            "round": round_number,
                            "report": filename,
                            "operator": {
                                "black_screen_or_desktop_failed": False,
                                "thermal_power_or_fan_limit_exceeded": False,
                                "local_console_and_ssh_both_unavailable": False,
                                "manual_recovery_required": False,
                            },
                        }
                    )
        self.ledger = {
            "schema": evaluate.LEDGER_SCHEMA,
            "experiment": self.lock["experiment"],
            "workloads": self.workloads,
            "runs": self.runs,
        }

    def _display(self, profile: str) -> dict[str, object]:
        outputs = [
            {
                "name": name,
                "enabled": True,
                "refresh_millihz": rates[0],
            }
            for name, rates in self.lock["profiles"][profile].items()
        ]
        signature = digest("display:" + profile)
        return {
            "kwin": {
                "backend": "DRM",
                "atomic": "true",
                "renderer": "FD740",
                "mesa": "26.1.8",
                "outputs": outputs,
            },
            "connectors": [],
            "signature": signature,
            "kwin_error": None,
        }

    def _records(self, variant: str, profile: str, boot_id: str) -> list[dict[str, object]]:
        display = self._display(profile)
        controls = {
            name: {"sha256": item["sha256"], "size": item["size"], "error": None}
            for name, item in self.lock["fixed_controls"].items()
        }
        runtime_controls = {
            name: {"sha256": item["sha256"], "size": item["size"], "error": None}
            for name, item in self.lock["runtime_controls"].items()
        }
        header = {
            "type": "header",
            "schema": self.lock["collector_schema"],
            "duration_s": 2700.0,
            "interval_s": 1350.0,
            "repo_revision": "1" * 40,
            "workload_sha256": self.workloads[profile],
            "boot": {
                "id": boot_id,
                "kernel": self.lock["kernel_release"],
                "notes": {
                    "sha256": self.lock["variants"][variant]["notes_sha256"],
                    "size": 128,
                    "error": None,
                },
            },
            "fixed_controls": controls,
            "runtime_controls": runtime_controls,
            "display": display,
        }
        samples = [
            {
                "type": "sample",
                "schema": self.lock["collector_schema"],
                "seq": sequence,
                "gpu": {"age_ms": 250, "gpu_percent": 4.0, "gpu_temp_c": 50.0},
                "pressure": {
                    resource: {"some": {"avg10": 0.1}}
                    for resource in ("cpu", "io", "memory")
                },
                "runtime_controls": runtime_controls,
            }
            for sequence in range(2)
        ]
        event_names = evaluate.ZERO_CANDIDATE_EVENTS + ("kwin_atomic_ebusy", "smmu_fault")
        counts = {name: 0 for name in event_names}
        summary = {
            "type": "summary",
            "schema": self.lock["collector_schema"],
            "wall_s": 2700.0,
            "sample_count": 2,
            "sample_gap_max_s": 1350.0,
            "counts": counts,
            "bursts": {"kwin_atomic_ebusy": {"max_events": 0}},
            "critical_user_d_state_intervals": [],
            "collector_cpu_percent": 0.01,
            "journal": {
                "integrity": True,
                "follow_errors": [],
                "follower_returncode": 0,
            },
            "boot": {"start": boot_id, "end": boot_id, "unchanged": True},
            "display": {
                "start_signature": display["signature"],
                "end_signature": display["signature"],
                "unchanged": True,
                "changed_during_run": False,
                "probe_errors": [],
            },
            "fixed_controls": {
                "start": controls,
                "end": controls,
                "unchanged": True,
            },
            "runtime_controls": {
                "start": runtime_controls,
                "end": runtime_controls,
                "unchanged": True,
                "signatures_seen": [
                    hashlib.sha256(
                        evaluate.canonical_bytes(runtime_controls)
                    ).hexdigest()
                ],
            },
            "interrupted": False,
            "result": "clean",
        }
        return [header, *samples, summary]

    def _write_report(self, filename: str, variant: str, profile: str, boot_id: str) -> None:
        records = self._records(variant, profile, boot_id)
        path = self.root / filename
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def rewrite(self, filename: str, mutate: object) -> None:
        path = self.root / filename
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        mutate(records)
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def write_ledger(self, name: str = "ledger.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(self.ledger), encoding="utf-8")
        path.chmod(0o600)
        return path

    def collect(self) -> dict[str, object]:
        return evaluate.evaluate(self.ledger, self.lock, self.lock_sha256, self.root)


class EvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.root = Path(self.temporary.name)
        self.fixture = Fixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_three_profile_three_round_matrix_passes_without_authorizing_install(self) -> None:
        report = self.fixture.collect()
        self.assertEqual(report["matrix"]["collector_report_count"], 18)
        self.assertEqual(report["matrix"]["matched_pair_count"], 9)
        self.assertEqual(report["matrix"]["distinct_boot_count"], 6)
        self.assertTrue(report["gates"]["experiment_acceptance_passed"])
        self.assertTrue(report["gates"]["power_and_fan_controls_bound"])
        self.assertFalse(report["gates"]["candidate_install_authorized"])
        self.assertFalse(report["matrix"]["raw_boot_identifiers_recorded"])

    def test_candidate_critical_event_is_valid_failure_not_an_authorization(self) -> None:
        filename = "candidate-1-dual-165-60.jsonl"
        self.fixture.rewrite(filename, lambda records: records[-1]["counts"].__setitem__("gmu_oob_timeout", 1))
        report = self.fixture.collect()
        pair = next(item for item in report["pairs"] if item["profile"] == "dual-165-60" and item["round"] == 1)
        self.assertFalse(pair["checks"]["candidate_zero_critical_events"])
        self.assertFalse(report["gates"]["experiment_acceptance_passed"])
        self.assertFalse(report["gates"]["candidate_install_authorized"])

    def test_notes_and_workload_must_bind_the_declared_cell(self) -> None:
        filename = "candidate-1-dual-165-60.jsonl"
        self.fixture.rewrite(
            filename,
            lambda records: records[0]["boot"]["notes"].__setitem__(
                "sha256", self.fixture.lock["variants"]["baseline"]["notes_sha256"]
            ),
        )
        with self.assertRaisesRegex(evaluate.EvaluationError, "run identity"):
            self.fixture.collect()
        self.fixture._write_report(filename, "candidate", "dual-165-60", "boot-candidate-1")
        self.fixture.rewrite(filename, lambda records: records[0].__setitem__("workload_sha256", digest("other")))
        with self.assertRaisesRegex(evaluate.EvaluationError, "workload"):
            self.fixture.collect()

    def test_fixed_control_or_display_drift_closes_acceptance_gate(self) -> None:
        filename = "candidate-1-dual-165-60.jsonl"
        self.fixture.rewrite(
            filename,
            lambda records: records[0]["fixed_controls"]["kwin_wayland"].__setitem__("sha256", "0" * 64),
        )
        report = self.fixture.collect()
        self.assertFalse(report["gates"]["all_reports_admissible"])
        self.assertFalse(report["gates"]["experiment_acceptance_passed"])
        self.fixture._write_report(filename, "candidate", "dual-165-60", "boot-candidate-1")
        self.fixture.rewrite(
            filename,
            lambda records: records[0]["display"]["kwin"]["outputs"][0].__setitem__("refresh_millihz", 60000),
        )
        report = self.fixture.collect()
        self.assertFalse(report["gates"]["experiment_acceptance_passed"])

    def test_power_and_fan_control_drift_closes_acceptance_gate(self) -> None:
        filename = "candidate-1-dual-165-60.jsonl"
        self.fixture.rewrite(
            filename,
            lambda records: records[1]["runtime_controls"]["fan_profile"].__setitem__(
                "sha256", "0" * 64
            ),
        )
        report = self.fixture.collect()
        self.assertFalse(report["gates"]["all_reports_admissible"])
        self.assertFalse(report["gates"]["experiment_acceptance_passed"])
        self.assertFalse(report["gates"]["power_and_fan_controls_bound"])
        self.fixture._write_report(
            filename, "candidate", "dual-165-60", "boot-candidate-1"
        )
        self.fixture.rewrite(
            filename,
            lambda records: records[-1]["fixed_controls"].__setitem__(
                "unchanged", False
            ),
        )
        report = self.fixture.collect()
        self.assertFalse(report["gates"]["all_reports_admissible"])

    def test_candidate_temperature_limit_and_nonregression_are_enforced(self) -> None:
        filename = "candidate-1-dual-165-60.jsonl"
        self.fixture.rewrite(
            filename,
            lambda records: [
                sample["gpu"].__setitem__("gpu_temp_c", 90.0)
                for sample in records
                if sample.get("type") == "sample"
            ],
        )
        report = self.fixture.collect()
        pair = next(
            item
            for item in report["pairs"]
            if item["profile"] == "dual-165-60" and item["round"] == 1
        )
        self.assertFalse(pair["checks"]["reports_admissible"])
        self.assertFalse(pair["checks"]["gpu_temperature_non_regression"])
        self.assertFalse(report["gates"]["experiment_acceptance_passed"])

    def test_report_inode_content_and_boot_reuse_are_rejected(self) -> None:
        original = self.root / "baseline-1-dual-165-60.jsonl"
        reused = self.root / "baseline-1-dual-60-60.jsonl"
        reused.unlink()
        os.link(original, reused)
        with self.assertRaisesRegex(evaluate.EvaluationError, "metadata is unsafe"):
            self.fixture.collect()
        reused.unlink()
        self.fixture._write_report(reused.name, "baseline", "dual-60-60", "boot-baseline-1")
        for profile in self.fixture.lock["profiles"]:
            filename = f"baseline-2-{profile}.jsonl"
            self.fixture.rewrite(
                filename,
                lambda records: (
                    records[0]["boot"].__setitem__("id", "boot-baseline-1"),
                    records[0].__setitem__("started_at", f"round-2-{profile}"),
                    records[-1].__setitem__(
                        "boot",
                        {"start": "boot-baseline-1", "end": "boot-baseline-1", "unchanged": True},
                    ),
                ),
            )
        with self.assertRaisesRegex(evaluate.EvaluationError, "reused across"):
            self.fixture.collect()

    def test_private_reports_and_ledger_are_required(self) -> None:
        ledger = self.fixture.write_ledger()
        ledger.chmod(0o644)
        with self.assertRaisesRegex(evaluate.EvaluationError, "metadata is unsafe"):
            evaluate.load_ledger(ledger, self.fixture.lock)
        ledger.chmod(0o600)
        hard = self.root / "hard-ledger.json"
        os.link(ledger, hard)
        with self.assertRaisesRegex(evaluate.EvaluationError, "metadata is unsafe"):
            evaluate.load_ledger(ledger, self.fixture.lock)

    def test_cli_writes_new_private_report_and_require_pass_succeeds(self) -> None:
        ledger = self.fixture.write_ledger()
        output = self.root / "evaluation.json"
        completed = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--ledger", os.fspath(ledger), "--output", os.fspath(output), "--require-pass"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        first = output.read_bytes()
        second = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--ledger", os.fspath(ledger), "--output", os.fspath(output)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(second.returncode, 2)
        self.assertEqual(output.read_bytes(), first)

    def test_cli_has_no_live_device_or_power_action(self) -> None:
        completed = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--help"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        for forbidden in ("--execute", "--device", "--flash", "--reboot", "--install", "--suspend"):
            self.assertNotIn(forbidden, completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
