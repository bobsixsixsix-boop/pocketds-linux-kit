#!/usr/bin/env python3
"""Verify the pinned COPR/source-snapshot evidence for PDS-002 offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO, Callable


SPEC_SCHEMA_VERSION = 1
REPORT_SCHEMA = "pocketds.kernel-source-evidence.v1"
TREE_SCHEMA = "pocketds.normalized-source-tree.v1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_SPEC_BYTES = 65_536
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_TREE_ENTRIES = 200_000
MAX_TREE_BYTES = 8 * 1024 * 1024 * 1024
ARTIFACT_NAMES = {
    "repomd",
    "primary",
    "binary_rpm",
    "source_rpm",
    "srpm_source_archive",
    "commit_archive",
    "lid_source_patch",
    "copr_keyring",
    "sqv",
    "rebuilt_dtb",
}
SPEC_FIELDS = {
    "schema_version",
    "expected_source_commit",
    "expected_source_tree_sha256",
    "copr_build_id",
    "package_nevra",
    "srpm_archive_root",
    "commit_archive_root",
    "artifacts",
    "expected_signing_fingerprint",
}
UNRESOLVED_GATES = [
    "source_to_binary_reproducible_build_not_proven",
]
RPM_LEAD_BYTES = 96
RPM_LEAD_MAGIC = b"\xed\xab\xee\xdb"
RPM_HEADER_MAGIC = b"\x8e\xad\xe8"
RPM_SIGNATURE_TAG_RSA = 268
RPM_BIN_TYPE = 7
MAX_RPM_INDEX_ENTRIES = 4096
MAX_RPM_HEADER_STORE_BYTES = 16 * 1024 * 1024
MAX_RPM_SIGNATURE_BYTES = 16 * 1024
HEX_FINGERPRINT = re.compile(r"^[0-9A-F]{40}$")


class SourceEvidenceError(RuntimeError):
    """A source artifact is unsafe, incomplete, or differs from the lock."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_json(content: bytes, where: str) -> object:
    def reject_constant(value: str) -> object:
        raise SourceEvidenceError(f"{where} contains non-finite JSON: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SourceEvidenceError(f"{where} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SourceEvidenceError(f"{where} is not strict UTF-8 JSON") from exc


def _read_locked_file(path: Path, *, maximum: int) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise SourceEvidenceError(f"artifact is unavailable or linked: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > maximum
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise SourceEvidenceError(f"artifact is unsafe, empty, or oversized: {path.name}")
        content = bytearray()
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                raise SourceEvidenceError(f"artifact changed while reading: {path.name}")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise SourceEvidenceError(f"artifact grew while reading: {path.name}")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise SourceEvidenceError(f"artifact identity changed while reading: {path.name}")
        return bytes(content), before
    finally:
        os.close(descriptor)


def load_spec(path: Path) -> dict[str, object]:
    content, _metadata = _read_locked_file(path, maximum=MAX_SPEC_BYTES)
    parsed = _strict_json(content, "source evidence spec")
    if not isinstance(parsed, dict):
        raise SourceEvidenceError("source evidence spec root must be an object")
    return parsed


def _string(value: object, name: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise SourceEvidenceError(f"invalid {name}")
    return value


def validate_spec(raw: dict[str, object]) -> dict[str, object]:
    if set(raw) != SPEC_FIELDS:
        raise SourceEvidenceError("source evidence spec fields differ")
    if raw["schema_version"] != SPEC_SCHEMA_VERSION:
        raise SourceEvidenceError("unsupported source evidence spec schema")
    commit = _string(raw["expected_source_commit"], "expected source commit")
    tree_sha = _string(raw["expected_source_tree_sha256"], "expected source tree SHA-256")
    if HEX40.fullmatch(commit) is None or HEX64.fullmatch(tree_sha) is None:
        raise SourceEvidenceError("source commit or tree SHA-256 is invalid")
    fingerprint = _string(raw["expected_signing_fingerprint"], "signing fingerprint")
    if HEX_FINGERPRINT.fullmatch(fingerprint) is None:
        raise SourceEvidenceError("signing fingerprint is invalid")
    build_id = raw["copr_build_id"]
    if not isinstance(build_id, int) or isinstance(build_id, bool) or build_id <= 0:
        raise SourceEvidenceError("COPR build ID is invalid")
    artifacts = raw["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != ARTIFACT_NAMES:
        raise SourceEvidenceError("source evidence artifact set differs")
    clean_artifacts: dict[str, dict[str, object]] = {}
    for name in sorted(ARTIFACT_NAMES):
        record = artifacts[name]
        if not isinstance(record, dict) or set(record) != {"filename", "sha256", "size"}:
            raise SourceEvidenceError(f"invalid {name} artifact record")
        filename = _string(record["filename"], f"{name} filename")
        if filename != Path(filename).name or "/" in filename or "\\" in filename:
            raise SourceEvidenceError(f"invalid {name} filename")
        sha256 = _string(record["sha256"], f"{name} SHA-256")
        size = record["size"]
        if HEX64.fullmatch(sha256) is None:
            raise SourceEvidenceError(f"invalid {name} SHA-256")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or size > MAX_ARTIFACT_BYTES
        ):
            raise SourceEvidenceError(f"invalid {name} size")
        clean_artifacts[name] = {"filename": filename, "sha256": sha256, "size": size}
    roots = {
        key: _string(raw[key], key)
        for key in ("srpm_archive_root", "commit_archive_root")
    }
    for name, root in roots.items():
        pure = PurePosixPath(root)
        if len(pure.parts) != 1 or str(pure) != root or root in {".", ".."}:
            raise SourceEvidenceError(f"invalid {name}")
    return {
        "schema_version": SPEC_SCHEMA_VERSION,
        "expected_source_commit": commit,
        "expected_source_tree_sha256": tree_sha,
        "expected_signing_fingerprint": fingerprint,
        "copr_build_id": build_id,
        "package_nevra": _string(raw["package_nevra"], "package NEVRA"),
        **roots,
        "artifacts": clean_artifacts,
    }


def _hash_artifact(path: Path, expected: dict[str, object]) -> dict[str, object]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise SourceEvidenceError(f"artifact is unavailable or linked: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size != expected["size"]
            or before.st_size <= 0
            or before.st_size > MAX_ARTIFACT_BYTES
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise SourceEvidenceError(f"artifact size, type, or mode differs: {path.name}")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                raise SourceEvidenceError(f"artifact changed while hashing: {path.name}")
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise SourceEvidenceError(f"artifact grew while hashing: {path.name}")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise SourceEvidenceError(f"artifact identity changed while hashing: {path.name}")
        observed = digest.hexdigest()
        if observed != expected["sha256"]:
            raise SourceEvidenceError(f"artifact SHA-256 differs: {path.name}")
        return {
            "filename": str(expected["filename"]),
            "sha256": observed,
            "size": before.st_size,
        }
    finally:
        os.close(descriptor)


class _DigestingReader:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        block = self.stream.read(size)
        self.digest.update(block)
        self.size += len(block)
        return block


def _safe_relative(member_name: str, expected_root: str) -> str | None:
    if not member_name or "\0" in member_name or any(ord(char) < 32 for char in member_name):
        raise SourceEvidenceError("source archive contains an invalid member name")
    path = PurePosixPath(member_name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise SourceEvidenceError("source archive contains an unsafe member path")
    if not path.parts or path.parts[0] != expected_root:
        raise SourceEvidenceError("source archive root differs")
    if len(path.parts) == 1:
        return None
    return "/".join(path.parts[1:])


def _safe_link(relative: str, target: str) -> str:
    if not target or "\0" in target or any(ord(char) < 32 for char in target):
        raise SourceEvidenceError("source archive contains an invalid symlink")
    pure = PurePosixPath(target)
    if pure.is_absolute():
        raise SourceEvidenceError("source archive symlink is absolute")
    combined: list[str] = list(PurePosixPath(relative).parent.parts)
    for part in pure.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not combined:
                raise SourceEvidenceError("source archive symlink escapes its root")
            combined.pop()
        else:
            combined.append(part)
    return target


def _scan_tree(path: Path, expected_root: str) -> tuple[dict[str, tuple[object, ...]], str]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise SourceEvidenceError(f"source archive is unavailable or linked: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_ARTIFACT_BYTES
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise SourceEvidenceError(f"source archive is unsafe: {path.name}")
        tree: dict[str, tuple[object, ...]] = {}
        total_bytes = 0
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            try:
                archive = tarfile.open(fileobj=stream, mode="r|gz")
            except (tarfile.TarError, OSError) as exc:
                raise SourceEvidenceError(f"source archive cannot be opened: {path.name}") from exc
            try:
                for member in archive:
                    if len(tree) >= MAX_TREE_ENTRIES:
                        raise SourceEvidenceError("source archive has too many entries")
                    relative = _safe_relative(member.name, expected_root)
                    if relative is None:
                        if not member.isdir():
                            raise SourceEvidenceError("source archive root is not a directory")
                        continue
                    if relative in tree:
                        raise SourceEvidenceError("source archive contains duplicate paths")
                    mode = member.mode & 0o777
                    if member.isfile():
                        if member.size < 0 or member.size > MAX_TREE_BYTES - total_bytes:
                            raise SourceEvidenceError("source archive expanded size is invalid")
                        extracted = archive.extractfile(member)
                        if extracted is None:
                            raise SourceEvidenceError("source archive file stream is unavailable")
                        reader = _DigestingReader(extracted)
                        while reader.read(1_048_576):
                            pass
                        if reader.size != member.size:
                            raise SourceEvidenceError("source archive member size differs")
                        total_bytes += reader.size
                        tree[relative] = ("file", mode, reader.size, reader.digest.hexdigest())
                    elif member.isdir():
                        tree[relative] = ("dir", mode)
                    elif member.issym():
                        tree[relative] = ("symlink", mode, _safe_link(relative, member.linkname))
                    else:
                        raise SourceEvidenceError("source archive contains an unsupported entry type")
            except (tarfile.TarError, OSError, EOFError) as exc:
                raise SourceEvidenceError(f"source archive is truncated: {path.name}") from exc
            finally:
                archive.close()
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise SourceEvidenceError(f"source archive identity changed: {path.name}")
    finally:
        os.close(descriptor)
    digest = hashlib.sha256()
    digest.update(canonical_bytes({"schema": TREE_SCHEMA}))
    for relative in sorted(tree):
        digest.update(b"\0")
        digest.update(canonical_bytes([relative, *tree[relative]]))
    return tree, digest.hexdigest()


def _rpm_header(
    content: mmap.mmap,
    offset: int,
    label: str,
) -> tuple[list[tuple[int, int, int, int]], int, int]:
    if offset < 0 or offset > len(content) - 16:
        raise SourceEvidenceError(f"{label} RPM header offset is invalid")
    if content[offset : offset + 3] != RPM_HEADER_MAGIC or content[offset + 3] != 1:
        raise SourceEvidenceError(f"{label} RPM header magic or version differs")
    if content[offset + 4 : offset + 8] != b"\0" * 4:
        raise SourceEvidenceError(f"{label} RPM header reserved bytes differ")
    index_count, store_size = struct.unpack_from(">II", content, offset + 8)
    if index_count > MAX_RPM_INDEX_ENTRIES or store_size > MAX_RPM_HEADER_STORE_BYTES:
        raise SourceEvidenceError(f"{label} RPM header bounds are excessive")
    index_start = offset + 16
    store_start = index_start + index_count * 16
    header_end = store_start + store_size
    if store_start < index_start or header_end < store_start or header_end > len(content):
        raise SourceEvidenceError(f"{label} RPM header is truncated")
    entries = [
        struct.unpack_from(">IIII", content, index_start + index * 16)
        for index in range(index_count)
    ]
    return entries, store_start, header_end


def rpm_signature_material(path: Path) -> tuple[bytes, bytes, dict[str, object]]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise SourceEvidenceError(f"RPM is unavailable or linked: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= RPM_LEAD_BYTES
            or before.st_size > MAX_ARTIFACT_BYTES
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise SourceEvidenceError(f"RPM is unsafe, empty, or oversized: {path.name}")
        with mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ) as content:
            if content[:4] != RPM_LEAD_MAGIC or content[4] != 3:
                raise SourceEvidenceError(f"RPM lead magic or version differs: {path.name}")
            signature_entries, signature_store, signature_end = _rpm_header(
                content, RPM_LEAD_BYTES, "signature"
            )
            matches = [entry for entry in signature_entries if entry[0] == RPM_SIGNATURE_TAG_RSA]
            if len(matches) != 1:
                raise SourceEvidenceError("RPM has no unique RSA header signature")
            _tag, value_type, value_offset, value_count = matches[0]
            if (
                value_type != RPM_BIN_TYPE
                or value_count <= 0
                or value_count > MAX_RPM_SIGNATURE_BYTES
                or value_offset > signature_end - signature_store
                or value_count > signature_end - signature_store - value_offset
            ):
                raise SourceEvidenceError("RPM RSA header signature bounds differ")
            signature = bytes(
                content[
                    signature_store + value_offset : signature_store + value_offset + value_count
                ]
            )
            main_offset = (signature_end + 7) & ~7
            _main_entries, _main_store, main_end = _rpm_header(content, main_offset, "main")
            signed_header = bytes(content[main_offset:main_end])
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise SourceEvidenceError(f"RPM identity changed while reading: {path.name}")
    finally:
        os.close(descriptor)
    return signature, signed_header, {
        "signature_tag": RPM_SIGNATURE_TAG_RSA,
        "signature_sha256": hashlib.sha256(signature).hexdigest(),
        "signature_size": len(signature),
        "signed_header_sha256": hashlib.sha256(signed_header).hexdigest(),
        "signed_header_size": len(signed_header),
    }


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise SourceEvidenceError("temporary signature material could not be written")
            remaining = remaining[written:]
    finally:
        os.close(descriptor)


def verify_rpm_signatures(
    paths: dict[str, Path],
    expected_fingerprint: str,
) -> dict[str, dict[str, object]]:
    sqv = paths["sqv"]
    if not os.access(sqv, os.X_OK):
        raise SourceEvidenceError("pinned sqv verifier is not executable")
    results: dict[str, dict[str, object]] = {}
    for name in ("binary_rpm", "source_rpm"):
        signature, signed_header, metadata = rpm_signature_material(paths[name])
        with tempfile.TemporaryDirectory(prefix="pocketds-rpm-signature-") as temporary:
            private = Path(temporary)
            signature_path = private / "signature.pgp"
            header_path = private / "signed-header.bin"
            _write_private(signature_path, signature)
            _write_private(header_path, signed_header)
            try:
                process = subprocess.run(
                    [
                        str(sqv),
                        "--keyring",
                        str(paths["copr_keyring"]),
                        "--signature-file",
                        str(signature_path),
                        str(header_path),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SourceEvidenceError("pinned sqv verifier could not complete") from exc
            fingerprints = {
                line.strip()
                for line in process.stdout.splitlines()
                if HEX_FINGERPRINT.fullmatch(line.strip()) is not None
            }
            if process.returncode != 0 or fingerprints != {expected_fingerprint}:
                raise SourceEvidenceError(f"{name} OpenPGP signature verification failed")
        results[name] = {
            **metadata,
            "signing_fingerprint": expected_fingerprint,
            "verified": True,
        }
    return results


def collect(
    raw_spec: dict[str, object],
    paths: dict[str, Path],
    *,
    signature_verifier: Callable[
        [dict[str, Path], str], dict[str, dict[str, object]]
    ] = verify_rpm_signatures,
) -> dict[str, object]:
    spec = validate_spec(raw_spec)
    if set(paths) != ARTIFACT_NAMES:
        raise SourceEvidenceError("provided artifact path set differs")
    measured = {
        name: _hash_artifact(paths[name], spec["artifacts"][name])
        for name in sorted(ARTIFACT_NAMES)
    }
    rpm_signatures = signature_verifier(paths, str(spec["expected_signing_fingerprint"]))
    if set(rpm_signatures) != {"binary_rpm", "source_rpm"}:
        raise SourceEvidenceError("RPM signature result set differs")
    if any(record.get("verified") is not True for record in rpm_signatures.values()):
        raise SourceEvidenceError("RPM signature result is not verified")
    srpm_tree, srpm_tree_sha = _scan_tree(
        paths["srpm_source_archive"], str(spec["srpm_archive_root"])
    )
    commit_tree, commit_tree_sha = _scan_tree(
        paths["commit_archive"], str(spec["commit_archive_root"])
    )
    if srpm_tree != commit_tree:
        raise SourceEvidenceError("SRPM source tree differs from the expected commit archive")
    expected_tree_sha = str(spec["expected_source_tree_sha256"])
    if srpm_tree_sha != expected_tree_sha or commit_tree_sha != expected_tree_sha:
        raise SourceEvidenceError("normalized source tree SHA-256 differs from the lock")
    gates = {
        "copr_metadata_artifacts_locked": True,
        "published_binary_rpm_locked": True,
        "published_source_rpm_locked": True,
        "srpm_source_member_locked": True,
        "commit_archive_locked": True,
        "source_snapshot_matches_expected_commit_tree": True,
        "custom_dtb_source_patch_locked": True,
        "rpm_archive_signature_verified": True,
        "source_to_binary_reproducible_build_proven": False,
        "custom_dtb_rebuild_reproduced": True,
    }
    return {
        "schema": REPORT_SCHEMA,
        "spec_sha256": hashlib.sha256(canonical_bytes(spec)).hexdigest(),
        "package": {
            "copr_build_id": spec["copr_build_id"],
            "nevra": spec["package_nevra"],
        },
        "expected_source_commit": spec["expected_source_commit"],
        "artifacts": measured,
        "rpm_signatures": rpm_signatures,
        "source_tree": {
            "schema": TREE_SCHEMA,
            "entry_count": len(srpm_tree),
            "sha256": srpm_tree_sha,
            "matches_expected_commit": True,
        },
        "gates": gates,
        "unresolved_gates": UNRESOLVED_GATES,
        "source_to_binary_provenance_complete": all(gates.values()),
        "read_only": True,
        "network": False,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline-check the pinned COPR kernel and two normalized source archives."
    )
    for name in sorted(ARTIFACT_NAMES):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true", help="pretty-print one JSON report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    paths = {name: getattr(args, name) for name in ARTIFACT_NAMES}
    try:
        report = collect(load_spec(Path(__file__).with_name("source-evidence-spec.json")), paths)
    except SourceEvidenceError as exc:
        print(f"kernel-source-evidence: refused: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.pretty
        else canonical_bytes(report).decode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
