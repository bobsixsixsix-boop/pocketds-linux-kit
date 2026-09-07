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
SOURCE = ROOT / "tools/kernel-ab/recovery/transaction.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_transaction_test", SOURCE
)
assert MODULE_SPEC and MODULE_SPEC.loader
transaction = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = transaction
MODULE_SPEC.loader.exec_module(transaction)


def record(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.baseline_image = root / "baseline.img"
        self.candidate_image = root / "candidate.img"
        self.baseline = b"B" * 1024
        self.candidate = b"C" * 1024
        assert len(self.baseline) == len(self.candidate)
        self.baseline_image.write_bytes(self.baseline)
        self.candidate_image.write_bytes(self.candidate)
        self.baseline_image.chmod(0o600)
        self.candidate_image.chmod(0o600)
        self.aliases = ["boot/Image", "boot/boot/Image", "boot/Image-fixture"]
        self.lock = {
            "device_id": "fixture-device",
            "machine_model": "Fixture Device",
            "kernel_release": "fixture.release",
            "artifacts": {
                "baseline_rollback_boot": record(self.baseline_image),
                "candidate_runtime_boot": record(self.candidate_image),
            },
            "disk_boot_aliases": self.aliases,
        }
        for relative in (
            "sys/firmware/devicetree/base/model",
            "proc/sys/kernel/osrelease",
            "proc/self/mountinfo",
            *self.aliases,
        ):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / "sys/firmware/devicetree/base/model").write_bytes(b"Fixture Device\x00")
        (root / "proc/sys/kernel/osrelease").write_text(
            "fixture.release\n", encoding="utf-8"
        )
        (root / "proc/self/mountinfo").write_text(
            "36 25 8:12 / /boot rw,relatime - vfat /dev/sda12 rw\n",
            encoding="utf-8",
        )
        self.set_aliases("baseline", "baseline", "baseline")

    def set_aliases(self, *identities: str) -> None:
        if len(identities) != len(self.aliases):
            raise AssertionError("fixture alias count differs")
        for relative, identity in zip(self.aliases, identities):
            content = self.baseline if identity == "baseline" else self.candidate
            path = self.root / relative
            if path.is_symlink():
                path.unlink()
            path.write_bytes(content)
            path.chmod(0o600)

    def inspect(self, target: str = "candidate") -> tuple[dict[str, object], dict[str, bytes], dict[str, str]]:
        return transaction.inspect(
            self.lock,
            root=self.root,
            baseline_image=self.baseline_image,
            candidate_image=self.candidate_image,
            target=target,
        )

    def perform(self, target: str = "candidate", **kwargs: object) -> dict[str, object]:
        return transaction.perform(
            self.lock,
            root=self.root,
            baseline_image=self.baseline_image,
            candidate_image=self.candidate_image,
            target=target,
            require_root=False,
            **kwargs,
        )

    def identities(self) -> list[str]:
        result = []
        for relative in self.aliases:
            content = (self.root / relative).read_bytes()
            result.append("baseline" if content == self.baseline else "candidate" if content == self.candidate else "unknown")
        return result


class RecoveryTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / "build").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_candidate_plan_is_read_only_and_requires_all_baseline(self) -> None:
        report, _contents, states = self.fixture.inspect()
        self.assertEqual(report["action"], "plan_only")
        self.assertEqual(report["target_identity"], "candidate")
        self.assertEqual(set(states.values()), {"baseline"})
        self.assertFalse(report["gates"]["write_executed"])
        self.assertFalse(report["gates"]["candidate_install_authorized"])
        self.assertEqual(self.fixture.identities(), ["baseline"] * 3)

    def test_candidate_stage_converges_all_aliases_without_authorizing_install(self) -> None:
        report = self.fixture.perform()
        self.assertEqual(report["action"], "candidate_staged")
        self.assertEqual(report["changed_alias_count"], 3)
        self.assertTrue(report["gates"]["all_aliases_match_target"])
        self.assertFalse(report["gates"]["candidate_install_authorized"])
        self.assertEqual(self.fixture.identities(), ["candidate"] * 3)

    def test_baseline_restore_accepts_candidate_or_mixed_preimage(self) -> None:
        for identities in (
            ("candidate", "candidate", "candidate"),
            ("candidate", "baseline", "candidate"),
        ):
            with self.subTest(identities=identities):
                self.fixture.set_aliases(*identities)
                report = self.fixture.perform(target="baseline")
                self.assertEqual(report["action"], "baseline_restored")
                self.assertEqual(self.fixture.identities(), ["baseline"] * 3)

    def test_candidate_stage_rejects_mixed_preimage(self) -> None:
        self.fixture.set_aliases("baseline", "candidate", "baseline")
        with self.assertRaisesRegex(transaction.TransactionError, "all-baseline"):
            self.fixture.perform()
        self.assertEqual(
            self.fixture.identities(), ["baseline", "candidate", "baseline"]
        )

    def test_unknown_alias_or_symlink_is_rejected_without_writes(self) -> None:
        path = self.fixture.root / self.fixture.aliases[1]
        path.write_bytes(b"unknown")
        with self.assertRaisesRegex(transaction.TransactionError, "neither locked"):
            self.fixture.inspect()
        self.fixture.set_aliases("baseline", "baseline", "baseline")
        path.unlink()
        path.symlink_to(self.fixture.root / self.fixture.aliases[0])
        with self.assertRaisesRegex(transaction.TransactionError, "unavailable or linked"):
            self.fixture.inspect()

    def test_source_tamper_is_rejected_before_alias_changes(self) -> None:
        self.fixture.candidate_image.write_bytes(self.fixture.candidate + b"x")
        with self.assertRaisesRegex(transaction.TransactionError, "artifact differs"):
            self.fixture.perform()
        self.assertEqual(self.fixture.identities(), ["baseline"] * 3)

    def test_machine_release_and_boot_mount_are_strict(self) -> None:
        cases = (
            ("proc/sys/kernel/osrelease", "wrong\n"),
            ("proc/self/mountinfo", "36 25 8:13 / /boot rw - ext4 /dev/sda13 rw\n"),
        )
        for relative, content in cases:
            with self.subTest(relative=relative):
                path = self.fixture.root / relative
                original = path.read_text(encoding="utf-8")
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(transaction.TransactionError):
                    self.fixture.inspect()
                path.write_text(original, encoding="utf-8")

    def test_post_rename_failure_restores_every_exact_preimage(self) -> None:
        calls = 0

        def fail_after_second_rename(path: Path, content: bytes) -> None:
            nonlocal calls
            calls += 1
            transaction._atomic_replace(path, content)
            if calls == 2:
                raise transaction.TransactionError("injected post-rename failure")

        with self.assertRaisesRegex(transaction.TransactionError, "exact preimage restored"):
            self.fixture.perform(writer=fail_after_second_rename)
        self.assertEqual(self.fixture.identities(), ["baseline"] * 3)
        self.assertFalse(any(".pds002-" in path.name for path in self.fixture.root.rglob("*")))

    def test_baseline_noop_is_explicit_and_writes_nothing(self) -> None:
        writes = 0

        def counted_writer(_path: Path, _content: bytes) -> None:
            nonlocal writes
            writes += 1

        report = self.fixture.perform(target="baseline", writer=counted_writer)
        self.assertEqual(report["action"], "no_change_needed")
        self.assertEqual(report["changed_alias_count"], 0)
        self.assertEqual(writes, 0)

    def test_cli_is_fixed_root_confirmation_gated_and_never_reboots(self) -> None:
        completed = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--help"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.assertNotIn("--root", completed.stdout)
        self.assertIn("--execute", completed.stdout)
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("args.confirm != CONFIRMATIONS[args.target]", source)
        for forbidden in ("subprocess", "os.system", '"reboot"', '"flash"', '"fastboot"'):
            self.assertNotIn(forbidden, source)

    def test_execution_report_is_private_new_and_never_overwritten(self) -> None:
        report = self.fixture.perform()
        output = self.fixture.root / "transaction.json"
        reservation = transaction._reserve_report(
            output, target="candidate", lock_sha256="a" * 64
        )
        intent = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(intent["action"], "transaction_reserved_before_boot_alias_write")
        self.assertFalse(intent["candidate_install_authorized"])
        transaction._finish_report(reservation, report)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        before = output.read_bytes()
        with self.assertRaisesRegex(transaction.TransactionError, "cannot reserve"):
            transaction._reserve_report(
                output, target="candidate", lock_sha256="a" * 64
            )
        self.assertEqual(output.read_bytes(), before)

    def test_cancelled_report_reservation_leaves_no_placeholder(self) -> None:
        output = self.fixture.root / "cancelled.json"
        reservation = transaction._reserve_report(
            output, target="baseline", lock_sha256="b" * 64
        )
        self.assertTrue(output.exists())
        transaction._cancel_report(reservation)
        self.assertFalse(output.exists())

    def test_abandoned_reservation_preserves_recovery_intent(self) -> None:
        output = self.fixture.root / "abandoned.json"
        reservation = transaction._reserve_report(
            output, target="candidate", lock_sha256="c" * 64
        )
        transaction._abandon_report(reservation)
        intent = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(intent["action"], "transaction_reserved_before_boot_alias_write")
        self.assertFalse(intent["final_report_committed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
