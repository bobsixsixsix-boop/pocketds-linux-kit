#!/usr/bin/env python3
"""Attest running-kernel versus on-disk boot identity after supervised recovery."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import uuid


HERE = Path(__file__).resolve().parent
PREFLIGHT_SOURCE = HERE / "preflight.py"
PREFLIGHT_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_preflight", PREFLIGHT_SOURCE
)
if PREFLIGHT_SPEC is None or PREFLIGHT_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load recovery preflight library")
preflight = importlib.util.module_from_spec(PREFLIGHT_SPEC)
sys.modules[PREFLIGHT_SPEC.name] = preflight
PREFLIGHT_SPEC.loader.exec_module(preflight)
payload = preflight.payload


AttestationError = preflight.PreflightError
REPORT_SCHEMA = "pocketds.kernel-fastboot-recovery-runtime-attestation.v2"
MAX_SMALL_FILE = 64 * 1024


def _read_pseudo(path: Path, *, maximum: int = MAX_SMALL_FILE) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AttestationError(f"runtime file is unavailable or linked: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) & 0o022:
            raise AttestationError(f"runtime file type or mode is unsafe: {path.name}")
        content = bytearray()
        while len(content) <= maximum:
            block = os.read(descriptor, min(4096, maximum + 1 - len(content)))
            if not block:
                break
            content.extend(block)
        if not content or len(content) > maximum:
            raise AttestationError(f"runtime file size is unsafe: {path.name}")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
        ):
            raise AttestationError(f"runtime file identity changed: {path.name}")
        return bytes(content), before
    finally:
        os.close(descriptor)


def _text(path: Path, name: str, *, nul: bool = False) -> str:
    content, _metadata = _read_pseudo(path)
    if nul:
        content = content.rstrip(b"\x00")
    try:
        text = content.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise AttestationError(f"{name} is not UTF-8") from exc
    if not text or "\x00" in text:
        raise AttestationError(f"invalid {name}")
    return text


def _runtime_identity(lock: dict[str, object], running: str) -> dict[str, object]:
    runtime = lock["runtime_identity"]
    return {
        "name": running,
        "notes_sha256": runtime[f"{running}_notes_sha256"],
        "build_id_hex": runtime[f"{running}_build_id_hex"],
    }


def _disk_identity(lock: dict[str, object], disk: str) -> dict[str, object]:
    name = "baseline_rollback_boot" if disk == "baseline" else "candidate_runtime_boot"
    record = lock["artifacts"][name]
    return {"name": disk, "sha256": record["sha256"], "size": record["size"]}


def collect(
    lock: dict[str, object],
    *,
    root: Path,
    running: str,
    disk: str,
    max_uptime_seconds: int,
) -> dict[str, object]:
    if running not in {"baseline", "candidate"} or disk not in {"baseline", "candidate"}:
        raise AttestationError("invalid runtime or disk identity selector")
    if max_uptime_seconds < 60 or max_uptime_seconds > 1800:
        raise AttestationError("invalid recovery uptime bound")
    try:
        root_meta = root.lstat()
    except OSError as exc:
        raise AttestationError("runtime root is unavailable") from exc
    if not stat.S_ISDIR(root_meta.st_mode) or stat.S_ISLNK(root_meta.st_mode):
        raise AttestationError("runtime root is not a real directory")

    identity = _runtime_identity(lock, running)
    notes, notes_meta = _read_pseudo(root / "sys/kernel/notes")
    notes_sha256 = hashlib.sha256(notes).hexdigest()
    build_id = bytes.fromhex(str(identity["build_id_hex"]))
    if (
        notes_meta.st_size != lock["runtime_identity"]["notes_size"]
        or len(notes) != lock["runtime_identity"]["notes_size"]
        or notes_sha256 != identity["notes_sha256"]
        or notes.count(build_id) != 1
    ):
        raise AttestationError("running kernel notes identity differs")

    release = _text(root / "proc/sys/kernel/osrelease", "kernel release")
    model = _text(root / "sys/firmware/devicetree/base/model", "machine model", nul=True)
    cmdline = _text(root / "proc/cmdline", "kernel command line")
    tokens = cmdline.split()
    if (
        release != lock["kernel_release"]
        or model != lock["machine_model"]
        or tokens.count("root=PARTLABEL=STORAGE") != 1
        or tokens.count("boot=LABEL=ROCKNIX") != 1
    ):
        raise AttestationError("runtime machine, release, or root binding differs")

    boot_id_text = _text(root / "proc/sys/kernel/random/boot_id", "boot ID")
    try:
        boot_id = str(uuid.UUID(boot_id_text))
    except ValueError as exc:
        raise AttestationError("invalid boot ID") from exc
    uptime_text = _text(root / "proc/uptime", "uptime").split()
    try:
        uptime = float(uptime_text[0])
    except (IndexError, ValueError) as exc:
        raise AttestationError("invalid uptime") from exc
    if not math.isfinite(uptime) or uptime < 0:
        raise AttestationError("invalid uptime")
    observed_at_unix_ns = time.time_ns()
    boot_started_at_unix_ns = observed_at_unix_ns - int(uptime * 1_000_000_000)
    if boot_started_at_unix_ns <= 0:
        raise AttestationError("invalid boot time")
    recent = uptime <= max_uptime_seconds

    disk_identity = _disk_identity(lock, disk)
    aliases: list[dict[str, object]] = []
    for relative in lock["disk_boot_aliases"]:
        path = root / str(relative)
        content, metadata = payload._read_regular(
            path, maximum=payload.MAX_ARTIFACT_BYTES
        )
        measured = {
            "path_id": hashlib.sha256(
                ("pds002-boot-alias-v1\x00" + str(relative)).encode()
            ).hexdigest(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": metadata.st_size,
        }
        if (
            measured["sha256"] != disk_identity["sha256"]
            or measured["size"] != disk_identity["size"]
        ):
            raise AttestationError("on-disk boot alias identity differs")
        aliases.append(measured)

    independent = running == "baseline" and disk == "candidate" and recent
    return {
        "schema": REPORT_SCHEMA,
        "read_only": True,
        "network": False,
        "device_id": lock["device_id"],
        "runtime": {
            "expected_identity": running,
            "kernel_release": release,
            "machine_model": model,
            "notes_sha256": notes_sha256,
            "build_id_hex": identity["build_id_hex"],
            "boot_token_sha256": hashlib.sha256(
                ("pds002-recovery-boot-v1\x00" + boot_id).encode()
            ).hexdigest(),
            "uptime_seconds": uptime,
            "max_uptime_seconds": max_uptime_seconds,
            "observed_at_unix_ns": observed_at_unix_ns,
            "boot_started_at_unix_ns": boot_started_at_unix_ns,
        },
        "disk": {
            "expected_identity": disk,
            "sha256": disk_identity["sha256"],
            "size": disk_identity["size"],
            "aliases": aliases,
        },
        "gates": {
            "machine_and_release_match": True,
            "running_kernel_build_id_matches": True,
            "disk_boot_aliases_match": True,
            "boot_is_within_recovery_window": recent,
            "independent_runtime_boot_observed": independent,
            "rollback_artifact_prevalidated": False,
            "candidate_install_authorized": False,
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--running", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--disk", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--max-uptime-seconds", type=int, default=900)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        raw, lock_sha256 = payload.load_lock(HERE / "lock.json")
        report = collect(
            preflight.validate_lock(raw),
            root=args.root,
            running=args.running,
            disk=args.disk,
            max_uptime_seconds=args.max_uptime_seconds,
        )
        report["lock_sha256"] = lock_sha256
    except (OSError, AttestationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
