#!/usr/bin/env python3
"""Run a confirmation-gated payload-only RPM cycle in a disposable mock root."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRANSACTION_LOCK = ROOT / "packaging/pocketds-userspace/transaction-lock.json"
DEFAULT_BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.json"
REPRO_PATH = ROOT / "scripts/pds020-userspace-rpm-repro.py"
MAX_LOCK_BYTES = 128 * 1024
MAX_RPM_BYTES = 16 * 1024 * 1024
MAX_TOOL_OUTPUT = 2 * 1024 * 1024
CONFIRMATION = "RUN ISOLATED PDS020 RPM TRANSACTION"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UNIQUEEXT_RE = re.compile(r"^pds020-userspace-txn-[1-9][0-9]{0,9}$")
MARKER_RE = re.compile(r"^PDS020_(?P<key>[A-Z0-9_]+)=(?P<value>[^\r\n]*)$")
MARKER_KEYS = {
    "STAGE",
    "NEVRA",
    "PACKAGE_COUNT",
    "FILE_COUNT",
    "SUDOERS",
    "FAN_SHA256",
    "FAN_OWNER",
}
INSTALL_VENDOR = "rpm -ivh --nodeps --noscripts --notriggers /tmp/vendor.rpm"
UPGRADE_HARDENED = "rpm -Uvh --nodeps --noscripts --notriggers /tmp/hardened.rpm"
ROLLBACK_VENDOR = (
    "rpm -Uvh --oldpackage --nodeps --noscripts --notriggers /tmp/vendor.rpm"
)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


repro = load_module("pds020_userspace_transaction_repro", REPRO_PATH)


class TransactionError(RuntimeError):
    """The isolated RPM transaction or its evidence failed closed."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise TransactionError("transaction lock contains a duplicate key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise TransactionError(f"transaction lock contains non-finite number {value}")


def strict_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise TransactionError(f"{label} is invalid UTF-8 JSON") from exc


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise TransactionError(f"{label} fields are incomplete or unsupported")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_record(value: Any, label: str) -> dict[str, Any]:
    record = exact_object(
        value, {"filename", "size", "sha256", "payload_sha256"}, label
    )
    if (
        not isinstance(record["filename"], str)
        or Path(record["filename"]).name != record["filename"]
        or type(record["size"]) is not int
        or record["size"] <= 0
        or not isinstance(record["sha256"], str)
        or not SHA256_RE.fullmatch(record["sha256"])
        or not isinstance(record["payload_sha256"], str)
        or not SHA256_RE.fullmatch(record["payload_sha256"])
    ):
        raise TransactionError(f"{label} is invalid")
    return record


