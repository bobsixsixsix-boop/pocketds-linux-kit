#!/usr/bin/env python3
"""Bounded, confirmation-gated recovery for the Plasma shell only.

The default action is a read-only status report.  Recovery deliberately avoids
KWin and the user manager, performs at most one stop/start sequence, and emits
one JSON document suitable for both the Panel and an SSH recovery console.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import time
from typing import Callable, Iterator, Protocol, Sequence


SCHEMA_VERSION = 1
UNIT = "plasma-plasmashell.service"
BUS_NAME = "org.kde.plasmashell"
APPLICATION_NAME = "plasmashell"
CONFIRM_ACTION = "BOUNDED-PLASMASHELL-RECOVERY"
WORKER_UNIT = "pocketds-plasma-recovery.service"
REQUEST_MAX_AGE_S = 30.0
STALE_CLEAR_MIN_AGE_S = 90.0
PRESERVE_UNITS = (
    "pocketds-keyboard.service",
    "pocketds-gpu-telemetry.service",
)
SHOW_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "MainPID",
    "NRestarts",
    "Result",
    "ControlGroup",
)
MANUAL_FALLBACK = (
    "systemctl --user status plasma-plasmashell.service --no-pager",
    "journalctl --user -u plasma-plasmashell.service -n 100 --no-pager",
    "systemctl --user kill --kill-whom=all --signal=SIGKILL plasma-plasmashell.service",
    "systemctl --user reset-failed plasma-plasmashell.service",
    "systemctl --user start plasma-plasmashell.service",
)


class RecoveryError(RuntimeError):
    """A fail-closed validation or command error."""


class LockConflict(RecoveryError):
    """Another status/recovery invocation owns the mutex."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class UnitSnapshot:
    unit: str
    load_state: str
    active_state: str
    sub_state: str
    main_pid: int
    n_restarts: int
    result: str
    control_group: str

    @property
    def stopped(self) -> bool:
        return self.active_state in {"inactive", "failed"} and self.main_pid == 0

    @property
    def running(self) -> bool:
        return (
            self.load_state == "loaded"
            and self.active_state == "active"
            and self.sub_state == "running"
            and self.main_pid > 0
        )


class Runner(Protocol):
    def __call__(self, argv: Sequence[str]) -> CommandResult:
        ...


class Clock(Protocol):
    def monotonic(self) -> float:
        ...

    def sleep(self, seconds: float) -> None:
        ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class SubprocessRunner:
    def __init__(self, timeout_s: float = 4.0) -> None:
        self.timeout_s = timeout_s

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        try:
            completed = subprocess.run(
                list(argv),
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CommandResult(124, "", str(exc))
        return CommandResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )


class BusCtlProbe:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def __call__(self) -> bool:
        result = self.runner(
            (
                "/usr/bin/busctl",
                "--user",
                "--no-pager",
                "status",
                BUS_NAME,
            )
        )
        return result.returncode == 0


def _parse_nonnegative_int(value: str, field: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RecoveryError(f"invalid {field}: {value!r}") from exc
    if parsed < 0:
        raise RecoveryError(f"negative {field}: {parsed}")
    return parsed


def parse_snapshot(unit: str, output: str) -> UnitSnapshot:
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key] = value
    missing = set(SHOW_PROPERTIES) - fields.keys()
    if missing:
        raise RecoveryError(f"systemctl show omitted: {sorted(missing)}")
    if fields["Id"] != unit:
        raise RecoveryError(
            f"systemctl returned unit {fields['Id']!r}, expected {unit!r}"
        )
    return UnitSnapshot(
        unit=unit,
        load_state=fields["LoadState"],
        active_state=fields["ActiveState"],
        sub_state=fields["SubState"],
        main_pid=_parse_nonnegative_int(fields["MainPID"], "MainPID"),
        n_restarts=_parse_nonnegative_int(fields["NRestarts"], "NRestarts"),
        result=fields["Result"],
        control_group=fields["ControlGroup"],
    )


def snapshot_to_json(snapshot: UnitSnapshot) -> dict[str, object]:
    return asdict(snapshot)


def validate_confirmations(confirm_unit: str | None, confirm_action: str | None) -> None:
    if confirm_unit != UNIT or confirm_action != CONFIRM_ACTION:
        raise RecoveryError(
            "recovery requires both --confirm-unit plasma-plasmashell.service "
            "and --confirm-action BOUNDED-PLASMASHELL-RECOVERY"
        )


