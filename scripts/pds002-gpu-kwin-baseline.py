#!/usr/bin/env python3
"""Stdout-only Pocket DS GPU/KWin baseline collector.

The collector reads the current desktop and kernel state and emits JSON Lines
to stdout.  It deliberately has no file-output option.  Capture stdout on the
SSH client if a durable report is required.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Iterable


SCHEMA = "pds002-baseline/v1"
JOURNAL_COMMAND = "journalctl"
KWIN_COMMAND = "busctl"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
FIXED_CONTROL_PATHS = {
    "kwin_wayland": Path("/usr/bin/kwin_wayland"),
    "a740_sqe_firmware": Path("/usr/lib/firmware/qcom/a740_sqe.fw"),
    "gmu_gen70200_firmware": Path("/usr/lib/firmware/qcom/gmu_gen70200.bin"),
    "fan_controller": Path("/usr/bin/pocketds-fancontrol"),
}
RUNTIME_CONTROL_PATHS = {
    "tuned_active_profile": Path("/etc/tuned/active_profile"),
    "fan_profile": Path("/etc/pocketds-fancontrol/profile"),
}

JOURNAL_CATEGORIES = (
    "kwin_atomic_ebusy",
    "gmu_oob_timeout",
    "gpu_lockup",
    "gpu_recover",
    "gpu_offender",
    "hfi_error",
    "smmu_fault",
    "fenced_register_delay",
    "dma_fence_log",
    "hung_task",
)

# /proc/status Name is limited to 15 bytes. Match both full and truncated
# desktop process names; a sustained D-state here is visible to the user even
# when the kernel hides wchan as "0" and emits no hung-task report.
CRITICAL_DSTATE_PREFIXES = (
    "kwin_wayland",
    "plasmashell",
    "plasma-plasmas",
    "Xwayland",
    "chromium",
    "steam",
    "gamescope",
    "retroarch",
    "es-de",
)
PROCESS_CATEGORIES = (
    "d_state_enter",
    "d_state_change",
    "d_state_exit",
    "dma_fence_wait",
)
ALL_CATEGORIES = JOURNAL_CATEGORIES + PROCESS_CATEGORIES

HFI_RE = re.compile(r"(?<![a-z0-9])hfi(?![a-z0-9])", re.IGNORECASE)
HFI_FAILURE_RE = re.compile(
    r"error|failed|failure|timeout|timed\s+out|unresponsive", re.IGNORECASE
)
KWIN_SCREEN_RE = re.compile(
    r"Screen\s+\d+:\n-+\n(.*?)(?=\nScreen\s+\d+:|\nCompositing\n|\Z)",
    re.DOTALL,
)

STOP_REQUESTED = False


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def consumed_cpu_s() -> float:
    """Return collector plus completed helper-process CPU time."""
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


def emit(record: dict[str, Any]) -> None:
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None


def read_int(path: Path) -> int | None:
    value = read_text(path)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def boot_id(proc_root: Path = Path("/proc")) -> str | None:
    return read_text(proc_root / "sys/kernel/random/boot_id")


def boot_uptime_s(proc_root: Path = Path("/proc")) -> float | None:
    value = read_text(proc_root / "uptime")
    if not value:
        return None
    try:
        return float(value.split()[0])
    except (IndexError, ValueError):
        return None


def kernel_notes_snapshot(path: Path = Path("/sys/kernel/notes")) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        return {"sha256": None, "size": None, "error": type(exc).__name__}
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size > 4096
        ):
            return {"sha256": None, "size": None, "error": "unsafe-metadata"}
        content = bytearray()
        while len(content) <= 4096:
            block = os.read(descriptor, min(4097 - len(content), 4096))
            if not block:
                break
            content.extend(block)
        if not content or len(content) > 4096:
            return {"sha256": None, "size": None, "error": "unsafe-content-size"}
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
        ):
            return {"sha256": None, "size": None, "error": "identity-changed"}
        return {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": before.st_size,
            "error": None,
        }
    finally:
        os.close(descriptor)


def regular_artifact_snapshot(path: Path, maximum: int = 16 * 1024 * 1024) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        return {"sha256": None, "size": None, "error": type(exc).__name__}
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > maximum
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            return {"sha256": None, "size": None, "error": "unsafe-metadata"}
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1_048_576, remaining))
            if not block:
                return {"sha256": None, "size": None, "error": "short-read"}
            digest.update(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            return {"sha256": None, "size": None, "error": "grew-during-read"}
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
        ):
            return {"sha256": None, "size": None, "error": "identity-changed"}
        return {"sha256": digest.hexdigest(), "size": before.st_size, "error": None}
    finally:
        os.close(descriptor)


def fixed_control_snapshot(
    paths: dict[str, Path] = FIXED_CONTROL_PATHS,
) -> dict[str, dict[str, Any]]:
    return {name: regular_artifact_snapshot(path) for name, path in paths.items()}


def runtime_control_snapshot(
    paths: dict[str, Path] = RUNTIME_CONTROL_PATHS,
) -> dict[str, dict[str, Any]]:
    return {
        name: regular_artifact_snapshot(path, maximum=4096)
        for name, path in paths.items()
    }


def snapshot_signature(value: object) -> str:
    content = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def parse_pressure(text: str | None) -> dict[str, dict[str, float | int]] | None:
    if not text:
        return None
    result: dict[str, dict[str, float | int]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        values: dict[str, float | int] = {}
        for field in fields[1:]:
            key, separator, value = field.partition("=")
            if not separator:
                continue
            try:
                values[key] = int(value) if key == "total" else float(value)
            except ValueError:
                continue
        result[fields[0]] = values
    return result or None


def pressure_snapshot(proc_root: Path = Path("/proc")) -> dict[str, Any]:
    return {
        resource: parse_pressure(read_text(proc_root / "pressure" / resource))
        for resource in ("cpu", "io", "memory")
    }


def parse_gpu_status(text: str | None, now_unix_ms: int) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    sample_ms = payload.get("sample_unix_ms")
    age_ms = None
    if isinstance(sample_ms, int):
        age_ms = max(0, now_unix_ms - sample_ms)
    return {
        "source": payload.get("source"),
        "semantics": payload.get("semantics"),
        "sample_unix_ms": sample_ms,
        "age_ms": age_ms,
        "gpu_percent": payload.get("gpu_percent"),
        "gpu_freq_hz": payload.get("gpu_freq_hz"),
        "gpu_temp_c": payload.get("gpu_temp_c"),
        "gpu_clients": payload.get("gpu_clients"),
    }


def gpu_status_snapshot(path: Path) -> dict[str, Any] | None:
    return parse_gpu_status(read_text(path), int(time.time() * 1000))


def field(text: str, label: str) -> str | None:
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def parse_kwin_support(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    outputs: list[dict[str, Any]] = []
    for block in KWIN_SCREEN_RE.findall(text):
        refresh = field(block, "Refresh Rate")
        scale = field(block, "Scale")
        enabled = field(block, "Enabled")
        try:
            refresh_millihz = int(refresh) if refresh is not None else None
        except ValueError:
            refresh_millihz = None
        try:
            scale_value = float(scale) if scale is not None else None
        except ValueError:
            scale_value = None
        outputs.append(
            {
                "name": field(block, "Name"),
                "enabled": enabled == "1" if enabled is not None else None,
                "geometry": field(block, "Geometry"),
                "scale": scale_value,
                "refresh_millihz": refresh_millihz,
                "adaptive_sync": field(block, "Adaptive Sync"),
            }
        )
    outputs.sort(key=lambda item: str(item.get("name")))
    return {
        "operation_mode": field(text, "Operation Mode"),
        "backend": field(text, "Name"),
        "atomic": field(text, "Atomic Mode Setting on GPU 0"),
        "renderer": field(text, "OpenGL renderer string"),
        "mesa": field(text, "Mesa version"),
        "outputs": outputs,
    }


def kwin_snapshot() -> tuple[dict[str, Any] | None, str | None]:
    command = [
        KWIN_COMMAND,
        "--user",
        "--json=short",
        "call",
        "org.kde.KWin",
        "/KWin",
        "org.kde.KWin",
        "supportInformation",
    ]
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=8
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        return None, type(error).__name__
    if result.returncode != 0:
        return None, result.stderr.strip()[:500] or f"returncode={result.returncode}"
    try:
        envelope = json.loads(result.stdout)
        support = envelope["data"][0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None, "invalid busctl JSON"
    return parse_kwin_support(support), None


def drm_connectors(drm_root: Path = Path("/sys/class/drm")) -> list[dict[str, Any]]:
    connectors: list[dict[str, Any]] = []
    try:
        paths = sorted(drm_root.glob("card*-DSI-*"))
    except OSError:
        paths = []
    for path in paths:
        modes_text = read_text(path / "modes")
        modes = modes_text.splitlines() if modes_text else []
        connectors.append(
            {
                "name": path.name.split("-", 1)[-1],
                "status": read_text(path / "status"),
                "enabled": read_text(path / "enabled"),
                "modes": modes,
            }
        )
    return connectors


def signature_for(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def display_snapshot(drm_root: Path = Path("/sys/class/drm")) -> dict[str, Any]:
    kwin, error = kwin_snapshot()
    payload = {"kwin": kwin, "connectors": drm_connectors(drm_root)}
    return {
        **payload,
        "signature": signature_for(payload),
        "kwin_error": error,
    }


def journal_source(entry: dict[str, Any]) -> str:
    for key in ("_COMM", "SYSLOG_IDENTIFIER", "_EXE"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def classify_journal(message: str, source: str = "") -> list[str]:
    lower = message.lower()
    source_lower = source.lower()
    categories: list[str] = []
    busy = (
        "device or resource busy" in lower
        or "设备或资源忙" in message
        or "ebusy" in lower
    )
    if (
        "atomic commit failed" in lower
        and "kwin_wayland" in source_lower
        and busy
    ):
        categories.append("kwin_atomic_ebusy")
    if "timeout waiting for gmu oob" in lower:
        categories.append("gmu_oob_timeout")
    if "hangcheck detected gpu lockup" in lower:
        categories.append("gpu_lockup")
    if "hangcheck recover" in lower:
        categories.append("gpu_recover")
    if "offending task:" in lower:
        categories.append("gpu_offender")
    if HFI_RE.search(message) and HFI_FAILURE_RE.search(message):
        categories.append("hfi_error")
    if "unhandled context fault" in lower:
        categories.append("smmu_fault")
    if "delay in fenced register write" in lower:
        categories.append("fenced_register_delay")
    if "dma_fence" in lower or "fence_default_wait" in lower:
        categories.append("dma_fence_log")
    if "blocked for more than" in lower or "hung task" in lower:
        categories.append("hung_task")
    return categories


def journal_monotonic_s(entry: dict[str, Any]) -> float | None:
    for key in ("__MONOTONIC_TIMESTAMP", "_SOURCE_MONOTONIC_TIMESTAMP"):
        value = entry.get(key)
        try:
            return int(value) / 1_000_000.0
        except (TypeError, ValueError):
            continue
    return None


def journal_event(entry: dict[str, Any], category: str) -> dict[str, Any]:
    message = entry.get("MESSAGE", "")
    if not isinstance(message, str):
        message = str(message)
    return {
        "type": "event",
        "timestamp": utc_now(),
        "journal_realtime_us": entry.get("__REALTIME_TIMESTAMP"),
        "boot_monotonic_s": journal_monotonic_s(entry),
        "category": category,
        "origin": "journal",
        "source": journal_source(entry),
        "pid": entry.get("_PID"),
        "message": message[:2000],
    }


def count_journal_stream(lines: Iterable[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(entry, dict):
            continue
        message = entry.get("MESSAGE", "")
        if not isinstance(message, str):
            message = str(message)
        for category in classify_journal(message, journal_source(entry)):
            counts[category] += 1
    return counts


def journal_boot_counts() -> tuple[dict[str, int] | None, str | None]:
    command = [JOURNAL_COMMAND, "-b", "--quiet", "--no-pager", "-o", "json"]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (FileNotFoundError, OSError) as error:
        return None, type(error).__name__
    assert process.stdout is not None
    counts = count_journal_stream(process.stdout)
    _stdout, stderr = process.communicate()
    if process.returncode != 0:
        return None, stderr.strip()[:500] or f"returncode={process.returncode}"
    return {key: counts.get(key, 0) for key in JOURNAL_CATEGORIES}, None


class JournalFollower:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.selector = selectors.DefaultSelector()
        self.errors: list[str] = []

    def start(self) -> None:
        command = [
            JOURNAL_COMMAND,
            "-b",
            "-f",
            "-n",
            "0",
            "--quiet",
            "--no-pager",
            "-o",
            "json",
        ]
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError) as error:
            self.errors.append(type(error).__name__)
            return
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")

    def poll(self, timeout: float) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if self.process is None:
            time.sleep(max(0.0, timeout))
            return entries
        for key, _mask in self.selector.select(max(0.0, timeout)):
            line = key.fileobj.readline()
            if line == "":
                try:
                    self.selector.unregister(key.fileobj)
                except (KeyError, ValueError):
                    pass
                continue
            if key.data == "stderr":
                self.errors.append(line.strip()[:500])
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    def stop(self) -> int | None:
        if self.process is None:
            return None
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        return self.process.returncode


def parse_proc_status(text: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not text:
        return result
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key] = value.strip()
    return result


def scan_d_state(proc_root: Path = Path("/proc")) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    try:
        entries = list(os.scandir(proc_root))
    except (FileNotFoundError, PermissionError, OSError):
        return result
    for entry in entries:
        if not entry.name.isdigit() or not entry.is_dir(follow_symlinks=False):
            continue
        pid = int(entry.name)
        root = Path(entry.path)
        status = parse_proc_status(read_text(root / "status"))
        state = status.get("State", "")
        if not state.startswith("D"):
            continue
        result[pid] = {
            "pid": pid,
            "name": status.get("Name"),
            "uid": (status.get("Uid") or "").split()[0] or None,
            "state": state,
            "wchan": read_text(root / "wchan"),
        }
    return result


class DStateTracker:
    def __init__(self) -> None:
        self.active: dict[int, dict[str, Any]] = {}
        self.intervals: list[dict[str, Any]] = []

    def update(
        self, current: dict[int, dict[str, Any]], now_monotonic: float
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for pid, record in current.items():
            previous = self.active.get(pid)
            if previous is None:
                self.active[pid] = {**record, "started_monotonic_s": now_monotonic}
                events.append(self._event("d_state_enter", record, now_monotonic))
                if is_fence_wchan(record.get("wchan")):
                    events.append(self._event("dma_fence_wait", record, now_monotonic))
            elif previous.get("wchan") != record.get("wchan"):
                self.active[pid] = {
                    **record,
                    "started_monotonic_s": previous["started_monotonic_s"],
                }
                events.append(self._event("d_state_change", record, now_monotonic))
                if is_fence_wchan(record.get("wchan")):
                    events.append(self._event("dma_fence_wait", record, now_monotonic))
        for pid in sorted(set(self.active) - set(current)):
            previous = self.active.pop(pid)
            interval = {
                "pid": pid,
                "name": previous.get("name"),
                "wchan": previous.get("wchan"),
                "started_monotonic_s": previous["started_monotonic_s"],
                "ended_monotonic_s": now_monotonic,
                "duration_s": round(
                    now_monotonic - previous["started_monotonic_s"], 3
                ),
                "open": False,
            }
            self.intervals.append(interval)
            events.append(self._event("d_state_exit", previous, now_monotonic))
        return events

    def summary(self, now_monotonic: float) -> list[dict[str, Any]]:
        result = list(self.intervals)
        for pid, record in sorted(self.active.items()):
            result.append(
                {
                    "pid": pid,
                    "name": record.get("name"),
                    "wchan": record.get("wchan"),
                    "started_monotonic_s": record["started_monotonic_s"],
                    "ended_monotonic_s": None,
                    "duration_s": round(
                        now_monotonic - record["started_monotonic_s"], 3
                    ),
                    "open": True,
                }
            )
        return result

    @staticmethod
    def _event(
        category: str, record: dict[str, Any], now_monotonic: float
    ) -> dict[str, Any]:
        return {
            "type": "event",
            "timestamp": utc_now(),
            "boot_monotonic_s": round(now_monotonic, 6),
            "category": category,
            "origin": "proc",
            "pid": record.get("pid"),
            "source": record.get("name"),
            "wchan": record.get("wchan"),
        }


def critical_user_d_state_intervals(
    intervals: list[dict[str, Any]], minimum_duration_s: float
) -> list[dict[str, Any]]:
    """Return sustained D-states from interactive desktop/game processes."""
    result = []
    for interval in intervals:
        name = interval.get("name")
        duration = interval.get("duration_s")
        if not isinstance(name, str) or not isinstance(duration, (int, float)):
            continue
        if duration < minimum_duration_s:
            continue
        if any(name.startswith(prefix) for prefix in CRITICAL_DSTATE_PREFIXES):
            result.append(interval)
    return result


def is_fence_wchan(wchan: Any) -> bool:
    if not isinstance(wchan, str):
        return False
    lower = wchan.lower()
    return "dma_fence" in lower or "fence_default_wait" in lower


class EventStats:
    def __init__(self, burst_gap_s: float) -> None:
        self.burst_gap_s = burst_gap_s
        self.counts: Counter[str] = Counter()
        self.times: dict[str, list[float]] = defaultdict(list)

    def add(self, category: str, monotonic_s: float | None) -> None:
        self.counts[category] += 1
        if monotonic_s is not None:
            self.times[category].append(monotonic_s)

    def rates(self, wall_s: float) -> dict[str, float]:
        hours = max(wall_s, 1e-9) / 3600.0
        return {
            category: round(self.counts.get(category, 0) / hours, 3)
            for category in ALL_CATEGORIES
        }

    def bursts(self) -> dict[str, dict[str, Any]]:
        return {
            category: burst_summary(self.times.get(category, []), self.burst_gap_s)
            for category in ALL_CATEGORIES
        }


def burst_summary(times: Iterable[float], gap_s: float) -> dict[str, Any]:
    ordered = sorted(times)
    if not ordered:
        return {
            "gap_threshold_s": gap_s,
            "burst_count": 0,
            "max_events": 0,
            "max_span_s": 0.0,
            "max_inter_event_gap_s": None,
        }
    groups: list[list[float]] = [[ordered[0]]]
    gaps: list[float] = []
    for value in ordered[1:]:
        gap = value - groups[-1][-1]
        gaps.append(gap)
        if gap <= gap_s:
            groups[-1].append(value)
        else:
            groups.append([value])
    return {
        "gap_threshold_s": gap_s,
        "burst_count": len(groups),
        "max_events": max(len(group) for group in groups),
        "max_span_s": round(max(group[-1] - group[0] for group in groups), 3),
        "max_inter_event_gap_s": round(max(gaps), 3) if gaps else None,
    }


def normalized_counts(counts: Counter[str] | dict[str, int]) -> dict[str, int]:
    return {category: int(counts.get(category, 0)) for category in ALL_CATEGORIES}


def journal_delta(
    start: dict[str, int] | None, end: dict[str, int] | None
) -> dict[str, int] | None:
    if start is None or end is None:
        return None
    return {
        category: max(0, end.get(category, 0) - start.get(category, 0))
        for category in JOURNAL_CATEGORIES
    }


def collect(args: argparse.Namespace) -> int:
    global STOP_REQUESTED
    STOP_REQUESTED = False
    start_cpu = consumed_cpu_s()
    initial_boot = boot_id(args.proc_root)
    initial_display = display_snapshot(args.drm_root)
    initial_fixed_controls = fixed_control_snapshot()
    initial_runtime_controls = runtime_control_snapshot()
    initial_counts, initial_counts_error = (
        (None, "disabled") if args.no_boot_snapshot else journal_boot_counts()
    )
    follower = JournalFollower()
    if not args.no_journal_follow:
        follower.start()

    start_monotonic = time.monotonic()
    start_boot_uptime = boot_uptime_s(args.proc_root)
    header = {
        "type": "header",
        "schema": SCHEMA,
        "started_at": utc_now(),
        "duration_s": args.duration,
        "interval_s": args.interval,
        "display_interval_s": args.display_interval,
        "burst_gap_s": args.burst_gap,
        "repo_revision": args.repo_revision,
        "workload_sha256": args.workload_sha256,
        "read_only": {
            "device_files_created": False,
            "device_output": "stdout-jsonl-only",
            "subprocess_allowlist": [JOURNAL_COMMAND, KWIN_COMMAND],
        },
        "boot": {
            "id": initial_boot,
            "kernel": os.uname().release if hasattr(os, "uname") else None,
            "uptime_s": start_boot_uptime,
            "notes": kernel_notes_snapshot(),
        },
        "fixed_controls": initial_fixed_controls,
        "runtime_controls": initial_runtime_controls,
        "display": initial_display,
        "journal_boot_counts_at_start": initial_counts,
        "journal_boot_counts_error": initial_counts_error,
        "telemetry_path": str(args.telemetry),
    }
    emit(header)

    stats = EventStats(args.burst_gap)
    journal_follow_counts: Counter[str] = Counter()
    d_tracker = DStateTracker()
    deadline = start_monotonic + args.duration
    next_sample = start_monotonic
    sample_count = 0
    sample_times: list[float] = []
    current_display = initial_display
    display_signatures_seen = {initial_display["signature"]}
    display_probe_errors: list[str] = []
    next_display_probe = start_monotonic + args.display_interval
    runtime_control_signatures_seen = {
        snapshot_signature(initial_runtime_controls)
    }

    while not STOP_REQUESTED:
        now = time.monotonic()
        if now >= deadline:
            break
        timeout = min(0.1, max(0.0, next_sample - now), max(0.0, deadline - now))
        for entry in follower.poll(timeout):
            message = entry.get("MESSAGE", "")
            if not isinstance(message, str):
                message = str(message)
            source = journal_source(entry)
            event_monotonic = journal_monotonic_s(entry)
            for category in classify_journal(message, source):
                event = journal_event(entry, category)
                emit(event)
                stats.add(category, event_monotonic)
                journal_follow_counts[category] += 1

        now = time.monotonic()
        if now < next_sample:
            continue
        current_boot_uptime = boot_uptime_s(args.proc_root)
        if now >= next_display_probe:
            current_display = display_snapshot(args.drm_root)
            display_signatures_seen.add(current_display["signature"])
            if current_display.get("kwin_error"):
                display_probe_errors.append(str(current_display["kwin_error"])[:500])
            next_display_probe = now + args.display_interval
        d_states = scan_d_state(args.proc_root)
        d_now = current_boot_uptime if current_boot_uptime is not None else now
        for event in d_tracker.update(d_states, d_now):
            emit(event)
            stats.add(event["category"], event.get("boot_monotonic_s"))
        current_runtime_controls = runtime_control_snapshot()
        runtime_control_signatures_seen.add(
            snapshot_signature(current_runtime_controls)
        )
        sample = {
            "type": "sample",
            "schema": SCHEMA,
            "seq": sample_count,
            "timestamp": utc_now(),
            "elapsed_s": round(now - start_monotonic, 3),
            "boot_monotonic_s": current_boot_uptime,
            "gpu": gpu_status_snapshot(args.telemetry),
            "pressure": pressure_snapshot(args.proc_root),
            "d_state": list(d_states.values()),
            "display_signature": current_display["signature"],
            "runtime_controls": current_runtime_controls,
        }
        emit(sample)
        sample_times.append(now)
        sample_count += 1
        next_sample += args.interval
        if next_sample <= time.monotonic():
            next_sample = time.monotonic() + args.interval

    for entry in follower.poll(0.0):
        message = entry.get("MESSAGE", "")
        if not isinstance(message, str):
            message = str(message)
        source = journal_source(entry)
        event_monotonic = journal_monotonic_s(entry)
        for category in classify_journal(message, source):
            emit(journal_event(entry, category))
            stats.add(category, event_monotonic)
            journal_follow_counts[category] += 1
    follower_returncode = follower.stop()

    end_monotonic = time.monotonic()
    wall_s = max(end_monotonic - start_monotonic, 1e-9)
    ending_boot = boot_id(args.proc_root)
    ending_display = display_snapshot(args.drm_root)
    ending_fixed_controls = fixed_control_snapshot()
    ending_runtime_controls = runtime_control_snapshot()
    runtime_control_signatures_seen.add(
        snapshot_signature(ending_runtime_controls)
    )
    ending_counts, ending_counts_error = (
        (None, "disabled") if args.no_boot_snapshot else journal_boot_counts()
    )
    delta = journal_delta(initial_counts, ending_counts)
    followed = {category: journal_follow_counts.get(category, 0) for category in JOURNAL_CATEGORIES}
    journal_integrity = delta == followed if delta is not None else None
    sample_gaps = [
        later - earlier for earlier, later in zip(sample_times, sample_times[1:])
    ]
    current_uptime = boot_uptime_s(args.proc_root)
    d_summary_now = current_uptime if current_uptime is not None else end_monotonic
    critical = (
        "gmu_oob_timeout",
        "gpu_lockup",
        "hfi_error",
        "dma_fence_log",
        "dma_fence_wait",
        "hung_task",
    )
    boot_unchanged = initial_boot == ending_boot
    display_unchanged = initial_display["signature"] == ending_display["signature"]
    display_signatures_seen.add(ending_display["signature"])
    display_changed_during_run = len(display_signatures_seen) > 1
    fixed_controls_unchanged = initial_fixed_controls == ending_fixed_controls
    runtime_controls_unchanged = (
        initial_runtime_controls == ending_runtime_controls
        and len(runtime_control_signatures_seen) == 1
    )
    inconclusive = (
        not boot_unchanged
        or not display_unchanged
        or display_changed_during_run
        or not fixed_controls_unchanged
        or not runtime_controls_unchanged
        or journal_integrity is False
        or (sample_gaps and max(sample_gaps) > args.interval * 2.0)
    )
    d_state_intervals = d_tracker.summary(d_summary_now)
    critical_d_state_min_s = args.interval * 1.5
    critical_d_states = critical_user_d_state_intervals(
        d_state_intervals, critical_d_state_min_s
    )
    observed = (
        any(stats.counts.get(category, 0) for category in critical)
        or bool(stats.counts.get("kwin_atomic_ebusy", 0))
        or bool(critical_d_states)
    )
    result = "inconclusive" if inconclusive else ("observed" if observed else "clean")
    summary = {
        "type": "summary",
        "schema": SCHEMA,
        "ended_at": utc_now(),
        "wall_s": round(wall_s, 3),
        "sample_count": sample_count,
        "sample_gap_max_s": round(max(sample_gaps), 3) if sample_gaps else None,
        "counts": normalized_counts(stats.counts),
        "rates_per_hour": stats.rates(wall_s),
        "bursts": stats.bursts(),
        "d_state_intervals": d_state_intervals,
        "critical_user_d_state_min_s": round(critical_d_state_min_s, 3),
        "critical_user_d_state_intervals": critical_d_states,
        "collector_cpu_percent": round(
            (consumed_cpu_s() - start_cpu) * 100.0 / wall_s, 3
        ),
        "journal": {
            "boot_counts_at_start": initial_counts,
            "boot_counts_at_end": ending_counts,
            "snapshot_delta": delta,
            "follow_counts": followed,
            "integrity": journal_integrity,
            "start_error": initial_counts_error,
            "end_error": ending_counts_error,
            "follow_errors": follower.errors[-10:],
            "follower_returncode": follower_returncode,
        },
        "boot": {"start": initial_boot, "end": ending_boot, "unchanged": boot_unchanged},
        "display": {
            "start_signature": initial_display["signature"],
            "end_signature": ending_display["signature"],
            "unchanged": display_unchanged,
            "changed_during_run": display_changed_during_run,
            "signatures_seen": sorted(display_signatures_seen),
            "probe_errors": display_probe_errors[-10:],
            "end": ending_display,
        },
        "fixed_controls": {
            "start": initial_fixed_controls,
            "end": ending_fixed_controls,
            "unchanged": fixed_controls_unchanged,
        },
        "runtime_controls": {
            "start": initial_runtime_controls,
            "end": ending_runtime_controls,
            "unchanged": runtime_controls_unchanged,
            "signatures_seen": sorted(runtime_control_signatures_seen),
        },
        "interrupted": STOP_REQUESTED,
        "result": result,
    }
    emit(summary)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=2700.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--display-interval", type=float, default=60.0)
    parser.add_argument("--burst-gap", type=float, default=5.0)
    parser.add_argument("--repo-revision", default="unspecified")
    parser.add_argument("--workload-sha256", default="unspecified")
    parser.add_argument("--uid", type=int, default=os.getuid())
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--drm-root", type=Path, default=Path("/sys/class/drm"))
    parser.add_argument("--telemetry", type=Path, default=None)
    parser.add_argument("--no-boot-snapshot", action="store_true")
    parser.add_argument("--no-journal-follow", action="store_true")
    args = parser.parse_args(argv)
    if not 0.2 <= args.duration <= 7200:
        parser.error("--duration must be between 0.2 and 7200 seconds")
    if not 0.1 <= args.interval <= 60:
        parser.error("--interval must be between 0.1 and 60 seconds")
    if not 1 <= args.display_interval <= 600:
        parser.error("--display-interval must be between 1 and 600 seconds")
    if args.workload_sha256 != "unspecified" and HEX64_RE.fullmatch(args.workload_sha256) is None:
        parser.error("--workload-sha256 must be a lowercase SHA-256 or unspecified")
    if not 0.1 <= args.burst_gap <= 60:
        parser.error("--burst-gap must be between 0.1 and 60 seconds")
    if args.telemetry is None:
        args.telemetry = Path(f"/run/user/{args.uid}/pocketds-gpu-status.json")
    return args


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    return collect(parse_args(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
