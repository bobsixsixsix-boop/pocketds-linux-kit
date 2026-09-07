#!/usr/bin/env python3
"""Validate the private live Codex quota cache without exposing account data."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any, Sequence


FIELDS = {
    "available",
    "fresh",
    "source",
    "synced_at",
    "source_timestamp",
    "limit_id",
    "limit_name",
    "plan_type",
    "primary_used_percent",
    "primary_reset",
    "primary_window_minutes",
    "secondary_used_percent",
    "secondary_reset",
    "secondary_window_minutes",
}
MAX_BYTES = 64 * 1024


class QuotaError(RuntimeError):
    """The quota cache was absent, unsafe, stale or not live."""


def reject_constant(value: str) -> None:
    raise QuotaError(f"non-finite JSON constant: {value}")


def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise QuotaError("quota cache contains a duplicate key")
        output[key] = value
    return output


def read_private(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise QuotaError("quota cache is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= MAX_BYTES
        ):
            raise QuotaError("quota cache identity or mode is unsafe")
        content = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise QuotaError("quota cache was truncated")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise QuotaError("quota cache grew while reading")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(
            content,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuotaError("quota cache is invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise QuotaError("quota cache schema is invalid")
    return payload


def percent(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QuotaError("quota percentage has an invalid type")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise QuotaError("quota percentage is outside 0..100")
    return number


def validate(payload: dict[str, Any], *, now: int, maximum_age: int) -> dict[str, Any]:
    if payload["available"] is not True or payload["fresh"] is not True:
        raise QuotaError("quota cache is not fresh and available")
    if payload["source"] != "app-server":
        raise QuotaError("quota cache is not backed by the live Codex app-server")
    synced_at = payload["synced_at"]
    if isinstance(synced_at, bool) or not isinstance(synced_at, int):
        raise QuotaError("quota sync time is invalid")
    age = now - synced_at
    if age < 0 or age > maximum_age:
        raise QuotaError("quota cache age is outside the acceptance window")
    primary = percent(payload["primary_used_percent"])
    secondary = percent(payload["secondary_used_percent"])
    if primary is None and secondary is None:
        raise QuotaError("quota cache has no usable rate-limit window")
    for key in ("limit_id", "limit_name", "plan_type"):
        if not isinstance(payload[key], str):
            raise QuotaError(f"quota field {key} has an invalid type")
    return {
        "accepted": True,
        "source": "app-server",
        "fresh": True,
        "age_seconds": age,
        "primary_present": primary is not None,
        "secondary_present": secondary is not None,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        / "pocketds-codex-quota.json",
    )
    parser.add_argument("--maximum-age", type=int, default=360)
    parser.add_argument("--now", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if not 1 <= arguments.maximum_age <= 900:
            raise QuotaError("maximum age is outside 1..900 seconds")
        report = validate(
            read_private(arguments.cache),
            now=int(time.time()) if arguments.now is None else arguments.now,
            maximum_age=arguments.maximum_age,
        )
    except (OSError, QuotaError, ValueError) as exc:
        print(f"Codex quota verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
