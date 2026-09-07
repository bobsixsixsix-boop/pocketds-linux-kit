#!/usr/bin/env python3
"""Validate and merge the Pocket DS Wiliwili GLFW controller mapping."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


POCKETDS_GLFW_GUID = "030000005e040000000b000001000000"
EXPECTED_BINDINGS = {
    "a": "b0",
    "b": "b1",
    "x": "b2",
    "y": "b3",
    "back": "b6",
    "start": "b7",
    "guide": "b8",
    "leftshoulder": "b4",
    "rightshoulder": "b5",
    "leftstick": "b9",
    "rightstick": "b10",
    "lefttrigger": "a2",
    "righttrigger": "a5",
    "leftx": "a0",
    "lefty": "a1",
    "rightx": "a3",
    "righty": "a4",
    "dpup": "h0.1",
    "dpright": "h0.2",
    "dpdown": "h0.4",
    "dpleft": "h0.8",
    "platform": "Linux",
}


def validate_mapping(line: str) -> str:
    mapping = line.strip()
    if "\n" in mapping or "\r" in mapping:
        raise ValueError("controller mapping must be exactly one line")

    fields = mapping.split(",")
    if len(fields) < 4 or fields[-1] != "":
        raise ValueError("controller mapping must be comma-terminated")
    if fields[0] != POCKETDS_GLFW_GUID:
        raise ValueError("unexpected Pocket DS GLFW GUID")
    if not re.fullmatch(r"[0-9a-f]{32}", fields[0]):
        raise ValueError("GLFW GUID must be 32 lowercase hexadecimal characters")
    if not fields[1] or "," in fields[1]:
        raise ValueError("controller mapping name is empty or invalid")

    bindings: dict[str, str] = {}
    for field in fields[2:-1]:
        if field.count(":") != 1:
            raise ValueError(f"invalid controller mapping field: {field!r}")
        key, value = field.split(":", 1)
        if not key or not value:
            raise ValueError(f"empty controller mapping field: {field!r}")
        if key in bindings:
            raise ValueError(f"duplicate controller mapping field: {key}")
        bindings[key] = value

    if bindings != EXPECTED_BINDINGS:
        missing = sorted(set(EXPECTED_BINDINGS) - set(bindings))
        extra = sorted(set(bindings) - set(EXPECTED_BINDINGS))
        wrong = sorted(
            key
            for key in set(bindings) & set(EXPECTED_BINDINGS)
            if bindings[key] != EXPECTED_BINDINGS[key]
        )
        raise ValueError(
            f"unexpected controller bindings; missing={missing}, extra={extra}, wrong={wrong}"
        )
    return mapping


def read_source(path: Path) -> str:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("mapping source must contain exactly one non-empty line")
    return validate_mapping(lines[0])


def database_key(line: str) -> tuple[str, str | None] | None:
    fields = line.strip().split(",")
    if not fields or fields[0] != POCKETDS_GLFW_GUID:
        return None
    platform = None
    for field in fields[2:]:
        if field.startswith("platform:"):
            platform = field.split(":", 1)[1]
            break
    return fields[0], platform


def merge_database(existing: str, mapping: str) -> str:
    """Replace Linux-applicable rows for this GUID; preserve other platforms."""

    validate_mapping(mapping)
    output: list[str] = []
    inserted = False
    for line in existing.splitlines():
        key = database_key(line)
        # GLFW accepts a missing platform field as a mapping for the current
        # platform. It also lets a later duplicate GUID replace an earlier
        # one, so both generic and explicit Linux rows must be de-duplicated.
        if key is not None and key[1] in {None, "Linux"}:
            if not inserted:
                output.append(mapping)
                inserted = True
            continue
        output.append(line)

    if not inserted:
        output.append(mapping)

    return "\n".join(output).rstrip("\n") + "\n"


def database_is_current(existing: str, mapping: str) -> bool:
    validate_mapping(mapping)
    applicable = [
        line.strip()
        for line in existing.splitlines()
        if (key := database_key(line)) is not None and key[1] in {None, "Linux"}
    ]
    return applicable == [mapping]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--existing", type=Path)
    group.add_argument("--check-existing", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    mapping = read_source(args.source)
    if args.check_existing is not None:
        existing = args.check_existing.read_text(encoding="utf-8")
        return 0 if database_is_current(existing, mapping) else 1
    existing = ""
    if args.existing is not None:
        existing = args.existing.read_text(encoding="utf-8")
    sys.stdout.write(merge_database(existing, mapping))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
