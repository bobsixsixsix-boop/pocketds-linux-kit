#!/usr/bin/env python3
"""Offline tests for the disposable mock RPM transaction cycle."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-userspace-rpm-transaction.py"
LOCK = ROOT / "packaging/pocketds-userspace/transaction-lock.json"
BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.json"
SPEC = importlib.util.spec_from_file_location("pds020_userspace_transaction_test", SOURCE)
assert SPEC and SPEC.loader
transaction = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = transaction
SPEC.loader.exec_module(transaction)


def marker_output(value: dict[str, object]) -> bytes:
    return (
        "mock diagnostic line\n"
        + "\n".join(
            (
                f"PDS020_STAGE={value['stage']}",
                f"PDS020_NEVRA={value['nevra']}",
                f"PDS020_PACKAGE_COUNT={value['package_count']}",
                f"PDS020_FILE_COUNT={value['file_count']}",
                f"PDS020_SUDOERS={value['sudoers']}",
                f"PDS020_FAN_SHA256={value['fan_sha256']}",
                f"PDS020_FAN_OWNER={value['fan_owner']}",
            )
        )
        + "\n"
    ).encode()


class TransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = transaction.load_lock(LOCK)
        self.build_lock = transaction.repro.load_lock(BUILD_LOCK)

    def test_recorded_lock_binds_two_vendor_builds_and_payload_only_scope(self):
        self.assertEqual(self.lock["vendor"]["independent_result_count"], 2)
        self.assertEqual(
            self.lock["vendor"]["artifact"]["sha256"],
            "27694198193c165369e560dcf1dd1779a81ecf9d191d605e57368c1c432a1f63",
        )
        self.assertTrue(self.lock["transaction"]["payload_ownership_only"])
        self.assertFalse(self.lock["transaction"]["scriptlets_executed"])
        self.assertFalse(self.lock["transaction"]["dependency_resolution_tested"])

    def test_exact_inspection_markers_parse_and_match_each_stage(self):
        for stage in self.lock["transaction"]["stages"]:
            expected = transaction.expected_inspection(stage, self.lock, self.build_lock)
            self.assertEqual(
                transaction.parse_inspection(marker_output(expected), stage), expected
            )

    def test_duplicate_missing_wrong_stage_and_bad_counts_fail_closed(self):
        expected = transaction.expected_inspection(
            "vendor-install", self.lock, self.build_lock
        )
        exact = marker_output(expected)
        variants = (
            exact + b"PDS020_STAGE=vendor-install\n",
            exact.replace(b"PDS020_FAN_OWNER", b"NOT_A_MARKER", 1),
            exact.replace(b"vendor-install", b"hardened-upgrade"),
            exact.replace(b"PDS020_FILE_COUNT=72", b"PDS020_FILE_COUNT=not-a-count"),
        )
        for data in variants:
            with self.subTest(data=data):
                with self.assertRaises(transaction.TransactionError):
                    transaction.parse_inspection(data, "vendor-install")

    def test_mock_cycle_uses_all_four_fixed_stages_and_cleans(self):
        commands: list[list[str]] = []

        def fake_run(
            command: list[str], _maximum: int, label: str, _timeout: int
        ) -> bytes:
            commands.append(command)
            if label.endswith(" inspection"):
                stage = label.removesuffix(" inspection")
                return marker_output(
                    transaction.expected_inspection(stage, self.lock, self.build_lock)
                )
            return b""

        with mock.patch.object(transaction, "run_bounded", side_effect=fake_run):
            observed = transaction.execute_transaction(
                self.lock,
                self.build_lock,
                Path("/private/vendor.rpm"),
                Path("/private/hardened.rpm"),
                Path("/usr/bin/sudo"),
                Path("/usr/libexec/mock/mock"),
            )
        self.assertEqual(
            [item["stage"] for item in observed], self.lock["transaction"]["stages"]
        )
        self.assertEqual(sum("--clean" in command for command in commands), 2)
        self.assertTrue(all("--offline" in command for command in commands[1:]))
        self.assertTrue(
            all(
                any(part.startswith("pds020-userspace-txn-") for part in command)
                for command in commands[1:]
            )
        )

    def test_failed_transaction_still_attempts_bounded_mock_cleanup(self):
        commands: list[list[str]] = []

        def fake_run(
            command: list[str], _maximum: int, label: str, _timeout: int
        ) -> bytes:
            commands.append(command)
            if label == "hardened-upgrade transaction":
                raise transaction.TransactionError("injected transaction failure")
            if label.endswith(" inspection"):
                stage = label.removesuffix(" inspection")
                return marker_output(
                    transaction.expected_inspection(stage, self.lock, self.build_lock)
                )
            return b""

        with mock.patch.object(transaction, "run_bounded", side_effect=fake_run):
            with self.assertRaisesRegex(transaction.TransactionError, "injected"):
                transaction.execute_transaction(
                    self.lock,
                    self.build_lock,
                    Path("/private/vendor.rpm"),
                    Path("/private/hardened.rpm"),
                    Path("/usr/bin/sudo"),
                    Path("/usr/libexec/mock/mock"),
                )
        self.assertIn("--clean", commands[-1])

    def test_plan_is_side_effect_free_and_execution_gate_is_exact(self):
        planned = subprocess.run(
            [sys.executable, os.fspath(SOURCE)],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        report = json.loads(planned.stdout)
        self.assertTrue(report["planned"])
        self.assertFalse(report["executed"])
        self.assertFalse(report["release_ready"])
        rejected = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--execute", "--confirm", "wrong"],
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertFalse(json.loads(rejected.stdout)["completed"])

    def test_lock_json_types_duplicates_and_policy_are_strict(self):
        with tempfile.TemporaryDirectory(prefix="pds020-transaction-") as name:
            path = Path(name) / "lock.json"
            value = json.loads(LOCK.read_text(encoding="utf-8"))
            value["transaction"]["scriptlets_executed"] = 0
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(transaction.TransactionError, "policy"):
                transaction.load_lock(path)
        with self.assertRaisesRegex(transaction.TransactionError, "duplicate key"):
            transaction.strict_json(b'{"schema":1,"schema":2}', "fixture")
        with self.assertRaisesRegex(transaction.TransactionError, "non-finite"):
            transaction.strict_json(b'{"schema":NaN}', "fixture")

    def test_report_is_private_new_and_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="pds020-transaction-") as name:
            base = Path(name)
            output = base / "report.json"
            transaction.validate_output(output)
            transaction.write_report(output, {"schema": 1, "accepted": True})
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                transaction.write_report(output, {"schema": 1})
            with self.assertRaisesRegex(transaction.TransactionError, "mode-0700"):
                transaction.validate_output(output)

    def test_source_has_only_fixed_disposable_mock_mutation_boundary(self):
        source = SOURCE.read_text(encoding="utf-8")
        for required in (
            '"--uniqueext"',
            '"--offline"',
            '"--clean"',
            '"--init"',
            '"--copyin"',
            '"--chroot"',
            "--nodeps --noscripts --notriggers",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "--root /",
            "dnf ",
            "systemctl",
            "reboot",
            "shutdown",
            "fastboot",
            "urllib.request",
            "requests",
            "socket",
            "shell=True",
            "rpmsign",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
