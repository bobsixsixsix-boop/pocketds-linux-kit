#!/usr/bin/env python3
"""Stage, apply, verify, activate, and roll back seven keyboard and transcription API artifacts.

Despite the compatibility `.sh` name, this is a standard-library Python
transaction driver. It reuses the already-tested PDS-005 file transaction but
keeps ASR deployment separate from the exact six-file UI transaction.
An existing failed unit can be repaired; its original failure is recorded, and
rollback restores its files and leaves it stopped rather than replaying a crash.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import fcntl
from functools import wraps
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import stat
import subprocess
import sys
from typing import Callable, Sequence


SCHEMA = "pocketds.asr-api-update.v1"
UNIT_SCHEMA = "pocketds.asr-api-unit-state.v1"
ACTIVATION_INTENT_SCHEMA = "pocketds.asr-api-activation-intent.v1"
ACTIVATION_COMPLETE_SCHEMA = "pocketds.asr-api-activation-complete.v1"
ACTIVATION_ROLLBACK_SCHEMA = "pocketds.asr-api-activation-rollback.v1"
UNIT = "pocketds-keyboard.service"
STAGE_CONFIRMATION = "POCKETDS-STAGE-ASR-API-SEVEN-FILES"
APPLY_CONFIRMATION = "POCKETDS-APPLY-ASR-API-SEVEN-FILES"
ROLLBACK_CONFIRMATION = "POCKETDS-ROLLBACK-ASR-API-SEVEN-FILES"
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
MAX_STATE_BYTES = 16 * 1024
ACTIVE_RESTART_ARTIFACTS = frozenset(
    {
        "keyboard-main",
        "keyboard-visibility-state",
        "keyboard-adapter",
        "keyboard-geometry",
        "keyboard-voice-artifacts",
        "keyboard-unit",
    }
)
ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
STATE_ROOT = ACCOUNT_HOME / ".local/state/pocketds-linux-kit/asr-api-transactions"


class InstallError(RuntimeError):
    """The focused ASR deployment or unit-state transition was unsafe."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str = ""


UnitCommand = Callable[[tuple[str, ...]], CommandResult]


