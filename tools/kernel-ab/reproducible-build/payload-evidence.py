#!/usr/bin/env python3
"""Replay the locked PDS-002 kernel payload reproduction evidence offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Callable


LOCK_SCHEMA_VERSION = 1
REPORT_SCHEMA = "pocketds.kernel-payload-reproduction.v1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_LOCK_BYTES = 131_072
MAX_ARTIFACT_BYTES = 768 * 1024 * 1024
MAX_TREE_ENTRIES = 10_000
MAX_TREE_BYTES = 1024 * 1024 * 1024
LOCK_FIELDS = {
    "schema_version",
    "copr_build_id",
    "package_nevra",
    "source_commit",
    "source_date_epoch",
    "environment",
    "artifacts",
    "recipe_files",
    "expected_payload",
}
ENVIRONMENT_FIELDS = {
    "architecture",
    "distribution",
    "original_mock_version",
    "reproducer_mock_version",
    "compiler_nevra",
    "libfaketime_nevra",
    "build_user",
    "build_host",
    "build_timestamp",
    "initramfs_regular_mtime_epoch",
    "initramfs_generated_mtime_epoch",
}
PAYLOAD_FIELDS = {
    "manifest_sha256",
    "entry_count",
    "regular_file_count",
    "directory_count",
    "symlink_count",
    "regular_file_bytes",
    "rpm_entry_count",
    "implicit_directory_count",
    "key_files",
}
KEY_FILE_NAMES = {"raw_image", "boot_image", "system_map", "dtb", "config"}
ARTIFACT_NAMES = {
    "source_rpm",
    "reference_rpm",
    "attested_rebuilt_rpm",
    "cpp",
    "gcc",
    "gcc_cxx",
    "gcc_plugin_annobin",
    "libatomic",
    "libfaketime",
    "libgcc",
    "libgomp",
    "libstdcxx",
    "libstdcxx_devel",
}
RECIPE_FILE_NAMES = {
    "copr_child_config",
    "mock_overlay",
    "date_wrapper",
    "make_wrapper",
}


class ReproductionError(RuntimeError):
    """A locked input or extracted payload is unsafe, incomplete, or different."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_json(content: bytes, where: str) -> object:
    def reject_constant(value: str) -> object:
        raise ReproductionError(f"{where} contains non-finite JSON: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ReproductionError(f"{where} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReproductionError(f"{where} is not strict UTF-8 JSON") from exc


def _read_regular(path: Path, *, maximum: int, require_nonempty: bool = True) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReproductionError(f"file is unavailable or linked: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        unsafe_mode = stat.S_IMODE(before.st_mode) & 0o022
        if (
            not stat.S_ISREG(before.st_mode)
            or (require_nonempty and before.st_size <= 0)
            or before.st_size < 0
            or before.st_size > maximum
            or unsafe_mode
        ):
            raise ReproductionError(f"file type, size, or mode is unsafe: {path.name}")
        content = bytearray()
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                raise ReproductionError(f"file changed while reading: {path.name}")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ReproductionError(f"file grew while reading: {path.name}")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
        ):
            raise ReproductionError(f"file identity changed while reading: {path.name}")
        return bytes(content), before
    finally:
        os.close(descriptor)


def load_lock(path: Path) -> tuple[dict[str, object], str]:
    content, _metadata = _read_regular(path, maximum=MAX_LOCK_BYTES)
    parsed = _strict_json(content, "reproduction lock")
    if not isinstance(parsed, dict):
        raise ReproductionError("reproduction lock root must be an object")
    return parsed, hashlib.sha256(content).hexdigest()


def _string(value: object, name: str, *, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ReproductionError(f"invalid {name}")
    return value


def _positive_int(value: object, name: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ReproductionError(f"invalid {name}")
    return value


def _record(value: object, name: str, *, maximum: int = MAX_ARTIFACT_BYTES) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"filename", "sha256", "size"}:
        raise ReproductionError(f"invalid {name} record")
    filename = _string(value["filename"], f"{name} filename")
    if filename != Path(filename).name or "/" in filename or "\\" in filename:
        raise ReproductionError(f"invalid {name} filename")
    digest = _string(value["sha256"], f"{name} SHA-256")
    if HEX64.fullmatch(digest) is None:
        raise ReproductionError(f"invalid {name} SHA-256")
    size = _positive_int(value["size"], f"{name} size")
    if size > maximum:
        raise ReproductionError(f"invalid {name} size")
    return {"filename": filename, "sha256": digest, "size": size}


def _relative_path(value: object, name: str) -> str:
    text = _string(value, name)
    path = PurePosixPath(text)
    if path.is_absolute() or text != str(path) or any(part in {".", ".."} for part in path.parts):
        raise ReproductionError(f"invalid {name}")
    return text


def validate_lock(raw: dict[str, object]) -> dict[str, object]:
    if set(raw) != LOCK_FIELDS:
        raise ReproductionError("reproduction lock fields differ")
    if raw["schema_version"] != LOCK_SCHEMA_VERSION:
        raise ReproductionError("unsupported reproduction lock schema")
    source_commit = _string(raw["source_commit"], "source commit")
    if HEX40.fullmatch(source_commit) is None:
        raise ReproductionError("source commit is not a full lowercase commit")
    environment = raw["environment"]
    if not isinstance(environment, dict) or set(environment) != ENVIRONMENT_FIELDS:
        raise ReproductionError("build environment fields differ")
    clean_environment: dict[str, object] = {}
    epoch_fields = {
        "initramfs_regular_mtime_epoch",
        "initramfs_generated_mtime_epoch",
    }
    for name in sorted(ENVIRONMENT_FIELDS):
        if name in epoch_fields:
            clean_environment[name] = _positive_int(environment[name], name)
        else:
            clean_environment[name] = _string(environment[name], name)
    artifacts = raw["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != ARTIFACT_NAMES:
        raise ReproductionError("artifact set differs")
    clean_artifacts = {name: _record(value, name) for name, value in sorted(artifacts.items())}
    if len({item["filename"] for item in clean_artifacts.values()}) != len(clean_artifacts):
        raise ReproductionError("artifact filenames are not unique")
    recipes = raw["recipe_files"]
    if not isinstance(recipes, dict) or set(recipes) != RECIPE_FILE_NAMES:
        raise ReproductionError("recipe file set differs")
    clean_recipes: dict[str, dict[str, object]] = {}
    for name, value in sorted(recipes.items()):
        record = _record(value, name, maximum=1_048_576)
        record["filename"] = _relative_path(record["filename"], f"{name} filename")
        clean_recipes[name] = record
    payload = raw["expected_payload"]
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS:
        raise ReproductionError("expected payload fields differ")
    manifest_sha = _string(payload["manifest_sha256"], "payload manifest SHA-256")
    if HEX64.fullmatch(manifest_sha) is None:
        raise ReproductionError("payload manifest SHA-256 is invalid")
    keys = payload["key_files"]
    if not isinstance(keys, dict) or set(keys) != KEY_FILE_NAMES:
        raise ReproductionError("key payload file set differs")
    clean_keys: dict[str, dict[str, object]] = {}
    for name, value in sorted(keys.items()):
        if not isinstance(value, dict) or set(value) != {"path", "sha256", "size"}:
            raise ReproductionError(f"invalid key file record: {name}")
        path = _relative_path(value["path"], f"{name} path")
        digest = _string(value["sha256"], f"{name} SHA-256")
        if HEX64.fullmatch(digest) is None:
            raise ReproductionError(f"invalid {name} SHA-256")
        clean_keys[name] = {
            "path": path,
            "sha256": digest,
            "size": _positive_int(value["size"], f"{name} size"),
        }
    clean_payload = {
        "manifest_sha256": manifest_sha,
        "entry_count": _positive_int(payload["entry_count"], "payload entry count"),
        "regular_file_count": _positive_int(payload["regular_file_count"], "regular file count"),
        "directory_count": _positive_int(payload["directory_count"], "directory count", allow_zero=True),
        "symlink_count": _positive_int(payload["symlink_count"], "symlink count", allow_zero=True),
        "regular_file_bytes": _positive_int(payload["regular_file_bytes"], "regular file bytes"),
        "rpm_entry_count": _positive_int(payload["rpm_entry_count"], "RPM entry count"),
        "implicit_directory_count": _positive_int(
            payload["implicit_directory_count"], "implicit directory count", allow_zero=True
        ),
        "key_files": clean_keys,
    }
    if clean_payload["entry_count"] != sum(
        clean_payload[name]
        for name in ("regular_file_count", "directory_count", "symlink_count")
    ):
        raise ReproductionError("payload type counts do not sum to entry count")
    if clean_payload["entry_count"] != (
        clean_payload["rpm_entry_count"] + clean_payload["implicit_directory_count"]
    ):
        raise ReproductionError("RPM and implicit counts do not sum to entry count")
    return {
        "schema_version": LOCK_SCHEMA_VERSION,
        "copr_build_id": _positive_int(raw["copr_build_id"], "COPR build ID"),
        "package_nevra": _string(raw["package_nevra"], "package NEVRA"),
        "source_commit": source_commit,
        "source_date_epoch": _positive_int(raw["source_date_epoch"], "source date epoch"),
        "environment": clean_environment,
        "artifacts": clean_artifacts,
        "recipe_files": clean_recipes,
        "expected_payload": clean_payload,
    }


def _hash_path(path: Path, expected: dict[str, object]) -> dict[str, object]:
    content, metadata = _read_regular(path, maximum=MAX_ARTIFACT_BYTES)
    measured = {
        "filename": path.name,
        "size": metadata.st_size,
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    if measured != expected:
        raise ReproductionError(f"locked file differs: {path.name}")
    return measured


def _hash_tree_file(path: Path, relative: str) -> dict[str, object]:
    content, metadata = _read_regular(path, maximum=MAX_TREE_BYTES, require_nonempty=False)
    return {
        "path": relative,
        "type": "file",
        "mode": stat.S_IMODE(metadata.st_mode),
        "size": metadata.st_size,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def scan_tree(root: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise ReproductionError("payload root is unavailable") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_IMODE(root_stat.st_mode) & 0o022:
        raise ReproductionError("payload root type or mode is unsafe")
    records: list[dict[str, object]] = []
    stack: list[tuple[Path, PurePosixPath]] = [(root, PurePosixPath())]
    regular_bytes = 0
    while stack:
        directory, prefix = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: os.fsencode(item.name))
        except OSError as exc:
            raise ReproductionError(f"cannot scan payload directory: {prefix}") from exc
        for entry in entries:
            relative_path = prefix / entry.name
            relative = str(relative_path)
            if relative.startswith("/") or any(part in {".", ".."} for part in relative_path.parts):
                raise ReproductionError("payload contains an unsafe path")
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ReproductionError(f"cannot stat payload entry: {relative}") from exc
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISREG(metadata.st_mode):
                record = _hash_tree_file(Path(entry.path), relative)
                regular_bytes += int(record["size"])
            elif stat.S_ISDIR(metadata.st_mode):
                record = {"path": relative, "type": "directory", "mode": mode}
                stack.append((Path(entry.path), relative_path))
            elif stat.S_ISLNK(metadata.st_mode):
                try:
                    target = os.readlink(entry.path)
                    after = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ReproductionError(f"cannot read payload symlink: {relative}") from exc
                if (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_mtime_ns,
                ):
                    raise ReproductionError(f"payload symlink changed while reading: {relative}")
                record = {"path": relative, "type": "symlink", "mode": mode, "target": target}
            else:
                raise ReproductionError(f"unsupported payload entry type: {relative}")
            records.append(record)
            if len(records) > MAX_TREE_ENTRIES or regular_bytes > MAX_TREE_BYTES:
                raise ReproductionError("payload tree exceeds safety limits")
    records.sort(key=lambda item: os.fsencode(str(item["path"])))
    manifest = hashlib.sha256()
    for record in records:
        manifest.update(canonical_bytes(record))
        manifest.update(b"\n")
    counts = {
        "manifest_sha256": manifest.hexdigest(),
        "entry_count": len(records),
        "regular_file_count": sum(item["type"] == "file" for item in records),
        "directory_count": sum(item["type"] == "directory" for item in records),
        "symlink_count": sum(item["type"] == "symlink" for item in records),
        "regular_file_bytes": regular_bytes,
    }
    return records, counts


def parse_rpm_query(
    output: str, *, absolute_paths: bool = True
) -> list[dict[str, object]]:
    lines = output.splitlines()
    if not lines or lines[0] != "8":
        raise ReproductionError("RPM payload does not declare SHA-256 file digests")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 5:
            raise ReproductionError("RPM payload query output is malformed")
        filename, size_text, mode_text, digest, target = fields
        if any(character in filename + target for character in ("\0", "\n", "\r", "\t")):
            raise ReproductionError("RPM payload contains a control character")
        if absolute_paths:
            if not filename.startswith("/") or filename.startswith("//"):
                raise ReproductionError("RPM payload contains an unsafe path")
            relative = _relative_path(filename[1:], "RPM payload path")
            if filename != "/" + relative:
                raise ReproductionError("RPM payload path is not canonical")
        else:
            relative = _relative_path(filename, "RPM source payload path")
            if filename != relative:
                raise ReproductionError("RPM source payload path is not canonical")
        if not relative or relative in seen:
            raise ReproductionError("RPM payload contains an empty or duplicate path")
        seen.add(relative)
        try:
            size = int(size_text, 10)
            full_mode = int(mode_text, 10)
        except ValueError as exc:
            raise ReproductionError("RPM payload size or mode is malformed") from exc
        mode = stat.S_IMODE(full_mode)
        if stat.S_ISREG(full_mode):
            if size < 0 or HEX64.fullmatch(digest) is None or target:
                raise ReproductionError("RPM regular-file metadata is invalid")
            record = {
                "path": relative,
                "type": "file",
                "mode": mode,
                "size": size,
                "sha256": digest,
            }
        elif stat.S_ISDIR(full_mode):
            if size != 0 or digest or target:
                raise ReproductionError("RPM directory metadata is invalid")
            record = {"path": relative, "type": "directory", "mode": mode}
        elif stat.S_ISLNK(full_mode):
            if not target or digest or size != len(os.fsencode(target)):
                raise ReproductionError("RPM symlink metadata is invalid")
            record = {"path": relative, "type": "symlink", "mode": mode, "target": target}
        else:
            raise ReproductionError(f"unsupported RPM payload entry type: {relative}")
        records.append(record)
        if len(records) > MAX_TREE_ENTRIES:
            raise ReproductionError("RPM payload exceeds the entry limit")
    records.sort(key=lambda item: os.fsencode(str(item["path"])))
    return records


def read_rpm_manifest(
    path: Path,
    expected: dict[str, object],
    *,
    absolute_paths: bool = True,
) -> list[dict[str, object]]:
    rpm = Path("/usr/bin/rpm")
    try:
        metadata = rpm.stat()
    except OSError as exc:
        raise ReproductionError("/usr/bin/rpm is required for payload binding") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ReproductionError("/usr/bin/rpm type or mode is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReproductionError(f"RPM is unavailable or linked: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_ARTIFACT_BYTES
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise ReproductionError(f"RPM type, size, or mode is unsafe: {path.name}")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                raise ReproductionError(f"RPM changed while hashing: {path.name}")
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ReproductionError(f"RPM grew while hashing: {path.name}")
        measured = {
            "filename": path.name,
            "size": before.st_size,
            "sha256": digest.hexdigest(),
        }
        if measured != expected:
            raise ReproductionError(f"locked file differs: {path.name}")
        os.lseek(descriptor, 0, os.SEEK_SET)
        query = "%{FILEDIGESTALGO}\\n[%{FILENAMES}\\t%{FILESIZES}\\t%{FILEMODES}\\t%{FILEDIGESTS}\\t%{FILELINKTOS}\\n]"
        result = subprocess.run(
            [str(rpm), "-qp", "--qf", query, f"/proc/self/fd/{descriptor}"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            pass_fds=(descriptor,),
            timeout=30,
        )
        after = os.fstat(descriptor)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ReproductionError(f"cannot query RPM payload: {path.name}") from exc
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_mode,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_mode,
    ):
        raise ReproductionError(f"RPM identity changed while querying: {path.name}")
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 2_000_000:
        raise ReproductionError(f"RPM payload query failed: {path.name}")
    return parse_rpm_query(result.stdout, absolute_paths=absolute_paths)


def _bind_package_to_tree(
    package_records: list[dict[str, object]],
    tree_records: list[dict[str, object]],
) -> list[str]:
    tree_by_path = {str(item["path"]): item for item in tree_records}
    package_paths = {str(item["path"]) for item in package_records}
    for record in package_records:
        if tree_by_path.get(str(record["path"])) != record:
            raise ReproductionError(f"extracted tree differs from its RPM: {record['path']}")
    implicit: list[str] = []
    for relative, record in sorted(tree_by_path.items()):
        if relative in package_paths:
            continue
        if record["type"] != "directory" or not any(
            path.startswith(relative + "/") for path in package_paths
        ):
            raise ReproductionError(f"extracted tree has an unexpected entry: {relative}")
        implicit.append(relative)
    return implicit


def collect(
    lock: dict[str, object],
    *,
    artifact_dir: Path,
    recipe_root: Path,
    reference_root: Path,
    rebuilt_root: Path,
    package_manifest_reader: Callable[
        [Path, dict[str, object]], list[dict[str, object]]
    ] = read_rpm_manifest,
) -> dict[str, object]:
    measured_artifacts = {
        name: _hash_path(artifact_dir / str(record["filename"]), record)
        for name, record in lock["artifacts"].items()
    }
    measured_recipes = {
        name: _hash_path(recipe_root / str(record["filename"]), record)
        for name, record in lock["recipe_files"].items()
    }
    reference_records, reference_summary = scan_tree(reference_root)
    rebuilt_records, rebuilt_summary = scan_tree(rebuilt_root)
    expected = lock["expected_payload"]
    for field in (
        "manifest_sha256",
        "entry_count",
        "regular_file_count",
        "directory_count",
        "symlink_count",
        "regular_file_bytes",
    ):
        if reference_summary[field] != expected[field]:
            raise ReproductionError(f"reference payload {field} differs from lock")
    if rebuilt_records != reference_records:
        raise ReproductionError("rebuilt payload differs from signed reference payload")
    reference_rpm_path = artifact_dir / str(lock["artifacts"]["reference_rpm"]["filename"])
    rebuilt_rpm_path = artifact_dir / str(
        lock["artifacts"]["attested_rebuilt_rpm"]["filename"]
    )
    reference_package = package_manifest_reader(
        reference_rpm_path, lock["artifacts"]["reference_rpm"]
    )
    rebuilt_package = package_manifest_reader(
        rebuilt_rpm_path, lock["artifacts"]["attested_rebuilt_rpm"]
    )
    if rebuilt_package != reference_package:
        raise ReproductionError("rebuilt RPM header payload differs from signed reference RPM")
    reference_implicit = _bind_package_to_tree(reference_package, reference_records)
    rebuilt_implicit = _bind_package_to_tree(rebuilt_package, rebuilt_records)
    if reference_implicit != rebuilt_implicit:
        raise ReproductionError("implicit extraction directory set differs")
    if len(reference_package) != expected["rpm_entry_count"]:
        raise ReproductionError("RPM entry count differs from lock")
    if len(reference_implicit) != expected["implicit_directory_count"]:
        raise ReproductionError("implicit directory count differs from lock")
    by_path = {str(item["path"]): item for item in reference_records}
    key_files: dict[str, dict[str, object]] = {}
    for name, expected_file in expected["key_files"].items():
        measured = by_path.get(str(expected_file["path"]))
        if measured is None or measured.get("type") != "file":
            raise ReproductionError(f"key payload file is missing: {name}")
        key = {
            "path": measured["path"],
            "sha256": measured["sha256"],
            "size": measured["size"],
        }
        if key != expected_file:
            raise ReproductionError(f"key payload file differs from lock: {name}")
        key_files[name] = key
    reference_rpm = measured_artifacts.get("reference_rpm")
    rebuilt_rpm = measured_artifacts.get("attested_rebuilt_rpm")
    if reference_rpm is None or rebuilt_rpm is None:
        raise ReproductionError("reference and attested rebuilt RPMs must both be locked")
    return {
        "schema": REPORT_SCHEMA,
        "read_only": True,
        "network": False,
        "package": {
            "copr_build_id": lock["copr_build_id"],
            "nevra": lock["package_nevra"],
            "source_commit": lock["source_commit"],
            "source_date_epoch": lock["source_date_epoch"],
        },
        "environment": lock["environment"],
        "artifacts": measured_artifacts,
        "recipe_files": measured_recipes,
        "reference_payload": {**reference_summary, "key_files": key_files},
        "rebuilt_payload": {**rebuilt_summary, "key_files": key_files},
        "rpm_payload_binding": {
            "entry_count": len(reference_package),
            "implicit_directories": reference_implicit,
            "reference_tree_bound_to_reference_rpm": True,
            "rebuilt_tree_bound_to_rebuilt_rpm": True,
            "rpm_header_manifests_byte_identical": True,
        },
        "comparison": {
            "payload_manifest_byte_identical": True,
            "matched_entry_count": reference_summary["entry_count"],
            "matched_regular_file_count": reference_summary["regular_file_count"],
            "rpm_container_byte_identical": (
                reference_rpm["size"] == rebuilt_rpm["size"]
                and reference_rpm["sha256"] == rebuilt_rpm["sha256"]
            ),
        },
        "gates": {
            "signed_reference_hash_bound": True,
            "source_and_toolchain_inputs_locked": True,
            "rebuild_recipe_locked": True,
            "attested_rebuild_rpm_locked": True,
            "complete_kernel_payload_reproduced": True,
            "source_to_binary_reproducible_build_proven": True,
            "rollback_artifact_prevalidated": False,
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--rebuilt-root", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    lock_path = Path(__file__).with_name("lock.json")
    try:
        raw, lock_sha256 = load_lock(lock_path)
        lock = validate_lock(raw)
        report = collect(
            lock,
            artifact_dir=args.artifact_dir,
            recipe_root=Path(__file__).parent,
            reference_root=args.reference_root,
            rebuilt_root=args.rebuilt_root,
        )
        report["lock_sha256"] = lock_sha256
    except (OSError, ReproductionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
