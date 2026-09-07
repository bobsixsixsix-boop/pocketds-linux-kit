#!/usr/bin/env python3
"""Offline verifier for signed Chromium source archives and the pinned receipt."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO, Sequence
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "components/chromium/runtime-lock.json"
RUNTIME_VERIFIER = ROOT / "scripts/pocketds-chromium-runtime-verify.py"
READ_CHUNK = 1024 * 1024
sys.dont_write_bytecode = True


class ProvenanceError(RuntimeError):
    """The archive set, receipt or verifier failed closed."""


def load_runtime_verifier():
    spec = importlib.util.spec_from_file_location(
        "pds020_runtime_verify_for_provenance", RUNTIME_VERIFIER
    )
    if spec is None or spec.loader is None:
        raise ProvenanceError("runtime verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runtime = load_runtime_verifier()


def sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(READ_CHUNK), b""):
        digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256_stream(stream)


def archive_filename(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(PurePosixPath(parsed.path).name)
    if (
        not name.endswith(".pkg.tar.xz")
        or name in {"", ".", ".."}
        or "/" in name
        or "\\" in name
    ):
        raise ProvenanceError("receipt archive filename is unsafe")
    return name


def safe_archive(path: Path, expected_size: int, expected_sha256: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ProvenanceError("required archive is missing or unsafe")
    metadata = path.lstat()
    if metadata.st_nlink != 1 or metadata.st_size != expected_size:
        raise ProvenanceError("archive size or link count mismatch")
    if sha256_file(path) != expected_sha256:
        raise ProvenanceError("archive SHA-256 mismatch")


def normalized_members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    result: dict[str, tarfile.TarInfo] = {}
    for member in archive:
        name = member.name
        while name.startswith("./"):
            name = name[2:]
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts:
            raise ProvenanceError("archive contains an unsafe member path")
        normalized = path.as_posix()
        if normalized in result:
            raise ProvenanceError("archive contains a duplicate member path")
        result[normalized] = member
    return result


def member_bytes(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    maximum: int = 1_048_576,
) -> bytes:
    if not member.isreg() or not 0 <= member.size <= maximum:
        raise ProvenanceError("archive metadata member is unsafe")
    stream = archive.extractfile(member)
    if stream is None:
        raise ProvenanceError("archive metadata member is unreadable")
    data = stream.read(maximum + 1)
    if len(data) != member.size:
        raise ProvenanceError("archive metadata member size changed")
    return data


def verify_regular_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    expected_size: int,
    expected_sha256: str,
) -> None:
    if not member.isreg() or member.size != expected_size:
        raise ProvenanceError("locked archive member size or type mismatch")
    stream = archive.extractfile(member)
    if stream is None or sha256_stream(stream) != expected_sha256:
        raise ProvenanceError("locked archive member SHA-256 mismatch")


def parse_pkginfo(data: bytes) -> dict[str, list[str]]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ProvenanceError("archive .PKGINFO is not UTF-8") from exc
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            values.setdefault(key, []).append(value)
    return values


def verify_package_archive(
    name: str,
    archive_path: Path,
    receipt: dict[str, Any],
    locked_items: list[dict[str, Any]],
) -> dict[str, int]:
    safe_archive(archive_path, receipt["archive_size"], receipt["archive_sha256"])
    try:
        archive = tarfile.open(archive_path, mode="r:xz")
    except (OSError, tarfile.TarError) as exc:
        raise ProvenanceError("package archive cannot be opened") from exc
    with archive:
        members = normalized_members(archive)
        metadata = receipt["metadata"]
        for metadata_name in (".PKGINFO", ".BUILDINFO", ".MTREE"):
            member = members.get(metadata_name)
            if member is None:
                raise ProvenanceError("package archive lacks required metadata")
            expected = metadata[metadata_name]
            verify_regular_member(
                archive, member, expected["size"], expected["sha256"]
            )
        pkginfo_member = members[".PKGINFO"]
        identity = parse_pkginfo(member_bytes(archive, pkginfo_member))
        if identity.get("pkgname") != [name]:
            raise ProvenanceError("package name does not match receipt")
        if identity.get("pkgver") != [receipt["version"]]:
            raise ProvenanceError("package version does not match receipt")
        if identity.get("arch") != [receipt["arch"]]:
            raise ProvenanceError("package architecture does not match receipt")
        for item in locked_items:
            member = members.get(item["path"])
            if member is None:
                raise ProvenanceError("locked archive member is missing")
            if item["type"] == "file":
                verify_regular_member(
                    archive, member, item["size"], item["sha256"]
                )
            elif not member.issym() or member.linkname != item["target"]:
                raise ProvenanceError("locked archive symlink mismatch")
    if len(locked_items) != receipt["locked_entries_verified"]:
        raise ProvenanceError("locked archive entry count mismatch")
    return {"metadata": 3, "locked_entries": len(locked_items)}


def verify_signature(
    sqv: Path,
    certificate: Path,
    archive: Path,
    receipt: dict[str, Any],
) -> None:
    try:
        signature = base64.b64decode(receipt["signature_base64"], validate=True)
    except Exception as exc:
        raise ProvenanceError("receipt signature is not valid base64") from exc
    with tempfile.TemporaryDirectory(prefix="pds020-signature-") as temporary:
        signature_path = Path(temporary) / "archive.sig"
        descriptor = os.open(
            signature_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        try:
            view = memoryview(signature)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise ProvenanceError("detached signature could not be staged")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            result = subprocess.run(
                [
                    str(sqv),
                    "--verbose",
                    "--keyring",
                    str(certificate),
                    "--signature-file",
                    str(signature_path),
                    str(archive),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
                env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProvenanceError("signature verifier could not run") from exc
    combined = f"{result.stdout}\n{result.stderr}"
    if (
        result.returncode != 0
        or receipt["signer_fingerprint"] not in combined
        or "1 of 1 signatures are valid" not in combined
    ):
        raise ProvenanceError("detached package signature verification failed")


def run(
    lock_path: Path,
    receipt_root: Path,
    archive_dir: Path,
    sqv_path: Path,
) -> dict[str, object]:
    if lock_path.is_symlink() or not lock_path.is_file():
        raise ProvenanceError("runtime lock is missing or unsafe")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError("runtime lock is invalid") from exc
    complete, gaps = runtime.validate_lock(lock)
    if not complete or gaps:
        raise ProvenanceError("runtime lock provenance is incomplete")
    runtime.validate_provenance_receipt(lock, receipt_root)
    receipt_path = receipt_root / runtime.safe_relative(
        lock["provenance_receipt"]["path"], "provenance_receipt.path"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    key = receipt["key"]
    certificate = receipt_root / runtime.safe_relative(
        key["certificate_path"], "provenance key certificate_path"
    )
    if sqv_path.is_symlink() or not sqv_path.is_file() or not os.access(sqv_path, os.X_OK):
        raise ProvenanceError("sqv verifier is missing or unsafe")
    if archive_dir.is_symlink() or not archive_dir.is_dir():
        raise ProvenanceError("archive directory is missing or unsafe")

    sources = {"chromium": lock["package"], **lock["compat_sources"]}
    package_reports: dict[str, dict[str, object]] = {}
    for name in sorted(sources):
        package_receipt = receipt["packages"][name]
        archive = archive_dir / archive_filename(package_receipt["archive_url"])
        if name == "chromium":
            locked_items = [
                {
                    "path": artifact["path"],
                    "type": "file",
                    "size": artifact["size"],
                    "sha256": artifact["sha256"],
                }
                for artifact in lock["observed"].values()
            ]
        else:
            locked_items = [
                item for item in lock["compat_files"] if item["source_package"] == name
            ]
        archive_report = verify_package_archive(
            name, archive, package_receipt, locked_items
        )
        verify_signature(sqv_path, certificate, archive, package_receipt)
        package_reports[name] = {
            "archive": "pass",
            "signature": "pass",
            **archive_report,
        }
    return {
        "schema": 1,
        "read_only": True,
        "network": False,
        "packages": package_reports,
        "package_count": len(package_reports),
        "release_ready": True,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--sqv", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--receipt-root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = run(args.lock, args.receipt_root, args.archive_dir, args.sqv)
    except (ProvenanceError, runtime.LockError) as exc:
        print(json.dumps({"release_ready": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
