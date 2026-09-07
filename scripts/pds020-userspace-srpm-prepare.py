#!/usr/bin/env python3
"""Verify and prepare the pinned pocketds-userspace spec without building it."""

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
from typing import Any, Sequence
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RELEASE_RE = re.compile(
    r"^(?P<version>[A-Za-z0-9.+_~]+)-"
    r"(?P<release>[A-Za-z0-9.+_~]+)\.(?P<dist>fc[0-9]+)$"
)
HARDENED_SUFFIX_RE = re.compile(r"^pds[1-9][0-9]*$")
MAX_SOURCE_RPM_BYTES = 16 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 1024 * 1024
MAX_SOURCE_TREE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_TREE_FILES = 512


class PrepareError(RuntimeError):
    """Pinned source or transform failed closed."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PrepareError("source lock JSON contains a duplicate key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> Any:
    raise PrepareError(f"source lock JSON contains non-finite number {value}")


def strict_json(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PrepareError(f"{label} is invalid UTF-8 JSON") from exc


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PrepareError(f"{label} fields are incomplete or unsupported")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PrepareError("required input is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > maximum
        ):
            raise PrepareError("required input has unsafe metadata")
        data = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise PrepareError("required input changed while being read")
            data.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise PrepareError("required input grew while being read")
        return bytes(data)
    finally:
        os.close(descriptor)


def safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PrepareError(f"{label} must be a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PrepareError(f"{label} must be a safe relative path")
    return path


def extracted_file(directory: Path, relative: PurePosixPath) -> Path:
    if directory.is_symlink() or not directory.is_dir():
        raise PrepareError("extracted source directory is missing or unsafe")
    current = directory
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise PrepareError("extracted source parent is missing or unsafe")
    return current / relative.parts[-1]


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise PrepareError(f"{label} must be lowercase SHA-256")
    return value


def require_identity(data: bytes, record: dict[str, Any], label: str) -> None:
    expected_size = record.get("size")
    if type(expected_size) is not int or expected_size <= 0:
        raise PrepareError(f"{label} size is invalid")
    if len(data) != expected_size:
        raise PrepareError(f"{label} size mismatch")
    expected_sha256 = require_sha256(record.get("sha256"), f"{label} sha256")
    if sha256_bytes(data) != expected_sha256:
        raise PrepareError(f"{label} SHA-256 mismatch")


def source_tree_identity(directory: Path) -> dict[str, int | str]:
    try:
        before = os.lstat(directory)
    except OSError as exc:
        raise PrepareError("extracted source directory is missing or unsafe") from exc
    if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise PrepareError("extracted source directory is missing or unsafe")
    entries: list[dict[str, int | str]] = []
    total_size = 0
    try:
        children = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise PrepareError("extracted source tree cannot be listed") from exc
    if not 1 <= len(children) <= MAX_SOURCE_TREE_FILES:
        raise PrepareError("extracted source tree file count is unsafe")
    for path in children:
        if (
            not path.name
            or path.name in {".", ".."}
            or "/" in path.name
            or "\\" in path.name
            or any(ord(character) < 0x20 for character in path.name)
        ):
            raise PrepareError("extracted source tree contains an unsafe name")
        data = read_regular(path, MAX_SOURCE_FILE_BYTES)
        total_size += len(data)
        if total_size > MAX_SOURCE_TREE_BYTES:
            raise PrepareError("extracted source tree is too large")
        entries.append(
            {
                "path": path.name,
                "size": len(data),
                "sha256": sha256_bytes(data),
            }
        )
    try:
        after = os.lstat(directory)
    except OSError as exc:
        raise PrepareError("extracted source tree changed while being read") from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_mtime_ns,
        before.st_mode,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mtime_ns,
        after.st_mode,
    ):
        raise PrepareError("extracted source tree changed while being read")
    manifest = json.dumps(
        entries,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "file_count": len(entries),
        "total_size": total_size,
        "manifest_sha256": sha256_bytes(manifest),
    }


def validate_source_tree_record(record: Any) -> dict[str, Any]:
    expected = exact_object(
        record,
        {"file_count", "total_size", "manifest_sha256"},
        "source tree record",
    )
    if (
        type(expected["file_count"]) is not int
        or not 1 <= expected["file_count"] <= MAX_SOURCE_TREE_FILES
        or type(expected["total_size"]) is not int
        or not 1 <= expected["total_size"] <= MAX_SOURCE_TREE_BYTES
    ):
        raise PrepareError("source tree count or size is invalid")
    require_sha256(expected["manifest_sha256"], "source tree manifest sha256")
    return expected


def require_source_tree(directory: Path, record: Any) -> dict[str, int | str]:
    expected = validate_source_tree_record(record)
    measured = source_tree_identity(directory)
    if measured != expected:
        raise PrepareError("extracted source tree identity mismatch")
    return measured


def require_version_contract(
    package: dict[str, Any],
    archive: dict[str, Any],
    transform: dict[str, Any],
) -> None:
    """Tie the recorded source/hardened NEVRs to the archive and spec edit."""
    source_version = package.get("source_version")
    hardened_version = package.get("hardened_version")
    if not isinstance(source_version, str) or not isinstance(hardened_version, str):
        raise PrepareError("source and hardened versions must be recorded")
    source_match = VERSION_RELEASE_RE.fullmatch(source_version)
    hardened_match = VERSION_RELEASE_RE.fullmatch(hardened_version)
    if source_match is None or hardened_match is None:
        raise PrepareError("source or hardened version has unsupported syntax")
    release_prefix = f"{source_match['release']}."
    hardened_release = hardened_match["release"]
    suffix = (
        hardened_release[len(release_prefix) :]
        if hardened_release.startswith(release_prefix)
        else ""
    )
    if (
        hardened_match["version"] != source_match["version"]
        or hardened_match["dist"] != source_match["dist"]
        or HARDENED_SUFFIX_RE.fullmatch(suffix) is None
    ):
        raise PrepareError("hardened version is not a pinned downstream release")

    expected_filename = f"{package['name']}-{source_version}.src.rpm"
    if archive.get("filename") != expected_filename:
        raise PrepareError("source RPM filename does not match recorded version")
    fan_source = package.get("fan_controller_source")
    fan_override = transform.get("fan_controller_override")
    if not isinstance(fan_source, dict) or not isinstance(fan_override, dict):
        raise PrepareError("fan controller source transform is missing")
    expected_replacement = [
        {
            "old": f"Release:        {source_match['release']}%{{?dist}}",
            "new": f"Release:        {hardened_match['release']}%{{?dist}}",
        },
        {
            "old": f"Source70:       {fan_source.get('path')}",
            "new": f"Source70:       {fan_override.get('path')}",
        },
    ]
    if transform.get("replace_exact_lines") != expected_replacement:
        raise PrepareError("spec source transform does not match recorded inputs")


def verify_source_signature(
    rpmkeys: Path,
    source_rpm: Path,
    fingerprint: Any,
) -> None:
    if not isinstance(fingerprint, str) or not FINGERPRINT_RE.fullmatch(fingerprint):
        raise PrepareError("source RPM signing fingerprint is invalid")
    if rpmkeys.is_symlink() or not rpmkeys.is_file() or not os.access(rpmkeys, os.X_OK):
        raise PrepareError("rpmkeys verifier is missing or unsafe")
    try:
        result = subprocess.run(
            [str(rpmkeys), "--checksig", "--verbose", str(source_rpm)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrepareError("rpmkeys verifier could not run") from exc
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if (
        result.returncode != 0
        or combined.count(f"key fingerprint: {fingerprint}: ok") != 2
        or "header sha256 digest: ok" not in combined
        or "payload sha256 digest: ok" not in combined
    ):
        raise PrepareError("source RPM signature or payload verification failed")


def transform_spec(source: bytes, transform: dict[str, Any]) -> bytes:
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PrepareError("source spec is not UTF-8") from exc
    if "\r" in text or not text.endswith("\n"):
        raise PrepareError("source spec has unsupported line endings")
    lines = text.splitlines()

    replacements = transform.get("replace_exact_lines")
    removals = transform.get("remove_exact_lines")
    insertion = transform.get("insert_after_exact_line")
    forbidden = transform.get("forbidden_tokens")
    if (
        not isinstance(replacements, list)
        or not replacements
        or not isinstance(removals, list)
        or not removals
        or not isinstance(insertion, dict)
        or set(insertion) != {"line", "text"}
        or not isinstance(forbidden, list)
        or not forbidden
    ):
        raise PrepareError("transform contract is incomplete")

    for replacement in replacements:
        if not isinstance(replacement, dict) or set(replacement) != {"old", "new"}:
            raise PrepareError("replacement contract is invalid")
        old = replacement["old"]
        new = replacement["new"]
        if not isinstance(old, str) or not isinstance(new, str) or lines.count(old) != 1:
            raise PrepareError("replacement source line is absent or ambiguous")
        lines[lines.index(old)] = new

    for removal in removals:
        if not isinstance(removal, str) or lines.count(removal) != 1:
            raise PrepareError("removal source line is absent or ambiguous")
        lines.remove(removal)

    marker = insertion["line"]
    inserted_text = insertion["text"]
    if (
        not isinstance(marker, str)
        or not isinstance(inserted_text, str)
        or lines.count(marker) != 1
        or "\r" in inserted_text
    ):
        raise PrepareError("changelog insertion contract is invalid")
    index = lines.index(marker) + 1
    inserted_lines = inserted_text.rstrip("\n").split("\n") + [""]
    lines[index:index] = inserted_lines
    result = ("\n".join(lines) + "\n").encode("utf-8")

    for token in forbidden:
        if not isinstance(token, str) or not token or token in result.decode("utf-8"):
            raise PrepareError("forbidden global-sudo token remains after transform")
    expected_size = transform.get("hardened_spec_size")
    if type(expected_size) is not int or len(result) != expected_size:
        raise PrepareError("hardened spec size mismatch")
    expected_sha256 = require_sha256(
        transform.get("hardened_spec_sha256"), "hardened spec sha256"
    )
    if sha256_bytes(result) != expected_sha256:
        raise PrepareError("hardened spec SHA-256 mismatch")
    return result


def write_new_private(path: Path, content: bytes, label: str) -> None:
    if path.is_symlink() or path.exists():
        raise PrepareError(f"{label} already exists or is unsafe")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise PrepareError(f"{label} directory is missing or unsafe")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PrepareError(f"{label} could not be written")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare(
    lock_path: Path,
    source_rpm: Path,
    extracted_dir: Path,
    rpmkeys: Path,
    output_spec: Path | None,
    output_fan_controller: Path | None = None,
) -> dict[str, object]:
    lock = exact_object(
        strict_json(
            read_regular(lock_path, MAX_SOURCE_FILE_BYTES),
            "source lock",
        ),
        {"schema", "package", "transform"},
        "source lock",
    )
    if type(lock["schema"]) is not int or lock["schema"] != 1:
        raise PrepareError("source lock schema is unsupported")
    package = exact_object(
        lock["package"],
        {
            "name",
            "source_version",
            "hardened_version",
            "source_rpm",
            "source_tree",
            "spec",
            "fan_controller_source",
            "forbidden_source",
        },
        "source lock package",
    )
    transform = exact_object(
        lock["transform"],
        {
            "replace_exact_lines",
            "remove_exact_lines",
            "insert_after_exact_line",
            "forbidden_tokens",
            "fan_controller_override",
            "hardened_spec_size",
            "hardened_spec_sha256",
        },
        "source lock transform",
    )
    if package.get("name") != "pocketds-userspace":
        raise PrepareError("source lock package identity is invalid")

    archive = exact_object(
        package["source_rpm"],
        {"filename", "url", "size", "sha256", "signing_key_fingerprint"},
        "source RPM record",
    )
    spec = exact_object(
        package["spec"],
        {"path", "size", "sha256"},
        "source spec record",
    )
    fan_source = exact_object(
        package["fan_controller_source"],
        {"path", "size", "sha256"},
        "source fan controller record",
    )
    forbidden_source = exact_object(
        package["forbidden_source"],
        {"path", "size", "sha256"},
        "forbidden source record",
    )
    source_tree_record = validate_source_tree_record(package["source_tree"])
    fan_override = exact_object(
        transform["fan_controller_override"],
        {"path", "size", "sha256", "payload_path", "payload_mode"},
        "fan controller override record",
    )
    if (
        fan_source.get("path") != "pocketds-fancontrol"
        or fan_override.get("path") != "pocketds-fancontrol.pds1"
        or fan_override.get("payload_path") != "/usr/bin/pocketds-fancontrol"
        or fan_override.get("payload_mode") != "0755"
    ):
        raise PrepareError("fan controller source or payload contract is invalid")
    require_version_contract(package, archive, transform)
    archive_url = archive["url"]
    parsed = urlparse(archive_url if isinstance(archive_url, str) else "")
    if (
        parsed.scheme != "https"
        or parsed.hostname != "download.copr.fedorainfracloud.org"
        or PurePosixPath(parsed.path).name != archive.get("filename")
    ):
        raise PrepareError("source RPM URL or filename is not pinned to Fedora COPR")
    source_rpm_bytes = read_regular(source_rpm, MAX_SOURCE_RPM_BYTES)
    require_identity(source_rpm_bytes, archive, "source RPM")
    verify_source_signature(
        rpmkeys,
        source_rpm,
        archive.get("signing_key_fingerprint"),
    )
    # rpmkeys receives a pathname rather than our already-open descriptor. Re-read and
    # re-pin the bytes so a pathname replacement during verification fails closed.
    require_identity(
        read_regular(source_rpm, MAX_SOURCE_RPM_BYTES),
        archive,
        "source RPM after signature verification",
    )
    source_tree = require_source_tree(extracted_dir, source_tree_record)

    spec_path = extracted_file(extracted_dir, safe_relative(spec.get("path"), "spec path"))
    source_spec = read_regular(spec_path, MAX_SOURCE_FILE_BYTES)
    require_identity(source_spec, spec, "source spec")
    fan_source_path = extracted_file(
        extracted_dir,
        safe_relative(fan_source.get("path"), "source fan controller path"),
    )
    fan_source_bytes = read_regular(fan_source_path, MAX_SOURCE_FILE_BYTES)
    require_identity(fan_source_bytes, fan_source, "source fan controller")
    forbidden_path = extracted_file(
        extracted_dir,
        safe_relative(forbidden_source.get("path"), "forbidden source path"),
    )
    forbidden_bytes = read_regular(forbidden_path, MAX_SOURCE_FILE_BYTES)
    require_identity(forbidden_bytes, forbidden_source, "forbidden source")
    if forbidden_bytes.strip() != b"%wheel ALL=(ALL) NOPASSWD: ALL":
        raise PrepareError("forbidden source content is unexpected")

    override_path = extracted_file(
        lock_path.parent,
        safe_relative(fan_override.get("path"), "fan controller override path"),
    )
    override_bytes = read_regular(override_path, MAX_SOURCE_FILE_BYTES)
    require_identity(override_bytes, fan_override, "fan controller override")

    hardened = transform_spec(source_spec, transform)
    if (output_spec is None) != (output_fan_controller is None):
        raise PrepareError("spec and fan controller outputs must be requested together")
    if output_spec is not None and output_fan_controller is not None:
        if output_spec == output_fan_controller:
            raise PrepareError("spec and fan controller outputs must be distinct")
        if output_spec.exists() or output_spec.is_symlink():
            raise PrepareError("output spec already exists or is unsafe")
        if output_fan_controller.exists() or output_fan_controller.is_symlink():
            raise PrepareError("output fan controller already exists or is unsafe")
        write_new_private(
            output_fan_controller,
            override_bytes,
            "output fan controller",
        )
        # Emit the spec last. If an I/O failure interrupts the pair, a lone
        # source override is inert; a lone spec could silently drive a build.
        write_new_private(output_spec, hardened, "output spec")

    return {
        "schema": 1,
        "read_only": output_spec is None,
        "network": False,
        "build": False,
        "source_rpm": "pass",
        "source_rpm_signature": "pass",
        "source_tree": "pass",
        "source_tree_manifest_sha256": source_tree["manifest_sha256"],
        "source_spec": "pass",
        "source_fan_controller": "pass",
        "fan_controller_override": "pass",
        "fan_controller_override_sha256": sha256_bytes(override_bytes),
        "global_nopasswd_removed": True,
        "hardened_spec_sha256": sha256_bytes(hardened),
        "output_written": output_spec is not None,
        # Source verification and deterministic input preparation alone never
        # authorize release.  Rebuild, signature, transaction and mounted-root
        # gates are separate and mandatory.
        "release_ready": False,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-rpm", type=Path, required=True)
    parser.add_argument("--extracted-dir", type=Path, required=True)
    parser.add_argument("--rpmkeys", type=Path, default=Path("/usr/bin/rpmkeys"))
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output-spec", type=Path)
    parser.add_argument("--output-fan-controller", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = prepare(
            args.lock,
            args.source_rpm,
            args.extracted_dir,
            args.rpmkeys,
            args.output_spec,
            args.output_fan_controller,
        )
    except (OSError, UnicodeError, TypeError, ValueError, PrepareError) as exc:
        print(json.dumps({"release_ready": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
