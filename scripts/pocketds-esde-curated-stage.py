#!/usr/bin/env python3
"""Build a small ES-DE stage from one collection in a verified full stage."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import xml.etree.ElementTree as ET


COLLECTION_PREFIX = "%ROMPATH%/"
SYSTEM_TO_ESDE = {"mame2003": "mame"}
MEDIA_TYPES = ("covers", "screenshots", "videos")


def regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def safe_relative(value: str) -> PurePosixPath:
    relative = PurePosixPath(value.strip().replace("\\", "/"))
    if (
        not value.strip()
        or relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) < 2
    ):
        raise ValueError(f"unsafe ROM path: {value!r}")
    return relative


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_collection(path: Path) -> list[PurePosixPath]:
    if not regular_file(path) or path.stat().st_size > 1024 * 1024:
        raise ValueError("collection must be a regular file no larger than 1 MiB")
    entries: list[PurePosixPath] = []
    seen: set[PurePosixPath] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(COLLECTION_PREFIX):
            raise ValueError(f"invalid collection line {line_number}")
        relative = safe_relative(line.removeprefix(COLLECTION_PREFIX))
        if relative in seen:
            raise ValueError(f"duplicate collection line {line_number}")
        seen.add(relative)
        entries.append(relative)
    if not entries:
        raise ValueError("collection is empty")
    return entries


def source_rom(source_home: Path, relative: PurePosixPath) -> Path:
    source = source_home / "ROMs" / Path(*relative.parts)
    if not regular_file(source):
        raise ValueError(f"missing or unsafe ROM: {relative.as_posix()}")
    return source


def metadata_index(source_home: Path, system: str) -> dict[str, ET.Element]:
    esde_system = SYSTEM_TO_ESDE.get(system, system)
    gamelist = source_home / "ES-DE/gamelists" / esde_system / "gamelist.xml"
    if not regular_file(gamelist) or gamelist.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"missing or unsafe gamelist for {system}")
    root = ET.parse(gamelist).getroot()
    if root.tag != "gameList":
        raise ValueError(f"invalid gamelist root for {system}")
    result: dict[str, ET.Element] = {}
    for game in root.findall("game"):
        raw_path = game.findtext("path", "")
        normalized = raw_path.removeprefix("./").replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
            raise ValueError(f"unsafe gamelist entry for {system}")
        if normalized in result:
            raise ValueError(f"duplicate gamelist entry for {system}: {normalized}")
        result[normalized] = game
    return result


def matching_media(source_home: Path, relative: PurePosixPath) -> list[tuple[Path, Path]]:
    system = relative.parts[0]
    esde_system = SYSTEM_TO_ESDE.get(system, system)
    rom_relative = Path(*relative.parts[1:])
    stem = rom_relative.with_suffix("")
    matches: list[tuple[Path, Path]] = []
    for media_type in MEDIA_TYPES:
        directory = (
            source_home
            / "ES-DE/downloaded_media"
            / esde_system
            / media_type
            / stem.parent
        )
        if not directory.exists():
            continue
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"unsafe media directory for {relative.as_posix()}")
        for source in sorted(directory.glob(stem.name + ".*")):
            if not regular_file(source):
                raise ValueError(f"unsafe media file for {relative.as_posix()}")
            target_relative = (
                Path("ES-DE/downloaded_media")
                / esde_system
                / media_type
                / stem.parent
                / source.name
            )
            matches.append((source, target_relative))
    return matches


def copy_regular(source: Path, target: Path) -> int:
    if target.exists() or target.is_symlink():
        raise ValueError(f"curated target collision: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=False)
    target.chmod(0o644)
    return target.stat().st_size


def write_gamelist(path: Path, games: list[ET.Element]) -> None:
    root = ET.Element("gameList")
    for game in games:
        root.append(copy.deepcopy(game))
    ET.indent(root, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode", short_empty_elements=True)
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o644)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-stage", type=Path, required=True)
    parser.add_argument("--collection", default="custom-精选集.cfg")
    parser.add_argument(
        "--support-rom",
        action="append",
        default=[],
        metavar="SYSTEM/PATH",
        help="copy an unlisted BIOS or parent ROM without adding it to the collection",
    )
    parser.add_argument("--stage", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_stage = args.source_stage.expanduser().absolute()
    source_home = source_stage / "home"
    stage = args.stage.expanduser().absolute()
    stage_home = stage / "home"
    if source_stage.is_symlink() or not source_stage.is_dir():
        raise SystemExit("source stage must be a real directory")
    if not source_home.is_dir() or source_home.is_symlink():
        raise SystemExit("source stage home must be a real directory")
    if stage.exists() or stage.is_symlink():
        raise SystemExit("curated stage path must not already exist")
    source_manifest = source_stage / "manifest.json"
    if not regular_file(source_manifest):
        raise SystemExit("source manifest is missing or unsafe")
    collection_source = source_home / "ES-DE/collections" / args.collection

    selected = parse_collection(collection_source)
    support = [safe_relative(value) for value in args.support_rom]
    selected_set = set(selected)
    support = list(dict.fromkeys(item for item in support if item not in selected_set))

    requested = selected + support
    rom_sources = {relative: source_rom(source_home, relative) for relative in requested}
    by_system: dict[str, list[PurePosixPath]] = {}
    for relative in selected:
        by_system.setdefault(relative.parts[0], []).append(relative)

    selected_metadata: dict[str, list[ET.Element]] = {}
    for system, entries in by_system.items():
        index = metadata_index(source_home, system)
        games: list[ET.Element] = []
        for relative in entries:
            rom_relative = PurePosixPath(*relative.parts[1:]).as_posix()
            if rom_relative not in index:
                raise ValueError(f"missing metadata: {relative.as_posix()}")
            games.append(index[rom_relative])
        selected_metadata[system] = games

    media: dict[Path, Path] = {}
    for relative in selected:
        for source, target_relative in matching_media(source_home, relative):
            previous = media.setdefault(target_relative, source)
            if previous != source:
                raise ValueError(f"media target collision: {target_relative}")

    stage.mkdir(mode=0o700, parents=True)
    stage_home.mkdir(mode=0o755)
    rom_bytes = 0
    for relative, source in rom_sources.items():
        rom_bytes += copy_regular(source, stage_home / "ROMs" / Path(*relative.parts))

    for system, games in selected_metadata.items():
        esde_system = SYSTEM_TO_ESDE.get(system, system)
        write_gamelist(
            stage_home / "ES-DE/gamelists" / esde_system / "gamelist.xml",
            games,
        )

    media_bytes = 0
    for target_relative, source in sorted(media.items(), key=lambda item: item[0].as_posix()):
        media_bytes += copy_regular(source, stage_home / target_relative)

    collection_target = stage_home / "ES-DE/collections" / args.collection
    collection_target.parent.mkdir(parents=True, exist_ok=True)
    collection_target.write_text(
        "\n".join(COLLECTION_PREFIX + item.as_posix() for item in selected) + "\n",
        encoding="utf-8",
    )
    collection_target.chmod(0o644)

    for directory in sorted(
        (path for path in stage_home.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        directory.chmod(0o755)

    inventory_path = stage / "files.jsonl"
    inventory_files = 0
    inventory_bytes = 0
    with inventory_path.open("w", encoding="utf-8") as stream:
        for path in sorted(stage_home.rglob("*")):
            if not regular_file(path):
                continue
            relative = path.relative_to(stage_home).as_posix()
            size = path.stat().st_size
            stream.write(
                json.dumps(
                    {"path": relative, "size": size},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            inventory_files += 1
            inventory_bytes += size
    inventory_path.chmod(0o600)

    manifest = {
        "schema": 1,
        "source_manifest_sha256": sha256(source_manifest),
        "source_collection_sha256": sha256(collection_source),
        "collection": {"file": args.collection, "games": len(selected)},
        "support_roms": len(support),
        "systems": {
            system: len(entries) for system, entries in sorted(by_system.items())
        },
        "inventory": {
            "file": inventory_path.name,
            "files": inventory_files,
            "logical_bytes": inventory_bytes,
        },
        "totals": {
            "rom_files": len(rom_sources),
            "rom_bytes": rom_bytes,
            "metadata_games": sum(len(games) for games in selected_metadata.values()),
            "media_files": len(media),
            "media_bytes": media_bytes,
        },
    }
    manifest_path = stage / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
