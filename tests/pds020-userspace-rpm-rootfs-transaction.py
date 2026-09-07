#!/usr/bin/env python3
"""Offline tests for the empty-root pds2 local RPM transaction gate."""

from __future__ import annotations

from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-userspace-rpm-rootfs-transaction.py"
LOCK = ROOT / "packaging/pocketds-userspace/rootfs-transaction-lock.pds2.json"
SPEC = importlib.util.spec_from_file_location("pds020_rootfs_transaction_test", SOURCE)
assert SPEC and SPEC.loader
rootfs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rootfs
SPEC.loader.exec_module(rootfs)


class RootfsTransactionTests(unittest.TestCase):
    def test_real_lock_is_exact_empty_root_contract(self) -> None:
        lock = rootfs.load_lock(LOCK)
        self.assertEqual(lock["installed_stage"]["package_count"], 322)
        self.assertEqual(
            lock["installed_stage"]["package_manifest_sha256"],
            "7cdf60c9f647186250a05c0b643d7e6d1cd4b0dac861e15bf97a03035f7ce0d0",
        )
        self.assertTrue(lock["builder"]["all_repositories_disabled"])
        self.assertTrue(lock["builder"]["cache_only"])
        self.assertEqual(lock["installed_contract"]["rpm_verify_output"], [])

    def test_plan_is_default_and_never_claims_release_ready(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = rootfs.main([])
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["planned"])
        self.assertTrue(report["empty_root"])
        self.assertTrue(report["offline"])
        self.assertFalse(report["device_root_touched"])
        self.assertFalse(report["release_ready"])
        self.assertEqual(report["confirmation_required"], rootfs.CONFIRMATION)

    def test_execution_requires_exact_confirmation_and_all_paths(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = rootfs.main(["--execute", "--confirm", "wrong"])
        self.assertEqual(result, 2)
        self.assertIn("execution gate is incomplete", output.getvalue())

    def test_lock_rejects_builder_drift_weak_count_and_unsafe_path(self) -> None:
        original = json.loads(LOCK.read_text(encoding="utf-8"))
        for mutation, error in (
            (
                lambda value: value["builder"].update(cache_only=False),
                "builder boundary",
            ),
            (
                lambda value: value["installed_stage"].update(package_count=True),
                "installed stage",
            ),
            (
                lambda value: value["installed_contract"]["legacy_paths_absent"].append(
                    "/etc/../etc/passwd"
                ),
                "unsafe path",
            ),
        ):
            with self.subTest(error=error):
                with tempfile.TemporaryDirectory(prefix="pds020-rootfs-lock-") as name:
                    candidate = copy.deepcopy(original)
                    mutation(candidate)
                    path = Path(name) / "lock.json"
                    path.write_text(json.dumps(candidate), encoding="utf-8")
                    with self.assertRaisesRegex(rootfs.RootfsTransactionError, error):
                        rootfs.load_lock(path)

    def test_work_directory_is_private_absolute_and_separate(self) -> None:
        with self.assertRaisesRegex(rootfs.RootfsTransactionError, "absolute"):
            rootfs.safe_private_directory(Path("relative"), "work directory")
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-work-") as name:
            work = Path(name)
            work.chmod(0o700)
            rootfs.safe_private_directory(work, "work directory")
            archive = work / "archive"
            archive.mkdir(mode=0o700)
            with self.assertRaisesRegex(rootfs.RootfsTransactionError, "separate"):
                rootfs.validate_distinct_directories(work, archive)
            work.chmod(0o755)
            with self.assertRaisesRegex(rootfs.RootfsTransactionError, "mode-0700"):
                rootfs.safe_private_directory(work, "work directory")

    def test_output_is_private_new_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-output-") as name:
            directory = Path(name)
            directory.chmod(0o700)
            path = directory / "result.json"
            rootfs.validate_output(path)
            rootfs.write_output(path, {"accepted": True})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaisesRegex(rootfs.RootfsTransactionError, "already exists"):
                rootfs.validate_output(path)

    def test_cleanup_revalidates_identity_and_uses_one_filesystem(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-clean-") as name:
            work = Path(name)
            work.chmod(0o700)
            root, identity = rootfs.create_root(work)
            commands: list[list[str]] = []

            def remove(command: list[str], _label: str, **_kwargs: object) -> object:
                commands.append(command)
                shutil.rmtree(root)
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

            with mock.patch.object(rootfs, "run_bounded", side_effect=remove):
                rootfs.cleanup_root(
                    Path("/usr/bin/sudo"),
                    Path("/usr/bin/rm"),
                    work,
                    rootfs.safe_private_directory(work, "work directory"),
                    root,
                    identity,
                )
            self.assertEqual(
                commands[0][-4:],
                ["-rf", "--one-file-system", "--", str(root)],
            )
            self.assertFalse(root.exists())

    def test_failed_transaction_still_attempts_cleanup(self) -> None:
        lock = rootfs.load_lock(LOCK)
        archive_lock = {"packages": [{"filename": "one.rpm"}]}
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-fail-") as name:
            work = Path(name)
            work.chmod(0o700)
            with (
                mock.patch.object(
                    rootfs,
                    "run_bounded",
                    side_effect=rootfs.RootfsTransactionError("install failed"),
                ),
                mock.patch.object(rootfs, "cleanup_root") as cleaned,
            ):
                with self.assertRaisesRegex(rootfs.RootfsTransactionError, "install failed"):
                    rootfs.execute(
                        lock,
                        {},
                        archive_lock,
                        Path("/archive"),
                        work,
                        Path("/usr/bin/sudo"),
                        Path("/usr/bin/dnf5"),
                        Path("/usr/bin/rpm"),
                        Path("/usr/bin/rm"),
                    )
            cleaned.assert_called_once()

    def test_execute_uses_only_local_archives_and_checks_then_cleans(self) -> None:
        lock = rootfs.load_lock(LOCK)
        archive_lock = {
            "packages": [{"filename": "one.rpm"}, {"filename": "two.rpm"}]
        }
        commands: list[list[str]] = []
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-run-") as name:
            work = Path(name)
            work.chmod(0o700)

            def completed(command: list[str], _label: str, **_kwargs: object) -> object:
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

            def cleanup(
                _sudo: Path,
                _rm: Path,
                _work: Path,
                _work_identity: tuple[int, int],
                root: Path,
                _root_identity: tuple[int, int],
            ) -> None:
                os.rmdir(root)

            with (
                mock.patch.object(rootfs, "run_bounded", side_effect=completed),
                mock.patch.object(
                    rootfs.full,
                    "package_manifest",
                    return_value=(
                        lock["installed_stage"]["package_count"],
                        lock["installed_stage"]["package_manifest_sha256"],
                    ),
                ),
                mock.patch.object(rootfs.full, "validate_installed"),
                mock.patch.object(rootfs, "cleanup_root", side_effect=cleanup),
            ):
                observed = rootfs.execute(
                    lock,
                    {},
                    archive_lock,
                    Path("/archive"),
                    work,
                    Path("/usr/bin/sudo"),
                    Path("/usr/bin/dnf5"),
                    Path("/usr/bin/rpm"),
                    Path("/usr/bin/rm"),
                )
        install = commands[0]
        check = commands[1]
        for required in (
            "--use-host-config",
            "--releasever=44",
            "--disable-repo=*",
            "--no-plugins",
            "--cacheonly",
            "--assumeyes",
            "install",
            "/archive/one.rpm",
            "/archive/two.rpm",
        ):
            self.assertIn(required, install)
        self.assertEqual(check[-1], "check")
        self.assertEqual(observed["dnf_check"], "pass")

    def test_runner_contains_no_dependency_script_signature_or_network_bypass(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "--nodeps",
            "--noscripts",
            "--notriggers",
            "--no-gpgchecks",
            "urlopen",
            "requests.",
            "curl ",
            "wget ",
            "shell=True",
        ):
            self.assertNotIn(forbidden, source)
        for required in (
            '"--disable-repo=*"',
            '"--no-plugins"',
            '"--cacheonly"',
            '"--one-file-system"',
            "archive_verifier.verify(",
            '"selinux_labels_verified": False',
            '"project_signature_verified": False',
            '"release_ready": False',
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
