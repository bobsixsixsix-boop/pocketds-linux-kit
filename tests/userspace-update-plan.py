#!/usr/bin/env python3
"""Offline tests for the fail-closed DNF5 stored-transaction evaluator."""

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
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pocketds-userspace-update-plan.py"
SPEC = importlib.util.spec_from_file_location("pocketds_userspace_update_plan", SOURCE)
assert SPEC and SPEC.loader
planner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = planner
SPEC.loader.exec_module(planner)


def inbound(
    name: str,
    *,
    version: str = "2.0",
    action: str = "Upgrade",
    reason: str = "User",
    filename: str | None = None,
) -> dict[str, str]:
    artifact = filename or f"{name}-{version}.aarch64.rpm"
    return {
        "nevra": f"{name}-0:{version}-1.fc44.aarch64",
        "action": action,
        "reason": reason,
        "repo_id": "@stored_transaction(updates)",
        "package_path": f"./packages/{artifact}",
    }


def replaced(name: str, *, version: str = "1.0") -> dict[str, str]:
    return {
        "nevra": f"{name}-0:{version}-1.fc44.aarch64",
        "action": "Replaced",
        "reason": "User",
        "repo_id": "@System",
    }


def safe_payload() -> dict[str, object]:
    return {
        "version": "1.0",
        "rpms": [
            inbound("bash", filename="bash-new.aarch64.rpm"),
            replaced("bash"),
            inbound(
                "libexample",
                version="1.1",
                action="Install",
                reason="Dependency",
                filename="libexample.aarch64.rpm",
            ),
        ],
    }


class StoredTransaction:
    def __init__(self, root: Path, payload: dict[str, object] | None = None) -> None:
        self.root = root
        self.packages = root / "packages"
        self.packages.mkdir(parents=True)
        self.root.chmod(0o700)
        self.packages.chmod(0o700)
        self.payload = copy.deepcopy(payload if payload is not None else safe_payload())
        self.materialize_payloads()
        self.write()

    def materialize_payloads(self) -> None:
        for index, record in enumerate(self.payload.get("rpms", [])):
            if not isinstance(record, dict):
                continue
            value = record.get("package_path")
            if not isinstance(value, str):
                continue
            relative = Path(value)
            if ".." in relative.parts or relative.is_absolute():
                continue
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"mock-rpm-{index}\n".encode())

    def write(self) -> None:
        (self.root / "transaction.json").write_text(
            json.dumps(self.payload, sort_keys=True), encoding="utf-8"
        )


