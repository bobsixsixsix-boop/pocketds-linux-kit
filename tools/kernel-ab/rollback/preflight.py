#!/usr/bin/env python3
"""Statically preflight the PDS-002 baseline rollback artifact offline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Callable


HERE = Path(__file__).resolve().parent
IFPC_SOURCE = HERE.parent / "candidate-build" / "ifpc-evidence.py"
IFPC_SPEC = importlib.util.spec_from_file_location("pocketds_ifpc_evidence", IFPC_SOURCE)
if IFPC_SPEC is None or IFPC_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load IFPC evidence library")
ifpc = importlib.util.module_from_spec(IFPC_SPEC)
sys.modules[IFPC_SPEC.name] = ifpc
IFPC_SPEC.loader.exec_module(ifpc)
payload = ifpc.payload


PreflightError = payload.ReproductionError
REPORT_SCHEMA = "pocketds.kernel-rollback-static-preflight.v1"
LOCK_FIELDS = {
    "schema_version",
    "artifact_id",
    "device_id",
    "kernel_release",
    "source_commit",
    "artifacts",
    "baseline_packages",
    "payload",
    "recovery_validation",
}
ARTIFACT_NAMES = {
    "rollback_bootimg",
    "runtime_report",
    "source_report",
    "custom_dtb",
    "baseline_payload_report",
    "candidate_report",
}
PACKAGE_NAMES = {"signed_reference_rpm", "attested_rebuilt_rpm"}
PAYLOAD_FIELDS = {
    "manifest_sha256",
    "entry_count",
    "regular_file_count",
    "directory_count",
    "symlink_count",
    "regular_file_bytes",
    "rpm_entry_count",
    "implicit_directories",
    "package_boot_image_path",
    "package_boot_image_sha256",
    "raw_image_path",
    "raw_image_sha256",
    "raw_image_size",
    "page_size",
    "kernel_size",
    "gzip_size",
    "gzip_sha256",
    "dtb_size",
    "dtb_sha256",
    "image_id_hex",
}
RECOVERY_FIELDS = {
    "status",
    "successful_boots",
    "successful_recovery_tests",
    "independent_recovery_path",
}


def _strict(value: object, fields: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PreflightError(f"{name} fields differ")
    return value


def _integer(value: object, name: str, *, zero: bool = False) -> int:
    minimum = 0 if zero else 1
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise PreflightError(f"invalid {name}")
    return value


def _digest(value: object, name: str) -> str:
    text = ifpc._string(value, name)
    if payload.HEX64.fullmatch(text) is None:
        raise PreflightError(f"invalid {name}")
    return text


def validate_lock(raw: dict[str, object]) -> dict[str, object]:
    _strict(raw, LOCK_FIELDS, "rollback lock")
    if raw["schema_version"] != 1:
        raise PreflightError("unsupported rollback lock schema")
    artifacts_raw = _strict(raw["artifacts"], ARTIFACT_NAMES, "rollback artifact")
    packages_raw = _strict(raw["baseline_packages"], PACKAGE_NAMES, "baseline package")
    artifacts = {
        name: payload._record(value, name)
        for name, value in sorted(artifacts_raw.items())
    }
    packages = {
        name: payload._record(value, name)
        for name, value in sorted(packages_raw.items())
    }
    if len({str(item["filename"]) for item in artifacts.values()}) != len(artifacts):
        raise PreflightError("rollback artifact filenames are not unique")
    if len({str(item["filename"]) for item in packages.values()}) != len(packages):
        raise PreflightError("baseline package filenames are not unique")
    expected_raw = _strict(raw["payload"], PAYLOAD_FIELDS, "rollback payload")
    implicit = expected_raw["implicit_directories"]
    if (
        not isinstance(implicit, list)
        or implicit != sorted(set(implicit))
    ):
        raise PreflightError("invalid implicit directory set")
    clean_payload: dict[str, object] = {
        "manifest_sha256": _digest(expected_raw["manifest_sha256"], "payload manifest SHA-256"),
        "entry_count": _integer(expected_raw["entry_count"], "entry count"),
        "regular_file_count": _integer(expected_raw["regular_file_count"], "regular file count"),
        "directory_count": _integer(expected_raw["directory_count"], "directory count", zero=True),
        "symlink_count": _integer(expected_raw["symlink_count"], "symlink count", zero=True),
        "regular_file_bytes": _integer(expected_raw["regular_file_bytes"], "regular file bytes"),
        "rpm_entry_count": _integer(expected_raw["rpm_entry_count"], "RPM entry count"),
        "implicit_directories": [payload._relative_path(item, "implicit directory") for item in implicit],
        "package_boot_image_path": payload._relative_path(expected_raw["package_boot_image_path"], "package boot image path"),
        "package_boot_image_sha256": _digest(expected_raw["package_boot_image_sha256"], "package boot image SHA-256"),
        "raw_image_path": payload._relative_path(expected_raw["raw_image_path"], "raw image path"),
        "raw_image_sha256": _digest(expected_raw["raw_image_sha256"], "raw Image SHA-256"),
        "raw_image_size": _integer(expected_raw["raw_image_size"], "raw Image size"),
        "page_size": _integer(expected_raw["page_size"], "page size"),
        "kernel_size": _integer(expected_raw["kernel_size"], "kernel size"),
        "gzip_size": _integer(expected_raw["gzip_size"], "gzip size"),
        "gzip_sha256": _digest(expected_raw["gzip_sha256"], "gzip SHA-256"),
        "dtb_size": _integer(expected_raw["dtb_size"], "DTB size"),
        "dtb_sha256": _digest(expected_raw["dtb_sha256"], "DTB SHA-256"),
        "image_id_hex": ifpc._hex(expected_raw["image_id_hex"], "boot image ID", length=32),
    }
    if clean_payload["entry_count"] != sum(
        int(clean_payload[name])
        for name in ("regular_file_count", "directory_count", "symlink_count")
    ):
        raise PreflightError("payload type counts do not sum")
    if clean_payload["entry_count"] != clean_payload["rpm_entry_count"] + len(implicit):
        raise PreflightError("RPM and implicit entry counts do not sum")
    if artifacts["custom_dtb"]["sha256"] != clean_payload["dtb_sha256"] or artifacts["custom_dtb"]["size"] != clean_payload["dtb_size"]:
        raise PreflightError("custom DTB locks are not bound")
    recovery_raw = _strict(raw["recovery_validation"], RECOVERY_FIELDS, "recovery validation")
    recovery = {
        "status": ifpc._string(recovery_raw["status"], "recovery status"),
        "successful_boots": _integer(recovery_raw["successful_boots"], "successful boots", zero=True),
        "successful_recovery_tests": _integer(recovery_raw["successful_recovery_tests"], "successful recovery tests", zero=True),
        "independent_recovery_path": recovery_raw["independent_recovery_path"],
    }
    if recovery_raw["independent_recovery_path"] is not False or recovery != {
        "status": "not_run",
        "successful_boots": 0,
        "successful_recovery_tests": 0,
        "independent_recovery_path": False,
    }:
        raise PreflightError("static preflight lock must not claim recovery validation")
    source_commit = ifpc._string(raw["source_commit"], "source commit")
    if payload.HEX40.fullmatch(source_commit) is None:
        raise PreflightError("invalid source commit")
    return {
        "schema_version": 1,
        "artifact_id": ifpc._string(raw["artifact_id"], "artifact ID"),
        "device_id": ifpc._string(raw["device_id"], "device ID"),
        "kernel_release": ifpc._string(raw["kernel_release"], "kernel release"),
        "source_commit": source_commit,
        "artifacts": artifacts,
        "baseline_packages": packages,
        "payload": clean_payload,
        "recovery_validation": recovery,
    }


def _hash_file(path: Path, expected: dict[str, object]) -> dict[str, object]:
    content, metadata = payload._read_regular(path, maximum=payload.MAX_ARTIFACT_BYTES)
    measured = {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": metadata.st_size,
    }
    if measured != expected:
        raise PreflightError(f"locked file differs: {path.name}")
    return measured


def _read_report(path: Path) -> dict[str, object]:
    content, _metadata = payload._read_regular(path, maximum=2 * 1024 * 1024)
    parsed = payload._strict_json(content, path.name)
    if not isinstance(parsed, dict):
        raise PreflightError(f"report root is not an object: {path.name}")
    return parsed


def _require_true(container: object, keys: tuple[str, ...], name: str) -> None:
    current = container
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise PreflightError(f"{name} is missing")
        current = current[key]
    if current is not True:
        raise PreflightError(f"{name} is not true")


def _verify_reports(lock: dict[str, object], artifact_dir: Path) -> dict[str, object]:
    runtime = _read_report(
        artifact_dir / str(lock["artifacts"]["runtime_report"]["filename"])
    )
    source = _read_report(
        artifact_dir / str(lock["artifacts"]["source_report"]["filename"])
    )
    baseline = _read_report(
        artifact_dir / str(lock["artifacts"]["baseline_payload_report"]["filename"])
    )
    candidate = _read_report(
        artifact_dir / str(lock["artifacts"]["candidate_report"]["filename"])
    )
    expected_payload = lock["payload"]
    if (
        runtime.get("schema") != "pocketds.kernel-ab-runtime-evidence.v3"
        or runtime.get("device_id") != lock["device_id"]
        or runtime.get("expected_source_commit") != lock["source_commit"]
    ):
        raise PreflightError("runtime report identity differs")
    runtime_identity = runtime.get("runtime")
    if not isinstance(runtime_identity, dict) or runtime_identity.get("kernel_release") != lock["kernel_release"]:
        raise PreflightError("runtime report kernel release differs")
    runtime_artifacts = runtime.get("artifacts")
    boot_images = runtime_artifacts.get("boot_images") if isinstance(runtime_artifacts, dict) else None
    if (
        not isinstance(boot_images, list)
        or len(boot_images) != 3
        or any(
            not isinstance(item, dict)
            or item.get("sha256") != lock["artifacts"]["rollback_bootimg"]["sha256"]
            or item.get("size") != lock["artifacts"]["rollback_bootimg"]["size"]
            for item in boot_images
        )
    ):
        raise PreflightError("runtime report boot alias binding differs")
    boot_container = runtime_artifacts.get("boot_container") if isinstance(runtime_artifacts, dict) else None
    boot_dtb = runtime_artifacts.get("boot_dtb") if isinstance(runtime_artifacts, dict) else None
    kernel_image = runtime_artifacts.get("kernel_image") if isinstance(runtime_artifacts, dict) else None
    if (
        not isinstance(boot_container, dict)
        or boot_container.get("format") != "android-bootimg-v0"
        or boot_container.get("page_size") != expected_payload["page_size"]
        or boot_container.get("kernel_blob_size") != expected_payload["kernel_size"]
        or boot_container.get("decompressed_kernel_sha256") != expected_payload["raw_image_sha256"]
        or boot_container.get("decompressed_kernel_size") != expected_payload["raw_image_size"]
        or boot_container.get("appended_dtb_sha256") != expected_payload["dtb_sha256"]
        or boot_container.get("appended_dtb_size") != expected_payload["dtb_size"]
        or not isinstance(boot_dtb, dict)
        or boot_dtb.get("sha256") != expected_payload["dtb_sha256"]
        or boot_dtb.get("size") != expected_payload["dtb_size"]
        or not isinstance(kernel_image, dict)
        or kernel_image.get("sha256") != expected_payload["raw_image_sha256"]
        or kernel_image.get("size") != expected_payload["raw_image_size"]
    ):
        raise PreflightError("runtime report boot composition differs")
    for gate in (
        "boot_container_appended_dtb_valid",
        "boot_container_dtb_mirror_matches",
        "boot_container_matches_raw_kernel",
        "boot_image_aliases_match",
        "config_mirror_matches",
        "firmware_set_measured",
        "machine_identity_matches",
        "runtime_kernel_release_matches",
    ):
        _require_true(runtime, ("gates", gate), f"runtime gate {gate}")
    runtime_gates = runtime.get("gates")
    if (
        not isinstance(runtime_gates, dict)
        or runtime_gates.get("prevalidated_rollback_artifact") is not False
        or runtime_gates.get("runtime_source_commit_cryptographically_bound") is not False
        or runtime.get("production_plan_ready") is not False
    ):
        raise PreflightError("runtime report overclaims readiness")
    if (
        source.get("schema") != "pocketds.kernel-source-evidence.v1"
        or source.get("read_only") is not True
        or source.get("network") is not False
        or source.get("expected_source_commit") != lock["source_commit"]
    ):
        raise PreflightError("source report identity differs")
    source_package = source.get("package")
    if not isinstance(source_package, dict) or source_package.get("nevra") != "kernel-" + str(lock["kernel_release"]):
        raise PreflightError("source report package binding differs")
    source_artifacts = source.get("artifacts")
    source_dtb = source_artifacts.get("rebuilt_dtb") if isinstance(source_artifacts, dict) else None
    if (
        not isinstance(source_dtb, dict)
        or source_dtb.get("sha256") != expected_payload["dtb_sha256"]
        or source_dtb.get("size") != expected_payload["dtb_size"]
    ):
        raise PreflightError("source report custom DTB binding differs")
    for gate in (
        "commit_archive_locked",
        "copr_metadata_artifacts_locked",
        "custom_dtb_rebuild_reproduced",
        "custom_dtb_source_patch_locked",
        "published_binary_rpm_locked",
        "published_source_rpm_locked",
        "rpm_archive_signature_verified",
        "source_snapshot_matches_expected_commit_tree",
        "srpm_source_member_locked",
    ):
        _require_true(source, ("gates", gate), f"source gate {gate}")
    source_gates = source.get("gates")
    if not isinstance(source_gates, dict) or source_gates.get("source_to_binary_reproducible_build_proven") is not False or source.get("source_to_binary_provenance_complete") is not False:
        raise PreflightError("source report overclaims binary provenance")
    if baseline.get("schema") != payload.REPORT_SCHEMA or baseline.get("read_only") is not True or baseline.get("network") is not False:
        raise PreflightError("baseline payload report identity differs")
    package = baseline.get("package")
    if not isinstance(package, dict) or package.get("nevra") != "kernel-" + str(lock["kernel_release"]) or package.get("source_commit") != lock["source_commit"]:
        raise PreflightError("baseline payload report package binding differs")
    for gate in (
        "signed_reference_hash_bound",
        "source_and_toolchain_inputs_locked",
        "attested_rebuild_rpm_locked",
        "complete_kernel_payload_reproduced",
        "source_to_binary_reproducible_build_proven",
    ):
        _require_true(baseline, ("gates", gate), f"baseline gate {gate}")
    for side in ("reference_payload", "rebuilt_payload"):
        report_payload = baseline.get(side)
        if not isinstance(report_payload, dict):
            raise PreflightError(f"baseline report {side} is missing")
        for field in (
            "manifest_sha256",
            "entry_count",
            "regular_file_count",
            "directory_count",
            "symlink_count",
            "regular_file_bytes",
        ):
            if report_payload.get(field) != expected_payload[field]:
                raise PreflightError(f"baseline report {side} {field} differs")
        keys = report_payload.get("key_files")
        boot = keys.get("boot_image") if isinstance(keys, dict) else None
        raw = keys.get("raw_image") if isinstance(keys, dict) else None
        if (
            not isinstance(boot, dict)
            or boot.get("path") != expected_payload["package_boot_image_path"]
            or boot.get("sha256") != expected_payload["package_boot_image_sha256"]
            or boot.get("size") != lock["artifacts"]["rollback_bootimg"]["size"]
            or not isinstance(raw, dict)
            or raw.get("path") != expected_payload["raw_image_path"]
            or raw.get("sha256") != expected_payload["raw_image_sha256"]
            or raw.get("size") != expected_payload["raw_image_size"]
        ):
            raise PreflightError(f"baseline report {side} key image binding differs")
    if candidate.get("schema") != ifpc.REPORT_SCHEMA or candidate.get("read_only") is not True or candidate.get("network") is not False:
        raise PreflightError("candidate report identity differs")
    experiment = candidate.get("experiment")
    if not isinstance(experiment, dict) or experiment.get("source_commit") != lock["source_commit"] or experiment.get("package_nevra") != "kernel-" + str(lock["kernel_release"]):
        raise PreflightError("candidate report source binding differs")
    for gate in (
        "candidate_built",
        "source_inputs_except_revert_identical",
        "single_variable_payload_delta_proven",
        "candidate_independently_reproduced",
    ):
        _require_true(candidate, ("gates", gate), f"candidate gate {gate}")
    gates = candidate.get("gates")
    if not isinstance(gates, dict) or gates.get("rollback_artifact_prevalidated") is not False or gates.get("candidate_install_authorized") is not False:
        raise PreflightError("candidate report overclaims installation readiness")
    return {
        "runtime_report_bound": True,
        "source_report_bound": True,
        "baseline_payload_report_bound": True,
        "candidate_report_bound": True,
        "candidate_was_not_install_authorized": True,
    }


def _tree_file(root: Path, relative: str) -> bytes:
    content, _metadata = payload._read_regular(
        root / relative,
        maximum=payload.MAX_TREE_BYTES,
        require_nonempty=False,
    )
    return content


def collect(
    lock: dict[str, object],
    *,
    rollback_artifact_dir: Path,
    baseline_artifact_dir: Path,
    reference_root: Path,
    rebuilt_root: Path,
    rpm_manifest_reader: Callable[..., list[dict[str, object]]] = payload.read_rpm_manifest,
) -> dict[str, object]:
    measured_artifacts = {
        name: _hash_file(rollback_artifact_dir / str(record["filename"]), record)
        for name, record in lock["artifacts"].items()
    }
    measured_packages = {
        name: _hash_file(baseline_artifact_dir / str(record["filename"]), record)
        for name, record in lock["baseline_packages"].items()
    }
    reports = _verify_reports(lock, rollback_artifact_dir)
    reference_records, reference_summary = payload.scan_tree(reference_root)
    rebuilt_records, rebuilt_summary = payload.scan_tree(rebuilt_root)
    if reference_records != rebuilt_records:
        raise PreflightError("baseline reference and rebuilt payloads differ")
    expected = lock["payload"]
    for summary, name in ((reference_summary, "reference"), (rebuilt_summary, "rebuilt")):
        for field in (
            "manifest_sha256",
            "entry_count",
            "regular_file_count",
            "directory_count",
            "symlink_count",
            "regular_file_bytes",
        ):
            if summary[field] != expected[field]:
                raise PreflightError(f"{name} payload {field} differs")
    signed_package = rpm_manifest_reader(
        baseline_artifact_dir
        / str(lock["baseline_packages"]["signed_reference_rpm"]["filename"]),
        lock["baseline_packages"]["signed_reference_rpm"],
    )
    rebuilt_package = rpm_manifest_reader(
        baseline_artifact_dir
        / str(lock["baseline_packages"]["attested_rebuilt_rpm"]["filename"]),
        lock["baseline_packages"]["attested_rebuilt_rpm"],
    )
    if signed_package != rebuilt_package or len(signed_package) != expected["rpm_entry_count"]:
        raise PreflightError("baseline RPM header manifests differ")
    reference_implicit = payload._bind_package_to_tree(signed_package, reference_records)
    rebuilt_implicit = payload._bind_package_to_tree(rebuilt_package, rebuilt_records)
    if reference_implicit != expected["implicit_directories"] or rebuilt_implicit != reference_implicit:
        raise PreflightError("baseline implicit directory set differs")
    rollback_content, _metadata = payload._read_regular(
        rollback_artifact_dir / str(lock["artifacts"]["rollback_bootimg"]["filename"]),
        maximum=payload.MAX_TREE_BYTES,
    )
    package_boot = _tree_file(reference_root, str(expected["package_boot_image_path"]))
    rebuilt_package_boot = _tree_file(rebuilt_root, str(expected["package_boot_image_path"]))
    if (
        rebuilt_package_boot != package_boot
        or hashlib.sha256(package_boot).hexdigest() != expected["package_boot_image_sha256"]
        or rollback_content == package_boot
    ):
        raise PreflightError("baseline package boot image or runtime composition boundary differs")
    custom_dtb, _custom_dtb_metadata = payload._read_regular(
        rollback_artifact_dir / str(lock["artifacts"]["custom_dtb"]["filename"]),
        maximum=1_048_576,
    )
    raw_image = _tree_file(reference_root, str(expected["raw_image_path"]))
    if len(raw_image) != expected["raw_image_size"] or hashlib.sha256(raw_image).hexdigest() != expected["raw_image_sha256"]:
        raise PreflightError("baseline raw Image differs")
    boot_expected = {
        "page_size": expected["page_size"],
        "baseline_kernel_size": expected["kernel_size"],
        "baseline_gzip_size": expected["gzip_size"],
        "baseline_gzip_sha256": expected["gzip_sha256"],
        "dtb_size": expected["dtb_size"],
        "dtb_sha256": expected["dtb_sha256"],
        "baseline_id_hex": expected["image_id_hex"],
    }
    boot = ifpc._parse_boot_image(rollback_content, boot_expected, raw_image, "baseline")
    boot.pop("header")
    if hashlib.sha256(custom_dtb).hexdigest() != boot["dtb_sha256"] or len(custom_dtb) != boot["dtb_size"]:
        raise PreflightError("runtime boot image does not append the source-bound custom DTB")
    return {
        "schema": REPORT_SCHEMA,
        "read_only": True,
        "network": False,
        "artifact": {
            "artifact_id": lock["artifact_id"],
            "device_id": lock["device_id"],
            "kernel_release": lock["kernel_release"],
            "source_commit": lock["source_commit"],
            **measured_artifacts["rollback_bootimg"],
        },
        "evidence_artifacts": measured_artifacts,
        "baseline_packages": measured_packages,
        "reports": reports,
        "payload": {
            "summary": reference_summary,
            "rpm_entry_count": len(signed_package),
            "implicit_directories": reference_implicit,
            "signed_and_rebuilt_payloads_identical": True,
            "package_boot_image_sha256": expected["package_boot_image_sha256"],
            "runtime_boot_composes_reproduced_raw_image_and_source_bound_dtb": True,
        },
        "boot_derivation": boot,
        "recovery_validation": lock["recovery_validation"],
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
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback-artifact-dir", type=Path, required=True)
    parser.add_argument("--baseline-artifact-dir", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--rebuilt-root", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        raw, lock_sha256 = payload.load_lock(HERE / "lock.json")
        lock = validate_lock(raw)
        report = collect(
            lock,
            rollback_artifact_dir=args.rollback_artifact_dir,
            baseline_artifact_dir=args.baseline_artifact_dir,
            reference_root=args.reference_root,
            rebuilt_root=args.rebuilt_root,
        )
        report["lock_sha256"] = lock_sha256
    except (OSError, PreflightError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
