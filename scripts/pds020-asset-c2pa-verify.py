#!/usr/bin/env python3
"""Offline verification of the wallpapers' C2PA provenance receipt."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import struct
import subprocess
import sys
import time
from typing import Any, Sequence
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RECEIPT = ROOT / "components/assets/provenance/c2pa-receipt.json"
MAX_METADATA_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 16 * 1024 * 1024
MAX_TOOL_BYTES = 64 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
REQUIRED_SUCCESS_COUNTS = {
    "timeStamp.validated": 1,
    "signingCredential.trusted": 1,
    "claimSignature.insideValidity": 1,
    "claimSignature.validated": 1,
    "assertion.hashedURI.match": 3,
    "assertion.dataHash.match": 1,
}
REQUIRED_INFORMATIONAL_CODES = ["timeStamp.untrusted"]
C2PATOOL_BINARY_SIZE = 46_628_528
C2PATOOL_BINARY_SHA256 = (
    "3fd3d51f90317f4cffe37b44b7e397392d92dc178dce609ba48e0bcee15420a6"
)
SIGNER_TRUST_LIST_SIZE = 37_911
SIGNER_TRUST_LIST_SHA256 = (
    "75cacc98b79ecac33713c7ecfb58d4a0ef383f3c1f886e7409f9e37e8664aea5"
)
TSA_TRUST_LIST_SIZE = 28_863
TSA_TRUST_LIST_SHA256 = (
    "c688d3555f4a2f1f8d663472bbd37888ff234abdd234c25934c0f9292e4eb5c9"
)


class VerifyError(RuntimeError):
    """Provenance input or claim failed closed."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise VerifyError("provenance JSON contains a duplicate key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> Any:
    raise VerifyError(f"provenance JSON contains non-finite number {value}")


