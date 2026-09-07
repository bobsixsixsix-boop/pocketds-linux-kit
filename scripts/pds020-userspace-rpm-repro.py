#!/usr/bin/env python3
"""Verify two locked offline mock rebuilds without building or installing."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.json"
DEFAULT_SOURCE_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.json"
AUDITOR_PATH = ROOT / "scripts/pds020-userspace-rpm-audit.py"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_LOCK_BYTES = 128 * 1024
MAX_RPM_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_MANIFEST_BYTES = 1024 * 1024
MAX_QUERY_BYTES = 64 * 1024
HEADER_FIELDS = (
    ("name", "NAME"),
    ("version", "VERSION"),
    ("release", "RELEASE"),
    ("arch", "ARCH"),
    ("buildhost", "BUILDHOST"),
    ("buildtime", "BUILDTIME"),
    ("payload_sha256", "PAYLOADSHA256"),
)
HEADER_FORMAT = "\\n".join(f"%{{{tag}:json}}" for _, tag in HEADER_FIELDS) + "\\n"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


auditor = load_module("pds020_userspace_repro_auditor", AUDITOR_PATH)


class ReproError(RuntimeError):
    """Rebuild evidence differs from the locked reproducible result."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReproError("build lock contains a duplicate key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise ReproError(f"build lock contains non-finite number {value}")


def strict_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReproError(f"{label} is invalid UTF-8 JSON") from exc


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ReproError(f"{label} fields are incomplete or unsupported")
    return value


def safe_result_directory(path: Path) -> tuple[int, int]:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ReproError("mock result directory is missing or unsafe") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise ReproError("mock result directory must be owned mode-0700")
    return metadata.st_dev, metadata.st_ino


def read_regular(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReproError("rebuild evidence is missing or unsafe") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid not in {0, os.getuid()}
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 0 < before.st_size <= maximum
        ):
            raise ReproError("rebuild evidence metadata is unsafe")
        data = bytearray()
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise ReproError("rebuild evidence changed while being read")
            data.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ReproError("rebuild evidence grew while being read")
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
            raise ReproError("rebuild evidence identity changed while being read")
        return bytes(data)
    finally:
        os.close(descriptor)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_record(value: Any, label: str) -> dict[str, Any]:
    record = exact_object(
        value,
        {"filename", "size", "sha256", "payload_sha256"},
        label,
    )
    if (
        not isinstance(record["filename"], str)
        or Path(record["filename"]).name != record["filename"]
        or type(record["size"]) is not int
        or record["size"] <= 0
        or not isinstance(record["sha256"], str)
        or not SHA256_RE.fullmatch(record["sha256"])
        or not isinstance(record["payload_sha256"], str)
        or not SHA256_RE.fullmatch(record["payload_sha256"])
    ):
        raise ReproError(f"{label} is invalid")
    return record


def run_bounded(command: list[str], maximum: int, label: str) -> bytes:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReproError(f"{label} could not run") from exc
    if result.returncode != 0 or len(result.stdout) > maximum:
        raise ReproError(f"{label} failed or exceeded its output bound")
    return result.stdout


def query_header(rpm: Path, artifact: Path) -> dict[str, Any]:
    if rpm.is_symlink() or not rpm.is_file() or not os.access(rpm, os.X_OK):
        raise ReproError("RPM query tool is missing or unsafe")
    values = auditor.json_lines(
        run_bounded(
            [str(rpm), "-qp", "--qf", HEADER_FORMAT, str(artifact)],
            MAX_QUERY_BYTES,
            "RPM header query",
        ),
        len(HEADER_FIELDS),
        "RPM header query",
    )
    return dict(zip((name for name, _ in HEADER_FIELDS), values, strict=True))


def load_lock(path: Path) -> dict[str, Any]:
    lock = exact_object(
        strict_json(read_regular(path, MAX_LOCK_BYTES), "build lock"),
        {"schema", "package", "builder", "payload_audit"},
        "build lock",
    )
    if type(lock["schema"]) is not int or lock["schema"] != 1:
        raise ReproError("build lock schema is unsupported")
    package = exact_object(
        lock["package"],
        {"name", "version", "release", "arch", "source_result", "binary_result"},
        "build package",
    )
    for key in ("name", "version", "release", "arch"):
        if not isinstance(package[key], str) or not package[key]:
            raise ReproError("build package identity is invalid")
    artifact_record(package["source_result"], "source result")
    artifact_record(package["binary_result"], "binary result")
    builder = exact_object(
        lock["builder"],
        {
            "distro",
            "architecture",
            "mock_config",
            "mock_nvr",
            "offline_rebuild",
            "independent_result_count",
            "macros",
            "buildtime",
            "installed_packages",
        },
        "builder",
    )
    macros = exact_object(
        builder["macros"],
        {"_buildhost", "use_source_date_epoch_as_buildtime"},
        "build macros",
    )
    packages = exact_object(
        builder["installed_packages"],
        {"filename", "size", "sha256"},
        "installed package manifest",
    )
    if (
        builder["distro"] != "fedora-44"
        or builder["architecture"] != "aarch64"
        or builder["mock_config"] != "fedora-44-aarch64"
        or not isinstance(builder["mock_nvr"], str)
        or builder["offline_rebuild"] is not True
        or builder["independent_result_count"] != 2
        or macros["_buildhost"] != "pocketds-build.invalid"
        or macros["use_source_date_epoch_as_buildtime"] != 1
        or type(builder["buildtime"]) is not int
        or builder["buildtime"] <= 0
        or packages["filename"] != "installed_pkgs.log"
        or type(packages["size"]) is not int
        or packages["size"] <= 0
        or not isinstance(packages["sha256"], str)
        or not SHA256_RE.fullmatch(packages["sha256"])
    ):
        raise ReproError("builder lock is invalid")
    payload = exact_object(
        lock["payload_audit"],
        {
            "file_count",
            "manifest_sha256",
            "scriptlet_count",
            "scriptlets_sha256",
            "fan_controller_sha256",
        },
        "payload audit lock",
    )
    if (
        type(payload["file_count"]) is not int
        or payload["file_count"] <= 0
        or type(payload["scriptlet_count"]) is not int
        or payload["scriptlet_count"] < 0
        or any(
            not isinstance(payload[key], str) or not SHA256_RE.fullmatch(payload[key])
            for key in (
                "manifest_sha256",
                "scriptlets_sha256",
                "fan_controller_sha256",
            )
        )
    ):
        raise ReproError("payload audit lock is invalid")
    return lock


