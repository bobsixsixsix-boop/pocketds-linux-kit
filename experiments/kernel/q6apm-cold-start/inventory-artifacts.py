#!/usr/bin/env python3
"""Fail-closed inventory for the private Q6APM candidate artifact root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


MAX_FILE_BYTES = 768 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 100
SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)
UF_COMPRESSED = getattr(stat, "UF_COMPRESSED", 0x20)


class InventoryError(RuntimeError):
    pass


def mode(metadata: os.stat_result) -> str:
    return f"{stat.S_IMODE(metadata.st_mode):04o}"


def directory_record(path: Path, relative: str) -> dict[str, object]:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise InventoryError(f"unsafe artifact directory: {relative}")
    return {
        "path": relative,
        "type": "directory",
        "mode": mode(metadata),
        "uid": metadata.st_uid,
        "link_count": metadata.st_nlink,
        "is_symlink": False,
    }


def file_record(path: Path, relative: str) -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise InventoryError(f"artifact is unavailable or linked: {relative}") from error
    try:
        before = os.fstat(descriptor)
        file_flags = int(getattr(before, "st_flags", 0))
        allocated_bytes = int(getattr(before, "st_blocks", 0)) * 512
        dataless = bool(file_flags & SF_DATALESS)
        compressed = bool(file_flags & UF_COMPRESSED)
        sparse = allocated_bytes < before.st_size
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > MAX_FILE_BYTES
            or dataless
            or compressed
            or sparse
        ):
            raise InventoryError(f"unsafe or unmaterialized artifact: {relative}")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                raise InventoryError(f"artifact has missing bytes: {relative}")
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise InventoryError(f"artifact grew while being read: {relative}")
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_uid,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            int(getattr(value, "st_flags", 0)),
            int(getattr(value, "st_blocks", 0)),
        )
        if identity(before) != identity(after):
            raise InventoryError(f"artifact changed while being read: {relative}")
        return {
            "path": relative,
            "type": "file",
            "mode": mode(before),
            "uid": before.st_uid,
            "link_count": before.st_nlink,
            "is_symlink": False,
            "size": before.st_size,
            "allocated_bytes": allocated_bytes,
            "filesystem_flags": file_flags,
            "dataless": dataless,
            "compressed": compressed,
            "sparse": sparse,
            "fully_readable": True,
            "sha256": digest.hexdigest(),
        }
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        supplied_root = args.root.lstat()
        if stat.S_ISLNK(supplied_root.st_mode) or not stat.S_ISDIR(supplied_root.st_mode):
            raise InventoryError("artifact root is linked or not a directory")
        root = args.root.resolve(strict=True)
        records = [directory_record(root, ".")]
        paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
        if not paths or len(paths) > MAX_ENTRIES:
            raise InventoryError("artifact entry count is unsafe")
        total_bytes = 0
        for path in paths:
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise InventoryError(f"linked artifact entry: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                records.append(directory_record(path, relative))
            elif stat.S_ISREG(metadata.st_mode):
                record = file_record(path, relative)
                total_bytes += int(record["size"])
                if total_bytes > MAX_TOTAL_BYTES:
                    raise InventoryError("artifact byte total is unsafe")
                records.append(record)
            else:
                raise InventoryError(f"unsupported artifact entry: {relative}")
        files = [record for record in records if record["type"] == "file"]
        directories = [record for record in records if record["type"] == "directory"]
        report = {
            "schema": "pocketds.q6apm-private-artifact-inventory.v1",
            "read_only": True,
            "network": False,
            "root_id": "q6apm-candidate-artifacts",
            "summary": {
                "entry_count": len(records),
                "directory_count": len(directories),
                "file_count": len(files),
                "regular_file_bytes": total_bytes,
                "root_mode": records[0]["mode"],
                "directory_mode": "0700",
                "file_mode": "0600",
                "all_owned_by_auditor": True,
                "all_single_link_files": True,
                "all_non_symlink": True,
                "all_non_dataless": True,
                "all_non_compressed": True,
                "all_non_sparse": True,
                "all_fully_readable": True,
            },
            "records": records,
            "gates": {
                "private_root": True,
                "materialized_artifacts": True,
                "artifact_install_authorized": False,
                "runtime_authorized": False,
                "deployed": False,
            },
        }
        json.dump(report, sys.stdout, sort_keys=True, indent=2)
        sys.stdout.write("\n")
        return 0
    except (OSError, InventoryError) as error:
        print(f"inventory-artifacts: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
