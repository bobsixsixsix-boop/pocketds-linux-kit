#!/usr/bin/env python3
"""Verify the PDS-002 A740 IFPC candidate as a locked, offline delta."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
from typing import Any, Callable
import zlib


HERE = Path(__file__).resolve().parent
PAYLOAD_SOURCE = HERE.parent / "reproducible-build" / "payload-evidence.py"
PAYLOAD_SPEC = importlib.util.spec_from_file_location(
    "pocketds_kernel_payload_evidence", PAYLOAD_SOURCE
)
if PAYLOAD_SPEC is None or PAYLOAD_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load the payload evidence library")
payload = importlib.util.module_from_spec(PAYLOAD_SPEC)
sys.modules[PAYLOAD_SPEC.name] = payload
PAYLOAD_SPEC.loader.exec_module(payload)


CandidateError = payload.ReproductionError
LOCK_SCHEMA_VERSION = 1
REPORT_SCHEMA = "pocketds.kernel-ifpc-candidate.v1"
HEX40 = payload.HEX40
HEX64 = payload.HEX64
MAX_TEXT_BYTES = 2 * 1024 * 1024
LOCK_FIELDS = {
    "schema_version",
    "experiment_id",
    "source_commit",
    "revert_commit",
    "package_nevra",
    "baseline_artifacts",
    "candidate_artifacts",
    "reproduced_candidate_artifacts",
    "recipe_files",
    "environment",
    "source_rpm",
    "payload",
}
BASELINE_ARTIFACTS = {"source_rpm", "reference_rpm"}
CANDIDATE_ARTIFACTS = {
    "source_rpm",
    "binary_rpm",
    "package_manifest",
    "compiler_summary",
    "wrapper_manifest",
    "build_log",
    "baseline_spec",
    "candidate_spec",
}
REPRODUCED_CANDIDATE_ARTIFACTS = {
    "binary_rpm",
    "package_manifest",
    "compiler_summary",
    "wrapper_manifest",
    "build_log",
}
RECIPE_FILES = {"revert_patch", "spec_patch"}
ENVIRONMENT_FIELDS = {
    "installed_package_count",
    "installed_package_manifest_sha256",
    "compiler_lines",
    "date_wrapper_sha256",
    "make_wrapper_sha256",
}
SOURCE_RPM_FIELDS = {
    "spec_filename",
    "patch_filename",
    "unchanged_members",
    "baseline_spec_sha256",
    "candidate_spec_sha256",
    "added_patch_sha256",
    "baseline_entry_count",
    "candidate_entry_count",
}
PAYLOAD_FIELDS = {
    "baseline_manifest_sha256",
    "candidate_manifest_sha256",
    "entry_count",
    "regular_file_count",
    "directory_count",
    "symlink_count",
    "regular_file_bytes",
    "rpm_entry_count",
    "implicit_directory_count",
    "unchanged_entry_count",
    "unchanged_regular_file_count",
    "changed_files",
    "raw_image_delta",
    "boot_derivation",
}
CHANGED_FILE_NAMES = {"boot_image", "raw_image"}
RAW_DELTA_FIELDS = {
    "different_byte_count",
    "quirk_offset",
    "baseline_quirk_hex",
    "candidate_quirk_hex",
    "ifpc_pointer_offset",
    "baseline_ifpc_pointer_hex",
    "candidate_ifpc_pointer_hex",
    "build_id_offset",
    "baseline_build_id_hex",
    "candidate_build_id_hex",
    "relr_offset",
    "baseline_relr_hex",
    "candidate_relr_hex",
}
BOOT_FIELDS = {
    "page_size",
    "dtb_size",
    "dtb_sha256",
    "baseline_kernel_size",
    "candidate_kernel_size",
    "baseline_gzip_size",
    "candidate_gzip_size",
    "baseline_gzip_sha256",
    "candidate_gzip_sha256",
    "baseline_id_hex",
    "candidate_id_hex",
    "header_different_byte_count",
}


def _strict_fields(value: object, expected: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise CandidateError(f"{name} fields differ")
    return value


def _string(value: object, name: str, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CandidateError(f"invalid {name}")
    return value


def _integer(value: object, name: str, *, zero: bool = False) -> int:
    minimum = 0 if zero else 1
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise CandidateError(f"invalid {name}")
    return value


def _hex(value: object, name: str, *, length: int | None = None) -> str:
    text = _string(value, name)
    try:
        decoded = bytes.fromhex(text)
    except ValueError as exc:
        raise CandidateError(f"invalid {name}") from exc
    if text != text.lower() or (length is not None and len(decoded) != length):
        raise CandidateError(f"invalid {name}")
    return text


def _digest(value: object, name: str) -> str:
    text = _string(value, name)
    if HEX64.fullmatch(text) is None:
        raise CandidateError(f"invalid {name}")
    return text


def _commit(value: object, name: str) -> str:
    text = _string(value, name)
    if HEX40.fullmatch(text) is None:
        raise CandidateError(f"invalid {name}")
    return text


def _basename(value: object, name: str) -> str:
    text = _string(value, name)
    if text != Path(text).name or "/" in text or "\\" in text:
        raise CandidateError(f"invalid {name}")
    return text


def _relative(value: object, name: str, *, parent_ok: bool = False) -> str:
    text = _string(value, name)
    path = PurePosixPath(text)
    rejected = {"."} if parent_ok else {".", ".."}
    if path.is_absolute() or text != str(path) or any(part in rejected for part in path.parts):
        raise CandidateError(f"invalid {name}")
    if parent_ok and (not path.parts or path.parts[0] != ".." or path.parts.count("..") != 1):
        raise CandidateError(f"invalid {name}")
    return text


def _record(value: object, name: str, maximum: int = payload.MAX_ARTIFACT_BYTES) -> dict[str, object]:
    return payload._record(value, name, maximum=maximum)


def _member(value: object, name: str) -> dict[str, object]:
    record = _strict_fields(value, {"sha256", "size"}, name)
    return {
        "sha256": _digest(record["sha256"], f"{name} SHA-256"),
        "size": _integer(record["size"], f"{name} size"),
    }


def _changed_file(value: object, name: str) -> dict[str, object]:
    record = _strict_fields(
        value, {"path", "size", "baseline_sha256", "candidate_sha256"}, name
    )
    return {
        "path": payload._relative_path(record["path"], f"{name} path"),
        "size": _integer(record["size"], f"{name} size"),
        "baseline_sha256": _digest(record["baseline_sha256"], f"{name} baseline SHA-256"),
        "candidate_sha256": _digest(record["candidate_sha256"], f"{name} candidate SHA-256"),
    }


def validate_lock(raw: dict[str, object]) -> dict[str, object]:
    _strict_fields(raw, LOCK_FIELDS, "IFPC lock")
    if raw["schema_version"] != LOCK_SCHEMA_VERSION:
        raise CandidateError("unsupported IFPC lock schema")
    baseline_raw = _strict_fields(raw["baseline_artifacts"], BASELINE_ARTIFACTS, "baseline artifact")
    candidate_raw = _strict_fields(raw["candidate_artifacts"], CANDIDATE_ARTIFACTS, "candidate artifact")
    reproduced_raw = _strict_fields(
        raw["reproduced_candidate_artifacts"],
        REPRODUCED_CANDIDATE_ARTIFACTS,
        "reproduced candidate artifact",
    )
    recipe_raw = _strict_fields(raw["recipe_files"], RECIPE_FILES, "recipe file")
    baseline = {name: _record(value, name) for name, value in sorted(baseline_raw.items())}
    candidate = {name: _record(value, name) for name, value in sorted(candidate_raw.items())}
    reproduced = {name: _record(value, name) for name, value in sorted(reproduced_raw.items())}
    all_filenames = [str(item["filename"]) for item in (*baseline.values(), *candidate.values())]
    if len(all_filenames) != len(set(all_filenames)):
        raise CandidateError("artifact filenames are not unique")
    reproduced_filenames = [str(item["filename"]) for item in reproduced.values()]
    if len(reproduced_filenames) != len(set(reproduced_filenames)):
        raise CandidateError("reproduced artifact filenames are not unique")
    recipes: dict[str, dict[str, object]] = {}
    for name, value in sorted(recipe_raw.items()):
        record = _strict_fields(value, {"path", "sha256", "size"}, name)
        recipes[name] = {
            "path": _relative(record["path"], f"{name} path", parent_ok=name == "revert_patch"),
            "sha256": _digest(record["sha256"], f"{name} SHA-256"),
            "size": _integer(record["size"], f"{name} size"),
        }
    environment_raw = _strict_fields(raw["environment"], ENVIRONMENT_FIELDS, "environment")
    compiler_lines = environment_raw["compiler_lines"]
    if (
        not isinstance(compiler_lines, list)
        or len(compiler_lines) != 2
        or any(not isinstance(line, str) or not line or "\n" in line for line in compiler_lines)
    ):
        raise CandidateError("invalid compiler lines")
    environment = {
        "installed_package_count": _integer(environment_raw["installed_package_count"], "package count"),
        "installed_package_manifest_sha256": _digest(environment_raw["installed_package_manifest_sha256"], "package manifest SHA-256"),
        "compiler_lines": compiler_lines,
        "date_wrapper_sha256": _digest(environment_raw["date_wrapper_sha256"], "date wrapper SHA-256"),
        "make_wrapper_sha256": _digest(environment_raw["make_wrapper_sha256"], "make wrapper SHA-256"),
    }
    source_raw = _strict_fields(raw["source_rpm"], SOURCE_RPM_FIELDS, "source RPM")
    unchanged_raw = source_raw["unchanged_members"]
    if not isinstance(unchanged_raw, dict) or not unchanged_raw:
        raise CandidateError("source RPM unchanged members differ")
    unchanged: dict[str, dict[str, object]] = {}
    for filename, value in sorted(unchanged_raw.items()):
        clean_name = _basename(filename, "source member filename")
        unchanged[clean_name] = _member(value, f"source member {clean_name}")
    source_rpm = {
        "spec_filename": _basename(source_raw["spec_filename"], "source spec filename"),
        "patch_filename": _basename(source_raw["patch_filename"], "source patch filename"),
        "unchanged_members": unchanged,
        "baseline_spec_sha256": _digest(source_raw["baseline_spec_sha256"], "baseline spec SHA-256"),
        "candidate_spec_sha256": _digest(source_raw["candidate_spec_sha256"], "candidate spec SHA-256"),
        "added_patch_sha256": _digest(source_raw["added_patch_sha256"], "added patch SHA-256"),
        "baseline_entry_count": _integer(source_raw["baseline_entry_count"], "baseline source entry count"),
        "candidate_entry_count": _integer(source_raw["candidate_entry_count"], "candidate source entry count"),
    }
    if environment["installed_package_manifest_sha256"] != candidate["package_manifest"]["sha256"]:
        raise CandidateError("environment and artifact package manifests are not bound")
    for name in ("package_manifest", "compiler_summary", "wrapper_manifest"):
        if reproduced[name] != candidate[name]:
            raise CandidateError(f"reproduced environment artifact differs: {name}")
    if reproduced["binary_rpm"]["size"] != candidate["binary_rpm"]["size"]:
        raise CandidateError("candidate RPM container sizes differ")
    if reproduced["binary_rpm"]["sha256"] == candidate["binary_rpm"]["sha256"]:
        raise CandidateError("independent candidate RPM container is not distinct")
    if source_rpm["baseline_spec_sha256"] != candidate["baseline_spec"]["sha256"]:
        raise CandidateError("baseline spec locks are not bound")
    if source_rpm["candidate_spec_sha256"] != candidate["candidate_spec"]["sha256"]:
        raise CandidateError("candidate spec locks are not bound")
    if source_rpm["added_patch_sha256"] != recipes["revert_patch"]["sha256"]:
        raise CandidateError("source RPM patch and recipe patch are not bound")
    if source_rpm["candidate_entry_count"] != source_rpm["baseline_entry_count"] + 1:
        raise CandidateError("candidate source RPM must add exactly one entry")
    if source_rpm["spec_filename"] in unchanged or source_rpm["patch_filename"] in unchanged:
        raise CandidateError("source RPM member roles overlap")
    payload_raw = _strict_fields(raw["payload"], PAYLOAD_FIELDS, "payload")
    changed_raw = _strict_fields(payload_raw["changed_files"], CHANGED_FILE_NAMES, "changed file")
    changed = {name: _changed_file(value, name) for name, value in sorted(changed_raw.items())}
    if changed["boot_image"]["path"] == changed["raw_image"]["path"]:
        raise CandidateError("changed payload paths overlap")
    delta_raw = _strict_fields(payload_raw["raw_image_delta"], RAW_DELTA_FIELDS, "raw image delta")
    delta: dict[str, object] = {
        "different_byte_count": _integer(delta_raw["different_byte_count"], "different byte count"),
    }
    for prefix, length in (("quirk", 1), ("ifpc_pointer", 8), ("build_id", 20), ("relr", 1)):
        delta[f"{prefix}_offset"] = _integer(delta_raw[f"{prefix}_offset"], f"{prefix} offset", zero=True)
        delta[f"baseline_{prefix}_hex"] = _hex(delta_raw[f"baseline_{prefix}_hex"], f"baseline {prefix}", length=length)
        delta[f"candidate_{prefix}_hex"] = _hex(delta_raw[f"candidate_{prefix}_hex"], f"candidate {prefix}", length=length)
    boot_raw = _strict_fields(payload_raw["boot_derivation"], BOOT_FIELDS, "boot derivation")
    boot: dict[str, object] = {}
    for name in BOOT_FIELDS:
        value = boot_raw[name]
        if name.endswith("sha256"):
            boot[name] = _digest(value, name)
        elif name.endswith("_hex"):
            boot[name] = _hex(value, name, length=32)
        else:
            boot[name] = _integer(value, name)
    clean_payload = {
        "baseline_manifest_sha256": _digest(payload_raw["baseline_manifest_sha256"], "baseline manifest SHA-256"),
        "candidate_manifest_sha256": _digest(payload_raw["candidate_manifest_sha256"], "candidate manifest SHA-256"),
        "entry_count": _integer(payload_raw["entry_count"], "payload entry count"),
        "regular_file_count": _integer(payload_raw["regular_file_count"], "regular file count"),
        "directory_count": _integer(payload_raw["directory_count"], "directory count", zero=True),
        "symlink_count": _integer(payload_raw["symlink_count"], "symlink count", zero=True),
        "regular_file_bytes": _integer(payload_raw["regular_file_bytes"], "regular file bytes"),
        "rpm_entry_count": _integer(payload_raw["rpm_entry_count"], "RPM entry count"),
        "implicit_directory_count": _integer(payload_raw["implicit_directory_count"], "implicit directory count", zero=True),
        "unchanged_entry_count": _integer(payload_raw["unchanged_entry_count"], "unchanged entry count"),
        "unchanged_regular_file_count": _integer(payload_raw["unchanged_regular_file_count"], "unchanged regular file count"),
        "changed_files": changed,
        "raw_image_delta": delta,
        "boot_derivation": boot,
    }
    if clean_payload["entry_count"] != sum(clean_payload[name] for name in ("regular_file_count", "directory_count", "symlink_count")):
        raise CandidateError("payload type counts do not sum")
    if clean_payload["entry_count"] != clean_payload["rpm_entry_count"] + clean_payload["implicit_directory_count"]:
        raise CandidateError("payload RPM and implicit counts do not sum")
    if clean_payload["unchanged_entry_count"] != clean_payload["entry_count"] - 2:
        raise CandidateError("payload changed-entry count is not exactly two")
    if clean_payload["unchanged_regular_file_count"] != clean_payload["regular_file_count"] - 2:
        raise CandidateError("payload changed regular-file count is not exactly two")
    return {
        "schema_version": LOCK_SCHEMA_VERSION,
        "experiment_id": _string(raw["experiment_id"], "experiment ID"),
        "source_commit": _commit(raw["source_commit"], "source commit"),
        "revert_commit": _commit(raw["revert_commit"], "revert commit"),
        "package_nevra": _string(raw["package_nevra"], "package NEVRA"),
        "baseline_artifacts": baseline,
        "candidate_artifacts": candidate,
        "reproduced_candidate_artifacts": reproduced,
        "recipe_files": recipes,
        "environment": environment,
        "source_rpm": source_rpm,
        "payload": clean_payload,
    }


def _hash_record(path: Path, expected: dict[str, object]) -> dict[str, object]:
    content, metadata = payload._read_regular(path, maximum=payload.MAX_ARTIFACT_BYTES)
    measured = {"filename": path.name, "sha256": hashlib.sha256(content).hexdigest(), "size": metadata.st_size}
    if measured != expected:
        raise CandidateError(f"locked file differs: {path.name}")
    return measured


def _hash_recipe(path: Path, expected: dict[str, object]) -> dict[str, object]:
    content, metadata = payload._read_regular(path, maximum=1_048_576)
    measured = {"path": str(expected["path"]), "sha256": hashlib.sha256(content).hexdigest(), "size": metadata.st_size}
    if measured != expected:
        raise CandidateError(f"locked recipe differs: {expected['path']}")
    return measured


def _text(path: Path, maximum: int = MAX_TEXT_BYTES) -> str:
    content, _metadata = payload._read_regular(path, maximum=maximum)
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CandidateError(f"text artifact is not UTF-8: {path.name}") from exc


def _source_record(record: dict[str, object]) -> dict[str, object]:
    return {"sha256": record.get("sha256"), "size": record.get("size")}


def _verify_source_rpms(
    lock: dict[str, object],
    baseline_path: Path,
    candidate_path: Path,
    manifest_reader: Callable[..., list[dict[str, object]]],
) -> dict[str, object]:
    baseline_records = manifest_reader(
        baseline_path, lock["baseline_artifacts"]["source_rpm"], absolute_paths=False
    )
    candidate_records = manifest_reader(
        candidate_path, lock["candidate_artifacts"]["source_rpm"], absolute_paths=False
    )
    source_lock = lock["source_rpm"]
    if len(baseline_records) != source_lock["baseline_entry_count"] or len(candidate_records) != source_lock["candidate_entry_count"]:
        raise CandidateError("source RPM entry count differs")
    baseline = {str(record["path"]): record for record in baseline_records}
    candidate = {str(record["path"]): record for record in candidate_records}
    expected_baseline = set(source_lock["unchanged_members"]) | {str(source_lock["spec_filename"])}
    expected_candidate = expected_baseline | {str(source_lock["patch_filename"])}
    if set(baseline) != expected_baseline or set(candidate) != expected_candidate:
        raise CandidateError("source RPM member set differs")
    for name, expected in source_lock["unchanged_members"].items():
        if _source_record(baseline[name]) != expected or candidate[name] != baseline[name]:
            raise CandidateError(f"source RPM unchanged member differs: {name}")
    spec_name = str(source_lock["spec_filename"])
    patch_name = str(source_lock["patch_filename"])
    if baseline[spec_name].get("sha256") != source_lock["baseline_spec_sha256"]:
        raise CandidateError("baseline source RPM spec differs")
    if candidate[spec_name].get("sha256") != source_lock["candidate_spec_sha256"]:
        raise CandidateError("candidate source RPM spec differs")
    if candidate[patch_name].get("sha256") != source_lock["added_patch_sha256"]:
        raise CandidateError("candidate source RPM patch differs")
    if any(record.get("type") != "file" or record.get("mode") != 0o644 for record in (*baseline_records, *candidate_records)):
        raise CandidateError("source RPM contains a non-regular or non-0644 member")
    return {
        "baseline_entry_count": len(baseline_records),
        "candidate_entry_count": len(candidate_records),
        "unchanged_input_count": len(source_lock["unchanged_members"]),
        "candidate_additions": [patch_name],
        "candidate_modifications": [spec_name],
        "all_other_members_identical": True,
    }


def _verify_environment(
    lock: dict[str, object], artifact_dir: Path, artifacts: dict[str, dict[str, object]]
) -> dict[str, object]:
    env = lock["environment"]
    compiler = _text(artifact_dir / str(artifacts["compiler_summary"]["filename"]), 4096).splitlines()
    if compiler != env["compiler_lines"]:
        raise CandidateError("compiler summary differs")
    packages = _text(artifact_dir / str(artifacts["package_manifest"]["filename"]), 1_048_576).splitlines()
    if len(packages) != env["installed_package_count"] or packages != sorted(set(packages)):
        raise CandidateError("installed package manifest is not the locked sorted unique set")
    wrapper_lines = _text(artifact_dir / str(artifacts["wrapper_manifest"]["filename"]), 4096).splitlines()
    expected_wrappers = [
        f"{env['date_wrapper_sha256']}  /usr/local/bin/date",
        f"{env['make_wrapper_sha256']}  /usr/local/bin/make",
    ]
    if wrapper_lines != expected_wrappers:
        raise CandidateError("wrapper manifest differs")
    return {"installed_package_count": len(packages), "compiler_lines": compiler, "wrappers_locked": True}


def _replace_once(content: bytes, before: bytes, after: bytes, name: str) -> bytes:
    if content.count(before) != 1:
        raise CandidateError(f"baseline spec {name} anchor is not unique")
    return content.replace(before, after, 1)


def _verify_specs(lock: dict[str, object], candidate_dir: Path) -> dict[str, object]:
    baseline_path = candidate_dir / str(lock["candidate_artifacts"]["baseline_spec"]["filename"])
    candidate_path = candidate_dir / str(lock["candidate_artifacts"]["candidate_spec"]["filename"])
    baseline, _ = payload._read_regular(baseline_path, maximum=1_048_576)
    candidate, _ = payload._read_regular(candidate_path, maximum=1_048_576)
    expected = _replace_once(
        baseline,
        b"Source2:        pocketds-initramfs.tar.zst\n\n\nBuildRequires:",
        b"Source2:        pocketds-initramfs.tar.zst\nPatch0:         0002-revert-a740-ifpc.patch\n\n\nBuildRequires:",
        "Patch0",
    )
    expected = _replace_once(
        expected,
        b"%setup -q -n linux-pocketds-v%{tarfile_release}\n\n\n# Drop the .config",
        b"%setup -q -n linux-pocketds-v%{tarfile_release}\n\npatch --batch --fuzz=0 --reject-file=- -p1 < %{PATCH0}\n\n\n# Drop the .config",
        "%prep",
    )
    if candidate != expected:
        raise CandidateError("candidate spec has changes beyond the locked Patch0 application")
    return {"baseline_size": len(baseline), "candidate_size": len(candidate), "only_patch_declaration_and_application_changed": True}


def _verify_build_log(
    lock: dict[str, object], artifact_dir: Path, artifacts: dict[str, dict[str, object]]
) -> dict[str, object]:
    log_path = artifact_dir / str(artifacts["build_log"]["filename"])
    log = _text(log_path)
    patch_marker = "patching file drivers/gpu/drm/msm/adreno/a6xx_catalog.c"
    output_marker = f"Wrote: /builddir/build/RPMS/{lock['package_nevra'].replace('.aarch64', '')}.aarch64.rpm"
    if log.count(patch_marker) != 1:
        raise CandidateError("build log does not contain exactly one target patch marker")
    if log.count(output_marker) != 1:
        raise CandidateError("build log does not contain exactly one candidate RPM marker")
    if "RPM build errors:" in log:
        raise CandidateError("build log contains RPM build errors")
    return {"target_patch_marker_count": 1, "candidate_rpm_marker_count": 1, "rpm_build_errors": False}


def _verify_tree_summary(summary: dict[str, object], lock: dict[str, object], kind: str) -> None:
    expected = lock["payload"]
    expected_manifest = expected[f"{kind}_manifest_sha256"]
    if summary["manifest_sha256"] != expected_manifest:
        raise CandidateError(f"{kind} payload manifest differs")
    for field in ("entry_count", "regular_file_count", "directory_count", "symlink_count", "regular_file_bytes"):
        if summary[field] != expected[field]:
            raise CandidateError(f"{kind} payload {field} differs")


def _verify_payload_delta(
    lock: dict[str, object], baseline_records: list[dict[str, object]], candidate_records: list[dict[str, object]]
) -> dict[str, object]:
    baseline = {str(record["path"]): record for record in baseline_records}
    candidate = {str(record["path"]): record for record in candidate_records}
    if set(baseline) != set(candidate):
        raise CandidateError("candidate payload path set differs")
    changed_paths = sorted(path for path in baseline if baseline[path] != candidate[path])
    expected_changed = sorted(str(value["path"]) for value in lock["payload"]["changed_files"].values())
    if changed_paths != expected_changed:
        raise CandidateError("candidate payload changed-file set differs")
    for name, expected in lock["payload"]["changed_files"].items():
        path = str(expected["path"])
        left = baseline[path]
        right = candidate[path]
        if left.get("type") != "file" or right.get("type") != "file" or left.get("mode") != right.get("mode"):
            raise CandidateError(f"changed payload metadata differs: {name}")
        if left.get("size") != expected["size"] or right.get("size") != expected["size"]:
            raise CandidateError(f"changed payload size differs: {name}")
        if left.get("sha256") != expected["baseline_sha256"] or right.get("sha256") != expected["candidate_sha256"]:
            raise CandidateError(f"changed payload hash differs: {name}")
    return {
        "changed_paths": changed_paths,
        "unchanged_entry_count": len(baseline) - len(changed_paths),
        "unchanged_regular_file_count": sum(record["type"] == "file" and baseline[path] == record for path, record in candidate.items()),
    }


def _tree_file(root: Path, relative: str) -> bytes:
    content, _metadata = payload._read_regular(root / relative, maximum=payload.MAX_TREE_BYTES, require_nonempty=False)
    return content


def _verify_raw_delta(lock: dict[str, object], baseline_root: Path, candidate_root: Path) -> dict[str, object]:
    changed = lock["payload"]["changed_files"]["raw_image"]
    left = _tree_file(baseline_root, str(changed["path"]))
    right = _tree_file(candidate_root, str(changed["path"]))
    if len(left) != len(right) or len(left) != changed["size"]:
        raise CandidateError("raw Image size differs")
    delta = lock["payload"]["raw_image_delta"]
    expected_offsets: set[int] = set()
    measured_slices: dict[str, dict[str, object]] = {}
    for prefix in ("quirk", "ifpc_pointer", "build_id", "relr"):
        offset = int(delta[f"{prefix}_offset"])
        baseline_bytes = bytes.fromhex(str(delta[f"baseline_{prefix}_hex"]))
        candidate_bytes = bytes.fromhex(str(delta[f"candidate_{prefix}_hex"]))
        if left[offset : offset + len(baseline_bytes)] != baseline_bytes or right[offset : offset + len(candidate_bytes)] != candidate_bytes:
            raise CandidateError(f"raw Image {prefix} slice differs")
        expected_offsets.update(offset + index for index, pair in enumerate(zip(baseline_bytes, candidate_bytes)) if pair[0] != pair[1])
        measured_slices[prefix] = {"offset": offset, "baseline_hex": baseline_bytes.hex(), "candidate_hex": candidate_bytes.hex()}
    actual_offsets = {index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]}
    if actual_offsets != expected_offsets or len(actual_offsets) != delta["different_byte_count"]:
        raise CandidateError("raw Image has a byte difference outside the locked slices")
    return {"different_byte_count": len(actual_offsets), "slices": measured_slices, "no_other_byte_difference": True}


def _parse_boot_image(content: bytes, expected: dict[str, object], raw_image: bytes, name: str) -> dict[str, object]:
    if len(content) < 2048 or content[:8] != b"ANDROID!":
        raise CandidateError(f"{name} boot image is not Android boot image v0")
    kernel_size = struct.unpack_from("<I", content, 8)[0]
    page_size = struct.unpack_from("<I", content, 36)[0]
    if page_size != expected["page_size"] or kernel_size != expected[f"{name}_kernel_size"]:
        raise CandidateError(f"{name} boot image header differs")
    if len(content) != page_size + ((kernel_size + page_size - 1) // page_size) * page_size:
        raise CandidateError(f"{name} boot image size/alignment differs")
    blob = content[page_size : page_size + kernel_size]
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        unpacked = inflater.decompress(blob) + inflater.flush()
    except zlib.error as exc:
        raise CandidateError(f"{name} boot kernel is not valid gzip") from exc
    if not inflater.eof or not inflater.unused_data:
        raise CandidateError(f"{name} boot image has no exact gzip/DTB boundary")
    dtb = inflater.unused_data
    gzip_bytes = blob[: len(blob) - len(dtb)]
    if unpacked != raw_image:
        raise CandidateError(f"{name} boot gzip does not reproduce raw Image")
    if len(gzip_bytes) != expected[f"{name}_gzip_size"] or hashlib.sha256(gzip_bytes).hexdigest() != expected[f"{name}_gzip_sha256"]:
        raise CandidateError(f"{name} boot gzip differs")
    if len(dtb) != expected["dtb_size"] or hashlib.sha256(dtb).hexdigest() != expected["dtb_sha256"]:
        raise CandidateError(f"{name} appended DTB differs")
    image_id = content[576:608]
    if image_id.hex() != expected[f"{name}_id_hex"]:
        raise CandidateError(f"{name} boot image ID differs")
    return {"kernel_size": kernel_size, "gzip_size": len(gzip_bytes), "gzip_sha256": hashlib.sha256(gzip_bytes).hexdigest(), "dtb_size": len(dtb), "dtb_sha256": hashlib.sha256(dtb).hexdigest(), "id_hex": image_id.hex(), "header": content[:page_size]}


def _verify_boot_derivation(lock: dict[str, object], baseline_root: Path, candidate_root: Path) -> dict[str, object]:
    files = lock["payload"]["changed_files"]
    baseline_boot = _tree_file(baseline_root, str(files["boot_image"]["path"]))
    candidate_boot = _tree_file(candidate_root, str(files["boot_image"]["path"]))
    baseline_raw = _tree_file(baseline_root, str(files["raw_image"]["path"]))
    candidate_raw = _tree_file(candidate_root, str(files["raw_image"]["path"]))
    expected = lock["payload"]["boot_derivation"]
    left = _parse_boot_image(baseline_boot, expected, baseline_raw, "baseline")
    right = _parse_boot_image(candidate_boot, expected, candidate_raw, "candidate")
    actual_header_offsets = {index for index, pair in enumerate(zip(left.pop("header"), right.pop("header"))) if pair[0] != pair[1]}
    baseline_size = struct.pack("<I", int(expected["baseline_kernel_size"]))
    candidate_size = struct.pack("<I", int(expected["candidate_kernel_size"]))
    baseline_id = bytes.fromhex(str(expected["baseline_id_hex"]))
    candidate_id = bytes.fromhex(str(expected["candidate_id_hex"]))
    expected_offsets = {8 + index for index, pair in enumerate(zip(baseline_size, candidate_size)) if pair[0] != pair[1]}
    expected_offsets.update(576 + index for index, pair in enumerate(zip(baseline_id, candidate_id)) if pair[0] != pair[1])
    if actual_header_offsets != expected_offsets or len(actual_header_offsets) != expected["header_different_byte_count"]:
        raise CandidateError("boot image header has a byte difference outside size and ID")
    if left["dtb_sha256"] != right["dtb_sha256"]:
        raise CandidateError("baseline and candidate boot images append different DTBs")
    return {"baseline": left, "candidate": right, "header_different_byte_count": len(actual_header_offsets), "only_kernel_size_and_id_header_fields_changed": True, "both_boot_images_derive_from_locked_raw_images": True}


def collect(
    lock: dict[str, object],
    *,
    baseline_artifact_dir: Path,
    candidate_artifact_dir: Path,
    reproduced_candidate_artifact_dir: Path,
    baseline_root: Path,
    candidate_root: Path,
    reproduced_candidate_root: Path,
    rpm_manifest_reader: Callable[..., list[dict[str, object]]] = payload.read_rpm_manifest,
) -> dict[str, object]:
    measured_baseline = {name: _hash_record(baseline_artifact_dir / str(record["filename"]), record) for name, record in lock["baseline_artifacts"].items()}
    measured_candidate = {name: _hash_record(candidate_artifact_dir / str(record["filename"]), record) for name, record in lock["candidate_artifacts"].items()}
    measured_reproduced = {
        name: _hash_record(reproduced_candidate_artifact_dir / str(record["filename"]), record)
        for name, record in lock["reproduced_candidate_artifacts"].items()
    }
    measured_recipes = {name: _hash_recipe((HERE / str(record["path"])).resolve(), record) for name, record in lock["recipe_files"].items()}
    source_evidence = _verify_source_rpms(
        lock,
        baseline_artifact_dir / str(lock["baseline_artifacts"]["source_rpm"]["filename"]),
        candidate_artifact_dir / str(lock["candidate_artifacts"]["source_rpm"]["filename"]),
        rpm_manifest_reader,
    )
    environment = _verify_environment(lock, candidate_artifact_dir, lock["candidate_artifacts"])
    reproduced_environment = _verify_environment(
        lock,
        reproduced_candidate_artifact_dir,
        lock["reproduced_candidate_artifacts"],
    )
    specs = _verify_specs(lock, candidate_artifact_dir)
    build_log = _verify_build_log(lock, candidate_artifact_dir, lock["candidate_artifacts"])
    reproduced_build_log = _verify_build_log(
        lock,
        reproduced_candidate_artifact_dir,
        lock["reproduced_candidate_artifacts"],
    )
    baseline_records, baseline_summary = payload.scan_tree(baseline_root)
    candidate_records, candidate_summary = payload.scan_tree(candidate_root)
    reproduced_records, reproduced_summary = payload.scan_tree(reproduced_candidate_root)
    _verify_tree_summary(baseline_summary, lock, "baseline")
    _verify_tree_summary(candidate_summary, lock, "candidate")
    _verify_tree_summary(reproduced_summary, lock, "candidate")
    if reproduced_records != candidate_records:
        raise CandidateError("independently reproduced candidate payload differs")
    baseline_package = rpm_manifest_reader(
        baseline_artifact_dir / str(lock["baseline_artifacts"]["reference_rpm"]["filename"]),
        lock["baseline_artifacts"]["reference_rpm"],
    )
    candidate_package = rpm_manifest_reader(
        candidate_artifact_dir / str(lock["candidate_artifacts"]["binary_rpm"]["filename"]),
        lock["candidate_artifacts"]["binary_rpm"],
    )
    reproduced_package = rpm_manifest_reader(
        reproduced_candidate_artifact_dir
        / str(lock["reproduced_candidate_artifacts"]["binary_rpm"]["filename"]),
        lock["reproduced_candidate_artifacts"]["binary_rpm"],
    )
    baseline_implicit = payload._bind_package_to_tree(baseline_package, baseline_records)
    candidate_implicit = payload._bind_package_to_tree(candidate_package, candidate_records)
    reproduced_implicit = payload._bind_package_to_tree(reproduced_package, reproduced_records)
    if len(baseline_package) != lock["payload"]["rpm_entry_count"] or len(candidate_package) != lock["payload"]["rpm_entry_count"]:
        raise CandidateError("binary RPM entry count differs")
    if reproduced_package != candidate_package:
        raise CandidateError("independently reproduced RPM header payload differs")
    if (
        baseline_implicit != candidate_implicit
        or candidate_implicit != reproduced_implicit
        or len(baseline_implicit) != lock["payload"]["implicit_directory_count"]
    ):
        raise CandidateError("implicit extraction directories differ")
    payload_delta = _verify_payload_delta(lock, baseline_records, candidate_records)
    if payload_delta["unchanged_entry_count"] != lock["payload"]["unchanged_entry_count"] or payload_delta["unchanged_regular_file_count"] != lock["payload"]["unchanged_regular_file_count"]:
        raise CandidateError("unchanged payload counts differ")
    raw_delta = _verify_raw_delta(lock, baseline_root, candidate_root)
    boot_derivation = _verify_boot_derivation(lock, baseline_root, candidate_root)
    return {
        "schema": REPORT_SCHEMA,
        "read_only": True,
        "network": False,
        "experiment": {"id": lock["experiment_id"], "source_commit": lock["source_commit"], "revert_commit": lock["revert_commit"], "package_nevra": lock["package_nevra"]},
        "artifacts": {
            "baseline": measured_baseline,
            "candidate": measured_candidate,
            "reproduced_candidate": measured_reproduced,
        },
        "recipe_files": measured_recipes,
        "environment": environment,
        "reproduced_environment": reproduced_environment,
        "source_rpm": source_evidence,
        "spec_delta": specs,
        "build_log": build_log,
        "reproduced_build_log": reproduced_build_log,
        "payload": {
            "baseline": baseline_summary,
            "candidate": candidate_summary,
            "reproduced_candidate": reproduced_summary,
            "rpm_entry_count": len(baseline_package),
            "implicit_directories": baseline_implicit,
            "candidate_rpm_header_manifest_reproduced": True,
            "candidate_payload_reproduced": True,
            **payload_delta,
        },
        "raw_image_delta": raw_delta,
        "boot_derivation": boot_derivation,
        "gates": {
            "candidate_built": True,
            "source_inputs_except_revert_identical": True,
            "single_variable_payload_delta_proven": True,
            "candidate_independently_reproduced": True,
            "rollback_artifact_prevalidated": False,
            "candidate_install_authorized": False,
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-artifact-dir", type=Path, required=True)
    parser.add_argument("--candidate-artifact-dir", type=Path, required=True)
    parser.add_argument("--reproduced-candidate-artifact-dir", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--reproduced-candidate-root", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        raw, lock_sha256 = payload.load_lock(HERE / "ifpc-lock.json")
        lock = validate_lock(raw)
        report = collect(
            lock,
            baseline_artifact_dir=args.baseline_artifact_dir,
            candidate_artifact_dir=args.candidate_artifact_dir,
            reproduced_candidate_artifact_dir=args.reproduced_candidate_artifact_dir,
            baseline_root=args.baseline_root,
            candidate_root=args.candidate_root,
            reproduced_candidate_root=args.reproduced_candidate_root,
        )
        report["lock_sha256"] = lock_sha256
    except (OSError, CandidateError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
