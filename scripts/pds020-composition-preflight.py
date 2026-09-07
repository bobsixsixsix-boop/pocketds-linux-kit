#!/usr/bin/env python3
"""Fail-closed preflight for the non-flashable Fedora rootfs composition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Sequence
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "packaging/image/composition-lock.json"
BLUEPRINT_RELATIVE = PurePosixPath("packaging/image/pocketds-rootfs.template.toml")
BUILDER_SOURCE = (
    "https://osbuild.org/docs/user-guide/image-descriptions/"
    "fedora-44/generic-container/"
)
MAX_INPUT_BYTES = 1024 * 1024
MAX_SBOM_BYTES = 64 * 1024 * 1024
MAX_ROOTFS_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_~-]*$")
ASSIGNMENT_RE = re.compile(r'^([a-z_]+) = ("(?:[^"\\]|\\.)*")$')
FORBIDDEN_BLUEPRINT_TOKENS = (
    "password",
    "sshkey",
    "ssh_key",
    "customizations.user",
    "hostname",
    "firstboot",
    "repositories",
    "network",
    "sudo-nopasswd",
    "nopasswd",
    "clearpart",
    "installation_device",
    "http://",
    "https://",
)
GATE_NAMES = {
    "builder_digest_verified",
    "project_rpm_signed_and_pinned",
    "userspace_pds2_rpm_signed_and_pinned",
    "repository_snapshot_signed_and_pinned",
    "release_evidence_complete",
    "rootfs_smoke_passed",
}
REPOSITORY_GATE = "repository_snapshot_signed_and_pinned"
RELEASE_EVIDENCE_GATE = "release_evidence_complete"
ROOTFS_SMOKE_GATE = "rootfs_smoke_passed"
SPDX_VALIDATION_LOCK_RELATIVE = PurePosixPath(
    "packaging/image/spdx-validation-lock.json"
)
SPDX_VALIDATION_LOCK_SHA256 = (
    "1b1afb7eb97e62be102833d0510b2de8d5a77e5f6c76f761d4b0ec8ea787d89e"
)
RELEASE_EVIDENCE_FILES = {
    "LICENSE",
    "NOTICE",
    "THIRD-PARTY.json",
    "SBOM.spdx.json",
    "components/assets/ASSETS.json",
}
ROOTFS_LOCK_RELATIVE = PurePosixPath(
    "packaging/pocketds-userspace/rootfs-transaction-lock.pds2.json"
)
BUILD_LOCK_RELATIVE = PurePosixPath(
    "packaging/pocketds-userspace/build-lock.pds2.json"
)
RELEASE_AUDIT_CHECK_IDS = {
    "sudoers-files-safe",
    "no-broad-nopasswd",
    "only-repository-nopasswd",
    "release-evidence-files",
    "project-license-notice-ready",
    "spdx-3.0.1-jsonld-structure",
    "third-party-source-structure",
    "asset-redistribution-structure",
    "chromium-runtime-provenance",
    "no-private-user-state",
    "no-private-system-state",
}
REPOSITORY_SMOKE_KEYS = {
    "schema",
    "completed",
    "accepted",
    "repository_receipt_sha256",
    "repository_repomd_sha256",
    "repository_signature_sha256",
    "project_rpm_sha256",
    "final_archive_records_sha256",
    "offline_file_repository_only",
    "dnf5_nvr",
    "repo_gpgcheck",
    "package_gpgcheck",
    "skip_if_unavailable",
    "available",
    "installed_package_count",
    "installed_manifest_sha256",
    "materialized_file_count",
    "dependency_check",
    "scriptlets_executed",
    "triggers_executed",
    "disposable_root_cleaned",
    "device_root_touched",
    "services_started",
    "selinux_labels_verified",
    "release_ready",
}
DNF5_NVR = "dnf5-5.4.1.0-1.fc44.aarch64"


class PreflightError(RuntimeError):
    """Composition metadata is unsafe, inconsistent or incomplete."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PreflightError("composition JSON contains a duplicate key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> Any:
    raise PreflightError(f"composition JSON contains non-finite number {value}")


