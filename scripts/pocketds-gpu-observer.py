#!/usr/bin/env python3
"""Low-overhead, read-only Pocket DS GPU/DRM observation.

The MSM DRM driver exposes per-client accumulated engine time in fdinfo. This
tool deduplicates repeated file descriptors by DRM client id, takes deltas, and
writes JSON Lines suitable for comparing display/kernel configurations. It
does not change frequencies, display modes, services, or power state.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any


GPU_DEVFREQ = Path("/sys/devices/platform/soc@0/3d00000.gpu/devfreq/3d00000.gpu")
ENGINE_RE = re.compile(r"^(\d+)\s+ns$")
INTEGER_RE = re.compile(r"^(\d+)")
STOP_REQUESTED = False


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


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


def parse_fdinfo(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    text = read_text(path)
    if not text:
        return values
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.startswith("drm-"):
            values[key] = value.strip()
    return values


def numeric_prefix(value: str | None) -> int | None:
    if value is None:
        return None
    match = INTEGER_RE.match(value)
    return int(match.group(1)) if match else None


def process_directories(proc_root: Path = Path("/proc")) -> dict[str, Path]:
    try:
        with os.scandir(proc_root) as entries:
            return {
                entry.name: Path(entry.path)
                for entry in entries
                if entry.name.isdigit() and entry.is_dir(follow_symlinks=False)
            }
    except (FileNotFoundError, PermissionError, OSError):
        return {}


def discover_drm_fdinfo(
    proc_root: Path = Path("/proc"), processes: list[Path] | None = None
) -> list[Path]:
    """Find fdinfo files that currently expose MSM GPU engine counters."""
    selected: dict[tuple[str, int], tuple[int, Path]] = {}
    if processes is None:
        processes = list(process_directories(proc_root).values())
    for process in processes:
        try:
            descriptors = list((process / "fd").iterdir())
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
            if not target.startswith("/dev/dri/"):
                continue
            fdinfo = process / "fdinfo" / descriptor.name
            values = parse_fdinfo(fdinfo)
            driver = values.get("drm-driver")
            client_id = numeric_prefix(values.get("drm-client-id"))
            engine_match = ENGINE_RE.match(values.get("drm-engine-gpu", ""))
            if driver != "msm" or client_id is None or engine_match is None:
                continue
            key = (driver, client_id)
            engine_ns = int(engine_match.group(1))
            current = selected.get(key)
            if current is None or engine_ns > current[0]:
                selected[key] = (engine_ns, fdinfo)
    return [selected[key][1] for key in sorted(selected)]


def scan_drm_clients(
    fdinfo_paths: list[Path], process_names: dict[str, str] | None = None
) -> dict[tuple[str, int], dict[str, Any]]:
    """Return one record per DRM client, even when it has several open fds."""
    clients: dict[tuple[str, int], dict[str, Any]] = {}
    if process_names is None:
        process_names = {}
    for fdinfo in fdinfo_paths:
        process = fdinfo.parent.parent
        comm = process_names.get(process.name)
        if comm is None:
            comm = read_text(process / "comm") or "unknown"
            process_names[process.name] = comm
        values = parse_fdinfo(fdinfo)
        driver = values.get("drm-driver")
        client_id = numeric_prefix(values.get("drm-client-id"))
        engine_match = ENGINE_RE.match(values.get("drm-engine-gpu", ""))
        if driver != "msm" or client_id is None or engine_match is None:
            continue
        key = (driver, client_id)
        record = clients.setdefault(
            key,
            {
                "client_id": client_id,
                "processes": set(),
                "engine_ns": int(engine_match.group(1)),
                "cycles": numeric_prefix(values.get("drm-cycles-gpu")) or 0,
                "maxfreq_hz": numeric_prefix(values.get("drm-maxfreq-gpu")),
                "resident_kib": numeric_prefix(values.get("drm-resident-memory")),
            },
        )
        record["processes"].add(comm)
        # Duplicate fds for one client normally expose identical counters.
        # Keep the largest value if a process races with a fresh snapshot.
        record["engine_ns"] = max(record["engine_ns"], int(engine_match.group(1)))
        record["cycles"] = max(
            record["cycles"], numeric_prefix(values.get("drm-cycles-gpu")) or 0
        )
    return clients


def thermal_sources() -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
        name = read_text(zone / "type")
        if name and ("gpuss" in name.lower() or "gpu" in name.lower()):
            sources[name] = zone / "temp"
    return sources


def journal_counts(start_epoch: float, end_epoch: float) -> dict[str, int | None]:
    patterns = {
        "kwin_atomic_ebusy": "atomic commit failed",
        "gpu_gmu_timeout": "gmu",
        "gpu_hfi_error": "hfi",
        "gpu_hangcheck": "hangcheck",
        "fence_wait": "dma_fence_default_wait",
    }
    counts: dict[str, int | None] = {key: None for key in patterns}
    if not Path("/run/systemd/system").exists():
        return counts

    commands = (
        ["journalctl", "-b", "-k", "--no-pager"],
        ["journalctl", "--user", "-b", "--no-pager"],
    )
    text_parts: list[str] = []
    for command in commands:
        try:
            result = subprocess.run(
                command
                + [
                    "--since",
                    f"@{start_epoch:.3f}",
                    "--until",
                    f"@{end_epoch:.3f}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            text_parts.append(result.stdout.lower())
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    if not text_parts:
        return counts
    journal = "\n".join(text_parts)
    for key, needle in patterns.items():
        counts[key] = journal.count(needle)
    return counts


def serializable_clients(clients: dict[tuple[str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in sorted(clients):
        client = clients[key]
        result.append(
            {
                "client_id": client["client_id"],
                "processes": sorted(client["processes"]),
                "engine_ns": client["engine_ns"],
                "cycles": client["cycles"],
                "maxfreq_hz": client["maxfreq_hz"],
                "resident_kib": client["resident_kib"],
            }
        )
    return result


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def default_output() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return repo_root / "test-results" / f"gpu-drm-observation-{stamp}.jsonl"


def observe(
    duration: float,
    interval: float,
    process_rescan_interval: float,
    rescan_interval: float,
    output: Path,
) -> dict[str, Any]:
    os.umask(0o077)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    start_epoch = time.time()
    start_monotonic = time.monotonic_ns()
    start_cpu = time.process_time()
    thermals = thermal_sources()
    previous_clients: dict[tuple[str, int], dict[str, Any]] | None = None
    previous_monotonic: int | None = None
    sample_count = 0
    peak_observed_percent: float | None = None
    peak_client_count = 0
    fdinfo_paths: list[Path] = []
    known_processes: set[str] = set()
    process_names: dict[str, str] = {}
    next_process_rescan = 0.0
    next_rescan = 0.0
    rescan_count = 0
    new_process_scan_count = 0

    with output.open("x", encoding="utf-8") as stream:
        header = {
            "type": "header",
            "schema": 1,
            "started_at": utc_now(),
            "duration_s": duration,
            "interval_s": interval,
            "process_rescan_interval_s": process_rescan_interval,
            "rescan_interval_s": rescan_interval,
            "kernel": os.uname().release if hasattr(os, "uname") else "unknown",
            "gpu_governor": read_text(GPU_DEVFREQ / "governor"),
            "gpu_available_frequencies_hz": read_text(
                GPU_DEVFREQ / "available_frequencies"
            ),
            "read_only": True,
        }
        stream.write(json.dumps(header, ensure_ascii=False) + "\n")
        stream.flush()

        deadline = time.monotonic() + duration
        next_sample = time.monotonic()
        while not STOP_REQUESTED:
            now = time.monotonic()
            if sample_count and now > deadline:
                break
            if now < next_sample:
                time.sleep(min(next_sample - now, 0.1))
                continue

            monotonic_ns = time.monotonic_ns()
            if now >= next_process_rescan:
                current_processes = process_directories()
                current_pids = set(current_processes)
                if now >= next_rescan:
                    fdinfo_paths = discover_drm_fdinfo(processes=list(current_processes.values()))
                    next_rescan = now + rescan_interval
                    rescan_count += 1
                else:
                    new_pids = current_pids - known_processes
                    if new_pids:
                        fdinfo_paths.extend(
                            discover_drm_fdinfo(
                                processes=[current_processes[pid] for pid in sorted(new_pids)]
                            )
                        )
                        fdinfo_paths = list(dict.fromkeys(fdinfo_paths))
                        new_process_scan_count += 1
                fdinfo_paths = [
                    path for path in fdinfo_paths if path.parent.parent.name in current_pids
                ]
                known_processes = current_pids
                process_names = {
                    pid: name for pid, name in process_names.items() if pid in current_pids
                }
                next_process_rescan = now + process_rescan_interval
            clients = scan_drm_clients(fdinfo_paths, process_names)
            elapsed_ns = (
                monotonic_ns - previous_monotonic
                if previous_monotonic is not None
                else None
            )
            engine_delta = 0
            cycles_delta = 0
            if previous_clients is not None and elapsed_ns and elapsed_ns > 0:
                for key, client in clients.items():
                    old = previous_clients.get(key)
                    if old is None:
                        continue
                    engine_delta += max(0, client["engine_ns"] - old["engine_ns"])
                    cycles_delta += max(0, client["cycles"] - old["cycles"])
                observed_percent: float | None = round(engine_delta * 100.0 / elapsed_ns, 3)
            else:
                observed_percent = None

            temperatures = {
                name: round(value / 1000.0, 1)
                for name, path in thermals.items()
                if (value := read_int(path)) is not None
            }
            sample = {
                "type": "sample",
                "timestamp": utc_now(),
                "elapsed_s": round((monotonic_ns - start_monotonic) / 1e9, 3),
                "gpu_freq_hz": read_int(GPU_DEVFREQ / "cur_freq"),
                "gpu_temperatures_c": temperatures,
                "observed_gpu_percent": observed_percent,
                "engine_delta_ns": engine_delta if previous_clients is not None else None,
                "cycles_delta": cycles_delta if previous_clients is not None else None,
                "client_count": len(clients),
                "clients": serializable_clients(clients),
            }
            stream.write(json.dumps(sample, ensure_ascii=False) + "\n")
            stream.flush()
            sample_count += 1
            peak_client_count = max(peak_client_count, len(clients))
            if observed_percent is not None:
                peak_observed_percent = max(peak_observed_percent or 0.0, observed_percent)
            previous_clients = clients
            previous_monotonic = monotonic_ns
            next_sample += interval
            if next_sample <= time.monotonic():
                next_sample = time.monotonic() + interval

        end_epoch = time.time()
        wall_s = max(time.monotonic_ns() - start_monotonic, 1) / 1e9
        cpu_s = time.process_time() - start_cpu
        summary = {
            "type": "summary",
            "ended_at": utc_now(),
            "wall_s": round(wall_s, 3),
            "sample_count": sample_count,
            "peak_client_count": peak_client_count,
            "peak_observed_gpu_percent": peak_observed_percent,
            "fdinfo_rescan_count": rescan_count,
            "new_process_scan_count": new_process_scan_count,
            "observer_cpu_percent": round(cpu_s * 100.0 / wall_s, 3),
            "journal_counts": journal_counts(start_epoch, end_epoch),
            "interrupted": STOP_REQUESTED,
        }
        stream.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=1800.0, help="seconds (0.2–86400)")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds (0.2–60)")
    parser.add_argument(
        "--process-rescan-interval",
        type=float,
        default=10.0,
        help="seconds between checks for newly started DRM clients (2–60)",
    )
    parser.add_argument(
        "--rescan-interval",
        type=float,
        default=300.0,
        help="seconds between full /proc fd discovery passes (5–3600)",
    )
    parser.add_argument("--output", type=Path, default=None, help="JSONL output path")
    args = parser.parse_args(argv)
    if not 0.2 <= args.duration <= 86400:
        parser.error("--duration must be between 0.2 and 86400 seconds")
    if not 0.2 <= args.interval <= 60:
        parser.error("--interval must be between 0.2 and 60 seconds")
    if not 2 <= args.process_rescan_interval <= 60:
        parser.error("--process-rescan-interval must be between 2 and 60 seconds")
    if not 5 <= args.rescan_interval <= 3600:
        parser.error("--rescan-interval must be between 5 and 3600 seconds")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    output = args.output or default_output()
    try:
        summary = observe(
            args.duration,
            args.interval,
            args.process_rescan_interval,
            args.rescan_interval,
            output,
        )
    except FileExistsError:
        print(f"output already exists: {output}", file=sys.stderr)
        return 2
    print(f"GPU/DRM observation: {output}")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
