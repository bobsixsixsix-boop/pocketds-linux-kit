#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "recovery" / "dispatch.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_dispatch", SOURCE
)
assert MODULE_SPEC and MODULE_SPEC.loader
dispatch = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = dispatch
MODULE_SPEC.loader.exec_module(dispatch)

HOST_TEST_SOURCE = ROOT / "tests" / "kernel-recovery-host-preflight.py"
HOST_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_host_test_fixture", HOST_TEST_SOURCE
)
assert HOST_SPEC and HOST_SPEC.loader
host_fixture = importlib.util.module_from_spec(HOST_SPEC)
sys.modules[HOST_SPEC.name] = host_fixture
HOST_SPEC.loader.exec_module(host_fixture)


class Fixture(host_fixture.Fixture):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.log = root / "fastboot.log"
        self.serial = "fixture-serial"
        self.set_fastboot(unlocked=True, devices=1)

    def set_fastboot(self, *, unlocked: bool, devices: int) -> None:
        if devices == 0:
            device_lines = ""
        elif devices == 1:
            device_lines = f"{self.serial}\\tfastboot\\n"
        else:
            device_lines = f"{self.serial}\\tfastboot\\nsecond\\tfastboot\\n"
        unlock = "yes" if unlocked else "no"
        script = f"""#!/bin/sh
printf '%s\\n' "$*" >> {shlex.quote(os.fspath(self.log))}
if [ "$1" = --version ]; then
  printf '%s\\n' {shlex.quote(self.version)}
elif [ "$1" = devices ]; then
  printf {shlex.quote(device_lines)}
elif [ "$1" = -s ] && [ "$3" = getvar ] && [ "$4" = unlocked ]; then
  printf '%s\\n' 'unlocked: {unlock}' >&2
elif [ "$1" = -s ] && [ "$3" = boot ]; then
  [ -r "$4" ] || exit 8
  printf '%s\\n' OKAY
else
  exit 9
fi
"""
        self.fastboot.write_text(script, encoding="utf-8")
        self.fastboot.chmod(0o700)
        self.lock["artifacts"]["host_fastboot"] = host_fixture.record(self.fastboot)

    def host_report(self) -> dict[str, object]:
        return dispatch.preflight.collect(
            self.validated(),
            artifact_dir=self.artifact_dir,
            abl_payload=self.abl,
            fastboot=self.fastboot,
        )

    def execute(self) -> dict[str, object]:
        return dispatch.execute(
            self.validated(),
            fastboot=self.fastboot,
            artifact_dir=self.artifact_dir,
            preflight_report=self.host_report(),
        )

    def log_lines(self) -> list[str]:
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()


class RecoveryDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        test_root = ROOT / "build"
        test_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=test_root)
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_only_never_enumerates_or_boots_device(self) -> None:
        report = dispatch.plan(
            self.fixture.validated(), preflight_report=self.fixture.host_report()
        )
        self.assertEqual(report["action"], "plan_only")
        self.assertFalse(report["device_access"])
        self.assertFalse(report["gates"]["temporary_baseline_boot_dispatched"])
        self.assertEqual(self.fixture.log_lines(), ["--version"])

    def test_execute_uses_one_unlocked_device_and_only_boot(self) -> None:
        report = self.fixture.execute()
        self.assertTrue(report["gates"]["live_fastboot_unlock_verified"])
        self.assertTrue(report["gates"]["temporary_baseline_boot_dispatched"])
        self.assertFalse(report["gates"]["independent_runtime_boot_observed"])
        self.assertFalse(report["recovery_design"]["writes_partition"])
        self.assertFalse(report["recovery_design"]["device_identifier_recorded"])
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn(self.fixture.serial, encoded)
        lines = self.fixture.log_lines()
        self.assertEqual(lines[0:2], ["--version", "devices"])
        self.assertEqual(lines[2], f"-s {self.fixture.serial} getvar unlocked")
        boot_argv = lines[3].split()
        self.assertEqual(boot_argv[0:3], ["-s", self.fixture.serial, "boot"])
        self.assertEqual(len(boot_argv), 4)
        self.assertRegex(boot_argv[3], r"^/dev/fd/[0-9]+$")

    def test_artifact_changed_after_preflight_is_rejected_before_device_access(self) -> None:
        host = self.fixture.host_report()
        self.fixture.baseline.write_bytes(b"x" * self.fixture.baseline.stat().st_size)
        with self.assertRaisesRegex(dispatch.DispatchError, "identity differs"):
            dispatch.execute(
                self.fixture.validated(),
                fastboot=self.fixture.fastboot,
                artifact_dir=self.fixture.artifact_dir,
                preflight_report=host,
            )
        self.assertEqual(self.fixture.log_lines(), ["--version"])

    def test_locked_device_is_rejected_before_boot(self) -> None:
        self.fixture.set_fastboot(unlocked=False, devices=1)
        with self.assertRaisesRegex(dispatch.DispatchError, "not explicitly unlocked"):
            self.fixture.execute()
        self.assertFalse(any(" boot " in f" {line} " for line in self.fixture.log_lines()))

    def test_zero_or_multiple_devices_are_rejected(self) -> None:
        for count in (0, 2):
            with self.subTest(count=count):
                self.fixture.set_fastboot(unlocked=True, devices=count)
                with self.assertRaisesRegex(dispatch.DispatchError, "exactly one device"):
                    self.fixture.execute()

    def test_bad_device_state_is_rejected(self) -> None:
        self.fixture.set_fastboot(unlocked=True, devices=1)
        content = self.fixture.fastboot.read_text(encoding="utf-8").replace(
            "fixture-serial\\tfastboot", "fixture-serial\\tunauthorized"
        )
        self.fixture.fastboot.write_text(content, encoding="utf-8")
        self.fixture.fastboot.chmod(0o700)
        self.fixture.lock["artifacts"]["host_fastboot"] = host_fixture.record(
            self.fixture.fastboot
        )
        with self.assertRaisesRegex(dispatch.DispatchError, "identity or state differs"):
            self.fixture.execute()

    def test_report_is_private_new_and_never_overwritten(self) -> None:
        report = self.fixture.execute()
        output = self.fixture.root / "dispatch.json"
        dispatch._safe_output(output, report)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        before = hashlib.sha256(output.read_bytes()).hexdigest()
        with self.assertRaisesRegex(dispatch.DispatchError, "cannot create"):
            dispatch._safe_output(output, report)
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), before)

    def test_unsafe_report_parent_is_rejected(self) -> None:
        report = dispatch.plan(
            self.fixture.validated(), preflight_report=self.fixture.host_report()
        )
        parent = self.fixture.root / "unsafe"
        parent.mkdir()
        parent.chmod(0o770)
        with self.assertRaisesRegex(dispatch.DispatchError, "parent is unsafe"):
            dispatch._safe_output(parent / "report.json", report)

    def test_linked_report_parent_is_rejected(self) -> None:
        report = dispatch.plan(
            self.fixture.validated(), preflight_report=self.fixture.host_report()
        )
        linked = self.fixture.root / "linked"
        linked.symlink_to(self.fixture.artifact_dir, target_is_directory=True)
        with self.assertRaisesRegex(dispatch.DispatchError, "unavailable or linked"):
            dispatch._safe_output(linked / "report.json", report)

    def test_confirmation_is_exact_and_source_has_no_partition_command(self) -> None:
        self.assertEqual(dispatch.CONFIRMATION, "PDS002-TEMP-BOOT-BASELINE-V1")
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("args.confirm != CONFIRMATION", source)
        self.assertNotIn('["flash"', source)
        self.assertNotIn('"erase"', source)
        self.assertNotIn('"set_active"', source)

    def test_output_and_confirmation_are_execution_only(self) -> None:
        args = dispatch.parse_args(
            [
                "--artifact-dir", os.fspath(self.fixture.artifact_dir),
                "--abl-payload", os.fspath(self.fixture.abl),
                "--fastboot", os.fspath(self.fixture.fastboot),
                "--confirm", dispatch.CONFIRMATION,
                "--output", os.fspath(self.fixture.root / "report.json"),
            ]
        )
        self.assertFalse(args.execute)
        self.assertIsNotNone(args.confirm)
        self.assertIsNotNone(args.output)
        # main() rejects this combination before any device command; the branch
        # is kept explicit so confirmation text can never turn plan into action.
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("plan mode accepts no confirmation or output", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
