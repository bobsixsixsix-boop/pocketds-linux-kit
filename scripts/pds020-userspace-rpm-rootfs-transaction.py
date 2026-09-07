#!/usr/bin/env python3
"""Install the exact pds2 rootfs RPM archive into a disposable empty root."""

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
DEFAULT_TRANSACTION_LOCK = (
    ROOT / "packaging/pocketds-userspace/rootfs-transaction-lock.pds2.json"
)
DEFAULT_ARCHIVE_LOCK = (
    ROOT / "packaging/pocketds-userspace/rootfs-package-archives-lock.pds2.json"
)
DEFAULT_BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.pds2.json"
DEFAULT_SOURCE_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.pds2.json"
FULL_TRANSACTION_PATH = ROOT / "scripts/pds020-userspace-rpm-full-transaction.py"
CONFIRMATION = "RUN ISOLATED PDS020 PDS2 ROOTFS TRANSACTION"
MAX_LOCK_BYTES = 256 * 1024
MAX_TOOL_OUTPUT = 4 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ROOT_NAME_RE = re.compile(r"^pds020-rootfs-txn-[0-9]{1,9}$")
UNIT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9@_.-]{0,127}\.service$")


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


full = load_module("pds020_rootfs_full_transaction", FULL_TRANSACTION_PATH)
repro = full.repro
archive_verifier = full.archive_verifier


class RootfsTransactionError(RuntimeError):
    """The rootfs archive, transaction, inspection or cleanup failed closed."""


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RootfsTransactionError(f"{label} fields are incomplete or unsupported")
    return value


def require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RootfsTransactionError(f"{label} is not lowercase SHA-256")
    return value


def safe_absolute_paths(values: Any, label: str) -> list[str]:
    if not isinstance(values, list):
        raise RootfsTransactionError(f"{label} is invalid")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
            raise RootfsTransactionError(f"{label} contains an unsafe path")
        path = PurePosixPath(value)
        if any(part in {"", ".", ".."} for part in path.parts[1:]):
            raise RootfsTransactionError(f"{label} contains an unsafe path")
        result.append(value)
    if len(result) != len(set(result)):
        raise RootfsTransactionError(f"{label} contains duplicate paths")
    return result


def lock_digest(path: Path) -> str:
    return hashlib.sha256(repro.read_regular(path, MAX_LOCK_BYTES)).hexdigest()


def load_lock(path: Path) -> dict[str, Any]:
    lock = exact_object(
        repro.strict_json(repro.read_regular(path, MAX_LOCK_BYTES), "rootfs lock"),
        {"schema", "artifact", "builder", "installed_stage", "installed_contract"},
        "rootfs lock",
    )
    if type(lock["schema"]) is not int or lock["schema"] != 1:
        raise RootfsTransactionError("rootfs lock schema is unsupported")
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
            "dnf5_nvr",
            "host_config_read_only",
            "all_repositories_disabled",
            "plugins_disabled",
            "cache_only",
            "local_archives_only",
        },
        "builder lock",
    )
    if builder != {
        "distro": "fedora-44",
        "architecture": "aarch64",
        "dnf5_nvr": "dnf5-5.4.1.0-1.fc44.aarch64",
        "host_config_read_only": True,
        "all_repositories_disabled": True,
        "plugins_disabled": True,
        "cache_only": True,
        "local_archives_only": True,
    }:
        raise RootfsTransactionError("rootfs builder boundary is unsupported")
    stage = exact_object(
        lock["installed_stage"],
        {"package_count", "package_manifest_sha256", "dnf_check"},
        "installed stage",
    )
    if (
        type(stage["package_count"]) is not int
        or stage["package_count"] != 322
        or stage["dnf_check"] != "pass"
    ):
        raise RootfsTransactionError("installed stage is invalid")
    require_sha(stage["package_manifest_sha256"], "installed manifest")
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
        contract["rpm_verify_output"] != []
        or contract["legacy_packages_absent"] != ["onboard", "onboard-data"]
    ):
        raise RootfsTransactionError("installed RPM verification contract is invalid")
    safe_absolute_paths(contract["legacy_paths_absent"], "legacy paths")
    safe_absolute_paths(contract["critical_payload_paths"], "critical paths")
    wants = contract["system_wants"]
    if not isinstance(wants, dict) or not wants:
        raise RootfsTransactionError("system wants contract is invalid")
    for unit, target in wants.items():
        if not isinstance(unit, str) or UNIT_NAME_RE.fullmatch(unit) is None or "/" in unit:
            raise RootfsTransactionError("system wants unit is unsafe")
        safe_absolute_paths([target], "system wants target")
    return lock


