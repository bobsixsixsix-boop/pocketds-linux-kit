#!/usr/bin/env python3
"""Run a confirmation-gated read-only smoke against an offline release root."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Sequence


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
ROOTFS_MODULE = ROOT / "scripts/pds020-userspace-rpm-rootfs-transaction.py"
AUDIT_MODULE = ROOT / "scripts/pds020-release-root-audit.py"
DEFAULT_COMPOSITION_LOCK = ROOT / "packaging/image/composition-lock.json"
BLUEPRINT_RELATIVE = PurePosixPath("packaging/image/pocketds-rootfs.template.toml")
DEFAULT_ROOTFS_LOCK = (
    ROOT / "packaging/pocketds-userspace/rootfs-transaction-lock.pds2.json"
)
DEFAULT_BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.pds2.json"
CONFIRMATION = "RUN READ-ONLY PDS020 ROOTFS SMOKE"
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024
MAX_LOCK_BYTES = 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_TOOL_OUTPUT = 4 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SELINUX_RE = re.compile(rb"^[^:]+:object_r:[A-Za-z0-9_]+:s0\x00?$")
SELINUX_PATHS = (
    "/usr/bin/pocketds-fancontrol",
    "/usr/lib/systemd/system/pocketds-fancontrol.service",
    "/usr/share/alsa/ucm2/Qualcomm/sm8550/APS/HiFi.conf",
)
RELEASE_AUDIT_CHECK_IDS = {
    "sudoers-files-safe",
    "no-broad-nopasswd",
    "only-repository-nopasswd",
    "release-evidence-files",
    "project-license-notice-ready",
    "spdx-3.0.1-jsonld-structure",
    "third-party-source-structure",
    "asset-redistribution-structure",
    "chromium-runtime-provenance",
    "no-private-user-state",
    "no-private-system-state",
}


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rootfs = load_module("pds020_rootfs_smoke_transaction", ROOTFS_MODULE)
audit = load_module("pds020_rootfs_smoke_audit", AUDIT_MODULE)


class RootfsSmokeError(RuntimeError):
    """The offline root or its bound evidence failed closed."""


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RootfsSmokeError(f"{label} fields are incomplete or unsupported")
    return value


def read_regular(path: Path, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RootfsSmokeError(f"{label} is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= maximum
        ):
            raise RootfsSmokeError(f"{label} metadata is unsafe")
        remaining = metadata.st_size
        data = bytearray()
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise RootfsSmokeError(f"{label} changed while reading")
            data.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise RootfsSmokeError(f"{label} grew while reading")
        return bytes(data)
    finally:
        os.close(descriptor)


def hash_regular(path: Path, maximum: int, label: str) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RootfsSmokeError(f"{label} is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= maximum
        ):
            raise RootfsSmokeError(f"{label} metadata is unsafe")
        digest = hashlib.sha256()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise RootfsSmokeError(f"{label} changed while hashing")
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise RootfsSmokeError(f"{label} grew while hashing")
        return metadata.st_size, digest.hexdigest()
    finally:
        os.close(descriptor)


def strict_json(data: bytes, label: str) -> Any:
    try:
        return rootfs.repro.strict_json(data, label)
    except (UnicodeError, ValueError, rootfs.repro.ReproError) as exc:
        raise RootfsSmokeError(f"{label} is invalid JSON") from exc


def safe_executable(path: Path, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RootfsSmokeError(f"{label} is missing or unsafe") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid not in {0, os.getuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise RootfsSmokeError(f"{label} is missing or unsafe")


def run_bounded(
    command: list[str],
    label: str,
    *,
    expected: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RootfsSmokeError(f"{label} could not run") from exc
    if (
        result.returncode not in expected
        or len(result.stdout) > MAX_TOOL_OUTPUT
        or len(result.stderr) > MAX_TOOL_OUTPUT
    ):
        raise RootfsSmokeError(f"{label} failed or exceeded its output bound")
    return result


def root_path(root: Path, absolute: str) -> Path:
    if not isinstance(absolute, str) or not absolute.startswith("/") or "\\" in absolute:
        raise RootfsSmokeError("rootfs contract path is unsafe")
    relative = PurePosixPath(absolute)
    if any(part in {"", ".", ".."} for part in relative.parts[1:]):
        raise RootfsSmokeError("rootfs contract path is unsafe")
    current = root
    for part in relative.parts[1:-1]:
        current = current / part
        if current.is_symlink():
            raise RootfsSmokeError("rootfs contract parent is linked")
        if current.exists() and not current.is_dir():
            raise RootfsSmokeError("rootfs contract parent is not a directory")
    return current / relative.parts[-1]


def repository_file(repository_root: Path, relative: PurePosixPath) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise RootfsSmokeError("repository input path is unsafe")
    current = repository_root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise RootfsSmokeError("repository input parent is unsafe")
    return current / relative.parts[-1]


def parse_os_release(root: Path) -> dict[str, str]:
    os_release = root_path(root, "/etc/os-release")
    if os_release.is_symlink():
        if os.readlink(os_release) != "../usr/lib/os-release":
            raise RootfsSmokeError("os-release link is unsafe")
        os_release = root_path(root, "/usr/lib/os-release")
    data = read_regular(os_release, MAX_TEXT_BYTES, "os-release")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RootfsSmokeError("os-release is not UTF-8") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RootfsSmokeError("os-release line is malformed")
        key, raw = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise RootfsSmokeError("os-release key is invalid or duplicated")
        if raw.startswith('"'):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RootfsSmokeError("os-release value is malformed") from exc
        else:
            value = raw
        if (
            not isinstance(value, str)
            or "\x00" in value
            or "\n" in value
            or "\r" in value
        ):
            raise RootfsSmokeError("os-release value is unsupported")
        values[key] = value
    if values.get("ID") != "fedora" or values.get("VERSION_ID") != "44":
        raise RootfsSmokeError("rootfs is not Fedora 44")
    return {"id": "fedora", "version_id": "44"}


def package_manifest(
    rpm: Path,
    root: Path,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = run_bounded,
) -> tuple[int, str]:
    result = runner(
        [str(rpm), "--root", str(root), "-qa", "--qf", rootfs.full.PACKAGE_QUERY],
        "rootfs package manifest",
    )
    if result.stderr:
        raise RootfsSmokeError("rootfs package manifest wrote stderr")
    lines = result.stdout.splitlines()
    if (
        not lines
        or any(rootfs.full.PACKAGE_LINE_RE.fullmatch(line) is None for line in lines)
        or len(lines) != len(set(lines))
    ):
        raise RootfsSmokeError("rootfs package manifest is malformed or duplicated")
    if any(line.rsplit(b"\t", 1)[-1] not in {b"aarch64", b"noarch"} for line in lines):
        raise RootfsSmokeError("rootfs package architecture differs")
    canonical = b"\n".join(sorted(lines)) + b"\n"
    return len(lines), hashlib.sha256(canonical).hexdigest()


def require_regular_payload(path: Path, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise RootfsSmokeError(f"{label} is missing") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise RootfsSmokeError(f"{label} is unsafe")


def verify_installed_contract(
    lock: dict[str, Any],
    build_lock: dict[str, Any],
    rpm: Path,
    root: Path,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = run_bounded,
) -> None:
    package = build_lock["package"]
    expected_nevra = (
        f"{package['name']}-{package['version']}-{package['release']}.{package['arch']}\n"
    ).encode()
    identity = runner(
        [
            str(rpm),
            "--root",
            str(root),
            "-q",
            "--qf",
            "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n",
            package["name"],
        ],
        "rootfs project package identity",
    )
    if identity.stderr or identity.stdout != expected_nevra:
        raise RootfsSmokeError("rootfs project package identity differs")
    verification = runner(
        [str(rpm), "--root", str(root), "-V", package["name"]],
        "rootfs project package verification",
        expected=(0, 1),
    )
    if verification.stderr or verification.stdout.decode(
        "utf-8", errors="strict"
    ).splitlines() != lock["installed_contract"]["rpm_verify_output"]:
        raise RootfsSmokeError("rootfs project package verification differs")
    for legacy in lock["installed_contract"]["legacy_packages_absent"]:
        result = runner(
            [str(rpm), "--root", str(root), "-q", legacy],
            "rootfs legacy package absence",
            expected=(1,),
        )
        if result.stderr or result.stdout != f"package {legacy} is not installed\n".encode():
            raise RootfsSmokeError("rootfs legacy package query is ambiguous")
    for absolute in lock["installed_contract"]["legacy_paths_absent"]:
        path = root_path(root, absolute)
        if path.exists() or path.is_symlink():
            raise RootfsSmokeError("rootfs legacy payload remains")
    for absolute in lock["installed_contract"]["critical_payload_paths"]:
        require_regular_payload(root_path(root, absolute), "critical rootfs payload")
    fan = root_path(root, "/usr/bin/pocketds-fancontrol")
    _, fan_sha256 = hash_regular(fan, MAX_TEXT_BYTES, "fan controller")
    if fan_sha256 != build_lock["payload_audit"]["fan_controller_sha256"]:
        raise RootfsSmokeError("rootfs fan controller differs")
    wants_root = root_path(root, "/etc/systemd/system/multi-user.target.wants/x").parent
    if wants_root.is_symlink() or not wants_root.is_dir():
        raise RootfsSmokeError("rootfs system wants directory is unsafe")
    for unit, target in lock["installed_contract"]["system_wants"].items():
        link = wants_root / unit
        if not link.is_symlink() or os.readlink(link) != target:
            raise RootfsSmokeError("rootfs system wants link differs")


def verify_selinux_labels(root: Path) -> int:
    count = 0
    for absolute in SELINUX_PATHS:
        path = root_path(root, absolute)
        require_regular_payload(path, "SELinux-labelled payload")
        label = read_selinux_label(path)
        if SELINUX_RE.fullmatch(label) is None:
            raise RootfsSmokeError("rootfs SELinux label is malformed")
        count += 1
    return count


def read_selinux_label(path: Path) -> bytes:
    getter = getattr(os, "getxattr", None)
    if getter is None:
        raise RootfsSmokeError("SELinux xattr inspection is unavailable")
    try:
        return getter(path, "security.selinux", follow_symlinks=False)
    except OSError as exc:
        raise RootfsSmokeError("rootfs SELinux label is missing") from exc


def canonical_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()


def verify(
    root: Path,
    artifact: Path,
    repository_root: Path,
    composition_lock_path: Path,
    rootfs_lock_path: Path,
    build_lock_path: Path,
    rpm: Path,
    *,
    max_home_entries: int = 200_000,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = run_bounded,
) -> dict[str, Any]:
    try:
        repository_root = repository_root.resolve(strict=True)
        if not (repository_root / ".git").exists():
            raise RootfsSmokeError("repository root is invalid")
        resolved_root, root_mode = audit.resolve_target(root, None)
    except (OSError, audit.AuditError) as exc:
        raise RootfsSmokeError("offline root is missing or unsafe") from exc
    if root_mode != "offline-mounted-root":
        raise RootfsSmokeError("live root is forbidden")
    root_metadata = resolved_root.stat()
    root_identity = (root_metadata.st_dev, root_metadata.st_ino)
    safe_executable(rpm, "RPM query executable")
    composition_data = read_regular(
        composition_lock_path, MAX_LOCK_BYTES, "composition lock"
    )
    rootfs_data = read_regular(rootfs_lock_path, MAX_LOCK_BYTES, "rootfs lock")
    build_data = read_regular(build_lock_path, MAX_LOCK_BYTES, "build lock")
    try:
        rootfs_lock = rootfs.load_lock(rootfs_lock_path)
        build_lock = rootfs.repro.load_lock(build_lock_path)
    except (rootfs.RootfsTransactionError, rootfs.repro.ReproError) as exc:
        raise RootfsSmokeError("rootfs or build lock differs") from exc
    if hashlib.sha256(build_data).hexdigest() != rootfs_lock["artifact"][
        "build_lock_sha256"
    ]:
        raise RootfsSmokeError("rootfs lock does not bind the build lock")
    if (
        read_regular(rootfs_lock_path, MAX_LOCK_BYTES, "rootfs lock") != rootfs_data
        or read_regular(build_lock_path, MAX_LOCK_BYTES, "build lock") != build_data
    ):
        raise RootfsSmokeError("rootfs or build lock changed during validation")
    composition = exact_object(
        strict_json(composition_data, "composition lock"),
        {"schema", "builder", "boundary", "blueprint", "identity_policy", "gates"},
        "composition lock",
    )
    builder = exact_object(
        composition["builder"],
        {
            "reference",
            "source",
            "distro",
            "arch",
            "image_type",
            "bootmode",
            "default_filename",
        },
        "composition builder",
    )
    boundary = exact_object(
        composition["boundary"],
        {
            "artifact_role",
            "flashable",
            "partition_table",
            "modifies_boot_chain",
            "build_on_daily_device",
        },
        "composition boundary",
    )
    identity_policy = exact_object(
        composition["identity_policy"],
        {
            "users_embedded",
            "passwords_embedded",
            "ssh_keys_embedded",
            "network_profiles_embedded",
            "hostname_embedded",
            "machine_id_initialized",
            "ssh_host_keys_initialized",
        },
        "composition identity policy",
    )
    if (
        type(composition["schema"]) is not int
        or composition["schema"] != 1
        or builder["distro"] != "fedora-44"
        or builder["arch"] != "aarch64"
        or builder["image_type"] != "generic-container"
        or builder["bootmode"] != "none"
        or builder["default_filename"] != "container.tar"
        or boundary["artifact_role"] != "rootfs-staging-only"
        or any(
            boundary[name] is not False
            for name in (
                "flashable",
                "partition_table",
                "modifies_boot_chain",
                "build_on_daily_device",
            )
        )
        or any(value is not False for value in identity_policy.values())
    ):
        raise RootfsSmokeError("composition lock is not the safe rootfs target")
    blueprint = exact_object(
        composition["blueprint"],
        {
            "path",
            "size",
            "sha256",
            "name",
            "description",
            "version",
            "distro",
            "packages",
        },
        "composition blueprint",
    )
    if blueprint["path"] != BLUEPRINT_RELATIVE.as_posix():
        raise RootfsSmokeError("composition blueprint path differs")
    blueprint_data = read_regular(
        repository_file(repository_root, BLUEPRINT_RELATIVE),
        MAX_LOCK_BYTES,
        "composition blueprint",
    )
    if (
        type(blueprint["size"]) is not int
        or len(blueprint_data) != blueprint["size"]
        or not isinstance(blueprint["sha256"], str)
        or hashlib.sha256(blueprint_data).hexdigest() != blueprint["sha256"]
    ):
        raise RootfsSmokeError("composition blueprint bytes differ")
    composition_policy_sha256 = canonical_sha256(
        {
            "builder": builder,
            "boundary": boundary,
            "blueprint": blueprint,
            "identity_policy": identity_policy,
        }
    )
    artifact_size, artifact_sha256 = hash_regular(
        artifact, MAX_ARTIFACT_BYTES, "rootfs artifact"
    )
    os_release = parse_os_release(resolved_root)
    package_count, package_sha256 = package_manifest(
        rpm, resolved_root, runner=runner
    )
    expected_stage = rootfs_lock["installed_stage"]
    if (
        package_count != expected_stage["package_count"]
        or package_sha256 != expected_stage["package_manifest_sha256"]
    ):
        raise RootfsSmokeError("rootfs package manifest differs")
    verify_installed_contract(
        rootfs_lock, build_lock, rpm, resolved_root, runner=runner
    )
    selinux_count = verify_selinux_labels(resolved_root)
    try:
        audit_report = audit.run_audit(
            resolved_root, repository_root, max_home_entries
        )
    except (OSError, audit.AuditError) as exc:
        raise RootfsSmokeError("mounted-root audit failed") from exc
    checks = audit_report.get("checks")
    check_ids = (
        [item.get("check_id") for item in checks if isinstance(item, dict)]
        if isinstance(checks, list)
        else []
    )
    if (
        audit_report.get("release_ready") is not True
        or audit_report.get("blockers") != []
        or not isinstance(checks, list)
        or len(checks) != len(RELEASE_AUDIT_CHECK_IDS)
        or any(item.get("status") != "pass" for item in checks if isinstance(item, dict))
        or not all(isinstance(item, dict) for item in checks)
        or len(check_ids) != len(set(check_ids))
        or set(check_ids) != RELEASE_AUDIT_CHECK_IDS
    ):
        raise RootfsSmokeError("mounted-root audit did not pass")
    audit_report["root_mode"] = root_mode
    final_artifact = hash_regular(artifact, MAX_ARTIFACT_BYTES, "rootfs artifact")
    final_packages = package_manifest(rpm, resolved_root, runner=runner)
    final_root_metadata = resolved_root.stat()
    if (
        final_artifact != (artifact_size, artifact_sha256)
        or final_packages != (package_count, package_sha256)
        or read_regular(
            composition_lock_path, MAX_LOCK_BYTES, "composition lock"
        )
        != composition_data
        or read_regular(rootfs_lock_path, MAX_LOCK_BYTES, "rootfs lock")
        != rootfs_data
        or read_regular(build_lock_path, MAX_LOCK_BYTES, "build lock")
        != build_data
        or (final_root_metadata.st_dev, final_root_metadata.st_ino) != root_identity
    ):
        raise RootfsSmokeError("rootfs smoke inputs changed during validation")
    return {
        "schema": 1,
        "completed": True,
        "accepted": True,
        "artifact": {
            "role": "osbuild-generic-container-tar",
            "size": artifact_size,
            "sha256": artifact_sha256,
        },
        "composition_policy_sha256": composition_policy_sha256,
        "rootfs_lock_sha256": hashlib.sha256(rootfs_data).hexdigest(),
        "build_lock_sha256": hashlib.sha256(build_data).hexdigest(),
        "os_release": os_release,
        "architecture": "aarch64",
        "package_count": package_count,
        "package_manifest_sha256": package_sha256,
        "installed_contract": "pass",
        "selinux_label_count": selinux_count,
        "selinux_labels_verified": True,
        "mounted_root_audit_sha256": canonical_sha256(audit_report),
        "mounted_root_check_count": len(checks),
        "mounted_root_audit_passed": True,
        "network": False,
        "services_started": False,
        "device_root_touched": False,
        "release_ready": False,
    }


def validate_output(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise RootfsSmokeError("output already exists or is unsafe")
    try:
        metadata = os.lstat(path.parent)
    except OSError as exc:
        raise RootfsSmokeError("output parent is missing or unsafe") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RootfsSmokeError("output parent is unsafe")


def write_output(path: Path, report: dict[str, Any]) -> None:
    content = (json.dumps(report, sort_keys=True) + "\n").encode()
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:  # pragma: no cover
                raise RootfsSmokeError("short report write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--composition-lock", type=Path, default=DEFAULT_COMPOSITION_LOCK)
    parser.add_argument("--rootfs-lock", type=Path, default=DEFAULT_ROOTFS_LOCK)
    parser.add_argument("--build-lock", type=Path, default=DEFAULT_BUILD_LOCK)
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument("--max-home-entries", type=int, default=200_000)
    args = parser.parse_args(argv)
    if not 1_000 <= args.max_home_entries <= 2_000_000:
        parser.error("--max-home-entries must be between 1000 and 2000000")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    execution_values = (args.confirm, args.root, args.artifact, args.output)
    try:
        if not args.execute:
            if any(value is not None for value in execution_values):
                raise RootfsSmokeError("plan accepts no execution arguments")
            print(
                json.dumps(
                    {
                        "planned": True,
                        "executed": False,
                        "offline_root_only": True,
                        "read_only": True,
                        "rpm_query_only": True,
                        "selinux_labels_required": True,
                        "mounted_root_audit_required": True,
                        "confirmation_required": CONFIRMATION,
                        "network": False,
                        "services_started": False,
                        "device_root_touched": False,
                        "release_ready": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.confirm != CONFIRMATION or any(
            value is None for value in execution_values[1:]
        ):
            raise RootfsSmokeError("execution gate is incomplete")
        validate_output(args.output)
        report = verify(
            args.root,
            args.artifact,
            args.repository_root,
            args.composition_lock,
            args.rootfs_lock,
            args.build_lock,
            args.rpm,
            max_home_entries=args.max_home_entries,
        )
        write_output(args.output, report)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, UnicodeError, ValueError, RootfsSmokeError) as exc:
        print(json.dumps({"completed": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
