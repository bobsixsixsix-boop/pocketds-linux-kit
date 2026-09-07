#!/usr/bin/env python3
"""Build a non-destructive ES-DE transfer tree from the Pocket DS ROM backup."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class System:
    metadata: str
    source_dir: str
    rom_dir: str
    esde_name: str
    extensions: frozenset[str]


SYSTEMS = (
    System("FC", "famicom", "famicom", "famicom", frozenset({".nes", ".fds", ".zip", ".7z"})),
    System("NES", "nes", "nes", "nes", frozenset({".nes", ".fds", ".zip", ".7z"})),
    System("SFC", "sfc", "sfc", "sfc", frozenset({".sfc", ".smc", ".fig", ".bs", ".zip", ".7z"})),
    System("GBC", "gbc", "gbc", "gbc", frozenset({".gb", ".gbc", ".dmg", ".zip", ".7z"})),
    System("GBA", "gba", "gba", "gba", frozenset({".gba", ".zip", ".7z"})),
    System("MD", "genesis", "genesis", "genesis", frozenset({".md", ".mdx", ".gen", ".smd", ".32x", ".bin", ".zip", ".7z"})),
    System("PCE", "pcengine", "pcengine", "pcengine", frozenset({".pce", ".cue", ".ccd", ".chd", ".bin", ".img", ".sub", ".zip", ".7z"})),
    System("N64", "n64", "n64", "n64", frozenset({".z64", ".n64", ".v64", ".rom", ".bin", ".u1", ".ndd", ".zip", ".7z"})),
    System("NDS", "nds", "nds", "nds", frozenset({".nds"})),
    System("PS1", "psx", "psx", "psx", frozenset({".cue", ".chd", ".m3u", ".pbp", ".iso", ".ccd", ".bin", ".img", ".sub", ".zip", ".7z"})),
    System("PSP", "psp", "psp", "psp", frozenset({".iso", ".cso", ".chd", ".pbp", ".elf"})),
    System("NEOGEO", "neogeo", "neogeo", "neogeo", frozenset({".zip", ".7z"})),
    System("ARCADE", "arcade", "arcade", "arcade", frozenset({".zip", ".7z"})),
    System("MAME2003", "mame2003", "mame2003", "mame", frozenset({".zip", ".7z"})),
)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".webm"}


def safe_relative(value: str) -> Path | None:
    pure = PurePosixPath(value.strip().replace("\\", "/"))
    if not value.strip() or pure.is_absolute() or ".." in pure.parts:
        return None
    return Path(*pure.parts)


def regular_file(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode)


def stage_file(source: Path, target: Path, mode: str) -> bool:
    if target.exists() or target.is_symlink():
        return False
    if not regular_file(source):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "hardlink":
        os.link(source, target, follow_symlinks=False)
    else:
        shutil.copy2(source, target, follow_symlinks=False)
    return True


def parse_pegasus(path: Path) -> list[dict[str, str]]:
    games: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith((" ", "\t")) or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        if key == "game":
            current = {"game": value.strip()}
            games.append(current)
        elif current is not None and key in {
            "file",
            "summary",
            "assets.box_front",
            "assets.screenshot",
            "assets.video",
        }:
            current[key] = value.strip()
    return games


def write_gamelist(path: Path, games: list[dict[str, str]]) -> None:
    root = ET.Element("gameList")
    for game in games:
        node = ET.SubElement(root, "game")
        ET.SubElement(node, "path").text = "./" + game["file"]
        ET.SubElement(node, "name").text = game["game"]
        if game.get("summary"):
            ET.SubElement(node, "desc").text = game["summary"]
    ET.indent(root, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode", short_empty_elements=True)
        + "\n",
        encoding="utf-8",
    )


def media_source(
    backup_system: Path, repaired_root: Path | None, metadata_key: str, relative: Path
) -> Path | None:
    candidates = [backup_system / relative]
    if repaired_root is not None:
        candidates.append(repaired_root / metadata_key / relative)
    for candidate in candidates:
        if regular_file(candidate):
            return candidate
    return None


def stage_system(
    system: System,
    *,
    backup_root: Path,
    metadata_root: Path,
    repaired_root: Path | None,
    nds_root: Path | None,
    stage_home: Path,
    mode: str,
) -> tuple[dict[str, object], dict[str, Path]]:
    backup_system = backup_root / system.source_dir
    rom_source = nds_root if system.metadata == "NDS" and nds_root else backup_system
    if not rom_source.is_dir() or rom_source.is_symlink():
        raise ValueError(f"unsafe or missing ROM source: {rom_source}")
    rom_target = stage_home / "ROMs" / system.rom_dir
    rom_index: dict[str, Path] = {}
    rom_bytes = 0
    for source in sorted(rom_source.rglob("*")):
        if not regular_file(source) or source.suffix.lower() not in system.extensions:
            continue
        relative = source.relative_to(rom_source)
        if stage_file(source, rom_target / relative, mode):
            rom_index[relative.as_posix()] = source
            rom_bytes += source.stat().st_size

    metadata_file = metadata_root / system.metadata / "metadata.pegasus.txt"
    metadata_games: list[dict[str, str]] = []
    missing_metadata_roms = 0
    media_files = 0
    media_bytes = 0
    missing_media = 0
    if regular_file(metadata_file):
        for game in parse_pegasus(metadata_file):
            relative = safe_relative(game.get("file", ""))
            if relative is None or relative.as_posix() not in rom_index:
                missing_metadata_roms += 1
                continue
            normalized = dict(game)
            normalized["file"] = relative.as_posix()
            metadata_games.append(normalized)
            stem = relative.with_suffix("")
            for field, media_type, allowed in (
                ("assets.box_front", "covers", IMAGE_EXTENSIONS),
                ("assets.screenshot", "screenshots", IMAGE_EXTENSIONS),
                ("assets.video", "videos", VIDEO_EXTENSIONS),
            ):
                asset = safe_relative(game.get(field, ""))
                if asset is None:
                    continue
                source = media_source(backup_system, repaired_root, system.metadata, asset)
                if source is None or source.suffix.lower() not in allowed:
                    missing_media += 1
                    continue
                target = (
                    stage_home
                    / "ES-DE/downloaded_media"
                    / system.esde_name
                    / media_type
                    / stem.parent
                    / (stem.name + source.suffix.lower().replace(".jpeg", ".jpg"))
                )
                if stage_file(source, target, mode):
                    media_files += 1
                    media_bytes += source.stat().st_size

    write_gamelist(
        stage_home / "ES-DE/gamelists" / system.esde_name / "gamelist.xml",
        metadata_games,
    )
    return (
        {
            "system": system.esde_name,
            "rom_directory": system.rom_dir,
            "rom_files": len(rom_index),
            "rom_bytes": rom_bytes,
            "metadata_games": len(metadata_games),
            "missing_metadata_roms": missing_metadata_roms,
            "media_files": media_files,
            "media_bytes": media_bytes,
            "missing_media_references": missing_media,
        },
        rom_index,
    )


def build_collection(
    selection: Path | None,
    indexes: dict[str, dict[str, Path]],
    stage_home: Path,
) -> dict[str, object]:
    result: dict[str, object] = {"name": "精选集", "games": 0, "missing": 0}
    if selection is None:
        return result
    systems_by_source = {system.source_dir: system for system in SYSTEMS}
    lines: list[str] = []
    missing = 0
    with selection.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            source_relative = safe_relative(row.get("source_rel", ""))
            if source_relative is None or len(source_relative.parts) < 2:
                missing += 1
                continue
            source_dir = source_relative.parts[0]
            system = systems_by_source.get(source_dir)
            if system is None:
                missing += 1
                continue
            relative = Path(*source_relative.parts[1:])
            if system.metadata == "NDS" and relative.suffix.lower() != ".nds":
                relative = relative.with_suffix(".nds")
            if relative.as_posix() not in indexes[system.metadata]:
                missing += 1
                continue
            lines.append(f"%ROMPATH%/{system.rom_dir}/{relative.as_posix()}")
    lines = list(dict.fromkeys(lines))
    target = stage_home / "ES-DE/collections/custom-精选集.cfg"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    result.update({"games": len(lines), "missing": missing})
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--metadata-root", type=Path, required=True)
    parser.add_argument("--repaired-media-root", type=Path)
    parser.add_argument("--nds-root", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--quarantine-root", type=Path)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--mode", choices=("hardlink", "copy"), default="hardlink")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stage = args.stage.expanduser().absolute()
    if stage.exists() or stage.is_symlink():
        raise SystemExit("stage path must not already exist")
    stage_home = stage / "home"
    stage_home.mkdir(parents=True)
    reports: list[dict[str, object]] = []
    indexes: dict[str, dict[str, Path]] = {}
    for system in SYSTEMS:
        report, index = stage_system(
            system,
            backup_root=args.backup_root.expanduser().resolve(),
            metadata_root=args.metadata_root.expanduser().resolve(),
            repaired_root=args.repaired_media_root.expanduser().resolve()
            if args.repaired_media_root
            else None,
            nds_root=args.nds_root.expanduser().resolve() if args.nds_root else None,
            stage_home=stage_home,
            mode=args.mode,
        )
        reports.append(report)
        indexes[system.metadata] = index
    collection = build_collection(args.selection, indexes, stage_home)
    quarantine_files = 0
    if args.quarantine_root and args.quarantine_root.is_dir():
        quarantine_files = sum(1 for path in args.quarantine_root.rglob("*") if regular_file(path))
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
    manifest = {
        "schema": 1,
        "stage_home": str(stage_home),
        "link_mode": args.mode,
        "systems": reports,
        "collection": collection,
        "quarantine_files_excluded": quarantine_files,
        "inventory": {
            "file": inventory_path.name,
            "files": inventory_files,
            "logical_bytes": inventory_bytes,
        },
        "totals": {
            "rom_files": sum(int(item["rom_files"]) for item in reports),
            "rom_bytes": sum(int(item["rom_bytes"]) for item in reports),
            "metadata_games": sum(int(item["metadata_games"]) for item in reports),
            "media_files": sum(int(item["media_files"]) for item in reports),
            "media_bytes": sum(int(item["media_bytes"]) for item in reports),
        },
    }
    (stage / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