def load_lock(path: Path) -> dict[str, Any]:
    lock = exact_object(
        strict_json(repro.read_regular(path, MAX_LOCK_BYTES), "transaction lock"),
        {"schema", "builder", "vendor", "hardened", "transaction"},
        "transaction lock",
    )
    if type(lock["schema"]) is not int or lock["schema"] != 1:
        raise TransactionError("transaction lock schema is unsupported")
    builder = exact_object(
        lock["builder"],
        {"distro", "architecture", "mock_config", "mock_nvr", "offline"},
        "transaction builder",
    )
    if (
        builder["distro"] != "fedora-44"
        or builder["architecture"] != "aarch64"
        or builder["mock_config"] != "fedora-44-aarch64"
        or builder["mock_nvr"] != "mock-6.8-1.fc44.noarch"
        or builder["offline"] is not True
    ):
        raise TransactionError("transaction builder is invalid")
    vendor = exact_object(
        lock["vendor"],
        {
            "independent_result_count",
            "artifact",
            "header",
            "installed_file_count",
            "sudoers_sha256",
            "fan_controller_sha256",
        },
        "vendor transaction input",
    )
    artifact_record(vendor["artifact"], "vendor artifact")
    header = exact_object(
        vendor["header"],
        {"name", "version", "release", "arch", "buildhost", "buildtime"},
        "vendor header",
    )
    if (
        vendor["independent_result_count"] != 2
        or header["name"] != "pocketds-userspace"
        or header["version"] != "20260507"
        or header["release"] != "20260730174706.fc44"
        or header["arch"] != "noarch"
        or header["buildhost"] != "pocketds-build.invalid"
        or type(header["buildtime"]) is not int
        or header["buildtime"] <= 0
        or type(vendor["installed_file_count"]) is not int
        or vendor["installed_file_count"] <= 0
        or any(
            not isinstance(vendor[key], str) or not SHA256_RE.fullmatch(vendor[key])
            for key in ("sudoers_sha256", "fan_controller_sha256")
        )
    ):
        raise TransactionError("vendor transaction input is invalid")
    hardened = exact_object(
        lock["hardened"], {"build_lock_sha256"}, "hardened transaction input"
    )
    if (
        not isinstance(hardened["build_lock_sha256"], str)
        or not SHA256_RE.fullmatch(hardened["build_lock_sha256"])
    ):
        raise TransactionError("hardened transaction input is invalid")
    transaction = exact_object(
        lock["transaction"],
        {
            "uniqueext_prefix",
            "stages",
            "rpm_flags",
            "dependency_resolution_tested",
            "scriptlets_executed",
            "triggers_executed",
            "payload_ownership_only",
        },
        "transaction policy",
    )
    if any(
        type(transaction[key]) is not bool
        for key in (
            "dependency_resolution_tested",
            "scriptlets_executed",
            "triggers_executed",
            "payload_ownership_only",
        )
    ) or transaction != {
        "uniqueext_prefix": "pds020-userspace-txn-",
        "stages": [
            "vendor-install",
            "hardened-upgrade",
            "vendor-rollback",
            "hardened-reupgrade",
        ],
        "rpm_flags": ["--nodeps", "--noscripts", "--notriggers"],
        "dependency_resolution_tested": False,
        "scriptlets_executed": False,
        "triggers_executed": False,
        "payload_ownership_only": True,
    }:
        raise TransactionError("transaction policy is invalid")
    return lock


