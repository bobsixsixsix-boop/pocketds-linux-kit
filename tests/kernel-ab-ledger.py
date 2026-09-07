#!/usr/bin/env python3

from __future__ import annotations

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
SOURCE = ROOT / "tools" / "kernel-ab" / "ab-ledger.py"
SPEC = importlib.util.spec_from_file_location("pocketds_kernel_ab_ledger_test", SOURCE)
assert SPEC and SPEC.loader
ledger_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ledger_module
SPEC.loader.exec_module(ledger_module)
evaluate = ledger_module.evaluate


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lock, _lock_sha256 = evaluate.load_lock()
        self.workloads = {name: digest(name) for name in self.lock["profiles"]}

    def test_template_is_complete_unique_and_deliberately_unresolved(self) -> None:
        ledger = ledger_module.build_ledger(self.lock, self.workloads)
        self.assertEqual(len(ledger["runs"]), 18)
        cells = {
            (run["variant"], run["profile"], run["round"])
            for run in ledger["runs"]
        }
        self.assertEqual(len(cells), 18)
        self.assertEqual(len({run["report"] for run in ledger["runs"]}), 18)
        self.assertTrue(
            all(value is None for run in ledger["runs"] for value in run["operator"].values())
        )

    def test_bad_or_missing_workload_hash_is_rejected(self) -> None:
        changed = dict(self.workloads)
        changed["dual-165-60"] = "not-a-digest"
        with self.assertRaisesRegex(evaluate.EvaluationError, "workload identity"):
            ledger_module.build_ledger(self.lock, changed)
        changed.pop("dual-60-60")
        with self.assertRaisesRegex(evaluate.EvaluationError, "profile set"):
            ledger_module.build_ledger(self.lock, changed)

    def test_unresolved_template_cannot_be_accepted_as_evidence(self) -> None:
        ledger = ledger_module.build_ledger(self.lock, self.workloads)
        with self.assertRaisesRegex(evaluate.EvaluationError, "operator observation"):
            evaluate.evaluate(ledger, self.lock, "0" * 64, ROOT)

    def test_cli_creates_one_private_file_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            output = Path(temporary) / "ledger.json"
            command = [sys.executable, os.fspath(SOURCE)]
            for profile, value in self.workloads.items():
                command.extend((f"--workload-{profile}", value))
            command.extend(("--output", os.fspath(output)))
            first = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            parsed = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(parsed["runs"]), 18)
            before = output.read_bytes()
            second = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(output.read_bytes(), before)

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
