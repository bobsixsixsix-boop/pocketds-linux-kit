#!/usr/bin/env python3
"""Audit one DNF5 stored transaction without running DNF or RPM.

This tool deliberately has no execution mode.  A successful audit means only
that the exact DNF5 1.0 stored transaction is confined to the ordinary
userspace ring; it never authorizes replay or installation.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any


REPORT_SCHEMA = "pocketds.userspace-update-plan.v1"
DNF_TRANSACTION_VERSION = "1.0"
DNF_FORMAT_AUDIT_COMMIT = "3cc84ee1540f1e40a29b19414a8f3e8521e5f3ff"
MAX_TRANSACTION_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024
MAX_TOTAL_PACKAGE_BYTES = 64 * 1024 * 1024 * 1024
MAX_PACKAGES = 20_000

# Keep this fail-closed evaluator boundary byte-for-byte aligned with the one
# generated DNF default in components/system/90-pocketds-hardware-protection.conf.
# scripts/render-update-protection.py includes ALL rings below, including boot.
# tests/update-policy.py enforces both membership and ordering.
HARDWARE_PATTERNS = (
    "kernel*",
    "pocketds-*",
    "linux-firmware*",
    "qcom-firmware*",
)
GRAPHICS_PATTERNS = (
    "mesa*",
    "libdrm*",
    "libglvnd*",
    "libva*",
    "libvdpau*",
    "kwin*",
    "plasma*",
    "qt5-qtwayland*",
    "qt6-qtbase*",
    "qt6-qtdeclarative*",
    "qt6-qtwayland*",
    "kwayland*",
    "wayland*",
    "egl-wayland*",
    "xwayland*",
    "xorg-x11-server*",
    "xorg-x11-drv-*",
    "vulkan*",
)
BOOT_PATTERNS = (
    "arm-trusted-firmware*",
    "bootupd*",
    "dracut*",
    "edk2*",
    "efibootmgr*",
    "fwupd*",
    "grub*",
    "grub2*",
    "grubby*",
    "kexec-tools*",
    "mokutil*",
    "sbsigntools*",
    "shim*",
    "systemd*",
    "systemd-boot*",
    "u-boot*",
    "uboot*",
)
PROTECTED_RINGS = {
    "hardware": HARDWARE_PATTERNS,
    "graphics": GRAPHICS_PATTERNS,
    "boot": BOOT_PATTERNS,
}

KNOWN_REASONS = {
    "None",
    "Dependency",
    "User",
    "Clean",
    "Weak Dependency",
    "Group",
    "External User",
}
ALLOWED_ACTIONS = {"Upgrade", "Replaced", "Install"}
INBOUND_ACTIONS = {"Upgrade", "Install"}
SAFE_DEPENDENCY_REASONS = {"Dependency", "Weak Dependency"}
# Pinned DNF5 transaction_sr.cpp always emits "version", but emits each of
# rpms/groups/environments only when its count is non-zero.  They are therefore
# optional non-empty arrays, never required or accepted as empty placeholders;
# every unknown key still fails.
TOP_LEVEL_KEYS = {"version", "rpms", "groups", "environments"}
RPM_KEYS = {"nevra", "action", "reason", "repo_id", "package_path", "group_id"}
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~^%-]*$")
SAFE_ARCH = re.compile(r"^[A-Za-z0-9_]+$")
SAFE_REPO = re.compile(r"^@stored_transaction\([A-Za-z0-9._:+-]+\)$")
SAFE_RPM_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+~^:%-]*\.rpm$")
FILE_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_uid",
    "st_gid",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)
DIRECTORY_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_uid",
    "st_gid",
    "st_nlink",
    "st_mtime_ns",
    "st_ctime_ns",
)

UNRESOLVED_EXECUTION_GATES = (
    "the generator DNF5 build/version is not recorded by transaction format 1.0",
    "DNF5 stored transaction 1.0 does not record package vendor identity",
    "RPM payload signatures and NEVRA-to-payload identity were not verified",
    "available disk space and post-update health checks were not verified",
    "this audit was not rebound to a fresh solver run immediately before installation",
)


class PlanError(RuntimeError):
    """The stored transaction is malformed, ambiguous, or unsafe to inspect."""


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep command-line failures machine-readable too."""

    def error(self, message: str) -> None:
        print(
            json.dumps(
                rejected_report(f"command line: {message}"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise SystemExit(2)


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PlanError(f'duplicate JSON key: "{key}"')
        value[key] = item
    return value


def parse_json(content: bytes) -> dict[str, Any]:
    if not content or len(content) > MAX_TRANSACTION_BYTES:
        raise PlanError("transaction JSON is empty or oversized")
    try:
        payload = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                PlanError(f"non-finite JSON value: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanError("transaction is not one strict UTF-8 JSON document") from exc
    if type(payload) is not dict:
        raise PlanError("transaction JSON root is not an object")
    return payload


def same_metadata(
    before: os.stat_result, after: os.stat_result, fields: tuple[str, ...]
) -> bool:
    return all(getattr(before, field) == getattr(after, field) for field in fields)


def check_regular_metadata(
    metadata: os.stat_result, *, maximum: int, label: str
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid not in {0, os.getuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not 0 < metadata.st_size <= maximum
    ):
        raise PlanError(f"{label} has unsafe identity or bounds")


def check_directory_metadata(metadata: os.stat_result, *, label: str) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid not in {0, os.getuid()}
        or mode not in {0o500, 0o700}
    ):
        raise PlanError(f"{label} must be an owner-only mode-0500/0700 directory")


def read_regular_at(
    directory_fd: int,
    name: str,
    *,
    maximum: int,
    label: str,
) -> tuple[bytes, os.stat_result]:
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise PlanError(f"{label} is unavailable") from exc
    check_regular_metadata(before, maximum=maximum, label=label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise PlanError(f"{label} could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not same_metadata(before, opened, FILE_IDENTITY_FIELDS):
            raise PlanError(f"{label} identity changed while opening")
        content = bytearray()
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise PlanError(f"{label} changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise PlanError(f"{label} grew while reading")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not same_metadata(opened, after, FILE_IDENTITY_FIELDS):
        raise PlanError(f"{label} changed while reading")
    return bytes(content), after


def hash_regular_at(
    directory_fd: int,
    name: str,
    *,
    maximum: int,
    label: str,
) -> tuple[str, int, os.stat_result]:
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise PlanError(f"{label} is unavailable") from exc
    check_regular_metadata(
        before, maximum=min(maximum, MAX_PACKAGE_BYTES), label=label
    )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise PlanError(f"{label} could not be opened safely") from exc
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if not same_metadata(before, opened, FILE_IDENTITY_FIELDS):
            raise PlanError(f"{label} identity changed while opening")
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise PlanError(f"{label} changed while hashing")
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise PlanError(f"{label} grew while hashing")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not same_metadata(opened, after, FILE_IDENTITY_FIELDS):
        raise PlanError(f"{label} changed while hashing")
    return digest.hexdigest(), after.st_size, after


def transaction_location(argument: Path) -> tuple[Path, str]:
    try:
        metadata = os.lstat(argument)
    except OSError as exc:
        raise PlanError("transaction path is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise PlanError("transaction path must not be a symlink")
    if stat.S_ISDIR(metadata.st_mode):
        return argument, "transaction.json"
    if stat.S_ISREG(metadata.st_mode):
        return argument.parent, argument.name
    raise PlanError("transaction path is neither a directory nor a regular file")


class TransactionSource:
    """Hold directory FDs so one audit cannot mix evidence across replacements."""

    def __init__(self, argument: Path) -> None:
        self.directory_path, self.transaction_name = transaction_location(argument)
        self.directory_fd, self.directory_metadata = self._open_directory_path(
            self.directory_path, "transaction directory"
        )
        self.transaction_metadata: os.stat_result | None = None
        self.packages_fd: int | None = None
        self.packages_metadata: os.stat_result | None = None
        self.payload_metadata: dict[str, os.stat_result] = {}

    @staticmethod
    def _open_directory_path(path: Path, label: str) -> tuple[int, os.stat_result]:
        try:
            before = os.lstat(path)
        except OSError as exc:
            raise PlanError(f"{label} is unavailable") from exc
        check_directory_metadata(before, label=label)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise PlanError(f"{label} could not be opened safely") from exc
        opened = os.fstat(descriptor)
        if not same_metadata(before, opened, DIRECTORY_IDENTITY_FIELDS):
            os.close(descriptor)
            raise PlanError(f"{label} identity changed while opening")
        return descriptor, opened

    def _open_packages(self) -> None:
        if self.packages_fd is not None:
            return
        try:
            before = os.stat(
                "packages", dir_fd=self.directory_fd, follow_symlinks=False
            )
        except OSError as exc:
            raise PlanError("stored packages directory is unavailable") from exc
        check_directory_metadata(before, label="stored packages directory")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            descriptor = os.open("packages", flags, dir_fd=self.directory_fd)
        except OSError as exc:
            raise PlanError("stored packages directory could not be opened safely") from exc
        opened = os.fstat(descriptor)
        if not same_metadata(before, opened, DIRECTORY_IDENTITY_FIELDS):
            os.close(descriptor)
            raise PlanError("stored packages directory identity changed while opening")
        self.packages_fd = descriptor
        self.packages_metadata = opened

    def read_transaction(self) -> bytes:
        content, metadata = read_regular_at(
            self.directory_fd,
            self.transaction_name,
            maximum=MAX_TRANSACTION_BYTES,
            label="transaction JSON",
        )
        self.transaction_metadata = metadata
        return content

    def hash_package(self, filename: str, *, maximum: int) -> tuple[str, int]:
        self._open_packages()
        assert self.packages_fd is not None
        digest, size, metadata = hash_regular_at(
            self.packages_fd,
            filename,
            maximum=maximum,
            label=f"RPM payload {filename}",
        )
        self.payload_metadata[filename] = metadata
        return digest, size

    def validate_unchanged(self) -> None:
        current_directory = os.fstat(self.directory_fd)
        if not same_metadata(
            self.directory_metadata, current_directory, DIRECTORY_IDENTITY_FIELDS
        ):
            raise PlanError("transaction directory changed during audit")
        try:
            current_path = os.lstat(self.directory_path)
        except OSError as exc:
            raise PlanError("transaction directory path vanished during audit") from exc
        if not same_metadata(
            self.directory_metadata, current_path, DIRECTORY_IDENTITY_FIELDS
        ):
            raise PlanError("transaction directory path changed during audit")

        if self.transaction_metadata is not None:
            try:
                current_transaction = os.stat(
                    self.transaction_name,
                    dir_fd=self.directory_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise PlanError("transaction JSON vanished during audit") from exc
            if not same_metadata(
                self.transaction_metadata, current_transaction, FILE_IDENTITY_FIELDS
            ):
                raise PlanError("transaction JSON changed during audit")

        if self.packages_fd is not None and self.packages_metadata is not None:
            current_packages = os.fstat(self.packages_fd)
            if not same_metadata(
                self.packages_metadata, current_packages, DIRECTORY_IDENTITY_FIELDS
            ):
                raise PlanError("stored packages directory changed during audit")
            try:
                current_packages_path = os.stat(
                    "packages", dir_fd=self.directory_fd, follow_symlinks=False
                )
            except OSError as exc:
                raise PlanError("stored packages directory vanished during audit") from exc
            if not same_metadata(
                self.packages_metadata,
                current_packages_path,
                DIRECTORY_IDENTITY_FIELDS,
            ):
                raise PlanError("stored packages directory path changed during audit")
            for filename, expected in self.payload_metadata.items():
                try:
                    current_payload = os.stat(
                        filename,
                        dir_fd=self.packages_fd,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise PlanError(f"RPM payload {filename} vanished during audit") from exc
                if not same_metadata(expected, current_payload, FILE_IDENTITY_FIELDS):
                    raise PlanError(f"RPM payload {filename} changed during audit")

    def close(self) -> None:
        if self.packages_fd is not None:
            os.close(self.packages_fd)
            self.packages_fd = None
        os.close(self.directory_fd)


def parse_nevra(nevra: object) -> dict[str, str]:
    if type(nevra) is not str or not 5 <= len(nevra) <= 1024:
        raise PlanError("RPM NEVRA is missing or oversized")
    if any(ord(character) < 33 or ord(character) == 127 for character in nevra):
        raise PlanError(f"RPM NEVRA contains control or whitespace: {nevra!r}")
    try:
        name_version, release_arch = nevra.rsplit("-", 1)
        name, epoch_version = name_version.rsplit("-", 1)
        release, arch = release_arch.rsplit(".", 1)
    except ValueError as exc:
        raise PlanError(f"RPM NEVRA is incomplete: {nevra}") from exc
    if ":" in epoch_version:
        epoch, version = epoch_version.split(":", 1)
        if not epoch.isdigit():
            raise PlanError(f"RPM epoch is invalid: {nevra}")
    else:
        epoch, version = "0", epoch_version
    if (
        not SAFE_COMPONENT.fullmatch(name)
        or not SAFE_COMPONENT.fullmatch(version)
        or not SAFE_COMPONENT.fullmatch(release)
        or not SAFE_ARCH.fullmatch(arch)
    ):
        raise PlanError(f"RPM NEVRA contains an unsupported component: {nevra}")
    return {
        "name": name,
        "epoch": epoch,
        "version": version,
        "release": release,
        "arch": arch,
    }


def protected_matches(name: str) -> list[dict[str, str]]:
    normalized = name.casefold()
    matches = []
    for ring, patterns in PROTECTED_RINGS.items():
        for pattern in patterns:
            if fnmatch.fnmatchcase(normalized, pattern.casefold()):
                matches.append({"ring": ring, "pattern": pattern})
    return matches


def safe_package_path(value: object) -> tuple[str, str]:
    if type(value) is not str or not value or "\x00" in value or len(value) > 1024:
        raise PlanError("inbound RPM package_path is missing or invalid")
    relative = PurePosixPath(value)
    parts = tuple(part for part in relative.parts if part != ".")
    if relative.is_absolute() or parts[:1] != ("packages",) or len(parts) != 2:
        raise PlanError(f"inbound RPM package_path escapes packages/: {value}")
    normalized = "/".join(parts)
    if value not in {normalized, f"./{normalized}"}:
        raise PlanError(f"inbound RPM package_path is not canonical: {value}")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or not SAFE_RPM_FILENAME.fullmatch(parts[-1])
    ):
        raise PlanError(f"inbound RPM package_path is unsafe: {value}")
    return parts[-1], normalized


def policy_report() -> dict[str, Any]:
    return {
        "allowed_actions": sorted(ALLOWED_ACTIONS),
        "dependency_install_reasons": sorted(SAFE_DEPENDENCY_REASONS),
        "bounds": {
            "transaction_bytes": MAX_TRANSACTION_BYTES,
            "package_bytes_each": MAX_PACKAGE_BYTES,
            "package_bytes_total": MAX_TOTAL_PACKAGE_BYTES,
            "package_records": MAX_PACKAGES,
        },
        "directory_modes": ["0500", "0700"],
        "protected_patterns": {
            ring: list(patterns) for ring, patterns in PROTECTED_RINGS.items()
        },
    }


def require_string(record: dict[str, Any], key: str, index: int) -> str:
    value = record.get(key)
    if type(value) is not str or not value or len(value) > 1024 or "\x00" in value:
        raise PlanError(f"RPM #{index} has invalid {key}")
    return value


def evaluate_payload(
    payload: dict[str, Any],
    *,
    source: TransactionSource,
    source_hash: str,
) -> dict[str, Any]:
    unknown_top = sorted(set(payload) - TOP_LEVEL_KEYS)
    if unknown_top:
        raise PlanError(f"unsupported transaction keys: {', '.join(unknown_top)}")
    if payload.get("version") != DNF_TRANSACTION_VERSION:
        raise PlanError("only the exact DNF5 stored transaction version 1.0 is supported")
    for collection in ("rpms", "groups", "environments"):
        value = payload.get(collection, [])
        if type(value) is not list:
            raise PlanError(f"transaction {collection} is not an array")
        if collection in payload and not value:
            raise PlanError(
                f"empty transaction {collection} is not emitted by the pinned DNF5 serializer"
            )
    groups = payload.get("groups", [])
    environments = payload.get("environments", [])
    raw_rpms = payload.get("rpms", [])
    if len(raw_rpms) > MAX_PACKAGES:
        raise PlanError("transaction contains too many RPM records")

    rejection_reasons: list[str] = []
    if groups:
        rejection_reasons.append("package-group actions are outside the daily userspace ring")
    if environments:
        rejection_reasons.append("environment actions are outside the daily userspace ring")

    records: list[dict[str, Any]] = []
    artifacts_seen: set[str] = set()
    identities_seen: set[tuple[str, str]] = set()
    protected: list[dict[str, Any]] = []
    upgrades: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    replaced: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    payload_bytes_seen = 0
    update_shape_valid = True

    for index, raw_record in enumerate(raw_rpms):
        if type(raw_record) is not dict:
            raise PlanError(f"RPM #{index} is not an object")
        unknown = sorted(set(raw_record) - RPM_KEYS)
        if unknown:
            raise PlanError(f"RPM #{index} has unsupported keys: {', '.join(unknown)}")
        nevra = require_string(raw_record, "nevra", index)
        action = require_string(raw_record, "action", index)
        reason = require_string(raw_record, "reason", index)
        repo_id = require_string(raw_record, "repo_id", index)
        parsed = parse_nevra(nevra)
        if action not in ALLOWED_ACTIONS:
            rejection_reasons.append(f"{nevra}: action {action!r} is not an update-only action")
        if reason not in KNOWN_REASONS:
            raise PlanError(f"{nevra}: unknown DNF5 reason {reason!r}")
        if "group_id" in raw_record:
            rejection_reasons.append(f"{nevra}: group-bound reason changes are not allowed")

        identity = (nevra, action)
        if identity in identities_seen:
            raise PlanError(f"duplicate RPM action record: {action} {nevra}")
        identities_seen.add(identity)

        matches = protected_matches(parsed["name"])
        if matches:
            protected.append({"name": parsed["name"], "nevra": nevra, "matches": matches})

        package_hash = None
        package_size = None
        normalized_path = None
        if action in INBOUND_ACTIONS:
            if not SAFE_REPO.fullmatch(repo_id):
                rejection_reasons.append(
                    f"{nevra}: inbound repository is not an official --store repository id"
                )
            try:
                filename, normalized_path = safe_package_path(
                    raw_record.get("package_path")
                )
                if normalized_path in artifacts_seen:
                    raise PlanError(f"duplicate inbound RPM payload path: {normalized_path}")
                artifacts_seen.add(normalized_path)
                package_hash, package_size = source.hash_package(
                    filename, maximum=MAX_TOTAL_PACKAGE_BYTES - payload_bytes_seen
                )
                payload_bytes_seen += package_size
            except PlanError as exc:
                rejection_reasons.append(f"{nevra}: {exc}")
        elif action == "Replaced":
            if repo_id != "@System":
                rejection_reasons.append(f"{nevra}: replaced RPM is not bound to @System")
            if "package_path" in raw_record:
                rejection_reasons.append(f"{nevra}: outbound replacement unexpectedly has a payload")
        elif "package_path" in raw_record:
            rejection_reasons.append(f"{nevra}: unsupported action unexpectedly has a payload")

        record = {
            "source_index": index,
            "name": parsed["name"],
            "epoch": parsed["epoch"],
            "version": parsed["version"],
            "release": parsed["release"],
            "arch": parsed["arch"],
            "nevra": nevra,
            "action": action,
            "reason": reason,
            "repo_id": repo_id,
            "package_path": normalized_path,
            "package_sha256": package_hash,
            "package_size": package_size,
        }
        records.append(record)
        key = (parsed["name"].casefold(), parsed["arch"].casefold())
        if action == "Upgrade":
            upgrades[key].append(record)
        elif action == "Replaced":
            replaced[key].append(record)
        elif action == "Install" and reason not in SAFE_DEPENDENCY_REASONS:
            update_shape_valid = False
            rejection_reasons.append(
                f"{nevra}: a new package is not a solver dependency of an upgrade"
            )

    if protected:
        for item in protected:
            rings = ",".join(sorted({match["ring"] for match in item["matches"]}))
            rejection_reasons.append(f"{item['nevra']}: protected {rings} package")

    if raw_rpms and not upgrades:
        update_shape_valid = False
        rejection_reasons.append("non-empty transaction contains no Upgrade action")
    for key in sorted(set(upgrades) | set(replaced)):
        inbound = upgrades.get(key, [])
        outbound = replaced.get(key, [])
        label = f"{key[0]}.{key[1]}"
        if len(inbound) != 1 or len(outbound) != 1:
            update_shape_valid = False
            rejection_reasons.append(
                f"{label}: Upgrade/Replaced pair must contain exactly one record each"
            )
        elif inbound[0]["nevra"] == outbound[0]["nevra"]:
            update_shape_valid = False
            rejection_reasons.append(f"{label}: upgrade does not change NEVRA")

    rejection_reasons = sorted(set(rejection_reasons))
    accepted = not rejection_reasons
    action_counts = Counter(record["action"] for record in records)
    package_bytes = sum(
        record["package_size"] for record in records if record["package_size"] is not None
    )
    return {
        "schema": REPORT_SCHEMA,
        "decision": "AUDITED_NOT_RUN" if accepted else "REJECTED_NOT_RUN",
        "accepted_for_userspace_review": accepted,
        "execution_authorized": False,
        "execution_state": "NOT RUN",
        "dnf5_invoked": False,
        "rpm_transaction_invoked": False,
        "source": {
            "format": "dnf5-stored-transaction",
            "format_version": DNF_TRANSACTION_VERSION,
            "format_contract_audited_at_upstream_commit": DNF_FORMAT_AUDIT_COMMIT,
            "transaction_json_sha256": source_hash,
        },
        "policy": policy_report(),
        "gates": {
            "transaction_schema_exact": True,
            "source_tree_stable": True,
            "only_userspace_packages": not protected,
            "only_update_actions": not any(
                record["action"] not in ALLOWED_ACTIONS for record in records
            ),
            "update_shape_valid": update_shape_valid,
            "no_groups_or_environments": not groups and not environments,
            "payload_files_hashed": all(
                record["package_sha256"] is not None
                for record in records
                if record["action"] in INBOUND_ACTIONS
            ),
            "generator_version_verified": False,
            "vendor_change_verified": False,
            "rpm_signatures_verified": False,
            "nevra_payload_binding_verified": False,
            "disk_space_verified": False,
            "fresh_solver_state_verified": False,
            "post_update_health_verified": False,
        },
        "counts": {
            "packages": len(records),
            "inbound_packages": sum(
                1 for record in records if record["action"] in INBOUND_ACTIONS
            ),
            "payload_bytes": package_bytes,
            "actions": dict(sorted(action_counts.items())),
            "protected_packages": len(protected),
        },
        "packages": sorted(
            records,
            key=lambda record: (
                record["name"].casefold(),
                record["arch"].casefold(),
                record["action"],
                record["nevra"],
            ),
        ),
        "protected_packages": sorted(protected, key=lambda item: item["nevra"]),
        "rejection_reasons": rejection_reasons,
        "unresolved_execution_gates": list(UNRESOLVED_EXECUTION_GATES),
    }


def rejected_report(message: str, source_hash: str | None = None) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "decision": "REJECTED_NOT_RUN",
        "accepted_for_userspace_review": False,
        "execution_authorized": False,
        "execution_state": "NOT RUN",
        "dnf5_invoked": False,
        "rpm_transaction_invoked": False,
        "source": {
            "format": "dnf5-stored-transaction",
            "format_version": None,
            "format_contract_audited_at_upstream_commit": DNF_FORMAT_AUDIT_COMMIT,
            "transaction_json_sha256": source_hash,
        },
        "policy": policy_report(),
        "gates": {
            "transaction_schema_exact": False,
            "source_tree_stable": False,
            "only_userspace_packages": False,
            "only_update_actions": False,
            "update_shape_valid": False,
            "no_groups_or_environments": False,
            "payload_files_hashed": False,
            "generator_version_verified": False,
            "vendor_change_verified": False,
            "rpm_signatures_verified": False,
            "nevra_payload_binding_verified": False,
            "disk_space_verified": False,
            "fresh_solver_state_verified": False,
            "post_update_health_verified": False,
        },
        "counts": {
            "packages": 0,
            "inbound_packages": 0,
            "payload_bytes": 0,
            "actions": {},
            "protected_packages": 0,
        },
        "packages": [],
        "protected_packages": [],
        "rejection_reasons": [message],
        "unresolved_execution_gates": list(UNRESOLVED_EXECUTION_GATES),
    }


def audit_path(argument: Path) -> dict[str, Any]:
    source_hash = None
    source = None
    try:
        source = TransactionSource(argument)
        content = source.read_transaction()
        source_hash = hashlib.sha256(content).hexdigest()
        payload = parse_json(content)
        report = evaluate_payload(payload, source=source, source_hash=source_hash)
        source.validate_unchanged()
        return report
    except (PlanError, OSError, UnicodeError) as exc:
        return rejected_report(str(exc), source_hash)
    finally:
        if source is not None:
            source.close()


def main() -> int:
    parser = JsonArgumentParser(
        description="Audit an exact DNF5 stored transaction; never run it."
    )
    parser.add_argument(
        "--transaction",
        type=Path,
        required=True,
        help="DNF5 --store directory or its transaction.json",
    )
    arguments = parser.parse_args()

    report = audit_path(arguments.transaction)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if report["accepted_for_userspace_review"] else 3


if __name__ == "__main__":
    sys.exit(main())
