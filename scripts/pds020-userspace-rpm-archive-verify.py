#!/usr/bin/env python3
"""Verify the exact offline pds2 dependency RPM archive without installing it."""

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
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE_LOCK = (
    ROOT / "packaging/pocketds-userspace/dependency-archives-lock.pds2.json"
)
DEFAULT_BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.pds2.json"
DEFAULT_SOURCE_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.pds2.json"
AUDITOR_PATH = ROOT / "scripts/pds020-userspace-rpm-audit.py"
REPRO_PATH = ROOT / "scripts/pds020-userspace-rpm-repro.py"
MAX_LOCK_BYTES = 256 * 1024
MAX_KEY_BYTES = 64 * 1024
MAX_RPM_BYTES = 16 * 1024 * 1024
MAX_TOOL_OUTPUT = 256 * 1024
ALLOWED_PACKAGE_BOUNDARIES = {(136, 135), (322, 321)}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{40}$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9+_.~%-]+\.rpm$")
VALUE_RE = re.compile(r"^[^\t\r\n]+$")
SIGNATURE_RE = re.compile(
    r"^    Header OpenPGP V4 RSA/SHA256 signature, key fingerprint: "
    r"(?P<fingerprint>[0-9A-Fa-f]{40}): OK$"
)
DIGEST_LINES = {
    "    Header SHA256 digest: OK",
    "    Payload SHA256 digest: OK",
}
QUERY_FORMAT = (
    "%{NAME}\\n%{EPOCHNUM}\\n%{VERSION}\\n%{RELEASE}\\n%{ARCH}\\n"
    "%{PAYLOADSHA256}\\n"
)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


auditor = load_module("pds020_archive_auditor", AUDITOR_PATH)
repro = load_module("pds020_archive_repro", REPRO_PATH)


class ArchiveError(RuntimeError):
    """The archive lock, directory, package identity or signature failed closed."""


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ArchiveError(f"{label} fields are incomplete or unsupported")
    return value


def require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ArchiveError(f"{label} is not lowercase SHA-256")
    return value


def load_lock(path: Path) -> dict[str, Any]:
    lock = exact_object(
        repro.strict_json(repro.read_regular(path, MAX_LOCK_BYTES), "archive lock"),
        {
            "schema",
            "fedora_release",
            "architecture",
            "fedora_key",
            "package_count",
            "fedora_package_count",
            "total_bytes",
            "records_sha256",
            "packages",
            "signature_verified",
            "release_ready",
        },
        "archive lock",
    )
    if (
        type(lock["schema"]) is not int
        or lock["schema"] != 1
        or type(lock["fedora_release"]) is not int
        or lock["fedora_release"] != 44
        or lock["architecture"] != "aarch64"
        or type(lock["package_count"]) is not int
        or type(lock["fedora_package_count"]) is not int
        or (lock["package_count"], lock["fedora_package_count"])
        not in ALLOWED_PACKAGE_BOUNDARIES
        or type(lock["total_bytes"]) is not int
        or not 0 < lock["total_bytes"] <= 256 * 1024 * 1024
        or lock["signature_verified"] is not False
        or lock["release_ready"] is not False
    ):
        raise ArchiveError("archive boundary is unsupported")
    require_sha(lock["records_sha256"], "archive records")
    key = exact_object(
        lock["fedora_key"],
        {"filename", "size", "sha256", "fingerprint"},
        "Fedora key",
    )
    if (
        key["filename"] != "RPM-GPG-KEY-fedora-44-primary"
        or type(key["size"]) is not int
        or not 0 < key["size"] <= MAX_KEY_BYTES
        or not isinstance(key["fingerprint"], str)
        or FINGERPRINT_RE.fullmatch(key["fingerprint"]) is None
    ):
        raise ArchiveError("Fedora key record is invalid")
    require_sha(key["sha256"], "Fedora key")
    packages = lock["packages"]
    if not isinstance(packages, list) or len(packages) != lock["package_count"]:
        raise ArchiveError("archive package records are incomplete")
    expected_fields = {
        "name",
        "epoch",
        "version",
        "release",
        "arch",
        "filename",
        "origin",
        "size",
        "sha256",
        "payload_sha256",
        "signature_verified",
        "signing_key_fingerprint",
    }
    names: set[str] = set()
    identities: set[tuple[Any, ...]] = set()
    fedora_count = 0
    project_count = 0
    total_bytes = 0
    previous_filename = ""
    for value in packages:
        record = exact_object(value, expected_fields, "package record")
        for field in ("name", "version", "release", "arch"):
            if not isinstance(record[field], str) or VALUE_RE.fullmatch(record[field]) is None:
                raise ArchiveError("package identity is invalid")
        if (
            type(record["epoch"]) is not int
            or not 0 <= record["epoch"] <= 2**31 - 1
            or not isinstance(record["filename"], str)
            or FILENAME_RE.fullmatch(record["filename"]) is None
            or Path(record["filename"]).name != record["filename"]
            or type(record["size"]) is not int
            or not 0 < record["size"] <= MAX_RPM_BYTES
        ):
            raise ArchiveError("package record is invalid")
        require_sha(record["sha256"], "package")
        require_sha(record["payload_sha256"], "package payload")
        if record["filename"] <= previous_filename:
            raise ArchiveError("package records are not uniquely filename-sorted")
        previous_filename = record["filename"]
        identity = tuple(record[field] for field in ("name", "epoch", "version", "release", "arch"))
        if record["filename"] in names or identity in identities:
            raise ArchiveError("archive package records contain duplicates")
        names.add(record["filename"])
        identities.add(identity)
        total_bytes += record["size"]
        if record["origin"] in {"fedora", "updates"}:
            fedora_count += 1
            if (
                record["signature_verified"] is not True
                or record["signing_key_fingerprint"] != key["fingerprint"]
            ):
                raise ArchiveError("Fedora package signature contract is invalid")
        elif record["origin"] == "project-pre-sign":
            project_count += 1
            if (
                record["name"] != "pocketds-userspace"
                or record["signature_verified"] is not False
                or record["signing_key_fingerprint"] is not None
            ):
                raise ArchiveError("project pre-sign package boundary is invalid")
        else:
            raise ArchiveError("package origin is unsupported")
    canonical = json.dumps(packages, sort_keys=True, separators=(",", ":")).encode()
    if (
        fedora_count != lock["fedora_package_count"]
        or project_count != 1
        or total_bytes != lock["total_bytes"]
        or hashlib.sha256(canonical).hexdigest() != lock["records_sha256"]
    ):
        raise ArchiveError("archive package aggregate differs")
    return lock


