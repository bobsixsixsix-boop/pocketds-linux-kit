#!/usr/bin/env python3
"""Fail-closed geometry repair for the existing Pocket DS Plasma applet.

The default CLI action is a read-only plan.  Applying or restoring is allowed
only while plasmashell is inactive, and every live write is an atomic
same-directory replacement guarded by an exact-byte precondition.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Callable, Optional, Tuple

from applet_migration import sha256


PLUGIN = "org.pocketds.controlpanel.v3"
MAX_CONFIG_BYTES = 16 * 1024 * 1024
MAX_TELEMETRY_AGE_MS = 125_000
MAX_FUTURE_SKEW_MS = 5_000


class GeometryError(RuntimeError):
    pass


class GeometryTargetAbsent(GeometryError):
    pass


class GeometryTargetAmbiguous(GeometryError):
    pass


class UnsafeConfig(GeometryError):
    pass


class PlasmaActive(GeometryError):
    pass


class ConcurrentChange(GeometryError):
    pass


class BundleConflict(GeometryError):
    pass


@dataclass(frozen=True)
class DisplayGeometry:
    width: int
    height: int
    sample_unix_ms: int


@dataclass(frozen=True)
class AppletOwner:
    containment_id: str
    applet_id: str

    @property
    def containment_section(self) -> bytes:
        return f"[Containments][{self.containment_id}]".encode("ascii")


@dataclass(frozen=True)
class GeometryPlan:
    owner: AppletOwner
    geometry: DisplayGeometry
    original: bytes
    applied: bytes
    matched_tokens: int
    changed_tokens: int

    @property
    def original_sha256(self) -> str:
        return sha256(self.original)

    @property
    def applied_sha256(self) -> str:
        return sha256(self.applied)


@dataclass(frozen=True)
class SecureSnapshot:
    content: bytes
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Bundle:
    directory: Path
    manifest: bytes
    original: bytes
    applied: bytes
    plan: GeometryPlan


def _header(line: bytes) -> Optional[bytes]:
    body = line.rstrip(b"\r\n")
    if body.startswith(b"[") and body.endswith(b"]"):
        return body
    return None


def _sections(content: bytes) -> Tuple[Tuple[bytes, int, int], ...]:
    lines = content.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if _header(line)]
    sections = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        sections.append((_header(lines[start]), start, end))
    return tuple(sections)


def locate_owner(content: bytes, plugin: str = PLUGIN) -> AppletOwner:
    """Return the one direct Plasma applet section owning ``plugin``."""
    pattern = re.compile(
        br"^\[Containments\]\[([0-9]+)\]\[Applets\]\[([0-9]+)\]$"
    )
    expected = ("plugin=" + plugin).encode("utf-8")
    lines = content.splitlines(keepends=True)
    owners = []
    for header, start, end in _sections(content):
        match = pattern.fullmatch(header)
        if match is None:
            continue
        for line in lines[start + 1 : end]:
            if line.rstrip(b"\r\n") == expected:
                owners.append(
                    AppletOwner(
                        match.group(1).decode("ascii"),
                        match.group(2).decode("ascii"),
                    )
                )
    if not owners:
        raise GeometryTargetAbsent(f"no applet owns {plugin}")
    if len(owners) != 1:
        raise GeometryTargetAmbiguous(
            f"expected one {plugin} owner, found {len(owners)}"
        )
    return owners[0]


def dimensions_from_telemetry(
    content: bytes, *, now_unix_ms: Optional[int] = None
) -> DisplayGeometry:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GeometryError("telemetry cache is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise GeometryError("telemetry cache root is not an object")
    if payload.get("display_status") != "ok":
        raise GeometryError("DSI telemetry status is not ok")
    if payload.get("display_dsi2_enabled") is not True:
        raise GeometryError("DSI-2 is not reported enabled")

    values = []
    for key in ("display_dsi2_logical_width", "display_dsi2_logical_height"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise GeometryError(f"{key} is not an integer")
        if not 1 <= value <= 16_384:
            raise GeometryError(f"{key} is outside the safe range")
        values.append(value)

    sample = payload.get("display_sample_unix_ms")
    if isinstance(sample, bool) or not isinstance(sample, int) or sample <= 0:
        raise GeometryError("display_sample_unix_ms is invalid")
    now = int(time.time() * 1000) if now_unix_ms is None else now_unix_ms
    age = now - sample
    if age > MAX_TELEMETRY_AGE_MS or age < -MAX_FUTURE_SKEW_MS:
        raise GeometryError("display telemetry is stale or from the future")
    return DisplayGeometry(values[0], values[1], sample)


def _split_line(line: bytes) -> Tuple[bytes, bytes]:
    if line.endswith(b"\r\n"):
        return line[:-2], b"\r\n"
    if line.endswith(b"\n"):
        return line[:-1], b"\n"
    return line, b""


def _rewrite_target_token(
    value: bytes, owner: AppletOwner, geometry: DisplayGeometry
) -> Tuple[bytes, int, int]:
    prefix = f"Applet-{owner.applet_id}:".encode("ascii")
    fields = re.compile(
        br"^(-?[0-9]+),(-?[0-9]+),(-?[0-9]+),(-?[0-9]+)((?:,.*)?)$"
    )
    parts = value.split(b";")
    matches = 0
    changed = 0
    for index, token in enumerate(parts):
        if not token.startswith(prefix):
            continue
        matches += 1
        match = fields.fullmatch(token[len(prefix) :])
        if match is None:
            raise GeometryError("target Applet geometry token has unknown syntax")
        replacement = (
            prefix
            + f"0,0,{geometry.width},{geometry.height}".encode("ascii")
            + match.group(5)
        )
        if replacement != token:
            parts[index] = replacement
            changed += 1
    return b";".join(parts), matches, changed


def plan_geometry(
    content: bytes, geometry: DisplayGeometry, plugin: str = PLUGIN
) -> GeometryPlan:
    """Change exactly two active geometry tokens and no other config bytes."""
    owner = locate_owner(content, plugin)
    lines = content.splitlines(keepends=True)
    containment_matches = [
        (start, end)
        for header, start, end in _sections(content)
        if header == owner.containment_section
    ]
    if len(containment_matches) != 1:
        raise GeometryTargetAmbiguous(
            "owner containment section is missing or duplicated"
        )

    active_key = f"ItemGeometries-{geometry.width}x{geometry.height}".encode("ascii")
    wanted = (active_key, b"ItemGeometriesHorizontal")
    occurrences = {key: [] for key in wanted}
    start, end = containment_matches[0]
    for index in range(start + 1, end):
        body, _newline = _split_line(lines[index])
        if b"=" not in body:
            continue
        key, _value = body.split(b"=", 1)
        if key in occurrences:
            occurrences[key].append(index)
    for key, indexes in occurrences.items():
        if len(indexes) != 1:
            raise GeometryTargetAmbiguous(
                f"expected exactly one {key.decode('ascii')} key, found {len(indexes)}"
            )

    matched = 0
    changed = 0
    for key in wanted:
        index = occurrences[key][0]
        body, newline = _split_line(lines[index])
        existing_key, value = body.split(b"=", 1)
        rewritten, token_matches, token_changes = _rewrite_target_token(
            value, owner, geometry
        )
        if token_matches != 1:
            raise GeometryTargetAmbiguous(
                f"expected one target token in {key.decode('ascii')}, "
                f"found {token_matches}"
            )
        matched += token_matches
        changed += token_changes
        lines[index] = existing_key + b"=" + rewritten + newline

    applied = b"".join(lines)
    if len(applied) - len(content) > 256 or len(content) - len(applied) > 256:
        raise GeometryError("unexpected geometry transform size delta")
    return GeometryPlan(owner, geometry, content, applied, matched, changed)


def _snapshot_from_stat(content: bytes, info: os.stat_result) -> SecureSnapshot:
    return SecureSnapshot(
        content=content,
        device=info.st_dev,
        inode=info.st_ino,
        mode=stat.S_IMODE(info.st_mode),
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
    )


def _validate_file_stat(info: os.stat_result, uid: int, label: str) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise UnsafeConfig(f"{label} is not a regular file")
    if info.st_uid != uid:
        raise UnsafeConfig(f"{label} is not owned by the current user")
    if info.st_nlink != 1:
        raise UnsafeConfig(f"{label} has an unsafe hard-link count")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022 or mode & 0o111:
        raise UnsafeConfig(f"{label} has unsafe mode {mode:04o}")


def read_secure_config(path: Path, *, uid: Optional[int] = None) -> SecureSnapshot:
    owner_uid = os.geteuid() if uid is None else uid
    try:
        before = path.lstat()
    except OSError as error:
        raise UnsafeConfig(f"cannot lstat config: {error}") from error
    if stat.S_ISLNK(before.st_mode):
        raise UnsafeConfig("config is a symlink")
    _validate_file_stat(before, owner_uid, "config")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise UnsafeConfig(f"cannot safely open config: {error}") from error
    try:
        opened = os.fstat(descriptor)
        _validate_file_stat(opened, owner_uid, "opened config")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ConcurrentChange("config changed while it was opened")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, MAX_CONFIG_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_CONFIG_BYTES:
                raise UnsafeConfig("config exceeds the bounded size")
        after_read = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after_path = path.lstat()
    except OSError as error:
        raise ConcurrentChange("config disappeared during read") from error
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(getattr(opened, field) != getattr(after_read, field) for field in stable_fields):
        raise ConcurrentChange("config changed during read")
    if any(getattr(opened, field) != getattr(after_path, field) for field in stable_fields):
        raise ConcurrentChange("config path changed during read")
    return _snapshot_from_stat(b"".join(chunks), opened)


def default_plasma_probe() -> bool:
    """Return whether any plasmashell process exists; probe failure is fatal."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "plasmashell"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GeometryError("cannot determine plasmashell state") from error
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise GeometryError("plasmashell state probe failed")