def strict_json(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise VerifyError(f"{label} is invalid UTF-8 JSON") from exc


def read_regular(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VerifyError("required provenance input is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > maximum
        ):
            raise VerifyError("provenance input has unsafe metadata")
        data = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise VerifyError("provenance input changed while being read")
            data.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise VerifyError("provenance input grew while being read")
        return bytes(data)
    finally:
        os.close(descriptor)


def safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise VerifyError(f"{label} is invalid")
    result = PurePosixPath(value)
    if result.is_absolute() or any(part in {"", ".", ".."} for part in result.parts):
        raise VerifyError(f"{label} is unsafe")
    return result


def bounded_file(repository_root: Path, relative: PurePosixPath) -> Path:
    if repository_root.is_symlink() or not repository_root.is_dir():
        raise VerifyError("repository root is missing or unsafe")
    current = repository_root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise VerifyError("provenance input parent is missing or unsafe")
    return current / relative.parts[-1]


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise VerifyError(f"{label} fields are incomplete or unsupported")
    return value


def require_identity(data: bytes, record: dict[str, Any], label: str) -> None:
    size = record.get("size")
    digest = record.get("sha256")
    if type(size) is not int or size <= 0 or len(data) != size:
        raise VerifyError(f"{label} size mismatch")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise VerifyError(f"{label} SHA-256 is invalid")
    if hashlib.sha256(data).hexdigest() != digest:
        raise VerifyError(f"{label} SHA-256 mismatch")


def png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise VerifyError("asset is not a bounded PNG with IHDR first")
    return struct.unpack(">II", data[16:24])


def repository_asset_paths(repository_root: Path) -> set[str]:
    assets_root = repository_root / "assets"
    if assets_root.is_symlink() or not assets_root.is_dir():
        raise VerifyError("asset root is missing or unsafe")
    paths: set[str] = set()
    for current_name, directory_names, file_names in os.walk(
        assets_root, topdown=True, followlinks=False
    ):
        current = Path(current_name)
        for directory_name in directory_names:
            if (current / directory_name).is_symlink():
                raise VerifyError("asset directory symlink is forbidden")
        for file_name in file_names:
            path = current / file_name
            if path.is_symlink() or not path.is_file():
                raise VerifyError("asset entry is not a regular file")
            paths.add(path.relative_to(repository_root).as_posix())
    return paths


def run_bounded(
    command: list[str],
    maximum: int,
    timeout: float,
    label: str,
) -> bytes:
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
        )
    except OSError as exc:
        raise VerifyError(f"{label} could not run") from exc
    assert process.stdout is not None and process.stderr is not None
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    limits = {process.stdout: maximum, process.stderr: MAX_METADATA_BYTES}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VerifyError(f"{label} timed out")
            events = selector.select(min(remaining, 1.0))
            if not events and process.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ)
                    for key in selector.get_map().values()
                ]
            for key, _mask in events:
                stream = key.fileobj
                chunk = os.read(stream.fileno(), 65_536)
                if not chunk:
                    selector.unregister(stream)
                    continue
                streams[stream].extend(chunk)
                if len(streams[stream]) > limits[stream]:
                    raise VerifyError(f"{label} exceeded its output bound")
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (OSError, subprocess.SubprocessError, VerifyError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    stdout = bytes(streams[process.stdout])
    if returncode != 0 or not stdout:
        raise VerifyError(f"{label} failed or returned no output")
    return stdout


def run_json(command: list[str]) -> dict[str, Any]:
    report = strict_json(
        run_bounded(
            command,
            MAX_METADATA_BYTES,
            60,
            "c2patool validation",
        ),
        "c2patool report",
    )
    if not isinstance(report, dict):
        raise VerifyError("c2patool report is not an object")
    return report


def verify_tool_version(c2patool: Path) -> None:
    try:
        version = run_bounded(
            [str(c2patool), "-V"],
            1024,
            10,
            "c2patool version check",
        ).decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise VerifyError("c2patool version is not UTF-8") from exc
    if version.strip() != "c2patool 0.26.60":
        raise VerifyError("c2patool version does not match receipt")


def verify_claim(report: dict[str, Any], asset: dict[str, Any]) -> None:
    active = report.get("active_manifest")
    manifests = report.get("manifests")
    if (
        not isinstance(active, str)
        or active != asset["active_manifest"]
        or not isinstance(manifests, dict)
    ):
        raise VerifyError("active C2PA manifest does not match receipt")
    manifest = manifests.get(active)
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("claim_version")) is not int
        or manifest.get("claim_version") != 2
        or asset["claim_version"] != 2
    ):
        raise VerifyError("active C2PA manifest is missing")

    generator_info = manifest.get("claim_generator_info")
    if not isinstance(generator_info, list) or not any(
        isinstance(info, dict)
        and info.get("name") == asset["claim_generator"]
        and info.get("specVersion") == "2.2.0"
        for info in generator_info
    ):
        raise VerifyError("C2PA claim generator does not match receipt")
    assertions = manifest.get("assertions")
    if not isinstance(assertions, list):
        raise VerifyError("C2PA assertions are missing")
    action_assertions = [
        item for item in assertions if isinstance(item, dict) and item.get("label") == "c2pa.actions.v2"
    ]
    if len(action_assertions) != 1:
        raise VerifyError("C2PA action assertion is absent or ambiguous")
    action_data = action_assertions[0].get("data")
    actions = action_data.get("actions") if isinstance(action_data, dict) else None
    if (
        not isinstance(actions, list)
        or any(not isinstance(item, dict) for item in actions)
        or [item.get("action") for item in actions]
        != [
        "c2pa.created",
        "c2pa.converted",
        "c2pa.watermarked.unbound",
        ]
    ):
        raise VerifyError("C2PA action chain does not match receipt")
    created = actions[0]
    software_agent = created.get("softwareAgent")
    if (
        not isinstance(software_agent, dict)
        or software_agent.get("name") != asset["software_agent"]
        or software_agent.get("version") != asset["software_version"]
        or created.get("digitalSourceType") != asset["digital_source_type"]
        or created.get("when") != asset["created_time"]
    ):
        raise VerifyError("C2PA creation assertion does not match receipt")

    signature = manifest.get("signature_info")
    if not isinstance(signature, dict) or {
        "alg": signature.get("alg"),
        "issuer": signature.get("issuer"),
        "common_name": signature.get("common_name"),
        "time": signature.get("time"),
    } != {
        "alg": asset["signature_algorithm"],
        "issuer": asset["signature_issuer"],
        "common_name": asset["signature_common_name"],
        "time": asset["signature_time"],
    }:
        raise VerifyError("C2PA signature identity does not match receipt")

    results = report.get("validation_results")
    active_results = results.get("activeManifest") if isinstance(results, dict) else None
    if (
        asset["validation_state"] != "Trusted"
        or report.get("validation_state") != "Trusted"
        or report.get("validation_status") is not None
        or not isinstance(active_results, dict)
    ):
        raise VerifyError("C2PA trust state does not match receipt")
    success = active_results.get("success")
    failure = active_results.get("failure", [])
    informational = active_results.get("informational", [])
    if not all(isinstance(items, list) for items in (success, failure, informational)) or any(
        not isinstance(item, dict) or not isinstance(item.get("code"), str)
        for items in (success, failure, informational)
        for item in items
    ):
        raise VerifyError("C2PA validation result lists are invalid")
    success_counts = Counter(item["code"] for item in success)
    failure_codes = sorted(item["code"] for item in failure)
    informational_codes = sorted(item["code"] for item in informational)
    recorded_success = asset["success_code_counts"]
    recorded_failure = asset["failure_codes"]
    recorded_informational = asset["informational_codes"]
    if (
        not isinstance(recorded_success, dict)
        or any(
            not isinstance(code, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count <= 0
            for code, count in recorded_success.items()
        )
        or not isinstance(recorded_failure, list)
        or any(not isinstance(code, str) for code in recorded_failure)
        or not isinstance(recorded_informational, list)
        or any(not isinstance(code, str) for code in recorded_informational)
        or dict(success_counts) != REQUIRED_SUCCESS_COUNTS
        or recorded_success != REQUIRED_SUCCESS_COUNTS
        or failure_codes
        or recorded_failure
        or informational_codes != REQUIRED_INFORMATIONAL_CODES
        or sorted(recorded_informational) != REQUIRED_INFORMATIONAL_CODES
    ):
        raise VerifyError("C2PA validation codes do not match receipt")


def verify(
    receipt_path: Path,
    repository_root: Path,
    c2patool: Path,
    signer_trust_list: Path,
    tsa_trust_list: Path,
) -> dict[str, Any]:
    receipt = strict_json(
        read_regular(receipt_path, MAX_METADATA_BYTES),
        "C2PA receipt",
    )
    receipt = exact_object(receipt, {"schema", "tool", "trust", "rights", "assets"}, "receipt")
    if type(receipt["schema"]) is not int or receipt["schema"] != 1:
        raise VerifyError("C2PA receipt schema is unsupported")

    tool = exact_object(
        receipt["tool"], {"name", "version", "release", "archive", "binary", "settings"}, "tool"
    )
    archive = exact_object(tool["archive"], {"filename", "size", "sha256"}, "tool archive")
    binary_record = exact_object(tool["binary"], {"size", "sha256"}, "tool binary")
    settings_record = exact_object(tool["settings"], {"path", "size", "sha256"}, "tool settings")
    release_url = urlparse(tool["release"] if isinstance(tool["release"], str) else "")
    if (
        tool["name"] != "c2patool"
        or tool["version"] != "0.26.60"
        or release_url.scheme != "https"
        or release_url.hostname != "github.com"
        or tool["release"]
        != "https://github.com/contentauth/c2pa-rs/releases/tag/c2patool-v0.26.60"
        or archive["filename"]
        != "c2patool-v0.26.60-universal-apple-darwin.zip"
        or type(archive["size"]) is not int
        or archive["size"] != 19955218
        or archive["sha256"]
        != "4ca9a8e0937ca28b9090c35e5af1a41c252023799b9bca07f27d974e9ea57beb"
        or type(binary_record["size"]) is not int
        or binary_record["size"] != C2PATOOL_BINARY_SIZE
        or binary_record["sha256"] != C2PATOOL_BINARY_SHA256
    ):
        raise VerifyError("c2patool release identity is not pinned")
    tool_data = read_regular(c2patool, MAX_TOOL_BYTES)
    require_identity(tool_data, binary_record, "c2patool binary")
    if c2patool.is_symlink() or not os.access(c2patool, os.X_OK):
        raise VerifyError("c2patool binary is not executable or safe")
    verify_tool_version(c2patool)

    settings_relative = safe_relative(settings_record["path"], "settings path")
    if settings_relative != PurePosixPath(
        "components/assets/provenance/c2patool-settings.json"
    ):
        raise VerifyError("c2patool settings path is not canonical")
    settings_path = bounded_file(repository_root, settings_relative)
    settings_data = read_regular(settings_path, MAX_METADATA_BYTES)
    require_identity(settings_data, settings_record, "c2patool settings")
    settings = exact_object(
        strict_json(settings_data, "c2patool settings"),
        {"verify"},
        "c2patool settings",
    )
    verify_settings = exact_object(
        settings["verify"],
        {"ocsp_fetch", "remote_manifest_fetch"},
        "c2patool verify settings",
    )
    if any(value is not False for value in verify_settings.values()):
        raise VerifyError("c2patool settings do not enforce offline validation")

    trust = exact_object(
        receipt["trust"],
        {
            "repository",
            "commit",
            "signer_list",
            "tsa_list",
            "timestamp_trust_verified",
            "timestamp_note",
        },
        "trust",
    )
    signer_record = exact_object(trust["signer_list"], {"filename", "size", "sha256"}, "signer list")
    tsa_record = exact_object(trust["tsa_list"], {"filename", "size", "sha256"}, "TSA list")
    trust_url = urlparse(trust["repository"] if isinstance(trust["repository"], str) else "")
    if (
        trust_url.scheme != "https"
        or trust_url.hostname != "github.com"
        or trust["repository"] != "https://github.com/c2pa-org/conformance-public"
        or trust["commit"] != "2466172859fad1215f7aaf7e3768b41a0ac29abc"
        or not COMMIT_RE.fullmatch(trust["commit"])
        or signer_record["filename"] != "C2PA-TRUST-LIST.pem"
        or type(signer_record["size"]) is not int
        or signer_record["size"] != SIGNER_TRUST_LIST_SIZE
        or signer_record["sha256"] != SIGNER_TRUST_LIST_SHA256
        or tsa_record["filename"] != "C2PA-TSA-TRUST-LIST.pem"
        or type(tsa_record["size"]) is not int
        or tsa_record["size"] != TSA_TRUST_LIST_SIZE
        or tsa_record["sha256"] != TSA_TRUST_LIST_SHA256
        or trust["timestamp_trust_verified"] is not False
        or not isinstance(trust["timestamp_note"], str)
        or "timeStamp.untrusted" not in trust["timestamp_note"]
    ):
        raise VerifyError("C2PA trust-list record is invalid")
    signer_data = read_regular(signer_trust_list, MAX_METADATA_BYTES)
    tsa_data = read_regular(tsa_trust_list, MAX_METADATA_BYTES)
    require_identity(signer_data, signer_record, "signer trust list")
    require_identity(tsa_data, tsa_record, "TSA trust list")

    rights = exact_object(
        receipt["rights"],
        {
            "c2pa_proves_content_integrity",
            "c2pa_proves_license",
            "human_author_identified",
            "license_identified",
            "redistribution_permission",
            "release_ready",
        },
        "rights boundary",
    )
    expected_rights = {
        "c2pa_proves_content_integrity": True,
        "c2pa_proves_license": False,
        "human_author_identified": False,
        "license_identified": False,
        "redistribution_permission": False,
        "release_ready": False,
    }
    if any(rights[name] is not expected for name, expected in expected_rights.items()):
        raise VerifyError("C2PA receipt overstates asset rights")

    assets = receipt["assets"]
    if not isinstance(assets, list) or not assets:
        raise VerifyError("C2PA receipt has no assets")
    expected_asset_keys = {
        "path",
        "size",
        "sha256",
        "width",
        "height",
        "active_manifest",
        "claim_version",
        "claim_generator",
        "software_agent",
        "software_version",
        "digital_source_type",
        "created_time",
        "signature_algorithm",
        "signature_issuer",
        "signature_common_name",
        "signature_time",
        "validation_state",
        "success_code_counts",
        "failure_codes",
        "informational_codes",
    }
    recorded_paths: set[str] = set()
    for asset_value in assets:
        asset = exact_object(asset_value, expected_asset_keys, "asset receipt")
        if (
            not isinstance(asset["active_manifest"], str)
            or type(asset["claim_version"]) is not int
            or asset["claim_version"] != 2
            or type(asset["width"]) is not int
            or asset["width"] <= 0
            or type(asset["height"]) is not int
            or asset["height"] <= 0
            or asset["validation_state"] != "Trusted"
        ):
            raise VerifyError("asset receipt trust or numeric fields are invalid")
        relative = safe_relative(asset["path"], "asset path")
        if relative.parts[0] != "assets" or asset["path"] in recorded_paths:
            raise VerifyError("asset path is outside assets or duplicated")
        recorded_paths.add(asset["path"])
        asset_path = bounded_file(repository_root, relative)
        asset_data = read_regular(asset_path, MAX_ASSET_BYTES)
        require_identity(asset_data, asset, "asset")
        width, height = png_dimensions(asset_data)
        if (width, height) != (asset["width"], asset["height"]):
            raise VerifyError("asset dimensions do not match receipt")
        report = run_json(
            [
                str(c2patool),
                "--settings",
                str(settings_path),
                str(asset_path),
                "trust",
                "--trust_anchors",
                str(signer_trust_list),
            ]
        )
        verify_claim(report, asset)
        require_identity(
            read_regular(asset_path, MAX_ASSET_BYTES), asset, "asset after C2PA validation"
        )

    if recorded_paths != repository_asset_paths(repository_root):
        raise VerifyError("C2PA receipt does not exactly cover repository assets")
    require_identity(read_regular(c2patool, MAX_TOOL_BYTES), binary_record, "c2patool after validation")
    require_identity(
        read_regular(signer_trust_list, MAX_METADATA_BYTES),
        signer_record,
        "signer trust list after validation",
    )
    require_identity(
        read_regular(tsa_trust_list, MAX_METADATA_BYTES),
        tsa_record,
        "TSA trust list after validation",
    )
    require_identity(
        read_regular(settings_path, MAX_METADATA_BYTES),
        settings_record,
        "c2patool settings after validation",
    )

    return {
        "schema": 1,
        "read_only": True,
        "network": False,
        "asset_count": len(assets),
        "provenance_verified": True,
        "timestamp_trust_verified": False,
        "redistribution_ready": False,
        "release_ready": False,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--c2patool", type=Path, required=True)
    parser.add_argument("--signer-trust-list", type=Path, required=True)
    parser.add_argument("--tsa-trust-list", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = verify(
            args.receipt,
            args.repository_root,
            args.c2patool,
            args.signer_trust_list,
            args.tsa_trust_list,
        )
    except (OSError, UnicodeError, TypeError, ValueError, VerifyError) as exc:
        print(json.dumps({"provenance_verified": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
