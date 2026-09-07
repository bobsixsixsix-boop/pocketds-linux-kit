#!/usr/bin/env python3
"""Run a confirmation-gated nested KWin/Xwayland failure acceptance."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Sequence


SCHEMA = "pocketds.xwayland-isolation.v1"
READY_SCHEMA = "pocketds.x11-probe-ready.v1"
CONFIRMATION = "POCKETDS-RUN-NESTED-XWAYLAND-FAILURE"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PROC_BYTES = 256 * 1024
MAX_REPORT_BYTES = 256 * 1024
KWIN = Path("/usr/bin/kwin_wayland")
XWAYLAND = Path("/usr/bin/Xwayland")
DBUS_RUN_SESSION = Path("/usr/bin/dbus-run-session")
SYSTEMCTL = Path("/usr/bin/systemctl")
CLIENT = Path(__file__).with_name("pds004-x11-probe-client.py")
SAFE_SESSION_PATH = re.compile(r"/[A-Za-z0-9_./-]+")
ALLOWED_FAILURE_STAGES = {
    "none",
    "launcher-exit",
    "probe-ready-timeout",
    "nested-kwin-missing",
    "nested-xwayland-missing",
    "probe-client-missing",
    "xwayland-raced",
    "client-exit-timeout",
    "nested-session-exit-timeout",
}


class HarnessError(RuntimeError):
    """The isolated process boundary is unsafe or ambiguous."""


class RoundFailure(RuntimeError):
    """A bounded expected runtime gate did not pass."""

    def __init__(self, stage: str) -> None:
        if stage not in ALLOWED_FAILURE_STAGES - {"none"}:
            raise ValueError("invalid round failure stage")
        super().__init__(stage)
        self.stage = stage


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    session: int
    starttime: int
    uid: int
    exe: str


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short Xwayland report write")
        view = view[written:]


def _read_fd(descriptor: int, size: int, *, maximum: int) -> bytes:
    if size < 0 or size > maximum:
        raise HarnessError("bounded file size is invalid")
    remaining = size
    content = bytearray()
    while remaining:
        block = os.read(descriptor, min(65_536, remaining))
        if not block:
            raise HarnessError("bounded file changed while reading")
        content.extend(block)
        remaining -= len(block)
    if os.read(descriptor, 1):
        raise HarnessError("bounded file grew while reading")
    return bytes(content)


def read_owned_file(
    path: Path,
    *,
    expected_uid: int,
    expected_mode: int,
    maximum: int,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HarnessError("required isolation file is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != expected_mode
            or not 0 < metadata.st_size <= maximum
        ):
            raise HarnessError("required isolation file identity is unsafe")
        return _read_fd(descriptor, metadata.st_size, maximum=maximum)
    finally:
        os.close(descriptor)


def safe_executable(path: Path, *, expected_uid: int) -> dict[str, object]:
    content = read_owned_file(
        path,
        expected_uid=expected_uid,
        expected_mode=0o755,
        maximum=MAX_FILE_BYTES,
    )
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "owner_verified": True,
        "mode": "0755",
    }


def parse_proc_stat(content: str) -> tuple[int, int, int]:
    closing = content.rfind(")")
    if closing < 2 or closing + 2 >= len(content):
        raise HarnessError("process stat is malformed")
    fields = content[closing + 2 :].split()
    if len(fields) < 20:
        raise HarnessError("process stat is truncated")
    try:
        ppid = int(fields[1])
        session_id = int(fields[3])
        starttime = int(fields[19])
    except ValueError as exc:
        raise HarnessError("process stat numeric fields are invalid") from exc
    if ppid < 0 or session_id <= 0 or starttime <= 0:
        raise HarnessError("process stat identity is invalid")
    return ppid, session_id, starttime


def _read_proc_bytes(path: Path, *, maximum: int = MAX_PROC_BYTES) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        content = bytearray()
        while len(content) <= maximum:
            block = os.read(descriptor, min(65_536, maximum + 1 - len(content)))
            if not block:
                return bytes(content)
            content.extend(block)
        raise HarnessError("process metadata overflowed")
    finally:
        os.close(descriptor)


def process_info(pid: int) -> ProcessInfo:
    if type(pid) is not int or pid <= 1:
        raise HarnessError("process id is invalid")
    root = Path("/proc") / str(pid)
    try:
        directory = root.stat()
        stat_text = _read_proc_bytes(root / "stat").decode("ascii", errors="strict")
        exe = os.readlink(root / "exe")
    except (OSError, UnicodeError) as exc:
        raise HarnessError("process identity is unavailable") from exc
    ppid, session_id, starttime = parse_proc_stat(stat_text)
    return ProcessInfo(pid, ppid, session_id, starttime, directory.st_uid, exe)


def scan_processes() -> dict[int, ProcessInfo]:
    processes: dict[int, ProcessInfo] = {}
    try:
        entries = list(Path("/proc").iterdir())
    except OSError as exc:
        raise HarnessError("procfs is unavailable") from exc
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            info = process_info(int(entry.name))
        except HarnessError:
            continue
        processes[info.pid] = info
    return processes


def descendants(processes: dict[int, ProcessInfo], root_pid: int) -> set[int]:
    result: set[int] = set()
    changed = True
    while changed:
        changed = False
        for pid, info in processes.items():
            if pid in result or pid == root_pid:
                continue
            if info.ppid == root_pid or info.ppid in result:
                result.add(pid)
                changed = True
    return result


def _proc_tokens(pid: int) -> list[str]:
    try:
        content = _read_proc_bytes(Path("/proc") / str(pid) / "cmdline")
        return [item.decode("utf-8", errors="strict") for item in content.split(b"\0") if item]
    except (OSError, UnicodeError) as exc:
        raise HarnessError("process command identity is unavailable") from exc


def _has_private_runtime(pid: int, runtime: Path) -> bool:
    try:
        content = _read_proc_bytes(Path("/proc") / str(pid) / "environ")
    except OSError as exc:
        raise HarnessError("process environment identity is unavailable") from exc
    expected = f"XDG_RUNTIME_DIR={runtime}".encode()
    return expected in [item for item in content.split(b"\0") if item]


def unique_owned_process(
    processes: dict[int, ProcessInfo],
    *,
    root_pid: int,
    runtime: Path,
    executable: str | None = None,
    command_token: str | None = None,
) -> ProcessInfo:
    candidates: list[ProcessInfo] = []
    for pid in descendants(processes, root_pid):
        info = processes[pid]
        if info.uid != os.getuid() or info.session != root_pid:
            continue
        if executable is not None and info.exe != executable:
            continue
        if command_token is not None and command_token not in _proc_tokens(pid):
            continue
        if not _has_private_runtime(pid, runtime):
            continue
        candidates.append(info)
    if not candidates:
        raise RoundFailure(
            "nested-xwayland-missing" if executable == str(XWAYLAND) else "probe-client-missing"
        )
    if len(candidates) != 1:
        raise HarnessError("isolated process ownership is ambiguous")
    return candidates[0]


def same_process(expected: ProcessInfo) -> bool:
    try:
        current = process_info(expected.pid)
    except HarnessError:
        return False
    return (
        current.pid == expected.pid
        and current.session == expected.session
        and current.starttime == expected.starttime
        and current.uid == expected.uid
        and current.exe == expected.exe
    )


def process_snapshot(processes: dict[int, ProcessInfo]) -> set[tuple[int, int, str]]:
    return {
        (info.pid, info.starttime, info.exe)
        for info in processes.values()
        if info.uid == os.getuid() and info.exe in {str(KWIN), str(XWAYLAND)}
    }


def read_ready(path: Path) -> dict[str, object]:
    content = read_owned_file(
        path,
        expected_uid=os.getuid(),
        expected_mode=0o600,
        maximum=16 * 1024,
    )

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise HarnessError("probe readiness has duplicate keys")
            value[key] = item
        return value

    try:
        payload = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=lambda _item: (_ for _ in ()).throw(
                HarnessError("probe readiness has a non-finite number")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessError("probe readiness is malformed") from exc
    expected = {
        "schema": READY_SCHEMA,
        "backend": "x11",
        "mapped": True,
        "input_capability": False,
        "identifiers_emitted": False,
    }
    if payload != expected:
        raise HarnessError("probe readiness identity is invalid")
    return payload


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700)
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise HarnessError("private isolation directory is unsafe")


def build_environment(root: Path, ready: Path) -> dict[str, str]:
    directories = {
        "HOME": root / "home",
        "XDG_RUNTIME_DIR": root / "runtime",
        "XDG_CONFIG_HOME": root / "config",
        "XDG_CACHE_HOME": root / "cache",
        "XDG_DATA_HOME": root / "data",
    }
    for directory in directories.values():
        _private_directory(directory)
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "GDK_BACKEND": "x11",
        "NO_AT_BRIDGE": "1",
        "POCKETDS_X11_PROBE_READY": str(ready),
        **{key: str(value) for key, value in directories.items()},
    }
    return environment


def build_command(client: Path = CLIENT) -> list[str]:
    client_text = str(client)
    if not client.is_absolute() or SAFE_SESSION_PATH.fullmatch(client_text) is None:
        raise HarnessError("probe client path is unsafe for KWin session parsing")
    return [
        str(DBUS_RUN_SESSION),
        "--",
        str(KWIN),
        "--virtual",
        "--xwayland",
        "--socket",
        "pds004-isolated",
        "--width",
        "1024",
        "--height",
        "768",
        "--scale",
        "1",
        "--no-lockscreen",
        "--no-global-shortcuts",
        "--no-kactivities",
        f"--exit-with-session={client_text}",
    ]


def service_snapshot() -> dict[str, tuple[str, ...]]:
    units = (
        ("user", "pocketds-keyboard.service"),
        ("user", "pocketds-gpu-telemetry.service"),
        ("system", "inputplumber.service"),
    )
    snapshot: dict[str, tuple[str, ...]] = {}
    for scope, unit in units:
        command = [str(SYSTEMCTL)]
        if scope == "user":
            command.append("--user")
        command.extend(
            [
                "show",
                unit,
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "-p",
                "MainPID",
                "-p",
                "NRestarts",
                "-p",
                "InvocationID",
                "--no-pager",
            ]
        )
        try:
            result = subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HarnessError("service identity probe is unavailable") from exc
        if result.returncode != 0 or len(result.stdout) > 65_536:
            raise HarnessError("service identity probe failed or overflowed")
        lines = tuple(sorted(line for line in result.stdout.decode("ascii").splitlines() if line))
        if len(lines) != 5:
            raise HarnessError("service identity probe is incomplete")
        snapshot[f"{scope}:{unit}"] = lines
    return snapshot


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _owned_runtime_processes(root_pid: int, runtime: Path) -> list[ProcessInfo]:
    owned: list[ProcessInfo] = []
    for info in scan_processes().values():
        if info.uid != os.getuid() or info.session != root_pid:
            continue
        try:
            private = _has_private_runtime(info.pid, runtime)
        except HarnessError:
            continue
        if private:
            owned.append(info)
    return owned


def _signal_exact_process(info: ProcessInfo, runtime: Path, sent_signal: signal.Signals) -> None:
    if not same_process(info):
        return
    if not _has_private_runtime(info.pid, runtime):
        raise HarnessError("isolated cleanup target identity drifted")
    try:
        os.kill(info.pid, sent_signal)
    except ProcessLookupError:
        return


def cleanup_owned_session(
    launcher: subprocess.Popen[bytes],
    identity: ProcessInfo,
    runtime: Path,
) -> None:
    if launcher.poll() is None:
        current = process_info(identity.pid)
        if not same_process(identity) or current.session != identity.pid:
            raise HarnessError("isolated session leader identity drifted; cleanup refused")
        os.killpg(identity.pid, signal.SIGTERM)
    if _wait_until(lambda: not _owned_runtime_processes(identity.pid, runtime), 3):
        return
    for info in _owned_runtime_processes(identity.pid, runtime):
        _signal_exact_process(info, runtime, signal.SIGKILL)
    if not _wait_until(lambda: not _owned_runtime_processes(identity.pid, runtime), 2):
        raise HarnessError("isolated session did not stop within cleanup bound")


def _run_round_in_root(root: Path, *, startup_timeout: float, failure_timeout: float) -> dict[str, object]:
    ready = root / "client-ready.json"
    environment = build_environment(root, ready)
    started = time.monotonic()
    result: dict[str, object] = {
        "probe_ready": False,
        "nested_kwin_unique": False,
        "nested_xwayland_unique": False,
        "probe_client_unique": False,
        "xwayland_signal_sent": False,
        "client_exited_within_bound": False,
        "nested_session_exited_within_bound": False,
        "cleanup_complete": False,
        "startup_ms": None,
        "failure_stage": "none",
    }
    launcher: subprocess.Popen[bytes] | None = None
    leader: ProcessInfo | None = None
    try:
        launcher = subprocess.Popen(
            build_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
        leader = process_info(launcher.pid)
        if leader.session != leader.pid or leader.uid != os.getuid():
            raise HarnessError("new isolated session leader identity is invalid")

        def probe_ready() -> bool:
            if launcher is not None and launcher.poll() is not None:
                return True
            return ready.exists()

        if not _wait_until(probe_ready, startup_timeout):
            raise RoundFailure("probe-ready-timeout")
        if launcher.poll() is not None and not ready.exists():
            raise RoundFailure("launcher-exit")
        read_ready(ready)
        result["probe_ready"] = True
        result["startup_ms"] = int((time.monotonic() - started) * 1000)
        processes = scan_processes()
        nested_kwin = [
            info
            for pid, info in processes.items()
            if pid in descendants(processes, leader.pid)
            and info.uid == os.getuid()
            and info.session == leader.pid
            and info.exe == str(KWIN)
            and _has_private_runtime(pid, Path(environment["XDG_RUNTIME_DIR"]))
        ]
        if not nested_kwin:
            raise RoundFailure("nested-kwin-missing")
        if len(nested_kwin) != 1:
            raise HarnessError("nested KWin ownership is ambiguous")
        result["nested_kwin_unique"] = True
        xwayland = unique_owned_process(
            processes,
            root_pid=leader.pid,
            runtime=Path(environment["XDG_RUNTIME_DIR"]),
            executable=str(XWAYLAND),
        )
        result["nested_xwayland_unique"] = True
        client = unique_owned_process(
            processes,
            root_pid=leader.pid,
            runtime=Path(environment["XDG_RUNTIME_DIR"]),
            command_token=str(CLIENT),
        )
        result["probe_client_unique"] = True
        current_xwayland = process_info(xwayland.pid)
        if (
            current_xwayland != xwayland
            or current_xwayland.session != leader.pid
            or not _has_private_runtime(xwayland.pid, Path(environment["XDG_RUNTIME_DIR"]))
        ):
            raise RoundFailure("xwayland-raced")
        try:
            os.kill(xwayland.pid, signal.SIGTERM)
        except ProcessLookupError as exc:
            raise RoundFailure("xwayland-raced") from exc
        result["xwayland_signal_sent"] = True
        if not _wait_until(lambda: not same_process(client), failure_timeout):
            raise RoundFailure("client-exit-timeout")
        result["client_exited_within_bound"] = True
        if not _wait_until(lambda: launcher.poll() is not None, failure_timeout):
            raise RoundFailure("nested-session-exit-timeout")
        result["nested_session_exited_within_bound"] = True
    except RoundFailure as exc:
        result["failure_stage"] = exc.stage
    finally:
        if launcher is not None and leader is not None:
            cleanup_owned_session(
                launcher,
                leader,
                Path(environment["XDG_RUNTIME_DIR"]),
            )
        result["cleanup_complete"] = launcher is None or launcher.poll() is not None
    return result


def run_round(runtime_base: Path, *, startup_timeout: float, failure_timeout: float) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="pds004-xwayland-", dir=runtime_base) as temporary:
        root = Path(temporary)
        os.chmod(root, 0o700)
        result = _run_round_in_root(
            root,
            startup_timeout=startup_timeout,
            failure_timeout=failure_timeout,
        )
    result["private_runtime_removed"] = not root.exists()
    return result


def evaluate_run(
    rounds: list[dict[str, object]],
    *,
    host_processes_unchanged: bool,
    support_services_unchanged: bool,
) -> dict[str, object]:
    required_fields = {
        "probe_ready",
        "nested_kwin_unique",
        "nested_xwayland_unique",
        "probe_client_unique",
        "xwayland_signal_sent",
        "client_exited_within_bound",
        "nested_session_exited_within_bound",
        "cleanup_complete",
        "private_runtime_removed",
        "startup_ms",
        "failure_stage",
    }
    clean_rounds: list[dict[str, object]] = []
    for record in rounds:
        if type(record) is not dict or set(record) != required_fields:
            raise HarnessError("round result fields are invalid")
        if record["failure_stage"] not in ALLOWED_FAILURE_STAGES:
            raise HarnessError("round failure stage is invalid")
        booleans = {
            key: value
            for key, value in record.items()
            if key not in {"startup_ms", "failure_stage"}
        }
        if any(type(value) is not bool for value in booleans.values()):
            raise HarnessError("round result boolean is invalid")
        startup = record["startup_ms"]
        if startup is not None and (type(startup) is not int or not 0 <= startup <= 60_000):
            raise HarnessError("round startup bound is invalid")
        passed = (
            all(booleans.values())
            and record["failure_stage"] == "none"
            and startup is not None
        )
        clean_rounds.append({**record, "pass": passed})
    accepted = (
        len(clean_rounds) >= 3
        and all(bool(item["pass"]) for item in clean_rounds)
        and host_processes_unchanged
        and support_services_unchanged
    )
    return {
        "schema": SCHEMA,
        "mode": "nested-virtual-kwin-xwayland-failure",
        "privacy": {
            "pids_emitted": False,
            "paths_emitted": False,
            "timestamps_emitted": False,
            "application_names_emitted": False,
            "display_names_emitted": False,
        },
        "safety": {
            "nested_virtual_backend_only": True,
            "live_compositor_signaled": False,
            "input_injected": False,
            "network_used": False,
            "host_processes_unchanged": host_processes_unchanged,
            "support_services_unchanged": support_services_unchanged,
        },
        "rounds": clean_rounds,
        "passed_round_count": sum(bool(item["pass"]) for item in clean_rounds),
        "required_round_count": 3,
        "accepted": accepted,
        "result": "PASS" if accepted else "INCOMPLETE",
    }


def runtime_base() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    if not value:
        raise HarnessError("XDG runtime directory is unavailable")
    path = Path(value)
    metadata = path.lstat()
    if (
        not path.is_absolute()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise HarnessError("XDG runtime directory identity is unsafe")
    return path


def preflight() -> dict[str, object]:
    executable_evidence = {
        "kwin_wayland": safe_executable(KWIN, expected_uid=0),
        "xwayland": safe_executable(XWAYLAND, expected_uid=0),
        "dbus_run_session": safe_executable(DBUS_RUN_SESSION, expected_uid=0),
        "systemctl": safe_executable(SYSTEMCTL, expected_uid=0),
        "probe_client": safe_executable(CLIENT, expected_uid=os.getuid()),
    }
    if platform.system() != "Linux" or not Path("/proc/self/stat").is_file():
        raise HarnessError("nested Xwayland acceptance requires Linux procfs")
    runtime_base()
    build_command()
    return executable_evidence


def plan() -> dict[str, object]:
    try:
        executables = preflight()
        safe = True
    except (HarnessError, OSError):
        executables = {}
        safe = False
    return {
        "schema": SCHEMA,
        "operation": "PLAN",
        "safe_to_execute": safe,
        "confirmation_required": CONFIRMATION,
        "rounds": 3,
        "virtual_width": 1024,
        "virtual_height": 768,
        "rootless_xwayland": True,
        "live_compositor_signaled": False,
        "input_injected": False,
        "services_restarted": False,
        "network_used": False,
        "absolute_paths_emitted": False,
        "executable_count": len(executables),
    }


def write_report(report: dict[str, object], destination: Path) -> None:
    content = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(content) > MAX_REPORT_BYTES:
        raise HarnessError("Xwayland report overflowed")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(destination, flags, 0o600)
    try:
        _write_all(descriptor, content)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def execute(round_count: int, output: Path) -> dict[str, object]:
    executables = preflight()
    before_services = service_snapshot()
    before_processes = process_snapshot(scan_processes())
    base = runtime_base()
    rounds = [
        run_round(base, startup_timeout=15, failure_timeout=5)
        for _index in range(round_count)
    ]
    after_processes = process_snapshot(scan_processes())
    after_services = service_snapshot()
    report = evaluate_run(
        rounds,
        host_processes_unchanged=before_processes == after_processes,
        support_services_unchanged=before_services == after_services,
    )
    report["executables"] = executables
    write_report(report, output)
    return report


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.execute:
        if (
            arguments.confirm != CONFIRMATION
            or arguments.output is None
            or not 3 <= arguments.rounds <= 5
        ):
            parser.error("execution needs exact confirmation, new output and 3--5 rounds")
    elif arguments.confirm is not None or arguments.output is not None or arguments.rounds != 3:
        parser.error("read-only plan accepts no execution arguments")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    if not arguments.execute:
        print(json.dumps(plan(), indent=2, sort_keys=True))
        return 0
    try:
        report = execute(arguments.rounds, arguments.output)
    except (HarnessError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        print(f"nested Xwayland acceptance failed: {exc}", file=sys.stderr)
        return 2
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