def _require_plasma_inactive(probe: Callable[[], bool]) -> None:
    if probe():
        raise PlasmaActive("plasmashell is active; refusing config write")


def _canonical_manifest(
    plan: GeometryPlan, config_mode: int, config_name: str
) -> bytes:
    payload = {
        "schema": 1,
        "plugin": PLUGIN,
        "owner": {
            "containmentId": plan.owner.containment_id,
            "appletId": plan.owner.applet_id,
        },
        "geometry": {
            "width": plan.geometry.width,
            "height": plan.geometry.height,
            "telemetrySampleUnixMs": plan.geometry.sample_unix_ms,
        },
        "config": {"file": config_name, "mode": config_mode},
        "original": {
            "file": "original.appletsrc",
            "sha256": plan.original_sha256,
            "size": len(plan.original),
        },
        "applied": {
            "file": "applied.appletsrc",
            "sha256": plan.applied_sha256,
            "size": len(plan.applied),
        },
        "matchedTokens": plan.matched_tokens,
        "changedTokens": plan.changed_tokens,
    }
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _read_state_file(path: Path, uid: int) -> bytes:
    try:
        info = path.lstat()
    except OSError as error:
        raise BundleConflict(f"cannot lstat backup {path.name}") from error
    if stat.S_ISLNK(info.st_mode):
        raise BundleConflict(f"backup {path.name} is a symlink")
    try:
        _validate_file_stat(info, uid, f"backup {path.name}")
    except UnsafeConfig as error:
        raise BundleConflict(str(error)) from error
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise BundleConflict(f"backup {path.name} is not mode 0600")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        content = bytearray()
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_CONFIG_BYTES:
                raise BundleConflict(f"backup {path.name} is too large")
    finally:
        os.close(descriptor)
    return bytes(content)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new_0600(path: Path, content: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_state_directory(path: Path, uid: int, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise BundleConflict(f"cannot inspect {label}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise BundleConflict(f"{label} is not a normal directory")
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) & 0o022:
        raise BundleConflict(f"{label} ownership or permissions are unsafe")


def persist_bundle(
    state_root: Path, plan: GeometryPlan, config_mode: int, config_name: str,
    *, uid: Optional[int] = None,
) -> Bundle:
    owner_uid = os.geteuid() if uid is None else uid
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _validate_state_directory(state_root, owner_uid, "state root")
    directory = state_root / plan.original_sha256
    manifest = _canonical_manifest(plan, config_mode, config_name)
    wanted = {
        "original.appletsrc": plan.original,
        "applied.appletsrc": plan.applied,
        "manifest.json": manifest,
    }
    try:
        directory.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        created = False
    _validate_state_directory(directory, owner_uid, "backup directory")
    if created:
        for name in ("original.appletsrc", "applied.appletsrc", "manifest.json"):
            _write_new_0600(directory / name, wanted[name])
        _fsync_directory(directory)
        _fsync_directory(state_root)
    else:
        for name, content in wanted.items():
            if _read_state_file(directory / name, owner_uid) != content:
                raise BundleConflict(f"existing backup conflicts at {name}")
    return Bundle(directory, manifest, plan.original, plan.applied, plan)


def _validate_bundle_payload(
    directory: Path, manifest_bytes: bytes, original: bytes, applied: bytes
) -> GeometryPlan:
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleConflict("backup manifest is invalid") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise BundleConflict("backup manifest schema is unsupported")
    if manifest.get("plugin") != PLUGIN:
        raise BundleConflict("backup plugin does not match")
    geometry_data = manifest.get("geometry")
    owner_data = manifest.get("owner")
    if not isinstance(geometry_data, dict) or not isinstance(owner_data, dict):
        raise BundleConflict("backup owner or geometry is absent")
    try:
        geometry = DisplayGeometry(
            int(geometry_data["width"]),
            int(geometry_data["height"]),
            int(geometry_data["telemetrySampleUnixMs"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise BundleConflict("backup geometry is invalid") from error
    if isinstance(geometry_data.get("width"), bool) or isinstance(
        geometry_data.get("height"), bool
    ):
        raise BundleConflict("backup geometry is invalid")
    plan = plan_geometry(original, geometry)
    expected_owner = {
        "containmentId": plan.owner.containment_id,
        "appletId": plan.owner.applet_id,
    }
    if owner_data != expected_owner or plan.applied != applied:
        raise BundleConflict("backup applied payload is not derived from original")
    for name, content in (("original", original), ("applied", applied)):
        record = manifest.get(name)
        if not isinstance(record, dict):
            raise BundleConflict(f"backup {name} record is absent")
        expected_file = f"{name}.appletsrc"
        if (
            record.get("file") != expected_file
            or record.get("size") != len(content)
            or record.get("sha256") != sha256(content)
        ):
            raise BundleConflict(f"backup {name} integrity failed")
    if (
        manifest.get("matchedTokens") != plan.matched_tokens
        or manifest.get("changedTokens") != plan.changed_tokens
    ):
        raise BundleConflict("backup transform counters do not match")
    if directory.name != plan.original_sha256:
        raise BundleConflict("backup directory hash does not match original")
    return plan


def load_bundle(
    state_root: Path, original_sha256: str, *, uid: Optional[int] = None
) -> Bundle:
    if re.fullmatch(r"[0-9a-f]{64}", original_sha256) is None:
        raise BundleConflict("original SHA-256 is not canonical")
    owner_uid = os.geteuid() if uid is None else uid
    _validate_state_directory(state_root, owner_uid, "state root")
    directory = state_root / original_sha256
    _validate_state_directory(directory, owner_uid, "backup directory")
    manifest = _read_state_file(directory / "manifest.json", owner_uid)
    original = _read_state_file(directory / "original.appletsrc", owner_uid)
    applied = _read_state_file(directory / "applied.appletsrc", owner_uid)
    plan = _validate_bundle_payload(directory, manifest, original, applied)
    return Bundle(directory, manifest, original, applied, plan)


def _same_identity(snapshot: SecureSnapshot, info: os.stat_result) -> bool:
    return (
        snapshot.device == info.st_dev
        and snapshot.inode == info.st_ino
        and snapshot.size == info.st_size
        and snapshot.mtime_ns == info.st_mtime_ns
    )


def _atomic_replace(path: Path, content: bytes, expected: SecureSnapshot) -> None:
    temporary = path.parent / (
        f".{path.name}.pocketds-{os.getpid()}-{secrets.token_hex(8)}.tmp"
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = None
    try:
        descriptor = os.open(temporary, flags, expected.mode)
        os.fchmod(descriptor, expected.mode)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        current = path.lstat()
        if not _same_identity(expected, current):
            raise ConcurrentChange("config changed immediately before rename")
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def apply_geometry(
    config: Path,
    state_root: Path,
    geometry: DisplayGeometry,
    *,
    plasma_probe: Callable[[], bool] = default_plasma_probe,
    precommit_hook: Optional[Callable[[Path], None]] = None,
    uid: Optional[int] = None,
) -> Tuple[GeometryPlan, Optional[Bundle]]:
    _require_plasma_inactive(plasma_probe)
    first = read_secure_config(config, uid=uid)
    plan = plan_geometry(first.content, geometry)
    if plan.applied == plan.original:
        return plan, None
    bundle = persist_bundle(
        state_root, plan, first.mode, config.name, uid=uid
    )
    if precommit_hook is not None:
        precommit_hook(config)
    _require_plasma_inactive(plasma_probe)
    current = read_secure_config(config, uid=uid)
    if current.content != plan.original or current.mode != first.mode:
        raise ConcurrentChange("config is no longer the exact planned original")
    _atomic_replace(config, plan.applied, current)
    return plan, bundle


def restore_geometry(
    config: Path,
    state_root: Path,
    original_sha256: str,
    *,
    plasma_probe: Callable[[], bool] = default_plasma_probe,
    precommit_hook: Optional[Callable[[Path], None]] = None,
    uid: Optional[int] = None,
) -> Bundle:
    _require_plasma_inactive(plasma_probe)
    bundle = load_bundle(state_root, original_sha256, uid=uid)
    first = read_secure_config(config, uid=uid)
    if sha256(first.content) != bundle.plan.applied_sha256:
        raise ConcurrentChange("live config hash is not the exact applied hash")
    if precommit_hook is not None:
        precommit_hook(config)
    _require_plasma_inactive(plasma_probe)
    current = read_secure_config(config, uid=uid)
    if current.content != bundle.applied or current.mode != first.mode:
        raise ConcurrentChange("live config changed before restore")
    _atomic_replace(config, bundle.original, current)
    return bundle


def _read_bounded(path: Path, limit: int) -> bytes:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise GeometryError(f"cannot read {path}: {error}") from error
    if len(content) > limit:
        raise GeometryError(f"{path} exceeds the bounded size")
    return content


def _default_config() -> Path:
    return Path.home() / ".config/plasma-org.kde.plasma.desktop-appletsrc"


def _default_telemetry() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.geteuid()}")
    return Path(runtime) / "pocketds-gpu-status.json"


def _default_state_root() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    return (
        Path(state) if state else Path.home() / ".local/state"
    ) / "pocketds-panel-geometry"


def _summary(plan: GeometryPlan, action: str, bundle: Optional[Bundle]) -> dict:
    return {
        "ok": True,
        "action": action,
        "plugin": PLUGIN,
        "owner": {
            "containment_id": plan.owner.containment_id,
            "applet_id": plan.owner.applet_id,
        },
        "geometry": {
            "width": plan.geometry.width,
            "height": plan.geometry.height,
            "source": "telemetry-cache-dsi2-logical",
            "sample_unix_ms": plan.geometry.sample_unix_ms,
        },
        "matched_tokens": plan.matched_tokens,
        "changed_tokens": plan.changed_tokens,
        "would_change": plan.original != plan.applied,
        "original_sha256": plan.original_sha256,
        "applied_sha256": plan.applied_sha256,
        "backup": str(bundle.directory) if bundle is not None else None,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="apply the planned repair")
    action.add_argument(
        "--restore", metavar="ORIGINAL_SHA256", help="restore an exact saved original"
    )
    parser.add_argument("--config", type=Path, default=_default_config())
    parser.add_argument("--telemetry", type=Path, default=_default_telemetry())
    parser.add_argument("--state-root", type=Path, default=_default_state_root())
    args = parser.parse_args(argv)
    try:
        if args.restore:
            bundle = restore_geometry(args.config, args.state_root, args.restore)
            result = _summary(bundle.plan, "restore", bundle)
        else:
            geometry = dimensions_from_telemetry(
                _read_bounded(args.telemetry, 1024 * 1024)
            )
            if args.apply:
                plan, bundle = apply_geometry(args.config, args.state_root, geometry)
                result = _summary(plan, "apply", bundle)
            else:
                snapshot = read_secure_config(args.config)
                plan = plan_geometry(snapshot.content, geometry)
                result = _summary(plan, "dry-run", None)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except GeometryError as error:
        print(
            json.dumps(
                {"ok": False, "error": type(error).__name__, "message": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
