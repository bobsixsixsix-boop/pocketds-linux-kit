#!/usr/bin/env python3
"""Build one TF-card game library for Android Pegasus G and Linux ES-DE."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import time
from urllib.parse import quote


SYSTEM_DIRECTORIES = {
    "arcade": "ARCADE",
    "famicom": "FC",
    "gba": "GBA",
    "gbc": "GBC",
    "genesis": "MD",
    "mame2003": "MAME2003",
    "n64": "N64",
    "nds": "NDS",
    "neogeo": "NEOGEO",
    "nes": "NES",
    "pcengine": "PCE",
    "psp": "PSP",
    "psx": "PS1",
    "sfc": "SFC",
}
SOURCE_DIRECTORIES = {target: source for source, target in SYSTEM_DIRECTORIES.items()}
MANAGED_TREES = ("Roms", "PocketDS/Frontends")


def regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def real_directory(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            for attempt in range(5):
                try:
                    block = stream.read(1024 * 1024)
                    break
                except TimeoutError:
                    if attempt == 4:
                        raise TimeoutError(f"timed out while hashing {path}")
                    time.sleep(0.25 * (attempt + 1))
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def safe_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink is not allowed in a library stage: {path}")
        if path.is_dir():
            continue
        if not regular_file(path):
            raise ValueError(f"special file is not allowed in a library stage: {path}")
        relative = path.relative_to(root).as_posix()
        if "\n" in relative or "\\" in relative:
            raise ValueError(f"unsupported path in a library stage: {relative!r}")
        files.append(path)
    return files


def copy_file(source: Path, target: Path) -> None:
    if not regular_file(source):
        raise ValueError(f"source is not a regular file: {source}")
    if target.exists() or target.is_symlink():
        raise ValueError(f"target collision: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=False)


def copy_tree(source: Path, target: Path) -> int:
    if not real_directory(source):
        raise ValueError(f"source is not a real directory: {source}")
    count = 0
    for source_file in safe_files(source):
        copy_file(source_file, target / source_file.relative_to(source))
        count += 1
    return count


def mapped_collection(text: str) -> str:
    output: list[str] = []
    prefix = "%ROMPATH%/"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(prefix):
            raise ValueError("ES-DE collection contains an unsupported line")
        relative = PurePosixPath(line.removeprefix(prefix))
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2:
            raise ValueError("ES-DE collection contains an unsafe ROM path")
        system = relative.parts[0]
        if system not in SYSTEM_DIRECTORIES:
            raise ValueError(f"ES-DE collection uses an unknown system: {system}")
        mapped = PurePosixPath(SYSTEM_DIRECTORIES[system], *relative.parts[1:])
        output.append(prefix + mapped.as_posix())
    if not output:
        raise ValueError("ES-DE collection is empty")
    return "\n".join(output) + "\n"


def pegasus_references(metadata: Path) -> list[str]:
    references: list[str] = []
    in_files = False
    for raw in metadata.read_text(encoding="utf-8").splitlines():
        if raw == "files:":
            in_files = True
            continue
        if in_files:
            if raw.startswith("  "):
                references.append(raw.strip())
                continue
            in_files = False
        if raw.startswith("file: "):
            references.append(raw.removeprefix("file: ").strip().strip('"'))
        elif raw.startswith("assets.") and ": " in raw:
            references.append(raw.split(": ", 1)[1].strip().strip('"'))
    return references


def split_pegasus_metadata(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    chunks = path.read_text(encoding="utf-8").split("\ngame: ")
    header = chunks[0].rstrip().splitlines()
    blocks: dict[str, list[str]] = {}
    for chunk in chunks[1:]:
        lines = chunk.rstrip().splitlines()
        block = [f"game: {lines[0]}", *lines[1:]]
        file_line = next((line for line in block if line.startswith("file: ")), None)
        if file_line is None:
            continue
        relative = file_line.removeprefix("file: ").strip().strip('"')
        blocks[relative] = block
    return header, blocks


def launch_lines(header: list[str]) -> list[str]:
    try:
        start = next(index for index, line in enumerate(header) if line.startswith("launch: "))
    except StopIteration:
        return []
    output = [header[start]]
    for line in header[start + 1 :]:
        if not line.startswith("  "):
            break
        output.append(line)
    return output


def compatible_android_launch(lines: list[str], system: str) -> list[str]:
    if system != "NDS":
        return lines
    # Every selected NDS game receives its own exact SAF URI below. A shared
    # collection launch rule cannot encode arbitrary UTF-8 paths correctly.
    return []


def nds_relative_path(reference: str) -> str | None:
    path = PurePosixPath(reference)
    parts = list(path.parts)
    while parts and parts[0] == "..":
        parts.pop(0)
    if len(parts) < 2 or parts[0].upper() != "NDS" or ".." in parts:
        return None
    return PurePosixPath(*parts[1:]).as_posix()


def watermelonds_document_uri(android_volume_id: str, rom_relative: str) -> str:
    tree_document = f"{android_volume_id}:Roms/NDS"
    rom_document = f"{tree_document}/{rom_relative}"
    base = "content://com.android.externalstorage.documents"
    return (
        f"{base}/tree/{quote(tree_document, safe='')}"
        f"/document/{quote(rom_document, safe='')}"
    )


def watermelonds_launch(android_volume_id: str, rom_relative: str) -> list[str]:
    uri = watermelonds_document_uri(android_volume_id, rom_relative)
    return [
        "launch: am start --user 0",
        "  -a me.magnum.melondualds.pocketds.POCKETDS_LAUNCH_ROM",
        "  -n me.magnum.melondualds.pocketds/me.magnum.melonds.pocketds.PocketDsLaunchActivity",
        f'  --es uri "{uri}"',
        "  --activity-clear-top",
    ]


def replace_game_launch(lines: list[str], replacement: list[str]) -> list[str]:
    output: list[str] = []
    index = 0
    replaced = False
    while index < len(lines):
        if lines[index].startswith("launch: "):
            replaced = True
            output.extend(replacement)
            index += 1
            while index < len(lines) and lines[index].startswith("  "):
                index += 1
            continue
        output.append(lines[index])
        index += 1
    if not replaced:
        raise ValueError("NDS Pegasus game has no launch rule to replace")
    return output


def fix_curated_android_launches(text: str, android_volume_id: str) -> str:
    chunks = text.split("\ngame: ")
    output = [chunks[0].rstrip()]
    for chunk in chunks[1:]:
        lines = chunk.rstrip().splitlines()
        file_line = next((line for line in lines if line.startswith("file: ")), None)
        if file_line is not None:
            reference = file_line.removeprefix("file: ").strip().strip('"')
            rom_relative = nds_relative_path(reference)
            if rom_relative is not None:
                lines = replace_game_launch(
                    lines,
                    watermelonds_launch(android_volume_id, rom_relative),
                )
        output.append("\n".join(lines))
    return output[0] + "".join(f"\n\ngame: {chunk}" for chunk in output[1:]) + "\n"


def curated_game_files(metadata: Path) -> dict[str, list[str]]:
    """Return the selected ROMs grouped by card system directory."""
    text = metadata.read_text(encoding="utf-8")
    chunks = text.split("\ngame: ")
    references: list[str] = []
    in_files = False
    for raw in chunks[0].splitlines():
        if raw == "files:":
            in_files = True
            continue
        if in_files and raw.startswith("  "):
            references.append(raw.strip().strip('"'))
        elif in_files:
            in_files = False
    for chunk in chunks[1:]:
        file_line = next(
            (line for line in chunk.splitlines() if line.startswith("file: ")),
            None,
        )
        if file_line:
            references.append(file_line.removeprefix("file: ").strip().strip('"'))

    grouped: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for reference in references:
        path = PurePosixPath(reference)
        parts = list(path.parts)
        while parts and parts[0] == "..":
            parts.pop(0)
        if len(parts) < 2 or parts[0] not in SOURCE_DIRECTORIES or ".." in parts:
            raise ValueError(f"curated Pegasus metadata has an unsafe game path: {reference}")
        system = parts[0]
        relative = PurePosixPath(*parts[1:]).as_posix()
        key = (system, relative)
        if key in seen:
            raise ValueError(f"curated Pegasus metadata repeats a game path: {reference}")
        seen.add(key)
        grouped.setdefault(system, []).append(relative)
    if not grouped:
        raise ValueError("curated Pegasus metadata contains no game files")
    return grouped


def curated_asset_lines(
    media_root: Path,
    source_system: str,
    game_name: str,
) -> list[str]:
    asset_lines: list[str] = []
    media_system = SOURCE_DIRECTORIES[source_system]
    search = {
        "images": ("assets.box_front", "assets.screenshot"),
        "videos": ("assets.video",),
        "marquees": ("assets.logo",),
    }
    for folder, keys in search.items():
        directory = media_root / media_system / folder
        matches = sorted(directory.glob(f"{game_name}.*")) if real_directory(directory) else []
        if not matches:
            continue
        relative = matches[0].relative_to(media_root).as_posix()
        for key in keys:
            asset_lines.append(f"{key}: ../精选集/media/{relative}")
    return asset_lines


def write_pegasus_categories(
    stage: Path,
    curated_metadata: Path,
    curated_media: Path,
    platform_metadata_root: Path,
) -> dict[str, int]:
    """Add one filtered Pegasus collection per console without duplicating games."""
    grouped = curated_game_files(curated_metadata)
    counts: dict[str, int] = {}
    for system, game_files in sorted(grouped.items()):
        source_metadata = platform_metadata_root / system / "metadata.pegasus.txt"
        if not regular_file(source_metadata):
            raise ValueError(f"Pegasus platform metadata is missing: {source_metadata}")
        header, blocks = split_pegasus_metadata(source_metadata)
        sort_line = next((line for line in header if line.startswith("sort-by: ")), None)
        output = [f"collection: {system}", f"shortname: {system}"]
        if sort_line:
            output.append(sort_line)
        output.extend(compatible_android_launch(launch_lines(header), system))
        output.extend(["files:", *(f"  {path}" for path in game_files), ""])

        # The curated collection deliberately references ARCADE games by file.
        # Keep their canonical records here; every other selected game already
        # has its sole game record in 精选集 and is attached here by file path.
        if system == "ARCADE":
            for game_file in game_files:
                source_block = blocks.get(game_file)
                if source_block is None:
                    raise ValueError(
                        f"selected ARCADE game is absent from platform metadata: {game_file}"
                    )
                game_name = source_block[0].removeprefix("game: ")
                clean_block = [
                    line for line in source_block if not line.startswith("assets.")
                ]
                clean_block.extend(curated_asset_lines(curated_media, system, game_name))
                output.extend([*clean_block, ""])

        target = stage / "Roms" / system / "metadata.pegasus.txt"
        if target.exists() or target.is_symlink():
            raise ValueError(f"Pegasus category metadata would overwrite a file: {target}")
        target.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
        validate_pegasus_references(target, stage)
        counts[system] = len(game_files)
    return counts


def validate_pegasus_references(metadata: Path, stage: Path) -> int:
    stage_root = stage.resolve()
    missing: list[str] = []
    for value in pegasus_references(metadata):
        target = (metadata.parent / value).resolve()
        if not target.is_relative_to(stage_root) or not regular_file(target):
            missing.append(value)
    if missing:
        raise ValueError("Pegasus metadata has missing or unsafe references:\n" + "\n".join(missing))
    text = metadata.read_text(encoding="utf-8")
    games = text.count("\ngame: ")
    header_files = 0
    in_files = False
    for raw in text.splitlines():
        if raw == "files:":
            in_files = True
            continue
        if in_files and raw.startswith("  "):
            header_files += 1
        elif in_files:
            break
    return games + header_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated-stage", type=Path, required=True)
    parser.add_argument("--pegasus-metadata", type=Path, required=True)
    parser.add_argument("--pegasus-media", type=Path, required=True)
    parser.add_argument("--pegasus-system-metadata", type=Path, required=True)
    parser.add_argument(
        "--android-volume-id",
        required=True,
        help="Android TF-card volume ID shown under /storage, for example 9C33-6BBD",
    )
    parser.add_argument("--stage", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.curated_stage.expanduser().absolute()
    source_home = source / "home"
    source_roms = source_home / "ROMs"
    source_esde = source_home / "ES-DE"
    source_manifest = source / "manifest.json"
    source_inventory = source / "files.jsonl"
    pegasus_metadata = args.pegasus_metadata.expanduser().absolute()
    pegasus_media = args.pegasus_media.expanduser().absolute()
    platform_metadata_root = args.pegasus_system_metadata.expanduser().absolute()
    stage = args.stage.expanduser().absolute()
    android_volume_id = args.android_volume_id.strip()

    if not android_volume_id or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in android_volume_id
    ):
        raise SystemExit("Android volume ID contains unsupported characters")

    for required in (source_manifest, source_inventory, pegasus_metadata):
        if not regular_file(required):
            raise SystemExit(f"required source is missing or unsafe: {required}")
    for required in (source_roms, source_esde, pegasus_media, platform_metadata_root):
        if not real_directory(required):
            raise SystemExit(f"required source directory is missing or unsafe: {required}")
    if stage.exists() or stage.is_symlink():
        raise SystemExit("shared library stage path must not already exist")

    source_state = json.loads(source_manifest.read_text(encoding="utf-8"))
    stage.mkdir(mode=0o700, parents=True)

    rom_files = 0
    for system in sorted(path for path in source_roms.iterdir() if path.is_dir()):
        if system.is_symlink() or system.name not in SYSTEM_DIRECTORIES:
            raise ValueError(f"unknown or unsafe ROM system: {system.name}")
        rom_files += copy_tree(
            system,
            stage / "Roms" / SYSTEM_DIRECTORIES[system.name],
        )

    esde_target = stage / "PocketDS/Frontends/ES-DE"
    esde_files = copy_tree(source_esde, esde_target)
    for collection in sorted((esde_target / "collections").glob("custom-*.cfg")):
        collection.write_text(
            mapped_collection(collection.read_text(encoding="utf-8")),
            encoding="utf-8",
        )

    pegasus_target = stage / "Roms/精选集"
    copy_file(pegasus_metadata, pegasus_target / "metadata.pegasus.txt")
    curated_target_metadata = pegasus_target / "metadata.pegasus.txt"
    curated_target_metadata.write_text(
        fix_curated_android_launches(
            curated_target_metadata.read_text(encoding="utf-8"),
            android_volume_id,
        ),
        encoding="utf-8",
    )
    pegasus_files = 1 + copy_tree(pegasus_media, pegasus_target / "media")
    pegasus_games = validate_pegasus_references(
        curated_target_metadata, stage
    )
    category_counts = write_pegasus_categories(
        stage,
        curated_target_metadata,
        pegasus_media,
        platform_metadata_root,
    )
    if sum(category_counts.values()) != pegasus_games:
        raise ValueError(
            "Pegasus console categories do not cover the curated collection exactly once"
        )
    pegasus_files += len(category_counts)

    for relative in (
        "PocketDS/BIOS",
        "PocketDS/Saves/Android",
        "PocketDS/Saves/Linux",
        "PocketDS/States/Android",
        "PocketDS/States/Linux",
        "PocketDS/Manifests",
    ):
        (stage / relative).mkdir(parents=True, exist_ok=True)

    readme = stage / "PocketDS/README.txt"
    readme.write_text(
        "Pocket DS shared game card\n"
        "\n"
        "Roms/ is the single shared ROM tree used by Android Pegasus G and Linux ES-DE.\n"
        "Frontend metadata is generated separately so neither frontend rewrites the other.\n"
        "Battery saves and save states remain OS-specific until emulator compatibility is verified.\n",
        encoding="utf-8",
    )

    inventory_files: list[Path] = []
    for relative in MANAGED_TREES:
        inventory_files.extend(safe_files(stage / relative))
    inventory_files.append(readme)
    inventory_files.sort(key=lambda path: path.relative_to(stage).as_posix())

    inventory = stage / "PocketDS/Manifests/files.sha256"
    inventory.write_text(
        "".join(
            f"{sha256(path)}  {path.relative_to(stage).as_posix()}\n"
            for path in inventory_files
        ),
        encoding="utf-8",
    )
    total_bytes = sum(path.stat().st_size for path in inventory_files)
    manifest = {
        "schema": 1,
        "name": "Pocket DS Android/Linux shared game library",
        "collection": source_state.get("collection"),
        "rom_files": rom_files,
        "pegasus_entries": pegasus_games,
        "pegasus_category_entries": sum(category_counts.values()),
        "pegasus_category_counts": category_counts,
        "pegasus_collections": len(category_counts) + 1,
        "pegasus_files": pegasus_files,
        "esde_files": esde_files,
        "managed_files": len(inventory_files),
        "managed_bytes": total_bytes,
        "android_volume_id": android_volume_id,
        "system_directories": SYSTEM_DIRECTORIES,
        "source_manifest_sha256": sha256(source_manifest),
        "source_inventory_sha256": sha256(source_inventory),
        "pegasus_metadata_sha256": sha256(pegasus_metadata),
        "pegasus_staged_metadata_sha256": sha256(curated_target_metadata),
        "inventory_sha256": sha256(inventory),
    }
    manifest_path = stage / "PocketDS/Manifests/shared-library.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
