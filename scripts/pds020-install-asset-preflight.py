#!/usr/bin/env python3
"""Read-only installer preflight for personal or asset-free repository assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
MAX_METADATA_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 16 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PERSONAL_RECEIPT = PurePosixPath(
    "components/assets/provenance/c2pa-receipt.json"
)
ASSET_MANIFEST = PurePosixPath("components/assets/ASSETS.json")
ASSET_FREE_MANIFEST = {"schema": 1, "profile": "asset-free", "assets": []}
PERSONAL_RIGHTS_BOUNDARY = {
    "c2pa_proves_content_integrity": True,
    "c2pa_proves_license": False,
    "human_author_identified": False,
    "license_identified": False,
    "redistribution_permission": False,
    "release_ready": False,
}


class PreflightError(RuntimeError):
    """The selected asset profile does not match the repository tree."""


def read_regular(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PreflightError("required asset-profile input is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > maximum
        ):
            raise PreflightError("asset-profile input has unsafe metadata")
        data = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise PreflightError("asset-profile input changed while being read")
            data.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise PreflightError("asset-profile input grew while being read")
        return bytes(data)
    finally:
        os.close(descriptor)


def safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PreflightError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PreflightError(f"{label} is unsafe")
    return path


def repository_file(root: Path, relative: PurePosixPath) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise PreflightError("repository root is missing or unsafe")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise PreflightError("asset-profile parent is missing or unsafe")
    return current / relative.parts[-1]


def repository_assets(root: Path, *, allow_missing: bool = False) -> set[str]:
    assets_root = root / "assets"
    try:
        metadata = assets_root.lstat()
    except FileNotFoundError as exc:
        # Git does not preserve empty directories. The caller must still
        # validate the exact asset-free manifest before accepting this tree.
        if allow_missing:
            return set()
        raise PreflightError("assets directory is missing or unsafe") from exc
    except OSError as exc:
        raise PreflightError("assets directory is missing or unsafe") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise PreflightError("assets directory is missing or unsafe")
    paths: set[str] = set()
    try:
        for current_name, directory_names, file_names in os.walk(
            assets_root, topdown=True, followlinks=False
        ):
            current = Path(current_name)
            for name in directory_names:
                path = current / name
                if path.is_symlink() or not path.is_dir():
                    raise PreflightError("asset directory entry is unsafe")
            for name in file_names:
                path = current / name
                if path.is_symlink() or not path.is_file():
                    raise PreflightError("asset file entry is unsafe")
                paths.add(path.relative_to(root).as_posix())
    except OSError as exc:
        raise PreflightError("assets directory could not be enumerated") from exc
    return paths


def json_object(path: Path) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PreflightError("asset-profile metadata contains a duplicate key")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise PreflightError(
            f"asset-profile metadata contains non-finite number {value}"
        )

    try:
        text = read_regular(path, MAX_METADATA_BYTES).decode(
            "utf-8", errors="strict"
        )
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PreflightError("asset-profile metadata is invalid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PreflightError("asset-profile metadata is not an object")
    return value


def personal_preflight(root: Path, actual: set[str]) -> dict[str, Any]:
    receipt = json_object(repository_file(root, PERSONAL_RECEIPT))
    rights = receipt.get("rights")
    if (
        set(receipt) != {"schema", "tool", "trust", "rights", "assets"}
        or type(receipt.get("schema")) is not int
        or receipt.get("schema") != 1
        or not isinstance(rights, dict)
        or set(rights) != set(PERSONAL_RIGHTS_BOUNDARY)
        or any(
            rights[name] is not expected
            for name, expected in PERSONAL_RIGHTS_BOUNDARY.items()
        )
    ):
        raise PreflightError("personal provenance rights boundary is invalid")
    assets = receipt.get("assets")
    if not isinstance(assets, list) or not assets:
        raise PreflightError("personal provenance has no assets")
    recorded: set[str] = set()
    for item in assets:
        if not isinstance(item, dict):
            raise PreflightError("personal asset record is invalid")
        relative = safe_relative(item.get("path"), "personal asset path")
        path = relative.as_posix()
        size = item.get("size")
        expected = item.get("sha256")
        if (
            relative.parts[0] != "assets"
            or path in recorded
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or not isinstance(expected, str)
            or not SHA256_RE.fullmatch(expected)
        ):
            raise PreflightError("personal asset record is incomplete or duplicated")
        data = read_regular(repository_file(root, relative), MAX_ASSET_BYTES)
        if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
            raise PreflightError("personal asset identity does not match provenance")
        recorded.add(path)
    if recorded != actual:
        raise PreflightError("personal provenance does not exactly cover assets")
    return {
        "profile": "personal-assets",
        "asset_count": len(recorded),
        "install_wallpapers": True,
        "public_asset_layer_ready": False,
    }


def asset_free_preflight(root: Path, actual: set[str]) -> dict[str, Any]:
    manifest = json_object(repository_file(root, ASSET_MANIFEST))
    if (
        type(manifest.get("schema")) is not int
        or manifest != ASSET_FREE_MANIFEST
        or actual
    ):
        raise PreflightError("asset-free profile is not exactly empty")
    return {
        "profile": "asset-free",
        "asset_count": 0,
        "install_wallpapers": False,
        "public_asset_layer_ready": True,
    }


def preflight(root: Path, profile: str) -> dict[str, Any]:
    allow_missing = profile == "asset-free"
    actual = repository_assets(root, allow_missing=allow_missing)
    if profile == "personal-assets":
        result = personal_preflight(root, actual)
    elif profile == "asset-free":
        result = asset_free_preflight(root, actual)
    else:
        raise PreflightError("asset profile is unsupported")
    if repository_assets(root, allow_missing=allow_missing) != actual:
        raise PreflightError("asset tree changed during profile preflight")
    return {
        "schema": 1,
        "read_only": True,
        "network": False,
        "asset_profile_ready": True,
        "release_ready": False,
        **result,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", choices=("personal-assets", "asset-free"), required=True
    )
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = preflight(args.repository_root, args.profile)
    except (OSError, PreflightError) as exc:
        print(json.dumps({"profile_ready": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