def evaluate(
    build_lock_path: Path,
    source_lock_path: Path,
    result_a: Path,
    result_b: Path,
    rpm: Path,
    rpm2archive: Path,
) -> dict[str, Any]:
    lock = load_lock(build_lock_path)
    package = lock["package"]
    builder = lock["builder"]
    payload = lock["payload_audit"]
    identities = [safe_result_directory(path) for path in (result_a, result_b)]
    if identities[0] == identities[1]:
        raise ReproError("two distinct mock result directories are required")
    expected_header = {
        "name": package["name"],
        "version": package["version"],
        "release": package["release"],
        "arch": package["arch"],
        "buildhost": builder["macros"]["_buildhost"],
        "buildtime": builder["buildtime"],
    }
    observed: list[dict[str, Any]] = []
    for result in (result_a, result_b):
        source_record = artifact_record(package["source_result"], "source result")
        binary_record = artifact_record(package["binary_result"], "binary result")
        packages_record = builder["installed_packages"]
        source_path = result / source_record["filename"]
        binary_path = result / binary_record["filename"]
        packages_path = result / packages_record["filename"]
        source_bytes = read_regular(source_path, MAX_RPM_BYTES)
        binary_bytes = read_regular(binary_path, MAX_RPM_BYTES)
        packages_bytes = read_regular(packages_path, MAX_PACKAGE_MANIFEST_BYTES)
        for data, record, label in (
            (source_bytes, source_record, "source result"),
            (binary_bytes, binary_record, "binary result"),
            (packages_bytes, packages_record, "installed package manifest"),
        ):
            if len(data) != record["size"] or sha256(data) != record["sha256"]:
                raise ReproError(f"{label} differs from the build lock")
        source_header = query_header(rpm, source_path)
        binary_header = query_header(rpm, binary_path)
        if (
            {key: source_header.get(key) for key in expected_header} != expected_header
            or source_header.get("payload_sha256") != source_record["payload_sha256"]
            or {key: binary_header.get(key) for key in expected_header} != expected_header
            or binary_header.get("payload_sha256") != binary_record["payload_sha256"]
        ):
            raise ReproError("RPM build header differs from the build lock")
        audit = auditor.audit(source_lock_path, binary_path, rpm, rpm2archive)
        expected_audit = {
            "payload_file_count": payload["file_count"],
            "payload_manifest_sha256": payload["manifest_sha256"],
            "scriptlet_count": payload["scriptlet_count"],
            "scriptlets_sha256": payload["scriptlets_sha256"],
            "fan_controller_sha256": payload["fan_controller_sha256"],
        }
        if (
            audit.get("payload_safe") is not True
            or audit.get("global_nopasswd_absent") is not True
            or audit.get("fan_controller_locked") is not True
            or any(audit.get(key) != value for key, value in expected_audit.items())
        ):
            raise ReproError("binary payload audit differs from the build lock")
        observed.append(
            {
                "source_bytes": source_bytes,
                "binary_bytes": binary_bytes,
                "packages_bytes": packages_bytes,
                "audit": audit,
            }
        )
    if any(
        observed[0][key] != observed[1][key]
        for key in ("source_bytes", "binary_bytes", "packages_bytes", "audit")
    ):
        raise ReproError("the two mock rebuild results differ")
    return {
        "schema": 1,
        "read_only": True,
        "network": False,
        "build": False,
        "install": False,
        "independent_result_count": 2,
        "source_rpm_byte_identical": True,
        "binary_rpm_byte_identical": True,
        "buildroot_package_manifest_identical": True,
        "source_rpm_sha256": package["source_result"]["sha256"],
        "binary_rpm_sha256": package["binary_result"]["sha256"],
        "payload_manifest_sha256": payload["manifest_sha256"],
        "global_nopasswd_absent": True,
        "fan_controller_locked": True,
        "mock_parameters_content_bound_not_signed": True,
        "signature_verified": False,
        "release_ready": False,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-a", type=Path, required=True)
    parser.add_argument("--result-b", type=Path, required=True)
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument("--rpm2archive", type=Path, default=Path("/usr/bin/rpm2archive"))
    parser.add_argument("--build-lock", type=Path, default=DEFAULT_BUILD_LOCK)
    parser.add_argument("--source-lock", type=Path, default=DEFAULT_SOURCE_LOCK)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = evaluate(
            args.build_lock,
            args.source_lock,
            args.result_a,
            args.result_b,
            args.rpm,
            args.rpm2archive,
        )
    except (OSError, UnicodeError, ValueError, ReproError, auditor.AuditError) as exc:
        print(json.dumps({"reproducible": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
