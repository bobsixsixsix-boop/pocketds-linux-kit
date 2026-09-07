#!/usr/bin/env python3
"""Offline tests for the dependency-resolved pds2 mock transaction gate."""

from __future__ import annotations

from contextlib import redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-userspace-rpm-full-transaction.py"
LOCK = ROOT / "packaging/pocketds-userspace/full-transaction-lock.pds2.json"
SPEC = importlib.util.spec_from_file_location("pds020_full_transaction_test", SOURCE)
assert SPEC and SPEC.loader
full = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = full
SPEC.loader.exec_module(full)


class FullTransactionTests(unittest.TestCase):
    def test_real_lock_is_exact_pds2_dependency_contract(self) -> None:
        lock = full.load_lock(LOCK)
        self.assertEqual(
            [stage["name"] for stage in lock["stages"]],
            ["baseline", "dependency-install", "remove", "offline-reinstall"],
        )
        self.assertEqual(
            lock["stages"][1]["package_manifest_sha256"],
            lock["stages"][3]["package_manifest_sha256"],
        )
        self.assertTrue(lock["builder"]["initial_install_offline"])
        self.assertTrue(lock["builder"]["dependency_archives_content_locked"])
        self.assertTrue(lock["builder"]["reinstall_offline"])
        self.assertEqual(
            lock["artifact"]["binary_rpm_sha256"],
            "457ee254ff2ae15b9a0cee2a839077b02e990da1015dbdcfbc4207b654c25cdf",
        )
        self.assertIn("onboard", lock["installed_contract"]["legacy_packages_absent"])
        self.assertEqual(
            lock["artifact"]["archive_lock_sha256"],
            "7d970599eca1bb7e20e95ab8e60b863f93cdf65bc79bca3fda9d81efa4d28953",
        )

    def test_plan_is_default_and_never_claims_release_ready(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = full.main([])
        self.assertEqual(result, 0)
        plan = json.loads(output.getvalue())
        self.assertTrue(plan["planned"])
        self.assertFalse(plan["executed"])
        self.assertFalse(plan["device_root_touched"])
        self.assertFalse(plan["release_ready"])
        self.assertTrue(plan["initial_install_offline"])
        self.assertTrue(plan["dependency_archives_content_locked"])
        self.assertEqual(plan["confirmation_required"], full.CONFIRMATION)

    def test_execution_requires_exact_confirmation_and_inputs(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = full.main(["--execute", "--confirm", "wrong"])
        self.assertEqual(result, 2)
        self.assertIn("execution gate is incomplete", output.getvalue())

    def test_lock_rejects_unknown_stage_and_unsafe_path(self) -> None:
        original = json.loads(LOCK.read_text(encoding="utf-8"))
        for mutation, error in (
            (lambda value: value["stages"][0].update(name="other"), "stage record"),
            (
                lambda value: value["installed_contract"]["legacy_paths_absent"].append(
                    "/usr/../etc/passwd"
                ),
                "unsafe path",
            ),
        ):
            with self.subTest(error=error):
                with tempfile.TemporaryDirectory(prefix="pds020-full-lock-") as name:
                    candidate = copy.deepcopy(original)
                    mutation(candidate)
                    path = Path(name) / "lock.json"
                    path.write_text(json.dumps(candidate), encoding="utf-8")
                    with self.assertRaisesRegex(full.FullTransactionError, error):
                        full.load_lock(path)

    def test_package_manifest_is_byte_sorted_and_content_bound(self) -> None:
        raw = (
            b"zeta\t0\t1\t1.fc44\tnoarch\n"
            b"alpha\t0\t2\t3.fc44\taarch64\n"
            b"gpg-pubkey\t0\tdeadbeef\t12345678\t(none)\n"
        )
        completed = subprocess.CompletedProcess([], 0, stdout=raw, stderr=b"")
        with mock.patch.object(full, "run_bounded", return_value=completed):
            count, digest = full.package_manifest(
                Path("/usr/bin/sudo"), Path("/usr/bin/rpm"), Path("/root")
            )
        expected = (
            b"alpha\t0\t2\t3.fc44\taarch64\n"
            b"gpg-pubkey\t0\tdeadbeef\t12345678\t(none)\n"
            b"zeta\t0\t1\t1.fc44\tnoarch\n"
        )
        self.assertEqual(count, 3)
        self.assertEqual(digest, hashlib.sha256(expected).hexdigest())

    def test_package_manifest_rejects_duplicate_or_malformed_rows(self) -> None:
        for raw in (
            b"same\t0\t1\t1.fc44\tnoarch\nsame\t0\t1\t1.fc44\tnoarch\n",
            b"not-an-rpm-row\n",
        ):
            with self.subTest(raw=raw):
                completed = subprocess.CompletedProcess([], 0, stdout=raw, stderr=b"")
                with mock.patch.object(full, "run_bounded", return_value=completed):
                    with self.assertRaises(full.FullTransactionError):
                        full.package_manifest(
                            Path("/usr/bin/sudo"),
                            Path("/usr/bin/rpm"),
                            Path("/root"),
                        )

    def test_stage_manifest_drift_fails_closed(self) -> None:
        lock = full.load_lock(LOCK)
        with mock.patch.object(
            full,
            "package_manifest",
            return_value=(lock["stages"][0]["package_count"], "0" * 64),
        ):
            with self.assertRaisesRegex(full.FullTransactionError, "manifest differs"):
                full.check_stage(
                    lock,
                    0,
                    Path("/usr/bin/sudo"),
                    Path("/usr/bin/rpm"),
                    Path("/root"),
                )

    def test_output_is_private_new_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-full-output-") as name:
            directory = Path(name)
            directory.chmod(0o700)
            path = directory / "result.json"
            full.validate_output(path)
            full.write_output(path, {"accepted": True})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text()), {"accepted": True})
            with self.assertRaisesRegex(full.FullTransactionError, "already exists"):
                full.validate_output(path)

    def test_runner_contains_no_payload_bypass_or_shell_execution(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("--nodeps", "--noscripts", "--notriggers", "shell=True"):
            self.assertNotIn(forbidden, source)
        for required in (
            'base + ["--offline", "--init"]',
            'base + ["--offline", "--install"] + package_paths',
            '"--offline", "--remove"',
            '"--offline", "--install"',
            'base + ["--clean"]',
            "archive_verifier.verify(",
        ):
            self.assertIn(required, source)
        self.assertNotIn('"initial_install_network": True', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
