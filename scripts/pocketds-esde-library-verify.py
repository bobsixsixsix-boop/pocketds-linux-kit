#!/usr/bin/env python3
"""Verify an exact ES-DE import tree against a pinned bounded inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, BinaryIO


MAX_MANIFEST_BYTES = 1024 * 1024
MAX_INVENTORY_BYTES = 16 * 1024 * 1024
MAX_INVENTORY_FILES = 1_000_000
MAX_INVENTORY_LINE_BYTES = 8192
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MANAGED_ROOTS = (
    PurePosixPath("ROMs"),
    PurePosixPath("ES-DE/gamelists"),
    PurePosixPath("ES-DE/downloaded_media"),
    PurePosixPath("ES-DE/collections"),
)


def reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def open_regular(path: Path, maximum: int, label: str) -> tuple[BinaryIO, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} is unsafe or unavailable") from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > maximum
    ):
        os.close(descriptor)
        raise ValueError(f"{label} is unsafe or oversized")
    return os.fdopen(descriptor, "rb"), metadata.st_size


def read_regular_bytes(path: Path, maximum: int, label: str) -> bytes:
    stream, expected = open_regular(path, maximum, label)
    with stream:
        payload = stream.read(expected + 1)
    if len(payload) != expected:
        raise ValueError(f"{label} changed while reading")
    return payload


def load_json(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from exc


def exact_int(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"invalid {label}")
    return value


def safe_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("invalid inventory path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise ValueError("unsafe inventory path")
    if len(pure.parts) < 2 or pure.parts[0] not in {"ROMs", "ES-DE"}:
        raise ValueError("inventory path is outside the managed library")
    if pure.parts[0] == "ES-DE" and (
        len(pure.parts) < 3
        or PurePosixPath(*pure.parts[:2]) not in MANAGED_ROOTS
    ):
        raise ValueError("inventory path is outside the managed ES-DE directories")
    return Path(*pure.parts)


def load_inventory(payload: bytes) -> list[tuple[Path, int]]:
    result: list[tuple[Path, int]] = []
    seen: set[str] = set()
    consumed = 0
    for raw in payload.splitlines(keepends=True):
        consumed += len(raw)
        if len(raw) > MAX_INVENTORY_LINE_BYTES:
            raise ValueError("inventory line is oversized")
        if not raw.endswith(b"\n"):
            raise ValueError("inventory line is unterminated")
        try:
            item = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("inventory contains invalid JSONL") from exc
        if not isinstance(item, dict) or set(item) != {"path", "size"}:
            raise ValueError("inventory row has an invalid schema")
        relative = safe_relative(item["path"])
        key = relative.as_posix()
        if key in seen:
            raise ValueError("inventory contains a duplicate path")
        seen.add(key)
        result.append((relative, exact_int(item["size"], "inventory size")))
        if len(result) > MAX_INVENTORY_FILES:
            raise ValueError("inventory has too many files")
    if consumed != len(payload):
        raise ValueError("inventory changed while reading")
    return result


def validate_manifest(manifest: Any, inventory_name: str) -> tuple[int, int]:
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema")) is not int
        or manifest["schema"] != 1
    ):
        raise ValueError("unsupported library manifest")
    inventory = manifest.get("inventory")
    if not isinstance(inventory, dict):
        raise ValueError("manifest inventory is missing")
    if inventory.get("file") != inventory_name:
        raise ValueError("manifest and inventory filename disagree")
    return (
        exact_int(inventory.get("files"), "manifest file count"),
        exact_int(inventory.get("logical_bytes"), "manifest byte count"),
    )


def require_sha256(value: str, label: str) -> str:
    if not SHA256_RE.fullmatch(value):
        raise ValueError(f"invalid expected {label} SHA-256")
    return value


def expected_directories(inventory: list[tuple[Path, int]]) -> set[str]:
    result = {root.as_posix() for root in MANAGED_ROOTS}
    for relative, _size in inventory:
        parent = PurePosixPath(relative.as_posix()).parent
        while parent != PurePosixPath("."):
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def validate_tree(
    home: Path,
    inventory: list[tuple[Path, int]],
    *,
    require_owner: bool,
    label: str,
) -> tuple[int, int]:
    expected_files = {relative.as_posix(): size for relative, size in inventory}
    allowed_directories = expected_directories(inventory)
    seen_files: set[str] = set()
    hardlinked_files = 0
    checked_bytes = 0

    def walk_error(error: OSError) -> None:
        raise ValueError(f"cannot traverse {label} tree") from error

    for managed in MANAGED_ROOTS:
        root = home / Path(*managed.parts)
        required = any(
            relative == managed.as_posix()
            or relative.startswith(managed.as_posix() + "/")
            for relative in expected_files
        )
        current_root = home
        root_available = True
        for part in managed.parts:
            current_root /= part
            current_relative = current_root.relative_to(home).as_posix()
            try:
                root_metadata = current_root.lstat()
            except FileNotFoundError:
                root_available = False
                break
            if not stat.S_ISDIR(root_metadata.st_mode):
                raise ValueError(f"{label} directory is unsafe: {current_relative}")
            if require_owner and root_metadata.st_uid != os.getuid():
                raise ValueError(
                    f"{label} directory has the wrong owner: {current_relative}"
                )
        if not root_available:
            if required:
                raise ValueError(f"missing {label} directory: {managed.as_posix()}")
            continue

        for current, directories, filenames in os.walk(
            root, followlinks=False, onerror=walk_error
        ):
            current_path = Path(current)
            current_relative = current_path.relative_to(home).as_posix()
            if current_relative not in allowed_directories:
                raise ValueError(f"unexpected {label} directory: {current_relative}")
            for name in directories:
                child = current_path / name
                relative = child.relative_to(home).as_posix()
                metadata = child.lstat()
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ValueError(f"{label} directory is unsafe: {relative}")
                if relative not in allowed_directories:
                    raise ValueError(f"unexpected {label} directory: {relative}")
                if require_owner and metadata.st_uid != os.getuid():
                    raise ValueError(f"{label} directory has the wrong owner: {relative}")
            for name in filenames:
                target = current_path / name
                relative = target.relative_to(home).as_posix()
                metadata = target.lstat()
                if relative not in expected_files:
                    raise ValueError(f"unexpected {label} file: {relative}")
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError(f"{label} file is not regular: {relative}")
                if require_owner and metadata.st_uid != os.getuid():
                    raise ValueError(f"{label} file has the wrong owner: {relative}")
                if metadata.st_size != expected_files[relative]:
                    raise ValueError(f"{label} file has the wrong size: {relative}")
                seen_files.add(relative)
                checked_bytes += metadata.st_size
                hardlinked_files += int(metadata.st_nlink > 1)

    missing = set(expected_files) - seen_files
    if missing:
        raise ValueError(f"missing {label} file: {sorted(missing)[0]}")
    return checked_bytes, hardlinked_files


def hash_regular(
    path: Path, expected_size: int, label: str
) -> tuple[str, tuple[int, int, int, int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} is unsafe or unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise ValueError(f"{label} changed before hashing")
        digest = hashlib.sha256()
        consumed = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            consumed += len(block)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if consumed != expected_size or identity_before != identity_after:
            raise ValueError(f"{label} changed while hashing")
        return digest.hexdigest(), identity_before
    finally:
        os.close(descriptor)


def verify(
    home: Path,
    manifest_path: Path,
    inventory_path: Path,
    *,
    expected_manifest_sha256: str,
    expected_inventory_sha256: str,
    mode: str,
    source_home: Path | None,
) -> dict[str, Any]:
    if mode not in {"fast", "full"}:
        raise ValueError("unsupported verification mode")
    if not home.is_dir() or home.is_symlink():
        raise ValueError("home is unsafe or unavailable")
    home = home.resolve()
    manifest_payload = read_regular_bytes(manifest_path, MAX_MANIFEST_BYTES, "manifest")
    inventory_payload = read_regular_bytes(
        inventory_path, MAX_INVENTORY_BYTES, "inventory"
    )
    manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    inventory_sha256 = hashlib.sha256(inventory_payload).hexdigest()
    if manifest_sha256 != require_sha256(expected_manifest_sha256, "manifest"):
        raise ValueError("manifest SHA-256 does not match the pinned identity")
    if inventory_sha256 != require_sha256(expected_inventory_sha256, "inventory"):
        raise ValueError("inventory SHA-256 does not match the pinned identity")

    manifest = load_json(manifest_payload, "manifest")
    expected_files, expected_bytes = validate_manifest(manifest, inventory_path.name)
    inventory = load_inventory(inventory_payload)
    inventory_bytes = sum(size for _path, size in inventory)
    if len(inventory) != expected_files or inventory_bytes != expected_bytes:
        raise ValueError("inventory totals disagree with the manifest")

    checked_bytes, hardlinked_files = validate_tree(
        home, inventory, require_owner=True, label="managed"
    )

    trusted_source_pairwise_verified = False
    content_bytes_read = 0
    if mode == "full":
        if source_home is None:
            raise ValueError("full verification requires --source-home")
        if not source_home.is_dir() or source_home.is_symlink():
            raise ValueError("source home is unsafe or unavailable")
        source_home = source_home.resolve()
        if source_home == home or os.path.samefile(source_home, home):
            raise ValueError("trusted source home must be distinct from managed home")
        source_bytes, _source_hardlinks = validate_tree(
            source_home, inventory, require_owner=False, label="source"
        )
        if source_bytes != checked_bytes:
            raise ValueError("source and target totals disagree")
        observed_identities: list[
            tuple[Path, int, tuple[int, int, int, int, int], tuple[int, int, int, int, int]]
        ] = []
        for relative, expected_size in inventory:
            source_digest, source_identity = hash_regular(
                source_home / relative,
                expected_size,
                f"source file {relative.as_posix()}",
            )
            target_digest, target_identity = hash_regular(
                home / relative,
                expected_size,
                f"managed file {relative.as_posix()}",
            )
            if source_identity[:2] == target_identity[:2]:
                raise ValueError(
                    f"trusted source aliases managed file: {relative.as_posix()}"
                )
            if source_digest != target_digest:
                raise ValueError(f"content differs: {relative.as_posix()}")
            observed_identities.append(
                (relative, expected_size, source_identity, target_identity)
            )
        for relative, expected_size, source_identity, target_identity in observed_identities:
            for path, expected_identity, label in (
                (source_home / relative, source_identity, "trusted source"),
                (home / relative, target_identity, "managed"),
            ):
                try:
                    metadata = path.lstat()
                except OSError as exc:
                    raise ValueError(
                        f"{label} file changed during full verification: "
                        f"{relative.as_posix()}"
                    ) from exc
                current_identity = (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                )
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size != expected_size
                    or current_identity != expected_identity
                ):
                    raise ValueError(
                        f"{label} file changed during full verification: "
                        f"{relative.as_posix()}"
                    )
        final_source_bytes, _final_source_hardlinks = validate_tree(
            source_home, inventory, require_owner=False, label="source"
        )
        final_bytes, _final_hardlinks = validate_tree(
            home, inventory, require_owner=True, label="managed"
        )
        if final_source_bytes != source_bytes or final_bytes != checked_bytes:
            raise ValueError("source or managed tree changed during full verification")
        trusted_source_pairwise_verified = True
        content_bytes_read = checked_bytes * 2
    elif source_home is not None:
        raise ValueError("--source-home is only valid with --mode full")

    return {
        "accepted": True,
        "schema": 2,
        "files": len(inventory),
        "logical_bytes": checked_bytes,
        "hardlinked_files": hardlinked_files,
        "mode": mode,
        "manifest_sha256": manifest_sha256,
        "inventory_sha256": inventory_sha256,
        "metadata_identity_verified": True,
        "extra_files_verified": True,
        "content_digests_pinned": False,
        "trusted_source_pairwise_verified": trusted_source_pairwise_verified,
        "content_bytes_read": content_bytes_read,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-inventory-sha256", required=True)
    parser.add_argument("--mode", choices=("fast", "full"), default="fast")
    parser.add_argument(
        "--source-home",
        type=Path,
        help=(
            "distinct operator-attested trusted source used for full pairwise SHA-256 "
            "comparison; this does not pin per-file content digests"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = verify(
            args.home.expanduser(),
            args.manifest.expanduser(),
            args.inventory.expanduser(),
            expected_manifest_sha256=args.expected_manifest_sha256,
            expected_inventory_sha256=args.expected_inventory_sha256,
            mode=args.mode,
            source_home=args.source_home.expanduser() if args.source_home else None,
        )
    except (OSError, ValueError) as exc:
        print(f"ES-DE library verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
