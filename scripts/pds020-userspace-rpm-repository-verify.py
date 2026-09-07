#!/usr/bin/env python3
"""Verify two reproducible unsigned repository-metadata results for pds2."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Sequence
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPOSITORY_LOCK = (
    ROOT / "packaging/pocketds-userspace/repository-metadata-lock.pds2.json"
)
DEFAULT_ARCHIVE_LOCK = (
    ROOT / "packaging/pocketds-userspace/rootfs-package-archives-lock.pds2.json"
)
DEFAULT_BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.pds2.json"
DEFAULT_SOURCE_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.pds2.json"
ARCHIVE_VERIFY_PATH = ROOT / "scripts/pds020-userspace-rpm-archive-verify.py"
MAX_LOCK_BYTES = 256 * 1024
MAX_REPOMD_BYTES = 64 * 1024
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_GENERATOR_BYTES = 1024 * 1024
MAX_TOOL_OUTPUT = 128 * 1024
SHA256_HEX = set("0123456789abcdef")
REPO_NS = "http://linux.duke.edu/metadata/repo"
COMMON_NS = "http://linux.duke.edu/metadata/common"
FILELISTS_NS = "http://linux.duke.edu/metadata/filelists"
OTHER_NS = "http://linux.duke.edu/metadata/other"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


archive_verifier = load_module("pds020_repository_archive_verifier", ARCHIVE_VERIFY_PATH)
repro = archive_verifier.repro


class RepositoryError(RuntimeError):
    """Repository metadata, package binding or reproduction failed closed."""


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RepositoryError(f"{label} fields are incomplete or unsupported")
    return value


def is_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= SHA256_HEX
    )


def artifact_record(value: Any, label: str) -> dict[str, Any]:
    record = exact_object(value, {"filename", "size", "sha256"}, label)
    if (
        not isinstance(record["filename"], str)
        or Path(record["filename"]).name != record["filename"]
        or type(record["size"]) is not int
        or not 0 < record["size"] <= MAX_METADATA_BYTES
        or not is_sha(record["sha256"])
    ):
        raise RepositoryError(f"{label} is invalid")
    return record


def load_lock(path: Path) -> dict[str, Any]:
    lock = exact_object(
        repro.strict_json(repro.read_regular(path, MAX_LOCK_BYTES), "repository lock"),
        {"schema", "archive", "generator", "repository", "policy"},
        "repository lock",
    )
    if type(lock["schema"]) is not int or lock["schema"] != 1:
        raise RepositoryError("repository lock schema is unsupported")
    archive = exact_object(
        lock["archive"],
        {"lock_sha256", "records_sha256", "package_count"},
        "archive contract",
    )
    if (
        not is_sha(archive["lock_sha256"])
        or not is_sha(archive["records_sha256"])
        or type(archive["package_count"]) is not int
        or archive["package_count"] != 322
    ):
        raise RepositoryError("archive contract is invalid")
    generator = exact_object(
        lock["generator"],
        {
            "distro",
            "architecture",
            "nvr",
            "executable_size",
            "executable_sha256",
            "rpm_verify_clean",
            "independent_result_count",
            "parameters",
            "input_file_mtime",
        },
        "generator contract",
    )
    expected_parameters = [
        "--quiet",
        "--checksum=sha256",
        "--repomd-checksum=sha256",
        "--no-database",
        "--general-compress-type=xz",
        "--revision=1787875200",
        "--set-timestamp-to-revision",
        "--workers=1",
    ]
    if (
        generator["distro"] != "fedora-44"
        or generator["architecture"] != "aarch64"
        or generator["nvr"] != "createrepo_c-1.2.1-5.fc44.aarch64"
        or type(generator["executable_size"]) is not int
        or not 0 < generator["executable_size"] <= MAX_GENERATOR_BYTES
        or not is_sha(generator["executable_sha256"])
        or generator["rpm_verify_clean"] is not True
        or type(generator["independent_result_count"]) is not int
        or generator["independent_result_count"] != 2
        or generator["parameters"] != expected_parameters
        or type(generator["input_file_mtime"]) is not int
        or generator["input_file_mtime"] != 1787875200
    ):
        raise RepositoryError("generator contract is unsupported")
    repository = exact_object(
        lock["repository"],
        {"revision", "repomd", "metadata", "primary_location_checksum_sha256"},
        "repository contract",
    )
    if (
        type(repository["revision"]) is not int
        or repository["revision"] != generator["input_file_mtime"]
        or not is_sha(repository["primary_location_checksum_sha256"])
    ):
        raise RepositoryError("repository contract is invalid")
    repomd = artifact_record(repository["repomd"], "repomd record")
    if repomd["filename"] != "repomd.xml" or repomd["size"] > MAX_REPOMD_BYTES:
        raise RepositoryError("repomd record is invalid")
    metadata = repository["metadata"]
    if not isinstance(metadata, list) or len(metadata) != 3:
        raise RepositoryError("metadata records are incomplete")
    observed_types: list[str] = []
    observed_names: set[str] = set()
    for value in metadata:
        record = exact_object(
            value,
            {"type", "filename", "size", "sha256", "open_size", "open_sha256"},
            "metadata record",
        )
        artifact_record(
            {key: record[key] for key in ("filename", "size", "sha256")},
            "metadata artifact",
        )
        if (
            record["type"] not in {"filelists", "other", "primary"}
            or type(record["open_size"]) is not int
            or not 0 < record["open_size"] <= MAX_METADATA_BYTES
            or not is_sha(record["open_sha256"])
            or not record["filename"].endswith(f"-{record['type']}.xml.xz")
            or not record["filename"].startswith(record["sha256"])
            or record["filename"] in observed_names
        ):
            raise RepositoryError("metadata record is invalid")
        observed_types.append(record["type"])
        observed_names.add(record["filename"])
    if observed_types != ["filelists", "other", "primary"]:
        raise RepositoryError("metadata records are not canonical")
    policy = exact_object(
        lock["policy"],
        {
            "detached_repomd_signature_required",
            "detached_repomd_signature_verified",
            "project_rpm_signature_verified",
            "release_ready",
        },
        "repository policy",
    )
    if policy != {
        "detached_repomd_signature_required": True,
        "detached_repomd_signature_verified": False,
        "project_rpm_signature_verified": False,
        "release_ready": False,
    }:
        raise RepositoryError("repository signing boundary is invalid")
    return lock


def safe_directory(path: Path, label: str) -> tuple[int, int]:
    if not path.is_absolute() or ".." in path.parts:
        raise RepositoryError(f"{label} path must be absolute and normalized")
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RepositoryError(f"{label} is missing or unsafe") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RepositoryError(f"{label} must be owned mode-0700")
    return metadata.st_dev, metadata.st_ino


def exact_directory(path: Path, names: set[str]) -> tuple[int, int]:
    identity = safe_directory(path, "repodata directory")
    try:
        entries = list(os.scandir(path))
    except OSError as exc:
        raise RepositoryError("repodata directory could not be read") from exc
    if {entry.name for entry in entries} != names or len(entries) != len(names):
        raise RepositoryError("repodata directory has missing or extra entries")
    for entry in entries:
        item = entry.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(item.st_mode)
            or stat.S_ISLNK(item.st_mode)
            or item.st_nlink != 1
            or item.st_uid not in {0, os.getuid()}
            or stat.S_IMODE(item.st_mode) & 0o022
        ):
            raise RepositoryError("repodata entry metadata is unsafe")
    return identity


def read_locked(path: Path, record: dict[str, Any], maximum: int) -> bytes:
    try:
        data = repro.read_regular(path, maximum)
    except repro.ReproError as exc:
        raise RepositoryError("repository artifact is missing or unsafe") from exc
    if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise RepositoryError("repository artifact content differs")
    return data


def decompress_locked(data: bytes, record: dict[str, Any]) -> bytes:
    try:
        decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        content = decompressor.decompress(data, max_length=record["open_size"] + 1)
    except lzma.LZMAError as exc:
        raise RepositoryError("metadata XZ stream is invalid") from exc
    if (
        not decompressor.eof
        or decompressor.unused_data
        or len(content) != record["open_size"]
        or hashlib.sha256(content).hexdigest() != record["open_sha256"]
    ):
        raise RepositoryError("metadata open content differs")
    return content


def parse_xml(data: bytes, label: str) -> ET.Element:
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise RepositoryError(f"{label} contains a forbidden XML declaration")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise RepositoryError(f"{label} is invalid XML") from exc


def require_text(parent: ET.Element, tag: str, namespace: str) -> str:
    children = parent.findall(f"{{{namespace}}}{tag}")
    if len(children) != 1 or children[0].text is None:
        raise RepositoryError("repository XML field is incomplete")
    return children[0].text


def validate_repomd(lock: dict[str, Any], data: bytes) -> None:
    root = parse_xml(data, "repomd")
    if root.tag != f"{{{REPO_NS}}}repomd" or root.attrib:
        raise RepositoryError("repomd root is invalid")
    root_tags = [item.tag for item in root]
    if (
        root_tags.count(f"{{{REPO_NS}}}revision") != 1
        or root_tags.count(f"{{{REPO_NS}}}data") != 3
        or len(root_tags) != 4
    ):
        raise RepositoryError("repomd child set differs")
    revision = require_text(root, "revision", REPO_NS)
    if revision != str(lock["repository"]["revision"]):
        raise RepositoryError("repomd revision differs")
    expected = {record["type"]: record for record in lock["repository"]["metadata"]}
    records = root.findall(f"{{{REPO_NS}}}data")
    if len(records) != 3 or {item.attrib.get("type") for item in records} != set(expected):
        raise RepositoryError("repomd data set differs")
    for item in records:
        if set(item.attrib) != {"type"}:
            raise RepositoryError("repomd data attributes differ")
        expected_tags = {
            f"{{{REPO_NS}}}checksum",
            f"{{{REPO_NS}}}open-checksum",
            f"{{{REPO_NS}}}location",
            f"{{{REPO_NS}}}timestamp",
            f"{{{REPO_NS}}}size",
            f"{{{REPO_NS}}}open-size",
        }
        if len(item) != len(expected_tags) or {child.tag for child in item} != expected_tags:
            raise RepositoryError("repomd data child set differs")
        record = expected[item.attrib["type"]]
        checksum = item.findall(f"{{{REPO_NS}}}checksum")
        open_checksum = item.findall(f"{{{REPO_NS}}}open-checksum")
        location = item.findall(f"{{{REPO_NS}}}location")
        if (
            len(checksum) != 1
            or checksum[0].attrib != {"type": "sha256"}
            or checksum[0].text != record["sha256"]
            or len(open_checksum) != 1
            or open_checksum[0].attrib != {"type": "sha256"}
            or open_checksum[0].text != record["open_sha256"]
            or len(location) != 1
            or location[0].attrib != {"href": f"repodata/{record['filename']}"}
            or require_text(item, "timestamp", REPO_NS) != str(lock["repository"]["revision"])
            or require_text(item, "size", REPO_NS) != str(record["size"])
            or require_text(item, "open-size", REPO_NS) != str(record["open_size"])
        ):
            raise RepositoryError("repomd data record differs")


def validate_primary(
    lock: dict[str, Any], data: bytes, packages: dict[str, dict[str, Any]]
) -> dict[str, tuple[str, str]]:
    root = parse_xml(data, "primary metadata")
    if (
        root.tag != f"{{{COMMON_NS}}}metadata"
        or root.attrib != {"packages": str(lock["archive"]["package_count"])}
    ):
        raise RepositoryError("primary metadata root differs")
    children = list(root)
    if len(children) != lock["archive"]["package_count"]:
        raise RepositoryError("primary package count differs")
    observed: list[tuple[str, str]] = []
    pkgids: dict[str, tuple[str, str]] = {}
    for item in children:
        if item.tag != f"{{{COMMON_NS}}}package" or item.attrib != {"type": "rpm"}:
            raise RepositoryError("primary package record is invalid")
        location_nodes = item.findall(f"{{{COMMON_NS}}}location")
        version_nodes = item.findall(f"{{{COMMON_NS}}}version")
        checksum_nodes = item.findall(f"{{{COMMON_NS}}}checksum")
        size_nodes = item.findall(f"{{{COMMON_NS}}}size")
        time_nodes = item.findall(f"{{{COMMON_NS}}}time")
        if not all(len(nodes) == 1 for nodes in (location_nodes, version_nodes, checksum_nodes, size_nodes, time_nodes)):
            raise RepositoryError("primary package fields are incomplete")
        location = location_nodes[0].attrib.get("href")
        if not isinstance(location, str) or Path(location).name != location or location not in packages:
            raise RepositoryError("primary package location is unsafe or unknown")
        expected = packages[location]
        checksum = checksum_nodes[0]
        version = version_nodes[0]
        size = size_nodes[0]
        if (
            require_text(item, "name", COMMON_NS) != expected["name"]
            or require_text(item, "arch", COMMON_NS) != expected["arch"]
            or version.attrib
            != {
                "epoch": str(expected["epoch"]),
                "ver": expected["version"],
                "rel": expected["release"],
            }
            or checksum.attrib != {"type": "sha256", "pkgid": "YES"}
            or checksum.text != expected["sha256"]
            or size.attrib.get("package") != str(expected["size"])
            or time_nodes[0].attrib.get("file") != str(lock["repository"]["revision"])
        ):
            raise RepositoryError("primary package identity differs")
        if location in {value[0] for value in observed} or expected["sha256"] in pkgids:
            raise RepositoryError("primary metadata contains duplicate packages")
        observed.append((location, expected["sha256"]))
        pkgids[expected["sha256"]] = (expected["name"], expected["arch"])
    if [item[0] for item in observed] != sorted(packages):
        raise RepositoryError("primary package locations are not canonical")
    canonical = "".join(f"{location}\t{checksum}\n" for location, checksum in observed).encode()
    if hashlib.sha256(canonical).hexdigest() != lock["repository"]["primary_location_checksum_sha256"]:
        raise RepositoryError("primary location/checksum manifest differs")
    return pkgids


def validate_auxiliary(
    data: bytes,
    namespace: str,
    label: str,
    pkgids: dict[str, tuple[str, str]],
) -> None:
    root = parse_xml(data, label)
    expected_tag = "filelists" if namespace == FILELISTS_NS else "otherdata"
    if (
        root.tag != f"{{{namespace}}}{expected_tag}"
        or root.attrib != {"packages": str(len(pkgids))}
    ):
        raise RepositoryError(f"{label} root differs")
    observed: list[str] = []
    for item in list(root):
        if item.tag != f"{{{namespace}}}package" or set(item.attrib) != {"pkgid", "name", "arch"}:
            raise RepositoryError(f"{label} package record is invalid")
        pkgid = item.attrib["pkgid"]
        if (
            pkgid not in pkgids
            or pkgid in observed
            or (item.attrib["name"], item.attrib["arch"]) != pkgids[pkgid]
        ):
            raise RepositoryError(f"{label} package set differs")
        observed.append(pkgid)
    if set(observed) != set(pkgids) or len(observed) != len(pkgids):
        raise RepositoryError(f"{label} package set differs")


def verify_result(
    lock: dict[str, Any],
    archive_lock: dict[str, Any],
    directory: Path,
    extra_names: set[str] | None = None,
) -> dict[str, Any]:
    names = {lock["repository"]["repomd"]["filename"]} | {
        record["filename"] for record in lock["repository"]["metadata"]
    }
    names |= extra_names or set()
    identity = exact_directory(directory, names)
    repomd = lock["repository"]["repomd"]
    repomd_bytes = read_locked(directory / repomd["filename"], repomd, MAX_REPOMD_BYTES)
    validate_repomd(lock, repomd_bytes)
    opened: dict[str, bytes] = {}
    for record in lock["repository"]["metadata"]:
        compressed = read_locked(directory / record["filename"], record, MAX_METADATA_BYTES)
        opened[record["type"]] = decompress_locked(compressed, record)
    packages = {record["filename"]: record for record in archive_lock["packages"]}
    pkgids = validate_primary(lock, opened["primary"], packages)
    validate_auxiliary(opened["filelists"], FILELISTS_NS, "filelists metadata", pkgids)
    validate_auxiliary(opened["other"], OTHER_NS, "other metadata", pkgids)
    if exact_directory(directory, names) != identity:
        raise RepositoryError("repodata directory changed during verification")
    read_locked(directory / repomd["filename"], repomd, MAX_REPOMD_BYTES)
    for record in lock["repository"]["metadata"]:
        read_locked(directory / record["filename"], record, MAX_METADATA_BYTES)
    return {
        "repomd_sha256": repomd["sha256"],
        "metadata_file_count": 3,
        "package_count": len(pkgids),
        "primary_location_checksum_sha256": lock["repository"]["primary_location_checksum_sha256"],
    }


def safe_executable(path: Path) -> bytes:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RepositoryError("repository generator is missing or unsafe") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise RepositoryError("repository generator is missing or unsafe")
    return repro.read_regular(path, MAX_GENERATOR_BYTES)


def run_bounded(command: list[str], label: str) -> bytes:
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
        raise RepositoryError(f"{label} could not run") from exc
    if result.returncode != 0 or result.stderr or len(result.stdout) > MAX_TOOL_OUTPUT:
        raise RepositoryError(f"{label} failed or exceeded its output bound")
    return result.stdout


def verify_generator(lock: dict[str, Any], createrepo_c: Path, rpm: Path) -> None:
    data = safe_executable(createrepo_c)
    generator = lock["generator"]
    if len(data) != generator["executable_size"] or hashlib.sha256(data).hexdigest() != generator["executable_sha256"]:
        raise RepositoryError("repository generator content differs")
    nvr = run_bounded(
        [str(rpm), "-q", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n", "createrepo_c"],
        "repository generator identity",
    )
    if nvr.decode("utf-8", errors="strict").strip() != generator["nvr"]:
        raise RepositoryError("repository generator identity differs")
    if run_bounded([str(rpm), "-V", "createrepo_c"], "repository generator verification"):
        raise RepositoryError("repository generator RPM verification differs")


def evaluate(
    repository_lock_path: Path,
    archive_lock_path: Path,
    archive_dir: Path,
    result_a: Path,
    result_b: Path,
    source_lock_path: Path,
    build_lock_path: Path,
    createrepo_c: Path,
    rpm: Path,
    rpmkeys: Path,
    rpm2archive: Path,
) -> dict[str, Any]:
    lock = load_lock(repository_lock_path)
    archive_bytes = repro.read_regular(archive_lock_path, MAX_LOCK_BYTES)
    if hashlib.sha256(archive_bytes).hexdigest() != lock["archive"]["lock_sha256"]:
        raise RepositoryError("rootfs archive lock differs")
    archive_lock = archive_verifier.load_lock(archive_lock_path)
    if (
        archive_lock["records_sha256"] != lock["archive"]["records_sha256"]
        or archive_lock["package_count"] != lock["archive"]["package_count"]
    ):
        raise RepositoryError("rootfs archive contract differs")
    archive_report = archive_verifier.verify(
        archive_lock,
        archive_dir,
        source_lock_path,
        build_lock_path,
        rpm,
        rpmkeys,
        rpm2archive,
    )
    if archive_report.get("verified") is not True:
        raise RepositoryError("rootfs archive verification failed")
    verify_generator(lock, createrepo_c, rpm)
    identity_a = safe_directory(result_a, "result A")
    identity_b = safe_directory(result_b, "result B")
    if identity_a == identity_b:
        raise RepositoryError("two distinct metadata result directories are required")
    first = verify_result(lock, archive_lock, result_a)
    second = verify_result(lock, archive_lock, result_b)
    if first != second:
        raise RepositoryError("repository metadata results differ")
    return {
        "schema": 1,
        "verified": True,
        "offline": True,
        "read_only": True,
        "build": False,
        "install": False,
        "independent_result_count": 2,
        "repository": first,
        "archive_package_count": archive_lock["package_count"],
        "fedora_signatures_verified": True,
        "project_payload_audited": True,
        "generator_content_verified": True,
        "generator_rpm_verified": True,
        "metadata_reproducible": True,
        "detached_repomd_signature_verified": False,
        "project_rpm_signature_verified": False,
        "release_ready": False,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--result-a", type=Path, required=True)
    parser.add_argument("--result-b", type=Path, required=True)
    parser.add_argument("--repository-lock", type=Path, default=DEFAULT_REPOSITORY_LOCK)
    parser.add_argument("--archive-lock", type=Path, default=DEFAULT_ARCHIVE_LOCK)
    parser.add_argument("--source-lock", type=Path, default=DEFAULT_SOURCE_LOCK)
    parser.add_argument("--build-lock", type=Path, default=DEFAULT_BUILD_LOCK)
    parser.add_argument("--createrepo-c", type=Path, default=Path("/usr/bin/createrepo_c"))
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument("--rpmkeys", type=Path, default=Path("/usr/bin/rpmkeys"))
    parser.add_argument("--rpm2archive", type=Path, default=Path("/usr/bin/rpm2archive"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = evaluate(
            args.repository_lock,
            args.archive_lock,
            args.archive_dir,
            args.result_a,
            args.result_b,
            args.source_lock,
            args.build_lock,
            args.createrepo_c,
            args.rpm,
            args.rpmkeys,
            args.rpm2archive,
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        RepositoryError,
        repro.ReproError,
        archive_verifier.ArchiveError,
        archive_verifier.auditor.AuditError,
    ) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
