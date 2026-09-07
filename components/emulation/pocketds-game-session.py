#!/usr/bin/env python3
"""Supervise one Pocket DS game session without broad process matching.

The supervisor owns the global game-session flock and one tokenized
InputPlumber lease.  Native applications run in a uniquely named user scope;
Wiliwili is bound to the unique numeric Flatpak instance reported after its
launch.  A nested RetroArch process may join an ES-DE session only after it
validates the live runtime record, and never acquires or restores input itself.
"""

from __future__ import annotations

import argparse
import enum
import fcntl
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple


PROTOCOL = "pds-game-session-v1"
INPUT_PROTOCOL = "pds-input-v1"
WILIWILI_APP_ID = "cn.xfangfang.wiliwili"
WILIWILI_BRANCH = "stable"
VALID_NATIVE_NAMES = frozenset(("es-de", "retroarch", "melonds", "steam"))
VALID_INPUT_MODES = frozenset(("gamepad", "joymouse"))
TOKEN_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RECEIPT_LIMIT = 256
FLATPAK_INSTANCE_LIMIT = 64
RECORD_LIMIT = 16 * 1024


class SupervisorError(RuntimeError):
    """A bounded, user-visible supervisor failure."""


class BusyError(SupervisorError):
    """The global game-session lease is already owned."""


class Phase(enum.Enum):
    INIT = "init"
    LOCKED = "locked"
    PREFLIGHT = "preflight"
    ACQUIRING_INPUT = "acquiring-input"
    INPUT_OWNED = "input-owned"
    STARTING_BACKEND = "starting-backend"
    RUNNING = "running"
    STOPPING = "stopping"
    RESTORING = "restoring"
    DONE = "done"
    JOIN_VALIDATING = "join-validating"
    JOIN_EXEC = "join-exec"
    JOIN_RUNNING = "join-running"
    JOIN_STOPPING = "join-stopping"


ALLOWED_TRANSITIONS = {
    Phase.INIT: {Phase.LOCKED, Phase.JOIN_VALIDATING, Phase.DONE},
    Phase.LOCKED: {Phase.PREFLIGHT, Phase.RESTORING, Phase.DONE},
    Phase.PREFLIGHT: {Phase.ACQUIRING_INPUT, Phase.RESTORING, Phase.DONE},
    Phase.ACQUIRING_INPUT: {Phase.INPUT_OWNED, Phase.RESTORING, Phase.DONE},
    Phase.INPUT_OWNED: {Phase.STARTING_BACKEND, Phase.RESTORING},
    Phase.STARTING_BACKEND: {
        Phase.RUNNING,
        Phase.STOPPING,
        Phase.RESTORING,
    },
    Phase.RUNNING: {Phase.STOPPING, Phase.RESTORING},
    Phase.STOPPING: {Phase.RESTORING, Phase.DONE},
    Phase.RESTORING: {Phase.DONE},
    Phase.JOIN_VALIDATING: {Phase.JOIN_EXEC, Phase.DONE},
    Phase.JOIN_EXEC: {Phase.JOIN_RUNNING, Phase.DONE},
    Phase.JOIN_RUNNING: {Phase.JOIN_STOPPING, Phase.DONE},
    Phase.JOIN_STOPPING: {Phase.DONE},
    Phase.DONE: set(),
}


def env_float(name: str, default: float, minimum: float = 0.05) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise SupervisorError(f"{name} is not a number") from exc
    if parsed < minimum:
        raise SupervisorError(f"{name} must be at least {minimum}")
    return parsed


def strip_remainder(arguments: Sequence[str]) -> List[str]:
    result = list(arguments)
    if result and result[0] == "--":
        result.pop(0)
    return result


def proc_starttime(proc_root: Path, pid: int) -> str:
    try:
        line = (proc_root / str(pid) / "stat").read_text(
            encoding="utf-8", errors="strict"
        )
    except (OSError, UnicodeError) as exc:
        raise SupervisorError(f"cannot read process identity for PID {pid}") from exc
    closing = line.rfind(")")
    if closing < 0:
        raise SupervisorError(f"malformed process identity for PID {pid}")
    fields = line[closing + 1 :].split()
    # fields[0] is /proc stat field 3; starttime is field 22.
    if len(fields) <= 19 or not fields[19].isdigit():
        raise SupervisorError(f"malformed process start time for PID {pid}")
    return fields[19]


