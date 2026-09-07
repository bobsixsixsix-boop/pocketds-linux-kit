#!/usr/bin/env python3
"""Collect fail-closed, read-only runtime evidence for the PDS-002 A/B plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
from typing import Any
import zlib


SPEC_SCHEMA_VERSION = 3
REPORT_SCHEMA = "pocketds.kernel-ab-runtime-evidence.v3"
FIRMWARE_SET_SCHEMA = "pocketds.gpu-firmware-set.v1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
MAX_SPEC_BYTES = 65_536
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_DECOMPRESSED_KERNEL_BYTES = 128 * 1024 * 1024
ANDROID_BOOT_MAGIC = b"ANDROID!"
ANDROID_BOOT_V0_HEADER_BYTES = 1632
FDT_MAGIC = 0xD00DFEED
SPEC_FIELDS = {
    "schema_version",
    "device_id",
    "expected_compatible",
    "expected_kernel_release",
    "expected_source_commit",
    "kernel_image",
    "kernel_config",
    "kernel_config_mirror",
    "boot_image_versioned",
    "boot_image_aliases",
    "boot_dtb",
    "firmware_files",
}
UNRESOLVED_GATES = [
    "prevalidated_rollback_artifact_missing",
    "runtime_kernel_not_cryptographically_bound_to_source_commit",
]


class EvidenceError(RuntimeError):
    """Evidence is incomplete, unsafe, or does not match the pinned baseline."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_json(content: bytes, where: str) -> object:
    def reject_constant(value: str) -> object:
        raise EvidenceError(f"{where} contains non-finite JSON: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceError(f"{where} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvidenceError(f"{where} is not strict UTF-8 JSON") from exc


def load_spec(path: Path) -> dict[str, object]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise EvidenceError("evidence spec is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_SPEC_BYTES
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise EvidenceError("evidence spec is unsafe, empty, or oversized")
        content = os.read(descriptor, metadata.st_size + 1)
        if len(content) != metadata.st_size:
            raise EvidenceError("evidence spec changed while reading")
    finally:
        os.close(descriptor)
    parsed = _strict_json(content, "evidence spec")
    if not isinstance(parsed, dict):
        raise EvidenceError("evidence spec root must be an object")
    return parsed


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise EvidenceError(f"invalid {name}")
    return value


def _relative_path(value: object, name: str) -> str:
    text = _string(value, name)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or text != str(path):
        raise EvidenceError(f"invalid {name}")
    return text


def validate_spec(raw: dict[str, object]) -> dict[str, object]:
    if set(raw) != SPEC_FIELDS:
        unknown = sorted(set(raw) - SPEC_FIELDS)
        missing = sorted(SPEC_FIELDS - set(raw))
        raise EvidenceError(f"evidence spec fields differ; unknown={unknown}, missing={missing}")
    if raw["schema_version"] != SPEC_SCHEMA_VERSION:
        raise EvidenceError("unsupported evidence spec schema")
    release = _string(raw["expected_kernel_release"], "expected kernel release")
    source_commit = _string(raw["expected_source_commit"], "expected source commit")
    if HEX40.fullmatch(source_commit) is None:
        raise EvidenceError("expected source commit must be a full lowercase commit")
    compatible = raw["expected_compatible"]
    if (
        not isinstance(compatible, list)
        or not compatible
        or any(not isinstance(item, str) or not item for item in compatible)
        or len(set(compatible)) != len(compatible)
    ):
        raise EvidenceError("expected compatible list is invalid")
    firmware = raw["firmware_files"]
    if (
        not isinstance(firmware, list)
        or not firmware
        or len(firmware) > 32
        or any(not isinstance(item, str) for item in firmware)
    ):
        raise EvidenceError("firmware file list is invalid")
    firmware_paths = [
        _relative_path(item, f"firmware_files[{index}]")
        for index, item in enumerate(firmware)
    ]
    if firmware_paths != sorted(set(firmware_paths)):
        raise EvidenceError("firmware file list must be unique and sorted")
    if any(not item.startswith("usr/lib/firmware/qcom/") for item in firmware_paths):
        raise EvidenceError("firmware files must stay below usr/lib/firmware/qcom")
    kernel_paths = {
        key: _relative_path(raw[key], key)
        for key in (
            "kernel_image",
            "kernel_config",
            "kernel_config_mirror",
            "boot_image_versioned",
            "boot_dtb",
        )
    }
    if any(
        release not in kernel_paths[key]
        for key in (
            "kernel_image",
            "kernel_config",
            "kernel_config_mirror",
            "boot_image_versioned",
            "boot_dtb",
        )
    ):
        raise EvidenceError("kernel artifact paths must contain the pinned release")
    aliases = raw["boot_image_aliases"]
    if (
        not isinstance(aliases, list)
        or not aliases
        or len(aliases) > 8
        or any(not isinstance(item, str) for item in aliases)
    ):
        raise EvidenceError("boot image aliases are invalid")
    alias_paths = [
        _relative_path(item, f"boot_image_aliases[{index}]")
        for index, item in enumerate(aliases)
    ]
    if alias_paths != sorted(set(alias_paths)):
        raise EvidenceError("boot image aliases must be unique and sorted")
    if kernel_paths["boot_image_versioned"] in alias_paths:
        raise EvidenceError("versioned boot image must not be repeated as an alias")
    if any(not item.startswith("boot/") for item in alias_paths):
        raise EvidenceError("boot image aliases must stay below boot")
    return {
        "schema_version": SPEC_SCHEMA_VERSION,
        "device_id": _string(raw["device_id"], "device id"),
        "expected_compatible": list(compatible),
        "expected_kernel_release": release,
        "expected_source_commit": source_commit,
        **kernel_paths,
        "boot_image_aliases": alias_paths,
        "firmware_files": firmware_paths,
    }


def _read_artifact(root: Path, relative: str) -> tuple[bytes, dict[str, object]]:
    path = root / relative
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceError(f"artifact is unavailable or linked: {relative}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_ARTIFACT_BYTES
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise EvidenceError(f"artifact is unsafe, empty, or oversized: {relative}")
        remaining = before.st_size
        content = bytearray()
        digest = hashlib.sha256()
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                raise EvidenceError(f"artifact changed while reading: {relative}")
            content.extend(block)
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise EvidenceError(f"artifact grew while reading: {relative}")
        after = os.fstat(descriptor)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after:
            raise EvidenceError(f"artifact identity changed while reading: {relative}")
        return bytes(content), {
            "path": relative,
            "sha256": digest.hexdigest(),
            "size": before.st_size,
        }
    finally:
        os.close(descriptor)


def _compatible(root: Path) -> list[str]:
    content, _metadata = _read_artifact(root, "proc/device-tree/compatible")
    try:
        values = [item.decode("utf-8", errors="strict") for item in content.split(b"\0") if item]
    except UnicodeDecodeError as exc:
        raise EvidenceError("device-tree compatible is not UTF-8") from exc
    if not values:
        raise EvidenceError("device-tree compatible is empty")
    return values


def _padded_size(size: int, page_size: int) -> int:
    return ((size + page_size - 1) // page_size) * page_size


def _parse_boot_image(
    content: bytes,
    raw_kernel: bytes,
    dtb_mirror: bytes | None = None,
) -> dict[str, object]:
    if len(content) < ANDROID_BOOT_V0_HEADER_BYTES or content[:8] != ANDROID_BOOT_MAGIC:
        raise EvidenceError("boot image is not an Android boot image v0")
    try:
        (
            kernel_size,
            _kernel_addr,
            ramdisk_size,
            _ramdisk_addr,
            second_size,
            _second_addr,
            _tags_addr,
            page_size,
            header_version,
            _os_version,
        ) = struct.unpack_from("<10I", content, 8)
    except struct.error as exc:
        raise EvidenceError("boot image v0 header is truncated") from exc
    if page_size != 2048 or header_version != 0:
        raise EvidenceError("boot image page size or header version differs")
    if kernel_size <= 0 or ramdisk_size != 0 or second_size != 0:
        raise EvidenceError("boot image v0 payload layout differs")
    expected_size = page_size + _padded_size(kernel_size, page_size)
    if len(content) != expected_size:
        raise EvidenceError("boot image size does not match its v0 header")
    kernel_blob = content[page_size : page_size + kernel_size]
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        decompressed = decompressor.decompress(
            kernel_blob,
            MAX_DECOMPRESSED_KERNEL_BYTES + 1,
        )
    except zlib.error as exc:
        raise EvidenceError("boot image kernel payload is not a valid gzip stream") from exc
    if len(decompressed) > MAX_DECOMPRESSED_KERNEL_BYTES or decompressor.unconsumed_tail:
        raise EvidenceError("boot image kernel payload exceeds the read-only evidence bound")
    try:
        decompressed += decompressor.flush()
    except zlib.error as exc:
        raise EvidenceError("boot image kernel gzip stream cannot be finalized") from exc
    if not decompressor.eof:
        raise EvidenceError("boot image kernel gzip stream is incomplete")
    if decompressed != raw_kernel:
        raise EvidenceError("boot image gzip kernel differs from the module-tree Image")
    appended_dtb = decompressor.unused_data
    if len(appended_dtb) < 40:
        raise EvidenceError("boot image has no complete appended DTB")
    try:
        (
            magic,
            total_size,
            off_dt_struct,
            off_dt_strings,
            off_mem_rsvmap,
            version,
            last_compatible_version,
            _boot_cpuid_phys,
            size_dt_strings,
            size_dt_struct,
        ) = struct.unpack_from(">10I", appended_dtb, 0)
    except struct.error as exc:
        raise EvidenceError("appended DTB header is truncated") from exc
    if magic != FDT_MAGIC or total_size != len(appended_dtb):
        raise EvidenceError("appended DTB magic or total size differs")
    if dtb_mirror is not None and appended_dtb != dtb_mirror:
        raise EvidenceError("boot image appended DTB differs from its versioned mirror")
    if version < 17 or last_compatible_version > version:
        raise EvidenceError("appended DTB version is unsupported")
    for offset, size in (
        (off_dt_struct, size_dt_struct),
        (off_dt_strings, size_dt_strings),
        (off_mem_rsvmap, 16),
    ):
        if offset < 40 or offset > total_size or size > total_size - offset:
            raise EvidenceError("appended DTB section bounds are invalid")
    return {
        "format": "android-bootimg-v0",
        "page_size": page_size,
        "kernel_blob_size": kernel_size,
        "decompressed_kernel_sha256": hashlib.sha256(decompressed).hexdigest(),
        "decompressed_kernel_size": len(decompressed),
        "appended_dtb_sha256": hashlib.sha256(appended_dtb).hexdigest(),
        "appended_dtb_size": len(appended_dtb),
    }


def collect(
    raw_spec: dict[str, object],
    *,
    root: Path = Path("/"),
    runtime_release: str | None = None,
) -> dict[str, object]:
    spec = validate_spec(raw_spec)
    observed_release = os.uname().release if runtime_release is None else runtime_release
    if observed_release != spec["expected_kernel_release"]:
        raise EvidenceError(
            f"runtime kernel release differs: {observed_release} != {spec['expected_kernel_release']}"
        )
    observed_compatible = _compatible(root)
    if observed_compatible != spec["expected_compatible"]:
        raise EvidenceError("device-tree compatible differs from the pinned Pocket DS identity")

    image_content, image = _read_artifact(root, str(spec["kernel_image"]))
    config_content, config = _read_artifact(root, str(spec["kernel_config"]))
    mirror_content, config_mirror = _read_artifact(root, str(spec["kernel_config_mirror"]))
    if config_content != mirror_content:
        raise EvidenceError("module-tree and boot kernel configs differ")

    versioned_content, versioned_boot_image = _read_artifact(
        root, str(spec["boot_image_versioned"])
    )
    boot_images = [versioned_boot_image]
    for relative in spec["boot_image_aliases"]:
        alias_content, alias_metadata = _read_artifact(root, str(relative))
        if alias_content != versioned_content:
            raise EvidenceError("boot image alias differs from the versioned boot image")
        boot_images.append(alias_metadata)
    boot_dtb_content, boot_dtb = _read_artifact(root, str(spec["boot_dtb"]))
    boot_container = _parse_boot_image(versioned_content, image_content, boot_dtb_content)

    firmware: list[dict[str, object]] = []
    for relative in spec["firmware_files"]:
        _content, metadata = _read_artifact(root, str(relative))
        firmware.append(metadata)
    firmware_identity = {
        "schema": FIRMWARE_SET_SCHEMA,
        "files": firmware,
    }
    firmware_set_sha256 = hashlib.sha256(canonical_bytes(firmware_identity)).hexdigest()
    spec_sha256 = hashlib.sha256(canonical_bytes(spec)).hexdigest()
    gates = {
        "config_mirror_matches": True,
        "boot_image_aliases_match": True,
        "boot_container_matches_raw_kernel": True,
        "boot_container_appended_dtb_valid": True,
        "boot_container_dtb_mirror_matches": True,
        "firmware_set_measured": True,
        "machine_identity_matches": True,
        "prevalidated_rollback_artifact": False,
        "runtime_kernel_release_matches": True,
        "runtime_source_commit_cryptographically_bound": False,
    }
    return {
        "schema": REPORT_SCHEMA,
        "device_id": spec["device_id"],
        "spec_sha256": spec_sha256,
        "expected_source_commit": spec["expected_source_commit"],
        "runtime": {
            "compatible": observed_compatible,
            "kernel_release": observed_release,
        },
        "artifacts": {
            "kernel_image": image,
            "kernel_config": config,
            "kernel_config_mirror": config_mirror,
            "boot_images": boot_images,
            "boot_container": boot_container,
            "boot_dtb": boot_dtb,
            "gpu_firmware": firmware,
            "firmware_set_sha256": firmware_set_sha256,
        },
        "planner_controls": {
            "firmware_set_sha256": firmware_set_sha256,
            "kernel_config_sha256": config["sha256"],
        },
        "gates": gates,
        "unresolved_gates": UNRESOLVED_GATES,
        "production_plan_ready": all(gates.values()),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read and hash the pinned Pocket DS kernel/config/GPU firmware baseline."
    )
    parser.add_argument("--pretty", action="store_true", help="pretty-print one JSON report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    spec_path = Path(__file__).with_name("evidence-spec.json")
    try:
        report = collect(load_spec(spec_path))
    except EvidenceError as exc:
        print(f"kernel-evidence: refused: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.pretty
        else canonical_bytes(report).decode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
