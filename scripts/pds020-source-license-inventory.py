#!/usr/bin/env python3
"""Inventory tracked-file hashes and SPDX headers without legal inference."""

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
from typing import Sequence


SCHEMA = "pocketds.source-license-inventory.v2"
MAX_FILES = 4_096
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
SPDX_RE = re.compile(
    r"SPDX-License-Identifier:\s*([A-Za-z0-9.+(): -]+?)\s*(?:\*/|$)",
    re.MULTILINE,
)
RELEASE_EVIDENCE_FILES = (
    "LICENSE",
    "NOTICE",
    "THIRD-PARTY.json",
    "SBOM.spdx.json",
    "components/assets/ASSETS.json",
)


class InventoryError(RuntimeError):
    """Tracked source inventory was unsafe, oversized or ambiguous."""


def git_output(repo_root: Path, arguments: tuple[str, ...]) -> bytes:
    try:
        result = subprocess.run(
            ("git", "-C", str(repo_root), *arguments),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InventoryError("Git inventory command is unavailable") from exc
    if result.returncode != 0 or len(result.stdout) > 8 * 1024 * 1024:
        raise InventoryError("Git inventory command failed or overflowed")
    return result.stdout


def tracked_paths(repo_root: Path) -> list[PurePosixPath]:
    raw = git_output(repo_root, ("ls-files", "-z"))
    entries = raw.split(b"\0")
    if entries and entries[-1] == b"":
        entries.pop()
    if not 1 <= len(entries) <= MAX_FILES:
        raise InventoryError("tracked file count is out of range")
    paths: list[PurePosixPath] = []
    for entry in entries:
        try:
            text = entry.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise InventoryError("tracked path is not UTF-8") from exc
        path = PurePosixPath(text)
        if not text or path.is_absolute() or ".." in path.parts or text != path.as_posix():
            raise InventoryError("tracked path is unsafe")
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise InventoryError("tracked path list contains duplicates")
    return sorted(paths, key=lambda item: item.as_posix())


def read_tracked(repo_root: Path, relative: PurePosixPath) -> tuple[bytes, int]:
    path = repo_root.joinpath(*relative.parts)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InventoryError("tracked file is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or not 0 <= metadata.st_size <= MAX_FILE_BYTES
        ):
            raise InventoryError("tracked file is unsafe or oversized")
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise InventoryError("tracked file changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise InventoryError("tracked file grew while reading")
        return bytes(content), stat.S_IMODE(metadata.st_mode)
    finally:
        os.close(descriptor)


def classify(relative: PurePosixPath, content: bytes, mode: int) -> dict[str, object]:
    digest = hashlib.sha256(content).hexdigest()
    binary = b"\0" in content
    text: str | None = None
    if not binary:
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            binary = True
    expression = None
    if text is not None:
        match = SPDX_RE.search(text[:16_384])
        expression = match.group(1) if match else None
    return {
        "path": relative.as_posix(),
        "sha256": digest,
        "bytes": len(content),
        "mode": f"{mode:04o}",
        "content_kind": "binary" if binary else "utf8-text",
        "spdx_header": expression,
    }


def inventory(
    repo_root: Path,
    paths: list[PurePosixPath],
    *,
    revision: str,
    dirty_change_count: int,
) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None or dirty_change_count < 0:
        raise InventoryError("repository identity is invalid")
    files: list[dict[str, object]] = []
    total = 0
    for relative in paths:
        content, mode = read_tracked(repo_root, relative)
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise InventoryError("tracked content exceeds the inventory bound")
        files.append(classify(relative, content, mode))
    license_counts: dict[str, int] = {}
    for item in files:
        expression = item["spdx_header"]
        if expression is not None:
            license_counts[expression] = license_counts.get(expression, 0) + 1
    tracked_names = {item["path"] for item in files}
    release_evidence = {
        name: (name in tracked_names) for name in RELEASE_EVIDENCE_FILES
    }
    return {
        "schema": SCHEMA,
        "scope": "all-git-tracked-files",
        "repository_revision": revision,
        "dirty_change_count": dirty_change_count,
        "file_count": len(files),
        "total_bytes": total,
        "content_counts": {
            kind: sum(item["content_kind"] == kind for item in files)
            for kind in ("utf8-text", "binary")
        },
        "spdx_header_counts": dict(sorted(license_counts.items())),
        "files_with_spdx_header": sum(item["spdx_header"] is not None for item in files),
        "files_without_spdx_header": sum(item["spdx_header"] is None for item in files),
        "release_evidence_files_tracked": release_evidence,
        "files": files,
        "legal_conclusion": "NOT_DETERMINED",
        "rights_or_redistribution_inferred": False,
        "release_ready": False,
        "inventory_complete": True,
    }


def repository_inventory(repo_root: Path) -> dict[str, object]:
    revision = git_output(repo_root, ("rev-parse", "HEAD")).decode("ascii").strip()
    dirty = git_output(
        repo_root,
        ("status", "--porcelain=v1", "--untracked-files=normal"),
    )
    dirty_count = len([line for line in dirty.splitlines() if line])
    return inventory(
        repo_root,
        tracked_paths(repo_root),
        revision=revision,
        dirty_change_count=dirty_count,
    )


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short source inventory write")
        view = view[written:]


def write_report(report: dict[str, object], destination: str) -> None:
    content = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if destination == "-":
        sys.stdout.buffer.write(content)
        return
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(destination, flags, 0o600)
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="-")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        report = repository_inventory(repo_root)
        write_report(report, arguments.output)
    except (InventoryError, OSError, UnicodeError) as exc:
        print(f"source license inventory failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