def safe_executable(path: Path, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise TransactionError(f"{label} is missing or unsafe") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid not in {0, os.getuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise TransactionError(f"{label} is missing or unsafe")


def validate_output(path: Path) -> None:
    try:
        parent = os.lstat(path.parent)
    except OSError as exc:
        raise TransactionError("output parent is missing or unsafe") from exc
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
        or path.exists()
        or path.is_symlink()
    ):
        raise TransactionError("output parent must be current-user-owned mode-0700")


def run_bounded(command: list[str], maximum: int, label: str, timeout: int) -> bytes:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TransactionError(f"{label} could not run") from exc
    if result.returncode != 0 or len(result.stdout) > maximum:
        raise TransactionError(f"{label} failed or exceeded its output bound")
    return result.stdout


def inspect_command(stage: str) -> str:
    if stage not in {
        "vendor-install",
        "hardened-upgrade",
        "vendor-rollback",
        "hardened-reupgrade",
    }:
        raise TransactionError("unknown transaction inspection stage")
    return (
        "set -eu; "
        "nevra=$(rpm -q --qf '%{NEVRA}\\n' pocketds-userspace); "
        "package_count=$(rpm -qa 'pocketds-userspace*' | wc -l | tr -d ' '); "
        "file_count=$(rpm -ql pocketds-userspace | wc -l | tr -d ' '); "
        "if test -e /etc/sudoers.d/10-wheel-nopasswd; then "
        "sudoers=$(sha256sum /etc/sudoers.d/10-wheel-nopasswd | cut -d ' ' -f 1); "
        "else sudoers=absent; fi; "
        "fan=$(sha256sum /usr/bin/pocketds-fancontrol | cut -d ' ' -f 1); "
        "fan_owner=$(rpm -qf --qf '%{NEVRA}\\n' /usr/bin/pocketds-fancontrol); "
        f"printf 'PDS020_STAGE={stage}\\n'; "
        "printf 'PDS020_NEVRA=%s\\n' \"$nevra\"; "
        "printf 'PDS020_PACKAGE_COUNT=%s\\n' \"$package_count\"; "
        "printf 'PDS020_FILE_COUNT=%s\\n' \"$file_count\"; "
        "printf 'PDS020_SUDOERS=%s\\n' \"$sudoers\"; "
        "printf 'PDS020_FAN_SHA256=%s\\n' \"$fan\"; "
        "printf 'PDS020_FAN_OWNER=%s\\n' \"$fan_owner\""
    )


def parse_inspection(data: bytes, expected_stage: str) -> dict[str, Any]:
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise TransactionError("mock inspection output is not UTF-8") from exc
    fields: dict[str, str] = {}
    for line in lines:
        match = MARKER_RE.fullmatch(line)
        if match is None:
            continue
        key = match["key"]
        if key in fields:
            raise TransactionError("mock inspection contains a duplicate marker")
        fields[key] = match["value"]
    if set(fields) != MARKER_KEYS or fields["STAGE"] != expected_stage:
        raise TransactionError("mock inspection markers are incomplete or mismatched")
    try:
        package_count = int(fields["PACKAGE_COUNT"])
        file_count = int(fields["FILE_COUNT"])
    except ValueError as exc:
        raise TransactionError("mock inspection count is invalid") from exc
    if package_count < 0 or file_count < 0:
        raise TransactionError("mock inspection count is invalid")
    return {
        "stage": fields["STAGE"],
        "nevra": fields["NEVRA"],
        "package_count": package_count,
        "file_count": file_count,
        "sudoers": fields["SUDOERS"],
        "fan_sha256": fields["FAN_SHA256"],
        "fan_owner": fields["FAN_OWNER"],
    }


def verify_artifacts(
    lock: dict[str, Any],
    transaction_lock_path: Path,
    build_lock_path: Path,
    vendor_path: Path,
    hardened_path: Path,
    rpm: Path,
) -> tuple[dict[str, Any], bytes, bytes, bytes, bytes]:
    transaction_lock_bytes = repro.read_regular(transaction_lock_path, MAX_LOCK_BYTES)
    build_lock_bytes = repro.read_regular(build_lock_path, repro.MAX_LOCK_BYTES)
    if sha256(build_lock_bytes) != lock["hardened"]["build_lock_sha256"]:
        raise TransactionError("hardened build lock differs from transaction lock")
    build_lock = repro.load_lock(build_lock_path)
    vendor_record = artifact_record(lock["vendor"]["artifact"], "vendor artifact")
    hardened_record = repro.artifact_record(
        build_lock["package"]["binary_result"], "hardened artifact"
    )
    if vendor_path.name != vendor_record["filename"]:
        raise TransactionError("vendor artifact filename differs from transaction lock")
    if hardened_path.name != hardened_record["filename"]:
        raise TransactionError("hardened artifact filename differs from build lock")
    vendor_bytes = repro.read_regular(vendor_path, MAX_RPM_BYTES)
    hardened_bytes = repro.read_regular(hardened_path, MAX_RPM_BYTES)
    for data, record, label in (
        (vendor_bytes, vendor_record, "vendor artifact"),
        (hardened_bytes, hardened_record, "hardened artifact"),
    ):
        if len(data) != record["size"] or sha256(data) != record["sha256"]:
            raise TransactionError(f"{label} differs from its lock")
    vendor_header = repro.query_header(rpm, vendor_path)
    expected_vendor_header = {
        **lock["vendor"]["header"],
        "payload_sha256": vendor_record["payload_sha256"],
    }
    if vendor_header != expected_vendor_header:
        raise TransactionError("vendor RPM header differs from transaction lock")
    hardened_header = repro.query_header(rpm, hardened_path)
    package = build_lock["package"]
    expected_hardened_header = {
        "name": package["name"],
        "version": package["version"],
        "release": package["release"],
        "arch": package["arch"],
        "buildhost": build_lock["builder"]["macros"]["_buildhost"],
        "buildtime": build_lock["builder"]["buildtime"],
        "payload_sha256": hardened_record["payload_sha256"],
    }
    if hardened_header != expected_hardened_header:
        raise TransactionError("hardened RPM header differs from build lock")
    return build_lock, transaction_lock_bytes, build_lock_bytes, vendor_bytes, hardened_bytes


def expected_inspection(
    stage: str, lock: dict[str, Any], build_lock: dict[str, Any]
) -> dict[str, Any]:
    vendor_stage = stage in {"vendor-install", "vendor-rollback"}
    if vendor_stage:
        header = lock["vendor"]["header"]
        nevra = (
            f"{header['name']}-{header['version']}-{header['release']}.{header['arch']}"
        )
        file_count = lock["vendor"]["installed_file_count"]
        sudoers = lock["vendor"]["sudoers_sha256"]
        fan = lock["vendor"]["fan_controller_sha256"]
    else:
        package = build_lock["package"]
        nevra = (
            f"{package['name']}-{package['version']}-{package['release']}.{package['arch']}"
        )
        file_count = build_lock["payload_audit"]["file_count"]
        sudoers = "absent"
        fan = build_lock["payload_audit"]["fan_controller_sha256"]
    return {
        "stage": stage,
        "nevra": nevra,
        "package_count": 1,
        "file_count": file_count,
        "sudoers": sudoers,
        "fan_sha256": fan,
        "fan_owner": nevra,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    data = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def execute_transaction(
    lock: dict[str, Any],
    build_lock: dict[str, Any],
    vendor_path: Path,
    hardened_path: Path,
    sudo: Path,
    mock_tool: Path,
) -> list[dict[str, Any]]:
    uniqueext = f"{lock['transaction']['uniqueext_prefix']}{os.getpid()}"
    if not UNIQUEEXT_RE.fullmatch(uniqueext):  # pragma: no cover
        raise TransactionError("generated mock uniqueext is unsafe")
    base = [
        str(sudo),
        "-n",
        str(mock_tool),
        "-r",
        lock["builder"]["mock_config"],
        "--uniqueext",
        uniqueext,
        "--offline",
    ]
    run_bounded([str(sudo), "-n", "true"], 4096, "non-interactive sudo preflight", 30)
    initialized = False
    primary_error: Exception | None = None
    observations: list[dict[str, Any]] = []
    operations = (
        ("vendor-install", INSTALL_VENDOR),
        ("hardened-upgrade", UPGRADE_HARDENED),
        ("vendor-rollback", ROLLBACK_VENDOR),
        ("hardened-reupgrade", UPGRADE_HARDENED),
    )
    try:
        run_bounded(base + ["--clean"], MAX_TOOL_OUTPUT, "mock pre-clean", 120)
        run_bounded(base + ["--init"], MAX_TOOL_OUTPUT, "mock initialization", 300)
        initialized = True
        run_bounded(
            base + ["--copyin", str(vendor_path), "/tmp/vendor.rpm"],
            MAX_TOOL_OUTPUT,
            "vendor copy-in",
            120,
        )
        run_bounded(
            base + ["--copyin", str(hardened_path), "/tmp/hardened.rpm"],
            MAX_TOOL_OUTPUT,
            "hardened copy-in",
            120,
        )
        for stage, command in operations:
            run_bounded(
                base + ["--chroot", command],
                MAX_TOOL_OUTPUT,
                f"{stage} transaction",
                120,
            )
            inspected = parse_inspection(
                run_bounded(
                    base + ["--chroot", inspect_command(stage)],
                    MAX_TOOL_OUTPUT,
                    f"{stage} inspection",
                    120,
                ),
                stage,
            )
            if inspected != expected_inspection(stage, lock, build_lock):
                raise TransactionError(f"{stage} inspection differs from the lock")
            observations.append(inspected)
    except Exception as exc:
        primary_error = exc
    finally:
        if initialized or primary_error is not None:
            try:
                run_bounded(base + ["--clean"], MAX_TOOL_OUTPUT, "mock cleanup", 120)
            except Exception as cleanup_exc:
                if primary_error is None:
                    primary_error = cleanup_exc
    if primary_error is not None:
        raise primary_error
    return observations


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--vendor-rpm", type=Path)
    parser.add_argument("--hardened-rpm", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--transaction-lock", type=Path, default=DEFAULT_TRANSACTION_LOCK)
    parser.add_argument("--build-lock", type=Path, default=DEFAULT_BUILD_LOCK)
    parser.add_argument("--sudo", type=Path, default=Path("/usr/bin/sudo"))
    parser.add_argument("--mock", type=Path, default=Path("/usr/libexec/mock/mock"))
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if not args.execute:
        if any(
            value is not None
            for value in (args.confirm, args.vendor_rpm, args.hardened_rpm, args.output)
        ):
            print(json.dumps({"planned": False, "error": "plan accepts no execution arguments"}))
            return 2
        try:
            lock = load_lock(args.transaction_lock)
        except (OSError, ValueError, TransactionError, repro.ReproError) as exc:
            print(json.dumps({"planned": False, "error": str(exc)}, sort_keys=True))
            return 1
        print(
            json.dumps(
                {
                    "schema": 1,
                    "planned": True,
                    "executed": False,
                    "offline": True,
                    "mock_config": lock["builder"]["mock_config"],
                    "stages": lock["transaction"]["stages"],
                    "payload_ownership_only": True,
                    "dependency_resolution_tested": False,
                    "scriptlets_executed": False,
                    "device_root_touched": False,
                    "confirmation_required": CONFIRMATION,
                    "release_ready": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if (
        args.confirm != CONFIRMATION
        or args.vendor_rpm is None
        or args.hardened_rpm is None
        or args.output is None
    ):
        print(json.dumps({"completed": False, "error": "execution gate is incomplete"}))
        return 2
    try:
        validate_output(args.output)
        lock = load_lock(args.transaction_lock)
        safe_executable(args.sudo, "sudo executable")
        safe_executable(args.mock, "mock executable")
        build_lock, transaction_lock_bytes, build_lock_bytes, vendor_bytes, hardened_bytes = (
            verify_artifacts(
                lock,
                args.transaction_lock,
                args.build_lock,
                args.vendor_rpm,
                args.hardened_rpm,
                args.rpm,
            )
        )
        observations = execute_transaction(
            lock,
            build_lock,
            args.vendor_rpm,
            args.hardened_rpm,
            args.sudo,
            args.mock,
        )
        if (
            repro.read_regular(args.transaction_lock, MAX_LOCK_BYTES)
            != transaction_lock_bytes
            or repro.read_regular(args.build_lock, repro.MAX_LOCK_BYTES)
            != build_lock_bytes
            or repro.read_regular(args.vendor_rpm, MAX_RPM_BYTES) != vendor_bytes
            or repro.read_regular(args.hardened_rpm, MAX_RPM_BYTES) != hardened_bytes
        ):
            raise TransactionError("transaction evidence changed during execution")
        report = {
            "schema": 1,
            "completed": True,
            "accepted": True,
            "offline": True,
            "isolated_mock_root_cleaned": True,
            "stage_count": len(observations),
            "stages": [item["stage"] for item in observations],
            "vendor_sudoers_present_both_times": True,
            "hardened_sudoers_absent_both_times": True,
            "fan_controller_reversible": True,
            "single_package_identity_each_stage": True,
            "payload_ownership_only": True,
            "dependency_resolution_tested": False,
            "scriptlets_executed": False,
            "triggers_executed": False,
            "device_root_touched": False,
            "release_ready": False,
        }
        write_report(args.output, report)
    except (OSError, ValueError, TransactionError, repro.ReproError) as exc:
        print(json.dumps({"completed": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