def proc_cgroup(proc_root: Path, pid: int) -> str:
    try:
        lines = (proc_root / str(pid) / "cgroup").read_text(
            encoding="utf-8", errors="strict"
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        raise SupervisorError(f"cannot read cgroup for PID {pid}") from exc
    unified = [line.split(":", 2)[2] for line in lines if line.startswith("0::")]
    if len(unified) != 1:
        raise SupervisorError(f"PID {pid} has no unique cgroup-v2 path")
    return validate_cgroup(unified[0])


def validate_cgroup(value: str) -> str:
    if (
        not value.startswith("/")
        or "\x00" in value
        or "\n" in value
        or any(part == ".." for part in value.split("/"))
    ):
        raise SupervisorError("unsafe cgroup path")
    normalized = value.rstrip("/")
    return normalized or "/"


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_in_cgroup(candidate: str, parent: str) -> bool:
    if parent == "/":
        return True
    return candidate == parent or candidate.startswith(parent + "/")


class SessionSupervisor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.phase = Phase.INIT
        self.signal_number: Optional[int] = None
        self.lock_fd: Optional[int] = None
        self.receipt: Optional[Tuple[str, str]] = None
        self.record_token = str(uuid.uuid4())
        self.record_written = False
        self.record_phase: Optional[str] = None
        self.backend_process: Optional[subprocess.Popen[bytes]] = None
        self.native_unit: Optional[str] = None
        self.native_scope_cgroup: Optional[str] = None
        self.flatpak_instance: Optional[str] = None
        self.flatpak_identity_verified = False
        self.flatpak_identity_uncertain = False
        self.backend_quiesced = False
        self.previous_power: Optional[str] = None
        self.performance_restore_required = False
        self.uncertain_process_groups: Set[int] = set()

        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
        default_lock = (
            str(Path(runtime_dir) / "pocketds-game-session.lock")
            if runtime_dir
            else ""
        )
        default_record_dir = (
            str(Path(runtime_dir) / "pocketds-game-session") if runtime_dir else ""
        )
        self.lock_path = Path(
            os.environ.get("POCKETDS_GAME_SESSION_LOCK", default_lock)
        )
        self.record_dir = Path(
            os.environ.get("POCKETDS_GAME_SESSION_DIR", default_record_dir)
        )
        if not str(self.lock_path) or not str(self.record_dir):
            raise SupervisorError("XDG_RUNTIME_DIR is required")
        self.record_path = self.record_dir / "session.json"
        self.input_helper = os.environ.get(
            "POCKETDS_INPUT_MODE", "/usr/local/libexec/pocketds-input-mode"
        )
        self.systemd_run = os.environ.get("POCKETDS_SYSTEMD_RUN", "systemd-run")
        self.systemctl = os.environ.get("POCKETDS_SYSTEMCTL", "systemctl")
        self.flatpak = os.environ.get("POCKETDS_FLATPAK", "flatpak")
        self.tuned_adm = os.environ.get("POCKETDS_TUNED_ADM", "tuned-adm")
        self.panelctl = os.environ.get(
            "POCKETDS_PANELCTL", "/usr/local/bin/pocketds-panelctl"
        )
        self.proc_root = Path(os.environ.get("POCKETDS_PROC_ROOT", "/proc"))
        self.cgroup_root = Path(
            os.environ.get("POCKETDS_CGROUP_ROOT", "/sys/fs/cgroup")
        )
        self.control_timeout = env_float("POCKETDS_CONTROL_TIMEOUT", 3.0)
        self.acquire_timeout = env_float(
            "POCKETDS_INPUT_ACQUIRE_TIMEOUT", 15.0
        )
        self.restore_timeout = env_float(
            "POCKETDS_INPUT_RESTORE_TIMEOUT", 15.0
        )
        self.start_timeout = env_float("POCKETDS_START_TIMEOUT", 5.0)
        self.term_timeout = env_float("POCKETDS_TERM_TIMEOUT", 5.0)

    def transition(self, new_phase: Phase) -> None:
        if new_phase not in ALLOWED_TRANSITIONS[self.phase]:
            raise SupervisorError(
                f"invalid state transition: {self.phase.value} -> {new_phase.value}"
            )
        self.phase = new_phase

    def install_signal_handlers(self) -> None:
        def remember(signum: int, _frame: object) -> None:
            if self.signal_number is None:
                self.signal_number = signum

        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, remember)

    def signal_status(self) -> int:
        return 128 + (self.signal_number or signal.SIGTERM)

    def control(
        self,
        command: Sequence[str],
        *,
        env: Optional[Dict[str, str]] = None,
        allow_failure: bool = False,
        timeout: Optional[float] = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            process: subprocess.Popen[str] = subprocess.Popen(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise SupervisorError(
                f"control command failed: {Path(command[0]).name}"
            ) from exc
        try:
            stdout, stderr = process.communicate(
                timeout=self.control_timeout if timeout is None else timeout
            )
        except subprocess.TimeoutExpired as exc:
            self.terminate_process_group(process)  # type: ignore[arg-type]
            raise SupervisorError(
                f"control command timed out: {Path(command[0]).name}"
            ) from exc
        except UnicodeError as exc:
            self.terminate_process_group(process)  # type: ignore[arg-type]
            raise SupervisorError(
                f"control command returned invalid text: {Path(command[0]).name}"
            ) from exc
        result = subprocess.CompletedProcess(
            list(command), process.returncode, stdout, stderr
        )
        process.poll()
        if self.process_group_exists(process):
            self.terminate_process_group(process)  # type: ignore[arg-type]
            raise SupervisorError(
                f"control command left a child process: {Path(command[0]).name}"
            )
        if result.returncode != 0 and not allow_failure:
            detail = result.stderr.strip()
            suffix = f": {detail}" if detail else ""
            raise SupervisorError(
                f"control command returned {result.returncode}: "
                f"{Path(command[0]).name}{suffix}"
            )
        return result

    def acquire_lock(self) -> None:
        parent = self.lock_path.parent
        try:
            parent_stat = parent.stat()
        except OSError as exc:
            raise SupervisorError("game-session lock directory is missing") from exc
        if not stat.S_ISDIR(parent_stat.st_mode) or parent.is_symlink():
            raise SupervisorError("game-session lock directory is unsafe")
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.lock_path, flags, 0o600)
            identity = os.fstat(fd)
        except OSError as exc:
            raise SupervisorError("cannot open game-session lock") from exc
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_nlink != 1
            or identity.st_uid != os.getuid()
        ):
            os.close(fd)
            raise SupervisorError("game-session lock is unsafe")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise BusyError("another managed game session is already running") from exc
        self.lock_fd = fd
        self.transition(Phase.LOCKED)

    def release_lock(self) -> None:
        if self.lock_fd is not None:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self.lock_fd)
                self.lock_fd = None

    def ensure_record_dir(self) -> None:
        parent = self.record_dir.parent
        if not parent.is_dir() or parent.is_symlink():
            raise SupervisorError("game-session record parent is unsafe")
        try:
            self.record_dir.mkdir(mode=0o700, exist_ok=True)
            identity = self.record_dir.stat()
        except OSError as exc:
            raise SupervisorError("cannot create game-session record directory") from exc
        if (
            self.record_dir.is_symlink()
            or not stat.S_ISDIR(identity.st_mode)
            or identity.st_uid != os.getuid()
            or identity.st_mode & 0o077
        ):
            raise SupervisorError("game-session record directory is unsafe")

    def base_record(self, phase: str) -> Dict[str, object]:
        owner_pid = os.getpid()
        record: Dict[str, object] = {
            "protocol": PROTOCOL,
            "token": self.record_token,
            "owner_pid": owner_pid,
            "owner_starttime": proc_starttime(self.proc_root, owner_pid),
            "owner_cgroup": proc_cgroup(self.proc_root, owner_pid),
            "backend": self.args.backend,
            "name": self.args.name,
            "phase": phase,
        }
        if self.native_unit is not None:
            record["unit"] = self.native_unit
        if self.native_scope_cgroup is not None:
            record["scope_cgroup"] = self.native_scope_cgroup
        if self.flatpak_instance is not None:
            record["flatpak_instance"] = self.flatpak_instance
        if self.receipt is not None:
            record["input_previous"] = self.receipt[0]
            record["input_token"] = self.receipt[1]
        if self.performance_restore_required and self.previous_power is not None:
            record["performance_restore_required"] = True
            record["performance_previous"] = self.previous_power
        return record

    def publish_record(self, record: Dict[str, object]) -> None:
        self.ensure_record_dir()
        payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        ) + b"\n"
        temporary_fd: Optional[int] = None
        temporary_name = ""
        try:
            temporary_fd, temporary_name = tempfile.mkstemp(
                prefix=".session.", dir=str(self.record_dir)
            )
            os.fchmod(temporary_fd, 0o600)
            os.write(temporary_fd, payload)
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = None
            os.replace(temporary_name, self.record_path)
            directory_fd = os.open(
                self.record_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            if temporary_fd is not None:
                os.close(temporary_fd)
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except OSError:
                    pass
            raise SupervisorError("cannot publish game-session record") from exc

    def write_record(self, phase: str) -> None:
        self.publish_record(self.base_record(phase))
        self.record_written = True
        self.record_phase = phase

    def checkpoint_record(self) -> None:
        if not self.record_written or self.record_phase is None:
            return
        self.write_record(self.record_phase)

    def remove_record(self) -> bool:
        if not self.record_written:
            return True
        try:
            record = read_record(self.record_path)
            if record.get("token") != self.record_token:
                return False
            self.record_path.unlink()
        except (OSError, SupervisorError):
            return False
        self.record_written = False
        self.record_phase = None
        return True

    def process_group_exists(self, process: subprocess.Popen[bytes]) -> bool:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            # PGIDs are numeric and reusable.  Once the original group is
            # observed absent, this supervisor must never send another real
            # signal to that number, even if a later probe sees it reused.
            self.uncertain_process_groups.add(process.pid)
            return False
        except PermissionError:
            self.uncertain_process_groups.add(process.pid)
            return True
        return True

    def wait_process_group_gone_after_eperm(
        self, process: subprocess.Popen[bytes]
    ) -> bool:
        """Resolve Darwin's signal-vs-zombie race without targeting a reused PGID.

        Once a real signal returned EPERM, the process-group identity is no
        longer safe to signal again.  We may only reap our Popen leader and
        observe that the exact numeric group has disappeared.  Any surviving,
        accessible, or still-unobservable group remains a cleanup failure.
        """

        deadline = time.monotonic() + min(self.term_timeout, 0.5)
        while True:
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                self.uncertain_process_groups.add(process.pid)
                return True
            except PermissionError:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def terminate_process_group(self, process: subprocess.Popen[bytes]) -> None:
        # Every caller created this process with start_new_session=True, making
        # process.pid the exact process-group ID.  This also catches helper
        # grandchildren left behind after their group leader has exited.
        uncertain_groups = self.uncertain_process_groups
        if process.pid in uncertain_groups:
            if self.wait_process_group_gone_after_eperm(process):
                return
            raise SupervisorError(
                "exact process group remains unverifiable after EPERM"
            )
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            uncertain_groups.add(process.pid)
            process.poll()
            return
        except PermissionError as exc:
            uncertain_groups.add(process.pid)
            if self.wait_process_group_gone_after_eperm(process):
                return
            raise SupervisorError(
                "exact process group became unverifiable during TERM"
            ) from exc
        deadline = time.monotonic() + min(self.term_timeout, 1.0)
        while time.monotonic() < deadline:
            process.poll()
            group_exists = self.process_group_exists(process)
            if not group_exists:
                return
            if process.pid in uncertain_groups:
                if self.wait_process_group_gone_after_eperm(process):
                    return
                raise SupervisorError(
                    "exact process group became unverifiable after TERM probe"
                )
            time.sleep(0.02)
        group_exists = self.process_group_exists(process)
        if not group_exists:
            return
        if process.pid in uncertain_groups:
            if self.wait_process_group_gone_after_eperm(process):
                return
            raise SupervisorError(
                "exact process group became unverifiable before KILL"
            )
        if group_exists:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                uncertain_groups.add(process.pid)
                process.poll()
                return
            except PermissionError as exc:
                uncertain_groups.add(process.pid)
                if self.wait_process_group_gone_after_eperm(process):
                    return
                raise SupervisorError(
                    "exact process group became unverifiable during KILL"
                ) from exc
        try:
            process.wait(timeout=min(self.term_timeout, 1.0))
        except subprocess.TimeoutExpired:
            pass

    def group_control(
        self, command: Sequence[str], *, allow_failure: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return self.control(command, allow_failure=allow_failure)

    def active_power_mode(self) -> str:
        active = self.group_control([self.tuned_adm, "active"])
        prefix = "Current active profile: "
        lines = [line.strip() for line in active.stdout.splitlines() if line.strip()]
        if len(lines) != 1 or not lines[0].startswith(prefix):
            raise SupervisorError(
                "performance state returned an unrecognized profile"
            )
        profile = lines[0][len(prefix) :]
        mapping = {
            "pocketds-powersave": "powersave",
            "pocketds-balanced": "balanced",
            "pocketds-performance": "performance",
        }
        mode = mapping.get(profile)
        if mode is None:
            raise SupervisorError("performance state is not Pocket DS managed")
        return mode

    def apply_performance(self) -> None:
        if not self.args.performance:
            return
        try:
            previous = self.active_power_mode()
        except SupervisorError as exc:
            print(
                f"pocketds-game-session: performance capture skipped: {exc}",
                file=sys.stderr,
            )
            return
        if previous == "performance":
            return
        # The panel command may mutate successfully and then hang or return a
        # failure.  Once an apply is attempted, exact restoration is required
        # regardless of the command's observed result.
        self.previous_power = previous
        self.performance_restore_required = True
        try:
            self.checkpoint_record()
        except SupervisorError as exc:
            self.performance_restore_required = False
            self.previous_power = None
            print(
                f"pocketds-game-session: performance journal failed: {exc}",
                file=sys.stderr,
            )
            return
        try:
            self.group_control([self.panelctl, "power", "performance"])
        except SupervisorError as exc:
            print(
                f"pocketds-game-session: performance apply failed: {exc}",
                file=sys.stderr,
            )
            return

    def clear_performance_journal(self) -> bool:
        self.performance_restore_required = False
        try:
            self.checkpoint_record()
        except SupervisorError as exc:
            # Repeating a restore or observing that the user superseded it is
            # safe; retain the marker rather than claiming an unjournaled end.
            self.performance_restore_required = True
            print(
                f"pocketds-game-session: performance journal cleanup failed: {exc}",
                file=sys.stderr,
            )
            return False
        return True

    def restore_performance(self) -> bool:
        if (
            not self.performance_restore_required
            or self.previous_power is None
        ):
            return True
        try:
            current = self.active_power_mode()
        except SupervisorError as exc:
            print(
                f"pocketds-game-session: performance restore state failed: {exc}",
                file=sys.stderr,
            )
            return False
        # This is a compare-and-set policy: only an unchanged managed
        # performance mode belongs to this session.  A different managed mode
        # is an explicit user/system supersession and must not be overwritten.
        if current != "performance":
            return self.clear_performance_journal()
        if self.previous_power == "performance":
            return self.clear_performance_journal()
        try:
            result = self.group_control(
                [self.panelctl, "power", self.previous_power], allow_failure=True
            )
        except SupervisorError as exc:
            print(
                f"pocketds-game-session: performance restore failed: {exc}",
                file=sys.stderr,
            )
            return False
        if result.returncode != 0:
            print(
                "pocketds-game-session: performance restore returned "
                f"{result.returncode}",
                file=sys.stderr,
            )
            return False
        return self.clear_performance_journal()

    def acquire_input(self) -> None:
        self.transition(Phase.ACQUIRING_INPUT)
        try:
            process = subprocess.Popen(
                [self.input_helper, "acquire", "gamepad"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=None,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise SupervisorError("cannot start the InputPlumber helper") from exc
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        buffer = bytearray()
        deadline = time.monotonic() + self.acquire_timeout
        termination_sent = False
        completed_cleanly = False
        try:
            while True:
                if self.signal_number is not None and not termination_sent:
                    termination_sent = True
                    self.terminate_process_group(process)
                    deadline = min(
                        deadline, time.monotonic() + self.term_timeout
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SupervisorError("InputPlumber acquisition timed out")
                events = selector.select(min(0.05, remaining))
                if events:
                    chunk = os.read(process.stdout.fileno(), RECEIPT_LIMIT + 1)
                    if chunk:
                        buffer.extend(chunk)
                        if len(buffer) > RECEIPT_LIMIT:
                            raise SupervisorError("InputPlumber receipt is too large")
                        if b"\n" in buffer and self.receipt is None:
                            line, remainder = bytes(buffer).split(b"\n", 1)
                            if remainder.strip():
                                raise SupervisorError(
                                    "InputPlumber helper emitted unexpected output"
                                )
                            self.receipt = parse_receipt(line)
                            # The receipt is durable before the helper can
                            # complete its first InputPlumber mutation.
                            self.write_record("acquiring-input")
                    elif process.poll() is not None:
                        break
                if process.poll() is not None:
                    tail = process.stdout.read()
                    if tail:
                        buffer.extend(tail)
                    break
            if len(buffer) > RECEIPT_LIMIT:
                raise SupervisorError("InputPlumber receipt is too large")
            if self.receipt is None:
                if b"\n" not in buffer:
                    raise SupervisorError("InputPlumber helper returned no receipt")
                line, remainder = bytes(buffer).split(b"\n", 1)
                if remainder.strip():
                    raise SupervisorError(
                        "InputPlumber helper emitted unexpected output"
                    )
                self.receipt = parse_receipt(line)
                self.write_record("acquiring-input")
            elif bytes(buffer).split(b"\n", 1)[1].strip():
                raise SupervisorError(
                    "InputPlumber helper emitted unexpected output"
                )
            if process.returncode != 0:
                raise SupervisorError(
                    f"InputPlumber acquisition returned {process.returncode}"
                )
            process.poll()
            if self.process_group_exists(process):
                raise SupervisorError(
                    "InputPlumber helper left an owned child process behind"
                )
            completed_cleanly = True
        finally:
            selector.close()
            if not completed_cleanly:
                self.terminate_process_group(process)
        self.transition(Phase.INPUT_OWNED)

    def recover_input(self, previous: str, token: str) -> bool:
        try:
            result = self.control(
                [
                    self.input_helper,
                    "recover-token",
                    token,
                    previous,
                ],
                allow_failure=True,
                timeout=self.restore_timeout,
            )
        except SupervisorError as exc:
            print(f"pocketds-game-session: input recovery failed: {exc}", file=sys.stderr)
            return False
        if result.returncode != 0:
            print(
                "pocketds-game-session: input recovery returned "
                f"{result.returncode}",
                file=sys.stderr,
            )
            return False
        valid_receipts = {
            f"pds-input-recover-v1 {outcome}\n"
            for outcome in ("restored", "already-target", "superseded")
        }
        if result.stdout not in valid_receipts:
            print(
                "pocketds-game-session: input recovery receipt is invalid",
                file=sys.stderr,
            )
            return False
        return True

    def restore_input(self) -> bool:
        if self.receipt is None:
            return True
        return self.recover_input(self.receipt[0], self.receipt[1])

    def preflight(self) -> None:
        self.transition(Phase.PREFLIGHT)
        self.recover_stale_record()
        if self.args.backend == "flatpak":
            rows = self.flatpak_ps()
            matching = [row for row in rows if row[1] == WILIWILI_APP_ID]
            if matching:
                raise SupervisorError("Wiliwili is already running")

    def validate_existing_record(self, record: Dict[str, object]) -> None:
        token = record.get("token")
        owner_pid = record.get("owner_pid")
        owner_starttime = record.get("owner_starttime")
        owner_cgroup = record.get("owner_cgroup")
        backend = record.get("backend")
        name = record.get("name")
        phase = record.get("phase")
        input_previous = record.get("input_previous")
        input_token = record.get("input_token")
        performance_required = record.get(
            "performance_restore_required", False
        )
        performance_previous = record.get("performance_previous")
        if (
            not isinstance(token, str)
            or not TOKEN_RE.fullmatch(token)
            or not isinstance(owner_pid, int)
            or owner_pid <= 0
            or not isinstance(owner_starttime, str)
            or not owner_starttime.isdigit()
            or not isinstance(owner_cgroup, str)
            or phase
            not in ("acquiring-input", "starting", "running", "restoring")
            or input_previous not in VALID_INPUT_MODES
            or not isinstance(input_token, str)
            or not TOKEN_RE.fullmatch(input_token)
            or not isinstance(performance_required, bool)
        ):
            raise SupervisorError("existing game-session record is invalid")
        if performance_required:
            if performance_previous not in (
                "powersave",
                "balanced",
                "performance",
            ):
                raise SupervisorError(
                    "existing performance recovery marker is invalid"
                )
        elif performance_previous is not None:
            raise SupervisorError("orphaned performance recovery value is invalid")
        validate_cgroup(owner_cgroup)
        if backend == "native":
            if name not in VALID_NATIVE_NAMES:
                raise SupervisorError("existing native session name is invalid")
            expected_unit = f"pocketds-{name}-{token.replace('-', '')}.scope"
            unit = record.get("unit")
            if unit is not None and unit != expected_unit:
                raise SupervisorError("existing native unit identity is invalid")
            if phase != "acquiring-input" and unit != expected_unit:
                raise SupervisorError("existing native unit identity is missing")
            scope_cgroup = record.get("scope_cgroup")
            if scope_cgroup is not None and not isinstance(scope_cgroup, str):
                raise SupervisorError("existing native cgroup identity is invalid")
            if isinstance(scope_cgroup, str):
                validate_cgroup(scope_cgroup)
            if unit is None and scope_cgroup is not None:
                raise SupervisorError("existing native cgroup lacks a unit identity")
        elif backend == "flatpak":
            if name != "wiliwili":
                raise SupervisorError("existing Flatpak session name is invalid")
            instance = record.get("flatpak_instance")
            if instance is not None and (
                not isinstance(instance, str)
                or not POSITIVE_INTEGER_RE.fullmatch(instance)
            ):
                raise SupervisorError("existing Flatpak instance identity is invalid")
            if phase == "running" and instance is None:
                raise SupervisorError("running Flatpak instance identity is missing")
        else:
            raise SupervisorError("existing game-session backend is invalid")

    def existing_owner_status(self, record: Dict[str, object]) -> str:
        owner_pid = int(record["owner_pid"])
        if not process_exists(owner_pid):
            return "dead"
        try:
            if (
                proc_starttime(self.proc_root, owner_pid)
                == record["owner_starttime"]
                and proc_cgroup(self.proc_root, owner_pid)
                == record["owner_cgroup"]
            ):
                return "live"
        except SupervisorError:
            return "unknown"
        # The numeric PID exists but no longer has the recorded birth identity.
        return "dead"

    def record_native_boundary_absent(self, record: Dict[str, object]) -> bool:
        if record.get("unit") is None:
            return True
        scope_cgroup = record.get("scope_cgroup")
        if isinstance(scope_cgroup, str):
            try:
                if self.cgroup_populated(scope_cgroup):
                    return False
            except SupervisorError:
                return False
        try:
            return self.native_unit_absent(str(record["unit"]))
        except SupervisorError:
            return False

    def record_flatpak_boundary_absent(self, record: Dict[str, object]) -> bool:
        try:
            rows = self.flatpak_ps()
        except SupervisorError:
            return False
        instance = record.get("flatpak_instance")
        if instance is None:
            return not any(row[1] == WILIWILI_APP_ID for row in rows)
        same_instance = [row for row in rows if row[0] == instance]
        return not same_instance

    def recover_stale_record(self) -> None:
        if not os.path.lexists(self.record_path):
            return
        record = read_record(self.record_path)
        self.validate_existing_record(record)
        owner_status = self.existing_owner_status(record)
        if owner_status == "live":
            raise SupervisorError("existing game-session owner is still live")
        if owner_status != "dead":
            raise SupervisorError("existing game-session owner cannot be verified")
        if record["backend"] == "native":
            absent = self.record_native_boundary_absent(record)
        else:
            absent = self.record_flatpak_boundary_absent(record)
        if not absent:
            raise SupervisorError(
                "stale game-session backend boundary is still present or unknown"
            )
        if record.get("performance_restore_required") is True:
            previous_power = str(record["performance_previous"])
            try:
                current_power = self.active_power_mode()
            except SupervisorError as exc:
                raise SupervisorError(
                    f"stale performance state recovery failed: {exc}"
                ) from exc
            if current_power == "performance" and previous_power != "performance":
                try:
                    power_result = self.control(
                        [self.panelctl, "power", previous_power],
                        allow_failure=True,
                    )
                except SupervisorError as exc:
                    raise SupervisorError(
                        f"stale performance recovery failed: {exc}"
                    ) from exc
                if power_result.returncode != 0:
                    raise SupervisorError(
                        "stale performance recovery returned a failure"
                    )
            # CAS succeeded, was already restored, or another managed mode
            # superseded this session.  Journal that conclusion before the
            # independently retryable input recovery.
            current_record = read_record(self.record_path)
            if current_record.get("token") != record.get("token"):
                raise SupervisorError(
                    "game-session record changed during performance recovery"
                )
            current_record.pop("performance_restore_required", None)
            current_record.pop("performance_previous", None)
            self.publish_record(current_record)
            record = current_record
        if not self.recover_input(
            str(record["input_previous"]), str(record["input_token"])
        ):
            raise SupervisorError("stale input recovery was not completed")
        # Re-open immediately before unlinking so a changed token can never be
        # removed based on an earlier stale-record decision.
        current = read_record(self.record_path)
        if current.get("token") != record.get("token"):
            raise SupervisorError("game-session record changed during recovery")
        try:
            self.record_path.unlink()
        except OSError as exc:
            raise SupervisorError("cannot remove proven-stale session record") from exc

    def flatpak_ps(self) -> List[Tuple[str, str, int]]:
        result = self.control(
            [self.flatpak, "ps", "--columns=instance,application,pid"]
        )
        rows: List[Tuple[str, str, int]] = []
        for index, raw_line in enumerate(result.stdout.splitlines()):
            line = raw_line.strip()
            if not line:
                continue
            fields = line.split()
            lowered = [field.lower() for field in fields]
            if index == 0 and lowered == ["instance", "application", "pid"]:
                continue
            if len(fields) != 3:
                raise SupervisorError("Flatpak process listing is malformed")
            instance, application, pid_text = fields
            if not POSITIVE_INTEGER_RE.fullmatch(instance) or not pid_text.isdigit():
                raise SupervisorError("Flatpak process listing lacks numeric identity")
            rows.append((instance, application, int(pid_text)))
        return rows

    def native_control_group(self, unit: str) -> str:
        result = self.control(
            [
                self.systemctl,
                "--user",
                "show",
                "--property=ControlGroup",
                "--value",
                unit,
            ]
        )
        value = result.stdout.strip()
        if not value or "\n" in value:
            raise SupervisorError("native scope has no unique control group")
        return validate_cgroup(value)

    def native_unit_snapshot(self, unit: str) -> Tuple[str, str, str]:
        result = self.control(
            [
                self.systemctl,
                "--user",
                "show",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=ControlGroup",
                unit,
            ],
            allow_failure=True,
        )
        fields: Dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in ("LoadState", "ActiveState", "ControlGroup"):
                fields[key] = value
        if set(fields) != {"LoadState", "ActiveState", "ControlGroup"}:
            raise SupervisorError("native unit state is not observable")
        control_group = fields["ControlGroup"]
        if control_group:
            control_group = validate_cgroup(control_group)
        return fields["LoadState"], fields["ActiveState"], control_group

    def native_unit_absent(self, unit: str) -> bool:
        load_state, active_state, control_group = self.native_unit_snapshot(unit)
        if control_group:
            if unit == self.native_unit:
                self.native_scope_cgroup = control_group
            return False
        return load_state in ("not-found", "loaded") and active_state in (
            "inactive",
            "failed",
        )

    def cgroup_populated(self, cgroup: str) -> bool:
        relative = validate_cgroup(cgroup).lstrip("/")
        events_path = self.cgroup_root / relative / "cgroup.events"
        try:
            payload = events_path.read_text(
                encoding="ascii", errors="strict"
            ).splitlines()
        except FileNotFoundError:
            return False
        except (OSError, UnicodeError) as exc:
            raise SupervisorError("cannot verify native scope population") from exc
        populated = [
            line.split()[1]
            for line in payload
            if len(line.split()) == 2 and line.split()[0] == "populated"
        ]
        if populated not in (["0"], ["1"]):
            raise SupervisorError("native scope population state is malformed")
        return populated[0] == "1"

    def native_scope_populated(self) -> bool:
        if self.native_scope_cgroup is None:
            if self.native_unit is not None and self.native_unit_absent(
                self.native_unit
            ):
                return False
            if self.native_scope_cgroup is None:
                raise SupervisorError("native scope cgroup identity is unavailable")
        return self.cgroup_populated(self.native_scope_cgroup)

    def best_effort_control(self, command: Sequence[str]) -> None:
        try:
            result = self.control(command, allow_failure=True)
            if result.returncode != 0:
                print(
                    "pocketds-game-session: cleanup control returned "
                    f"{result.returncode}: {Path(command[0]).name}",
                    file=sys.stderr,
                )
        except SupervisorError as exc:
            print(f"pocketds-game-session: cleanup control failed: {exc}", file=sys.stderr)

    def wait_native_quiescent(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            process_done = (
                self.backend_process is None
                or self.backend_process.poll() is not None
            )
            client_group_empty = (
                self.backend_process is None
                or not self.process_group_exists(self.backend_process)
            )
            try:
                scope_empty = not self.native_scope_populated()
            except SupervisorError:
                scope_empty = False
            if process_done and client_group_empty and scope_empty:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def wait_native_scope_empty(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if not self.native_scope_populated():
                    return True
            except SupervisorError:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def stop_native(self) -> None:
        process = self.backend_process
        unit = self.native_unit
        if unit is None:
            return
        if self.wait_native_quiescent(0.05):
            self.backend_quiesced = True
            return
        self.best_effort_control([self.systemctl, "--user", "stop", unit])
        if self.wait_native_quiescent(self.term_timeout):
            self.backend_quiesced = True
            return
        if self.wait_native_scope_empty(0.05):
            if process is not None and self.process_group_exists(process):
                self.terminate_process_group(process)
            self.backend_quiesced = True
            return
        self.best_effort_control(
            [
                self.systemctl,
                "--user",
                "kill",
                "--kill-whom=all",
                "--signal=KILL",
                unit,
            ]
        )
        if not self.wait_native_scope_empty(self.term_timeout):
            raise SupervisorError(
                "native scope remained populated after exact stop and kill"
            )
        if process is not None and self.process_group_exists(process):
            # The cgroup is confirmed empty; reap only our exact systemd-run
            # client if it failed to observe unit teardown.
            self.terminate_process_group(process)
        self.backend_quiesced = True

    def run_native(self, command: Sequence[str]) -> int:
        unit_token = self.record_token.replace("-", "")
        unit = f"pocketds-{self.args.name}-{unit_token}.scope"
        self.native_unit = unit
        self.write_record("starting")
        if self.signal_number is not None:
            return self.signal_status()
        environment = os.environ.copy()
        environment["POCKETDS_GAME_SESSION_TOKEN"] = self.record_token
        invocation = [
            self.systemd_run,
            "--user",
            "--scope",
            f"--unit={unit}",
            "--slice=app.slice",
            "--property=KillMode=control-group",
            "--property=SendSIGKILL=yes",
            "--property=TimeoutStopSec=5s",
            "--expand-environment=no",
            "--collect",
            "--quiet",
            "--",
            *command,
        ]
        try:
            self.backend_process = subprocess.Popen(
                invocation,
                stdin=None,
                stdout=None,
                stderr=None,
                env=environment,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise SupervisorError("cannot start native user scope") from exc

        deadline = time.monotonic() + self.start_timeout
        while self.native_scope_cgroup is None:
            if self.signal_number is not None:
                self.transition(Phase.STOPPING)
                self.stop_native()
                return self.signal_status()
            if self.backend_process.poll() is not None:
                try:
                    if self.native_unit_absent(unit):
                        if self.process_group_exists(self.backend_process):
                            self.terminate_process_group(self.backend_process)
                        self.backend_quiesced = True
                        return normalize_returncode(
                            self.backend_process.returncode
                        )
                except SupervisorError:
                    pass
                self.transition(Phase.STOPPING)
                self.stop_native()
                return normalize_returncode(self.backend_process.returncode)
            try:
                self.native_scope_cgroup = self.native_control_group(unit)
            except SupervisorError:
                if time.monotonic() >= deadline:
                    self.transition(Phase.STOPPING)
                    self.stop_native()
                    raise SupervisorError("native scope did not become observable")
                time.sleep(0.05)

        self.write_record("running")
        self.transition(Phase.RUNNING)
        while True:
            if self.signal_number is not None:
                self.transition(Phase.STOPPING)
                self.stop_native()
                return self.signal_status()
            try:
                status = normalize_returncode(
                    self.backend_process.wait(timeout=0.1)
                )
            except subprocess.TimeoutExpired:
                continue
            if not self.wait_native_quiescent(self.term_timeout):
                self.transition(Phase.STOPPING)
                self.stop_native()
            else:
                self.backend_quiesced = True
            return status

    def receive_flatpak_instance(self, read_fd: int) -> str:
        selector = selectors.DefaultSelector()
        selector.register(read_fd, selectors.EVENT_READ)
        buffer = bytearray()
        deadline = time.monotonic() + self.start_timeout
        termination_sent = False
        try:
            while True:
                if self.signal_number is not None and not termination_sent:
                    termination_sent = True
                    self.reap_backend_client()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SupervisorError("Flatpak instance receipt timed out")
                events = selector.select(min(0.05, remaining))
                if not events:
                    continue
                chunk = os.read(
                    read_fd, FLATPAK_INSTANCE_LIMIT + 1 - len(buffer)
                )
                if chunk:
                    buffer.extend(chunk)
                    if len(buffer) > FLATPAK_INSTANCE_LIMIT:
                        raise SupervisorError("Flatpak instance receipt is too large")
                    if b"\n" not in buffer:
                        continue
                    line, remainder = bytes(buffer).split(b"\n", 1)
                    if remainder:
                        raise SupervisorError(
                            "Flatpak instance receipt has extra output"
                        )
                    payload = line
                    break
                payload = bytes(buffer)
                break
        finally:
            selector.close()
        try:
            instance = payload.decode("ascii", errors="strict")
        except UnicodeError as exc:
            raise SupervisorError("Flatpak instance receipt is not ASCII") from exc
        if not POSITIVE_INTEGER_RE.fullmatch(instance):
            raise SupervisorError("Flatpak instance receipt is invalid")
        return instance

    def bind_flatpak(self) -> None:
        if self.flatpak_instance is None:
            raise SupervisorError("Flatpak instance receipt is unavailable")
        deadline = time.monotonic() + self.start_timeout
        while True:
            rows = self.flatpak_ps()
            same_instance = [
                row for row in rows if row[0] == self.flatpak_instance
            ]
            if len(same_instance) > 1:
                raise SupervisorError("Flatpak instance listing is ambiguous")
            if len(same_instance) == 1:
                _instance, application, _pid = same_instance[0]
                if application != WILIWILI_APP_ID:
                    raise SupervisorError(
                        "Flatpak instance belongs to a different application"
                    )
                # Flatpak 1.18 may report pid=0.  The instance-id-fd receipt,
                # cross-checked against the exact application row, is the
                # authoritative identity; the client Popen PID is irrelevant.
                self.flatpak_identity_verified = True
                return
            if time.monotonic() >= deadline:
                raise SupervisorError("Wiliwili Flatpak instance did not appear")
            if self.backend_process is not None:
                self.backend_process.poll()
            time.sleep(0.05)

    def flatpak_binding_state(self) -> str:
        if self.flatpak_instance is None:
            return "unbound"
        try:
            rows = self.flatpak_ps()
        except SupervisorError:
            self.flatpak_identity_uncertain = True
            raise
        same_instance = [row for row in rows if row[0] == self.flatpak_instance]
        if not same_instance:
            return "absent"
        if (
            len(same_instance) == 1
            and same_instance[0][1] == WILIWILI_APP_ID
        ):
            return "current"
        self.flatpak_identity_uncertain = True
        return "ambiguous"

    def wait_flatpak_absent(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            try:
                state = self.flatpak_binding_state()
                if state == "absent":
                    return True
            except SupervisorError:
                self.flatpak_identity_uncertain = True
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def reap_backend_client(self) -> None:
        process = self.backend_process
        if process is None:
            return
        process.poll()
        if self.process_group_exists(process):
            self.terminate_process_group(process)

    def stop_flatpak(self) -> None:
        process = self.backend_process
        if process is None:
            return
        if self.flatpak_instance is None:
            # No authoritative Flatpak instance was ever bound.  Never guess
            # an app-id target; reap only the exact client process we created.
            self.reap_backend_client()
            self.backend_quiesced = True
            return
        try:
            state = self.flatpak_binding_state()
        except SupervisorError:
            self.flatpak_identity_uncertain = True
            state = "unknown"
        if state == "absent":
            self.reap_backend_client()
            self.backend_quiesced = True
            return
        # The fd receipt is the only target used for termination.  Even if ps
        # is temporarily ambiguous, this never widens to an application ID.
        self.best_effort_control([self.flatpak, "kill", self.flatpak_instance])
        self.reap_backend_client()
        if not self.wait_flatpak_absent(self.term_timeout):
            raise SupervisorError(
                "bound Flatpak instance could not be confirmed stopped"
            )
        self.backend_quiesced = True

    def run_flatpak(self, app_arguments: Sequence[str]) -> int:
        self.write_record("starting")
        if self.signal_number is not None:
            return self.signal_status()
        environment = os.environ.copy()
        environment["POCKETDS_GAME_SESSION_TOKEN"] = self.record_token
        read_fd, write_fd = os.pipe()
        invocation = [
            self.flatpak,
            "run",
            f"--branch={WILIWILI_BRANCH}",
            "--arch=aarch64",
            "--command=wiliwili",
            "--die-with-parent",
            f"--instance-id-fd={write_fd}",
            WILIWILI_APP_ID,
            *app_arguments,
        ]
        try:
            self.backend_process = subprocess.Popen(
                invocation,
                stdin=None,
                stdout=None,
                stderr=None,
                env=environment,
                start_new_session=True,
                close_fds=True,
                pass_fds=(write_fd,),
            )
        except OSError as exc:
            os.close(read_fd)
            raise SupervisorError("cannot start Wiliwili Flatpak") from exc
        finally:
            os.close(write_fd)
        try:
            self.flatpak_instance = self.receive_flatpak_instance(read_fd)
            # Persist the authoritative numeric receipt before relying on ps
            # or doing any further launch work.
            self.write_record("starting")
            self.bind_flatpak()
        except SupervisorError:
            self.stop_flatpak()
            if self.signal_number is not None:
                return self.signal_status()
            raise
        finally:
            os.close(read_fd)
        self.write_record("running")
        self.transition(Phase.RUNNING)
        while True:
            if self.signal_number is not None:
                self.transition(Phase.STOPPING)
                self.stop_flatpak()
                return self.signal_status()
            try:
                status = normalize_returncode(
                    self.backend_process.wait(timeout=0.1)
                )
            except subprocess.TimeoutExpired:
                continue
            self.transition(Phase.STOPPING)
            self.stop_flatpak()
            return status

    def run(self) -> int:
        self.install_signal_handlers()
        app_status = 1
        restore_ok = True
        try:
            self.acquire_lock()
            self.preflight()
            if self.signal_number is not None:
                return self.signal_status()
            try:
                self.acquire_input()
            except SupervisorError:
                if self.signal_number is not None:
                    return self.signal_status()
                raise
            if self.signal_number is not None:
                return self.signal_status()
            self.apply_performance()
            if self.signal_number is not None:
                return self.signal_status()
            self.transition(Phase.STARTING_BACKEND)
            command = strip_remainder(self.args.command)
            if self.args.backend == "native":
                app_status = self.run_native(command)
            else:
                app_status = self.run_flatpak(command)
            return app_status
        finally:
            cleanup_ok = True
            performance_ok = True
            input_ok = True
            if (
                self.backend_process is not None
                and self.phase in (Phase.STARTING_BACKEND, Phase.RUNNING)
            ):
                self.transition(Phase.STOPPING)
            backend_safe = self.backend_process is None or self.backend_quiesced
            if self.backend_process is not None and not self.backend_quiesced:
                if self.phase not in (Phase.STOPPING, Phase.RESTORING):
                    self.transition(Phase.STOPPING)
                try:
                    if self.args.backend == "native":
                        self.stop_native()
                    else:
                        self.stop_flatpak()
                except SupervisorError as exc:
                    cleanup_ok = False
                    print(
                        f"pocketds-game-session: backend cleanup failed: {exc}",
                        file=sys.stderr,
                    )
                backend_safe = self.backend_quiesced
            if (
                self.args.backend == "flatpak"
                and self.backend_process is not None
                and (
                    not self.flatpak_identity_verified
                    or self.flatpak_identity_uncertain
                )
            ):
                # An exact receipt may let us terminate numerically, but a
                # never-verified or later-ambiguous ps identity cannot authorize
                # immediate restoration.  Defer to stale-record recovery.
                backend_safe = False
            if backend_safe:
                performance_ok = self.restore_performance()
                if not performance_ok:
                    cleanup_ok = False
            elif self.performance_restore_required:
                cleanup_ok = False
                print(
                    "pocketds-game-session: CRITICAL: performance was not "
                    "restored while the backend may still be active",
                    file=sys.stderr,
                )
            if self.receipt is not None and backend_safe:
                if self.phase != Phase.RESTORING:
                    self.transition(Phase.RESTORING)
                if self.record_written:
                    try:
                        journal_phase = "restoring"
                        if (
                            self.args.backend == "native"
                            and self.native_unit is None
                        ):
                            # No backend boundary was ever allocated; preserve
                            # the only schema phase allowed to omit a unit.
                            journal_phase = "acquiring-input"
                        self.write_record(journal_phase)
                    except SupervisorError as exc:
                        cleanup_ok = False
                        print(
                            "pocketds-game-session: restore journal failed: "
                            f"{exc}",
                            file=sys.stderr,
                        )
                input_ok = self.restore_input()
                restore_ok = input_ok
                if not input_ok:
                    cleanup_ok = False
            elif self.receipt is not None:
                input_ok = False
                cleanup_ok = False
                print(
                    "pocketds-game-session: CRITICAL: input was not restored "
                    "because the backend could not be confirmed stopped",
                    file=sys.stderr,
                )
            if backend_safe and performance_ok and input_ok:
                if not self.remove_record():
                    cleanup_ok = False
                    print(
                        "pocketds-game-session: completed journal could not be removed",
                        file=sys.stderr,
                    )
            self.release_lock()
            if self.phase != Phase.DONE:
                self.transition(Phase.DONE)
            if (not restore_ok or not cleanup_ok) and self.signal_number is None:
                # A finally block cannot replace a pending return directly.
                # Store the override for main() to inspect.
                self.args.cleanup_failed = True

    def join(self) -> int:
        self.install_signal_handlers()
        self.transition(Phase.JOIN_VALIDATING)
        process: Optional[subprocess.Popen[bytes]] = None
        status = 1
        try:
            token = self.args.token
            if not TOKEN_RE.fullmatch(token):
                raise SupervisorError("join token is malformed")
            deadline = time.monotonic() + self.start_timeout
            record: Dict[str, object]
            while True:
                if self.signal_number is not None:
                    return self.signal_status()
                record = read_record(self.record_path)
                if record.get("token") != token:
                    raise SupervisorError(
                        "join token does not match the live session"
                    )
                if record.get("phase") == "running":
                    break
                if (
                    record.get("phase") != "starting"
                    or time.monotonic() >= deadline
                ):
                    raise SupervisorError("game session is not joinable")
                time.sleep(0.05)
            validate_join_record(record, self.proc_root)
            caller_cgroup = proc_cgroup(self.proc_root, os.getpid())
            scope_cgroup = str(record["scope_cgroup"])
            if not is_in_cgroup(caller_cgroup, scope_cgroup):
                raise SupervisorError("join caller is outside the ES-DE scope")
            command = strip_remainder(self.args.command)
            self.transition(Phase.JOIN_EXEC)
            environment = os.environ.copy()
            environment["POCKETDS_GAME_SESSION_JOINED"] = "1"
            try:
                process = subprocess.Popen(
                    command,
                    stdin=None,
                    stdout=None,
                    stderr=None,
                    env=environment,
                    start_new_session=True,
                    close_fds=True,
                )
            except OSError as exc:
                raise SupervisorError(
                    "cannot execute joined RetroArch command"
                ) from exc
            self.transition(Phase.JOIN_RUNNING)
            while True:
                if self.signal_number is not None:
                    self.transition(Phase.JOIN_STOPPING)
                    self.terminate_process_group(process)
                    status = self.signal_status()
                    return status
                try:
                    status = normalize_returncode(process.wait(timeout=0.1))
                except subprocess.TimeoutExpired:
                    continue
                if self.process_group_exists(process):
                    self.transition(Phase.JOIN_STOPPING)
                    self.terminate_process_group(process)
                return status
        finally:
            if process is not None and self.process_group_exists(process):
                if self.phase == Phase.JOIN_RUNNING:
                    self.transition(Phase.JOIN_STOPPING)
                self.terminate_process_group(process)
            if self.phase != Phase.DONE:
                self.transition(Phase.DONE)


def request_session_stop(supervisor: SessionSupervisor) -> int:
    """Ask the exact live session owner to perform its normal cleanup path."""

    if not os.path.lexists(supervisor.record_path):
        return 0
    record = read_record(supervisor.record_path)
    supervisor.validate_existing_record(record)
    if supervisor.existing_owner_status(record) != "live":
        raise SupervisorError("game-session owner is not live")

    # Re-open and revalidate immediately before signaling.  The secure record,
    # UUID token, process birth time and cgroup together prevent a reused PID or
    # replaced journal from becoming a broad process-kill primitive.
    current = read_record(supervisor.record_path)
    if current.get("token") != record.get("token"):
        raise SupervisorError("game-session record changed before stop")
    supervisor.validate_existing_record(current)
    if supervisor.existing_owner_status(current) != "live":
        raise SupervisorError("game-session owner changed before stop")
    owner_pid = int(current["owner_pid"])
    if owner_pid == os.getpid():
        raise SupervisorError("game-session owner cannot signal itself")
    try:
        os.kill(owner_pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as exc:
        raise SupervisorError("cannot signal game-session owner") from exc
    return 0


def probe_native_session(supervisor: SessionSupervisor, name: str) -> int:
    """Return 0 only for the exact live, running native session requested.

    Exit 3 means no live owner is present and a normal ``run`` may perform its
    existing stale-record recovery.  Exit 75 means a live session exists but
    must not be bypassed by a second launcher.
    """

    if not os.path.lexists(supervisor.record_path):
        return 3
    record = read_record(supervisor.record_path)
    supervisor.validate_existing_record(record)
    owner_status = supervisor.existing_owner_status(record)
    if owner_status == "dead":
        return 3
    if owner_status != "live":
        raise SupervisorError("game-session owner cannot be verified")
    if (
        record.get("backend") != "native"
        or record.get("name") != name
        or record.get("phase") != "running"
    ):
        return 75
    scope_cgroup = record.get("scope_cgroup")
    if not isinstance(scope_cgroup, str):
        return 75
    if not supervisor.cgroup_populated(scope_cgroup):
        return 75
    return 0


def parse_receipt(line: bytes) -> Tuple[str, str]:
    try:
        text = line.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise SupervisorError("InputPlumber receipt is not ASCII") from exc
    fields = text.split()
    if len(fields) != 3 or fields[0] != INPUT_PROTOCOL:
        raise SupervisorError("InputPlumber receipt protocol is invalid")
    previous, token = fields[1], fields[2]
    if previous not in VALID_INPUT_MODES or not TOKEN_RE.fullmatch(token):
        raise SupervisorError("InputPlumber receipt identity is invalid")
    return previous, token


def normalize_returncode(returncode: int) -> int:
    if returncode < 0:
        return 128 + (-returncode)
    return min(returncode, 255)


def read_record(path: Path) -> Dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        fd = os.open(path, flags)
        identity = os.fstat(fd)
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_nlink != 1
            or identity.st_uid != os.getuid()
            or identity.st_mode & 0o077
            or identity.st_size <= 0
            or identity.st_size > RECORD_LIMIT
        ):
            raise SupervisorError("game-session record is unsafe")
        payload = os.read(fd, RECORD_LIMIT + 1)
    except OSError as exc:
        raise SupervisorError("cannot read game-session record") from exc
    finally:
        if "fd" in locals():
            os.close(fd)
    try:
        record = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SupervisorError("game-session record is malformed") from exc
    if not isinstance(record, dict) or record.get("protocol") != PROTOCOL:
        raise SupervisorError("game-session record protocol is invalid")
    return record


def validate_join_record(record: Dict[str, object], proc_root: Path) -> None:
    required = {
        "token",
        "owner_pid",
        "owner_starttime",
        "owner_cgroup",
        "backend",
        "name",
        "phase",
        "scope_cgroup",
        "unit",
    }
    if not required.issubset(record):
        raise SupervisorError("game-session record lacks join identity")
    token = record["token"]
    owner_pid = record["owner_pid"]
    owner_starttime = record["owner_starttime"]
    owner_cgroup = record["owner_cgroup"]
    if (
        not isinstance(token, str)
        or not TOKEN_RE.fullmatch(token)
        or not isinstance(owner_pid, int)
        or owner_pid <= 0
        or not isinstance(owner_starttime, str)
        or not owner_starttime.isdigit()
        or not isinstance(owner_cgroup, str)
        or record["backend"] != "native"
        or record["name"] != "es-de"
        or record["phase"] != "running"
        or not isinstance(record["scope_cgroup"], str)
        or record["unit"]
        != f"pocketds-es-de-{token.replace('-', '')}.scope"
    ):
        raise SupervisorError("game-session join identity is invalid")
    if not process_exists(owner_pid):
        raise SupervisorError("game-session owner no longer exists")
    if proc_starttime(proc_root, owner_pid) != owner_starttime:
        raise SupervisorError("game-session owner PID was reused")
    if proc_cgroup(proc_root, owner_pid) != validate_cgroup(owner_cgroup):
        raise SupervisorError("game-session owner changed cgroup")
    validate_cgroup(str(record["scope_cgroup"]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    run_parser = subparsers.add_parser("run", help="own a complete game session")
    run_parser.add_argument("--backend", choices=("native", "flatpak"), required=True)
    run_parser.add_argument("--name", required=True)
    run_parser.add_argument("--app-id")
    run_parser.add_argument("--performance", action="store_true")
    run_parser.add_argument("command", nargs=argparse.REMAINDER)

    join_parser = subparsers.add_parser(
        "join", help="join an owning ES-DE session without a second lease"
    )
    join_parser.add_argument("--token", required=True)
    join_parser.add_argument("--performance", action="store_true")
    join_parser.add_argument("command", nargs=argparse.REMAINDER)
    probe_parser = subparsers.add_parser(
        "probe", help="check one exact live native session without changing it"
    )
    probe_parser.add_argument("--name", choices=sorted(VALID_NATIVE_NAMES), required=True)
    subparsers.add_parser("stop", help="stop the exact live managed game session")
    return parser


def validate_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.action in {"stop", "probe"}:
        return
    command = strip_remainder(args.command)
    if args.action == "join":
        if args.performance:
            parser.error("join does not accept --performance")
        if not command:
            parser.error("a command is required after --")
        return
    if not SAFE_NAME_RE.fullmatch(args.name):
        parser.error("--name is unsafe")
    if args.backend == "native":
        if not command:
            parser.error("a native command is required after --")
        if args.name not in VALID_NATIVE_NAMES:
            parser.error("native --name must be es-de, retroarch, melonds, or steam")
        if args.app_id is not None:
            parser.error("native sessions do not accept --app-id")
    else:
        if args.name != "wiliwili" or args.app_id != WILIWILI_APP_ID:
            parser.error(
                "flatpak sessions require --name wiliwili and the exact Wiliwili app ID"
            )
        if args.performance:
            parser.error("--performance is only valid for native RetroArch")
    if (
        args.backend == "native"
        and args.performance
        and args.name not in {"retroarch", "melonds"}
    ):
        parser.error("--performance is only valid for native emulators")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_arguments(args, parser)
    args.cleanup_failed = False
    try:
        supervisor = SessionSupervisor(args)
        if args.action == "stop":
            status = request_session_stop(supervisor)
        elif args.action == "probe":
            status = probe_native_session(supervisor, args.name)
        elif args.action == "join":
            status = supervisor.join()
        else:
            status = supervisor.run()
        if args.cleanup_failed and status == 0:
            return 1
        return status
    except BusyError as exc:
        print(f"pocketds-game-session: {exc}", file=sys.stderr)
        return 75
    except SupervisorError as exc:
        print(f"pocketds-game-session: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
