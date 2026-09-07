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
SOURCE = ROOT / "tools" / "kernel-ab" / "recovery" / "evaluate.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_evaluate_test", SOURCE
)
assert MODULE_SPEC and MODULE_SPEC.loader
evaluate = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = evaluate
MODULE_SPEC.loader.exec_module(evaluate)


def token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def evidence() -> dict[str, dict[str, object]]:
    return {
        name: {"filename": name + ".json", "sha256": token(name), "size": 100}
        for name in (
            "static_preflight", "host_preflight", "dispatch", "ram_attestation",
            "restored_attestation", "normal_boot_attestation",
        )
    }


class Fixture:
    def __init__(self) -> None:
        raw, self.lock_sha256 = evaluate.payload.load_lock(
            ROOT / "tools/kernel-ab/recovery/lock.json"
        )
        self.lock = evaluate.preflight.validate_lock(raw)
        rollback_raw, self.rollback_lock_sha256 = evaluate.payload.load_lock(
            ROOT / "tools/kernel-ab/rollback/lock.json"
        )
        self.rollback_lock = evaluate.rollback_preflight.validate_lock(rollback_raw)
        self.host = self._host()
        self.dispatch = self._dispatch()
        self.ram = self._runtime(
            "baseline", "candidate", token("ram-boot"), 30.0, True,
            observed_at=1_000_000_000_000,
        )
        self.restored = self._runtime(
            "baseline", "baseline", token("ram-boot"), 55.0, False,
            observed_at=1_025_000_000_000,
        )
        self.normal = self._runtime(
            "baseline", "baseline", token("normal-boot"), 20.0, False,
            observed_at=1_050_000_000_000,
        )
        self.static = self._static()

    def _host(self) -> dict[str, object]:
        return {
            "schema": evaluate.preflight.REPORT_SCHEMA,
            "read_only": True,
            "network": False,
            "device_access": False,
            "device_id": self.lock["device_id"],
            "kernel_release": self.lock["kernel_release"],
            "artifacts": self.lock["artifacts"],
            "boot_images": {
                "baseline": {
                    "kernel_size": self.lock["boot_images"]["baseline_kernel_size"],
                    "page_size": self.lock["boot_images"]["page_size"],
                    "image_id_hex": self.lock["boot_images"]["baseline_image_id_hex"],
                },
                "candidate": {
                    "kernel_size": self.lock["boot_images"]["candidate_kernel_size"],
                    "page_size": self.lock["boot_images"]["page_size"],
                    "image_id_hex": self.lock["boot_images"]["candidate_image_id_hex"],
                },
            },
            "rocknix_abl": {
                "release": self.lock["rocknix_abl"]["release"],
                "linuxloader_commit": self.lock["rocknix_abl"]["linuxloader_commit"],
                "official_release_asset_digest_bound": True,
                "fastboot_evidence_string_plaintext_in_wrapper": False,
            },
            "host_fastboot": {
                "version_line": self.lock["host_fastboot"]["version_line"],
                "exit_code": 0,
            },
            "recovery_design": {
                "transport": "fastboot-ram-boot",
                "temporary_boot_subcommand": "boot",
                "requires_unlocked": True,
                "writes_partition": False,
                "runtime_proof": "baseline /sys/kernel/notes with candidate image still on disk",
            },
            "gates": {
                "host_recovery_material_preflight_passed": True,
                "live_fastboot_unlock_verified": False,
                "independent_runtime_boot_observed": False,
                "rollback_artifact_prevalidated": False,
                "candidate_install_authorized": False,
            },
            "lock_sha256": self.lock_sha256,
        }

    def _dispatch(self) -> dict[str, object]:
        return {
            "schema": evaluate.dispatch.REPORT_SCHEMA,
            "action": "temporary_baseline_boot_dispatched",
            "device_access": True,
            "preflight": self.host["gates"],
            "artifact": self.lock["artifacts"]["baseline_rollback_boot"],
            "recovery_design": {
                "transport": "fastboot-ram-boot",
                "subcommand": "boot",
                "device_count": 1,
                "unlocked": True,
                "writes_partition": False,
                "device_identifier_recorded": False,
            },
            "gates": {
                "live_fastboot_unlock_verified": True,
                "temporary_baseline_boot_dispatched": True,
                "independent_runtime_boot_observed": False,
                "rollback_artifact_prevalidated": False,
                "candidate_install_authorized": False,
            },
            "lock_sha256": self.lock_sha256,
        }

    def _runtime(
        self, running: str, disk: str, boot_token: str, uptime: float, independent: bool,
        *, observed_at: int,
    ) -> dict[str, object]:
        runtime_identity = self.lock["runtime_identity"]
        artifact = (
            "baseline_rollback_boot" if disk == "baseline" else "candidate_runtime_boot"
        )
        disk_record = self.lock["artifacts"][artifact]
        return {
            "schema": "pocketds.kernel-fastboot-recovery-runtime-attestation.v2",
            "read_only": True,
            "network": False,
            "device_id": self.lock["device_id"],
            "runtime": {
                "expected_identity": running,
                "kernel_release": self.lock["kernel_release"],
                "machine_model": self.lock["machine_model"],
                "notes_sha256": runtime_identity[f"{running}_notes_sha256"],
                "build_id_hex": runtime_identity[f"{running}_build_id_hex"],
                "boot_token_sha256": boot_token,
                "uptime_seconds": uptime,
                "max_uptime_seconds": 900,
                "observed_at_unix_ns": observed_at,
                "boot_started_at_unix_ns": observed_at - int(uptime * 1_000_000_000),
            },
            "disk": {
                "expected_identity": disk,
                "sha256": disk_record["sha256"],
                "size": disk_record["size"],
                "aliases": [
                    {
                        "path_id": hashlib.sha256(
                            ("pds002-boot-alias-v1\x00" + relative).encode()
                        ).hexdigest(),
                        "sha256": disk_record["sha256"],
                        "size": disk_record["size"],
                    }
                    for relative in self.lock["disk_boot_aliases"]
                ],
            },
            "gates": {
                "machine_and_release_match": True,
                "running_kernel_build_id_matches": True,
                "disk_boot_aliases_match": True,
                "boot_is_within_recovery_window": True,
                "independent_runtime_boot_observed": independent,
                "rollback_artifact_prevalidated": False,
                "candidate_install_authorized": False,
            },
            "lock_sha256": self.lock_sha256,
        }

    def _static(self) -> dict[str, object]:
        rollback = self.rollback_lock
        return {
            "schema": evaluate.STATIC_SCHEMA,
            "read_only": True,
            "network": False,
            "artifact": {
                "artifact_id": rollback["artifact_id"],
                "device_id": rollback["device_id"],
                "kernel_release": rollback["kernel_release"],
                "source_commit": rollback["source_commit"],
                **rollback["artifacts"]["rollback_bootimg"],
            },
            "recovery_validation": {
                "status": "not_run",
                "successful_boots": 0,
                "successful_recovery_tests": 0,
                "independent_recovery_path": False,
            },
            "gates": {
                "rollback_artifact_content_locked": True,
                "baseline_source_to_binary_evidence_bound": True,
                "signed_and_reproduced_baseline_bound": True,
                "custom_dtb_source_and_rebuild_evidence_bound": True,
                "runtime_boot_composition_bound": True,
                "android_bootimg_structure_verified": True,
                "candidate_evidence_bound": True,
                "static_rollback_preflight_passed": True,
                "rollback_artifact_prevalidated": False,
                "candidate_install_authorized": False,
            },
            "lock_sha256": self.rollback_lock_sha256,
        }

    def collect(self, **changes: object) -> dict[str, object]:
        values = {
            "static_report": self.static,
            "host_report": self.host,
            "dispatch_report": self.dispatch,
            "ram_report": self.ram,
            "restored_report": self.restored,
            "normal_report": self.normal,
        }
        values.update(changes)
        return evaluate.collect(
            self.lock,
            lock_sha256=self.lock_sha256,
            rollback_lock=self.rollback_lock,
            rollback_lock_sha256=self.rollback_lock_sha256,
            evidence=evidence(),
            **values,
        )


class RecoveryEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def test_complete_sequence_prevalidates_rollback_but_not_candidate_install(self) -> None:
        report = self.fixture.collect()
        self.assertEqual(report["validation"]["status"], "passed")
        self.assertEqual(report["validation"]["successful_boots"], 1)
        self.assertEqual(report["validation"]["successful_recovery_tests"], 1)
        self.assertTrue(report["validation"]["independent_recovery_path"])
        self.assertTrue(report["gates"]["rollback_artifact_prevalidated"])
        self.assertFalse(report["gates"]["candidate_install_authorized"])

    def test_ram_boot_must_run_baseline_over_candidate_disk(self) -> None:
        changed = copy.deepcopy(self.fixture.ram)
        changed["disk"]["expected_identity"] = "baseline"
        with self.assertRaisesRegex(evaluate.EvaluationError, "identity binding differs"):
            self.fixture.collect(ram_report=changed)

    def test_restore_must_share_ram_boot_token_and_follow_observation(self) -> None:
        changed = copy.deepcopy(self.fixture.restored)
        changed["runtime"]["boot_token_sha256"] = token("different")
        with self.assertRaisesRegex(evaluate.EvaluationError, "boot-token"):
            self.fixture.collect(restored_report=changed)
        changed = copy.deepcopy(self.fixture.restored)
        changed["runtime"]["uptime_seconds"] = 20.0
        changed["runtime"]["observed_at_unix_ns"] = 990_000_000_000
        changed["runtime"]["boot_started_at_unix_ns"] = 970_000_000_000
        with self.assertRaisesRegex(evaluate.EvaluationError, "observation order"):
            self.fixture.collect(restored_report=changed)

    def test_normal_boot_must_have_a_new_boot_token(self) -> None:
        changed = copy.deepcopy(self.fixture.normal)
        changed["runtime"]["boot_token_sha256"] = token("ram-boot")
        with self.assertRaisesRegex(evaluate.EvaluationError, "boot-token"):
            self.fixture.collect(normal_report=changed)

    def test_normal_boot_must_start_after_the_restore_observation(self) -> None:
        changed = copy.deepcopy(self.fixture.normal)
        changed["runtime"]["observed_at_unix_ns"] = 990_000_000_000
        changed["runtime"]["boot_started_at_unix_ns"] = 970_000_000_000
        with self.assertRaisesRegex(evaluate.EvaluationError, "observation order"):
            self.fixture.collect(normal_report=changed)

    def test_stale_or_overclaiming_runtime_report_is_rejected(self) -> None:
        changed = copy.deepcopy(self.fixture.ram)
        changed["gates"]["boot_is_within_recovery_window"] = False
        with self.assertRaisesRegex(evaluate.EvaluationError, "gates differ"):
            self.fixture.collect(ram_report=changed)
        changed = copy.deepcopy(self.fixture.normal)
        changed["gates"]["candidate_install_authorized"] = True
        with self.assertRaisesRegex(evaluate.EvaluationError, "gates differ"):
            self.fixture.collect(normal_report=changed)

    def test_dispatch_must_be_unlocked_ram_only_and_partition_free(self) -> None:
        for key, value in (("unlocked", False), ("writes_partition", True)):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.fixture.dispatch)
                changed["recovery_design"][key] = value
                with self.assertRaisesRegex(evaluate.EvaluationError, "dispatch binding differs"):
                    self.fixture.collect(dispatch_report=changed)

    def test_static_preflight_must_remain_non_authorizing(self) -> None:
        changed = copy.deepcopy(self.fixture.static)
        changed["recovery_validation"]["status"] = "passed"
        with self.assertRaisesRegex(evaluate.EvaluationError, "static rollback"):
            self.fixture.collect(static_report=changed)

    def test_unknown_runtime_field_is_rejected(self) -> None:
        changed = copy.deepcopy(self.fixture.ram)
        changed["runtime"]["unknown"] = True
        with self.assertRaisesRegex(evaluate.EvaluationError, "fields differ"):
            self.fixture.collect(ram_report=changed)

    def test_private_report_reader_rejects_public_linked_and_hardlinked_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            root = Path(temporary)
            path = root / "report.json"
            path.write_text(json.dumps(self.fixture.host), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(evaluate.EvaluationError, "privacy metadata"):
                evaluate._private_report(path, evaluate.preflight.REPORT_SCHEMA)
            path.chmod(0o600)
            hard = root / "hard.json"
            os.link(path, hard)
            with self.assertRaisesRegex(evaluate.EvaluationError, "privacy metadata"):
                evaluate._private_report(path, evaluate.preflight.REPORT_SCHEMA)
            hard.unlink()
            linked = root / "linked.json"
            linked.symlink_to(path)
            with self.assertRaisesRegex(evaluate.EvaluationError, "unavailable or linked"):
                evaluate._private_report(linked, evaluate.preflight.REPORT_SCHEMA)

    def test_private_output_is_new_and_never_overwritten(self) -> None:
        report = self.fixture.collect()
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            output = Path(temporary) / "validation.json"
            evaluate.dispatch._safe_output(output, report)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            before = output.read_bytes()
            with self.assertRaisesRegex(evaluate.dispatch.DispatchError, "cannot create"):
                evaluate.dispatch._safe_output(output, report)
            self.assertEqual(output.read_bytes(), before)

    def test_cli_writes_one_private_validation_report_end_to_end(self) -> None:
        reports = {
            "static": self.fixture.static,
            "host": self.fixture.host,
            "dispatch": self.fixture.dispatch,
            "ram": self.fixture.ram,
            "restored": self.fixture.restored,
            "normal": self.fixture.normal,
        }
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            root = Path(temporary)
            paths: dict[str, Path] = {}
            for name, report in reports.items():
                path = root / f"{name}.json"
                path.write_text(json.dumps(report), encoding="utf-8")
                path.chmod(0o600)
                paths[name] = path
            output = root / "validation.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    os.fspath(SOURCE),
                    "--static-preflight", os.fspath(paths["static"]),
                    "--host-preflight", os.fspath(paths["host"]),
                    "--dispatch", os.fspath(paths["dispatch"]),
                    "--ram-attestation", os.fspath(paths["ram"]),
                    "--restored-attestation", os.fspath(paths["restored"]),
                    "--normal-boot-attestation", os.fspath(paths["normal"]),
                    "--output", os.fspath(output),
                ],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            stdout_report = json.loads(completed.stdout)
            disk_report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(stdout_report, disk_report)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertTrue(disk_report["gates"]["rollback_artifact_prevalidated"])
            self.assertFalse(disk_report["gates"]["candidate_install_authorized"])

    def test_cli_and_source_have_no_live_device_or_power_path(self) -> None:
        completed = subprocess.run(
            [sys.executable, os.fspath(SOURCE), "--help"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        for forbidden in ("--execute", "--fastboot", "--device", "--flash", "--reboot", "--install"):
            self.assertNotIn(forbidden, completed.stdout)
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("os.system", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