def safe_executable(path: Path, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RootfsTransactionError(f"{label} is missing or unsafe") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise RootfsTransactionError(f"{label} is missing or unsafe")


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
        raise RootfsTransactionError(f"{label} could not run") from exc
    if (
        result.returncode not in expected
        or len(result.stdout) > MAX_TOOL_OUTPUT
        or len(result.stderr) > MAX_TOOL_OUTPUT
    ):
        raise RootfsTransactionError(f"{label} failed or exceeded its output bound")
    return result


def safe_private_directory(path: Path, label: str) -> tuple[int, int]:
    if not path.is_absolute() or ".." in path.parts:
        raise RootfsTransactionError(f"{label} path must be absolute and normalized")
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RootfsTransactionError(f"{label} is missing or unsafe") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RootfsTransactionError(f"{label} must be owned mode-0700")
    return metadata.st_dev, metadata.st_ino


def validate_distinct_directories(work_dir: Path, archive_dir: Path) -> None:
    if (
        work_dir == archive_dir
        or work_dir in archive_dir.parents
        or archive_dir in work_dir.parents
    ):
        raise RootfsTransactionError("work and archive directories must be separate")


def create_root(work_dir: Path) -> tuple[Path, tuple[int, int]]:
    suffix = secrets.randbelow(1_000_000_000) + 1
    name = f"pds020-rootfs-txn-{suffix}"
    if ROOT_NAME_RE.fullmatch(name) is None:  # pragma: no cover
        raise RootfsTransactionError("generated root name is unsafe")
    root = work_dir / name
    try:
        os.mkdir(root, mode=0o700)
        metadata = os.lstat(root)
    except OSError as exc:
        raise RootfsTransactionError("disposable root could not be created") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RootfsTransactionError("disposable root metadata is unsafe")
    return root, (metadata.st_dev, metadata.st_ino)


def cleanup_root(
    sudo: Path,
    rm: Path,
    work_dir: Path,
    work_identity: tuple[int, int],
    root: Path,
    root_identity: tuple[int, int],
) -> None:
    if root.parent != work_dir or ROOT_NAME_RE.fullmatch(root.name) is None:
        raise RootfsTransactionError("refusing unsafe disposable root cleanup")
    if safe_private_directory(work_dir, "work directory") != work_identity:
        raise RootfsTransactionError("work directory changed before cleanup")
    try:
        metadata = os.lstat(root)
    except OSError as exc:
        raise RootfsTransactionError("disposable root vanished before cleanup") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != root_identity
    ):
        raise RootfsTransactionError("disposable root changed before cleanup")
    run_bounded(
        [
            str(sudo),
            str(rm),
            "-rf",
            "--one-file-system",
            "--",
            str(root),
        ],
        "disposable root cleanup",
        timeout=180,
    )
    if root.exists() or root.is_symlink():
        raise RootfsTransactionError("disposable root cleanup was incomplete")


