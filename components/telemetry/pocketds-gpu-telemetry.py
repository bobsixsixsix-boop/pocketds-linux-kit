#!/usr/bin/env python3
"""Publish low-overhead Pocket DS GPU telemetry for the local desktop session.

The utilization value is the sum of observable MSM DRM client engine-time
deltas.  It is not presented as a hidden/global hardware counter.  Missing or
stale data is written as JSON null instead of being fabricated as zero.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from types import ModuleType
from typing import Any, Callable


STOP_REQUESTED = False
STOP_EVENT = threading.Event()
STANDBY_INTERVAL_S = 5.0
BACKLIGHT_POWER_PATHS = (
    Path("/sys/class/backlight/ae94000.dsi.0/bl_power"),
    Path("/sys/class/backlight/sy7758-backlight/bl_power"),
)

DISPLAY_FIELDS = (
    "display_session",
    "display_backend",
    "display_compositor",
    "display_renderer",
    "display_gl_vendor",
    "display_gl_platform",
    "display_dsi1_enabled",
    "display_dsi1_physical_width_px",
    "display_dsi1_physical_height_px",
    "display_dsi1_physical_width_mm",
    "display_dsi1_physical_height_mm",
    "display_dsi1_logical_x",
    "display_dsi1_logical_y",
    "display_dsi1_logical_width",
    "display_dsi1_logical_height",
    "display_dsi1_scale",
    "display_dsi1_refresh_hz",
    "display_dsi2_enabled",
    "display_dsi2_physical_width_px",
    "display_dsi2_physical_height_px",
    "display_dsi2_physical_width_mm",
    "display_dsi2_physical_height_mm",
    "display_dsi2_logical_x",
    "display_dsi2_logical_y",
    "display_dsi2_logical_width",
    "display_dsi2_logical_height",
    "display_dsi2_scale",
    "display_dsi2_refresh_hz",
)


def displays_powered() -> bool:
    """Fail active when DPMS state cannot be read."""
    try:
        states = [int(path.read_text(encoding="utf-8").strip()) for path in BACKLIGHT_POWER_PATHS]
    except (OSError, UnicodeError, ValueError):
        return True
    return any(state == 0 for state in states)


def empty_display_payload(status: str = "unavailable") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "display_status": status,
        "display_source": "kwin-supportInformation",
        "display_semantics": "physical-output-refresh-not-app-fps",
        "display_physical_mode_semantics": "logical-size-times-scale-rounded",
        "display_sample_unix_ms": None,
        "display_app_fps": None,
        "display_app_fps_status": "unavailable",
    }
    payload.update(dict.fromkeys(DISPLAY_FIELDS))
    return payload


def _line_value(text: str, label: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(label)}:[ \t]*(.+?)\s*$", text)
    if match is None:
        return None
    value = match.group(1).strip()
    return value[:160] if value else None


def _bounded_int(value: str | None, low: int, high: int) -> int | None:
    try:
        parsed = int(value or "")
    except ValueError:
        return None
    return parsed if low <= parsed <= high else None


def _bounded_float(value: str | None, low: float, high: float) -> float | None:
    try:
        parsed = float(value or "")
    except ValueError:
        return None
    return parsed if low <= parsed <= high else None


def _screen_payload(block: str, prefix: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    enabled = _line_value(block, "Enabled")
    values[f"{prefix}_enabled"] = (
        True if enabled == "1" else False if enabled == "0" else None
    )

    physical = re.fullmatch(r"(\d+)x(\d+)mm", _line_value(block, "Physical size") or "")
    values[f"{prefix}_physical_width_mm"] = (
        _bounded_int(physical.group(1), 1, 2000) if physical else None
    )
    values[f"{prefix}_physical_height_mm"] = (
        _bounded_int(physical.group(2), 1, 2000) if physical else None
    )

    geometry = re.fullmatch(
        r"(-?\d+),(-?\d+),(\d+)x(\d+)", _line_value(block, "Geometry") or ""
    )
    geometry_fields = ("x", "y", "width", "height")
    geometry_bounds = ((-32768, 32768), (-32768, 32768), (1, 16384), (1, 16384))
    logical: dict[str, int | None] = {}
    for index, (name, bounds) in enumerate(zip(geometry_fields, geometry_bounds), 1):
        value = _bounded_int(geometry.group(index), *bounds) if geometry else None
        logical[name] = value
        values[f"{prefix}_logical_{name}"] = (
            value
        )

    scale = _bounded_float(
        _line_value(block, "Scale"), 0.25, 8.0
    )
    values[f"{prefix}_scale"] = scale
    physical_width = (
        round(logical["width"] * scale)
        if logical["width"] is not None and scale is not None else None
    )
    physical_height = (
        round(logical["height"] * scale)
        if logical["height"] is not None and scale is not None else None
    )
    values[f"{prefix}_physical_width_px"] = (
        physical_width if physical_width is not None and 1 <= physical_width <= 32768 else None
    )
    values[f"{prefix}_physical_height_px"] = (
        physical_height if physical_height is not None and 1 <= physical_height <= 32768 else None
    )
    refresh_millihz = _bounded_int(
        _line_value(block, "Refresh Rate"), 1000, 1000000
    )
    values[f"{prefix}_refresh_hz"] = (
        round(refresh_millihz / 1000.0, 3) if refresh_millihz is not None else None
    )
    return values


def parse_kwin_support_information(text: str) -> dict[str, Any]:
    """Flatten stable KWin supportInformation fields without inventing FPS."""
    payload = empty_display_payload("partial")
    payload["display_session"] = _line_value(text, "Operation Mode")
    backend = re.search(
        r"(?ms)^LogicalOutput backend\s*\n=+\s*\nName:[ \t]*(.+?)\s*$", text
    )
    payload["display_backend"] = backend.group(1).strip()[:160] if backend else None
    payload["display_compositor"] = _line_value(text, "Compositing Type")
    payload["display_renderer"] = _line_value(text, "OpenGL renderer string")
    payload["display_gl_vendor"] = _line_value(text, "OpenGL vendor string")
    payload["display_gl_platform"] = _line_value(text, "OpenGL platform interface")

    blocks = re.finditer(
        r"(?ms)^Screen \d+:\s*\n-+\s*\n(.*?)(?=^Screen \d+:|^Compositing\s*\n=+|\Z)",
        text,
    )
    for match in blocks:
        block = match.group(1)
        name = _line_value(block, "Name")
        if name == "DSI-1":
            payload.update(_screen_payload(block, "display_dsi1"))
        elif name == "DSI-2":
            payload.update(_screen_payload(block, "display_dsi2"))

    required = (
        "display_session",
        "display_backend",
        "display_compositor",
        "display_renderer",
        "display_dsi1_enabled",
        "display_dsi1_physical_width_px",
        "display_dsi1_physical_height_px",
        "display_dsi1_logical_width",
        "display_dsi1_logical_height",
        "display_dsi1_scale",
        "display_dsi1_refresh_hz",
        "display_dsi2_enabled",
        "display_dsi2_physical_width_px",
        "display_dsi2_physical_height_px",
        "display_dsi2_logical_width",
        "display_dsi2_logical_height",
        "display_dsi2_scale",
        "display_dsi2_refresh_hz",
    )
    if all(payload[key] is not None for key in required):
        payload["display_status"] = "ok"
    return payload


def decode_busctl_reply(stdout: str) -> str:
    if len(stdout) > 262144:
        raise ValueError("supportInformation reply is too large")
    reply = json.loads(stdout)
    if reply.get("type") != "s" or not isinstance(reply.get("data"), list):
        raise ValueError("unexpected busctl reply signature")
    data = reply["data"]
    if len(data) != 1 or not isinstance(data[0], str):
        raise ValueError("unexpected supportInformation payload")
    return data[0]


def query_kwin_display() -> dict[str, Any]:
    """Read KWin on the user bus; every failure becomes an unavailable snapshot."""
    payload = empty_display_payload()
    payload["display_sample_unix_ms"] = int(time.time() * 1000)
    try:
        result = subprocess.run(
            [
                "/usr/bin/busctl",
                "--user",
                "--json=short",
                "--timeout=2s",
                "call",
                "org.kde.KWin",
                "/KWin",
                "org.kde.KWin",
                "supportInformation",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )
        if result.returncode != 0:
            return payload
        parsed = parse_kwin_support_information(decode_busctl_reply(result.stdout))
        parsed["display_sample_unix_ms"] = payload["display_sample_unix_ms"]
        return parsed
    except (
        FileNotFoundError,
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return payload


class KWinDisplaySampler:
    """Poll KWin on a background thread so the two-second GPU loop never waits."""

    def __init__(
        self,
        interval: float = 60.0,
        query: Callable[[], dict[str, Any]] = query_kwin_display,
    ) -> None:
        self.interval = interval
        self.query = query
        self._payload = empty_display_payload()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._updated = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._worker, name="pocketds-kwin-display", daemon=True
        )
        self._thread.start()

    def _worker(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                payload = self.query()
            except Exception:
                payload = empty_display_payload()
                payload["display_sample_unix_ms"] = int(time.time() * 1000)
            with self._lock:
                self._payload = payload
            self._updated.set()
            remaining = max(0.0, self.interval - (time.monotonic() - started))
            if self._stop.wait(remaining):
                break

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._payload)

    def wait_for_update(self, timeout: float) -> bool:
        return self._updated.wait(timeout)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)


def load_observer() -> ModuleType:
    override = os.environ.get("POCKETDS_GPU_OBSERVER")
    here = Path(__file__).resolve().parent
    candidates = [
        Path(override) if override else None,
        here / "pocketds-gpu-observer.py",
        here.parent.parent / "scripts" / "pocketds-gpu-observer.py",
    ]
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("pocketds_gpu_observer", candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("pocketds-gpu-observer.py was not found")


def default_output() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return runtime / "pocketds-gpu-status.json"


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    STOP_EVENT.set()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


class GpuSampler:
    def __init__(
        self,
        observer: ModuleType,
        process_rescan_interval: float = 10.0,
        full_rescan_interval: float = 300.0,
        late_client_retry_window: float = 180.0,
    ) -> None:
        self.observer = observer
        self.process_rescan_interval = process_rescan_interval
        self.full_rescan_interval = full_rescan_interval
        self.late_client_retry_window = late_client_retry_window
        self.thermals = observer.thermal_sources()
        self.fdinfo_paths: list[Path] = []
        self.known_processes: set[str] = set()
        self.retry_processes_until: dict[str, float] = {}
        self.process_names: dict[str, str] = {}
        self.previous_clients: dict[tuple[str, int], dict[str, Any]] | None = None
        self.previous_monotonic_ns: int | None = None
        self.next_process_rescan = 0.0
        self.next_full_rescan = 0.0

    def _refresh_paths(self, now: float) -> None:
        if now < self.next_process_rescan:
            return
        processes = self.observer.process_directories()
        current_pids = set(processes)
        # FEX and other launchers may exist long before opening their first DRM fd.
        # Retry only recent PIDs so the five-minute full scan can stay infrequent.
        new_pids = current_pids - self.known_processes
        for pid in new_pids:
            self.retry_processes_until[pid] = now + self.late_client_retry_window
        self.retry_processes_until = {
            pid: deadline
            for pid, deadline in self.retry_processes_until.items()
            if pid in current_pids and now <= deadline
        }

        if now >= self.next_full_rescan:
            self.fdinfo_paths = self.observer.discover_drm_fdinfo(
                processes=list(processes.values())
            )
            self.next_full_rescan = now + self.full_rescan_interval
        else:
            retry_pids = sorted(self.retry_processes_until)
            if retry_pids:
                self.fdinfo_paths.extend(
                    self.observer.discover_drm_fdinfo(
                        processes=[processes[pid] for pid in retry_pids]
                    )
                )
                # A retry may rediscover the same client through the same fd.
                self.fdinfo_paths = list(dict.fromkeys(self.fdinfo_paths))
        self.fdinfo_paths = [
            path for path in self.fdinfo_paths if path.parent.parent.name in current_pids
        ]
        self.known_processes = current_pids
        self.process_names = {
            pid: name for pid, name in self.process_names.items() if pid in current_pids
        }
        self.next_process_rescan = now + self.process_rescan_interval

    def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        monotonic_ns = time.monotonic_ns()
        self._refresh_paths(now)
        clients = self.observer.scan_drm_clients(self.fdinfo_paths, self.process_names)

        percent: float | None = None
        if self.previous_clients is not None and self.previous_monotonic_ns is not None:
            elapsed_ns = monotonic_ns - self.previous_monotonic_ns
            if elapsed_ns > 0:
                engine_delta = 0
                for key, client in clients.items():
                    previous = self.previous_clients.get(key)
                    if previous is not None:
                        engine_delta += max(0, client["engine_ns"] - previous["engine_ns"])
                percent = round(min(100.0, engine_delta * 100.0 / elapsed_ns), 2)

        temperatures = [
            value / 1000.0
            for path in self.thermals.values()
            if (value := self.observer.read_int(path)) is not None
        ]
        payload = {
            "schema": 1,
            "source": "msm-drm-fdinfo",
            "semantics": "observable-drm-client-load",
            "sample_unix_ms": int(time.time() * 1000),
            "gpu_percent": percent,
            "gpu_freq_hz": self.observer.read_int(
                self.observer.GPU_DEVFREQ / "cur_freq"
            ),
            "gpu_temp_c": round(max(temperatures), 1) if temperatures else None,
            "gpu_clients": len(clients),
        }
        self.previous_clients = clients
        self.previous_monotonic_ns = monotonic_ns
        return payload


def run(output: Path, interval: float, display_interval: float, once: bool) -> int:
    os.umask(0o077)
    sampler = GpuSampler(load_observer())
    display = KWinDisplaySampler(display_interval)
    if not once:
        display.start()
    next_sample = time.monotonic()
    try:
        while not STOP_REQUESTED:
            now = time.monotonic()
            if now < next_sample:
                if STOP_EVENT.wait(next_sample - now):
                    break
            payload = sampler.sample()
            payload.update(display.snapshot())
            atomic_write_json(output, payload)
            if once:
                break
            wait_interval = interval if displays_powered() else max(interval, STANDBY_INTERVAL_S)
            next_sample = time.monotonic() + wait_interval
    finally:
        display.close()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=default_output())
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--display-interval", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not 0.5 <= args.interval <= 60:
        parser.error("--interval must be between 0.5 and 60 seconds")
    if not 15 <= args.display_interval <= 3600:
        parser.error("--display-interval must be between 15 and 3600 seconds")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    return run(args.output, args.interval, args.display_interval, args.once)


if __name__ == "__main__":
    raise SystemExit(main())
