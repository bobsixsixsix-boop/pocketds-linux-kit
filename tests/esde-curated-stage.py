#!/usr/bin/env python3
"""Smoke-test the curated ES-DE stage builder."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "scripts/pocketds-esde-curated-stage.py"


def write_gamelist(path: Path, games: list[tuple[str, str]]) -> None:
    root = ET.Element("gameList")
    for rom_path, name in games:
        game = ET.SubElement(root, "game")
        ET.SubElement(game, "path").text = "./" + rom_path
        ET.SubElement(game, "name").text = name
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


with tempfile.TemporaryDirectory(prefix="pocketds-esde-curated-") as temporary:
    root = Path(temporary)
    source = root / "source"
    home = source / "home"
    target = root / "target"
    (source / "manifest.json").parent.mkdir(parents=True)
    (source / "manifest.json").write_text('{"schema":1}\n', encoding="utf-8")

    roms = {
        "famicom/精选/Game One.zip": b"one",
        "famicom/Other.zip": b"other",
        "mame2003/tmnt.zip": b"turtles",
        "neogeo/neogeo.zip": b"bios",
    }
    for relative, payload in roms.items():
        path = home / "ROMs" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    write_gamelist(
        home / "ES-DE/gamelists/famicom/gamelist.xml",
        [("精选/Game One.zip", "One"), ("Other.zip", "Other")],
    )
    write_gamelist(
        home / "ES-DE/gamelists/mame/gamelist.xml",
        [("tmnt.zip", "TMNT")],
    )
    cover = home / "ES-DE/downloaded_media/famicom/covers/精选/Game One.png"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(b"cover")
    unselected_cover = home / "ES-DE/downloaded_media/famicom/covers/Other.png"
    unselected_cover.write_bytes(b"other-cover")
    collection = home / "ES-DE/collections/custom-精选集.cfg"
    collection.parent.mkdir(parents=True)
    collection.write_text(
        "%ROMPATH%/famicom/精选/Game One.zip\n"
        "%ROMPATH%/mame2003/tmnt.zip\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            str(BUILDER),
            "--source-stage",
            str(source),
            "--support-rom",
            "neogeo/neogeo.zip",
            "--stage",
            str(target),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    manifest = json.loads(completed.stdout)
    assert manifest["collection"]["games"] == 2
    assert manifest["support_roms"] == 1
    assert manifest["totals"]["rom_files"] == 3
    assert manifest["totals"]["metadata_games"] == 2
    assert manifest["totals"]["media_files"] == 1

    curated = target / "home"
    assert (curated / "ROMs/famicom/精选/Game One.zip").read_bytes() == b"one"
    assert (curated / "ROMs/mame2003/tmnt.zip").read_bytes() == b"turtles"
    assert (curated / "ROMs/neogeo/neogeo.zip").read_bytes() == b"bios"
    assert not (curated / "ROMs/famicom/Other.zip").exists()
    assert (curated / "ES-DE/downloaded_media/famicom/covers/精选/Game One.png").is_file()
    assert not (curated / "ES-DE/downloaded_media/famicom/covers/Other.png").exists()

    famicom = ET.parse(curated / "ES-DE/gamelists/famicom/gamelist.xml").getroot()
    mame = ET.parse(curated / "ES-DE/gamelists/mame/gamelist.xml").getroot()
    assert [game.findtext("name") for game in famicom.findall("game")] == ["One"]
    assert [game.findtext("name") for game in mame.findall("game")] == ["TMNT"]
    assert (curated / "ES-DE/collections/custom-精选集.cfg").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "%ROMPATH%/famicom/精选/Game One.zip",
        "%ROMPATH%/mame2003/tmnt.zip",
    ]

print("  [OK] curated stage contains only selected games, metadata, media and support ROMs")