def _shared_transaction():
    name = "pds005_ui_update_transaction_for_asr_api"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).resolve().with_name("pds005-ui-update-transaction.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise InstallError("shared file transaction cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def _wrapper_transaction_lock(transaction: Path):
    """Serialize file markers and user-unit transitions as one operation."""

    shared = _shared_transaction()
    parent = transaction.parent
    shared._check_directory(parent, 0o700)
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_fd = os.open(parent, parent_flags)
    descriptor = -1
    name = ".asr-api-wrapper.lock"
    try:
        descriptor = os.open(
            name,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        metadata = os.fstat(descriptor)
        canonical = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (metadata.st_dev, metadata.st_ino) != (canonical.st_dev, canonical.st_ino)
        ):
            raise InstallError("ASR wrapper lock identity is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (metadata.st_dev, metadata.st_ino) != (after.st_dev, after.st_ino):
            raise InstallError("ASR wrapper lock changed identity")
        yield
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        os.close(parent_fd)


def _serialized_wrapper(function):
    @wraps(function)
    def wrapped(artifacts: list[object], transaction: Path, *args, **kwargs):
        with _wrapper_transaction_lock(transaction):
            return function(artifacts, transaction, *args, **kwargs)

    return wrapped


def artifact_plan(repo_root: Path, home: Path) -> list[object]:
    shared = _shared_transaction()
    uid = os.getuid()
    return [
        shared.Artifact(
            "keyboard-main",
            repo_root / "components/keyboard/pocketds-keyboard.py",
            home / ".local/bin/pocketds-keyboard.py",
            uid,
            0o755,
            False,
        ),
        shared.Artifact(
            "keyboard-visibility-state",
            repo_root / "components/keyboard/visibility_state.py",
            home / ".local/bin/visibility_state.py",
            uid,
            0o644,
            False,
        ),
        shared.Artifact(
            "keyboard-adapter",
            repo_root / "components/keyboard/keyboard_adapter.py",
            home / ".local/bin/keyboard_adapter.py",
            uid,
            0o644,
            False,
        ),
        shared.Artifact(
            "keyboard-geometry",
            repo_root / "components/keyboard/screen_geometry.py",
            home / ".local/bin/screen_geometry.py",
            uid,
            0o644,
            False,
        ),
        shared.Artifact(
            "keyboard-voice-artifacts",
            repo_root / "components/keyboard/voice_artifacts.py",
            home / ".local/bin/voice_artifacts.py",
            uid,
            0o644,
            False,
        ),
        shared.Artifact(
            "keyboard-unit",
            repo_root / "components/keyboard/pocketds-keyboard.service",
            home / ".config/systemd/user/pocketds-keyboard.service",
            uid,
            0o644,
            False,
        ),
        shared.Artifact(
            "asr-api-provisioner",
            repo_root / "scripts/pocketds-asr-api-provision.py",
            home / ".local/bin/pocketds-asr-api-provision",
            uid,
            0o755,
            False,
        ),
    ]


def _user_manager_environment(
    *, runtime_root: Path = Path("/run/user"), uid: int | None = None
) -> dict[str, str]:
    """Bind systemctl to this uid's owned runtime directory and bus socket."""

    owner = os.getuid() if uid is None else uid
    runtime = runtime_root / str(owner)
    bus = runtime / "bus"
    try:
        runtime_metadata = runtime.lstat()
        bus_metadata = bus.lstat()
    except OSError:
        raise InstallError("user manager runtime or bus is unavailable") from None
    if (
        not stat.S_ISDIR(runtime_metadata.st_mode)
        or stat.S_ISLNK(runtime_metadata.st_mode)
        or runtime_metadata.st_uid != owner
        or stat.S_IMODE(runtime_metadata.st_mode) != 0o700
    ):
        raise InstallError("user manager runtime directory is unsafe")
    if (
        not stat.S_ISSOCK(bus_metadata.st_mode)
        or stat.S_ISLNK(bus_metadata.st_mode)
        or bus_metadata.st_uid != owner
    ):
        raise InstallError("user manager bus socket is unsafe")
    return {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "XDG_RUNTIME_DIR": str(runtime),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}",
    }


def _systemctl(arguments: tuple[str, ...]) -> CommandResult:
    try:
        result = subprocess.run(
            ("systemctl", "--user", *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=20,
            env=_user_manager_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError("bounded user-unit operation is unavailable") from None
    if len(result.stdout) > 4_096:
        raise InstallError("user-unit response overflowed")
    try:
        output = result.stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        raise InstallError("user-unit response encoding is invalid") from None
    return CommandResult(result.returncode, output)


def capture_unit_state(*, unit_command: UnitCommand = _systemctl) -> dict[str, str]:
    enabled_result = unit_command(("is-enabled", UNIT))
    active_result = unit_command(("is-active", UNIT))
    enabled = enabled_result.output
    active = active_result.output
    if enabled not in {"enabled", "disabled", "static", "masked", "not-found"}:
        raise InstallError("keyboard unit enabled state is unsupported")
    if active not in {"active", "inactive", "failed"}:
        raise InstallError("keyboard unit active state is unsupported")
    return {"enabled": enabled, "active": active}


def _run_unit_mutation(
    arguments: tuple[str, ...], *, unit_command: UnitCommand
) -> None:
    result = unit_command(arguments)
    if result.returncode != 0:
        raise InstallError("user-unit mutation failed")


def _state_content(state: dict[str, str]) -> bytes:
    value = {
        "schema": UNIT_SCHEMA,
        "unit": UNIT,
        "enabled": state["enabled"],
        "active": state["active"],
    }
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _validate_active_restart_preimages(
    state: dict[str, str], artifacts: list[object], transaction: Path
) -> None:
    """An active service must have every file needed to restart its preimage."""

    if state["active"] != "active":
        return
    shared = _shared_transaction()
    _manifest, records, _manifest_hash = shared._validate_manifest(
        transaction, artifacts
    )
    if set(records).intersection(ACTIVE_RESTART_ARTIFACTS) != set(
        ACTIVE_RESTART_ARTIFACTS
    ):
        raise InstallError("active keyboard restart artifact plan is incomplete")
    missing = [
        artifact_id
        for artifact_id in ACTIVE_RESTART_ARTIFACTS
        if records[artifact_id]["before_state"] != "PRESENT"
    ]
    if missing:
        raise InstallError("active keyboard runtime has no complete restart preimage")


def _load_state(transaction: Path) -> dict[str, str]:
    shared = _shared_transaction()
    value, _content = shared._load_json(
        transaction / "unit-state.json", maximum=MAX_STATE_BYTES
    )
    if set(value) != {"schema", "unit", "enabled", "active"}:
        raise InstallError("unit-state fields are invalid")
    if value["schema"] != UNIT_SCHEMA or value["unit"] != UNIT:
        raise InstallError("unit-state identity is invalid")
    enabled = value["enabled"]
    active = value["active"]
    if (
        type(enabled) is not str
        or enabled not in {"enabled", "disabled", "static", "masked", "not-found"}
        or type(active) is not str
        or active not in {"active", "inactive", "failed"}
    ):
        raise InstallError("unit-state values are invalid")
    return {"enabled": enabled, "active": active}


def _activation_intent_path(transaction: Path) -> Path:
    return transaction / "activation-intent.json"


def _activation_complete_path(transaction: Path) -> Path:
    return transaction / "activation-complete.json"


def _activation_rollback_path(transaction: Path) -> Path:
    return transaction / "activation-rollback.json"


def _fsync_transaction_directory(transaction: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(transaction, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_transaction_marker(
    transaction: Path, destination_name: str, content: bytes
) -> None:
    """Publish a complete marker atomically; a crash may leave only a temp."""

    if destination_name not in {
        "activation-intent.json",
        "activation-complete.json",
        "activation-rollback.json",
    }:
        raise InstallError("transaction marker name is invalid")
    shared = _shared_transaction()
    temporary = transaction / (
        f".{destination_name}.staging-{os.getpid()}-{secrets.token_hex(8)}"
    )
    shared._write_exclusive(temporary, content)
    _rename_transaction_noreplace(temporary, transaction / destination_name)


def _rename_transaction_noreplace(source: Path, destination: Path) -> None:
    if source.parent != destination.parent or source.name in {"", ".", ".."}:
        raise InstallError("transaction publication paths are invalid")
    shared = _shared_transaction()
    shared._check_directory(source.parent, 0o700)
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
            raise InstallError("atomic no-replace transaction publication is unavailable")
        if result != 0:
            error = ctypes.get_errno()
            if error in {errno.EEXIST, errno.ENOTEMPTY}:
                raise InstallError("ASR transaction target already exists")
            raise InstallError("ASR transaction could not be published atomically")
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _write_activation_intent(transaction: Path, *, requested: bool) -> None:
    shared = _shared_transaction()
    content = (
        json.dumps(
            {
                "schema": ACTIVATION_INTENT_SCHEMA,
                "unit": UNIT,
                "activation_requested": requested,
                "credentials_written": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    _write_transaction_marker(transaction, "activation-intent.json", content)


def _load_activation_intent(transaction: Path) -> bool:
    shared = _shared_transaction()
    value, _content = shared._load_json(
        _activation_intent_path(transaction), maximum=MAX_STATE_BYTES
    )
    if value != {
        "schema": ACTIVATION_INTENT_SCHEMA,
        "unit": UNIT,
        "activation_requested": value.get("activation_requested"),
        "credentials_written": False,
    } or type(value.get("activation_requested")) is not bool:
        raise InstallError("activation intent is invalid")
    return bool(value["activation_requested"])


def _write_activation_complete(transaction: Path, *, requested: bool) -> None:
    shared = _shared_transaction()
    content = (
        json.dumps(
            {
                "schema": ACTIVATION_COMPLETE_SCHEMA,
                "unit": UNIT,
                "activation_requested": requested,
                "activation_performed": requested,
                "credentials_written": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    _write_transaction_marker(transaction, "activation-complete.json", content)


def _load_activation_complete(transaction: Path) -> bool | None:
    path = _activation_complete_path(transaction)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InstallError("activation completion marker is unavailable") from exc
    shared = _shared_transaction()
    value, _content = shared._load_json(path, maximum=MAX_STATE_BYTES)
    requested = value.get("activation_requested")
    performed = value.get("activation_performed")
    if value != {
        "schema": ACTIVATION_COMPLETE_SCHEMA,
        "unit": UNIT,
        "activation_requested": requested,
        "activation_performed": performed,
        "credentials_written": False,
    } or type(requested) is not bool or type(performed) is not bool or performed != requested:
        raise InstallError("activation completion marker is invalid")
    return bool(performed)


def _write_activation_rollback(transaction: Path, *, requested: bool) -> None:
    shared = _shared_transaction()
    content = (
        json.dumps(
            {
                "schema": ACTIVATION_ROLLBACK_SCHEMA,
                "unit": UNIT,
                "activation_requested": requested,
                "unit_state_restored": requested,
                "credentials_written": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    _write_transaction_marker(transaction, "activation-rollback.json", content)


def _load_activation_rollback(transaction: Path) -> bool | None:
    path = _activation_rollback_path(transaction)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InstallError("activation rollback marker is unavailable") from exc
    shared = _shared_transaction()
    value, _content = shared._load_json(path, maximum=MAX_STATE_BYTES)
    requested = value.get("activation_requested")
    restored = value.get("unit_state_restored")
    if value != {
        "schema": ACTIVATION_ROLLBACK_SCHEMA,
        "unit": UNIT,
        "activation_requested": requested,
        "unit_state_restored": restored,
        "credentials_written": False,
    } or type(requested) is not bool or type(restored) is not bool or restored != requested:
        raise InstallError("activation rollback marker is invalid")
    return bool(requested)


def _shared_rollback_is_complete(
    shared: object, artifacts: list[object], transaction: Path
) -> bool:
    marker = transaction / "rolled-back.json"
    try:
        marker.lstat()
    except FileNotFoundError:
        return False
    manifest, _records, manifest_hash = shared._validate_manifest(transaction, artifacts)
    shared._validate_marker(
        transaction,
        "rolled-back",
        str(manifest["repository_revision"]),
        manifest_hash,
    )
    return True


@contextmanager
def _signal_rollback_guard():
    previous: dict[int, object] = {}

    def caught(signum: int, _frame: object) -> None:
        raise InstallError(f"deployment interrupted by signal {signum}")

    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    try:
        for item in signals:
            previous[item] = signal.getsignal(item)
            signal.signal(item, caught)
        yield
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)


@_serialized_wrapper
def stage_transaction(
    artifacts: list[object],
    transaction: Path,
    revision: str,
    *,
    unit_command: UnitCommand = _systemctl,
) -> dict[str, object]:
    state = capture_unit_state(unit_command=unit_command)
    shared = _shared_transaction()
    shared._check_directory(transaction.parent, 0o700)
    temporary = transaction.with_name(
        f".{transaction.name}.staging-{os.getpid()}-{secrets.token_hex(8)}"
    )
    result = shared.stage_transaction(artifacts, temporary, revision)
    try:
        _validate_active_restart_preimages(state, artifacts, temporary)
        shared._write_exclusive(temporary / "unit-state.json", _state_content(state))
        for directory in (temporary / "payload", temporary / "backup", temporary):
            _fsync_transaction_directory(directory)
        _rename_transaction_noreplace(temporary, transaction)
    except InstallError:
        raise
    except Exception as exc:
        raise InstallError("unit-state snapshot could not be bound to transaction") from exc
    return {
        "schema": SCHEMA,
        "operation": "STAGED",
        "artifact_count": result["artifact_count"],
        "activation_deferred": True,
        "credentials_written": False,
        "unit_state_captured": True,
    }


def _restore_unit_state(
    before: dict[str, str], *, unit_command: UnitCommand, force_restart: bool = False
) -> None:
    _run_unit_mutation(("daemon-reload",), unit_command=unit_command)
    current = capture_unit_state(unit_command=unit_command)
    if current["enabled"] != before["enabled"]:
        raise InstallError("keyboard unit enabled state changed outside this transaction")
    expected = dict(before)
    if before["active"] == "failed":
        # Preserve the failed preimage in unit-state.json, but never recreate
        # a crash by starting the broken generation during rollback. Restore
        # its stopped lifecycle instead; systemd's failure flag is diagnostic.
        expected["active"] = "inactive"
        _run_unit_mutation(("stop", UNIT), unit_command=unit_command)
        _run_unit_mutation(("reset-failed", UNIT), unit_command=unit_command)
    elif before["active"] == "active" and force_restart:
        # Files may already be rolled back while the active Python process is
        # still the newly deployed generation. A state-only comparison cannot
        # distinguish that process image, so rollback must restart explicitly.
        _run_unit_mutation(("restart", UNIT), unit_command=unit_command)
    elif current["active"] != before["active"]:
        operation = "start" if before["active"] == "active" else "stop"
        _run_unit_mutation((operation, UNIT), unit_command=unit_command)
    if capture_unit_state(unit_command=unit_command) != expected:
        raise InstallError("keyboard unit state restoration did not verify")


def _post_install_enabled_state(before: dict[str, str]) -> str:
    """Return the only allowed is-enabled transition caused by a new unit file."""

    return "disabled" if before["enabled"] == "not-found" else before["enabled"]


@_serialized_wrapper
def apply_transaction(
    artifacts: list[object],
    transaction: Path,
    *,
    activate: bool,
    unit_command: UnitCommand = _systemctl,
) -> dict[str, object]:
    shared = _shared_transaction()
    before = _load_state(transaction)
    apply_started = False
    try:
        with _signal_rollback_guard():
            # This durable intent must exist before the shared transaction can
            # replace the first live file.  After power loss, rollback can then
            # distinguish an unresolved activation request from a clean stage.
            _write_activation_intent(transaction, requested=activate)
            apply_started = True
            result = shared.apply_transaction(artifacts, transaction)
            if activate:
                _run_unit_mutation(("daemon-reload",), unit_command=unit_command)
                current = capture_unit_state(unit_command=unit_command)
                expected_before_activation = {
                    "enabled": _post_install_enabled_state(before),
                    "active": before["active"],
                }
                if current != expected_before_activation:
                    raise InstallError("keyboard unit state changed before activation")
                if before["active"] == "failed":
                    # A repaired unit may otherwise still hit StartLimitBurst.
                    # Intent and the original failed state are already durable.
                    _run_unit_mutation(("reset-failed", UNIT), unit_command=unit_command)
                _run_unit_mutation(("restart", UNIT), unit_command=unit_command)
                active = capture_unit_state(unit_command=unit_command)
                if (
                    active["enabled"] != expected_before_activation["enabled"]
                    or active["active"] != "active"
                ):
                    raise InstallError("activated keyboard unit did not verify")
            _write_activation_complete(transaction, requested=activate)
    except Exception as exc:
        rollback_errors: list[Exception] = []
        rollback_complete = False
        if apply_started:
            try:
                if not _shared_rollback_is_complete(shared, artifacts, transaction):
                    shared.rollback_transaction(artifacts, transaction)
                rollback_complete = True
            except Exception as caught:
                rollback_errors.append(caught)
        if apply_started and activate:
            try:
                _restore_unit_state(
                    before, unit_command=unit_command, force_restart=True
                )
            except Exception as caught:
                rollback_errors.append(caught)
        if apply_started and rollback_complete and not rollback_errors:
            try:
                _write_activation_rollback(transaction, requested=activate)
            except Exception as caught:
                rollback_errors.append(caught)
        if rollback_errors:
            raise InstallError(
                "ASR apply failed and exact file/unit rollback was incomplete"
            ) from rollback_errors[0]
        if isinstance(exc, InstallError):
            raise
        raise InstallError("ASR apply failed and was rolled back") from exc
    return {
        "schema": SCHEMA,
        "operation": "APPLIED",
        "artifact_count": result["artifact_count"],
        "activation_performed": activate,
        "credentials_written": False,
        "unit_enablement_preserved": True,
    }


@_serialized_wrapper
def verify_transaction(
    artifacts: list[object],
    transaction: Path,
    *,
    unit_command: UnitCommand = _systemctl,
) -> dict[str, object]:
    shared = _shared_transaction()
    result = shared.verify_transaction(artifacts, transaction)
    requested = _load_activation_intent(transaction)
    activated = _load_activation_complete(transaction)
    if activated is None or activated != requested:
        raise InstallError("activation is incomplete; explicit rollback is required")
    before = _load_state(transaction)
    unit_verified = True
    if activated:
        current = capture_unit_state(unit_command=unit_command)
        unit_verified = (
            current["enabled"] == _post_install_enabled_state(before)
            and current["active"] == "active"
        )
    complete = bool(result["complete"]) and unit_verified
    return {
        "schema": SCHEMA,
        "operation": "VERIFY",
        "artifact_count": len(artifacts),
        "activation_performed": activated,
        "unit_verified": unit_verified,
        "complete": complete,
        "credentials_written": False,
    }


@_serialized_wrapper
def rollback_transaction(
    artifacts: list[object],
    transaction: Path,
    *,
    unit_command: UnitCommand = _systemctl,
) -> dict[str, object]:
    shared = _shared_transaction()
    before = _load_state(transaction)
    requested = _load_activation_intent(transaction)
    completed = _load_activation_complete(transaction)
    if completed is not None and completed != requested:
        raise InstallError("activation intent and completion disagree")
    rolled_activation = _load_activation_rollback(transaction)
    if rolled_activation is not None:
        if rolled_activation != requested:
            raise InstallError("activation rollback disagrees with intent")
        raise InstallError("ASR transaction was already rolled back")
    if requested:
        current = capture_unit_state(unit_command=unit_command)
        allowed = (
            before,
            {
                "enabled": _post_install_enabled_state(before),
                "active": "active",
            },
        )
        if before["active"] == "failed":
            # An interrupted repair can stop after clearing the failure flag,
            # before restart. Its durable intent still permits exact file undo.
            allowed += ({"enabled": before["enabled"], "active": "inactive"},)
        if current not in allowed:
            raise InstallError("activated keyboard unit state drifted; rollback refused")
    if not _shared_rollback_is_complete(shared, artifacts, transaction):
        shared.rollback_transaction(artifacts, transaction)
    if requested:
        _restore_unit_state(before, unit_command=unit_command, force_restart=True)
    _write_activation_rollback(transaction, requested=requested)
    return {
        "schema": SCHEMA,
        "operation": "ROLLED-BACK",
        "artifact_count": len(artifacts),
        "all_preimages_verified": True,
        "unit_state_restored": requested,
        "prior_failed_unit_left_stopped": requested and before["active"] == "failed",
        "credentials_written": False,
    }


def _transaction_path(name: str) -> Path:
    if NAME_RE.fullmatch(name) is None:
        raise InstallError("transaction name is invalid")
    return STATE_ROOT / name


def _ensure_state_root(transaction: Path) -> None:
    shared = _shared_transaction()
    transaction.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shared._check_directory(transaction.parent, 0o700)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--stage", metavar="NAME")
    operation.add_argument("--apply", metavar="NAME")
    operation.add_argument("--verify", metavar="NAME")
    operation.add_argument("--rollback", metavar="NAME")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--confirm")
    arguments = parser.parse_args(list(argv))
    if arguments.activate and arguments.apply is None:
        parser.error("--activate is valid only with --apply")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parents[1]
    artifacts = artifact_plan(repo_root, ACCOUNT_HOME)
    shared = _shared_transaction()
    try:
        if arguments.stage is not None:
            if arguments.confirm != STAGE_CONFIRMATION:
                raise InstallError("stage needs the exact confirmation")
            transaction = _transaction_path(arguments.stage)
            _ensure_state_root(transaction)
            revision = shared.repository_revision(repo_root)
            report = stage_transaction(artifacts, transaction, revision)
            if shared.repository_revision(repo_root) != revision:
                raise InstallError("repository changed while ASR transaction was staged")
        elif arguments.apply is not None:
            if arguments.confirm != APPLY_CONFIRMATION:
                raise InstallError("apply needs the exact confirmation")
            report = apply_transaction(
                artifacts,
                _transaction_path(arguments.apply),
                activate=arguments.activate,
            )
        elif arguments.verify is not None:
            if arguments.confirm is not None:
                raise InstallError("read-only verify does not accept confirmation")
            report = verify_transaction(artifacts, _transaction_path(arguments.verify))
        elif arguments.rollback is not None:
            if arguments.confirm != ROLLBACK_CONFIRMATION:
                raise InstallError("rollback needs the exact confirmation")
            report = rollback_transaction(artifacts, _transaction_path(arguments.rollback))
        else:
            if arguments.confirm is not None or arguments.activate:
                raise InstallError("read-only plan does not accept mutation options")
            source = shared.deployment_plan(artifacts)
            report = {
                "schema": SCHEMA,
                "operation": "PLAN",
                "artifact_count": len(artifacts),
                "artifacts": source["artifacts"],
                "safe_to_stage": source["safe_to_stage"],
                "activation_deferred": True,
                "credentials_written": False,
            }
    except (InstallError, shared.TransactionError, OSError, UnicodeError) as exc:
        print(f"ASR API update transaction failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("complete", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
