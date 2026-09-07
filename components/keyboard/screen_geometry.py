#!/usr/bin/python3
"""Resolve the current Pocket DS lower-screen logical geometry safely."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import time


MAX_SAMPLE_AGE_MS = 120_000
MAX_CLOCK_SKEW_MS = 5_000
MAX_CACHE_BYTES = 262_144


@dataclass(frozen=True)
class ScreenGeometry:
    x: int
    y: int
    width: int
    height: int


# Verified on the current Pocket DS layout. This is only the fail-safe path;
# fresh KWin telemetry remains authoritative whenever it is available.
FALLBACK_LOWER_SCREEN = ScreenGeometry(283, 720, 819, 614)


def _strict_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("geometry value is not numeric")
    integer = int(value)
    if integer != value:
        raise ValueError("geometry value is not integral")
    return integer


def geometry_from_payload(
    payload: object,
    *,
    now_ms: int,
) -> ScreenGeometry:
    if not isinstance(payload, dict):
        raise ValueError("telemetry root is not an object")
    if payload.get("display_status") != "ok":
        raise ValueError("display telemetry is not complete")
    if payload.get("display_dsi2_enabled") is not True:
        raise ValueError("DSI-2 is not enabled")

    sample_ms = _strict_int(payload.get("display_sample_unix_ms"))
    age_ms = now_ms - sample_ms
    if age_ms < -MAX_CLOCK_SKEW_MS or age_ms > MAX_SAMPLE_AGE_MS:
        raise ValueError("display telemetry is stale")

    geometry = ScreenGeometry(
        x=_strict_int(payload.get("display_dsi2_logical_x")),
        y=_strict_int(payload.get("display_dsi2_logical_y")),
        width=_strict_int(payload.get("display_dsi2_logical_width")),
        height=_strict_int(payload.get("display_dsi2_logical_height")),
    )
    if not -16_384 <= geometry.x <= 16_384:
        raise ValueError("DSI-2 x coordinate is out of range")
    if not -16_384 <= geometry.y <= 16_384:
        raise ValueError("DSI-2 y coordinate is out of range")
    if not 320 <= geometry.width <= 4_096:
        raise ValueError("DSI-2 width is out of range")
    if not 240 <= geometry.height <= 4_096:
        raise ValueError("DSI-2 height is out of range")
    return geometry


def read_private_payload(path: Path, *, expected_uid: int | None = None) -> object:
    """Read one bounded owner-private cache snapshot without following links."""

    owner = os.getuid() if expected_uid is None else expected_uid
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= MAX_CACHE_BYTES
        ):
            raise ValueError("display telemetry cache is unsafe")
        remaining = metadata.st_size
        payload = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise ValueError("display telemetry cache changed while reading")
            payload.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ValueError("display telemetry cache grew while reading")
    finally:
        os.close(descriptor)
    try:
        return json.loads(bytes(payload).decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("display telemetry cache is invalid") from exc


def load_lower_screen_geometry(
    *,
    runtime_dir: Path | None = None,
    now_ms: int | None = None,
) -> ScreenGeometry:
    """Return fresh DSI-2 geometry, or the verified fail-safe geometry."""

    runtime = runtime_dir or Path(
        os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    )
    sample_time = int(time.time() * 1_000) if now_ms is None else now_ms
    try:
        payload = read_private_payload(runtime / "pocketds-gpu-status.json")
        return geometry_from_payload(payload, now_ms=sample_time)
    except (OSError, TypeError, ValueError):
        return FALLBACK_LOWER_SCREEN
