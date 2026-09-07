#!/usr/bin/env python3
"""Tests for the non-flashable Fedora rootfs composition preflight."""

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
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-composition-preflight.py"
LOCK = ROOT / "packaging/image/composition-lock.json"
BLUEPRINT = ROOT / "packaging/image/pocketds-rootfs.template.toml"
SPDX_LOCK = ROOT / "packaging/image/spdx-validation-lock.json"
ROOTFS_LOCK = (
    ROOT / "packaging/pocketds-userspace/rootfs-transaction-lock.pds2.json"
)
BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.pds2.json"
SPEC = importlib.util.spec_from_file_location("pds020_composition_preflight", SOURCE)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


def repository_smoke_evidence() -> dict[str, object]:
    return {
        "schema": 1,
        "completed": True,
        "accepted": True,
        "repository_receipt_sha256": "1" * 64,
        "repository_repomd_sha256": "2" * 64,
        "repository_signature_sha256": "3" * 64,
        "project_rpm_sha256": "4" * 64,
        "final_archive_records_sha256": "5" * 64,
        "offline_file_repository_only": True,
        "dnf5_nvr": "dnf5-5.4.1.0-1.fc44.aarch64",
        "repo_gpgcheck": True,
        "package_gpgcheck": True,
        "skip_if_unavailable": False,
        "available": {
            "package_count": 322,
            "package_manifest_sha256": "6" * 64,
        },
        "installed_package_count": 322,
        "installed_manifest_sha256": "7" * 64,
        "materialized_file_count": 327,
        "dependency_check": "pass",
        "scriptlets_executed": True,
        "triggers_executed": True,
        "disposable_root_cleaned": True,
        "device_root_touched": False,
        "services_started": False,
        "selinux_labels_verified": False,
        "release_ready": False,
    }


def mounted_root_audit_report() -> dict[str, object]:
    return {
        "schema": 1,
        "read_only": True,
        "standard": {
            "sbom": "SPDX-3.0.1-JSON-LD",
            "sbom_validation": "structural-preflight-only",
        },
        "checks": [
            {
                "check_id": check_id,
                "status": "pass",
                "summary": f"fixture {check_id} passed",
                "evidence": {},
            }
            for check_id in sorted(preflight.RELEASE_AUDIT_CHECK_IDS)
        ],
        "blockers": [],
        "release_ready": True,
        "privacy": {"paths_disclosed": False},
        "root_mode": "offline-mounted-root",
    }


def spdx_offline_report(
    sbom_sha256: str, validation_lock: dict[str, object]
) -> dict[str, object]:
    return {
        "schema": 1,
        "verified": True,
        "offline": True,
        "spec_version": "3.0.1",
        "sbom_sha256": sbom_sha256,
        "official_artifacts": {
            name: record["sha256"]
            for name, record in validation_lock["artifacts"].items()
        },
        "validator_packages": validation_lock["validator"],
        "json_schema_valid": True,
        "owl_shacl_valid": True,
        "rdf_triple_count": 26,
        "semantic_result_triple_count": 2,
        "legal_conclusion": "NOT_DETERMINED",
        "rights_or_redistribution_inferred": False,
        "release_ready": False,
    }