def strict_json(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PreflightError(f"{label} is invalid JSON") from exc


def read_regular(path: Path, maximum: int = MAX_INPUT_BYTES) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PreflightError("required composition input is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > maximum
        ):
            raise PreflightError("composition input has unsafe metadata")
        data = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise PreflightError("composition input changed while being read")
            data.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise PreflightError("composition input grew while being read")
        return bytes(data)
    finally:
        os.close(descriptor)


def safe_relative(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PreflightError("blueprint path is invalid")
    result = PurePosixPath(value)
    if result.is_absolute() or any(part in {"", ".", ".."} for part in result.parts):
        raise PreflightError("blueprint path is unsafe")
    return result


def bounded_file(repository_root: Path, relative: PurePosixPath) -> Path:
    if repository_root.is_symlink() or not repository_root.is_dir():
        raise PreflightError("repository root is missing or unsafe")
    current = repository_root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise PreflightError("composition input parent is missing or unsafe")
    return current / relative.parts[-1]


def parse_blueprint(data: bytes) -> dict[str, Any]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PreflightError("blueprint is not UTF-8") from exc
    if "\r" in text or not text.endswith("\n"):
        raise PreflightError("blueprint has unsupported line endings")
    lowered = text.lower()
    if any(token in lowered for token in FORBIDDEN_BLUEPRINT_TOKENS):
        raise PreflightError("blueprint contains forbidden identity or mutation fields")

    root: dict[str, str] = {}
    packages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "[[packages]]":
            current = {}
            packages.append(current)
            continue
        match = ASSIGNMENT_RE.fullmatch(line)
        if match is None:
            raise PreflightError("blueprint uses an unsupported TOML construct")
        key = match.group(1)
        try:
            value = json.loads(match.group(2))
        except json.JSONDecodeError as exc:
            raise PreflightError("blueprint string is invalid") from exc
        destination = root if current is None else current
        if key in destination:
            raise PreflightError("blueprint contains a duplicate field")
        destination[key] = value

    if set(root) != {"name", "description", "version", "distro"}:
        raise PreflightError("blueprint root fields are incomplete or unsupported")
    if any(set(package) != {"name", "version"} for package in packages):
        raise PreflightError("blueprint package fields are incomplete or unsupported")
    if len({package["name"] for package in packages}) != len(packages):
        raise PreflightError("blueprint contains duplicate packages")
    return {**root, "packages": packages}


def require_exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PreflightError(f"{label} fields are incomplete or unsupported")
    return value


def valid_nonzero_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and SHA256_RE.fullmatch(value) is not None
        and value != "0" * 64
    )


def canonical_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()


def validate_repository_smoke(evidence: Any) -> None:
    report = require_exact_object(
        evidence, REPOSITORY_SMOKE_KEYS, "repository smoke evidence"
    )
    available = require_exact_object(
        report["available"],
        {"package_count", "package_manifest_sha256"},
        "repository smoke available manifest",
    )
    if (
        type(report["schema"]) is not int
        or report["schema"] != 1
        or report["completed"] is not True
        or report["accepted"] is not True
        or any(
            not valid_nonzero_sha(report[name])
            for name in (
                "repository_receipt_sha256",
                "repository_repomd_sha256",
                "repository_signature_sha256",
                "project_rpm_sha256",
                "final_archive_records_sha256",
                "installed_manifest_sha256",
            )
        )
        or report["offline_file_repository_only"] is not True
        or report["dnf5_nvr"] != DNF5_NVR
        or report["repo_gpgcheck"] is not True
        or report["package_gpgcheck"] is not True
        or report["skip_if_unavailable"] is not False
        or type(available["package_count"]) is not int
        or available["package_count"] != 322
        or not valid_nonzero_sha(available["package_manifest_sha256"])
        or type(report["installed_package_count"]) is not int
        or report["installed_package_count"] != 322
        or type(report["materialized_file_count"]) is not int
        or report["materialized_file_count"] != 327
        or report["dependency_check"] != "pass"
        or report["scriptlets_executed"] is not True
        or report["triggers_executed"] is not True
        or report["disposable_root_cleaned"] is not True
        or report["device_root_touched"] is not False
        or report["services_started"] is not False
        or report["selinux_labels_verified"] is not False
        or report["release_ready"] is not False
    ):
        raise PreflightError("repository smoke evidence does not prove the locked gate")


def read_evidence_reference(
    repository_root: Path,
    reference: Any,
    evidence_paths: set[PurePosixPath],
    label: str,
) -> dict[str, Any]:
    reference = require_exact_object(reference, {"path", "sha256"}, label)
    relative = safe_relative(reference["path"])
    if relative.parts[:3] != ("packaging", "image", "evidence"):
        raise PreflightError(f"{label} is outside its bounded directory")
    if relative in evidence_paths:
        raise PreflightError(f"{label} path is reused")
    evidence_paths.add(relative)
    expected_sha = reference["sha256"]
    if not valid_nonzero_sha(expected_sha):
        raise PreflightError(f"{label} SHA-256 is invalid")
    data = read_regular(bounded_file(repository_root, relative))
    if hashlib.sha256(data).hexdigest() != expected_sha:
        raise PreflightError(f"{label} SHA-256 mismatch")
    report = strict_json(data, label)
    if not isinstance(report, dict):
        raise PreflightError(f"{label} is not an object")
    return report


def validate_release_root_audit(report: Any) -> None:
    report = require_exact_object(
        report,
        {
            "schema",
            "read_only",
            "standard",
            "checks",
            "blockers",
            "release_ready",
            "privacy",
            "root_mode",
        },
        "mounted-root audit report",
    )
    standard = require_exact_object(
        report["standard"], {"sbom", "sbom_validation"}, "audit standard"
    )
    privacy = require_exact_object(
        report["privacy"], {"paths_disclosed"}, "audit privacy"
    )
    checks = report["checks"]
    if not isinstance(checks, list) or len(checks) != len(RELEASE_AUDIT_CHECK_IDS):
        raise PreflightError("mounted-root audit check set differs")
    identifiers: set[str] = set()
    for check in checks:
        check = require_exact_object(
            check,
            {"check_id", "status", "summary", "evidence"},
            "mounted-root audit check",
        )
        if (
            not isinstance(check["check_id"], str)
            or check["check_id"] in identifiers
            or check["status"] != "pass"
            or not isinstance(check["summary"], str)
            or not check["summary"].strip()
            or not isinstance(check["evidence"], dict)
        ):
            raise PreflightError("mounted-root audit check does not pass")
        identifiers.add(check["check_id"])
    if (
        type(report["schema"]) is not int
        or report["schema"] != 1
        or report["read_only"] is not True
        or standard
        != {
            "sbom": "SPDX-3.0.1-JSON-LD",
            "sbom_validation": "structural-preflight-only",
        }
        or identifiers != RELEASE_AUDIT_CHECK_IDS
        or report["blockers"] != []
        or report["release_ready"] is not True
        or privacy["paths_disclosed"] is not False
        or report["root_mode"] != "offline-mounted-root"
    ):
        raise PreflightError("mounted-root audit report does not prove the locked gate")


def validate_spdx_offline_report(
    report: Any,
    validation_lock: dict[str, Any],
    sbom_sha256: str,
) -> None:
    report = require_exact_object(
        report,
        {
            "schema",
            "verified",
            "offline",
            "spec_version",
            "sbom_sha256",
            "official_artifacts",
            "validator_packages",
            "json_schema_valid",
            "owl_shacl_valid",
            "rdf_triple_count",
            "semantic_result_triple_count",
            "legal_conclusion",
            "rights_or_redistribution_inferred",
            "release_ready",
        },
        "SPDX offline validation report",
    )
    expected_artifacts = {
        name: record["sha256"]
        for name, record in validation_lock["artifacts"].items()
    }
    if (
        type(report["schema"]) is not int
        or report["schema"] != 1
        or report["verified"] is not True
        or report["offline"] is not True
        or report["spec_version"] != "3.0.1"
        or report["sbom_sha256"] != sbom_sha256
        or report["official_artifacts"] != expected_artifacts
        or report["validator_packages"] != validation_lock["validator"]
        or report["json_schema_valid"] is not True
        or report["owl_shacl_valid"] is not True
        or type(report["rdf_triple_count"]) is not int
        or report["rdf_triple_count"] <= 0
        or type(report["semantic_result_triple_count"]) is not int
        or report["semantic_result_triple_count"] <= 0
        or report["legal_conclusion"] != "NOT_DETERMINED"
        or report["rights_or_redistribution_inferred"] is not False
        or report["release_ready"] is not False
    ):
        raise PreflightError("SPDX offline report does not prove the locked gate")


def validate_release_evidence(
    evidence: Any,
    repository_root: Path,
    evidence_paths: set[PurePosixPath],
) -> None:
    receipt = require_exact_object(
        evidence,
        {
            "schema",
            "completed",
            "accepted",
            "repository_evidence_sha256",
            "mounted_root_audit",
            "spdx_offline_validation",
            "spdx_validation_lock_sha256",
            "release_ready",
        },
        "release evidence receipt",
    )
    evidence_hashes = require_exact_object(
        receipt["repository_evidence_sha256"],
        RELEASE_EVIDENCE_FILES,
        "release evidence file hashes",
    )
    for name, expected_sha in evidence_hashes.items():
        if not valid_nonzero_sha(expected_sha):
            raise PreflightError("release evidence file SHA-256 is invalid")
        maximum = MAX_SBOM_BYTES if name == "SBOM.spdx.json" else MAX_INPUT_BYTES
        data = read_regular(
            bounded_file(repository_root, PurePosixPath(name)), maximum
        )
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise PreflightError("release evidence file SHA-256 mismatch")

    lock_data = read_regular(
        bounded_file(repository_root, SPDX_VALIDATION_LOCK_RELATIVE)
    )
    lock_sha = hashlib.sha256(lock_data).hexdigest()
    if (
        receipt["spdx_validation_lock_sha256"] != SPDX_VALIDATION_LOCK_SHA256
        or lock_sha != SPDX_VALIDATION_LOCK_SHA256
    ):
        raise PreflightError("SPDX validation lock differs")
    validation_lock = require_exact_object(
        strict_json(lock_data, "SPDX validation lock"),
        {"schema", "specification", "artifacts", "validator", "policy"},
        "SPDX validation lock",
    )
    artifacts = require_exact_object(
        validation_lock["artifacts"],
        {"context", "json_schema", "semantic_model"},
        "SPDX validation artifacts",
    )
    for name, record in artifacts.items():
        record = require_exact_object(
            record, {"filename", "url", "size", "sha256"}, f"{name} artifact"
        )
        if not valid_nonzero_sha(record["sha256"]):
            raise PreflightError("SPDX validation artifact hash is invalid")
    require_exact_object(
        validation_lock["validator"],
        {"python", "jsonschema", "rdflib", "pyshacl"},
        "SPDX validator packages",
    )

    audit_report = read_evidence_reference(
        repository_root,
        receipt["mounted_root_audit"],
        evidence_paths,
        "mounted-root audit report",
    )
    spdx_report = read_evidence_reference(
        repository_root,
        receipt["spdx_offline_validation"],
        evidence_paths,
        "SPDX offline validation report",
    )
    validate_release_root_audit(audit_report)
    validate_spdx_offline_report(
        spdx_report, validation_lock, evidence_hashes["SBOM.spdx.json"]
    )
    if (
        type(receipt["schema"]) is not int
        or receipt["schema"] != 1
        or receipt["completed"] is not True
        or receipt["accepted"] is not True
        or receipt["release_ready"] is not False
    ):
        raise PreflightError("release evidence receipt does not prove the locked gate")


def validate_rootfs_smoke(
    evidence: Any,
    repository_root: Path,
    composition_policy_sha256: str,
) -> None:
    report = require_exact_object(
        evidence,
        {
            "schema",
            "completed",
            "accepted",
            "artifact",
            "composition_policy_sha256",
            "rootfs_lock_sha256",
            "build_lock_sha256",
            "os_release",
            "architecture",
            "package_count",
            "package_manifest_sha256",
            "installed_contract",
            "selinux_label_count",
            "selinux_labels_verified",
            "mounted_root_audit_sha256",
            "mounted_root_check_count",
            "mounted_root_audit_passed",
            "network",
            "services_started",
            "device_root_touched",
            "release_ready",
        },
        "rootfs smoke evidence",
    )
    artifact = require_exact_object(
        report["artifact"], {"role", "size", "sha256"}, "rootfs smoke artifact"
    )
    os_release = require_exact_object(
        report["os_release"], {"id", "version_id"}, "rootfs smoke OS release"
    )
    rootfs_lock_data = read_regular(
        bounded_file(repository_root, ROOTFS_LOCK_RELATIVE)
    )
    build_lock_data = read_regular(
        bounded_file(repository_root, BUILD_LOCK_RELATIVE)
    )
    rootfs_lock_sha256 = hashlib.sha256(rootfs_lock_data).hexdigest()
    build_lock_sha256 = hashlib.sha256(build_lock_data).hexdigest()
    rootfs_lock = require_exact_object(
        strict_json(rootfs_lock_data, "rootfs transaction lock"),
        {"schema", "artifact", "builder", "installed_stage", "installed_contract"},
        "rootfs transaction lock",
    )
    locked_artifact = require_exact_object(
        rootfs_lock["artifact"],
        {
            "source_lock_sha256",
            "build_lock_sha256",
            "archive_lock_sha256",
            "binary_rpm_sha256",
        },
        "rootfs transaction artifact",
    )
    installed_stage = require_exact_object(
        rootfs_lock["installed_stage"],
        {"package_count", "package_manifest_sha256", "dnf_check"},
        "rootfs installed stage",
    )
    if (
        type(report["schema"]) is not int
        or report["schema"] != 1
        or report["completed"] is not True
        or report["accepted"] is not True
        or artifact["role"] != "osbuild-generic-container-tar"
        or type(artifact["size"]) is not int
        or not 0 < artifact["size"] <= MAX_ROOTFS_ARTIFACT_BYTES
        or not valid_nonzero_sha(artifact["sha256"])
        or report["composition_policy_sha256"] != composition_policy_sha256
        or report["rootfs_lock_sha256"] != rootfs_lock_sha256
        or report["build_lock_sha256"] != build_lock_sha256
        or locked_artifact["build_lock_sha256"] != build_lock_sha256
        or type(rootfs_lock["schema"]) is not int
        or rootfs_lock["schema"] != 1
        or os_release != {"id": "fedora", "version_id": "44"}
        or report["architecture"] != "aarch64"
        or type(report["package_count"]) is not int
        or report["package_count"] != 322
        or type(installed_stage["package_count"]) is not int
        or installed_stage["package_count"] != 322
        or report["package_count"] != installed_stage["package_count"]
        or not valid_nonzero_sha(report["package_manifest_sha256"])
        or report["package_manifest_sha256"]
        != installed_stage["package_manifest_sha256"]
        or installed_stage["dnf_check"] != "pass"
        or report["installed_contract"] != "pass"
        or type(report["selinux_label_count"]) is not int
        or report["selinux_label_count"] != 3
        or report["selinux_labels_verified"] is not True
        or not valid_nonzero_sha(report["mounted_root_audit_sha256"])
        or type(report["mounted_root_check_count"]) is not int
        or report["mounted_root_check_count"] != len(RELEASE_AUDIT_CHECK_IDS)
        or report["mounted_root_audit_passed"] is not True
        or report["network"] is not False
        or report["services_started"] is not False
        or report["device_root_touched"] is not False
        or report["release_ready"] is not False
    ):
        raise PreflightError("rootfs smoke evidence does not prove the locked gate")


def validate_generic_gate_evidence(name: str, evidence: Any) -> None:
    evidence = require_exact_object(
        evidence, {"schema", "gate", "verified", "subject_sha256"}, "gate evidence"
    )
    if (
        type(evidence["schema"]) is not int
        or evidence["schema"] != 1
        or evidence["gate"] != name
        or evidence["verified"] is not True
        or not valid_nonzero_sha(evidence["subject_sha256"])
    ):
        raise PreflightError("gate evidence does not verify the locked subject")


def validate_gates(
    gates: Any,
    repository_root: Path,
    composition_policy_sha256: str,
) -> list[str]:
    gates = require_exact_object(gates, GATE_NAMES, "composition gates")
    blockers: list[str] = []
    evidence_paths: set[PurePosixPath] = set()
    for name in sorted(GATE_NAMES):
        record = require_exact_object(
            gates[name], {"passed", "evidence", "sha256"}, f"{name} gate"
        )
        if not isinstance(record["passed"], bool):
            raise PreflightError("composition gate value is not boolean")
        if not record["passed"]:
            if record["evidence"] is not None or record["sha256"] is not None:
                raise PreflightError("incomplete gate must not claim evidence")
            blockers.append(name)
            continue

        relative = safe_relative(record["evidence"])
        if relative.parts[:3] != ("packaging", "image", "evidence"):
            raise PreflightError("gate evidence is outside its bounded directory")
        if relative in evidence_paths:
            raise PreflightError("gate evidence path is reused")
        evidence_paths.add(relative)
        expected_sha = record["sha256"]
        if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
            raise PreflightError("gate evidence SHA-256 is invalid")
        evidence_data = read_regular(bounded_file(repository_root, relative))
        if hashlib.sha256(evidence_data).hexdigest() != expected_sha:
            raise PreflightError("gate evidence SHA-256 mismatch")
        evidence = strict_json(evidence_data, "gate evidence")
        if name == REPOSITORY_GATE:
            validate_repository_smoke(evidence)
        elif name == RELEASE_EVIDENCE_GATE:
            validate_release_evidence(evidence, repository_root, evidence_paths)
        elif name == ROOTFS_SMOKE_GATE:
            validate_rootfs_smoke(
                evidence, repository_root, composition_policy_sha256
            )
        else:
            validate_generic_gate_evidence(name, evidence)
    return blockers


def validate(lock_path: Path, repository_root: Path) -> dict[str, Any]:
    lock = strict_json(read_regular(lock_path), "composition lock")
    lock = require_exact_object(
        lock,
        {"schema", "builder", "boundary", "blueprint", "identity_policy", "gates"},
        "composition lock",
    )
    if type(lock["schema"]) is not int or lock["schema"] != 1:
        raise PreflightError("composition lock schema is unsupported")

    builder = require_exact_object(
        lock["builder"],
        {
            "reference",
            "source",
            "distro",
            "arch",
            "image_type",
            "bootmode",
            "default_filename",
        },
        "builder",
    )
    digest_prefix = "ghcr.io/osbuild/image-builder-cli@sha256:"
    reference = builder["reference"]
    parsed_source = urlparse(builder["source"] if isinstance(builder["source"], str) else "")
    if (
        not isinstance(reference, str)
        or not reference.startswith(digest_prefix)
        or not SHA256_RE.fullmatch(reference[len(digest_prefix) :])
        or parsed_source.scheme != "https"
        or parsed_source.hostname != "osbuild.org"
        or builder["source"] != BUILDER_SOURCE
        or builder["distro"] != "fedora-44"
        or builder["arch"] != "aarch64"
        or builder["image_type"] != "generic-container"
        or builder["bootmode"] != "none"
        or builder["default_filename"] != "container.tar"
    ):
        raise PreflightError("builder is not the pinned non-bootable Fedora target")

    boundary = require_exact_object(
        lock["boundary"],
        {
            "artifact_role",
            "flashable",
            "partition_table",
            "modifies_boot_chain",
            "build_on_daily_device",
        },
        "release boundary",
    )
    if boundary["artifact_role"] != "rootfs-staging-only" or any(
        boundary[name] is not False
        for name in (
            "flashable",
            "partition_table",
            "modifies_boot_chain",
            "build_on_daily_device",
        )
    ):
        raise PreflightError("composition crosses the rootfs-only safety boundary")

    blueprint_record = require_exact_object(
        lock["blueprint"],
        {
            "path",
            "size",
            "sha256",
            "name",
            "description",
            "version",
            "distro",
            "packages",
        },
        "blueprint record",
    )
    blueprint_relative = safe_relative(blueprint_record["path"])
    if blueprint_relative != BLUEPRINT_RELATIVE:
        raise PreflightError("blueprint path is not the canonical staging template")
    blueprint_path = bounded_file(repository_root, blueprint_relative)
    blueprint_data = read_regular(blueprint_path)
    expected_size = blueprint_record["size"]
    expected_sha = blueprint_record["sha256"]
    if (
        type(expected_size) is not int
        or expected_size <= 0
        or len(blueprint_data) != expected_size
    ):
        raise PreflightError("blueprint size mismatch")
    if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
        raise PreflightError("blueprint SHA-256 is invalid")
    if hashlib.sha256(blueprint_data).hexdigest() != expected_sha:
        raise PreflightError("blueprint SHA-256 mismatch")
    blueprint = parse_blueprint(blueprint_data)
    expected_blueprint = {
        "name": blueprint_record["name"],
        "description": blueprint_record["description"],
        "version": blueprint_record["version"],
        "distro": blueprint_record["distro"],
        "packages": blueprint_record["packages"],
    }
    if (
        blueprint != expected_blueprint
        or blueprint["name"] != "pocketds-linux-rootfs"
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", blueprint["version"])
        or blueprint["distro"] != builder["distro"]
    ):
        raise PreflightError("blueprint does not match its locked semantic record")
    for package in blueprint["packages"]:
        if (
            not isinstance(package["name"], str)
            or not VERSION_RE.fullmatch(package["name"])
            or not isinstance(package["version"], str)
            or (
                package["version"] != "__PIN_REQUIRED__"
                and not VERSION_RE.fullmatch(package["version"])
            )
        ):
            raise PreflightError("blueprint package pin is invalid")

    identity = require_exact_object(
        lock["identity_policy"],
        {
            "users_embedded",
            "passwords_embedded",
            "ssh_keys_embedded",
            "network_profiles_embedded",
            "hostname_embedded",
            "machine_id_initialized",
            "ssh_host_keys_initialized",
        },
        "identity policy",
    )
    if any(value is not False for value in identity.values()):
        raise PreflightError("composition embeds private or cloned identity")

    composition_policy_sha256 = canonical_sha256(
        {
            "builder": builder,
            "boundary": boundary,
            "blueprint": blueprint_record,
            "identity_policy": identity,
        }
    )
    blockers = validate_gates(
        lock["gates"], repository_root, composition_policy_sha256
    )
    if any("__PIN_REQUIRED__" == package["version"] for package in blueprint["packages"]):
        blockers.append("package_version_pins_complete")
    blockers.sort()
    return {
        "schema": 1,
        "read_only": True,
        "network": False,
        "build": False,
        "artifact_role": boundary["artifact_role"],
        "modifies_boot_chain": False,
        "blockers": blockers,
        "release_ready": not blockers,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = validate(args.lock, args.repository_root)
    except (OSError, PreflightError) as exc:
        print(json.dumps({"release_ready": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
