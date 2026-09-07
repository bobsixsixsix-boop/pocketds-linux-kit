#!/usr/bin/env python3
"""Verify the locked host-side fastboot RAM-recovery material without a device."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys


HERE = Path(__file__).resolve().parent
PAYLOAD_SOURCE = HERE.parent / "reproducible-build" / "payload-evidence.py"
PAYLOAD_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_payload_evidence", PAYLOAD_SOURCE
)
if PAYLOAD_SPEC is None or PAYLOAD_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load payload evidence library")
payload = importlib.util.module_from_spec(PAYLOAD_SPEC)
sys.modules[PAYLOAD_SPEC.name] = payload
PAYLOAD_SPEC.loader.exec_module(payload)


PreflightError = payload.ReproductionError
REPORT_SCHEMA = "pocketds.kernel-fastboot-recovery-host-preflight.v1"
LOCK_FIELDS = {
    "schema_version",
    "device_id",
    "machine_model",
    "kernel_release",
    "artifacts",
    "boot_images",
    "rocknix_abl",
    "host_fastboot",
    "runtime_identity",
    "disk_boot_aliases",
}
ARTIFACT_NAMES = {
    "baseline_rollback_boot",
    "candidate_runtime_boot",
    "rocknix_abl_payload",
    "host_fastboot",
}
BOOT_FIELDS = {
    "page_size",
    "container_size",
    "baseline_kernel_size",
    "baseline_image_id_hex",
    "candidate_kernel_size",
    "candidate_image_id_hex",
}
ABL_FIELDS = {
    "release",
    "release_url",
    "release_asset_sha256",
    "linuxloader_commit",
    "partition_prefix_size",
    "required_fastboot_string",
}
FASTBOOT_FIELDS = {"version_line", "temporary_boot_subcommand", "requires_unlocked"}
RUNTIME_FIELDS = {
    "notes_size",
    "baseline_notes_sha256",
    "candidate_notes_sha256",
    "baseline_build_id_hex",
    "candidate_build_id_hex",
}


def _strict(value: object, fields: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PreflightError(f"{name} fields differ")
    return value


def _string(value: object, name: str, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise PreflightError(f"invalid {name}")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PreflightError(f"invalid {name}")
    return value


def _digest(value: object, name: str) -> str:
    text = _string(value, name)
    if payload.HEX64.fullmatch(text) is None:
        raise PreflightError(f"invalid {name}")
    return text


def _hex(value: object, name: str, length: int) -> str:
    text = _string(value, name)
    try:
        decoded = bytes.fromhex(text)
    except ValueError as exc:
        raise PreflightError(f"invalid {name}") from exc
    if text != text.lower() or len(decoded) != length:
        raise PreflightError(f"invalid {name}")
    return text


def validate_lock(raw: dict[str, object]) -> dict[str, object]:
    _strict(raw, LOCK_FIELDS, "recovery lock")
    if raw["schema_version"] != 1:
        raise PreflightError("unsupported recovery lock schema")
    artifacts_raw = _strict(raw["artifacts"], ARTIFACT_NAMES, "recovery artifacts")
    artifacts = {
        name: payload._record(value, name)
        for name, value in sorted(artifacts_raw.items())
    }
    boot_raw = _strict(raw["boot_images"], BOOT_FIELDS, "boot image geometry")
    boot = {
        name: _integer(boot_raw[name], name)
        for name in (
            "page_size",
            "container_size",
            "baseline_kernel_size",
            "candidate_kernel_size",
        )
    }
    boot["baseline_image_id_hex"] = _hex(
        boot_raw["baseline_image_id_hex"], "baseline image ID", 32
    )
    boot["candidate_image_id_hex"] = _hex(
        boot_raw["candidate_image_id_hex"], "candidate image ID", 32
    )
    if boot["page_size"] & (boot["page_size"] - 1) or boot["page_size"] < 512:
        raise PreflightError("invalid boot image page size")
    if any(
        artifacts[name]["size"] != boot["container_size"]
        for name in ("baseline_rollback_boot", "candidate_runtime_boot")
    ):
        raise PreflightError("boot artifact and geometry sizes differ")

    abl_raw = _strict(raw["rocknix_abl"], ABL_FIELDS, "ROCKNIX ABL")
    abl = {
        "release": _string(abl_raw["release"], "ABL release"),
        "release_url": _string(abl_raw["release_url"], "ABL release URL"),
        "release_asset_sha256": _digest(
            abl_raw["release_asset_sha256"], "ABL release asset SHA-256"
        ),
        "linuxloader_commit": _hex(
            abl_raw["linuxloader_commit"], "LinuxLoader commit", 20
        ),
        "partition_prefix_size": _integer(
            abl_raw["partition_prefix_size"], "ABL partition prefix size"
        ),
        "required_fastboot_string": _string(
            abl_raw["required_fastboot_string"], "ABL fastboot evidence string"
        ),
    }
    if (
        abl["release_url"]
        != "https://github.com/ROCKNIX/abl/releases/tag/v" + str(abl["release"])
        or abl["partition_prefix_size"] != artifacts["rocknix_abl_payload"]["size"]
    ):
        raise PreflightError("ROCKNIX ABL release binding differs")

    fastboot_raw = _strict(raw["host_fastboot"], FASTBOOT_FIELDS, "host fastboot")
    fastboot = {
        "version_line": _string(fastboot_raw["version_line"], "fastboot version"),
        "temporary_boot_subcommand": _string(
            fastboot_raw["temporary_boot_subcommand"], "fastboot subcommand"
        ),
        "requires_unlocked": fastboot_raw["requires_unlocked"],
    }
    if (
        fastboot["temporary_boot_subcommand"] != "boot"
        or fastboot["requires_unlocked"] is not True
    ):
        raise PreflightError("fastboot recovery policy differs")

    runtime_raw = _strict(raw["runtime_identity"], RUNTIME_FIELDS, "runtime identity")
    runtime = {
        "notes_size": _integer(runtime_raw["notes_size"], "kernel notes size"),
        "baseline_notes_sha256": _digest(
            runtime_raw["baseline_notes_sha256"], "baseline notes SHA-256"
        ),
        "candidate_notes_sha256": _digest(
            runtime_raw["candidate_notes_sha256"], "candidate notes SHA-256"
        ),
        "baseline_build_id_hex": _hex(
            runtime_raw["baseline_build_id_hex"], "baseline build ID", 20
        ),
        "candidate_build_id_hex": _hex(
            runtime_raw["candidate_build_id_hex"], "candidate build ID", 20
        ),
    }
    if runtime["baseline_build_id_hex"] == runtime["candidate_build_id_hex"]:
        raise PreflightError("runtime build IDs are not distinct")

    aliases_raw = raw["disk_boot_aliases"]
    if not isinstance(aliases_raw, list) or len(aliases_raw) != 3:
        raise PreflightError("disk boot alias set differs")
    aliases = [payload._relative_path(item, "disk boot alias") for item in aliases_raw]
    if len(set(aliases)) != 3:
        raise PreflightError("disk boot aliases are not unique")
    return {
        "schema_version": 1,
        "device_id": _string(raw["device_id"], "device ID"),
        "machine_model": _string(raw["machine_model"], "machine model"),
        "kernel_release": _string(raw["kernel_release"], "kernel release"),
        "artifacts": artifacts,
        "boot_images": boot,
        "rocknix_abl": abl,
        "host_fastboot": fastboot,
        "runtime_identity": runtime,
        "disk_boot_aliases": aliases,
    }


def _read_locked(path: Path, expected: dict[str, object], *, executable: bool = False) -> bytes:
    content, metadata = payload._read_regular(path, maximum=payload.MAX_ARTIFACT_BYTES)
    measured = {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": metadata.st_size,
    }
    if measured != expected:
        raise PreflightError(f"locked recovery input differs: {path.name}")
    mode = stat.S_IMODE(metadata.st_mode)
    if executable and (mode & 0o111) == 0:
        raise PreflightError(f"locked executable is not executable: {path.name}")
    return content


def _parse_boot(content: bytes, expected: dict[str, object], prefix: str) -> dict[str, object]:
    page_size = int(expected["page_size"])
    if len(content) != expected["container_size"] or content[:8] != b"ANDROID!":
        raise PreflightError(f"{prefix} is not the locked Android boot image")
    kernel_size = struct.unpack_from("<I", content, 8)[0]
    ramdisk_size = struct.unpack_from("<I", content, 16)[0]
    second_size = struct.unpack_from("<I", content, 24)[0]
    header_page = struct.unpack_from("<I", content, 36)[0]
    image_id = content[576:608].hex()
    if (
        kernel_size != expected[f"{prefix}_kernel_size"]
        or ramdisk_size != 0
        or second_size != 0
        or header_page != page_size
        or image_id != expected[f"{prefix}_image_id_hex"]
        or len(content) != page_size + ((kernel_size + page_size - 1) // page_size) * page_size
        or any(content[page_size + kernel_size :])
    ):
        raise PreflightError(f"{prefix} boot image geometry differs")
    return {"kernel_size": kernel_size, "page_size": page_size, "image_id_hex": image_id}


def _fastboot_version(path: Path, expected_line: str) -> dict[str, object]:
    before = path.stat()
    try:
        completed = subprocess.run(
            [os.fspath(path), "--version"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError("locked fastboot version probe failed") from exc
    after = path.stat()
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
        raise PreflightError("fastboot identity changed during version probe")
    if len(completed.stdout) > 4096:
        raise PreflightError("fastboot version output is too large")
    try:
        lines = completed.stdout.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise PreflightError("fastboot version output is not UTF-8") from exc
    if completed.returncode != 0 or not lines or lines[0] != expected_line:
        raise PreflightError("fastboot version differs")
    return {"version_line": lines[0], "exit_code": completed.returncode}


def collect(
    lock: dict[str, object], *, artifact_dir: Path, abl_payload: Path, fastboot: Path
) -> dict[str, object]:
    baseline = _read_locked(
        artifact_dir / str(lock["artifacts"]["baseline_rollback_boot"]["filename"]),
        lock["artifacts"]["baseline_rollback_boot"],
    )
    candidate = _read_locked(
        artifact_dir / str(lock["artifacts"]["candidate_runtime_boot"]["filename"]),
        lock["artifacts"]["candidate_runtime_boot"],
    )
    abl = _read_locked(abl_payload, lock["artifacts"]["rocknix_abl_payload"])
    _read_locked(fastboot, lock["artifacts"]["host_fastboot"], executable=True)
    if baseline == candidate:
        raise PreflightError("baseline and candidate recovery images are identical")
    boot = {
        "baseline": _parse_boot(baseline, lock["boot_images"], "baseline"),
        "candidate": _parse_boot(candidate, lock["boot_images"], "candidate"),
    }
    evidence_string = str(lock["rocknix_abl"]["required_fastboot_string"]).encode()
    # The Qualcomm wrapper stores a compressed firmware volume, so the string is
    # not expected in the wrapper itself. Its exact release payload hash is the
    # binding; this field records which extracted-string observation motivated
    # the recovery path without pretending to re-extract it here.
    if evidence_string in abl:
        wrapper_contains_plaintext = True
    else:
        wrapper_contains_plaintext = False
    version = _fastboot_version(fastboot, str(lock["host_fastboot"]["version_line"]))
    return {
        "schema": REPORT_SCHEMA,
        "read_only": True,
        "network": False,
        "device_access": False,
        "device_id": lock["device_id"],
        "kernel_release": lock["kernel_release"],
        "artifacts": {
            name: lock["artifacts"][name]
            for name in (
                "baseline_rollback_boot",
                "candidate_runtime_boot",
                "rocknix_abl_payload",
                "host_fastboot",
            )
        },
        "boot_images": boot,
        "rocknix_abl": {
            "release": lock["rocknix_abl"]["release"],
            "linuxloader_commit": lock["rocknix_abl"]["linuxloader_commit"],
            "official_release_asset_digest_bound": True,
            "fastboot_evidence_string_plaintext_in_wrapper": wrapper_contains_plaintext,
        },
        "host_fastboot": version,
        "recovery_design": {
            "transport": "fastboot-ram-boot",
            "temporary_boot_subcommand": "boot",
            "requires_unlocked": True,
            "writes_partition": False,
            "runtime_proof": "baseline /sys/kernel/notes with candidate image still on disk",
        },
        "gates": {
            "host_recovery_material_preflight_passed": True,
            "live_fastboot_unlock_verified": False,
            "independent_runtime_boot_observed": False,
            "rollback_artifact_prevalidated": False,
            "candidate_install_authorized": False,
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--abl-payload", type=Path, required=True)
    parser.add_argument("--fastboot", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        raw, lock_sha256 = payload.load_lock(HERE / "lock.json")
        report = collect(
            validate_lock(raw),
            artifact_dir=args.artifact_dir,
            abl_payload=args.abl_payload,
            fastboot=args.fastboot,
        )
        report["lock_sha256"] = lock_sha256
    except (OSError, PreflightError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
