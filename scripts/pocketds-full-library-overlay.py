#!/usr/bin/env python3
"""Prepare a verified full-library transfer plan for the shared Pocket DS TF card.

The large ROM and ES-DE files stay in their locked source stage.  This tool writes
small generated metadata plus exact rsync file lists, so deployment can copy only
the files named by the pinned inventory and ignore any later Finder artefacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from urllib.parse import quote


SYSTEMS = {
    "arcade": ("ARCADE", "ARCADE", "arcade", "ARCADE · 街机"),
    "famicom": ("FC", "FC", "famicom", "FC · 红白机"),
    "gba": ("GBA", "GBA", "gba", "GBA · Game Boy Advance"),
    "gbc": ("GBC", "GBC", "gbc", "GBC · Game Boy Color"),
    "genesis": ("MD", "MD", "genesis", "MD · Mega Drive"),
    "mame2003": ("MAME2003", "MAME2003", "mame", "MAME2003 · 怀旧街机"),
    "n64": ("N64", "N64", "n64", "N64 · Nintendo 64"),
    "nds": ("NDS", "NDS", "nds", "NDS · Nintendo DS"),
    "neogeo": ("NEOGEO", "NEOGEO", "neogeo", "NEOGEO"),
    "nes": ("NES", "NES", "nes", "NES · Nintendo Entertainment System"),
    "pcengine": ("PCE", "PCE", "pcengine", "PCE · PC Engine"),
    "psp": ("PSP", "PSP", "psp", "PSP · PlayStation Portable"),
    "psx": ("PS1", "PS1", "psx", "PS1 · PlayStation"),
    "sfc": ("SFC", "SFC", "sfc", "SFC · 超级任天堂"),
}
ASSET_TYPES = {
    "assets.box_front": "covers",
    "assets.screenshot": "screenshots",
    "assets.video": "videos",
}
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_INVENTORY_BYTES = 16 * 1024 * 1024


def regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(value: str) -> PurePosixPath:
    if not value or "\x00" in value or "\n" in value or "\\" in value:
        raise ValueError(f"unsafe path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ValueError(f"unsafe path: {value!r}")
    return path


def read_bounded(path: Path, maximum: int, label: str) -> bytes:
    if not regular_file(path) or path.stat().st_size > maximum:
        raise ValueError(f"{label} is missing, unsafe, or oversized")
    return path.read_bytes()


def load_source(
    source: Path,
    expected_manifest_sha256: str,
    expected_inventory_sha256: str,
) -> tuple[dict[str, object], list[dict[str, object]], int, int]:
    manifest_path = source / "manifest.json"
    inventory_path = source / "files.jsonl"
    manifest_bytes = read_bounded(manifest_path, MAX_MANIFEST_BYTES, "manifest")
    inventory_bytes = read_bounded(inventory_path, MAX_INVENTORY_BYTES, "inventory")
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise ValueError("source manifest does not match the pinned SHA-256")
    if hashlib.sha256(inventory_bytes).hexdigest() != expected_inventory_sha256:
        raise ValueError("source inventory does not match the pinned SHA-256")
    manifest = json.loads(manifest_bytes)
    rows = [json.loads(line) for line in inventory_bytes.splitlines()]
    inventory_state = manifest.get("inventory", {})
    if (
        inventory_state.get("files") != len(rows)
        or inventory_state.get("logical_bytes")
        != sum(int(row["size"]) for row in rows)
    ):
        raise ValueError("source manifest and inventory totals disagree")

    home = source / "home"
    seen: set[str] = set()
    for row in rows:
        if set(row) != {"path", "size"} or type(row["size"]) is not int:
            raise ValueError("source inventory row has an invalid schema")
        relative = safe_relative(row["path"])
        if relative.parts[0] not in {"ROMs", "ES-DE"}:
            raise ValueError(f"source inventory path is unmanaged: {relative}")
        if relative.as_posix() in seen:
            raise ValueError(f"source inventory repeats a path: {relative}")
        seen.add(relative.as_posix())
        target = home / Path(*relative.parts)
        if not regular_file(target) or target.stat().st_size != row["size"]:
            raise ValueError(f"source file is missing or has the wrong size: {relative}")

    actual = {
        path.relative_to(home).as_posix()
        for path in home.rglob("*")
        if regular_file(path)
    }
    return manifest, rows, len(actual - seen), sum(
        (home / extra).stat().st_size for extra in actual - seen
    )


def split_metadata(text: str) -> tuple[list[str], list[list[str]]]:
    header: list[str] = []
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for raw in text.splitlines():
        if raw.startswith("game: "):
            current = [raw]
            blocks.append(current)
        elif current is None:
            header.append(raw)
        else:
            current.append(raw)
    while header and not header[-1]:
        header.pop()
    for block in blocks:
        while block and not block[-1]:
            block.pop()
    return header, blocks


def without_launch(lines: list[str]) -> list[str]:
    output: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith("launch: "):
            skipping = True
            continue
        if skipping and line.startswith((" ", "\t")):
            continue
        skipping = False
        output.append(line)
    return output


def normalized_header(lines: list[str], display_name: str, *, nds: bool) -> list[str]:
    result: list[str] = []
    inserted = False
    source = without_launch(lines) if nds else lines
    for line in source:
        if line.startswith("collection: "):
            result.extend((f"collection: {display_name}", f"shortname: {display_name}"))
            inserted = True
        elif not line.startswith("shortname: "):
            result.append(line)
    if not inserted:
        result[0:0] = [f"collection: {display_name}", f"shortname: {display_name}"]
    return result


def nds_launch(android_volume_id: str, rom_relative: str) -> list[str]:
    tree_document = f"{android_volume_id}:Roms/NDS"
    rom_document = f"{tree_document}/{rom_relative}"
    base = "content://com.android.externalstorage.documents"
    uri = (
        f"{base}/tree/{quote(tree_document, safe='')}"
        f"/document/{quote(rom_document, safe='')}"
    )
    return [
        "launch: am start --user 0",
        "  -a me.magnum.melondualds.pocketds.POCKETDS_LAUNCH_ROM",
        "  -n me.magnum.melondualds.pocketds/me.magnum.melonds.pocketds.PocketDsLaunchActivity",
        f'  --es uri "{uri}"',
        "  --activity-clear-top",
    ]


def media_index(rows: list[dict[str, object]]) -> dict[tuple[str, str, str], str]:
    result: dict[tuple[str, str, str], str] = {}
    for row in rows:
        path = PurePosixPath(str(row["path"]))
        if len(path.parts) < 5 or path.parts[:2] != ("ES-DE", "downloaded_media"):
            continue
        system, media_type = path.parts[2:4]
        relative = PurePosixPath(*path.parts[4:])
        key = (system, media_type, relative.with_suffix("").as_posix())
        previous = result.setdefault(key, path.as_posix())
        if previous != path.as_posix():
            raise ValueError(f"ambiguous media files for {key}")
    return result


def transform_platform_metadata(
    source_path: Path,
    *,
    source_system: str,
    esde_system: str,
    display_name: str,
    expected_roms: set[str],
    media: dict[tuple[str, str, str], str],
    android_volume_id: str,
) -> tuple[str, int]:
    if not regular_file(source_path):
        raise ValueError(f"Pegasus metadata is missing: {source_path}")
    header, blocks = split_metadata(source_path.read_text(encoding="utf-8"))
    output = normalized_header(header, display_name, nds=source_system == "nds")
    observed: set[str] = set()
    for block in blocks:
        file_line = next((line for line in block if line.startswith("file: ")), None)
        if file_line is None:
            raise ValueError(f"Pegasus game has no file line in {source_path}")
        relative = safe_relative(file_line.removeprefix("file: ").strip().strip('"'))
        relative_text = relative.as_posix()
        if relative_text in observed:
            raise ValueError(f"Pegasus metadata repeats a ROM: {relative_text}")
        observed.add(relative_text)
        stem = relative.with_suffix("").as_posix()
        transformed: list[str] = []
        for line in without_launch(block):
            key = line.split(":", 1)[0]
            if key in ASSET_TYPES:
                media_type = ASSET_TYPES[key]
                target = media.get((esde_system, media_type, stem))
                if target is None:
                    raise ValueError(f"missing staged media for {source_system}/{relative_text}")
                target_relative = PurePosixPath(target).relative_to("ES-DE")
                transformed.append(f"{key}: ../../PocketDS/Frontends/ES-DE/{target_relative}")
                continue
            transformed.append(line)
            if source_system == "nds" and line.startswith("file: "):
                transformed.extend(nds_launch(android_volume_id, relative_text))
        output.extend(("", *transformed))
    if observed != expected_roms:
        missing = sorted(expected_roms - observed)
        extra = sorted(observed - expected_roms)
        raise ValueError(
            f"Pegasus/ROM inventory mismatch for {source_system}: "
            f"missing={missing[:1]} extra={extra[:1]}"
        )
    return "\n".join(output).rstrip() + "\n", len(observed)


def target_path(source_path: PurePosixPath) -> PurePosixPath:
    if source_path.parts[0] == "ROMs":
        source_system = source_path.parts[1]
        if source_system not in SYSTEMS:
            raise ValueError(f"unknown ROM system: {source_system}")
        return PurePosixPath("Roms", SYSTEMS[source_system][0], *source_path.parts[2:])
    if source_path.parts[0] == "ES-DE":
        return PurePosixPath("PocketDS", "Frontends", *source_path.parts)
    raise ValueError(f"unmanaged source path: {source_path}")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-stage", type=Path, required=True)
    parser.add_argument("--pegasus-system-metadata", type=Path, required=True)
    parser.add_argument("--android-volume-id", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-inventory-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source_stage.expanduser().absolute()
    metadata_root = args.pegasus_system_metadata.expanduser().absolute()
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise SystemExit("output path must not already exist")
    if not args.android_volume_id or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in args.android_volume_id
    ):
        raise SystemExit("Android volume ID contains unsupported characters")
    try:
        manifest, rows, ignored_files, ignored_bytes = load_source(
            source,
            args.expected_manifest_sha256,
            args.expected_inventory_sha256,
        )
        media = media_index(rows)
        roms: dict[str, set[str]] = {system: set() for system in SYSTEMS}
        esde_files: list[str] = []
        target_rows: dict[str, tuple[int, Path]] = {}
        for row in rows:
            source_relative = PurePosixPath(str(row["path"]))
            mapped = target_path(source_relative)
            target_rows[mapped.as_posix()] = (
                int(row["size"]),
                source / "home" / Path(*source_relative.parts),
            )
            if source_relative.parts[0] == "ROMs":
                system = source_relative.parts[1]
                roms[system].add(PurePosixPath(*source_relative.parts[2:]).as_posix())
            else:
                esde_files.append(PurePosixPath(*source_relative.parts[1:]).as_posix())

        output.mkdir(mode=0o700, parents=True)
        overlay = output / "overlay"
        platform_counts: dict[str, int] = {}
        for source_system, (target, metadata_name, esde_name, display_name) in SYSTEMS.items():
            if not roms[source_system]:
                continue
            text, count = transform_platform_metadata(
                metadata_root / metadata_name / "metadata.pegasus.txt",
                source_system=source_system,
                esde_system=esde_name,
                display_name=display_name,
                expected_roms=roms[source_system],
                media=media,
                android_volume_id=args.android_volume_id,
            )
            target_metadata = overlay / "Roms" / target / "metadata.pegasus.txt"
            write_text(target_metadata, text)
            target_rows[f"Roms/{target}/metadata.pegasus.txt"] = (
                target_metadata.stat().st_size,
                target_metadata,
            )
            platform_counts[target] = count
            write_text(
                output / "transfer" / "roms" / f"{source_system}.files",
                "\n".join(sorted(roms[source_system])) + "\n",
            )

        collection_source = source / "home/ES-DE/collections/custom-精选集.cfg"
        selected: list[str] = []
        mapped_collection: list[str] = []
        for raw in collection_source.read_text(encoding="utf-8").splitlines():
            prefix = "%ROMPATH%/"
            if not raw.startswith(prefix):
                raise ValueError("unsupported ES-DE collection entry")
            relative = safe_relative(raw.removeprefix(prefix))
            source_system = relative.parts[0]
            if source_system not in SYSTEMS:
                raise ValueError(f"unknown collection system: {source_system}")
            target = SYSTEMS[source_system][0]
            rom_relative = PurePosixPath(*relative.parts[1:]).as_posix()
            if rom_relative not in roms[source_system]:
                raise ValueError(f"collection ROM is absent: {relative}")
            mapped_collection.append(f"{prefix}{target}/{rom_relative}")
            selected.append(f"  ../{target}/{rom_relative}")
        if len(selected) != int(manifest["collection"]["games"]):
            raise ValueError("collection count disagrees with the source manifest")

        collection_cfg = overlay / "PocketDS/Frontends/ES-DE/collections/custom-精选集.cfg"
        write_text(collection_cfg, "\n".join(mapped_collection) + "\n")
        target_rows["PocketDS/Frontends/ES-DE/collections/custom-精选集.cfg"] = (
            collection_cfg.stat().st_size,
            collection_cfg,
        )
        curated = overlay / "Roms/精选集/metadata.pegasus.txt"
        write_text(
            curated,
            "collection: 精选集\n"
            "shortname: 精选集\n"
            "sort-by: 000\n"
            f"summary: {len(selected)} 款中文热门精选；游戏本体与各主机分类共用，不重复占用空间\n"
            "files:\n"
            + "\n".join(selected)
            + "\n",
        )
        target_rows["Roms/精选集/metadata.pegasus.txt"] = (curated.stat().st_size, curated)

        write_text(output / "transfer/esde.files", "\n".join(sorted(esde_files)) + "\n")
        readme = overlay / "PocketDS/README.txt"
        write_text(
            readme,
            "Pocket DS Android/Linux shared game card\n\n"
            "Roms/ is the single shared ROM tree for Android Pegasus G and Linux ES-DE.\n"
            "Games are grouped by console; the 精选集 is a metadata-only view and does not duplicate ROMs.\n"
            "Frontend metadata is separate, and Android/Linux saves and states remain OS-specific.\n",
        )
        target_rows["PocketDS/README.txt"] = (readme.stat().st_size, readme)

        inventory_path = overlay / "PocketDS/Manifests/full-library-files.sha256"
        inventory_path.parent.mkdir(parents=True, exist_ok=True)
        total_bytes = sum(size for size, _path in target_rows.values())
        hashed_bytes = 0
        with inventory_path.open("w", encoding="utf-8") as stream:
            for index, (relative, (size, source_file)) in enumerate(
                sorted(target_rows.items()), start=1
            ):
                digest = sha256(source_file)
                stream.write(f"{digest}  {relative}\n")
                hashed_bytes += size
                if index % 1000 == 0 or hashed_bytes == total_bytes:
                    print(
                        f"hashed {index}/{len(target_rows)} files "
                        f"({hashed_bytes}/{total_bytes} bytes)",
                        file=sys.stderr,
                        flush=True,
                    )

        plan = {
            "schema": 1,
            "name": "Pocket DS full Android/Linux shared game library",
            "android_volume_id": args.android_volume_id,
            "source_manifest_sha256": args.expected_manifest_sha256,
            "source_inventory_sha256": args.expected_inventory_sha256,
            "source_inventory_files": len(rows),
            "source_inventory_bytes": sum(int(row["size"]) for row in rows),
            "ignored_unmanaged_source_files": ignored_files,
            "ignored_unmanaged_source_bytes": ignored_bytes,
            "rom_files": sum(len(files) for files in roms.values()),
            "platform_counts": platform_counts,
            "platforms": len(platform_counts),
            "featured_games": len(selected),
            "target_managed_files": len(target_rows),
            "target_managed_bytes": total_bytes,
            "target_inventory_sha256": sha256(inventory_path),
            "system_directories": {
                source_system: values[0] for source_system, values in SYSTEMS.items()
            },
        }
        manifest_target = overlay / "PocketDS/Manifests/full-library.json"
        write_text(
            manifest_target,
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"full shared library planning failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
