#!/usr/bin/env python3
"""Verify safe Pegasus-to-ES-DE staging and curated collection conversion."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
STAGER = ROOT / "scripts/pocketds-esde-library-stage.py"
SYSTEMS = ROOT / "components/emulation/es_systems.xml"
# Extension snapshot from every gamelist in the pinned 2026-08-29 v2 stage.
PINNED_GAMELIST_EXTENSIONS = {
    "arcade": {".zip"},
    "famicom": {".zip"},
    "gba": {".zip"},
    "gbc": {".gbc", ".zip"},
    "genesis": {".zip"},
    "mame": {".zip"},
    "n64": {".n64", ".rom", ".v64", ".z64"},
    "nds": {".nds"},
    "neogeo": {".zip"},
    "nes": {".nes", ".zip"},
    "pcengine": {".zip"},
    "psp": {".cso", ".iso", ".pbp"},
    "psx": {".img", ".pbp"},
    "sfc": {".sfc", ".smc", ".zip"},
}


with tempfile.TemporaryDirectory(prefix="pocketds-esde-stage-") as temporary:
    temp = Path(temporary)
    backup = temp / "backup"
    metadata = temp / "metadata"
    repaired = temp / "repaired"
    fixed_nds = temp / "nds-fixed"
    quarantine = temp / "quarantine"
    stage = temp / "stage"
    for system in (
        "famicom", "nes", "sfc", "gbc", "gba", "genesis", "pcengine",
        "n64", "nds", "psx", "psp", "neogeo", "arcade", "mame2003",
    ):
        (backup / system).mkdir(parents=True)

    n64_rom = backup / "n64/体育/Game.z64"
    n64_rom.parent.mkdir(parents=True)
    n64_rom.write_bytes(b"rom")
    (backup / "n64/images").mkdir()
    (backup / "n64/images/Game.png").write_bytes(b"png")
    (backup / "n64/images/Game.mp4").write_bytes(b"mp4")
    (backup / "psx/Disc.img").write_bytes(b"psx")
    fixed_nds.mkdir()
    (fixed_nds / "Handheld.nds").write_bytes(b"nds")
    quarantine.mkdir()
    (quarantine / "broken.corrupt.nds").write_bytes(b"bad")

    (metadata / "N64").mkdir(parents=True)
    (metadata / "N64/metadata.pegasus.txt").write_text(
        "collection: N64\nlaunch: android-command\n\n"
        "game: Native Name\nfile: 体育/Game.z64\n"
        "assets.box_front: images/Game.png\n"
        "assets.screenshot: images/Game.png\n"
        "assets.video: images/Game.mp4\n\n"
        "game: Escape\nfile: ../outside.z64\n",
        encoding="utf-8",
    )
    (metadata / "NDS").mkdir()
    (metadata / "NDS/metadata.pegasus.txt").write_text(
        "game: Handheld\nfile: Handheld.nds\n"
        "assets.box_front: media_repaired/art.png\n",
        encoding="utf-8",
    )
    (repaired / "NDS/media_repaired").mkdir(parents=True)
    (repaired / "NDS/media_repaired/art.png").write_bytes(b"art")
    for key in ("FC", "NES", "SFC", "GBC", "GBA", "MD", "PCE", "PSP", "NEOGEO", "ARCADE", "MAME2003"):
        (metadata / key).mkdir()
        (metadata / key / "metadata.pegasus.txt").write_text("", encoding="utf-8")
    (metadata / "PS1").mkdir()
    (metadata / "PS1/metadata.pegasus.txt").write_text(
        "game: Disc image\nfile: Disc.img\n",
        encoding="utf-8",
    )

    selection = temp / "selection.tsv"
    selection.write_text(
        "system\tdisplay\tsource_rel\n"
        "n64\tNative Name\tn64/体育/Game.z64\n"
        "nds\tHandheld\tnds/Handheld.zip\n",
        encoding="utf-8",
    )
    output = subprocess.run(
        [
            str(STAGER), "--backup-root", str(backup), "--metadata-root", str(metadata),
            "--repaired-media-root", str(repaired), "--nds-root", str(fixed_nds),
            "--selection", str(selection), "--quarantine-root", str(quarantine),
            "--stage", str(stage), "--mode", "copy",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    manifest = json.loads(output.stdout)
    assert manifest["totals"]["rom_files"] == 3
    assert manifest["totals"]["metadata_games"] == 3
    assert manifest["quarantine_files_excluded"] == 1
    inventory = [
        json.loads(line)
        for line in (stage / "files.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert manifest["inventory"]["files"] == len(inventory)
    assert {item["path"] for item in inventory} >= {
        "ROMs/n64/体育/Game.z64",
        "ROMs/nds/Handheld.nds",
        "ES-DE/collections/custom-精选集.cfg",
    }
    home = stage / "home"
    assert (home / "ROMs/n64/体育/Game.z64").read_bytes() == b"rom"
    assert (home / "ROMs/nds/Handheld.nds").read_bytes() == b"nds"
    assert (home / "ROMs/psx/Disc.img").read_bytes() == b"psx"
    assert not (home / "ROMs/nds/Handheld.zip").exists()
    gamelist = ET.parse(home / "ES-DE/gamelists/n64/gamelist.xml")
    assert gamelist.findtext("./game/path") == "./体育/Game.z64"
    assert gamelist.findtext("./game/name") == "Native Name"
    assert gamelist.find("./game/image") is None
    assert (home / "ES-DE/downloaded_media/n64/screenshots/体育/Game.png").is_file()
    assert (home / "ES-DE/downloaded_media/n64/videos/体育/Game.mp4").is_file()
    assert (home / "ES-DE/downloaded_media/nds/covers/Handheld.png").is_file()
    collection = (home / "ES-DE/collections/custom-精选集.cfg").read_text(encoding="utf-8")
    assert "%ROMPATH%/n64/体育/Game.z64" in collection
    assert "%ROMPATH%/nds/Handheld.nds" in collection
    assert "android-command" not in (home / "ES-DE/gamelists/n64/gamelist.xml").read_text(encoding="utf-8")

    configured = {
        node.findtext("name"): set((node.findtext("extension") or "").lower().split())
        for node in ET.parse(SYSTEMS).getroot().findall("system")
    }
    for system_name, extensions in PINNED_GAMELIST_EXTENSIONS.items():
        assert extensions <= configured[system_name]
    observed: dict[str, set[str]] = {}
    for gamelist_path in sorted((home / "ES-DE/gamelists").glob("*/gamelist.xml")):
        extensions = {
            Path(path.text.removeprefix("./")).suffix.lower()
            for path in ET.parse(gamelist_path).getroot().findall("./game/path")
            if path.text
        }
        observed[gamelist_path.parent.name] = extensions
        assert extensions <= configured[gamelist_path.parent.name]
    assert observed["psx"] == {".img"}

print("  [OK] ROMs, native ES-DE metadata/media and curated collection stage safely")
