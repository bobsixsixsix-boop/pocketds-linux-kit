"""Pure Applet-33 migration/restore model for PDS-005.

This module never reads or writes the live Plasma configuration.  An eventual
I/O adapter must implement the atomic and ownership contract documented beside
the experiment.  Keeping byte transformation separate lets recovery be proven
without stopping or starting plasmashell in tests.
"""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Dict, Mapping, Optional, Tuple


class MigrationError(ValueError):
    pass


class MigrationConflict(MigrationError):
    pass


class MigrationTargetAbsent(MigrationError):
    pass


class MigrationTargetAmbiguous(MigrationError):
    pass


class MigrationState(str, Enum):
    PRESENT_UNBACKED = "present-unbacked"
    ABSENT_UNMANAGED = "absent-unmanaged"
    MIGRATED = "migrated"
    RESTORED = "restored"
    CONFLICT = "conflict"
    INVALID_BACKUP = "invalid-backup"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class AppletTarget:
    containment_id: str = "2"
    applet_id: str = "33"
    plugin: str = "org.pocketds.controlpanel.v3"

    @property
    def base_section(self) -> bytes:
        return (
            "[Containments][%s][Applets][%s]"
            % (self.containment_id, self.applet_id)
        ).encode("ascii")

    @property
    def containment_section(self) -> bytes:
        return ("[Containments][%s]" % self.containment_id).encode("ascii")


@dataclass(frozen=True)
class MigrationPlan:
    target: AppletTarget
    original: bytes
    migrated: bytes
    removed_sections: int
    removed_geometry_references: int

    @property
    def original_sha256(self) -> str:
        return sha256(self.original)

    @property
    def migrated_sha256(self) -> str:
        return sha256(self.migrated)


@dataclass(frozen=True)
class BackupBundle:
    manifest: bytes
    original: bytes
    migrated: bytes


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _header(line: bytes) -> Optional[bytes]:
    body = line.rstrip(b"\r\n")
    if body.startswith(b"[") and body.endswith(b"]"):
        return body
    return None


def _sections(content: bytes) -> Tuple[Tuple[bytes, int, int], ...]:
    lines = content.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if _header(line)]
    result = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        result.append((_header(lines[start]), start, end))
    return tuple(result)


def _plugin_owners(content: bytes, plugin: str) -> Tuple[Tuple[str, str], ...]:
    lines = content.splitlines(keepends=True)
    pattern = re.compile(
        br"^\[Containments\]\[([0-9]+)\]\[Applets\]\[([0-9]+)\]$"
    )
    expected = ("plugin=" + plugin).encode("utf-8")
    owners = []
    for header, start, end in _sections(content):
        match = pattern.match(header)
        if not match:
            continue
        keys = {line.rstrip(b"\r\n") for line in lines[start + 1 : end]}
        if expected in keys:
            owners.append(
                (match.group(1).decode("ascii"), match.group(2).decode("ascii"))
            )
    return tuple(owners)


def _geometry_without_target(value: bytes, applet_id: str) -> Tuple[bytes, int]:
    prefix = ("Applet-%s:" % applet_id).encode("ascii")
    tokens = re.findall(br"[^;]*;|[^;]+$", value)
    kept = []
    removed = 0
    for token in tokens:
        if token.startswith(prefix):
            removed += 1
        else:
            kept.append(token)
    return b"".join(kept), removed


def plan_migration(content: bytes, target: AppletTarget = AppletTarget()) -> MigrationPlan:
    owners = _plugin_owners(content, target.plugin)
    expected_owner = (target.containment_id, target.applet_id)
    if owners != (expected_owner,):
        if not owners:
            raise MigrationTargetAbsent("expected Panel applet is absent")
        raise MigrationTargetAmbiguous(
            "Panel plugin owner is ambiguous or unexpected: %r" % (owners,)
        )

    lines = content.splitlines(keepends=True)
    target_prefix = target.base_section
    remove_lines = set()
    removed_sections = 0
    for header, start, end in _sections(content):
        suffix = header[len(target_prefix) :] if header.startswith(target_prefix) else None
        if suffix == b"" or (suffix is not None and suffix.startswith(b"[")):
            removed_sections += 1
            remove_lines.update(range(start, end))

    output = []
    current_header = None
    removed_geometry = 0
    for index, line in enumerate(lines):
        header = _header(line)
        if header is not None:
            current_header = header
        if index in remove_lines:
            continue
        if current_header == target.containment_section and b"=" in line:
            key, value_with_newline = line.split(b"=", 1)
            if key.startswith(b"ItemGeometries"):
                newline = b""
                value = value_with_newline
                if value.endswith(b"\r\n"):
                    value, newline = value[:-2], b"\r\n"
                elif value.endswith(b"\n"):
                    value, newline = value[:-1], b"\n"
                filtered, removed = _geometry_without_target(value, target.applet_id)
                removed_geometry += removed
                if removed:
                    if filtered:
                        output.append(key + b"=" + filtered + newline)
                    continue
        output.append(line)

    migrated = b"".join(output)
    if removed_sections < 1:
        raise MigrationError("target section was not removed")
    if removed_geometry < 1:
        raise MigrationError("target has no geometry ownership reference")
    if target.base_section in migrated:
        raise MigrationError("target section reference remains after migration")
    if ("Applet-%s:" % target.applet_id).encode("ascii") in migrated:
        raise MigrationError("target geometry reference remains after migration")
    if target.plugin.encode("utf-8") in migrated:
        raise MigrationError("target plugin reference remains after migration")
    return MigrationPlan(
        target, content, migrated, removed_sections, removed_geometry
    )


