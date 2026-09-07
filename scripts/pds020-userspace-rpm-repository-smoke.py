#!/usr/bin/env python3
"""Run a confirmation-gated DNF5 repo_gpgcheck smoke in a disposable root."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
SIGNED_GATE_PATH = ROOT / "scripts/pds020-userspace-rpm-repository-postsign.py"
ROOTFS_PATH = ROOT / "scripts/pds020-userspace-rpm-rootfs-transaction.py"
DEFAULT_ROOTFS_LOCK = (
    ROOT / "packaging/pocketds-userspace/rootfs-transaction-lock.pds2.json"
)
CONFIRMATION = "RUN ISOLATED PDS020 PDS2 REPOSITORY SMOKE"
DNF5_NVR = "dnf5-5.4.1.0-1.fc44.aarch64"
MAX_TOOL_OUTPUT = 4 * 1024 * 1024
QUERY_FORMAT = "%{name}\t%{epoch}\t%{version}\t%{release}\t%{arch}\n"
FORBIDDEN_DNF_TEXT = (
    b"GPG signature is not available",
    b"GPG verification is enabled, but",
    b"Failed to download metadata",
    b"repomd.xml.asc] -",
    b"Skipping repositories",
)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


signed = load_module("pds020_repository_smoke_signed_gate", SIGNED_GATE_PATH)
rootfs = load_module("pds020_repository_smoke_rootfs", ROOTFS_PATH)
repro = signed.repro


class RepositorySmokeError(RuntimeError):
    """The signed repository, DNF5 smoke or cleanup failed closed."""


def private_directory(path: Path, label: str) -> tuple[int, int]:
    try:
        return rootfs.safe_private_directory(path, label)
    except rootfs.RootfsTransactionError as exc:
        raise RepositorySmokeError(str(exc)) from exc


def safe_executable(path: Path, label: str) -> None:
    try:
        rootfs.safe_executable(path, label)
    except rootfs.RootfsTransactionError as exc:
        raise RepositorySmokeError(str(exc)) from exc


def create_workspace(work_dir: Path) -> tuple[Path, tuple[int, int]]:
    name = f"pds020-repository-smoke-{secrets.randbelow(1_000_000_000) + 1}"
    path = work_dir / name
    try:
        os.mkdir(path, mode=0o700)
        metadata = os.lstat(path)
    except OSError as exc:
        raise RepositorySmokeError("disposable workspace could not be created") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RepositorySmokeError("disposable workspace metadata is unsafe")
    return path, (metadata.st_dev, metadata.st_ino)


def cleanup_workspace(
    sudo: Path,
    rm: Path,
    work_dir: Path,
    work_identity: tuple[int, int],
    workspace: Path,
    workspace_identity: tuple[int, int],
) -> None:
    if workspace.parent != work_dir or not workspace.name.startswith(
        "pds020-repository-smoke-"
    ):
        raise RepositorySmokeError("refusing unsafe repository workspace cleanup")
    if private_directory(work_dir, "work directory") != work_identity:
        raise RepositorySmokeError("work directory changed before cleanup")
    try:
        metadata = os.lstat(workspace)
    except OSError as exc:
        raise RepositorySmokeError("disposable workspace vanished before cleanup") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != workspace_identity
    ):
        raise RepositorySmokeError("disposable workspace changed before cleanup")
    try:
        rootfs.run_bounded(
            [
                str(sudo),
                str(rm),
                "-rf",
                "--one-file-system",
                "--",
                str(workspace),
            ],
            "repository smoke cleanup",
            timeout=180,
        )
    except rootfs.RootfsTransactionError as exc:
        raise RepositorySmokeError("repository smoke cleanup failed") from exc
    if workspace.exists() or workspace.is_symlink():
        raise RepositorySmokeError("repository smoke cleanup was incomplete")


def write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        count = os.write(descriptor, data[offset : offset + 65_536])
        if count <= 0:  # pragma: no cover
            raise RepositorySmokeError("disposable repository write failed")
        offset += count


def copy_bound(source: Path, destination: Path, maximum: int) -> tuple[int, str]:
    data = repro.read_regular(source, maximum)
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o400,
        )
    except OSError as exc:
        raise RepositorySmokeError("disposable repository target is unsafe") from exc
    try:
        write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    copied = repro.read_regular(destination, maximum)
    if copied != data:
        raise RepositorySmokeError("disposable repository copy differs")
    return len(data), hashlib.sha256(data).hexdigest()


def expected_query_rows(packages: list[dict[str, Any]]) -> list[str]:
    rows = [
        "\t".join(
            (
                record["name"],
                str(record["epoch"]),
                record["version"],
                record["release"],
                record["arch"],
            )
        )
        for record in packages
    ]
    if len(rows) != 322 or len(set(rows)) != 322:
        raise RepositorySmokeError("expected repository package identities differ")
    return sorted(rows)


def parse_query_output(data: bytes, packages: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RepositorySmokeError("DNF5 repository query is not UTF-8") from exc
    if "\r" in text or (text and not text.endswith("\n")):
        raise RepositorySmokeError("DNF5 repository query framing differs")
    rows = text.splitlines()
    if rows != expected_query_rows(packages):
        raise RepositorySmokeError("DNF5 repository package manifest differs")
    canonical = "".join(f"{row}\n" for row in rows).encode()
    return {
        "package_count": len(rows),
        "package_manifest_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def ensure_clean_dnf_output(stdout: bytes, stderr: bytes, label: str) -> None:
    combined = stdout + b"\n" + stderr
    if any(token in combined for token in FORBIDDEN_DNF_TEXT):
        raise RepositorySmokeError(f"{label} reported a repository/signature failure")


def materialize_repository(
    repository_root: Path,
    signed_archive_dir: Path,
    result_a: Path,
    final_archive: dict[str, Any],
    repository_receipt: dict[str, Any],
) -> dict[str, Any]:
    repository_root.mkdir(mode=0o700)
    repodata = repository_root / "repodata"
    repodata.mkdir(mode=0o700)
    copied_bytes = 0
    for record in final_archive["packages"]:
        size, digest = copy_bound(
            signed_archive_dir / record["filename"],
            repository_root / record["filename"],
            signed.MAX_RPM_BYTES,
        )
        if size != record["size"] or digest != record["sha256"]:
            raise RepositorySmokeError("materialized package differs from final archive")
        copied_bytes += size
    metadata_records = [repository_receipt["repository"]["repomd"]] + list(
        repository_receipt["repository"]["metadata"]
    )
    metadata_records.append(repository_receipt["repository_signature"]["artifact"])
    for record in metadata_records:
        maximum = (
            signed.MAX_SIGNATURE_BYTES
            if record["filename"].endswith(".asc")
            else signed.repository.MAX_METADATA_BYTES
        )
        size, digest = copy_bound(
            result_a / record["filename"],
            repodata / record["filename"],
            maximum,
        )
        if size != record["size"] or digest != record["sha256"]:
            raise RepositorySmokeError("materialized repodata differs from receipt")
        copied_bytes += size
    observed = list(os.scandir(repository_root))
    package_names = {record["filename"] for record in final_archive["packages"]}
    if {entry.name for entry in observed} != package_names | {"repodata"}:
        raise RepositorySmokeError("materialized repository root is incomplete")
    signed.repository.exact_directory(
        repodata, {record["filename"] for record in metadata_records}
    )
    return {
        "file_count": len(final_archive["packages"]) + len(metadata_records),
        "copied_bytes": copied_bytes,
    }


def dnf_base(
    sudo: Path,
    dnf5: Path,
    installroot: Path,
    repository_root: Path,
    fedora_key: Path,
    project_key: Path,
) -> list[str]:
    key_urls = f"{fedora_key.as_uri()},{project_key.as_uri()}"
    return [
        str(sudo),
        str(dnf5),
        "--quiet",
        "--assumeyes",
        f"--installroot={installroot}",
        "--use-host-config",
        "--releasever=44",
        "--forcearch=aarch64",
        "--no-plugins",
        f"--repofrompath=pds2,{repository_root.as_uri()}",
        "--repo=pds2",
        "--setopt=pds2.repo_gpgcheck=1",
        "--setopt=pds2.gpgcheck=1",
        "--setopt=pds2.skip_if_unavailable=0",
        f"--setopt=pds2.gpgkey={key_urls}",
        "--setopt=install_weak_deps=0",
        "--setopt=cachedir=/var/cache/libdnf5",
        "--setopt=logdir=/var/log",
    ]


def run_dnf(command: list[str], label: str) -> bytes:
    try:
        result = rootfs.run_bounded(command, label, timeout=1200)
    except rootfs.RootfsTransactionError as exc:
        raise RepositorySmokeError(f"{label} failed") from exc
    ensure_clean_dnf_output(result.stdout, result.stderr, label)
    return result.stdout


def package_specs(packages: list[dict[str, Any]]) -> list[str]:
    return [
        f"{record['name']}-{record['epoch']}:{record['version']}-{record['release']}.{record['arch']}"
        for record in packages
    ]


def execute(
    repository_receipt: dict[str, Any],
    final_archive: dict[str, Any],
    signed_archive_dir: Path,
    result_a: Path,
    unsigned_archive_dir: Path,
    project_key: Path,
    rootfs_lock: dict[str, Any],
    build_lock: dict[str, Any],
    work_dir: Path,
    sudo: Path,
    dnf5: Path,
    rpm: Path,
    rm: Path,
) -> dict[str, Any]:
    work_identity = private_directory(work_dir, "work directory")
    workspace, workspace_identity = create_workspace(work_dir)
    primary: Exception | None = None
    observation: dict[str, Any] | None = None
    try:
        repository_root = workspace / "repository"
        installroot = workspace / "installroot"
        installroot.mkdir(mode=0o700)
        materialized = materialize_repository(
            repository_root,
            signed_archive_dir,
            result_a,
            final_archive,
            repository_receipt,
        )
        fedora_key = unsigned_archive_dir / final_archive["fedora_key"]["filename"]
        base = dnf_base(
            sudo,
            dnf5,
            installroot,
            repository_root,
            fedora_key,
            project_key,
        )
        query = run_dnf(
            base + ["repoquery", f"--queryformat={QUERY_FORMAT}"],
            "DNF5 signed repository query",
        )
        available = parse_query_output(query, final_archive["packages"])
        run_dnf(
            base + ["install"] + package_specs(final_archive["packages"]),
            "DNF5 signed repository install",
        )
        count, installed_digest = rootfs.full.package_manifest(sudo, rpm, installroot)
        expected_stage = rootfs_lock["installed_stage"]
        if (
            count != expected_stage["package_count"]
            or installed_digest != expected_stage["package_manifest_sha256"]
        ):
            raise RepositorySmokeError("installed signed-repository manifest differs")
        run_dnf(base + ["check"], "DNF5 signed repository dependency check")
        rootfs.full.validate_installed(rootfs_lock, build_lock, sudo, rpm, installroot)
        observation = {
            "available": available,
            "installed_package_count": count,
            "installed_manifest_sha256": installed_digest,
            "materialized_file_count": materialized["file_count"],
        }
    except Exception as exc:
        primary = exc
    finally:
        try:
            cleanup_workspace(
                sudo,
                rm,
                work_dir,
                work_identity,
                workspace,
                workspace_identity,
            )
        except Exception as cleanup_exc:
            if primary is None:
                primary = cleanup_exc
    if primary is not None:
        raise primary
    if observation is None:  # pragma: no cover
        raise RepositorySmokeError("repository smoke observation is missing")
    return observation


def validate_output(path: Path) -> None:
    try:
        rootfs.validate_output(path)
    except rootfs.RootfsTransactionError as exc:
        raise RepositorySmokeError(str(exc)) from exc


def write_output(path: Path, report: dict[str, Any]) -> None:
    try:
        rootfs.write_output(path, report)
    except (OSError, rootfs.RootfsTransactionError) as exc:
        raise RepositorySmokeError("repository smoke report could not be written") from exc


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--rpm-postsign-receipt", type=Path)
    parser.add_argument("--signed-rpm", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--unsigned-archive-dir", type=Path)
    parser.add_argument("--signed-archive-dir", type=Path)
    parser.add_argument("--result-a", type=Path)
    parser.add_argument("--result-b", type=Path)
    parser.add_argument("--rootfs-lock", type=Path, default=DEFAULT_ROOTFS_LOCK)
    parser.add_argument("--archive-lock", type=Path, default=signed.DEFAULT_ARCHIVE_LOCK)
    parser.add_argument("--repository-lock", type=Path, default=signed.DEFAULT_REPOSITORY_LOCK)
    parser.add_argument("--build-lock", type=Path, default=signed.DEFAULT_BUILD_LOCK)
    parser.add_argument("--source-lock", type=Path, default=signed.DEFAULT_SOURCE_LOCK)
    parser.add_argument("--createrepo-c", type=Path, default=Path("/usr/bin/createrepo_c"))
    parser.add_argument("--gpg", type=Path, default=Path("/usr/bin/gpg"))
    parser.add_argument("--sudo", type=Path, default=Path("/usr/bin/sudo"))
    parser.add_argument("--dnf5", type=Path, default=Path("/usr/bin/dnf5"))
    parser.add_argument("--rpmkeys", type=Path, default=Path("/usr/bin/rpmkeys"))
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument("--rpm2archive", type=Path, default=Path("/usr/bin/rpm2archive"))
    parser.add_argument("--rm", type=Path, default=Path("/usr/bin/rm"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    execution_values = (
        args.confirm,
        args.work_dir,
        args.output,
        args.receipt,
        args.rpm_postsign_receipt,
        args.signed_rpm,
        args.public_key,
        args.signature,
        args.unsigned_archive_dir,
        args.signed_archive_dir,
        args.result_a,
        args.result_b,
    )
    try:
        if not args.execute:
            if any(value is not None for value in execution_values):
                raise RepositorySmokeError("plan accepts no execution arguments")
            print(
                json.dumps(
                    {
                        "planned": True,
                        "executed": False,
                        "offline_file_repository_only": True,
                        "repo_gpgcheck_required": True,
                        "package_gpgcheck_required": True,
                        "skip_if_unavailable": False,
                        "exact_available_package_count": 322,
                        "empty_root_install": True,
                        "confirmation_required": CONFIRMATION,
                        "device_root_touched": False,
                        "release_ready": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.confirm != CONFIRMATION or any(value is None for value in execution_values[1:]):
            print(json.dumps({"completed": False, "error": "execution gate is incomplete"}))
            return 2
        validate_output(args.output)
        private_directory(args.work_dir, "work directory")
        for tool, label in (
            (args.sudo, "sudo executable"),
            (args.dnf5, "DNF5 executable"),
            (args.rpm, "RPM executable"),
            (args.rpmkeys, "RPM signature verifier"),
            (args.rpm2archive, "RPM archive extractor"),
            (args.createrepo_c, "repository generator"),
            (args.gpg, "OpenPGP verifier"),
            (args.rm, "cleanup executable"),
        ):
            safe_executable(tool, label)
        dnf_identity = rootfs.run_bounded(
            [
                str(args.rpm),
                "-q",
                "--qf",
                "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n",
                "dnf5",
            ],
            "DNF5 package identity",
        ).stdout.decode("utf-8", errors="strict").strip()
        if dnf_identity != DNF5_NVR:
            raise RepositorySmokeError("DNF5 package identity differs")
        signed_report = signed.evaluate(
            args.receipt,
            args.rpm_postsign_receipt,
            args.signed_rpm,
            args.public_key,
            args.signature,
            args.unsigned_archive_dir,
            args.signed_archive_dir,
            args.result_a,
            args.result_b,
            args.archive_lock,
            args.repository_lock,
            args.build_lock,
            args.source_lock,
            args.createrepo_c,
            args.gpg,
            args.rpmkeys,
            args.rpm,
            args.rpm2archive,
        )
        if signed_report.get("repository_ready_for_composition") is not True:
            raise RepositorySmokeError("signed repository gate failed")
        repository_receipt = signed.load_receipt(args.receipt)
        rpm_receipt = signed.postsign.load_receipt(args.rpm_postsign_receipt)
        unsigned_archive = signed.archive.load_lock(args.archive_lock)
        final_archive, aggregate = signed.derive_final_packages(
            unsigned_archive,
            repository_receipt["project_post_sign"]["signed_rpm"],
            rpm_receipt["signing"]["fingerprint"],
        )
        if aggregate != repository_receipt["final_archive"]:
            raise RepositorySmokeError("final archive aggregate differs")
        rootfs_lock = rootfs.load_lock(args.rootfs_lock)
        build_lock = repro.load_lock(args.build_lock)
        observation = execute(
            repository_receipt,
            final_archive,
            args.signed_archive_dir,
            args.result_a,
            args.unsigned_archive_dir,
            args.public_key,
            rootfs_lock,
            build_lock,
            args.work_dir,
            args.sudo,
            args.dnf5,
            args.rpm,
            args.rm,
        )
        post_report = signed.evaluate(
            args.receipt,
            args.rpm_postsign_receipt,
            args.signed_rpm,
            args.public_key,
            args.signature,
            args.unsigned_archive_dir,
            args.signed_archive_dir,
            args.result_a,
            args.result_b,
            args.archive_lock,
            args.repository_lock,
            args.build_lock,
            args.source_lock,
            args.createrepo_c,
            args.gpg,
            args.rpmkeys,
            args.rpm,
            args.rpm2archive,
        )
        if post_report != signed_report:
            raise RepositorySmokeError("signed repository evidence changed during smoke")
        report = {
            "schema": 1,
            "completed": True,
            "accepted": True,
            "repository_receipt_sha256": hashlib.sha256(
                repro.read_regular(args.receipt, signed.MAX_RECEIPT_BYTES)
            ).hexdigest(),
            "repository_repomd_sha256": repository_receipt["repository"]["repomd"][
                "sha256"
            ],
            "repository_signature_sha256": repository_receipt[
                "repository_signature"
            ]["artifact"]["sha256"],
            "project_rpm_sha256": repository_receipt["project_post_sign"][
                "signed_rpm"
            ]["sha256"],
            "final_archive_records_sha256": repository_receipt["final_archive"][
                "records_sha256"
            ],
            "offline_file_repository_only": True,
            "dnf5_nvr": DNF5_NVR,
            "repo_gpgcheck": True,
            "package_gpgcheck": True,
            "skip_if_unavailable": False,
            "available": observation["available"],
            "installed_package_count": observation["installed_package_count"],
            "installed_manifest_sha256": observation["installed_manifest_sha256"],
            "materialized_file_count": observation["materialized_file_count"],
            "dependency_check": "pass",
            "scriptlets_executed": True,
            "triggers_executed": True,
            "disposable_root_cleaned": True,
            "device_root_touched": False,
            "services_started": False,
            "selinux_labels_verified": False,
            "release_ready": False,
        }
        write_output(args.output, report)
    except (
        OSError,
        UnicodeError,
        ValueError,
        RepositorySmokeError,
        rootfs.RootfsTransactionError,
        rootfs.full.FullTransactionError,
        rootfs.repro.ReproError,
        repro.ReproError,
        signed.RepositoryPostSignError,
        signed.archive.ArchiveError,
        signed.archive.auditor.AuditError,
        signed.repository.RepositoryError,
        signed.repository.repro.ReproError,
        signed.postsign.PostSignError,
        signed.postsign.repro.ReproError,
        signed.postsign.auditor.AuditError,
    ) as exc:
        print(json.dumps({"completed": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
