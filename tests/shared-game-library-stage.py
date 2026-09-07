#!/usr/bin/env python3
"""Verify the dual-OS TF-card library layout and path mapping."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/pocketds-shared-library-stage.py"


with tempfile.TemporaryDirectory(prefix="pocketds-shared-library-") as temporary:
    root = Path(temporary)
    curated = root / "curated"
    source_home = curated / "home"
    (curated / "manifest.json").parent.mkdir(parents=True)
    (curated / "manifest.json").write_text(
        '{"collection":{"file":"custom-精选集.cfg","games":2}}\n',
        encoding="utf-8",
    )
    (curated / "files.jsonl").write_text('{}\n', encoding="utf-8")
    roms = {
        "famicom/精选/One.zip": b"one",
        "arcade/tmnt.zip": b"tmnt",
        "nds/DS.nds": b"ds",
    }
    for relative, payload in roms.items():
        target = source_home / "ROMs" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    collection = source_home / "ES-DE/collections/custom-精选集.cfg"
    collection.parent.mkdir(parents=True)
    collection.write_text(
        "%ROMPATH%/famicom/精选/One.zip\n%ROMPATH%/arcade/tmnt.zip\n"
        "%ROMPATH%/nds/DS.nds\n",
        encoding="utf-8",
    )
    gamelist = source_home / "ES-DE/gamelists/famicom/gamelist.xml"
    gamelist.parent.mkdir(parents=True)
    gamelist.write_text("<gameList />\n", encoding="utf-8")

    media = root / "pegasus-media"
    (media / "famicom/images").mkdir(parents=True)
    (media / "famicom/images/One.png").write_bytes(b"cover")
    (media / "arcade/images").mkdir(parents=True)
    (media / "arcade/images/TMNT.png").write_bytes(b"arcade cover")
    (media / "arcade/videos").mkdir(parents=True)
    (media / "arcade/videos/TMNT.mp4").write_bytes(b"arcade video")
    metadata = root / "精选集.metadata.pegasus.txt"
    metadata.write_text(
        "collection: 精选集\nfiles:\n  ../ARCADE/tmnt.zip\n\n"
        "game: One\nfile: ../FC/精选/One.zip\n"
        "assets.box_front: media/famicom/images/One.png\n\n"
        "game: DS\nfile: ../NDS/DS.nds\n"
        "launch: am start --user 0\n"
        "  -n com.dsemu.drastic/.DraSticActivity\n"
        "  -a android.intent.action.VIEW\n"
        "  -d \"{file.uri}\"\n",
        encoding="utf-8",
    )
    system_metadata = root / "system-metadata"
    (system_metadata / "FC").mkdir(parents=True)
    (system_metadata / "FC/metadata.pegasus.txt").write_text(
        "collection: FC\nsort-by: 011\nextensions: zip\n"
        "launch: retroarch {file.path}\n\n"
        "game: Original One\nfile: 精选/One.zip\n",
        encoding="utf-8",
    )
    (system_metadata / "ARCADE").mkdir(parents=True)
    (system_metadata / "ARCADE/metadata.pegasus.txt").write_text(
        "collection: 热门动作街机\nsort-by: 001\nextensions: zip\n"
        "launch: fbneo {file.path}\n\n"
        "game: TMNT\nfile: tmnt.zip\n"
        "assets.box_front: unavailable/tmnt.png\n",
        encoding="utf-8",
    )
    (system_metadata / "NDS").mkdir(parents=True)
    (system_metadata / "NDS/metadata.pegasus.txt").write_text(
        "collection: NDS\nsort-by: 026\nextensions: nds\n"
        "launch: am start --user 0\n"
        "  -n com.dsemu.drastic/.DraSticActivity\n"
        "  -a android.intent.action.VIEW\n"
        "  -d \"{file.uri}\"\n\n"
        "game: DS\nfile: DS.nds\n",
        encoding="utf-8",
    )
    stage = root / "shared"
    result = subprocess.run(
        [
            str(BUILDER),
            "--curated-stage",
            str(curated),
            "--pegasus-metadata",
            str(metadata),
            "--pegasus-media",
            str(media),
            "--pegasus-system-metadata",
            str(system_metadata),
            "--android-volume-id",
            "ABCD-1234",
            "--stage",
            str(stage),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["rom_files"] == 3
    assert report["pegasus_entries"] == 3
    assert report["pegasus_category_entries"] == 3
    assert report["pegasus_category_counts"] == {"ARCADE": 1, "FC": 1, "NDS": 1}
    assert report["pegasus_collections"] == 4
    assert (stage / "Roms/FC/精选/One.zip").read_bytes() == b"one"
    assert (stage / "Roms/ARCADE/tmnt.zip").read_bytes() == b"tmnt"
    assert (stage / "Roms/NDS/DS.nds").read_bytes() == b"ds"
    assert (stage / "Roms/精选集/media/famicom/images/One.png").is_file()
    assert (stage / "PocketDS/Frontends/ES-DE/collections/custom-精选集.cfg").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "%ROMPATH%/FC/精选/One.zip",
        "%ROMPATH%/ARCADE/tmnt.zip",
        "%ROMPATH%/NDS/DS.nds",
    ]
    assert (stage / "Roms/FC/metadata.pegasus.txt").read_text(
        encoding="utf-8"
    ) == (
        "collection: FC\nshortname: FC\nsort-by: 011\n"
        "launch: retroarch {file.path}\nfiles:\n  精选/One.zip\n"
    )
    arcade_metadata = (stage / "Roms/ARCADE/metadata.pegasus.txt").read_text(
        encoding="utf-8"
    )
    assert "collection: ARCADE" in arcade_metadata
    assert "game: TMNT\nfile: tmnt.zip" in arcade_metadata
    assert "assets.box_front: ../精选集/media/arcade/images/TMNT.png" in arcade_metadata
    assert "assets.video: ../精选集/media/arcade/videos/TMNT.mp4" in arcade_metadata
    assert "unavailable/tmnt.png" not in arcade_metadata
    nds_category = (stage / "Roms/NDS/metadata.pegasus.txt").read_text(
        encoding="utf-8"
    )
    nds_curated = (stage / "Roms/精选集/metadata.pegasus.txt").read_text(
        encoding="utf-8"
    )
    expected_nds_uri = (
        "content://com.android.externalstorage.documents/"
        "tree/ABCD-1234%3ARoms%2FNDS/"
        "document/ABCD-1234%3ARoms%2FNDS%2FDS.nds"
    )
    assert "launch:" not in nds_category
    assert "me.magnum.melondualds.pocketds.POCKETDS_LAUNCH_ROM" in nds_curated
    assert "me.magnum.melonds.pocketds.PocketDsLaunchActivity" in nds_curated
    assert "--display 2" not in nds_curated
    assert f'--es uri "{expected_nds_uri}"' in nds_curated
    assert "com.dsemu.drastic" not in nds_curated
    assert "{file.uri}" not in nds_curated
    inventory = stage / "PocketDS/Manifests/files.sha256"
    assert hashlib.sha256(inventory.read_bytes()).hexdigest() == report["inventory_sha256"]
    listed = inventory.read_text(encoding="utf-8")
    assert "Roms/FC/精选/One.zip" in listed
    assert "Roms/精选集/metadata.pegasus.txt" in listed
    assert "Roms/FC/metadata.pegasus.txt" in listed

print("  [OK] shared TF library maps one ROM tree to Pegasus G and ES-DE")