def create_backup_bundle(plan: MigrationPlan) -> BackupBundle:
    manifest = {
        "schema": 1,
        "target": {
            "containmentId": plan.target.containment_id,
            "appletId": plan.target.applet_id,
            "plugin": plan.target.plugin,
        },
        "original": {
            "file": "original.appletsrc",
            "sha256": plan.original_sha256,
            "size": len(plan.original),
        },
        "migrated": {
            "file": "migrated.appletsrc",
            "sha256": plan.migrated_sha256,
            "size": len(plan.migrated),
        },
        "removedSections": plan.removed_sections,
        "removedGeometryReferences": plan.removed_geometry_references,
    }
    encoded = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return BackupBundle(encoded, plan.original, plan.migrated)


def validate_bundle(bundle: BackupBundle, target: AppletTarget = AppletTarget()) -> Mapping[str, object]:
    try:
        manifest = json.loads(bundle.manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationError("backup manifest is invalid") from error
    if manifest.get("schema") != 1:
        raise MigrationError("backup schema is unsupported")
    expected_target = {
        "containmentId": target.containment_id,
        "appletId": target.applet_id,
        "plugin": target.plugin,
    }
    if manifest.get("target") != expected_target:
        raise MigrationError("backup target does not match Applet-33")
    expected_files = {
        "original": "original.appletsrc",
        "migrated": "migrated.appletsrc",
    }
    for name, content in (("original", bundle.original), ("migrated", bundle.migrated)):
        record = manifest.get(name)
        if not isinstance(record, dict):
            raise MigrationError("backup %s record is absent" % name)
        if record.get("file") != expected_files[name]:
            raise MigrationError("backup %s filename is not canonical" % name)
        if (
            record.get("size") != len(content)
            or record.get("sha256") != sha256(content)
        ):
            raise MigrationError("backup %s payload failed integrity check" % name)
    try:
        derived = plan_migration(bundle.original, target)
    except MigrationError as error:
        raise MigrationError("backup original cannot produce a valid migration") from error
    if derived.migrated != bundle.migrated:
        raise MigrationError("backup migrated payload is not derived from original")
    if manifest.get("removedSections") != derived.removed_sections:
        raise MigrationError("backup removed-section count is inconsistent")
    if manifest.get("removedGeometryReferences") != derived.removed_geometry_references:
        raise MigrationError("backup geometry count is inconsistent")
    return manifest


def classify(current: bytes, bundle: Optional[BackupBundle], target: AppletTarget = AppletTarget()) -> MigrationState:
    if bundle is None:
        try:
            plan_migration(current, target)
        except MigrationTargetAbsent:
            return MigrationState.ABSENT_UNMANAGED
        except MigrationError:
            return MigrationState.AMBIGUOUS
        return MigrationState.PRESENT_UNBACKED
    try:
        validate_bundle(bundle, target)
    except MigrationError:
        return MigrationState.INVALID_BACKUP
    digest = sha256(current)
    if digest == sha256(bundle.migrated):
        return MigrationState.MIGRATED
    if digest == sha256(bundle.original):
        return MigrationState.RESTORED
    return MigrationState.CONFLICT


def migrate_bytes(current: bytes, bundle: BackupBundle, target: AppletTarget = AppletTarget()) -> bytes:
    validate_bundle(bundle, target)
    state = classify(current, bundle, target)
    if state == MigrationState.MIGRATED:
        return current
    if state != MigrationState.RESTORED:
        raise MigrationConflict("current config is not the exact backed-up original")
    return bundle.migrated


def restore_bytes(current: bytes, bundle: BackupBundle, target: AppletTarget = AppletTarget()) -> bytes:
    validate_bundle(bundle, target)
    state = classify(current, bundle, target)
    if state == MigrationState.RESTORED:
        return current
    if state != MigrationState.MIGRATED:
        raise MigrationConflict("current config changed after migration; refusing overwrite")
    return bundle.original
