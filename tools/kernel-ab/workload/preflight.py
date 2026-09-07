#!/usr/bin/env python3
"""Preflight the fixed offline PDS-002 WebGL2 compositor workload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "manifest.json"
SCHEMA = "pocketds.kernel-ab-workload-preflight.v1"
MAX_MANIFEST = 64 * 1024
MAX_ASSET = 1024 * 1024
MAX_BROWSER = 512 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_FIELDS = {
    "schema_version", "workload_id", "duration_seconds", "network_content",
    "asset", "browser", "arguments", "renderer_contract",
}
ARTIFACT_FIELDS = {"path", "sha256", "size"}
EXPECTED_BROWSER_PATHS = {
    "wrapper": "/usr/local/bin/pocketds-chromium-v4l2",
    "binary": "/opt/pocketds-chromium-v4l2-151.0.7922.137/usr/lib/chromium/chromium",
}
EXPECTED_ARGUMENTS = [
    "--app={asset_uri}",
    "--user-data-dir={private_profile}",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-translate",
    "--disable-component-update",
    "--disable-background-networking",
    "--disable-domain-reliability",
    "--disable-client-side-phishing-detection",
    "--metrics-recording-only",
    "--disable-features=OptimizationHints,MediaRouter",
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--disable-quic",
    "--disable-breakpad",
    "--disable-default-apps",
    "--disable-extensions",
    "--window-position=0,0",
    "--window-size=1280,720",
    "--start-fullscreen",
]


class PreflightError(RuntimeError):
    """The locked workload or its runtime content is incomplete or unsafe."""


def strict_json(content: bytes, where: str) -> object:
    def reject_constant(value: str) -> object:
        raise PreflightError(f"{where} contains non-finite JSON: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise PreflightError(f"{where} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PreflightError(f"{where} is not strict UTF-8 JSON") from exc


def read_artifact(
    path: Path, maximum: int, label: str, *, executable: bool = False
) -> tuple[bytes, dict[str, object]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PreflightError(f"{label} is unavailable or linked") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > maximum
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or (executable and not stat.S_IMODE(before.st_mode) & 0o111)
        ):
            raise PreflightError(f"{label} metadata is unsafe")
        remaining = before.st_size
        content = bytearray()
        digest = hashlib.sha256()
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                raise PreflightError(f"{label} changed while reading")
            content.extend(block)
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise PreflightError(f"{label} grew while reading")
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
            raise PreflightError(f"{label} identity changed")
        return bytes(content), {"sha256": digest.hexdigest(), "size": before.st_size}
    finally:
        os.close(descriptor)


def exact(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PreflightError(f"{label} fields differ")
    return value


def validate_identity(value: object, label: str) -> dict[str, object]:
    item = exact(value, ARTIFACT_FIELDS, label)
    if (
        not isinstance(item["path"], str)
        or not PurePosixPath(item["path"]).is_absolute()
        or not isinstance(item["sha256"], str)
        or HEX64.fullmatch(item["sha256"]) is None
        or not isinstance(item["size"], int)
        or isinstance(item["size"], bool)
        or item["size"] <= 0
    ):
        raise PreflightError(f"{label} identity is invalid")
    return item


def load_manifest(path: Path = MANIFEST_PATH) -> tuple[dict[str, Any], str]:
    content, _identity = read_artifact(path, MAX_MANIFEST, "workload manifest")
    parsed = strict_json(content, "workload manifest")
    manifest = exact(parsed, MANIFEST_FIELDS, "workload manifest")
    asset = exact(manifest["asset"], {"filename", "sha256", "size"}, "workload asset")
    browser = exact(manifest["browser"], {"wrapper", "binary"}, "workload browser")
    renderer = exact(
        manifest["renderer_contract"],
        {"api", "webgl_context_loss_is_failure", "hidden_document_is_failure", "animated_compositor_layers"},
        "renderer contract",
    )
    if (
        manifest["schema_version"] != 1
        or manifest["workload_id"] != "pds002-webgl2-compositor-v1"
        or manifest["duration_seconds"] != 2700
        or manifest["network_content"] is not False
        or asset["filename"] != "webgl-compositor.html"
        or not isinstance(asset["sha256"], str)
        or HEX64.fullmatch(asset["sha256"]) is None
        or not isinstance(asset["size"], int)
        or isinstance(asset["size"], bool)
        or not 1 <= asset["size"] <= MAX_ASSET
        or renderer != {
            "api": "WebGL2",
            "webgl_context_loss_is_failure": True,
            "hidden_document_is_failure": True,
            "animated_compositor_layers": 48,
        }
    ):
        raise PreflightError("workload manifest contract differs")
    validate_identity(browser["wrapper"], "browser wrapper")
    validate_identity(browser["binary"], "browser binary")
    if any(browser[name]["path"] != path for name, path in EXPECTED_BROWSER_PATHS.items()):
        raise PreflightError("browser runtime path differs")
    arguments = manifest["arguments"]
    if arguments != EXPECTED_ARGUMENTS:
        raise PreflightError("workload argument contract differs")
    return manifest, hashlib.sha256(content).hexdigest()


def collect(manifest: dict[str, Any], manifest_sha256: str, root: Path = Path("/")) -> dict[str, object]:
    asset_content, asset_identity = read_artifact(HERE / manifest["asset"]["filename"], MAX_ASSET, "workload asset")
    if b"getContext(\"webgl2\"" not in asset_content or b"requestAnimationFrame" not in asset_content:
        raise PreflightError("workload asset semantics differ")
    if asset_identity != {key: manifest["asset"][key] for key in ("sha256", "size")}:
        raise PreflightError("workload asset identity differs")
    measured_browser: dict[str, dict[str, object]] = {}
    for name in ("wrapper", "binary"):
        expected = manifest["browser"][name]
        relative = PurePosixPath(expected["path"]).relative_to("/")
        _content, identity = read_artifact(
            root / Path(*relative.parts), MAX_BROWSER, f"browser {name}", executable=True
        )
        if identity != {key: expected[key] for key in ("sha256", "size")}:
            raise PreflightError(f"browser {name} identity differs")
        measured_browser[name] = identity
    return {
        "schema": SCHEMA,
        "read_only": True,
        "network": False,
        "workload": {
            "id": manifest["workload_id"],
            "sha256": manifest_sha256,
            "duration_seconds": manifest["duration_seconds"],
            "asset": asset_identity,
            "browser": measured_browser,
            "profile_specific_content": False,
        },
        "gates": {
            "manifest_and_asset_locked": True,
            "browser_runtime_locked": True,
            "offline_content_only": True,
            "formal_run_ready": True,
            "workload_execution_started": False,
            "candidate_install_authorized": False,
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        manifest, manifest_sha256 = load_manifest()
        report = collect(manifest, manifest_sha256)
    except (OSError, PreflightError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
