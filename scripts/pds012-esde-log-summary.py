#!/usr/bin/env python3
"""Summarize an ES-DE log without emitting paths, ROM names or raw lines."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys


SCHEMA = "pocketds.esde-log-summary.v1"
MAX_LOG_BYTES = 8 * 1024 * 1024
SHARED_ROOT_CONFIG = Path.home() / ".config/pocketds-linux-kit/shared-library-root"
EXPECTED_SHARED_ROOT = Path("/mnt/pocketds-games")
LEVEL = re.compile(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|FATAL)\b", re.IGNORECASE)
STARTUP = re.compile(r"Application startup time:\s*([0-9]+)\s*ms", re.IGNORECASE)
EVENTS = {
    "clean_shutdowns": re.compile(r"cleanly shutting down", re.IGNORECASE),
    "gamelist_events": re.compile(r"gamelist", re.IGNORECASE),
    "missing_reference_events": re.compile(
        r"missing|does not exist|couldn.t find", re.IGNORECASE
    ),
    "parse_failures": re.compile(
        r"couldn.t parse|parse error|invalid.*xml", re.IGNORECASE
    ),
    "rom_scan_events": re.compile(
        r"scan|searching.*game|game files", re.IGNORECASE
    ),
    "system_load_events": re.compile(
        r"load(?:ing|ed).*system|system.*load", re.IGNORECASE
    ),
}


def default_log_path(
    config: Path = SHARED_ROOT_CONFIG,
    expected_root: Path = EXPECTED_SHARED_ROOT,
) -> Path:
    """Select the active ES-DE home without trusting arbitrary config paths."""

    if not config.exists() and not config.is_symlink():
        return Path.home() / "ES-DE/logs/es_log.txt"
    try:
        metadata = config.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > 4096:
            raise RuntimeError("shared library configuration is unsafe")
        lines = config.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("shared library configuration is unreadable") from exc
    if lines != [str(expected_root)]:
        raise RuntimeError("shared library configuration is not the managed root")
    return expected_root / "PocketDS/Frontends/ES-DE/logs/es_log.txt"


def read_log(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("ES-DE log is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_LOG_BYTES
        ):
            raise RuntimeError("ES-DE log is unsafe or oversized")
        remaining = metadata.st_size
        data = bytearray()
        while remaining:
            block = os.read(descriptor, min(65536, remaining))
            if not block:
                raise RuntimeError("ES-DE log changed while reading")
            data.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise RuntimeError("ES-DE log grew while reading")
        try:
            return bytes(data).decode("utf-8", errors="strict"), metadata.st_size
        except UnicodeDecodeError as exc:
            raise RuntimeError("ES-DE log is not UTF-8") from exc
    finally:
        os.close(descriptor)


def summarize(text: str, byte_count: int) -> dict[str, object]:
    lines = text.splitlines()
    levels = {name: 0 for name in ("debug", "info", "warning", "error", "fatal")}
    for line in lines:
        match = LEVEL.search(line)
        if not match:
            continue
        level = match.group(1).lower()
        levels["warning" if level == "warn" else level] += 1
    event_counts = {
        name: sum(bool(pattern.search(line)) for line in lines)
        for name, pattern in EVENTS.items()
    }
    startup_times = [int(match.group(1)) for match in STARTUP.finditer(text)]
    summary_has_errors = bool(
        levels["error"] or levels["fatal"] or event_counts["parse_failures"]
    )
    return {
        "schema": SCHEMA,
        "privacy": {
            "paths_emitted": False,
            "raw_lines_emitted": False,
            "rom_names_emitted": False,
        },
        "bytes": byte_count,
        "lines": len(lines),
        "level_counts": levels,
        "event_counts": event_counts,
        "startup": {
            "samples": len(startup_times),
            "minimum_ms": min(startup_times) if startup_times else None,
            "maximum_ms": max(startup_times) if startup_times else None,
            "latest_ms": startup_times[-1] if startup_times else None,
        },
        "complete_session_observed": bool(
            startup_times and event_counts["clean_shutdowns"]
        ),
        "summary_has_errors": summary_has_errors,
    }


def write_report(report: dict[str, object], destination: str) -> None:
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if destination == "-":
        sys.stdout.buffer.write(payload)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short ES-DE summary write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--output", default="-", help="new JSON path or - for stdout")
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="require at least one complete session and no error/parse event",
    )
    arguments = parser.parse_args()
    try:
        log_path = default_log_path() if arguments.log is None else arguments.log
        text, byte_count = read_log(log_path)
        report = summarize(text, byte_count)
        write_report(report, arguments.output)
    except (OSError, RuntimeError) as exc:
        print(f"ES-DE log summary failed: {exc}", file=sys.stderr)
        return 2
    if arguments.require_clean and (
        not report["complete_session_observed"] or report["summary_has_errors"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