def safe_archive_directory(path: Path, expected_names: set[str]) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ArchiveError("archive directory path must be absolute and normalized")
    try:
        metadata = os.lstat(path)
        entries = list(os.scandir(path))
    except OSError as exc:
        raise ArchiveError("archive directory is missing or unsafe") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ArchiveError("archive directory must be owned mode-0700")
    observed_names = {entry.name for entry in entries}
    if len(entries) != len(observed_names) or observed_names != expected_names:
        raise ArchiveError("archive directory has missing, duplicate or extra entries")
    for entry in entries:
        try:
            item = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ArchiveError("archive entry is missing or unsafe") from exc
        if (
            not stat.S_ISREG(item.st_mode)
            or stat.S_ISLNK(item.st_mode)
            or item.st_nlink != 1
            or item.st_uid not in {0, os.getuid()}
            or stat.S_IMODE(item.st_mode) & 0o022
        ):
            raise ArchiveError("archive entry metadata is unsafe")


def read_locked(path: Path, size: int, digest: str, maximum: int, label: str) -> bytes:
    try:
        data = repro.read_regular(path, maximum)
    except repro.ReproError as exc:
        raise ArchiveError(f"{label} is missing or unsafe") from exc
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ArchiveError(f"{label} content differs")
    return data


def safe_executable(path: Path, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ArchiveError(f"{label} is missing or unsafe") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise ArchiveError(f"{label} is missing or unsafe")


def run_bounded(command: list[str], label: str, home: Path) -> bytes:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
            env={
                "LC_ALL": "C",
                "LANG": "C",
                "HOME": os.fspath(home),
                "XDG_CONFIG_HOME": os.fspath(home / "config"),
                "PATH": "/usr/bin:/bin",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ArchiveError(f"{label} could not run") from exc
    if result.returncode != 0 or result.stderr or len(result.stdout) > MAX_TOOL_OUTPUT:
        raise ArchiveError(f"{label} failed or exceeded its output bound")
    return result.stdout


def parse_query(data: bytes, expected: dict[str, Any]) -> None:
    try:
        values = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise ArchiveError("RPM query output is not UTF-8") from exc
    observed = [
        expected["name"],
        str(expected["epoch"]),
        expected["version"],
        expected["release"],
        expected["arch"],
        expected["payload_sha256"],
    ]
    if values != observed:
        raise ArchiveError("RPM identity or payload digest differs")


def parse_signature_output(data: bytes, artifact: Path, fingerprint: str) -> None:
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise ArchiveError("RPM signature output is not UTF-8") from exc
    if not lines or lines[0] != f"{artifact}:":
        raise ArchiveError("RPM signature artifact identity is invalid")
    signatures = []
    digests = []
    for line in lines[1:]:
        match = SIGNATURE_RE.fullmatch(line)
        if match is None:
            digests.append(line)
        else:
            signatures.append(match)
    if (
        len(signatures) != 1
        or signatures[0]["fingerprint"].lower() != fingerprint
        or len(digests) != 2
        or set(digests) != DIGEST_LINES
    ):
        raise ArchiveError("Fedora RPM signature or digest set is invalid")


def verify(
    lock: dict[str, Any],
    archive_dir: Path,
    source_lock_path: Path,
    build_lock_path: Path,
    rpm: Path,
    rpmkeys: Path,
    rpm2archive: Path,
) -> dict[str, Any]:
    key = lock["fedora_key"]
    expected_names = {key["filename"]} | {
        record["filename"] for record in lock["packages"]
    }
    safe_archive_directory(archive_dir, expected_names)
    public_key = archive_dir / key["filename"]
    read_locked(public_key, key["size"], key["sha256"], MAX_KEY_BYTES, "Fedora key")
    build_lock = repro.load_lock(build_lock_path)
    project_records = [
        record for record in lock["packages"] if record["origin"] == "project-pre-sign"
    ]
    project = project_records[0]
    expected_binary = build_lock["package"]["binary_result"]
    if (
        project["filename"] != expected_binary["filename"]
        or project["size"] != expected_binary["size"]
        or project["sha256"] != expected_binary["sha256"]
        or project["payload_sha256"] != expected_binary["payload_sha256"]
    ):
        raise ArchiveError("project package differs from the canonical build lock")
    with tempfile.TemporaryDirectory(prefix="pds020-archive-keyring-") as name:
        temporary_home = Path(name)
        database = temporary_home / "rpmdb"
        database.mkdir(mode=0o700)
        common = [str(rpmkeys), "--dbpath", str(database)]
        if run_bounded(common + ["--import", str(public_key)], "Fedora key import", temporary_home):
            raise ArchiveError("Fedora key import produced unexpected output")
        listed = run_bounded(common + ["--list"], "isolated key listing", temporary_home)
        try:
            key_lines = listed.decode("utf-8", errors="strict").splitlines()
        except UnicodeError as exc:
            raise ArchiveError("isolated key listing is not UTF-8") from exc
        if (
            len(key_lines) != 1
            or not key_lines[0].lower().startswith(f"{key['fingerprint']} ")
            or not key_lines[0].endswith(" public key")
        ):
            raise ArchiveError("isolated keyring does not contain exactly the Fedora key")
        for record in lock["packages"]:
            artifact = archive_dir / record["filename"]
            read_locked(artifact, record["size"], record["sha256"], MAX_RPM_BYTES, "RPM")
            queried = run_bounded(
                [str(rpm), "-qp", "--qf", QUERY_FORMAT, str(artifact)],
                "RPM identity query",
                temporary_home,
            )
            parse_query(queried, record)
            if record["origin"] != "project-pre-sign":
                checked = run_bounded(
                    common + ["--checksig", "--verbose", str(artifact)],
                    "Fedora RPM signature verification",
                    temporary_home,
                )
                parse_signature_output(checked, artifact, key["fingerprint"])
    project_rpm = archive_dir / project["filename"]
    audit = auditor.audit(source_lock_path, project_rpm, rpm, rpm2archive)
    if audit.get("payload_safe") is not True:
        raise ArchiveError("project pre-sign RPM audit failed")
    read_locked(public_key, key["size"], key["sha256"], MAX_KEY_BYTES, "Fedora key")
    for record in lock["packages"]:
        read_locked(
            archive_dir / record["filename"],
            record["size"],
            record["sha256"],
            MAX_RPM_BYTES,
            "RPM",
        )
    safe_archive_directory(archive_dir, expected_names)
    return {
        "schema": 1,
        "verified": True,
        "offline": True,
        "read_only": True,
        "installed": False,
        "package_count": lock["package_count"],
        "fedora_package_count": lock["fedora_package_count"],
        "fedora_signatures_verified": True,
        "project_payload_audited": True,
        "project_signature_verified": False,
        "release_ready": False,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--archive-lock", type=Path, default=DEFAULT_ARCHIVE_LOCK)
    parser.add_argument("--build-lock", type=Path, default=DEFAULT_BUILD_LOCK)
    parser.add_argument("--source-lock", type=Path, default=DEFAULT_SOURCE_LOCK)
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument("--rpmkeys", type=Path, default=Path("/usr/bin/rpmkeys"))
    parser.add_argument("--rpm2archive", type=Path, default=Path("/usr/bin/rpm2archive"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        lock = load_lock(args.archive_lock)
        for path, label in (
            (args.rpm, "RPM executable"),
            (args.rpmkeys, "RPM signature verifier"),
            (args.rpm2archive, "RPM archive extractor"),
        ):
            safe_executable(path, label)
        report = verify(
            lock,
            args.archive_dir,
            args.source_lock,
            args.build_lock,
            args.rpm,
            args.rpmkeys,
            args.rpm2archive,
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        ArchiveError,
        repro.ReproError,
        auditor.AuditError,
    ) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