def validate_timeouts(
    quit_timeout: float,
    term_timeout: float,
    kill_timeout: float,
    start_timeout: float,
    poll_interval: float,
) -> None:
    bounds = {
        "quit-timeout": (quit_timeout, 0.25, 5.0),
        "term-timeout": (term_timeout, 0.5, 10.0),
        "kill-timeout": (kill_timeout, 0.25, 5.0),
        "start-timeout": (start_timeout, 1.0, 30.0),
        "poll-interval": (poll_interval, 0.05, 1.0),
    }
    for name, (value, minimum, maximum) in bounds.items():
        if not minimum <= value <= maximum:
            raise RecoveryError(
                f"--{name} must be between {minimum:g} and {maximum:g} seconds"
            )


def default_lock_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime) / "pocketds-plasma-recovery.lock"


def default_request_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime) / "pocketds-plasma-recovery.request"


def default_result_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime) / "pocketds-plasma-recovery-result.json"


def _safe_runtime_file(path: Path, *, flags: int, mode: int = 0o600) -> int:
    if not path.parent.is_dir():
        raise RecoveryError(f"runtime directory is unavailable: {path.parent}")
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags | os.O_CLOEXEC, mode)
    except OSError as exc:
        raise RecoveryError(f"cannot open runtime file {path.name}: {exc}") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise RecoveryError(f"runtime file {path.name} is not regular")
    if metadata.st_uid != os.getuid():
        os.close(descriptor)
        raise RecoveryError(f"runtime file {path.name} has the wrong owner")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        os.close(descriptor)
        raise RecoveryError(f"runtime file {path.name} is wider than 0600")
    return descriptor


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise RecoveryError("short write to runtime handoff file")
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def timeout_settings(
    quit_timeout: float,
    term_timeout: float,
    kill_timeout: float,
    start_timeout: float,
    poll_interval: float,
) -> dict[str, float]:
    validate_timeouts(
        quit_timeout,
        term_timeout,
        kill_timeout,
        start_timeout,
        poll_interval,
    )
    return {
        "quit_timeout": quit_timeout,
        "term_timeout": term_timeout,
        "kill_timeout": kill_timeout,
        "start_timeout": start_timeout,
        "poll_interval": poll_interval,
    }


def write_request(
    path: Path,
    now_unix: float,
    settings: dict[str, float],
    request_id: str | None = None,
) -> str:
    request_id = request_id or secrets.token_hex(16)
    if len(request_id) != 32 or any(
        character not in "0123456789abcdef" for character in request_id
    ):
        raise RecoveryError("request_id must be 32 lowercase hexadecimal characters")
    payload = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "unit": UNIT,
            "action": CONFIRM_ACTION,
            "created_unix_ms": int(now_unix * 1000),
            "trigger_pid": os.getpid(),
            "request_id": request_id,
            "timeouts": settings,
        },
        sort_keys=True,
    ).encode("utf-8")
    descriptor = _safe_runtime_file(
        path,
        flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL,
    )
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)
    return request_id


def consume_request(path: Path, now_unix: float) -> dict[str, object]:
    descriptor = _safe_runtime_file(path, flags=os.O_RDONLY)
    try:
        opened_metadata = os.fstat(descriptor)
        content = os.read(descriptor, 4097)
    finally:
        os.close(descriptor)
    if len(content) > 4096:
        raise RecoveryError("recovery request is too large")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("recovery request is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RecoveryError("recovery request must be a JSON object")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("unit") != UNIT
        or payload.get("action") != CONFIRM_ACTION
        or not isinstance(payload.get("created_unix_ms"), int)
        or not isinstance(payload.get("timeouts"), dict)
        or not isinstance(payload.get("request_id"), str)
    ):
        raise RecoveryError("recovery request contract mismatch")
    request_id = payload["request_id"]
    if len(request_id) != 32 or any(
        character not in "0123456789abcdef" for character in request_id
    ):
        raise RecoveryError("recovery request_id contract mismatch")
    timeout_payload = payload["timeouts"]
    expected_keys = {
        "quit_timeout",
        "term_timeout",
        "kill_timeout",
        "start_timeout",
        "poll_interval",
    }
    if set(timeout_payload) != expected_keys or any(
        not isinstance(timeout_payload[key], (int, float))
        or isinstance(timeout_payload[key], bool)
        for key in expected_keys
    ):
        raise RecoveryError("recovery request timeout contract mismatch")
    timeout_settings(**timeout_payload)
    age_s = now_unix - (int(payload["created_unix_ms"]) / 1000.0)
    if age_s < -2.0 or age_s > REQUEST_MAX_AGE_S:
        raise RecoveryError(f"recovery request is stale or from the future: {age_s:.3f}s")
    # Consume before the first destructive action so a failed worker cannot be
    # restarted into a recovery loop.  The exact path is inside XDG_RUNTIME_DIR.
    try:
        current_metadata = os.lstat(path)
    except OSError as exc:
        raise RecoveryError(f"recovery request changed before consumption: {exc}") from exc
    if (current_metadata.st_dev, current_metadata.st_ino) != (
        opened_metadata.st_dev,
        opened_metadata.st_ino,
    ):
        raise RecoveryError("recovery request changed before consumption")
    try:
        path.unlink()
    except OSError as exc:
        raise RecoveryError(f"cannot consume recovery request: {exc}") from exc
    _fsync_directory(path.parent)
    return payload


