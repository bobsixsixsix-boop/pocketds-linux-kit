#!/usr/bin/env python3
"""Metadata-only ROM tree preflight for PDS-012 diagnostics.

This intentionally models the filesystem walk, not ES-DE's full parser.  It
never opens regular files and never follows symbolic links.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Result:
    directories: int = 0
    regular_files: int = 0
    candidate_roms: int = 0
    candidate_extensions: dict[str, int] = field(default_factory=dict)
    symlinks: int = 0
    special_files: int = 0
    directory_errors: int = 0
    entry_errors: int = 0
    depth_limited_directories: int = 0
    entries: int = 0
    max_depth_seen: int = 0
    elapsed_seconds: float = 0.0
    stopped: str = "complete"


def scan(
    root: Path,
    extensions: set[str],
    *,
    timeout_seconds: float = 5.0,
    max_entries: int = 250_000,
    max_depth: int = 64,
    delay_per_entry: float = 0.0,
) -> Result:
    root = root.expanduser().absolute()
    root_mode = root.lstat().st_mode
    if stat.S_ISLNK(root_mode):
        raise ValueError("ROM root must not be a symbolic link")
    if not stat.S_ISDIR(root_mode):
        raise ValueError("ROM root must be a directory")
    extensions = {item.lower().lstrip(".") for item in extensions if item}
    started = time.monotonic()
    deadline = started + timeout_seconds
    result = Result()
    stack: list[tuple[Path, int]] = [(root, 0)]

    while stack:
        directory, depth = stack.pop()
        if time.monotonic() >= deadline:
            result.stopped = "timeout"
            break
        result.directories += 1
        result.max_depth_seen = max(result.max_depth_seen, depth)
        try:
            iterator = os.scandir(directory)
        except OSError:
            result.directory_errors += 1
            continue

        with iterator:
            for entry in iterator:
                if time.monotonic() >= deadline:
                    result.stopped = "timeout"
                    stack.clear()
                    break
                if result.entries >= max_entries:
                    result.stopped = "entry_limit"
                    stack.clear()
                    break
                if delay_per_entry:
                    time.sleep(delay_per_entry)

                result.entries += 1
                try:
                    mode = entry.stat(follow_symlinks=False).st_mode
                except OSError:
                    result.entry_errors += 1
                    continue
                if stat.S_ISLNK(mode):
                    result.symlinks += 1
                elif stat.S_ISDIR(mode):
                    if depth >= max_depth:
                        result.depth_limited_directories += 1
                    else:
                        stack.append((Path(entry.path), depth + 1))
                elif stat.S_ISREG(mode):
                    result.regular_files += 1
                    suffix = Path(entry.name).suffix.lower().lstrip(".")
                    if suffix in extensions:
                        result.candidate_roms += 1
                        result.candidate_extensions[suffix] = (
                            result.candidate_extensions.get(suffix, 0) + 1
                        )
                else:
                    result.special_files += 1

    if result.stopped == "complete" and result.depth_limited_directories:
        result.stopped = "depth_limit"
    result.candidate_extensions = dict(sorted(result.candidate_extensions.items()))
    result.elapsed_seconds = round(time.monotonic() - started, 6)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--extensions",
        default=(
            "zip,7z,nes,fds,sfc,smc,fig,bs,gb,gbc,dmg,gba,md,mdx,gen,"
            "smd,32x,bin,pce,cue,ccd,chd,n64,z64,v64,nds,m3u,pbp,iso,"
            "cso,elf"
        ),
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-entries", type=int, default=250_000)
    parser.add_argument("--max-depth", type=int, default=64)
    parser.add_argument("--delay-per-entry", type=float, default=0.0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.max_entries <= 0:
        parser.error("--max-entries must be positive")
    if args.max_depth < 0:
        parser.error("--max-depth must be non-negative")
    if args.delay_per_entry < 0:
        parser.error("--delay-per-entry must be non-negative")
    extensions = {item.strip().lower().lstrip(".") for item in args.extensions.split(",") if item.strip()}
    try:
        result = scan(
            args.root,
            extensions,
            timeout_seconds=args.timeout,
            max_entries=args.max_entries,
            max_depth=args.max_depth,
            delay_per_entry=args.delay_per_entry,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"stopped": "invalid_root", "error": str(exc)}))
        return 2
    print(json.dumps(asdict(result), sort_keys=True))
    return {
        "complete": 0,
        "timeout": 124,
        "entry_limit": 75,
        "depth_limit": 76,
    }.get(result.stopped, 1)


if __name__ == "__main__":
    raise SystemExit(main())