def validate_output(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise RootfsTransactionError("output already exists or is unsafe")
    safe_private_directory(path.parent, "output parent")


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
    archive_dir: Path,
    archive_lock_path: Path,
    source_lock_path: Path,
    build_lock_path: Path,
    rpm: Path,
    rpmkeys: Path,
    rpm2archive: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    for path, expected, label in (
        (source_lock_path, lock["artifact"]["source_lock_sha256"], "source lock"),
        (build_lock_path, lock["artifact"]["build_lock_sha256"], "build lock"),
        (archive_lock_path, lock["artifact"]["archive_lock_sha256"], "archive lock"),
    ):
        if lock_digest(path) != expected:
            raise RootfsTransactionError(f"{label} differs")
    build_lock = repro.load_lock(build_lock_path)
    archive_lock = archive_verifier.load_lock(archive_lock_path)
    if (
        archive_lock["package_count"] != lock["installed_stage"]["package_count"]
        or archive_lock["fedora_package_count"] != 321
    ):
        raise RootfsTransactionError("rootfs archive package boundary differs")
    project = next(
        record
        for record in archive_lock["packages"]
        if record["origin"] == "project-pre-sign"
    )
    if (
        project["sha256"] != lock["artifact"]["binary_rpm_sha256"]
        or project["sha256"] != build_lock["package"]["binary_result"]["sha256"]
    ):
        raise RootfsTransactionError("project RPM differs")
    try:
        report = archive_verifier.verify(
            archive_lock,
            archive_dir,
            source_lock_path,
            build_lock_path,
            rpm,
            rpmkeys,
            rpm2archive,
        )
    except (
        archive_verifier.ArchiveError,
        archive_verifier.repro.ReproError,
        archive_verifier.auditor.AuditError,
    ) as exc:
        raise RootfsTransactionError("rootfs archive verification failed") from exc
    if report.get("verified") is not True:
        raise RootfsTransactionError("rootfs archive verification failed")
    return build_lock, archive_lock


def execute(
    lock: dict[str, Any],
    build_lock: dict[str, Any],
    archive_lock: dict[str, Any],
    archive_dir: Path,
    work_dir: Path,
    sudo: Path,
    dnf5: Path,
    rpm: Path,
    rm: Path,
) -> dict[str, Any]:
    work_identity = safe_private_directory(work_dir, "work directory")
    validate_distinct_directories(work_dir, archive_dir)
    root, root_identity = create_root(work_dir)
    primary: Exception | None = None
    observation: dict[str, Any] | None = None
    package_paths = [
        str(archive_dir / record["filename"])
        for record in archive_lock["packages"]
    ]
    base = [
        str(sudo),
        str(dnf5),
        "--use-host-config",
        f"--installroot={root}",
        "--releasever=44",
        "--disable-repo=*",
        "--no-plugins",
        "--cacheonly",
    ]
    try:
        run_bounded(
            base + ["--assumeyes", "install"] + package_paths,
            "offline local rootfs install",
        )
        count, digest = full.package_manifest(sudo, rpm, root)
        expected = lock["installed_stage"]
        if count != expected["package_count"] or digest != expected["package_manifest_sha256"]:
            raise RootfsTransactionError("installed package manifest differs")
        run_bounded(base + ["check"], "offline DNF dependency check")
        full.validate_installed(lock, build_lock, sudo, rpm, root)
        observation = {
            "name": "empty-root-install",
            "package_count": count,
            "package_manifest_sha256": digest,
            "dnf_check": "pass",
        }
    except Exception as exc:
        primary = exc
    finally:
        try:
            cleanup_root(
                sudo,
                rm,
                work_dir,
                work_identity,
                root,
                root_identity,
            )
        except Exception as cleanup_exc:
            if primary is None:
                primary = cleanup_exc
    if primary is not None:
        raise primary
    if observation is None:  # pragma: no cover
        raise RootfsTransactionError("rootfs observation is missing")
    return observation


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--transaction-lock", type=Path, default=DEFAULT_TRANSACTION_LOCK)
    parser.add_argument("--archive-lock", type=Path, default=DEFAULT_ARCHIVE_LOCK)
    parser.add_argument("--build-lock", type=Path, default=DEFAULT_BUILD_LOCK)
    parser.add_argument("--source-lock", type=Path, default=DEFAULT_SOURCE_LOCK)
    parser.add_argument("--sudo", type=Path, default=Path("/usr/bin/sudo"))
    parser.add_argument("--dnf5", type=Path, default=Path("/usr/bin/dnf5"))
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument("--rpmkeys", type=Path, default=Path("/usr/bin/rpmkeys"))
    parser.add_argument("--rpm2archive", type=Path, default=Path("/usr/bin/rpm2archive"))
    parser.add_argument("--rm", type=Path, default=Path("/usr/bin/rm"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        lock = load_lock(args.transaction_lock)
        if not args.execute:
            if any(
                value is not None
                for value in (args.confirm, args.archive_dir, args.work_dir, args.output)
            ):
                raise RootfsTransactionError("plan accepts no execution arguments")
            print(
                json.dumps(
                    {
                        "planned": True,
                        "executed": False,
                        "empty_root": True,
                        "offline": True,
                        "local_archives_only": True,
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
            or args.work_dir is None
            or args.output is None
        ):
            print(json.dumps({"completed": False, "error": "execution gate is incomplete"}))
            return 2
        validate_output(args.output)
        safe_private_directory(args.work_dir, "work directory")
        for path, label in (
            (args.sudo, "sudo executable"),
            (args.dnf5, "DNF5 executable"),
            (args.rpm, "RPM executable"),
            (args.rpmkeys, "RPM signature verifier"),
            (args.rpm2archive, "RPM archive extractor"),
            (args.rm, "cleanup executable"),
        ):
            safe_executable(path, label)
        dnf_identity = run_bounded(
            [
                str(args.rpm),
                "-q",
                "--qf",
                "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n",
                "dnf5",
            ],
            "DNF5 package identity",
        ).stdout.decode("utf-8", errors="strict").strip()
        if dnf_identity != lock["builder"]["dnf5_nvr"]:
            raise RootfsTransactionError("DNF5 package identity differs")
        build_lock, archive_lock = verify_inputs(
            lock,
            args.archive_dir,
            args.archive_lock,
            args.source_lock,
            args.build_lock,
            args.rpm,
            args.rpmkeys,
            args.rpm2archive,
        )
        observation = execute(
            lock,
            build_lock,
            archive_lock,
            args.archive_dir,
            args.work_dir,
            args.sudo,
            args.dnf5,
            args.rpm,
            args.rm,
        )
        post_archive = archive_verifier.verify(
            archive_lock,
            args.archive_dir,
            args.source_lock,
            args.build_lock,
            args.rpm,
            args.rpmkeys,
            args.rpm2archive,
        )
        if post_archive.get("verified") is not True:
            raise RootfsTransactionError("rootfs archive changed during transaction")
        project_record = next(
            record
            for record in archive_lock["packages"]
            if record["origin"] == "project-pre-sign"
        )
        report = {
            "schema": 1,
            "completed": True,
            "accepted": True,
            "stage": observation,
            "archive_package_count": archive_lock["package_count"],
            "transaction_lock_sha256": lock_digest(args.transaction_lock),
            "archive_lock_sha256": lock["artifact"]["archive_lock_sha256"],
            "archive_records_sha256": archive_lock["records_sha256"],
            "project_rpm_sha256": project_record["sha256"],
            "dnf5_nvr": lock["builder"]["dnf5_nvr"],
            "fedora_signatures_verified": True,
            "project_payload_audited": True,
            "project_signature_verified": False,
            "empty_root": True,
            "offline": True,
            "local_archives_only": True,
            "dependency_resolution_tested": True,
            "scriptlets_executed": True,
            "triggers_executed": True,
            "dnf_check": "pass",
            "legacy_osk_absent": True,
            "global_nopasswd_absent": True,
            "disposable_root_cleaned": True,
            "device_root_touched": False,
            "selinux_labels_verified": False,
            "services_started": False,
            "release_ready": False,
        }
        write_output(args.output, report)
    except (
        OSError,
        UnicodeError,
        ValueError,
        RootfsTransactionError,
        full.FullTransactionError,
        repro.ReproError,
        archive_verifier.ArchiveError,
        archive_verifier.repro.ReproError,
        archive_verifier.auditor.AuditError,
    ) as exc:
        print(json.dumps({"completed": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
