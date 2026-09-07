#!/usr/bin/env python3
"""Verify the full Android/Linux library overlay on a bounded fixture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
PLANNER = ROOT / "scripts/pocketds-full-library-overlay.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


with tempfile.TemporaryDirectory(prefix="pocketds-full-library-") as temporary:
    temp = Path(temporary)
    source = temp / "source"
    home = source / "home"
    metadata = temp / "metadata"
    output = temp / "output"

    files = {
        "ROMs/famicom/经典/游戏.zip": b"fc-rom",
        "ROMs/nds/马里奥.nds": b"nds-rom",
        "ES-DE/gamelists/famicom/gamelist.xml": (
            b"<gameList><game><path>./\xe7\xbb\x8f\xe5\x85\xb8/\xe6\xb8\xb8\xe6\x88\x8f.zip</path>"
            b"<name>\xe6\xb8\xb8\xe6\x88\x8f</name></game></gameList>"
        ),
        "ES-DE/gamelists/nds/gamelist.xml": (
            b"<gameList><game><path>./\xe9\xa9\xac\xe9\x87\x8c\xe5\xa5\xa5.nds</path>"
            b"<name>\xe9\xa9\xac\xe9\x87\x8c\xe5\xa5\xa5</name></game></gameList>"
        ),
        "ES-DE/downloaded_media/famicom/covers/经典/游戏.png": b"fc-cover",
        "ES-DE/downloaded_media/famicom/screenshots/经典/游戏.png": b"fc-shot",
        "ES-DE/downloaded_media/nds/covers/马里奥.png": b"nds-cover",
        "ES-DE/downloaded_media/nds/screenshots/马里奥.png": b"nds-shot",
        "ES-DE/downloaded_media/nds/videos/马里奥.mp4": b"nds-video",
        "ES-DE/collections/custom-精选集.cfg": (
            "%ROMPATH%/famicom/经典/游戏.zip\n"
            "%ROMPATH%/nds/马里奥.nds\n"
        ).encode(),
    }
    for relative, payload in files.items():
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    # Files added after the pinned inventory must never leak into the transfer plan.
    (home / "ES-DE/.DS_Store").write_bytes(b"finder-junk")
    rows = [
        {"path": relative, "size": len(payload)}
        for relative, payload in sorted(files.items())
    ]
    inventory = source / "files.jsonl"
    inventory.parent.mkdir(parents=True, exist_ok=True)
    inventory.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    manifest = source / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "inventory": {
                    "files": len(rows),
                    "logical_bytes": sum(row["size"] for row in rows),
                },
                "collection": {"name": "精选集", "games": 2, "missing": 0},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    (metadata / "FC").mkdir(parents=True)
    (metadata / "FC/metadata.pegasus.txt").write_text(
        "collection: FC\nshortname: FC\nsort-by: 011\n"
        "launch: am start --user 0\n  -n retroarch/.Main\n\n"
        "game: 游戏\nfile: 经典/游戏.zip\n"
        "assets.box_front: ignored/source/path.png\n"
        "assets.screenshot: ignored/source/path.png\n",
        encoding="utf-8",
    )
    (metadata / "NDS").mkdir()
    (metadata / "NDS/metadata.pegasus.txt").write_text(
        "collection: NDS\nshortname: NDS\n"
        "launch: am start --user 0\n  -n com.dsemu.drastic/.DraSticActivity\n\n"
        "game: 马里奥\nfile: 马里奥.nds\n"
        "assets.box_front: ignored/source/path.png\n"
        "assets.screenshot: ignored/source/path.png\n"
        "assets.video: ignored/source/path.mp4\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(PLANNER),
            "--source-stage", str(source),
            "--pegasus-system-metadata", str(metadata),
            "--android-volume-id", "9C33-6BBD",
            "--expected-manifest-sha256", digest(manifest),
            "--expected-inventory-sha256", digest(inventory),
            "--output", str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    plan = json.loads(result.stdout)
    overlay = output / "overlay"
    assert plan["rom_files"] == 2
    assert plan["platform_counts"] == {"FC": 1, "NDS": 1}
    assert plan["featured_games"] == 2
    assert plan["ignored_unmanaged_source_files"] == 1
    assert plan["ignored_unmanaged_source_bytes"] == len(b"finder-junk")

    fc = (overlay / "Roms/FC/metadata.pegasus.txt").read_text(encoding="utf-8")
    nds = (overlay / "Roms/NDS/metadata.pegasus.txt").read_text(encoding="utf-8")
    featured = (overlay / "Roms/精选集/metadata.pegasus.txt").read_text(
        encoding="utf-8"
    )
    collection = (
        overlay / "PocketDS/Frontends/ES-DE/collections/custom-精选集.cfg"
    ).read_text(encoding="utf-8")
    assert "collection: FC · 红白机" in fc
    assert "retroarch/.Main" in fc
    assert (
        "../../PocketDS/Frontends/ES-DE/downloaded_media/"
        "famicom/covers/经典/游戏.png"
    ) in fc
    assert "com.dsemu.drastic" not in nds
    assert "me.magnum.melondualds.pocketds.POCKETDS_LAUNCH_ROM" in nds
    assert "9C33-6BBD%3ARoms%2FNDS%2F%E9%A9%AC%E9%87%8C%E5%A5%A5.nds" in nds
    assert "summary: 2 款中文热门精选" in featured
    assert "  ../FC/经典/游戏.zip" in featured
    assert "  ../NDS/马里奥.nds" in featured
    assert collection.splitlines() == [
        "%ROMPATH%/FC/经典/游戏.zip",
        "%ROMPATH%/NDS/马里奥.nds",
    ]

    assert (output / "transfer/roms/famicom.files").read_text() == "经典/游戏.zip\n"
    assert (output / "transfer/roms/nds.files").read_text() == "马里奥.nds\n"
    assert ".DS_Store" not in (output / "transfer/esde.files").read_text()
    checksums = overlay / "PocketDS/Manifests/full-library-files.sha256"
    assert digest(checksums) == plan["target_inventory_sha256"]
    assert len(checksums.read_text().splitlines()) == plan["target_managed_files"]

print("  [OK] full dual-OS library paths, launchers and pinned inventory")