def _worker_inactive(runner: Runner) -> dict[str, object]:
    properties = ("Id", "LoadState", "ActiveState", "SubState", "MainPID")
    result = runner(
        (
            "/usr/bin/systemctl",
            "--user",
            "show",
            "--no-pager",
            f"--property={','.join(properties)}",
            WORKER_UNIT,
        )
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RecoveryError(f"cannot inspect recovery worker: {detail}")
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    missing = set(properties) - fields.keys()
    if missing or fields.get("Id") != WORKER_UNIT:
        raise RecoveryError("recovery worker status contract mismatch")
    main_pid = _parse_nonnegative_int(fields["MainPID"], "worker MainPID")
    inactive = fields["ActiveState"] in {"inactive", "failed"} and main_pid == 0
    if not inactive:
        raise RecoveryError("recovery worker is still active; stale request was not removed")
    return {
        "load_state": fields["LoadState"],
        "active_state": fields["ActiveState"],
        "sub_state": fields["SubState"],
        "main_pid": main_pid,
    }


def clear_stale_request(
    runner: Runner,
    path: Path,
    now_unix: float,
) -> dict[str, object]:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return {
            "schema_version": SCHEMA_VERSION,
            "action": "clear-stale-request",
            "unit": UNIT,
            "ok": True,
            "removed": False,
            "plasma_mutations_executed": False,
        }
    except OSError as exc:
        raise RecoveryError(f"cannot inspect recovery request: {exc}") from exc

    descriptor = _safe_runtime_file(path, flags=os.O_RDONLY)
    try:
        opened_metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if opened_metadata.st_nlink != 1:
        raise RecoveryError("recovery request has an unexpected hard-link count")
    age_s = now_unix - opened_metadata.st_mtime
    if age_s < STALE_CLEAR_MIN_AGE_S:
        raise RecoveryError(
            f"recovery request is only {age_s:.3f}s old; wait for the 90s stale gate"
        )
    if age_s > 86400.0:
        # XDG_RUNTIME_DIR should not survive boot.  A wildly old timestamp is
        # treated as corrupt state and remains available for manual inspection.
        raise RecoveryError("recovery request timestamp is implausibly old")

    worker = _worker_inactive(runner)
    try:
        current_metadata = os.lstat(path)
    except OSError as exc:
        raise RecoveryError(f"recovery request changed before cleanup: {exc}") from exc
    if (current_metadata.st_dev, current_metadata.st_ino) != (
        opened_metadata.st_dev,
        opened_metadata.st_ino,
    ):
        raise RecoveryError("recovery request changed before cleanup")
    try:
        path.unlink()
    except OSError as exc:
        raise RecoveryError(f"cannot remove stale recovery request: {exc}") from exc
    _fsync_directory(path.parent)
    return {
        "schema_version": SCHEMA_VERSION,
        "action": "clear-stale-request",
        "unit": UNIT,
        "ok": True,
        "removed": True,
        "request_age_ms": int(age_s * 1000),
        "worker": worker,
        "plasma_mutations_executed": False,
    }


def write_result(path: Path, report: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = _safe_runtime_file(
        temporary,
        flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL,
    )
    try:
        content = (json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        _write_all(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def queue_recovery(
    runner: Runner,
    request_path: Path,
    lock_path: Path,
    now_unix: float,
    settings: dict[str, float],
    confirm_unit: str | None,
    confirm_action: str | None,
) -> dict[str, object]:
    validate_confirmations(confirm_unit, confirm_action)
    # Prepare under the worker mutex, then release it before starting systemd.
    # O_EXCL keeps a second trigger out during the handoff window; once the
    # worker consumes the request it holds this same mutex for the full action.
    with exclusive_lock(lock_path):
        request_id = write_request(request_path, now_unix, settings)
    result = runner(
        (
            "/usr/bin/systemctl",
            "--user",
            "start",
            "--no-block",
            WORKER_UNIT,
        )
    )
    if result.returncode != 0:
        try:
            with exclusive_lock(lock_path):
                request_path.unlink()
                _fsync_directory(request_path.parent)
        except (FileNotFoundError, RecoveryError):
            # The failed queue remains fail-closed if cleanup cannot be proven.
            pass
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RecoveryError(f"cannot queue independent recovery worker: {detail}")
    return {
        "schema_version": SCHEMA_VERSION,
        "action": "queue-recovery",
        "unit": UNIT,
        "worker_unit": WORKER_UNIT,
        "ok": True,
        "queued": True,
        "plasma_mutations_executed": False,
        "request_id": request_id,
        "request_created_unix_ms": int(now_unix * 1000),
        "result_path": str(default_result_path()),
    }


def run_worker(
    runner: Runner,
    request_path: Path,
    result_path: Path,
    now_unix: float,
) -> dict[str, object]:
    request_id: str | None = None
    request_created_unix_ms: int | None = None
    try:
        request = consume_request(request_path, now_unix)
        request_id = str(request["request_id"])
        request_created_unix_ms = int(request["created_unix_ms"])
        settings = timeout_settings(**request["timeouts"])
        controller = PlasmaRecovery(
            runner,
            BusCtlProbe(runner),
            RealClock(),
            **settings,
        )
        report = controller.recover()
    except RecoveryError as exc:
        report = error_report("worker", str(exc))
    except Exception as exc:  # keep the systemd/Panel result contract on bugs
        report = error_report(
            "worker",
            f"unexpected worker failure: {type(exc).__name__}: {exc}",
        )
    report["worker_unit"] = WORKER_UNIT
    report["request_id"] = request_id
    report["request_created_unix_ms"] = request_created_unix_ms
    report["worker_started_unix_ms"] = int(now_unix * 1000)
    write_result(result_path, report)
    return report


@contextmanager
def exclusive_lock(path: Path, wait_timeout: float = 0.0) -> Iterator[None]:
    if not 0.0 <= wait_timeout <= 2.0:
        raise RecoveryError("lock wait timeout must be between 0 and 2 seconds")
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    descriptor = _safe_runtime_file(path, flags=flags)
    try:
        deadline = time.monotonic() + wait_timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockConflict(
                        "another Plasma status/recovery action is active"
                    ) from exc
                time.sleep(min(0.05, remaining))
        yield
    finally:
        os.close(descriptor)


class PlasmaRecovery:
    def __init__(
        self,
        runner: Runner,
        bus_probe: Callable[[], bool],
        clock: Clock,
        *,
        quit_timeout: float = 2.0,
        term_timeout: float = 5.0,
        kill_timeout: float = 2.0,
        start_timeout: float = 12.0,
        poll_interval: float = 0.2,
    ) -> None:
        validate_timeouts(
            quit_timeout,
            term_timeout,
            kill_timeout,
            start_timeout,
            poll_interval,
        )
        self.runner = runner
        self.bus_probe = bus_probe
        self.clock = clock
        self.quit_timeout = quit_timeout
        self.term_timeout = term_timeout
        self.kill_timeout = kill_timeout
        self.start_timeout = start_timeout
        self.poll_interval = poll_interval

    def _run(self, *argv: str) -> CommandResult:
        return self.runner(argv)

    def snapshot(self, unit: str = UNIT) -> UnitSnapshot:
        result = self._run(
            "/usr/bin/systemctl",
            "--user",
            "show",
            "--no-pager",
            f"--property={','.join(SHOW_PROPERTIES)}",
            unit,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit {result.returncode}"
            raise RecoveryError(f"cannot inspect {unit}: {detail}")
        return parse_snapshot(unit, result.stdout)

    def _preserved(self) -> dict[str, UnitSnapshot]:
        return {unit: self.snapshot(unit) for unit in PRESERVE_UNITS}

    @staticmethod
    def _preserve_violations(
        before: dict[str, UnitSnapshot], after: dict[str, UnitSnapshot]
    ) -> list[str]:
        violations: list[str] = []
        for unit in PRESERVE_UNITS:
            old = before[unit]
            new = after[unit]
            fields = ("active_state", "sub_state", "main_pid", "n_restarts")
            changed = [field for field in fields if getattr(old, field) != getattr(new, field)]
            if changed:
                violations.append(f"{unit} changed fields: {','.join(changed)}")
        return violations

    def _wait_stopped(self, timeout: float) -> UnitSnapshot | None:
        deadline = self.clock.monotonic() + timeout
        while True:
            current = self.snapshot()
            if current.stopped:
                return current
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                return None
            self.clock.sleep(min(self.poll_interval, remaining))

    def _wait_healthy(self, timeout: float) -> tuple[UnitSnapshot | None, bool]:
        deadline = self.clock.monotonic() + timeout
        last: UnitSnapshot | None = None
        last_bus = False
        while True:
            last = self.snapshot()
            last_bus = self.bus_probe()
            if last.running and last_bus:
                return last, True
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                return last, last_bus
            self.clock.sleep(min(self.poll_interval, remaining))

    def status(self, action: str = "status") -> dict[str, object]:
        shell = self.snapshot()
        bus_owner = self.bus_probe()
        preserved = self._preserved()
        healthy = shell.running and bus_owner
        report: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "action": action,
            "unit": UNIT,
            "ok": healthy,
            "healthy": healthy,
            "mutations_executed": False,
            "shell": snapshot_to_json(shell),
            "bus_name": BUS_NAME,
            "bus_owner": bus_owner,
            "preserved_services": {
                unit: snapshot_to_json(snapshot) for unit, snapshot in preserved.items()
            },
        }
        if action == "dry-run":
            report["plan"] = [
                "stop only plasma-plasmashell.service --no-block through systemd",
                "on SIGTERM timeout: SIGKILL only that unit cgroup",
                "reset-failed once and start only that unit once",
                "wait for active/running and org.kde.plasmashell bus owner",
                "verify keyboard and GPU telemetry snapshots are unchanged",
            ]
        return report

    def recover(self) -> dict[str, object]:
        before_shell = self.snapshot()
        before_preserved = self._preserved()
        events: list[dict[str, object]] = []
        report: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "action": "recover",
            "unit": UNIT,
            "ok": False,
            "healthy": False,
            "mutations_executed": False,
            "before": snapshot_to_json(before_shell),
            "events": events,
            "manual_fallback": list(MANUAL_FALLBACK),
        }

        def command(label: str, *argv: str) -> CommandResult:
            result = self._run(*argv)
            events.append(
                {
                    "event": label,
                    "returncode": result.returncode,
                    "stderr": result.stderr.strip()[:400],
                }
            )
            report["mutations_executed"] = True
            return result

        def finish_failure(message: str) -> dict[str, object]:
            report["error"] = message
            try:
                report["after"] = snapshot_to_json(self.snapshot())
                after_preserved = self._preserved()
                violations = self._preserve_violations(before_preserved, after_preserved)
                report["preserve_violations"] = violations
                report["preserved_services_after"] = {
                    unit: snapshot_to_json(snapshot)
                    for unit, snapshot in after_preserved.items()
                }
            except RecoveryError as exc:
                report["postcheck_error"] = str(exc)
            return report

        stopped = before_shell.stopped
        if stopped:
            events.append({"event": "already-inactive", "returncode": 0})

        # Stop through the unit manager first: a KDE-only quit races with
        # Restart=always and can kill a freshly auto-respawned shell.
        if not stopped:
            stop_result = command(
                "sigterm-stop",
                "/usr/bin/systemctl",
                "--user",
                "stop",
                "--no-block",
                UNIT,
            )
            if stop_result.returncode != 0:
                return finish_failure("bounded SIGTERM stop request failed")
            stopped_snapshot = self._wait_stopped(self.term_timeout)
            if stopped_snapshot is not None:
                stopped = True
                events.append({"event": "sigterm-stop-observed", "returncode": 0})

        if not stopped:
            kill_result = command(
                "unit-cgroup-sigkill",
                "/usr/bin/systemctl",
                "--user",
                "kill",
                "--kill-whom=all",
                "--signal=SIGKILL",
                UNIT,
            )
            if kill_result.returncode != 0:
                return finish_failure("unit-cgroup SIGKILL request failed")
            stopped_snapshot = self._wait_stopped(self.kill_timeout)
            if stopped_snapshot is None:
                return finish_failure("unit remained active after bounded cgroup SIGKILL")
            events.append({"event": "unit-cgroup-sigkill-observed", "returncode": 0})

        reset_result = command(
            "reset-failed",
            "/usr/bin/systemctl",
            "--user",
            "reset-failed",
            UNIT,
        )
        if reset_result.returncode != 0:
            return finish_failure("reset-failed failed; start was not attempted")

        start_result = command(
            "start-once",
            "/usr/bin/systemctl",
            "--user",
            "start",
            "--no-block",
            UNIT,
        )
        if start_result.returncode != 0:
            return finish_failure("single Plasma start request failed")

        after_shell, bus_owner = self._wait_healthy(self.start_timeout)
        after_preserved = self._preserved()
        violations = self._preserve_violations(before_preserved, after_preserved)
        report["after"] = (
            snapshot_to_json(after_shell) if after_shell is not None else None
        )
        report["bus_owner"] = bus_owner
        report["preserve_violations"] = violations
        report["preserved_services_after"] = {
            unit: snapshot_to_json(snapshot)
            for unit, snapshot in after_preserved.items()
        }
        if after_shell is None or not after_shell.running or not bus_owner:
            report["error"] = "Plasma did not regain active/running state and bus ownership"
            return report
        if violations:
            report["error"] = "unrelated protected services changed during recovery"
            return report
        report["ok"] = True
        report["healthy"] = True
        report.pop("manual_fallback", None)
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read or perform bounded recovery of Plasma shell only."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="read-only status (default)")
    mode.add_argument("--dry-run", action="store_true", help="read-only recovery plan")
    mode.add_argument("--recover", action="store_true", help="perform one bounded recovery")
    mode.add_argument(
        "--clear-stale-request",
        action="store_true",
        help="remove only a proven stale request while the worker is inactive",
    )
    mode.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--confirm-unit")
    parser.add_argument("--confirm-action")
    parser.add_argument("--quit-timeout", type=float, default=2.0)
    parser.add_argument("--term-timeout", type=float, default=5.0)
    parser.add_argument("--kill-timeout", type=float, default=2.0)
    parser.add_argument("--start-timeout", type=float, default=12.0)
    parser.add_argument("--poll-interval", type=float, default=0.2)
    return parser


def error_report(action: str, message: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "action": action,
        "unit": UNIT,
        "ok": False,
        "error": message,
        "manual_fallback": list(MANUAL_FALLBACK),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    action = (
        "worker"
        if args.worker
        else "clear-stale-request"
        if args.clear_stale_request
        else "recover"
        if args.recover
        else "dry-run"
        if args.dry_run
        else "status"
    )
    try:
        validate_timeouts(
            args.quit_timeout,
            args.term_timeout,
            args.kill_timeout,
            args.start_timeout,
            args.poll_interval,
        )
        if args.recover or args.clear_stale_request:
            validate_confirmations(args.confirm_unit, args.confirm_action)
        elif args.confirm_unit is not None or args.confirm_action is not None:
            raise RecoveryError("confirmation arguments are valid only with --recover")
        runner = SubprocessRunner()
        settings = timeout_settings(
            args.quit_timeout,
            args.term_timeout,
            args.kill_timeout,
            args.start_timeout,
            args.poll_interval,
        )
        if action == "recover":
            report = queue_recovery(
                runner,
                default_request_path(),
                default_lock_path(),
                time.time(),
                settings,
                args.confirm_unit,
                args.confirm_action,
            )
        elif action == "clear-stale-request":
            with exclusive_lock(default_lock_path()):
                report = clear_stale_request(
                    runner,
                    default_request_path(),
                    time.time(),
                )
        else:
            lock_wait = 2.0 if action == "worker" else 0.0
            with exclusive_lock(default_lock_path(), wait_timeout=lock_wait):
                if action == "worker":
                    report = run_worker(
                        runner,
                        default_request_path(),
                        default_result_path(),
                        time.time(),
                    )
                else:
                    recovery = PlasmaRecovery(
                        runner,
                        BusCtlProbe(runner),
                        RealClock(),
                        **settings,
                    )
                    report = recovery.status(action)
    except LockConflict as exc:
        report = error_report(action, str(exc))
        report["lock_conflict"] = True
    except RecoveryError as exc:
        report = error_report(action, str(exc))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
