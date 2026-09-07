#!/usr/bin/env python3
"""Stage, apply, verify and roll back the complete measured Pocket DS UI bundle."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Callable, Sequence


SCHEMA = "pocketds.ui-update-transaction.v1"
MARKER_SCHEMA = "pocketds.ui-update-marker.v1"
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
STAGE_CONFIRMATION = "POCKETDS-STAGE-TEN-UI-FILES"
APPLY_CONFIRMATION = "POCKETDS-APPLY-TEN-UI-FILES"
ROLLBACK_CONFIRMATION = "POCKETDS-ROLLBACK-TEN-UI-FILES"
LEGACY_ROLLBACK_CONFIRMATION = "POCKETDS-ROLLBACK-EIGHT-UI-FILES"
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")


class TransactionError(RuntimeError):
    """The transaction was unsafe, stale, incomplete or ambiguous."""


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    source: Path
    target: Path
    target_uid: int
    target_mode: int
    privileged: bool
    target_must_exist: bool = False


ReplaceFunction = Callable[[Path, Path, int, str], None]
RemoveFunction = Callable[[Path, str], None]


def artifact_plan(
    repo_root: Path,
    home: Path,
    system_root: Path,
    *,
    user_uid: int | None = None,
    root_uid: int = 0,
) -> list[Artifact]:
    uid = os.getuid() if user_uid is None else user_uid
    panel = home / ".local/share/plasma/plasmoids/org.pocketds.controlpanel.v3"
    return [
        Artifact(
            "panel-controller-diagram",
            repo_root / "components/control-panel/plasmoid/contents/ui/ControllerDiagram.qml",
            panel / "contents/ui/ControllerDiagram.qml",
            uid,
            0o644,
            False,
        ),
        Artifact(
            "panel-controller-session",
            repo_root / "components/control-panel/plasmoid/contents/ui/ControllerTestSession.qml",
            panel / "contents/ui/ControllerTestSession.qml",
            uid,
            0o644,
            False,
        ),
        Artifact(
            "panel-qml",
            repo_root / "components/control-panel/plasmoid/contents/ui/main.qml",
            panel / "contents/ui/main.qml",
            uid,
            0o644,
            False,
        ),
        Artifact(
            "deep-suspend-guard",
            repo_root / "components/system/pocketds-deep-suspend.py",
            system_root / "usr/local/libexec/pocketds-deep-suspend",
            root_uid,
            0o755,
            True,
        ),
        Artifact(
            "deep-suspend-polkit",
            repo_root / "components/system/90-pocketds-deep-suspend.rules",
            system_root / "etc/polkit-1/rules.d/90-pocketds-deep-suspend.rules",
            root_uid,
            0o644,
            True,
        ),
        Artifact(
            "panel-root-helper",
            repo_root / "components/control-panel/pocketds-panel-root",
            system_root / "usr/local/libexec/pocketds-panel-root",
            root_uid,
            0o755,
            True,
            True,
        ),
        Artifact(
            "keyboard-main",
            repo_root / "components/keyboard/pocketds-keyboard.py",
            home / ".local/bin/pocketds-keyboard.py",
            uid,
            0o755,
            False,
        ),
        Artifact(
            "keyboard-adapter",
            repo_root / "components/keyboard/keyboard_adapter.py",
            home / ".local/bin/keyboard_adapter.py",
            uid,
            0o644,
            False,
        ),
        Artifact(
            "keyboard-geometry",
            repo_root / "components/keyboard/screen_geometry.py",
            home / ".local/bin/screen_geometry.py",
            uid,
            0o644,
            False,
        ),
        Artifact(
            "keyboard-voice-artifacts",
            repo_root / "components/keyboard/voice_artifacts.py",
            home / ".local/bin/voice_artifacts.py",
            uid,
            0o644,
            False,
        ),
    ]


def _read_file(
    path: Path,
    *,
    expected_uid: int,
    expected_mode: int | None = None,
    allow_missing: bool = False,
    maximum: int = MAX_ARTIFACT_BYTES,
) -> tuple[bytes, int] | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise TransactionError("required transaction file is missing") from None
    except OSError as exc:
        raise TransactionError("transaction file is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= maximum
            or (expected_mode is not None and mode != expected_mode)
        ):
            raise TransactionError("transaction file owner, type, links, size or mode is unsafe")
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise TransactionError("transaction file changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise TransactionError("transaction file grew while reading")
        return bytes(content), mode
    finally:
        os.close(descriptor)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _source_snapshot(artifact: Artifact) -> tuple[bytes, dict[str, object]]:
    result = _read_file(
        artifact.source,
        expected_uid=os.getuid(),
        expected_mode=artifact.target_mode,
    )
    assert result is not None
    content, source_mode = result
    return content, {
        "source_sha256": _sha256(content),
        "source_bytes": len(content),
        "source_mode": f"{source_mode:04o}",
    }


def _target_snapshot(artifact: Artifact) -> tuple[bytes | None, dict[str, object]]:
    result = _read_file(
        artifact.target,
        expected_uid=artifact.target_uid,
        allow_missing=not artifact.target_must_exist,
    )
    if result is None:
        return None, {
            "before_state": "MISSING",
            "before_sha256": None,
            "before_bytes": None,
            "before_mode": None,
        }
    content, mode = result
    if mode not in (0o600, 0o644, 0o700, 0o755):
        raise TransactionError("live preimage mode is not safe to preserve")
    return content, {
        "before_state": "PRESENT",
        "before_sha256": _sha256(content),
        "before_bytes": len(content),
        "before_mode": f"{mode:04o}",
    }


def deployment_plan(artifacts: list[Artifact]) -> dict[str, object]:
    results: list[dict[str, object]] = []
    safe = True
    for artifact in artifacts:
        try:
            source, source_fields = _source_snapshot(artifact)
            live, live_fields = _target_snapshot(artifact)
            if live is None:
                state = "MISSING"
            elif _sha256(live) != _sha256(source):
                state = "DRIFT"
            elif live_fields["before_mode"] != f"{artifact.target_mode:04o}":
                state = "MODE_DRIFT"
            else:
                state = "MATCH"
            source_hash = source_fields["source_sha256"]
            live_hash = live_fields["before_sha256"]
        except TransactionError:
            state = "UNSAFE"
            source_hash = None
            live_hash = None
            safe = False
        results.append(
            {
                "artifact_id": artifact.artifact_id,
                "state": state,
                "source_sha256": source_hash,
                "live_sha256": live_hash,
                "target_mode": f"{artifact.target_mode:04o}",
                "privileged": artifact.privileged,
            }
        )
    return {
        "schema": SCHEMA,
        "operation": "PLAN",
        "artifact_count": len(results),
        "artifacts": results,
        "safe_to_stage": safe,
        "activation_deferred": True,
        "services_restarted": False,
        "absolute_paths_emitted": False,
    }


def _check_directory(path: Path, mode: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TransactionError("transaction directory is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise TransactionError("transaction directory owner, type or mode is unsafe")


def _write_exclusive(path: Path, content: bytes, mode: int = 0o600) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, mode)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short transaction write")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _json_bytes(value: dict[str, object]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


@contextmanager
def _transaction_lock(parent: Path):
    """Serialize every mutating/readback phase across all UI transactions."""
    _check_directory(parent, 0o700)
    lock_path = parent / ".ui-update.lock"
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise TransactionError("UI transaction lock is unavailable or linked") from exc
    locked = False
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise TransactionError("UI transaction lock identity is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise TransactionError("another UI transaction operation is active") from exc
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _serialized_transaction(operation: Callable[..., dict[str, object]]):
    @functools.wraps(operation)
    def locked(
        artifacts: list[Artifact], transaction: Path, *args, **kwargs
    ) -> dict[str, object]:
        with _transaction_lock(transaction.parent):
            return operation(artifacts, transaction, *args, **kwargs)

    return locked


@_serialized_transaction
def stage_transaction(
    artifacts: list[Artifact], transaction: Path, revision: str
) -> dict[str, object]:
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise TransactionError("repository revision is invalid")
    try:
        transaction.mkdir(mode=0o700)
        (transaction / "payload").mkdir(mode=0o700)
        (transaction / "backup").mkdir(mode=0o700)
    except OSError as exc:
        raise TransactionError("new transaction directory could not be created") from exc
    _check_directory(transaction, 0o700)
    _check_directory(transaction / "payload", 0o700)
    _check_directory(transaction / "backup", 0o700)

    records: list[dict[str, object]] = []
    for artifact in artifacts:
        source, source_fields = _source_snapshot(artifact)
        live, live_fields = _target_snapshot(artifact)
        _write_exclusive(transaction / "payload" / artifact.artifact_id, source)
        if live is not None:
            _write_exclusive(transaction / "backup" / artifact.artifact_id, live)
        records.append(
            {
                "artifact_id": artifact.artifact_id,
                "privileged": artifact.privileged,
                "target_mode": f"{artifact.target_mode:04o}",
                **source_fields,
                **live_fields,
            }
        )
    manifest: dict[str, object] = {
        "schema": SCHEMA,
        "repository_revision": revision,
        "artifact_count": len(records),
        "artifacts": records,
        "activation_deferred": True,
        "services_restarted": False,
        "absolute_paths_recorded": False,
    }
    content = _json_bytes(manifest)
    _write_exclusive(transaction / "manifest.json", content)
    return {
        "schema": SCHEMA,
        "operation": "STAGED",
        "repository_revision": revision,
        "artifact_count": len(records),
        "manifest_sha256": _sha256(content),
        "activation_deferred": True,
        "services_restarted": False,
    }


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise TransactionError("transaction JSON contains duplicate keys")
        value[key] = item
    return value


def _load_json(path: Path, *, maximum: int = MAX_MANIFEST_BYTES) -> tuple[dict[str, object], bytes]:
    result = _read_file(
        path,
        expected_uid=os.getuid(),
        expected_mode=0o600,
        maximum=maximum,
    )
    assert result is not None
    content, _mode = result
    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _item: (_ for _ in ()).throw(
                TransactionError("transaction JSON contains a non-finite number")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TransactionError("transaction JSON is invalid") from exc
    if type(value) is not dict:
        raise TransactionError("transaction JSON root must be an object")
    return value, content


def _validate_manifest(
    transaction: Path, artifacts: list[Artifact]
) -> tuple[dict[str, object], dict[str, dict[str, object]], str]:
    _check_directory(transaction, 0o700)
    _check_directory(transaction / "payload", 0o700)
    _check_directory(transaction / "backup", 0o700)
    manifest, content = _load_json(transaction / "manifest.json")
    expected_root = {
        "schema",
        "repository_revision",
        "artifact_count",
        "artifacts",
        "activation_deferred",
        "services_restarted",
        "absolute_paths_recorded",
    }
    if set(manifest) != expected_root:
        raise TransactionError("transaction manifest fields are invalid")
    revision = manifest["repository_revision"]
    records = manifest["artifacts"]
    if (
        manifest["schema"] != SCHEMA
        or type(revision) is not str
        or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        or type(records) is not list
        or type(manifest["artifact_count"]) is not int
        or manifest["artifact_count"] != len(artifacts)
        or len(records) != len(artifacts)
        or manifest["activation_deferred"] is not True
        or manifest["services_restarted"] is not False
        or manifest["absolute_paths_recorded"] is not False
    ):
        raise TransactionError("transaction manifest identity is invalid")
    expected_record = {
        "artifact_id",
        "privileged",
        "target_mode",
        "source_sha256",
        "source_bytes",
        "source_mode",
        "before_state",
        "before_sha256",
        "before_bytes",
        "before_mode",
    }
    by_id: dict[str, dict[str, object]] = {}
    # Length equality was checked above; avoid zip(strict=...) so the same
    # transaction code remains usable with the older Python on the Mac host.
    for artifact, raw in zip(artifacts, records):
        if type(raw) is not dict or set(raw) != expected_record:
            raise TransactionError("transaction artifact fields are invalid")
        if (
            raw["artifact_id"] != artifact.artifact_id
            or type(raw["privileged"]) is not bool
            or raw["privileged"] != artifact.privileged
            or raw["target_mode"] != f"{artifact.target_mode:04o}"
            or type(raw["source_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", raw["source_sha256"]) is None
            or type(raw["source_bytes"]) is not int
            or not 0 < raw["source_bytes"] <= MAX_ARTIFACT_BYTES
            or type(raw["source_mode"]) is not str
            or re.fullmatch(r"[0-7]{4}", raw["source_mode"]) is None
            or raw["before_state"] not in ("MISSING", "PRESENT")
        ):
            raise TransactionError("transaction artifact identity is invalid")
        if raw["before_state"] == "MISSING":
            if any(raw[key] is not None for key in ("before_sha256", "before_bytes", "before_mode")):
                raise TransactionError("missing transaction preimage has data")
        elif (
            type(raw["before_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", raw["before_sha256"]) is None
            or type(raw["before_bytes"]) is not int
            or not 0 < raw["before_bytes"] <= MAX_ARTIFACT_BYTES
            or type(raw["before_mode"]) is not str
            or re.fullmatch(r"[0-7]{4}", raw["before_mode"]) is None
        ):
            raise TransactionError("present transaction preimage is invalid")
        if artifact.target_must_exist and raw["before_state"] != "PRESENT":
            raise TransactionError("required privileged preimage is missing")
        by_id[artifact.artifact_id] = raw
    return manifest, by_id, _sha256(content)


def _bound_payload(transaction: Path, record: dict[str, object]) -> Path:
    payload = transaction / "payload" / str(record["artifact_id"])
    result = _read_file(payload, expected_uid=os.getuid(), expected_mode=0o600)
    assert result is not None
    content, _mode = result
    if len(content) != record["source_bytes"] or _sha256(content) != record["source_sha256"]:
        raise TransactionError("transaction payload drifted")
    return payload


def _bound_backup(transaction: Path, record: dict[str, object]) -> Path | None:
    backup = transaction / "backup" / str(record["artifact_id"])
    if record["before_state"] == "MISSING":
        if backup.exists() or backup.is_symlink():
            raise TransactionError("missing preimage unexpectedly has a backup")
        return None
    result = _read_file(backup, expected_uid=os.getuid(), expected_mode=0o600)
    assert result is not None
    content, _mode = result
    if len(content) != record["before_bytes"] or _sha256(content) != record["before_sha256"]:
        raise TransactionError("transaction backup drifted")
    return backup


def _target_matches(artifact: Artifact, record: dict[str, object], phase: str) -> bool:
    result = _read_file(
        artifact.target,
        expected_uid=artifact.target_uid,
        allow_missing=record["before_state"] == "MISSING" and phase == "before",
    )
    if phase == "before" and record["before_state"] == "MISSING":
        return result is None
    if result is None:
        return False
    content, mode = result
    if phase == "before":
        return _sha256(content) == record["before_sha256"] and f"{mode:04o}" == record["before_mode"]
    return _sha256(content) == record["source_sha256"] and mode == artifact.target_mode


def _known_target_phase(artifact: Artifact, record: dict[str, object]) -> str | None:
    """Classify a live target without accepting content outside the transaction."""
    matches: list[str] = []
    for phase in ("payload", "before"):
        try:
            if _target_matches(artifact, record, phase):
                matches.append(phase)
        except TransactionError:
            # A missing target can only be a valid `before` state.  Other read
            # failures remain unknown and are rejected by the caller.
            pass
    if len(matches) == 2:
        return "both"
    return matches[0] if matches else None


def _check_user_parent(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TransactionError("target parent is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise TransactionError("target parent is unsafe")


def atomic_user_replace(source: Path, target: Path, mode: int, token: str) -> None:
    _check_user_parent(target.parent)
    temporary = target.parent / f".{target.name}.{token}.new"
    content_result = _read_file(source, expected_uid=os.getuid(), expected_mode=0o600)
    assert content_result is not None
    content, _source_mode = content_result
    _write_exclusive(temporary, content, mode)
    try:
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def sudo_atomic_replace(source: Path, target: Path, mode: int, token: str) -> None:
    temporary = target.parent / f".{target.name}.{token}.new"
    commands = (
        ("sudo", "-n", "/usr/bin/test", "!", "-e", str(temporary)),
        (
            "sudo",
            "-n",
            "/usr/bin/install",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            f"{mode:04o}",
            "--",
            str(source),
            str(temporary),
        ),
        ("sudo", "-n", "/usr/bin/mv", "--", str(temporary), str(target)),
    )
    temporary_created = False
    try:
        for index, command in enumerate(commands):
            result = subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=20,
                env={**os.environ, "LC_ALL": "C"},
            )
            if result.returncode != 0:
                raise TransactionError("privileged atomic replace failed")
            if index == 1:
                temporary_created = True
            elif index == 2:
                temporary_created = False
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TransactionError("privileged atomic replace was unavailable") from exc
    finally:
        if temporary_created:
            try:
                subprocess.run(
                    ("sudo", "-n", "/usr/bin/rm", "-f", "--", str(temporary)),
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                    env={**os.environ, "LC_ALL": "C"},
                )
            except (OSError, subprocess.TimeoutExpired):
                pass


def atomic_user_remove(target: Path, _token: str) -> None:
    _check_user_parent(target.parent)
    target.unlink()
    directory = os.open(target.parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def sudo_atomic_remove(target: Path, _token: str) -> None:
    try:
        result = subprocess.run(
            ("sudo", "-n", "/usr/bin/rm", "--", str(target)),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=20,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TransactionError("privileged atomic remove was unavailable") from exc
    if result.returncode != 0:
        raise TransactionError("privileged atomic remove failed")


def _replace(
    artifact: Artifact,
    source: Path,
    mode: int,
    token: str,
    privileged_replace: ReplaceFunction,
) -> None:
    if artifact.privileged:
        privileged_replace(source, artifact.target, mode, token)
    else:
        atomic_user_replace(source, artifact.target, mode, token)


def _remove(
    artifact: Artifact,
    token: str,
    privileged_remove: RemoveFunction,
) -> None:
    if artifact.privileged:
        privileged_remove(artifact.target, token)
    else:
        atomic_user_remove(artifact.target, token)


def _restore_one(
    artifact: Artifact,
    record: dict[str, object],
    transaction: Path,
    token: str,
    privileged_replace: ReplaceFunction,
    privileged_remove: RemoveFunction,
) -> None:
    if not _target_matches(artifact, record, "payload"):
        raise TransactionError("deployed target changed; rollback refused")
    backup = _bound_backup(transaction, record)
    if backup is None:
        _remove(artifact, token, privileged_remove)
        if not _target_matches(artifact, record, "before"):
            raise TransactionError("rollback verification failed")
        return
    before_mode = int(str(record["before_mode"]), 8)
    _replace(artifact, backup, before_mode, token, privileged_replace)
    if not _target_matches(artifact, record, "before"):
        raise TransactionError("rollback verification failed")


def _marker_path(transaction: Path, name: str) -> Path:
    return transaction / f"{name}.json"


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish one same-directory file without replacement."""

    if (
        source.parent != destination.parent
        or source.name in {"", ".", ".."}
        or destination.name in {"", ".", ".."}
    ):
        raise TransactionError("transaction publication paths are invalid")
    _check_directory(source.parent, 0o700)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_fd = os.open(source.parent, flags)
    try:
        library = ctypes.CDLL(None, use_errno=True)
        if hasattr(library, "renameat2"):
            function = library.renameat2
            function.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            function.restype = ctypes.c_int
            result = function(
                parent_fd,
                os.fsencode(source.name),
                parent_fd,
                os.fsencode(destination.name),
                1,  # RENAME_NOREPLACE
            )
        elif hasattr(library, "renamex_np"):
            function = library.renamex_np
            function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            function.restype = ctypes.c_int
            result = function(
                os.fsencode(source),
                os.fsencode(destination),
                0x00000004,  # RENAME_EXCL on Darwin
            )
        else:
            raise TransactionError(
                "atomic no-replace transaction publication is unavailable"
            )
        if result != 0:
            error = ctypes.get_errno()
            if error in {errno.EEXIST, errno.ENOTEMPTY}:
                raise TransactionError("transaction marker already exists")
            raise TransactionError("transaction marker publication failed")
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _write_marker(transaction: Path, name: str, revision: str, manifest_hash: str) -> None:
    marker = {
        "schema": MARKER_SCHEMA,
        "state": name.upper(),
        "repository_revision": revision,
        "manifest_sha256": manifest_hash,
        "activation_deferred": True,
        "services_restarted": False,
    }
    temporary = transaction / (
        f".{name}.json.staging-{os.getpid()}-{secrets.token_hex(8)}"
    )
    _write_exclusive(temporary, _json_bytes(marker))
    _rename_noreplace(temporary, _marker_path(transaction, name))