def cli(path: Path, *, path_prefix: Path | None = None) -> tuple[int, dict[str, object]]:
    environment = os.environ.copy()
    if path_prefix is not None:
        environment["PATH"] = os.pathsep.join((os.fspath(path_prefix), environment["PATH"]))
    result = subprocess.run(
        [sys.executable, os.fspath(SOURCE), "--transaction", os.fspath(path)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.stderr:
        raise AssertionError(f"planner wrote unexpected stderr: {result.stderr}")
    return result.returncode, json.loads(result.stdout)


class UserspaceUpdatePlanTests(unittest.TestCase):
    def test_pinned_dnf5_omits_empty_optional_collections(self) -> None:
        payload = safe_payload()
        self.assertNotIn("groups", payload)
        self.assertNotIn("environments", payload)
        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            transaction = StoredTransaction(Path(temporary), payload)
            code, report = cli(transaction.root)
        self.assertEqual(code, 0)
        self.assertTrue(report["gates"]["transaction_schema_exact"])

    def test_safe_update_is_audited_but_never_authorized_or_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            transaction = StoredTransaction(Path(temporary))
            code, report = cli(transaction.root)
        self.assertEqual(code, 0)
        self.assertEqual(report["schema"], "pocketds.userspace-update-plan.v1")
        self.assertEqual(report["decision"], "AUDITED_NOT_RUN")
        self.assertTrue(report["accepted_for_userspace_review"])
        self.assertFalse(report["execution_authorized"])
        self.assertEqual(report["execution_state"], "NOT RUN")
        self.assertFalse(report["dnf5_invoked"])
        self.assertFalse(report["rpm_transaction_invoked"])
        self.assertEqual(report["counts"]["actions"], {"Install": 1, "Replaced": 1, "Upgrade": 1})
        self.assertTrue(report["gates"]["payload_files_hashed"])
        self.assertTrue(report["gates"]["source_tree_stable"])
        self.assertTrue(report["gates"]["update_shape_valid"])
        for gate in (
            "generator_version_verified",
            "vendor_change_verified",
            "rpm_signatures_verified",
            "nevra_payload_binding_verified",
            "disk_space_verified",
            "fresh_solver_state_verified",
            "post_update_health_verified",
        ):
            self.assertFalse(report["gates"][gate])
        hashes = [
            record["package_sha256"]
            for record in report["packages"]
            if record["action"] in ("Install", "Upgrade")
        ]
        self.assertTrue(all(isinstance(value, str) and len(value) == 64 for value in hashes))
        self.assertTrue(report["unresolved_execution_gates"])

    def test_empty_transaction_is_a_safe_nothing_to_do_audit(self) -> None:
        payload = {"version": "1.0"}
        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            transaction = StoredTransaction(Path(temporary), payload)
            code, report = cli(transaction.root)
        self.assertEqual(code, 0)
        self.assertTrue(report["accepted_for_userspace_review"])
        self.assertEqual(report["counts"]["packages"], 0)
        self.assertFalse(report["execution_authorized"])

    def test_every_protected_ring_is_rejected(self) -> None:
        samples = {
            "kernel": "hardware",
            "kernel-tools": "hardware",
            "pocketds-panel": "hardware",
            "linux-firmware": "hardware",
            "qcom-firmware": "hardware",
            "mesa-dri-drivers": "graphics",
            "libdrm": "graphics",
            "kwin": "graphics",
            "plasma-workspace": "graphics",
            "qt6-qtwayland": "graphics",
            "xorg-x11-server-Xwayland": "graphics",
            "vulkan-loader": "graphics",
            "grub2-common": "boot",
            "dracut": "boot",
            "shim-aa64": "boot",
            "systemd": "boot",
        }
        for name, expected_ring in samples.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory(
                prefix="pds-update-plan-"
            ) as temporary:
                payload = {
                    "version": "1.0",
                    "rpms": [inbound(name, filename=f"{name}.rpm"), replaced(name)],
                }
                transaction = StoredTransaction(Path(temporary), payload)
                code, report = cli(transaction.root)
                self.assertEqual(code, 3)
                self.assertFalse(report["accepted_for_userspace_review"])
                self.assertFalse(report["gates"]["only_userspace_packages"])
                rings = {
                    match["ring"]
                    for package in report["protected_packages"]
                    for match in package["matches"]
                }
                self.assertIn(expected_ring, rings)
                self.assertFalse(report["execution_authorized"])

    def test_remove_downgrade_reinstall_and_reason_change_are_rejected(self) -> None:
        for action in ("Remove", "Downgrade", "Reinstall", "Reason Change"):
            with self.subTest(action=action), tempfile.TemporaryDirectory(
                prefix="pds-update-plan-"
            ) as temporary:
                payload = {
                    "version": "1.0",
                    "rpms": [
                        {
                            "nevra": "bash-0:1.0-1.fc44.aarch64",
                            "action": action,
                            "reason": "User",
                            "repo_id": "@System",
                        }
                    ],
                }
                transaction = StoredTransaction(Path(temporary), payload)
                code, report = cli(transaction.root)
                self.assertEqual(code, 3)
                self.assertFalse(report["gates"]["only_update_actions"])
                self.assertTrue(any(action in reason for reason in report["rejection_reasons"]))

    def test_user_requested_install_and_unpaired_upgrade_are_rejected(self) -> None:
        variants = []
        requested_install = safe_payload()
        requested_install["rpms"][2]["reason"] = "User"
        variants.append(requested_install)
        variants.append(
            {
                "version": "1.0",
                "rpms": [inbound("bash", filename="bash.rpm")],
            }
        )
        for payload in variants:
            with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
                transaction = StoredTransaction(Path(temporary), payload)
                code, report = cli(transaction.root)
            self.assertEqual(code, 3)
            self.assertFalse(report["gates"]["update_shape_valid"])

    def test_groups_environments_schema_drift_and_unknown_fields_fail_closed(self) -> None:
        variants = []
        group = safe_payload()
        group["groups"] = [{"group_id": "workstation", "action": "Install"}]
        variants.append(group)
        environment = safe_payload()
        environment["environments"] = [{"environment_id": "workstation"}]
        variants.append(environment)
        version = safe_payload()
        version["version"] = "1.1"
        variants.append(version)
        top_extra = safe_payload()
        top_extra["helpful_extra"] = True
        variants.append(top_extra)
        rpm_extra = safe_payload()
        rpm_extra["rpms"][0]["vendor"] = "Fedora"
        variants.append(rpm_extra)
        for collection in ("rpms", "groups", "environments"):
            explicit_empty = safe_payload()
            explicit_empty[collection] = []
            variants.append(explicit_empty)
        for payload in variants:
            with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
                transaction = StoredTransaction(Path(temporary), payload)
                code, report = cli(transaction.root)
            self.assertEqual(code, 3)
            self.assertEqual(report["decision"], "REJECTED_NOT_RUN")
            self.assertFalse(report["execution_authorized"])

    def test_dnf4_history_store_and_human_output_are_outside_the_parser_boundary(self) -> None:
        legacy_dnf4 = {
            "version": "1.0",
            "rpms": [
                {
                    "nevra": "bash-0:2.0-1.fc44.aarch64",
                    "action": "Upgrade",
                    "reason": "user",
                    "repo_id": "updates",
                },
                {
                    "nevra": "bash-0:1.0-1.fc44.aarch64",
                    "action": "Upgraded",
                    "reason": "user",
                    "repo_id": "@System",
                },
            ],
        }
        dnf5_history_store = {
            "version": "1.0",
            "rpms": [
                {
                    "nevra": "bash-0:2.0-1.fc44.aarch64",
                    "action": "Upgrade",
                    "reason": "User",
                    "repo_id": "updates",
                },
                replaced("bash"),
            ],
        }
        for payload in (legacy_dnf4, dnf5_history_store):
            with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
                transaction = StoredTransaction(Path(temporary), payload)
                code, report = cli(transaction.root)
            self.assertEqual(code, 3)
            self.assertEqual(report["execution_state"], "NOT RUN")
            self.assertFalse(report["execution_authorized"])

        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            root = Path(temporary)
            (root / "transaction.json").write_text(
                "Dependencies resolved.\nUpgrade 2 Packages\nIs this ok [y/N]: N\n",
                encoding="utf-8",
            )
            code, report = cli(root)
        self.assertEqual(code, 3)
        self.assertEqual(report["decision"], "REJECTED_NOT_RUN")
        self.assertEqual(report["execution_state"], "NOT RUN")

    def test_only_current_stored_repository_identity_is_accepted(self) -> None:
        for repo_id in (
            "@@stored_transaction(updates)",
            "updates",
            "@System",
            "@stored_transaction(updates)/escape",
        ):
            with self.subTest(repo_id=repo_id), tempfile.TemporaryDirectory(
                prefix="pds-update-plan-"
            ) as temporary:
                payload = safe_payload()
                payload["rpms"][0]["repo_id"] = repo_id
                transaction = StoredTransaction(Path(temporary), payload)
                code, report = cli(transaction.root)
            self.assertEqual(code, 3)
            self.assertTrue(
                any("official --store repository id" in value for value in report["rejection_reasons"])
            )

    def test_duplicate_key_and_nonfinite_json_fail_closed(self) -> None:
        documents = (
            b'{"version":"1.0","version":"1.0","rpms":[]}',
            b'{"version":"1.0","rpms":[],"groups":[],"environments":[],"bad":NaN}',
        )
        for document in documents:
            with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
                root = Path(temporary)
                (root / "packages").mkdir()
                (root / "packages").chmod(0o700)
                (root / "transaction.json").write_bytes(document)
                code, report = cli(root)
            self.assertEqual(code, 3)
            self.assertFalse(report["gates"]["transaction_schema_exact"])
            self.assertIn("policy", report)
            self.assertEqual(
                report["source"]["transaction_json_sha256"],
                hashlib.sha256(document).hexdigest(),
            )

    def test_missing_symlink_and_traversal_payloads_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            transaction = StoredTransaction(Path(temporary))
            (transaction.root / "packages/bash-new.aarch64.rpm").unlink()
            code, report = cli(transaction.root)
            self.assertEqual(code, 3)
            self.assertFalse(report["gates"]["payload_files_hashed"])
            self.assertEqual(report["execution_state"], "NOT RUN")

        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            transaction = StoredTransaction(Path(temporary))
            target = transaction.root / "packages/bash-new.aarch64.rpm"
            target.unlink()
            target.symlink_to(transaction.root / "packages/libexample.aarch64.rpm")
            code, report = cli(transaction.root)
            self.assertEqual(code, 3)
            self.assertFalse(report["gates"]["payload_files_hashed"])
            self.assertEqual(report["execution_state"], "NOT RUN")

        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            payload = safe_payload()
            payload["rpms"][0]["package_path"] = "packages/../escape.rpm"
            transaction = StoredTransaction(Path(temporary), payload)
            code, report = cli(transaction.root)
            self.assertEqual(code, 3)
            self.assertFalse(report["gates"]["payload_files_hashed"])
            self.assertEqual(report["execution_state"], "NOT RUN")

    def test_world_readable_transaction_or_packages_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            transaction = StoredTransaction(Path(temporary))
            transaction.root.chmod(0o755)
            code, report = cli(transaction.root)
        self.assertEqual(code, 3)
        self.assertEqual(report["execution_state"], "NOT RUN")
        self.assertTrue(
            any("owner-only" in reason for reason in report["rejection_reasons"])
        )

        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            transaction = StoredTransaction(Path(temporary))
            transaction.packages.chmod(0o755)
            code, report = cli(transaction.root)
        self.assertEqual(code, 3)
        self.assertEqual(report["execution_state"], "NOT RUN")
        self.assertTrue(
            any("owner-only" in reason for reason in report["rejection_reasons"])
        )

    def test_packages_directory_mtime_change_during_audit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            transaction = StoredTransaction(Path(temporary))
            original = planner.TransactionSource.hash_package
            changed = False

            def mutate_after_hash(
                source: object, filename: str, *, maximum: int
            ) -> tuple[str, int]:
                nonlocal changed
                result = original(source, filename, maximum=maximum)
                if not changed:
                    changed = True
                    marker = transaction.packages / "created-during-audit"
                    marker.write_bytes(b"mutation\n")
                return result

            with mock.patch.object(
                planner.TransactionSource, "hash_package", mutate_after_hash
            ):
                report = planner.audit_path(transaction.root)
        self.assertFalse(report["accepted_for_userspace_review"])
        self.assertEqual(report["execution_state"], "NOT RUN")
        self.assertFalse(report["gates"]["source_tree_stable"])
        self.assertTrue(
            any(
                "stored packages directory changed during audit" in reason
                for reason in report["rejection_reasons"]
            )
        )

    def test_transaction_directory_replacement_during_audit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            base = Path(temporary)
            transaction = StoredTransaction(base / "transaction")
            original = planner.TransactionSource.hash_package
            changed = False

            def replace_after_hash(
                source: object, filename: str, *, maximum: int
            ) -> tuple[str, int]:
                nonlocal changed
                result = original(source, filename, maximum=maximum)
                if not changed:
                    changed = True
                    transaction.root.rename(base / "original-transaction")
                    transaction.root.mkdir(mode=0o700)
                return result

            with mock.patch.object(
                planner.TransactionSource, "hash_package", replace_after_hash
            ):
                report = planner.audit_path(transaction.root)
        self.assertFalse(report["accepted_for_userspace_review"])
        self.assertEqual(report["execution_state"], "NOT RUN")
        self.assertFalse(report["gates"]["source_tree_stable"])
        self.assertTrue(
            any(
                "transaction directory" in reason and "during audit" in reason
                for reason in report["rejection_reasons"]
            )
        )

    def test_planner_never_invokes_dnf5_or_rpm_even_when_they_are_on_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            base = Path(temporary)
            transaction = StoredTransaction(base / "transaction")
            mock_bin = base / "bin"
            mock_bin.mkdir()
            marker = base / "package-manager-was-invoked"
            for command in ("dnf5", "rpm"):
                executable = mock_bin / command
                executable.write_text(
                    f"#!/bin/sh\nprintf invoked > {marker!s}\nexit 99\n", encoding="utf-8"
                )
                executable.chmod(0o755)
            code, report = cli(transaction.root, path_prefix=mock_bin)
            self.assertEqual(code, 0)
            self.assertFalse(marker.exists())
            self.assertFalse(report["dnf5_invoked"])
            self.assertFalse(report["rpm_transaction_invoked"])

    def test_command_line_error_is_also_one_json_document(self) -> None:
        result = subprocess.run(
            [sys.executable, os.fspath(SOURCE)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(report["decision"], "REJECTED_NOT_RUN")
        self.assertFalse(report["execution_authorized"])

    def test_report_binds_exact_transaction_and_payload_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds-update-plan-") as temporary:
            transaction = StoredTransaction(Path(temporary))
            document = (transaction.root / "transaction.json").read_bytes()
            rpm = transaction.root / "packages/bash-new.aarch64.rpm"
            code, report = cli(transaction.root)
            self.assertEqual(code, 0)
            self.assertEqual(
                report["source"]["transaction_json_sha256"], hashlib.sha256(document).hexdigest()
            )
            bash = next(
                record
                for record in report["packages"]
                if record["name"] == "bash" and record["action"] == "Upgrade"
            )
            self.assertEqual(bash["package_sha256"], hashlib.sha256(rpm.read_bytes()).hexdigest())

    def test_source_has_no_execution_or_network_capability(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "import subprocess",
            "os.system",
            "os.exec",
            "os.spawn",
            "ctypes",
            "urllib",
            "requests",
            "socket",
            "dnf5 replay",
            "dnf5 upgrade",
            "rpm -",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("apply", source.casefold())


if __name__ == "__main__":
    unittest.main(verbosity=2)
