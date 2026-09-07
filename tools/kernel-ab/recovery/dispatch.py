#!/usr/bin/env python3
"""Plan or explicitly dispatch the locked baseline image with fastboot boot."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


HERE = Path(__file__).resolve().parent
PREFLIGHT_SOURCE = HERE / "preflight.py"
PREFLIGHT_SPEC = importlib.util.spec_from_file_location(
    "pocketds_recovery_dispatch_preflight", PREFLIGHT_SOURCE
)
if PREFLIGHT_SPEC is None or PREFLIGHT_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load recovery preflight library")
preflight = importlib.util.module_from_spec(PREFLIGHT_SPEC)
sys.modules[PREFLIGHT_SPEC.name] = preflight
PREFLIGHT_SPEC.loader.exec_module(preflight)
payload = preflight.payload


DispatchError = preflight.PreflightError
REPORT_SCHEMA = "pocketds.kernel-fastboot-recovery-dispatch.v1"
CONFIRMATION = "PDS002-TEMP-BOOT-BASELINE-V1"
MAX_OUTPUT = 64 * 1024
SERIAL = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
UNLOCKED = re.compile(r"(?m)^(?:\(bootloader\) )?unlocked:\s*yes\s*$")


def _command(
    argv: list[str], *, timeout: int, pass_fds: tuple[int, ...] = ()
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            pass_fds=pass_fds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DispatchError("fastboot recovery command failed to run") from exc
    if len(completed.stdout) > MAX_OUTPUT:
        raise DispatchError("fastboot recovery output is too large")
    return completed


@contextmanager
def _locked_boot_descriptor(
    path: Path, expected: dict[str, object]
) -> Iterator[int]:
    """Hold the exact locked boot image open across the fastboot subprocess."""

    if path.name != expected["filename"]:
        raise DispatchError("baseline recovery filename differs")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DispatchError("baseline recovery image is unavailable or linked") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size != expected["size"]
        ):
            raise DispatchError("baseline recovery image metadata differs")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                raise DispatchError("baseline recovery image changed while reading")
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise DispatchError("baseline recovery image grew while reading")
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
        ) or digest.hexdigest() != expected["sha256"]:
            raise DispatchError("baseline recovery image identity differs")
        os.lseek(descriptor, 0, os.SEEK_SET)
        yield descriptor
    finally:
        os.close(descriptor)


def _one_device(fastboot: Path) -> str:
    completed = _command([os.fspath(fastboot), "devices"], timeout=10)
    try:
        lines = completed.stdout.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise DispatchError("fastboot device output is not UTF-8") from exc
    records = [line.split() for line in lines if line.strip()]
    if completed.returncode != 0 or len(records) != 1 or len(records[0]) != 2:
        raise DispatchError("fastboot requires exactly one device")
    serial, state = records[0]
    if SERIAL.fullmatch(serial) is None or state != "fastboot":
        raise DispatchError("fastboot device identity or state differs")
    return serial


def _unlocked(fastboot: Path, serial: str) -> None:
    completed = _command(
        [os.fspath(fastboot), "-s", serial, "getvar", "unlocked"], timeout=10
    )
    try:
        output = completed.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DispatchError("fastboot unlock output is not UTF-8") from exc
    if completed.returncode != 0 or UNLOCKED.search(output) is None:
        raise DispatchError("fastboot device is not explicitly unlocked")


def _safe_parent(path: Path) -> tuple[int, str]:
    if path.name in {"", ".", ".."}:
        raise DispatchError("invalid dispatch report path")
    absolute = Path(os.path.abspath(path.parent))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(absolute.anchor, flags)
        for part in absolute.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise DispatchError("dispatch report parent is unavailable or linked") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        os.close(descriptor)
        raise DispatchError("dispatch report parent is unsafe")
    return descriptor, path.name


def _safe_output(path: Path, report: dict[str, object]) -> None:
    parent_fd, name = _safe_parent(path)
    content = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
        created = True
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise DispatchError("dispatch report write stopped")
            view = view[written:]
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        if stat.S_IMODE(after.st_mode) != 0o600 or after.st_size != len(content):
            raise DispatchError("dispatch report metadata differs")
    except OSError as exc:
        raise DispatchError("cannot create dispatch report") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if created and sys.exc_info()[0] is not None:
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
        os.close(parent_fd)


def plan(
    lock: dict[str, object], *, preflight_report: dict[str, object]
) -> dict[str, object]:
    return {
        "schema": REPORT_SCHEMA,
        "action": "plan_only",
        "device_access": False,
        "preflight": preflight_report["gates"],
        "artifact": lock["artifacts"]["baseline_rollback_boot"],
        "required_confirmation": CONFIRMATION,
        "recovery_design": {
            "transport": "fastboot-ram-boot",
            "subcommand": "boot",
            "requires_exactly_one_device": True,
            "requires_unlocked": True,
            "writes_partition": False,
        },
        "gates": {
            "live_fastboot_unlock_verified": False,
            "temporary_baseline_boot_dispatched": False,
            "independent_runtime_boot_observed": False,
            "rollback_artifact_prevalidated": False,
            "candidate_install_authorized": False,
        },
    }


def execute(
    lock: dict[str, object],
    *,
    fastboot: Path,
    artifact_dir: Path,
    preflight_report: dict[str, object],
) -> dict[str, object]:
    artifact = artifact_dir / str(lock["artifacts"]["baseline_rollback_boot"]["filename"])
    expected = lock["artifacts"]["baseline_rollback_boot"]
    with _locked_boot_descriptor(artifact, expected) as descriptor:
        serial = _one_device(fastboot)
        _unlocked(fastboot, serial)
        # Passing the already verified descriptor prevents a path replacement
        # between host preflight and the fastboot reader opening the image.
        descriptor_path = f"/dev/fd/{descriptor}"
        completed = _command(
            [os.fspath(fastboot), "-s", serial, "boot", descriptor_path],
            timeout=90,
            pass_fds=(descriptor,),
        )
    if completed.returncode != 0:
        raise DispatchError("fastboot temporary baseline boot was rejected")
    return {
        "schema": REPORT_SCHEMA,
        "action": "temporary_baseline_boot_dispatched",
        "device_access": True,
        "preflight": preflight_report["gates"],
        "artifact": lock["artifacts"]["baseline_rollback_boot"],
        "recovery_design": {
            "transport": "fastboot-ram-boot",
            "subcommand": "boot",
            "device_count": 1,
            "unlocked": True,
            "writes_partition": False,
            "device_identifier_recorded": False,
        },
        "gates": {
            "live_fastboot_unlock_verified": True,
            "temporary_baseline_boot_dispatched": True,
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
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        raw, lock_sha256 = payload.load_lock(HERE / "lock.json")
        lock = preflight.validate_lock(raw)
        host = preflight.collect(
            lock,
            artifact_dir=args.artifact_dir,
            abl_payload=args.abl_payload,
            fastboot=args.fastboot,
        )
        if args.execute:
            if args.confirm != CONFIRMATION or args.output is None:
                raise DispatchError("execution needs the exact confirmation and a new output")
            report = execute(
                lock,
                fastboot=args.fastboot,
                artifact_dir=args.artifact_dir,
                preflight_report=host,
            )
            report["lock_sha256"] = lock_sha256
            _safe_output(args.output, report)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            if args.confirm is not None or args.output is not None:
                raise DispatchError("plan mode accepts no confirmation or output")
            report = plan(lock, preflight_report=host)
            report["lock_sha256"] = lock_sha256
            print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    except (OSError, DispatchError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