def _validate_marker(transaction: Path, name: str, revision: str, manifest_hash: str) -> None:
    marker, _content = _load_json(_marker_path(transaction, name))
    if marker != {
        "schema": MARKER_SCHEMA,
        "state": name.upper(),
        "repository_revision": revision,
        "manifest_sha256": manifest_hash,
        "activation_deferred": True,
        "services_restarted": False,
    }:
        raise TransactionError("transaction marker is invalid")


def _remove_exact_marker(
    transaction: Path, name: str, revision: str, manifest_hash: str
) -> None:
    """Remove only a complete marker created by this exact transaction."""
    marker = _marker_path(transaction, name)
    if not marker.exists() and not marker.is_symlink():
        return
    _validate_marker(transaction, name, revision, manifest_hash)
    marker.unlink()
    directory = os.open(transaction, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@_serialized_transaction
def apply_transaction(
    artifacts: list[Artifact],
    transaction: Path,
    *,
    privileged_replace: ReplaceFunction = sudo_atomic_replace,
    privileged_remove: RemoveFunction = sudo_atomic_remove,
) -> dict[str, object]:
    manifest, records, manifest_hash = _validate_manifest(transaction, artifacts)
    marker_paths = (
        _marker_path(transaction, "applied"),
        _marker_path(transaction, "rolled-back"),
    )
    if any(path.exists() or path.is_symlink() for path in marker_paths):
        raise TransactionError("transaction was already consumed")
    for artifact in artifacts:
        record = records[artifact.artifact_id]
        _bound_payload(transaction, record)
        _bound_backup(transaction, record)
        if not _target_matches(artifact, record, "before"):
            raise TransactionError("live preimage changed after staging")

    applied: list[Artifact] = []
    try:
        for artifact in sorted(artifacts, key=lambda item: item.privileged):
            record = records[artifact.artifact_id]
            payload = _bound_payload(transaction, record)
            try:
                _replace(
                    artifact,
                    payload,
                    artifact.target_mode,
                    f"pds005-{artifact.artifact_id}",
                    privileged_replace,
                )
            except Exception:
                # A replace may have crossed its rename boundary before a later
                # fsync or verification error. Include that exact payload in
                # automatic rollback when it is already visible at the target.
                try:
                    if _target_matches(artifact, record, "payload"):
                        applied.append(artifact)
                except TransactionError:
                    pass
                raise
            applied.append(artifact)
            if not _target_matches(artifact, record, "payload"):
                raise TransactionError("deployed target verification failed")
        _write_marker(
            transaction,
            "applied",
            str(manifest["repository_revision"]),
            manifest_hash,
        )
    except Exception as exc:
        rollback_errors: list[Exception] = []
        try:
            _remove_exact_marker(
                transaction,
                "applied",
                str(manifest["repository_revision"]),
                manifest_hash,
            )
        except Exception as caught:
            # Continue restoring live targets even if a partial or altered
            # marker makes this transaction directory unusable afterward.
            rollback_errors.append(caught)
        for artifact in reversed(applied):
            try:
                _restore_one(
                    artifact,
                    records[artifact.artifact_id],
                    transaction,
                    f"pds005-auto-{artifact.artifact_id}",
                    privileged_replace,
                    privileged_remove,
                )
            except Exception as caught:  # preserve both failures for the caller
                # Restoration of one artifact must not strand the other
                # independently recoverable targets at the new version.
                rollback_errors.append(caught)
        for artifact in applied:
            try:
                if not _target_matches(
                    artifact, records[artifact.artifact_id], "before"
                ):
                    rollback_errors.append(
                        TransactionError("automatic rollback verification failed")
                    )
            except Exception as caught:
                rollback_errors.append(caught)
        if rollback_errors:
            raise TransactionError(
                "apply failed and automatic rollback was incomplete"
            ) from rollback_errors[0]
        if isinstance(exc, TransactionError):
            raise
        raise TransactionError("apply failed and was rolled back") from exc
    return {
        "schema": SCHEMA,
        "operation": "APPLIED",
        "artifact_count": len(artifacts),
        "all_targets_verified": True,
        "activation_deferred": True,
        "services_restarted": False,
    }


@_serialized_transaction
def verify_transaction(artifacts: list[Artifact], transaction: Path) -> dict[str, object]:
    manifest, records, manifest_hash = _validate_manifest(transaction, artifacts)
    _validate_marker(
        transaction,
        "applied",
        str(manifest["repository_revision"]),
        manifest_hash,
    )
    if _marker_path(transaction, "rolled-back").exists() or _marker_path(
        transaction, "rolled-back"
    ).is_symlink():
        raise TransactionError("transaction was already rolled back")
    results: list[dict[str, object]] = []
    for artifact in artifacts:
        record = records[artifact.artifact_id]
        try:
            _bound_payload(transaction, record)
            matches = _target_matches(artifact, record, "payload")
        except TransactionError:
            matches = False
        results.append({"artifact_id": artifact.artifact_id, "state": "MATCH" if matches else "NOT_MATCH"})
    complete = all(item["state"] == "MATCH" for item in results)
    return {
        "schema": SCHEMA,
        "operation": "VERIFY",
        "artifacts": results,
        "complete": complete,
        "activation_deferred": True,
        "services_restarted": False,
    }


@_serialized_transaction
def rollback_transaction(
    artifacts: list[Artifact],
    transaction: Path,
    *,
    privileged_replace: ReplaceFunction = sudo_atomic_replace,
    privileged_remove: RemoveFunction = sudo_atomic_remove,
) -> dict[str, object]:
    manifest, records, manifest_hash = _validate_manifest(transaction, artifacts)
    applied_marker = _marker_path(transaction, "applied")
    if applied_marker.exists() or applied_marker.is_symlink():
        # A present marker must be complete and exact.  Only a genuinely absent
        # marker may enter known-state recovery after an interrupted apply.
        _validate_marker(
            transaction,
            "applied",
            str(manifest["repository_revision"]),
            manifest_hash,
        )
    if _marker_path(transaction, "rolled-back").exists() or _marker_path(
        transaction, "rolled-back"
    ).is_symlink():
        raise TransactionError("transaction was already rolled back")
    for artifact in artifacts:
        _bound_payload(transaction, records[artifact.artifact_id])
        _bound_backup(transaction, records[artifact.artifact_id])
        if _known_target_phase(artifact, records[artifact.artifact_id]) is None:
            raise TransactionError("deployed target changed; rollback refused")
    for artifact in reversed(sorted(artifacts, key=lambda item: item.privileged)):
        record = records[artifact.artifact_id]
        phase = _known_target_phase(artifact, record)
        if phase in ("before", "both"):
            # A previous rollback attempt may have crossed its rename boundary
            # before reporting an error.  Exact preimages are safe to skip, so
            # the operator can retry instead of being stranded mid-rollback.
            continue
        if phase != "payload":
            raise TransactionError("deployed target changed; rollback refused")
        _restore_one(
            artifact,
            record,
            transaction,
            f"pds005-rollback-{artifact.artifact_id}",
            privileged_replace,
            privileged_remove,
        )
    if any(
        _known_target_phase(artifact, records[artifact.artifact_id])
        not in ("before", "both")
        for artifact in artifacts
    ):
        raise TransactionError("rollback verification failed")
    _write_marker(
        transaction,
        "rolled-back",
        str(manifest["repository_revision"]),
        manifest_hash,
    )
    return {
        "schema": SCHEMA,
        "operation": "ROLLED-BACK",
        "artifact_count": len(artifacts),
        "all_preimages_verified": True,
        "activation_deferred": True,
        "services_restarted": False,
    }


def repository_revision(repo_root: Path) -> str:
    commands = (("rev-parse", "HEAD"), ("status", "--porcelain=v1", "--untracked-files=normal"))
    outputs: list[bytes] = []
    for arguments in commands:
        try:
            result = subprocess.run(
                ("git", "-C", str(repo_root), *arguments),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransactionError("Git identity probe is unavailable") from exc
        if result.returncode != 0 or len(result.stdout) > 8 * 1024 * 1024:
            raise TransactionError("Git identity probe failed or overflowed")
        outputs.append(result.stdout)
    revision = outputs[0].decode("ascii", errors="strict").strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None or outputs[1]:
        raise TransactionError("staging requires an exact clean repository revision")
    return revision


def _transaction_path(name: str) -> Path:
    if NAME_RE.fullmatch(name) is None:
        raise TransactionError("transaction name is invalid")
    return Path.home() / ".local/state/pocketds-linux-kit/ui-transactions" / name


def recovery_artifact_plan(artifacts: list[Artifact], transaction: Path) -> list[Artifact]:
    """Keep exact older receipts recoverable without permitting partial installs."""
    _check_directory(transaction, 0o700)
    manifest, _content = _load_json(transaction / "manifest.json")
    records = manifest.get("artifacts")
    if type(records) is not list or any(type(record) is not dict for record in records):
        raise TransactionError("transaction artifact records are invalid")
    identities = [record.get("artifact_id") for record in records]
    legacy_ids = [
        "panel-qml", "deep-suspend-guard", "deep-suspend-polkit", "panel-root-helper",
        "keyboard-main", "keyboard-adapter", "keyboard-geometry", "keyboard-voice-artifacts",
    ]
    if identities == legacy_ids:
        by_id = {artifact.artifact_id: artifact for artifact in artifacts}
        try:
            selected = [by_id[identity] for identity in legacy_ids]
        except KeyError as exc:
            raise TransactionError("legacy UI recovery plan is incomplete") from exc
    else:
        selected = artifacts
    # Validate every field, source/payload binding and declared count. Choosing
    # a legacy plan never grants arbitrary targets or applies an older bundle.
    _validate_manifest(transaction, selected)
    return selected


def _ensure_state_root(transaction: Path) -> None:
    root = transaction.parent
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise TransactionError("transaction state root is unsafe")
    os.chmod(root, 0o700)
    _check_directory(root, 0o700)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--stage", metavar="NAME")
    action.add_argument("--apply", metavar="NAME")
    action.add_argument("--verify", metavar="NAME")
    action.add_argument("--rollback", metavar="NAME")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parents[1]
    artifacts = artifact_plan(repo_root, Path.home(), Path("/"))
    try:
        if arguments.stage is not None:
            if arguments.confirm != STAGE_CONFIRMATION:
                raise TransactionError("stage needs the exact confirmation")
            transaction = _transaction_path(arguments.stage)
            _ensure_state_root(transaction)
            revision = repository_revision(repo_root)
            report = stage_transaction(artifacts, transaction, revision)
            if repository_revision(repo_root) != revision:
                raise TransactionError("repository changed while the transaction was staged")
        elif arguments.apply is not None:
            if arguments.confirm != APPLY_CONFIRMATION:
                raise TransactionError("apply needs the exact confirmation")
            report = apply_transaction(artifacts, _transaction_path(arguments.apply))
        elif arguments.verify is not None:
            if arguments.confirm is not None:
                raise TransactionError("read-only verify does not accept confirmation")
            transaction = _transaction_path(arguments.verify)
            report = verify_transaction(recovery_artifact_plan(artifacts, transaction), transaction)
        elif arguments.rollback is not None:
            transaction = _transaction_path(arguments.rollback)
            recovery = recovery_artifact_plan(artifacts, transaction)
            confirmation = LEGACY_ROLLBACK_CONFIRMATION if len(recovery) == 8 else ROLLBACK_CONFIRMATION
            if arguments.confirm != confirmation:
                raise TransactionError("rollback needs the exact confirmation")
            report = rollback_transaction(recovery, transaction)
        else:
            if arguments.confirm is not None:
                raise TransactionError("read-only plan does not accept confirmation")
            report = deployment_plan(artifacts)
    except (TransactionError, OSError, UnicodeError) as exc:
        print(f"UI update transaction failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("complete", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
