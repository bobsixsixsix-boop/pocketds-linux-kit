#!/usr/bin/env python3
"""Run a bounded, read-only continuity/resource soak on Panel telemetry."""

from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Sequence


SCHEMA_VERSION = 3
SERVICE = "pocketds-gpu-telemetry.service"
SERVICE_PROPERTIES = (
    "Id",
    "ActiveState",
    "SubState",
    "MainPID",
    "NRestarts",
    "MemoryCurrent",
    "MemoryPeak",
    "CPUUsageNSec",
)
MAX_CACHE_BYTES = 262_144
MAX_REFRESH_TRANSITION_EVENTS = 32
STOP_REQUESTED = False


class SoakError(RuntimeError):
    """A bounded collector or contract failure."""


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def runtime_cache() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "pocketds-gpu-status.json"


def _optional_nonnegative(value: str, name: str) -> int | None:
    if value in {"", "[not set]", "n/a"}:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SoakError(f"invalid {name}: {value!r}") from exc
    if parsed < 0:
        raise SoakError(f"negative {name}: {parsed}")
    return parsed


def service_snapshot(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    result = runner(
        [
            "/usr/bin/systemctl",
            "--user",
            "show",
            "--no-pager",
            f"--property={','.join(SERVICE_PROPERTIES)}",
            SERVICE,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip()[:300] or f"exit {result.returncode}"
        raise SoakError(f"cannot inspect {SERVICE}: {detail}")
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    missing = set(SERVICE_PROPERTIES) - fields.keys()
    if missing or fields.get("Id") != SERVICE:
        raise SoakError(f"service status contract mismatch: {sorted(missing)}")
    return {
        "active_state": fields["ActiveState"],
        "sub_state": fields["SubState"],
        "main_pid": _optional_nonnegative(fields["MainPID"], "MainPID"),
        "n_restarts": _optional_nonnegative(fields["NRestarts"], "NRestarts"),
        "memory_current": _optional_nonnegative(
            fields["MemoryCurrent"], "MemoryCurrent"
        ),
        "memory_peak": _optional_nonnegative(fields["MemoryPeak"], "MemoryPeak"),
        "cpu_usage_nsec": _optional_nonnegative(
            fields["CPUUsageNSec"], "CPUUsageNSec"
        ),
    }


def read_private_json(path: Path) -> dict[str, object]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SoakError(f"open: {exc.strerror or exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SoakError("unsafe-type")
        if metadata.st_uid != os.getuid():
            raise SoakError("unsafe-owner")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise SoakError("unsafe-mode")
        if metadata.st_nlink != 1:
            raise SoakError("unsafe-links")
        if not 0 < metadata.st_size <= MAX_CACHE_BYTES:
            raise SoakError("unsafe-size")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, MAX_CACHE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_CACHE_BYTES:
                raise SoakError("unsafe-size")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(b"".join(chunks))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SoakError("invalid-json") from exc
    if not isinstance(payload, dict):
        raise SoakError("invalid-root")
    return payload


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_CLOEXEC
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class Accumulator:
    def __init__(self, stale_after_ms: int) -> None:
        self.stale_after_ms = stale_after_ms
        self.attempt_count = 0
        self.read_count = 0
        self.read_failures: Counter[str] = Counter()
        self.gpu_timestamps: set[int] = set()
        self.last_gpu_timestamp: int | None = None
        self.max_gpu_timestamp_gap_ms = 0
        self.gpu_timestamp_regressions = 0
        self.stale_sample_reads = 0
        self.max_sample_age_ms = 0
        self.display_timestamps: set[int] = set()
        self.display_status_counts: Counter[str] = Counter()
        self.refresh_pairs_hz: set[tuple[float, float]] = set()
        self.current_refresh_pair_hz: tuple[float, float] | None = None
        self.refresh_pair_transition_count = 0
        self.refresh_pair_transition_events: list[dict[str, object]] = []
        self.display_app_fps_nonnull_count = 0

    def failure(self, reason: str) -> None:
        self.attempt_count += 1
        self.read_failures[reason[:80]] += 1

    def observe(self, payload: dict[str, object], now_unix_ms: int) -> None:
        self.attempt_count += 1
        timestamp = payload.get("sample_unix_ms")
        if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp <= 0:
            self.read_failures["invalid-sample-timestamp"] += 1
            return
        self.read_count += 1
        self.gpu_timestamps.add(timestamp)
        if self.last_gpu_timestamp is not None:
            delta = timestamp - self.last_gpu_timestamp
            if delta < 0:
                self.gpu_timestamp_regressions += 1
            else:
                self.max_gpu_timestamp_gap_ms = max(
                    self.max_gpu_timestamp_gap_ms, delta
                )
        self.last_gpu_timestamp = timestamp
        age = now_unix_ms - timestamp
        if age < -5000:
            self.read_failures["future-sample-timestamp"] += 1
        else:
            self.max_sample_age_ms = max(self.max_sample_age_ms, max(0, age))
            if age > self.stale_after_ms:
                self.stale_sample_reads += 1

        display_timestamp = payload.get("display_sample_unix_ms")
        if isinstance(display_timestamp, int) and not isinstance(display_timestamp, bool):
            self.display_timestamps.add(display_timestamp)
        status = payload.get("display_status")
        self.display_status_counts[
            status if isinstance(status, str) and status else "invalid"
        ] += 1
        top = payload.get("display_dsi1_refresh_hz")
        bottom = payload.get("display_dsi2_refresh_hz")
        if (
            isinstance(top, (int, float))
            and not isinstance(top, bool)
            and isinstance(bottom, (int, float))
            and not isinstance(bottom, bool)
        ):
            pair = (round(float(top), 3), round(float(bottom), 3))
            self.refresh_pairs_hz.add(pair)
            if self.current_refresh_pair_hz is not None and pair != self.current_refresh_pair_hz:
                self.refresh_pair_transition_count += 1
                if len(self.refresh_pair_transition_events) < MAX_REFRESH_TRANSITION_EVENTS:
                    self.refresh_pair_transition_events.append(
                        {
                            "read_index": self.read_count,
                            "display_sample_unix_ms": display_timestamp
                            if isinstance(display_timestamp, int)
                            and not isinstance(display_timestamp, bool)
                            else None,
                            "from_hz": list(self.current_refresh_pair_hz),
                            "to_hz": list(pair),
                        }
                    )
            self.current_refresh_pair_hz = pair
        if payload.get("display_app_fps") is not None:
            self.display_app_fps_nonnull_count += 1

    def fields(self) -> dict[str, object]:
        return {
            "attempt_count": self.attempt_count,
            "read_count": self.read_count,
            "read_failures": dict(sorted(self.read_failures.items())),
            "distinct_gpu_timestamps": len(self.gpu_timestamps),
            "max_gpu_timestamp_gap_ms": self.max_gpu_timestamp_gap_ms,
            "gpu_timestamp_regressions": self.gpu_timestamp_regressions,
            "stale_sample_reads": self.stale_sample_reads,
            "max_sample_age_ms": self.max_sample_age_ms,
            "distinct_display_timestamps": len(self.display_timestamps),
            "display_status_counts": dict(sorted(self.display_status_counts.items())),
            "refresh_pairs_hz": [list(pair) for pair in sorted(self.refresh_pairs_hz)],
            "refresh_pair_transition_count": self.refresh_pair_transition_count,
            "refresh_pair_transition_events": self.refresh_pair_transition_events,
            "refresh_pair_transition_events_truncated": (
                self.refresh_pair_transition_count
                > len(self.refresh_pair_transition_events)
            ),
            "display_app_fps_nonnull_count": self.display_app_fps_nonnull_count,
        }


def _delta(
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    field: str,
) -> int | None:
    if before is None or after is None:
        return None
    first = before.get(field)
    last = after.get(field)
    if not isinstance(first, int) or isinstance(first, bool):
        return None
    if not isinstance(last, int) or isinstance(last, bool):
        return None
    return last - first


def make_report(
    accumulator: Accumulator,
    *,
    started_unix_ms: int,
    elapsed_s: float,
    requested_duration_s: float,
    interval_s: float,
    expected_display_interval_s: float,
    completion_reason: str,
    service_before: dict[str, object] | None,
    service_after: dict[str, object] | None,
    observer_cpu_s: float,
    max_gpu_gap_ms: int,
    max_service_cpu_percent: float,
    max_observer_cpu_percent: float,
    max_memory_growth_bytes: int,
    terminal_error: str | None = None,
) -> dict[str, object]:
    complete = completion_reason == "completed"
    expected_attempts = max(1, math.ceil(requested_duration_s / interval_s - 1e-9))
    expected_display_samples = max(
        1, math.floor(requested_duration_s / expected_display_interval_s) - 2
    )
    cpu_delta = _delta(service_before, service_after, "cpu_usage_nsec")
    memory_delta = _delta(service_before, service_after, "memory_current")
    restarts_delta = _delta(service_before, service_after, "n_restarts")
    service_cpu_percent = (
        max(0, cpu_delta) * 100.0 / (elapsed_s * 1_000_000_000)
        if cpu_delta is not None and elapsed_s > 0
        else None
    )
    observer_cpu_percent = (
        observer_cpu_s * 100.0 / elapsed_s if elapsed_s > 0 else None
    )
    before_pid = service_before.get("main_pid") if service_before else None
    after_pid = service_after.get("main_pid") if service_after else None
    fields = accumulator.fields()
    gates: dict[str, bool] = {
        "complete": complete,
        "attempt_coverage": accumulator.attempt_count >= math.floor(expected_attempts * 0.99),
        "read_failures_zero": not accumulator.read_failures,
        "gpu_timestamp_per_read": len(accumulator.gpu_timestamps) == accumulator.read_count,
        "gpu_timestamp_regressions_zero": accumulator.gpu_timestamp_regressions == 0,
        "stale_sample_reads_zero": accumulator.stale_sample_reads == 0,
        "gpu_gap_within_limit": accumulator.max_gpu_timestamp_gap_ms <= max_gpu_gap_ms,
        "display_status_ok_only": bool(accumulator.display_status_counts)
        and set(accumulator.display_status_counts) == {"ok"},
        "display_cadence": len(accumulator.display_timestamps) >= expected_display_samples,
        "one_refresh_pair": len(accumulator.refresh_pairs_hz) == 1,
        "app_fps_remains_null": accumulator.display_app_fps_nonnull_count == 0,
        "service_active": service_after is not None
        and service_after.get("active_state") == "active",
        "service_same_pid": isinstance(before_pid, int)
        and before_pid > 0
        and before_pid == after_pid,
        "service_no_restarts": restarts_delta == 0,
        "service_cpu_within_limit": service_cpu_percent is not None
        and service_cpu_percent <= max_service_cpu_percent,
        "observer_cpu_within_limit": observer_cpu_percent is not None
        and observer_cpu_percent <= max_observer_cpu_percent,
        "memory_growth_within_limit": memory_delta is not None
        and memory_delta <= max_memory_growth_bytes,
    }
    return {
        "schema": SCHEMA_VERSION,
        "read_only": True,
        "service": SERVICE,
        "started_unix_ms": started_unix_ms,
        "elapsed_s": round(elapsed_s, 3),
        "requested_duration_s": requested_duration_s,
        "interval_s": interval_s,
        "completion_reason": completion_reason,
        "terminal_error": terminal_error,
        "complete": complete,
        **fields,
        "expected_attempts": expected_attempts,
        "expected_display_samples_min": expected_display_samples,
        "service_before": service_before,
        "service_after": service_after,
        "service_cpu_percent_one_core": (
            round(service_cpu_percent, 4) if service_cpu_percent is not None else None
        ),
        "service_memory_current_growth_bytes": memory_delta,
        "service_restarts_delta": restarts_delta,
        "observer_cpu_percent_one_core": (
            round(observer_cpu_percent, 4) if observer_cpu_percent is not None else None
        ),
        "gates": gates,
        "accepted": all(gates.values()),
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=runtime_cache())
    parser.add_argument("--duration", type=float, default=86_400.0)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--checkpoint-interval", type=float, default=60.0)
    parser.add_argument("--expected-display-interval", type=float, default=60.0)
    parser.add_argument("--stale-after-ms", type=int, default=5_000)
    parser.add_argument("--max-gpu-gap-ms", type=int, default=5_000)
    parser.add_argument("--max-service-cpu-percent", type=float, default=0.5)
    parser.add_argument("--max-observer-cpu-percent", type=float, default=0.1)
    parser.add_argument("--max-memory-growth-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args(argv)
    if not 0.2 <= args.duration <= 604_800:
        parser.error("--duration must be between 0.2 and 604800 seconds")
    if not 0.1 <= args.interval <= 60:
        parser.error("--interval must be between 0.1 and 60 seconds")
    if not 0.1 <= args.checkpoint_interval <= 3600:
        parser.error("--checkpoint-interval must be between 0.1 and 3600 seconds")
    if not 15 <= args.expected_display_interval <= 3600:
        parser.error("--expected-display-interval must be between 15 and 3600 seconds")
    if not 1000 <= args.stale_after_ms <= 120_000:
        parser.error("--stale-after-ms must be between 1000 and 120000")
    if not 1000 <= args.max_gpu_gap_ms <= 120_000:
        parser.error("--max-gpu-gap-ms must be between 1000 and 120000")
    if not 0 < args.max_service_cpu_percent <= 100:
        parser.error("--max-service-cpu-percent must be in (0, 100]")
    if not 0 < args.max_observer_cpu_percent <= 100:
        parser.error("--max-observer-cpu-percent must be in (0, 100]")
    if not 0 <= args.max_memory_growth_bytes <= 1 << 30:
        parser.error("--max-memory-growth-bytes must be between 0 and 1 GiB")
    return args


def run(args: argparse.Namespace) -> int:
    global STOP_REQUESTED
    STOP_REQUESTED = False
    os.umask(0o077)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.output.exists():
        raise SoakError(f"output already exists: {args.output}")
    lock_path = args.output.with_name(f".{args.output.name}.lock")
    lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SoakError(f"another soak owns {lock_path}") from exc
        before = service_snapshot()
        if before.get("active_state") != "active" or not before.get("main_pid"):
            raise SoakError(f"{SERVICE} is not active before the soak")
        accumulator = Accumulator(args.stale_after_ms)
        started_unix_ms = int(time.time() * 1000)
        started_monotonic = time.monotonic()
        started_cpu = time.process_time()
        deadline = started_monotonic + args.duration
        next_read = started_monotonic
        next_checkpoint = started_monotonic + args.checkpoint_interval
        while not STOP_REQUESTED:
            now = time.monotonic()
            if now >= deadline:
                break
            if now < next_read:
                time.sleep(min(next_read - now, 0.2))
                continue
            try:
                accumulator.observe(read_private_json(args.cache), int(time.time() * 1000))
            except SoakError as exc:
                accumulator.failure(str(exc))
            now = time.monotonic()
            if now >= next_checkpoint:
                progress = make_report(
                    accumulator,
                    started_unix_ms=started_unix_ms,
                    elapsed_s=now - started_monotonic,
                    requested_duration_s=args.duration,
                    interval_s=args.interval,
                    expected_display_interval_s=args.expected_display_interval,
                    completion_reason="running",
                    service_before=before,
                    service_after=None,
                    observer_cpu_s=time.process_time() - started_cpu,
                    max_gpu_gap_ms=args.max_gpu_gap_ms,
                    max_service_cpu_percent=args.max_service_cpu_percent,
                    max_observer_cpu_percent=args.max_observer_cpu_percent,
                    max_memory_growth_bytes=args.max_memory_growth_bytes,
                )
                atomic_write_json(args.output, progress)
                next_checkpoint = now + args.checkpoint_interval
            next_read += args.interval
            if next_read <= now:
                next_read = now + args.interval
        completion_reason = "signal" if STOP_REQUESTED else "completed"
        terminal_error = None
        try:
            after = service_snapshot()
        except SoakError as exc:
            after = None
            completion_reason = "service-snapshot-error"
            terminal_error = str(exc)[:300]
        elapsed = time.monotonic() - started_monotonic
        report = make_report(
            accumulator,
            started_unix_ms=started_unix_ms,
            elapsed_s=elapsed,
            requested_duration_s=args.duration,
            interval_s=args.interval,
            expected_display_interval_s=args.expected_display_interval,
            completion_reason=completion_reason,
            service_before=before,
            service_after=after,
            observer_cpu_s=time.process_time() - started_cpu,
            max_gpu_gap_ms=args.max_gpu_gap_ms,
            max_service_cpu_percent=args.max_service_cpu_percent,
            max_observer_cpu_percent=args.max_observer_cpu_percent,
            max_memory_growth_bytes=args.max_memory_growth_bytes,
            terminal_error=terminal_error,
        )
        atomic_write_json(args.output, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        if completion_reason != "completed":
            return 4
        return 0 if report["accepted"] or not args.require_pass else 3
    finally:
        os.close(lock_descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        return run(args)
    except SoakError as exc:
        print(f"pds008-telemetry-soak: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