class Fixture:
    def __init__(self, base: Path) -> None:
        self.root = base / "repository"
        self.image_dir = self.root / "packaging" / "image"
        self.image_dir.mkdir(parents=True)
        self.blueprint = self.image_dir / BLUEPRINT.name
        self.blueprint.write_bytes(BLUEPRINT.read_bytes())
        self.lock = json.loads(LOCK.read_text(encoding="utf-8"))
        self.lock_path = self.image_dir / LOCK.name
        self.write_lock()

    def write_lock(self) -> None:
        self.lock_path.write_text(json.dumps(self.lock), encoding="utf-8")

    def relock_blueprint(self) -> None:
        data = self.blueprint.read_bytes()
        record = self.lock["blueprint"]
        record["size"] = len(data)
        record["sha256"] = hashlib.sha256(data).hexdigest()
        self.write_lock()

    def make_release_evidence(self, evidence_dir: Path) -> dict[str, object]:
        evidence_content = {
            "LICENSE": b"fixture license evidence\n",
            "NOTICE": b"fixture notice evidence\n",
            "THIRD-PARTY.json": b'{"schema":1,"components":[]}\n',
            "SBOM.spdx.json": b'{"fixture":"content-bound"}\n',
            "components/assets/ASSETS.json": (
                b'{"schema":1,"profile":"asset-free","assets":[]}\n'
            ),
        }
        evidence_hashes: dict[str, str] = {}
        for relative, data in evidence_content.items():
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            evidence_hashes[relative] = hashlib.sha256(data).hexdigest()

        spdx_lock_data = SPDX_LOCK.read_bytes()
        (self.image_dir / SPDX_LOCK.name).write_bytes(spdx_lock_data)
        validation_lock = json.loads(spdx_lock_data)
        audit_data = (
            json.dumps(mounted_root_audit_report(), sort_keys=True) + "\n"
        ).encode()
        spdx_data = (
            json.dumps(
                spdx_offline_report(
                    evidence_hashes["SBOM.spdx.json"], validation_lock
                ),
                sort_keys=True,
            )
            + "\n"
        ).encode()
        reports = {
            "mounted-root-audit.json": audit_data,
            "spdx-offline-validation.json": spdx_data,
        }
        references: dict[str, dict[str, str]] = {}
        for filename, data in reports.items():
            path = evidence_dir / filename
            path.write_bytes(data)
            references[filename] = {
                "path": path.relative_to(self.root).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        return {
            "schema": 1,
            "completed": True,
            "accepted": True,
            "repository_evidence_sha256": evidence_hashes,
            "mounted_root_audit": references["mounted-root-audit.json"],
            "spdx_offline_validation": references[
                "spdx-offline-validation.json"
            ],
            "spdx_validation_lock_sha256": hashlib.sha256(
                spdx_lock_data
            ).hexdigest(),
            "release_ready": False,
        }

    def make_rootfs_smoke_evidence(self) -> dict[str, object]:
        lock_dir = self.root / "packaging/pocketds-userspace"
        lock_dir.mkdir(parents=True, exist_ok=True)
        rootfs_lock_data = ROOTFS_LOCK.read_bytes()
        build_lock_data = BUILD_LOCK.read_bytes()
        (lock_dir / ROOTFS_LOCK.name).write_bytes(rootfs_lock_data)
        (lock_dir / BUILD_LOCK.name).write_bytes(build_lock_data)
        rootfs_lock = json.loads(rootfs_lock_data)
        policy_sha256 = preflight.canonical_sha256(
            {
                "builder": self.lock["builder"],
                "boundary": self.lock["boundary"],
                "blueprint": self.lock["blueprint"],
                "identity_policy": self.lock["identity_policy"],
            }
        )
        return {
            "schema": 1,
            "completed": True,
            "accepted": True,
            "artifact": {
                "role": "osbuild-generic-container-tar",
                "size": 4096,
                "sha256": "8" * 64,
            },
            "composition_policy_sha256": policy_sha256,
            "rootfs_lock_sha256": hashlib.sha256(rootfs_lock_data).hexdigest(),
            "build_lock_sha256": hashlib.sha256(build_lock_data).hexdigest(),
            "os_release": {"id": "fedora", "version_id": "44"},
            "architecture": "aarch64",
            "package_count": rootfs_lock["installed_stage"]["package_count"],
            "package_manifest_sha256": rootfs_lock["installed_stage"][
                "package_manifest_sha256"
            ],
            "installed_contract": "pass",
            "selinux_label_count": 3,
            "selinux_labels_verified": True,
            "mounted_root_audit_sha256": "9" * 64,
            "mounted_root_check_count": len(preflight.RELEASE_AUDIT_CHECK_IDS),
            "mounted_root_audit_passed": True,
            "network": False,
            "services_started": False,
            "device_root_touched": False,
            "release_ready": False,
        }

    def make_ready(self) -> None:
        project_version = "0.1.0-1.pds1.fc44"
        text = self.blueprint.read_text(encoding="utf-8").replace(
            "__PIN_REQUIRED__", project_version
        )
        self.blueprint.write_text(text, encoding="utf-8")
        self.lock["blueprint"]["packages"][0]["version"] = project_version
        self.relock_blueprint()
        evidence_dir = self.image_dir / "evidence"
        evidence_dir.mkdir()
        for index, gate in enumerate(sorted(self.lock["gates"]), start=1):
            if gate == preflight.REPOSITORY_GATE:
                evidence = repository_smoke_evidence()
            elif gate == preflight.RELEASE_EVIDENCE_GATE:
                evidence = self.make_release_evidence(evidence_dir)
            elif gate == preflight.ROOTFS_SMOKE_GATE:
                evidence = self.make_rootfs_smoke_evidence()
            else:
                evidence = {
                    "schema": 1,
                    "gate": gate,
                    "verified": True,
                    "subject_sha256": f"{index:064x}",
                }
            data = (json.dumps(evidence, sort_keys=True) + "\n").encode()
            relative = Path("packaging") / "image" / "evidence" / f"{gate}.json"
            (self.root / relative).write_bytes(data)
            self.lock["gates"][gate] = {
                "passed": True,
                "evidence": relative.as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        self.relock_blueprint()


class PreflightTests(unittest.TestCase):
    def test_repository_template_is_explicitly_blocked(self):
        report = preflight.validate(LOCK, ROOT)
        self.assertFalse(report["release_ready"])
        self.assertFalse(report["modifies_boot_chain"])
        self.assertIn("package_version_pins_complete", report["blockers"])
        self.assertEqual(report["artifact_role"], "rootfs-staging-only")

    def test_complete_content_pinned_fixture_passes(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.make_ready()
            report = preflight.validate(fixture.lock_path, fixture.root)
            self.assertTrue(report["release_ready"])
            self.assertEqual(report["blockers"], [])
            self.assertTrue(report["read_only"])
            self.assertFalse(report["network"])
            self.assertFalse(report["build"])

    def test_blueprint_tamper_fails_before_semantic_parse(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.blueprint.write_bytes(fixture.blueprint.read_bytes() + b"\n")
            with self.assertRaisesRegex(preflight.PreflightError, "size mismatch"):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_relocked_identity_field_is_still_forbidden(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            text = fixture.blueprint.read_text(encoding="utf-8").replace(
                'distro = "fedora-44"\n',
                'distro = "fedora-44"\nhostname = "cloned-device"\n',
            )
            fixture.blueprint.write_text(text, encoding="utf-8")
            fixture.relock_blueprint()
            with self.assertRaisesRegex(preflight.PreflightError, "forbidden identity"):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_duplicate_package_fails_even_when_relocked(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            addition = '\n[[packages]]\nname = "pocketds-userspace"\nversion = "1.fc44"\n'
            fixture.blueprint.write_text(
                fixture.blueprint.read_text(encoding="utf-8") + addition,
                encoding="utf-8",
            )
            fixture.lock["blueprint"]["packages"].append(
                {"name": "pocketds-userspace", "version": "1.fc44"}
            )
            fixture.relock_blueprint()
            with self.assertRaisesRegex(preflight.PreflightError, "duplicate packages"):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_boot_or_partition_boundary_cannot_be_enabled(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.lock["boundary"]["flashable"] = True
            fixture.write_lock()
            with self.assertRaisesRegex(preflight.PreflightError, "safety boundary"):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_builder_tag_or_wrong_target_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.lock["builder"]["reference"] = (
                "ghcr.io/osbuild/image-builder-cli:latest"
            )
            fixture.write_lock()
            with self.assertRaisesRegex(preflight.PreflightError, "non-bootable Fedora"):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_true_gate_requires_unique_content_hashed_evidence(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.lock["gates"]["builder_digest_verified"] = {
                "passed": True,
                "evidence": "packaging/image/evidence/missing.json",
                "sha256": "1" * 64,
            }
            fixture.write_lock()
            with self.assertRaisesRegex(preflight.PreflightError, "missing or unsafe"):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_repository_gate_rejects_generic_placeholder_evidence(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.make_ready()
            gate = preflight.REPOSITORY_GATE
            record = fixture.lock["gates"][gate]
            evidence = fixture.root / record["evidence"]
            data = (
                json.dumps(
                    {
                        "schema": 1,
                        "gate": gate,
                        "verified": True,
                        "subject_sha256": "1" * 64,
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            evidence.write_bytes(data)
            record["sha256"] = hashlib.sha256(data).hexdigest()
            fixture.write_lock()
            with self.assertRaisesRegex(preflight.PreflightError, "smoke evidence"):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_release_gate_rejects_generic_placeholder_evidence(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.make_ready()
            gate = preflight.RELEASE_EVIDENCE_GATE
            record = fixture.lock["gates"][gate]
            evidence = fixture.root / record["evidence"]
            data = (
                json.dumps(
                    {
                        "schema": 1,
                        "gate": gate,
                        "verified": True,
                        "subject_sha256": "1" * 64,
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            evidence.write_bytes(data)
            record["sha256"] = hashlib.sha256(data).hexdigest()
            fixture.write_lock()
            with self.assertRaisesRegex(
                preflight.PreflightError, "release evidence receipt"
            ):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_release_gate_binds_files_audit_and_offline_spdx_report(self):
        for mutation, error in (
            ("sbom", "release evidence file SHA-256 mismatch"),
            ("audit", "audit check does not pass"),
            ("spdx", "SPDX offline report does not prove"),
        ):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory(
                    prefix="pds020-composition-"
                ) as name:
                    fixture = Fixture(Path(name))
                    fixture.make_ready()
                    gate_record = fixture.lock["gates"][
                        preflight.RELEASE_EVIDENCE_GATE
                    ]
                    receipt_path = fixture.root / gate_record["evidence"]
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    if mutation == "sbom":
                        (fixture.root / "SBOM.spdx.json").write_bytes(b"changed\n")
                    else:
                        reference_name = (
                            "mounted_root_audit"
                            if mutation == "audit"
                            else "spdx_offline_validation"
                        )
                        report_path = fixture.root / receipt[reference_name]["path"]
                        report = json.loads(report_path.read_text(encoding="utf-8"))
                        if mutation == "audit":
                            report["checks"][0]["status"] = "fail"
                        else:
                            report["official_artifacts"]["context"] = "9" * 64
                        report_data = (
                            json.dumps(report, sort_keys=True) + "\n"
                        ).encode()
                        report_path.write_bytes(report_data)
                        receipt[reference_name]["sha256"] = hashlib.sha256(
                            report_data
                        ).hexdigest()
                        receipt_data = (
                            json.dumps(receipt, sort_keys=True) + "\n"
                        ).encode()
                        receipt_path.write_bytes(receipt_data)
                        gate_record["sha256"] = hashlib.sha256(
                            receipt_data
                        ).hexdigest()
                        fixture.write_lock()
                    with self.assertRaisesRegex(preflight.PreflightError, error):
                        preflight.validate(fixture.lock_path, fixture.root)

    def test_rootfs_smoke_gate_rejects_generic_placeholder_evidence(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.make_ready()
            gate = preflight.ROOTFS_SMOKE_GATE
            record = fixture.lock["gates"][gate]
            evidence = fixture.root / record["evidence"]
            data = (
                json.dumps(
                    {
                        "schema": 1,
                        "gate": gate,
                        "verified": True,
                        "subject_sha256": "1" * 64,
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode()
            evidence.write_bytes(data)
            record["sha256"] = hashlib.sha256(data).hexdigest()
            fixture.write_lock()
            with self.assertRaisesRegex(preflight.PreflightError, "rootfs smoke"):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_rootfs_smoke_gate_binds_policy_manifest_and_selinux(self):
        for field, value in (
            ("composition_policy_sha256", "7" * 64),
            ("package_manifest_sha256", "7" * 64),
            ("selinux_labels_verified", False),
            ("mounted_root_check_count", 10),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(
                    prefix="pds020-composition-"
                ) as name:
                    fixture = Fixture(Path(name))
                    fixture.make_ready()
                    gate = preflight.ROOTFS_SMOKE_GATE
                    record = fixture.lock["gates"][gate]
                    evidence_path = fixture.root / record["evidence"]
                    evidence = json.loads(
                        evidence_path.read_text(encoding="utf-8")
                    )
                    evidence[field] = value
                    data = (
                        json.dumps(evidence, sort_keys=True) + "\n"
                    ).encode()
                    evidence_path.write_bytes(data)
                    record["sha256"] = hashlib.sha256(data).hexdigest()
                    fixture.write_lock()
                    with self.assertRaisesRegex(
                        preflight.PreflightError,
                        "rootfs smoke evidence does not prove",
                    ):
                        preflight.validate(fixture.lock_path, fixture.root)

    def test_repository_smoke_policy_drift_fails_even_when_relocked(self):
        for field, value in (
            ("repo_gpgcheck", False),
            ("package_gpgcheck", False),
            ("skip_if_unavailable", True),
            ("installed_package_count", 321),
            ("materialized_file_count", 326),
            ("device_root_touched", True),
            ("release_ready", True),
        ):
            with self.subTest(field=field):
                evidence = repository_smoke_evidence()
                evidence[field] = value
                with self.assertRaisesRegex(
                    preflight.PreflightError, "does not prove the locked gate"
                ):
                    preflight.validate_repository_smoke(evidence)

    def test_duplicate_lock_and_evidence_keys_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            data = fixture.lock_path.read_text(encoding="utf-8")
            fixture.lock_path.write_text(
                data.replace(
                    '"flashable": false',
                    '"flashable": true, "flashable": false',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(preflight.PreflightError, "duplicate key"):
                preflight.validate(fixture.lock_path, fixture.root)

        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.make_ready()
            gate = "builder_digest_verified"
            evidence = fixture.root / fixture.lock["gates"][gate]["evidence"]
            data = evidence.read_text(encoding="utf-8").replace(
                '"verified": true',
                '"verified": false, "verified": true',
                1,
            )
            evidence.write_text(data, encoding="utf-8")
            fixture.lock["gates"][gate]["sha256"] = hashlib.sha256(
                data.encode("utf-8")
            ).hexdigest()
            fixture.write_lock()
            with self.assertRaisesRegex(preflight.PreflightError, "duplicate key"):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_non_finite_json_number_is_not_accepted(self):
        for value in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(preflight.PreflightError, "non-finite"):
                    preflight.strict_json(b'{"value":' + value + b"}", "fixture")

    def test_json_encoding_types_and_depth_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.lock_path.write_bytes(
                json.dumps(fixture.lock).encode("utf-16")
            )
            with self.assertRaisesRegex(preflight.PreflightError, "invalid JSON"):
                preflight.validate(fixture.lock_path, fixture.root)

        for transform, error in (
            (
                lambda value: json.dumps({**value, "schema": True}).encode(),
                "does not verify",
            ),
            (lambda value: json.dumps(value).encode("utf-16"), "invalid JSON"),
        ):
            with self.subTest(evidence_error=error):
                with tempfile.TemporaryDirectory(
                    prefix="pds020-composition-"
                ) as name:
                    fixture = Fixture(Path(name))
                    fixture.make_ready()
                    gate = "builder_digest_verified"
                    record = fixture.lock["gates"][gate]
                    evidence = fixture.root / record["evidence"]
                    value = json.loads(evidence.read_text(encoding="utf-8"))
                    data = transform(value)
                    evidence.write_bytes(data)
                    record["sha256"] = hashlib.sha256(data).hexdigest()
                    fixture.write_lock()
                    with self.assertRaisesRegex(preflight.PreflightError, error):
                        preflight.validate(fixture.lock_path, fixture.root)

        for field, value, error in (
            ("schema", True, "schema"),
            ("boundary.flashable", 0, "safety boundary"),
            ("blueprint.size", True, "size mismatch"),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(
                    prefix="pds020-composition-"
                ) as name:
                    fixture = Fixture(Path(name))
                    if field == "schema":
                        fixture.lock["schema"] = value
                    elif field == "boundary.flashable":
                        fixture.lock["boundary"]["flashable"] = value
                    else:
                        fixture.lock["blueprint"]["size"] = value
                    fixture.write_lock()
                    with self.assertRaisesRegex(preflight.PreflightError, error):
                        preflight.validate(fixture.lock_path, fixture.root)

        with mock.patch.object(
            preflight.json,
            "loads",
            side_effect=RecursionError("synthetic depth limit"),
        ):
            with self.assertRaisesRegex(preflight.PreflightError, "invalid JSON"):
                preflight.strict_json(b"[]", "deep fixture")

    def test_symlinked_evidence_parent_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="pds020-composition-") as name:
            fixture = Fixture(Path(name))
            fixture.make_ready()
            real_evidence = fixture.image_dir / "evidence"
            moved_evidence = Path(name) / "outside-evidence"
            real_evidence.rename(moved_evidence)
            os.symlink(moved_evidence, real_evidence)
            with self.assertRaisesRegex(preflight.PreflightError, "parent.*unsafe"):
                preflight.validate(fixture.lock_path, fixture.root)

    def test_cli_reports_categories_without_building(self):
        result = subprocess.run(
            [sys.executable, str(SOURCE)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["release_ready"])
        self.assertNotIn(str(ROOT), result.stdout)
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "urllib.request",
            "requests",
            "socket",
            "os.remove",
            "os.unlink",
            "shutil",
            "shell=True",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
