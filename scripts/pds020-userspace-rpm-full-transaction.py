#!/usr/bin/env python3
"""Run a dependency-resolved pds2 install/remove/offline-reinstall in mock."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FULL_LOCK = (
    ROOT / "packaging/pocketds-userspace/full-transaction-lock.pds2.json"
)
DEFAULT_ARCHIVE_LOCK = (
    ROOT / "packaging/pocketds-userspace/dependency-archives-lock.pds2.json"
)
DEFAULT_BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.pds2.json"
DEFAULT_SOURCE_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.pds2.json"
REPRO_PATH = ROOT / "scripts/pds020-userspace-rpm-repro.py"
ARCHIVE_VERIFY_PATH = ROOT / "scripts/pds020-userspace-rpm-archive-verify.py"
CONFIRMATION = "RUN ISOLATED PDS020 PDS2 FULL TRANSACTION"
MAX_LOCK_BYTES = 256 * 1024
MAX_RPM_BYTES = 16 * 1024 * 1024
MAX_TOOL_OUTPUT = 4 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
UNIT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9@_.-]{0,127}\.service$")
PACKAGE_LINE_RE = re.compile(
    rb"^[A-Za-z0-9+_.~-]+\t[0-9]+\t[^\t\n]+\t[^\t\n]+\t"
    rb"(?:[A-Za-z0-9_.-]+|\(none\))$"
)
PACKAGE_QUERY = "%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\n"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


repro = load_module("pds020_full_transaction_repro", REPRO_PATH)
archive_verifier = load_module(
    "pds020_full_transaction_archive_verifier", ARCHIVE_VERIFY_PATH
)


class FullTransactionError(RuntimeError):
    """A full transaction input, stage or cleanup failed closed."""


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise FullTransactionError(f"{label} fields are incomplete or unsupported")
    return value


def require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise FullTransactionError(f"{label} is not lowercase SHA-256")
    return value


def lock_digest(path: Path) -> str:
    return hashlib.sha256(repro.read_regular(path, MAX_LOCK_BYTES)).hexdigest()


def safe_absolute_paths(values: Any, label: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise FullTransactionError(f"{label} is empty or invalid")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
            raise FullTransactionError(f"{label} contains an unsafe path")
        path = PurePosixPath(value)
        if any(part in {"", ".", ".."} for part in path.parts[1:]):
            raise FullTransactionError(f"{label} contains an unsafe path")
        result.append(value)
    if len(result) != len(set(result)):
        raise FullTransactionError(f"{label} contains duplicate paths")
    return result


def load_lock(path: Path) -> dict[str, Any]:
    lock = exact_object(
        repro.strict_json(repro.read_regular(path, MAX_LOCK_BYTES), "full lock"),
        {"schema", "artifact", "builder", "stages", "installed_contract"},
        "full lock",
    )
    if type(lock["schema"]) is not int or lock["schema"] != 1:
        raise FullTransactionError("full lock schema is unsupported")
    artifact = exact_object(
        lock["artifact"],
        {
            "source_lock_sha256",
            "build_lock_sha256",
            "archive_lock_sha256",
            "binary_rpm_sha256",
        },
        "artifact lock",
    )
    for key, value in artifact.items():
        require_sha(value, f"artifact {key}")
    builder = exact_object(
        lock["builder"],
        {
            "distro",
            "architecture",
            "mock_config",
            "mock_nvr",
            "initial_install_offline",
            "dependency_archives_content_locked",
            "remove_offline",
            "reinstall_offline",
        },
        "builder lock",
    )
    if (
        builder["distro"] != "fedora-44"
        or builder["architecture"] != "aarch64"
        or builder["mock_config"] != "fedora-44-aarch64"
        or not isinstance(builder["mock_nvr"], str)
        or builder["mock_nvr"] != "mock-6.8-1.fc44.noarch"
        or builder["initial_install_offline"] is not True
        or builder["dependency_archives_content_locked"] is not True
        or builder["remove_offline"] is not True
        or builder["reinstall_offline"] is not True
    ):
        raise FullTransactionError("builder transaction boundary is unsupported")
    stages = lock["stages"]
    expected_names = ["baseline", "dependency-install", "remove", "offline-reinstall"]
    if not isinstance(stages, list) or len(stages) != len(expected_names):
        raise FullTransactionError("stage lock is incomplete")
    for expected_name, value in zip(expected_names, stages):
        stage = exact_object(
            value,
            {"name", "package_count", "package_manifest_sha256"},
            "stage record",
        )
        if (
            stage["name"] != expected_name
            or type(stage["package_count"]) is not int
            or stage["package_count"] <= 0
        ):
            raise FullTransactionError("stage record is invalid")
        require_sha(stage["package_manifest_sha256"], "stage manifest")
    contract = exact_object(
        lock["installed_contract"],
        {
            "rpm_verify_output",
            "legacy_packages_absent",
            "legacy_paths_absent",
            "critical_payload_paths",
            "system_wants",
        },
        "installed contract",
    )
    if (
        contract["rpm_verify_output"] != ["S.5......  c /etc/fstab"]
        or contract["legacy_packages_absent"] != ["onboard", "onboard-data"]
    ):
        raise FullTransactionError("installed RPM verification contract is invalid")
    safe_absolute_paths(contract["legacy_paths_absent"], "legacy paths")
    safe_absolute_paths(contract["critical_payload_paths"], "critical paths")
    wants = contract["system_wants"]
    if not isinstance(wants, dict) or not wants:
        raise FullTransactionError("system wants contract is invalid")
    for unit, target in wants.items():
        if UNIT_NAME_RE.fullmatch(unit) is None or "/" in unit:
            raise FullTransactionError("system wants unit is unsafe")
        safe_absolute_paths([target], "system wants target")
    return lock


def safe_executable(path: Path, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise FullTransactionError(f"{label} is missing") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or not os.access(path, os.X_OK)
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise FullTransactionError(f"{label} is unsafe")


def run_bounded(
    command: list[str],
    label: str,
    *,
    timeout: int = 900,
    expected: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FullTransactionError(f"{label} could not run") from exc
    if (
        result.returncode not in expected
        or len(result.stdout) > MAX_TOOL_OUTPUT
        or len(result.stderr) > MAX_TOOL_OUTPUT
    ):
        raise FullTransactionError(f"{label} failed or exceeded its output bound")
    return result


def package_manifest(sudo: Path, rpm: Path, root: Path) -> tuple[int, str]:
    output = run_bounded(
        [str(sudo), str(rpm), "--root", str(root), "-qa", "--qf", PACKAGE_QUERY],
        "installed package query",
    ).stdout
    lines = output.splitlines()
    if not lines or any(PACKAGE_LINE_RE.fullmatch(line) is None for line in lines):
        raise FullTransactionError("installed package manifest is malformed")
    if len(lines) != len(set(lines)):
        raise FullTransactionError("installed package manifest contains duplicates")
    normalized = b"\n".join(sorted(lines)) + b"\n"
    return len(lines), hashlib.sha256(normalized).hexdigest()


def check_stage(
    lock: dict[str, Any],
    stage_index: int,
    sudo: Path,
    rpm: Path,
    root: Path,
) -> dict[str, Any]:
    count, digest = package_manifest(sudo, rpm, root)
    expected = lock["stages"][stage_index]
    if count != expected["package_count"] or digest != expected["package_manifest_sha256"]:
        raise FullTransactionError(f"{expected['name']} package manifest differs")
    return {"name": expected["name"], "package_count": count, "sha256": digest}


def root_path(root: Path, absolute: str) -> Path:
    return root.joinpath(*PurePosixPath(absolute).parts[1:])


def require_absent(sudo: Path, root: Path, paths: list[str], label: str) -> None:
    for value in paths:
        run_bounded(
            [str(sudo), "/usr/bin/test", "!", "-e", str(root_path(root, value))],
            label,
        )


def validate_installed(
    lock: dict[str, Any],
    build_lock: dict[str, Any],
    sudo: Path,
    rpm: Path,
    root: Path,
) -> None:
    package = build_lock["package"]
    expected_nevra = (
        f"{package['name']}-{package['version']}-{package['release']}.{package['arch']}"
    )
    result = run_bounded(
        [
            str(sudo),
            str(rpm),
            "--root",
            str(root),
            "-q",
            "--qf",
            "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n",
            package["name"],
        ],
        "installed package identity",
    )
    if result.stdout.decode("utf-8", errors="strict").strip() != expected_nevra:
        raise FullTransactionError("installed package identity differs")
    verification = run_bounded(
        [str(sudo), str(rpm), "--root", str(root), "-V", package["name"]],
        "installed package verification",
        expected=(0, 1),
    )
    observed_verify = verification.stdout.decode("utf-8", errors="strict").splitlines()
    if observed_verify != lock["installed_contract"]["rpm_verify_output"]:
        raise FullTransactionError("installed payload verification differs")
    for legacy in lock["installed_contract"]["legacy_packages_absent"]:
        absent = run_bounded(
            [str(sudo), str(rpm), "--root", str(root), "-q", legacy],
            "legacy package absence",
            expected=(1,),
        )
        if absent.stderr or absent.stdout != f"package {legacy} is not installed\n".encode():
            raise FullTransactionError("legacy package query is ambiguous")
    require_absent(
        sudo,
        root,
        lock["installed_contract"]["legacy_paths_absent"],
        "legacy path absence",
    )
    for value in lock["installed_contract"]["critical_payload_paths"]:
        run_bounded(
            [str(sudo), "/usr/bin/test", "-e", str(root_path(root, value))],
            "critical payload presence",
        )
    fan = root_path(root, "/usr/bin/pocketds-fancontrol")
    fan_result = run_bounded(
        [str(sudo), "/usr/bin/sha256sum", str(fan)],
        "fan payload hash",
    )
    if fan_result.stdout.split(maxsplit=1)[0].decode() != build_lock[
        "payload_audit"
    ]["fan_controller_sha256"]:
        raise FullTransactionError("installed fan controller differs")
    wants_root = root_path(root, "/etc/systemd/system/multi-user.target.wants")
    for unit, target in lock["installed_contract"]["system_wants"].items():
        link = run_bounded(
            [str(sudo), "/usr/bin/readlink", str(wants_root / unit)],
            "system wants link",
        )
        if link.stdout.decode("utf-8", errors="strict").strip() != target:
            raise FullTransactionError("system wants link differs")


def validate_removed(
    lock: dict[str, Any],
    sudo: Path,
    rpm: Path,
    root: Path,
) -> None:
    absent = run_bounded(
        [str(sudo), str(rpm), "--root", str(root), "-q", "pocketds-userspace"],
        "removed package absence",
        expected=(1,),
    )
    if absent.stderr or absent.stdout != b"package pocketds-userspace is not installed\n":
        raise FullTransactionError("removed package query is ambiguous")
    require_absent(
        sudo,
        root,
        lock["installed_contract"]["critical_payload_paths"],
        "removed critical payload absence",
    )
    wants_root = root_path(root, "/etc/systemd/system/multi-user.target.wants")
    require_absent(
        sudo,
        root,
        [f"/etc/systemd/system/multi-user.target.wants/{unit}" for unit in lock[
            "installed_contract"
        ]["system_wants"]],
        "removed system wants absence",
    )


def validate_output(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise FullTransactionError("output already exists or is unsafe")
    parent = path.parent
    try:
        metadata = os.lstat(parent)
    except OSError as exc:
        raise FullTransactionError("output parent is missing") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise FullTransactionError("output parent is unsafe")


def write_output(path: Path, report: dict[str, Any]) -> None:
    content = (json.dumps(report, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    try:
        os.write(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_inputs(
    lock: dict[str, Any],
    source_lock_path: Path,
    build_lock_path: Path,
    archive_lock_path: Path,
    archive_dir: Path,
    rpm: Path,
    rpmkeys: Path,
    rpm2archive: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if lock_digest(source_lock_path) != lock["artifact"]["source_lock_sha256"]:
        raise FullTransactionError("source lock differs")
    if lock_digest(build_lock_path) != lock["artifact"]["build_lock_sha256"]:
        raise FullTransactionError("build lock differs")
    if lock_digest(archive_lock_path) != lock["artifact"]["archive_lock_sha256"]:
        raise FullTransactionError("archive lock differs")
    build_lock = repro.load_lock(build_lock_path)
    archive_lock = archive_verifier.load_lock(archive_lock_path)
    project = next(
        record
        for record in archive_lock["packages"]
        if record["origin"] == "project-pre-sign"
    )
    hardened_rpm = archive_dir / project["filename"]
    rpm_bytes = repro.read_regular(hardened_rpm, MAX_RPM_BYTES)
    expected = build_lock["package"]["binary_result"]
    if (
        len(rpm_bytes) != expected["size"]
        or hashlib.sha256(rpm_bytes).hexdigest() != expected["sha256"]
        or expected["sha256"] != lock["artifact"]["binary_rpm_sha256"]
    ):
        raise FullTransactionError("hardened RPM differs")
    try:
        archive_report = archive_verifier.verify(
            archive_lock,
            archive_dir,
            source_lock_path,
            build_lock_path,
            rpm,
            rpmkeys,
            rpm2archive,
        )
    except (
        archive_verifier.repro.ReproError,
        archive_verifier.auditor.AuditError,
    ) as exc:
        raise FullTransactionError("dependency archive verification failed") from exc
    if archive_report.get("verified") is not True:
        raise FullTransactionError("dependency archive verification failed")
    return build_lock, archive_lock


def execute(
    lock: dict[str, Any],
    build_lock: dict[str, Any],
    archive_lock: dict[str, Any],
    archive_dir: Path,
    sudo: Path,
    mock: Path,
    rpm: Path,
) -> list[dict[str, Any]]:
    suffix = secrets.randbelow(1_000_000_000) + 1
    uniqueext = f"pds020-userspace-fulltxn-{suffix}"
    if SAFE_NAME_RE.fullmatch(uniqueext) is None:  # pragma: no cover
        raise FullTransactionError("generated mock root name is unsafe")
    config = lock["builder"]["mock_config"]
    root = Path("/var/lib/mock") / f"{config}-{uniqueext}" / "root"
    base = [str(sudo), str(mock), "-r", config, "--uniqueext", uniqueext]
    initialized = False
    primary: Exception | None = None
    observations: list[dict[str, Any]] = []
    package_paths = [
        str(archive_dir / record["filename"])
        for record in archive_lock["packages"]
    ]
    project_path = next(
        archive_dir / record["filename"]
        for record in archive_lock["packages"]
        if record["origin"] == "project-pre-sign"
    )
    try:
        run_bounded(base + ["--offline", "--init"], "offline mock initialization")
        initialized = True
        observations.append(check_stage(lock, 0, sudo, rpm, root))
        run_bounded(
            base + ["--offline", "--install"] + package_paths,
            "offline content-locked dependency install",
        )
        observations.append(check_stage(lock, 1, sudo, rpm, root))
        validate_installed(lock, build_lock, sudo, rpm, root)
        run_bounded(
            base + ["--offline", "--remove", "pocketds-userspace"],
            "offline package removal",
        )
        observations.append(check_stage(lock, 2, sudo, rpm, root))
        validate_removed(lock, sudo, rpm, root)
        run_bounded(
            base + ["--offline", "--install", str(project_path)],
            "offline package reinstall",
        )
        observations.append(check_stage(lock, 3, sudo, rpm, root))
        validate_installed(lock, build_lock, sudo, rpm, root)
    except Exception as exc:
        primary = exc
    finally:
        if initialized or primary is not None:
            try:
                run_bounded(base + ["--clean"], "mock cleanup", timeout=180)
            except Exception as cleanup_exc:
                if primary is None:
                    primary = cleanup_exc
    if primary is not None:
        raise primary
    return observations


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--full-lock", type=Path, default=DEFAULT_FULL_LOCK)
    parser.add_argument("--archive-lock", type=Path, default=DEFAULT_ARCHIVE_LOCK)
    parser.add_argument("--build-lock", type=Path, default=DEFAULT_BUILD_LOCK)
    parser.add_argument("--source-lock", type=Path, default=DEFAULT_SOURCE_LOCK)
    parser.add_argument("--sudo", type=Path, default=Path("/usr/bin/sudo"))
    parser.add_argument("--mock", type=Path, default=Path("/usr/libexec/mock/mock"))
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument("--rpmkeys", type=Path, default=Path("/usr/bin/rpmkeys"))
    parser.add_argument("--rpm2archive", type=Path, default=Path("/usr/bin/rpm2archive"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        lock = load_lock(args.full_lock)
        if not args.execute:
            if any(value is not None for value in (args.confirm, args.archive_dir, args.output)):
                raise FullTransactionError("plan accepts no execution arguments")
            print(
                json.dumps(
                    {
                        "planned": True,
                        "executed": False,
                        "initial_install_offline": True,
                        "dependency_archives_content_locked": True,
                        "remove_offline": True,
                        "reinstall_offline": True,
                        "dependency_resolution_tested": True,
                        "scriptlets_executed": True,
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
            or args.archive_dir is None
            or args.output is None
        ):
            print(json.dumps({"completed": False, "error": "execution gate is incomplete"}))
            return 2
        validate_output(args.output)
        for path, label in (
            (args.sudo, "sudo executable"),
            (args.mock, "mock executable"),
            (args.rpm, "RPM executable"),
            (args.rpmkeys, "RPM signature verifier"),
            (args.rpm2archive, "rpm2archive executable"),
        ):
            safe_executable(path, label)
        build_lock, archive_lock = verify_inputs(
            lock,
            args.source_lock,
            args.build_lock,
            args.archive_lock,
            args.archive_dir,
            args.rpm,
            args.rpmkeys,
            args.rpm2archive,
        )
        observations = execute(
            lock,
            build_lock,
            archive_lock,
            args.archive_dir,
            args.sudo,
            args.mock,
            args.rpm,
        )
        report = {
            "schema": 1,
            "completed": True,
            "accepted": True,
            "stages": observations,
            "initial_install_offline": True,
            "dependency_archives_content_locked": True,
            "remove_offline": True,
            "reinstall_offline": True,
            "dependency_resolution_tested": True,
            "scriptlets_executed": True,
            "triggers_executed": True,
            "legacy_osk_absent": True,
            "global_nopasswd_absent": True,
            "mock_root_cleaned": True,
            "device_root_touched": False,
            "signature_verified": False,
            "release_ready": False,
        }
        write_output(args.output, report)
    except (
        OSError,
        UnicodeError,
        ValueError,
        FullTransactionError,
        repro.ReproError,
        archive_verifier.ArchiveError,
    ) as exc:
        print(json.dumps({"completed": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
