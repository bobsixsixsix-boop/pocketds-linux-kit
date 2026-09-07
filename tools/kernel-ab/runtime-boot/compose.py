#!/usr/bin/env python3
"""Compose or verify the locked IFPC + native-lid Android boot image offline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import sys
import zlib


HERE = Path(__file__).resolve().parent
IFPC_SOURCE = HERE.parent / "candidate-build" / "ifpc-evidence.py"
IFPC_SPEC = importlib.util.spec_from_file_location("pocketds_ifpc_evidence", IFPC_SOURCE)
if IFPC_SPEC is None or IFPC_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load IFPC evidence library")
ifpc = importlib.util.module_from_spec(IFPC_SPEC)
sys.modules[IFPC_SPEC.name] = ifpc
IFPC_SPEC.loader.exec_module(ifpc)
payload = ifpc.payload


ComposeError = payload.ReproductionError
REPORT_SCHEMA = "pocketds.kernel-runtime-boot-composition.v1"
LOCK_FIELDS = {
    "schema_version",
    "artifact_id",
    "device_id",
    "kernel_release",
    "source_commit",
    "revert_commit",
    "inputs",
    "package_boot",
    "runtime_boot",
}
INPUT_NAMES = {
    "candidate_package_bootimg",
    "candidate_raw_image",
    "custom_dtb",
    "candidate_report",
    "rollback_preflight_report",
}
BOOT_FIELDS = {
    "page_size",
    "kernel_size",
    "gzip_size",
    "gzip_sha256",
    "dtb_size",
    "dtb_sha256",
    "image_id_hex",
}
RUNTIME_FIELDS = BOOT_FIELDS | {"filename", "sha256", "size"}


def _strict(value: object, fields: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ComposeError(f"{name} fields differ")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ComposeError(f"invalid {name}")
    return value


def _digest(value: object, name: str) -> str:
    text = ifpc._string(value, name)
    if payload.HEX64.fullmatch(text) is None:
        raise ComposeError(f"invalid {name}")
    return text


def _boot_record(value: object, name: str, *, runtime: bool = False) -> dict[str, object]:
    fields = RUNTIME_FIELDS if runtime else BOOT_FIELDS
    raw = _strict(value, fields, name)
    result: dict[str, object] = {
        "page_size": _integer(raw["page_size"], f"{name} page size"),
        "kernel_size": _integer(raw["kernel_size"], f"{name} kernel size"),
        "gzip_size": _integer(raw["gzip_size"], f"{name} gzip size"),
        "gzip_sha256": _digest(raw["gzip_sha256"], f"{name} gzip SHA-256"),
        "dtb_size": _integer(raw["dtb_size"], f"{name} DTB size"),
        "dtb_sha256": _digest(raw["dtb_sha256"], f"{name} DTB SHA-256"),
        "image_id_hex": ifpc._hex(raw["image_id_hex"], f"{name} image ID", length=32),
    }
    if runtime:
        record = payload._record(
            {"filename": raw["filename"], "sha256": raw["sha256"], "size": raw["size"]},
            "runtime boot image",
        )
        result.update(record)
    return result


def validate_lock(raw: dict[str, object]) -> dict[str, object]:
    _strict(raw, LOCK_FIELDS, "runtime boot lock")
    if raw["schema_version"] != 1:
        raise ComposeError("unsupported runtime boot lock schema")
    inputs_raw = _strict(raw["inputs"], INPUT_NAMES, "runtime boot input")
    inputs = {
        name: payload._record(value, name)
        for name, value in sorted(inputs_raw.items())
    }
    if len({str(item["filename"]) for item in inputs.values()}) != len(inputs):
        raise ComposeError("runtime boot input filenames are not unique")
    package_boot = _boot_record(raw["package_boot"], "package boot")
    runtime_boot = _boot_record(raw["runtime_boot"], "runtime boot", runtime=True)
    if package_boot["page_size"] != runtime_boot["page_size"]:
        raise ComposeError("package and runtime page sizes differ")
    if package_boot["gzip_size"] != runtime_boot["gzip_size"] or package_boot["gzip_sha256"] != runtime_boot["gzip_sha256"]:
        raise ComposeError("package and runtime gzip streams are not locked equal")
    if inputs["candidate_package_bootimg"]["size"] != runtime_boot["size"]:
        raise ComposeError("package and runtime container sizes differ")
    if inputs["custom_dtb"]["size"] != runtime_boot["dtb_size"] or inputs["custom_dtb"]["sha256"] != runtime_boot["dtb_sha256"]:
        raise ComposeError("custom DTB input and runtime locks differ")
    if inputs["candidate_raw_image"]["size"] <= 0:
        raise ComposeError("candidate raw Image lock is empty")
    source_commit = ifpc._string(raw["source_commit"], "source commit")
    revert_commit = ifpc._string(raw["revert_commit"], "revert commit")
    if payload.HEX40.fullmatch(source_commit) is None or payload.HEX40.fullmatch(revert_commit) is None:
        raise ComposeError("source or revert commit is invalid")
    return {
        "schema_version": 1,
        "artifact_id": ifpc._string(raw["artifact_id"], "artifact ID"),
        "device_id": ifpc._string(raw["device_id"], "device ID"),
        "kernel_release": ifpc._string(raw["kernel_release"], "kernel release"),
        "source_commit": source_commit,
        "revert_commit": revert_commit,
        "inputs": inputs,
        "package_boot": package_boot,
        "runtime_boot": runtime_boot,
    }


def _read_locked(path: Path, expected: dict[str, object]) -> bytes:
    content, metadata = payload._read_regular(path, maximum=payload.MAX_ARTIFACT_BYTES)
    measured = {"filename": path.name, "sha256": hashlib.sha256(content).hexdigest(), "size": metadata.st_size}
    if measured != expected:
        raise ComposeError(f"locked input differs: {path.name}")
    return content


def _report(content: bytes, name: str) -> dict[str, object]:
    parsed = payload._strict_json(content, name)
    if not isinstance(parsed, dict):
        raise ComposeError(f"{name} root is not an object")
    return parsed


def _require_true(container: object, path: tuple[str, ...], name: str) -> None:
    current = container
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise ComposeError(f"{name} is missing")
        current = current[key]
    if current is not True:
        raise ComposeError(f"{name} is not true")


def _verify_reports(lock: dict[str, object], inputs: dict[str, bytes]) -> dict[str, object]:
    candidate = _report(inputs["candidate_report"], "candidate report")
    rollback = _report(inputs["rollback_preflight_report"], "rollback preflight report")
    if candidate.get("schema") != ifpc.REPORT_SCHEMA:
        raise ComposeError("candidate report schema differs")
    experiment = candidate.get("experiment")
    if (
        not isinstance(experiment, dict)
        or experiment.get("source_commit") != lock["source_commit"]
        or experiment.get("revert_commit") != lock["revert_commit"]
        or experiment.get("package_nevra") != "kernel-" + str(lock["kernel_release"])
    ):
        raise ComposeError("candidate report experiment binding differs")
    for gate in (
        "candidate_built",
        "source_inputs_except_revert_identical",
        "single_variable_payload_delta_proven",
        "candidate_independently_reproduced",
    ):
        _require_true(candidate, ("gates", gate), f"candidate gate {gate}")
    candidate_gates = candidate.get("gates")
    if not isinstance(candidate_gates, dict) or candidate_gates.get("rollback_artifact_prevalidated") is not False or candidate_gates.get("candidate_install_authorized") is not False:
        raise ComposeError("candidate report overclaims readiness")
    if rollback.get("schema") != "pocketds.kernel-rollback-static-preflight.v1":
        raise ComposeError("rollback preflight report schema differs")
    artifact = rollback.get("artifact")
    if (
        not isinstance(artifact, dict)
        or artifact.get("device_id") != lock["device_id"]
        or artifact.get("kernel_release") != lock["kernel_release"]
        or artifact.get("source_commit") != lock["source_commit"]
        or artifact.get("artifact_id") != "pds002-live-baseline-rollback-v2"
    ):
        raise ComposeError("rollback report artifact binding differs")
    for gate in (
        "rollback_artifact_content_locked",
        "baseline_source_to_binary_evidence_bound",
        "signed_and_reproduced_baseline_bound",
        "custom_dtb_source_and_rebuild_evidence_bound",
        "runtime_boot_composition_bound",
        "static_rollback_preflight_passed",
    ):
        _require_true(rollback, ("gates", gate), f"rollback gate {gate}")
    rollback_gates = rollback.get("gates")
    recovery = rollback.get("recovery_validation")
    if (
        not isinstance(rollback_gates, dict)
        or rollback_gates.get("rollback_artifact_prevalidated") is not False
        or rollback_gates.get("candidate_install_authorized") is not False
        or recovery != {
            "status": "not_run",
            "successful_boots": 0,
            "successful_recovery_tests": 0,
            "independent_recovery_path": False,
        }
    ):
        raise ComposeError("rollback report overclaims recovery readiness")
    boot = rollback.get("boot_derivation")
    if not isinstance(boot, dict) or boot.get("dtb_sha256") != lock["runtime_boot"]["dtb_sha256"] or boot.get("dtb_size") != lock["runtime_boot"]["dtb_size"]:
        raise ComposeError("rollback report custom DTB binding differs")
    return {
        "candidate_report_bound": True,
        "rollback_preflight_report_bound": True,
        "rollback_remains_unvalidated": True,
    }


def _parse_package_boot(lock: dict[str, object], boot: bytes, raw: bytes) -> tuple[bytes, bytes]:
    expected = lock["package_boot"]
    parser_expected = {
        "page_size": expected["page_size"],
        "candidate_kernel_size": expected["kernel_size"],
        "candidate_gzip_size": expected["gzip_size"],
        "candidate_gzip_sha256": expected["gzip_sha256"],
        "dtb_size": expected["dtb_size"],
        "dtb_sha256": expected["dtb_sha256"],
        "candidate_id_hex": expected["image_id_hex"],
    }
    parsed = ifpc._parse_boot_image(boot, parser_expected, raw, "candidate")
    page_size = int(expected["page_size"])
    kernel_size = int(expected["kernel_size"])
    blob = boot[page_size : page_size + kernel_size]
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    unpacked = decoder.decompress(blob) + decoder.flush()
    if not decoder.eof or unpacked != raw or not decoder.unused_data:
        raise ComposeError("candidate package boot gzip boundary differs")
    gzip_bytes = blob[: len(blob) - len(decoder.unused_data)]
    if any(boot[page_size + ((kernel_size + page_size - 1) // page_size) * page_size :]):
        raise ComposeError("candidate package boot has non-zero trailing data")
    return bytes(parsed["header"]), gzip_bytes


def _compose(lock: dict[str, object], header_bytes: bytes, gzip_bytes: bytes, raw: bytes, dtb: bytes) -> bytes:
    expected = lock["runtime_boot"]
    page_size = int(expected["page_size"])
    kernel = gzip_bytes + dtb
    if len(kernel) != expected["kernel_size"]:
        raise ComposeError("runtime kernel blob size differs")
    header = bytearray(header_bytes)
    if struct.unpack_from("<I", header, 16)[0] != 0 or struct.unpack_from("<I", header, 24)[0] != 0:
        raise ComposeError("candidate boot unexpectedly has ramdisk or second stage")
    struct.pack_into("<I", header, 8, len(kernel))
    identifier = hashlib.sha1(
        kernel
        + struct.pack("<I", len(kernel))
        + struct.pack("<I", 0)
        + struct.pack("<I", 0)
    ).digest()
    header[576:608] = identifier + bytes(12)
    image = bytes(header) + kernel + bytes((-len(kernel)) % page_size)
    measured = {
        "filename": expected["filename"],
        "sha256": hashlib.sha256(image).hexdigest(),
        "size": len(image),
    }
    if measured != {name: expected[name] for name in ("filename", "sha256", "size")}:
        raise ComposeError("composed runtime boot image differs from lock")
    if identifier.hex() + "00" * 12 != expected["image_id_hex"]:
        raise ComposeError("composed runtime boot image ID differs from lock")
    parser_expected = {
        "page_size": expected["page_size"],
        "runtime_kernel_size": expected["kernel_size"],
        "runtime_gzip_size": expected["gzip_size"],
        "runtime_gzip_sha256": expected["gzip_sha256"],
        "dtb_size": expected["dtb_size"],
        "dtb_sha256": expected["dtb_sha256"],
        "runtime_id_hex": expected["image_id_hex"],
    }
    ifpc._parse_boot_image(image, parser_expected, raw, "runtime")
    return image


def _safe_parent(output: Path) -> tuple[int, str]:
    if output.name in {"", ".", ".."}:
        raise ComposeError("invalid output filename")
    parent = output.parent
    descriptor: int | None = None
    try:
        absolute = Path(os.path.abspath(parent))
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(absolute.anchor, flags)
        for part in absolute.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ComposeError("output parent is unavailable or linked") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
        os.close(descriptor)
        raise ComposeError("output parent is linked, writable, or not a directory")
    return descriptor, output.name


def _write_new(output: Path, content: bytes) -> None:
    parent_fd, name = _safe_parent(output)
    descriptor: int | None = None
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
        created = True
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ComposeError("runtime boot output write stopped")
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size != len(content):
            raise ComposeError("runtime boot output metadata differs")
    except OSError as exc:
        raise ComposeError("cannot create runtime boot output") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if created and sys.exc_info()[0] is not None:
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
        os.close(parent_fd)


def run(lock: dict[str, object], *, artifact_dir: Path, output: Path, verify_existing: bool) -> dict[str, object]:
    if output.name != lock["runtime_boot"]["filename"]:
        raise ComposeError("output filename differs from fixed lock")
    inputs = {
        name: _read_locked(artifact_dir / str(record["filename"]), record)
        for name, record in lock["inputs"].items()
    }
    reports = _verify_reports(lock, inputs)
    header, gzip_bytes = _parse_package_boot(
        lock, inputs["candidate_package_bootimg"], inputs["candidate_raw_image"]
    )
    image = _compose(
        lock,
        header,
        gzip_bytes,
        inputs["candidate_raw_image"],
        inputs["custom_dtb"],
    )
    if verify_existing:
        existing, _metadata = payload._read_regular(output, maximum=payload.MAX_ARTIFACT_BYTES)
        if existing != image:
            raise ComposeError("existing runtime boot image differs from deterministic composition")
        action = "verified_existing"
    else:
        _write_new(output, image)
        action = "created_new"
    return {
        "schema": REPORT_SCHEMA,
        "read_only_inputs": True,
        "network": False,
        "action": action,
        "artifact": {
            "artifact_id": lock["artifact_id"],
            "device_id": lock["device_id"],
            "kernel_release": lock["kernel_release"],
            "source_commit": lock["source_commit"],
            "revert_commit": lock["revert_commit"],
            **{name: lock["runtime_boot"][name] for name in ("filename", "sha256", "size")},
        },
        "reports": reports,
        "composition": {
            "candidate_gzip_preserved": True,
            "source_bound_native_lid_dtb_appended": True,
            "android_v0_size_and_id_recomputed": True,
            "deterministic_output_matches_lock": True,
        },
        "gates": {
            "candidate_runtime_boot_composed": True,
            "rollback_artifact_prevalidated": False,
            "candidate_install_authorized": False,
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify-existing", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        raw, lock_sha256 = payload.load_lock(HERE / "lock.json")
        lock = validate_lock(raw)
        output = args.verify_existing if args.verify_existing is not None else args.output
        report = run(
            lock,
            artifact_dir=args.artifact_dir,
            output=output,
            verify_existing=args.verify_existing is not None,
        )
        report["lock_sha256"] = lock_sha256
    except (OSError, ComposeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
