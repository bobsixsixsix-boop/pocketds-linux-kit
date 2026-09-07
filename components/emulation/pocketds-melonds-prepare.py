#!/usr/bin/env python3
"""Keep melonDS's two display windows mapped to the two physical DS panels."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib


SECTIONS = {
    "Instance0.Window0": {
        "ScreenLayout": "0",
        "ScreenSizing": "4",
        "ShowOSD": "false",
    },
    "Instance0.Window1": {
        "Enabled": "true",
        "ScreenLayout": "0",
        "ScreenSizing": "5",
        "ShowOSD": "false",
    },
    "Instance0.Joystick": {
        "A": "0",
        "B": "1",
        "X": "2",
        "Y": "3",
        "L": "4",
        "R": "5",
        "Select": "6",
        "Start": "7",
        "Up": "257",
        "Right": "258",
        "Down": "260",
        "Left": "264",
    },
    "Instance0.Keyboard": {
        "HK_FullscreenToggle": "16777274",
    },
}
SECTION_RE = re.compile(r"^\s*\[([^]\r\n]+)]\s*(?:#.*)?(?:\r?\n)?$")
KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_-]+)\s*=.*?(\r?\n)?$")


class PrepareError(RuntimeError):
    pass


def desired_sections(
    save_dir: Path | None = None, state_dir: Path | None = None
) -> dict[str, dict[str, str]]:
    if (save_dir is None) != (state_dir is None):
        raise PrepareError("melonDS save and state paths must be configured together")
    sections = {section: dict(values) for section, values in SECTIONS.items()}
    if save_dir is not None and state_dir is not None:
        if not save_dir.is_absolute() or not state_dir.is_absolute():
            raise PrepareError("melonDS save and state paths must be absolute")
        sections = {
            "Instance0": {
                "SaveFilePath": json.dumps(str(save_dir), ensure_ascii=False),
                "SavestatePath": json.dumps(str(state_dir), ensure_ascii=False),
            },
            **sections,
        }
    return sections


def patch_config(
    text: str, save_dir: Path | None = None, state_dir: Path | None = None
) -> str:
    sections = desired_sections(save_dir, state_dir)
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    current: str | None = None
    seen = {section: set() for section in sections}

    def finish_section(section: str | None) -> None:
        if section not in sections:
            return
        for key, value in sections[section].items():
            if key not in seen[section]:
                output.append(f"{key} = {value}\n")

    for line in lines:
        section_match = SECTION_RE.match(line)
        if section_match:
            finish_section(current)
            current = section_match.group(1)
            output.append(line)
            continue
        key_match = KEY_RE.match(line)
        if current in sections and key_match:
            key = key_match.group(2)
            if key in sections[current]:
                if key in seen[current]:
                    raise PrepareError(f"duplicate key in [{current}]: {key}")
                seen[current].add(key)
                newline = key_match.group(3) or "\n"
                output.append(
                    f"{key_match.group(1)}{key} = {sections[current][key]}{newline}"
                )
                continue
        output.append(line)
    finish_section(current)

    for section, values in sections.items():
        if any(SECTION_RE.match(line) and SECTION_RE.match(line).group(1) == section for line in lines):
            continue
        if output and not output[-1].endswith("\n"):
            output[-1] += "\n"
        if output and output[-1].strip():
            output.append("\n")
        output.append(f"[{section}]\n")
        output.extend(f"{key} = {value}\n" for key, value in values.items())

    patched = "".join(output)
    try:
        parsed = tomllib.loads(patched)
    except tomllib.TOMLDecodeError as exc:
        raise PrepareError("patched melonDS configuration is invalid TOML") from exc
    instance = parsed.get("Instance0")
    if not isinstance(instance, dict):
        raise PrepareError("melonDS configuration lacks Instance0")
    for section, expected in sections.items():
        actual: object = parsed
        for table_name in section.split("."):
            if not isinstance(actual, dict):
                break
            actual = actual.get(table_name)
        if not isinstance(actual, dict):
            raise PrepareError(f"melonDS configuration lacks {section}")
        for key, literal in expected.items():
            expected_value = tomllib.loads(f"value = {literal}\n")["value"]
            if actual.get(key) != expected_value:
                raise PrepareError(f"failed to set {section}.{key}")
    return patched


def ensure_owned_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while True:
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            missing.append(cursor)
            parent = cursor.parent
            if parent == cursor:
                raise PrepareError("melonDS configuration parent is unavailable")
            cursor = parent
            continue
        if cursor.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise PrepareError("melonDS configuration parent is not a directory")
        if metadata.st_uid != os.getuid():
            raise PrepareError("melonDS configuration parent has an unexpected owner")
        break

    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        metadata = directory.lstat()
        if directory.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise PrepareError("melonDS configuration parent is not a directory")
        if metadata.st_uid != os.getuid():
            raise PrepareError("melonDS configuration parent has an unexpected owner")


def create_config(
    path: Path, save_dir: Path | None = None, state_dir: Path | None = None
) -> bool:
    ensure_owned_directory(path.parent)
    if save_dir is not None and state_dir is not None:
        ensure_owned_directory(save_dir)
        ensure_owned_directory(state_dir)
    patched = patch_config("", save_dir, state_dir)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".new", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(patched)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            return update(path, save_dir, state_dir)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def update(
    path: Path, save_dir: Path | None = None, state_dir: Path | None = None
) -> bool:
    desired_sections(save_dir, state_dir)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return create_config(path, save_dir, state_dir)
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise PrepareError("melonDS configuration is not a regular file")
    if metadata.st_uid != os.getuid():
        raise PrepareError("melonDS configuration has an unexpected owner")
    original = path.read_text(encoding="utf-8")
    patched = patch_config(original, save_dir, state_dir)
    if save_dir is not None and state_dir is not None:
        ensure_owned_directory(save_dir)
        ensure_owned_directory(state_dir)
    if patched == original:
        return False

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".new", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(patched)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--save-dir", type=Path)
    parser.add_argument("--state-dir", type=Path)
    args = parser.parse_args()
    try:
        update(args.config, args.save_dir, args.state_dir)
    except (OSError, UnicodeError, PrepareError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
