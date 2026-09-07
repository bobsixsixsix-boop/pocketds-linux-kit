#!/usr/bin/env python3
"""Fail-closed tests for the exact-tree ES-DE import verifier."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
VERIFY = ROOT / "scripts/pocketds-esde-library-verify.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    home = root / "home"
    (home / "ROMs/n64").mkdir(parents=True)
    (home / "ES-DE/collections").mkdir(parents=True)
    (home / "ROMs/n64/Game.z64").write_bytes(b"rom")
    (home / "ES-DE/collections/custom-test.cfg").write_bytes(b"entry\n")
    inventory = root / "files.jsonl"
    rows = [
        {"path": "ES-DE/collections/custom-test.cfg", "size": 6},
        {"path": "ROMs/n64/Game.z64", "size": 3},
    ]
    inventory.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "inventory": {
                    "file": "files.jsonl",
                    "files": 2,
                    "logical_bytes": 9,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return home, manifest, inventory


def run(
    home: Path,
    manifest: Path,
    inventory: Path,
    *,
    manifest_sha256: str | None = None,
    inventory_sha256: str | None = None,
    mode: str = "fast",
    source_home: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        str(VERIFY),
        "--home",
        str(home),
        "--manifest",
        str(manifest),
        "--inventory",
        str(inventory),
        "--expected-manifest-sha256",
        manifest_sha256 or digest(manifest),
        "--expected-inventory-sha256",
        inventory_sha256 or digest(inventory),
        "--mode",
        mode,
    ]
    if source_home is not None:
        command += ["--source-home", str(source_home)]
    return subprocess.run(command, text=True, capture_output=True, check=False)


with tempfile.TemporaryDirectory(prefix="pocketds-esde-verify-") as temporary:
    root = Path(temporary)
    home, manifest, inventory = write_fixture(root)
    manifest_sha256 = digest(manifest)
    inventory_sha256 = digest(inventory)
    accepted = run(
        home,
        manifest,
        inventory,
        manifest_sha256=manifest_sha256,
        inventory_sha256=inventory_sha256,
    )
    assert accepted.returncode == 0, accepted.stderr
    report = json.loads(accepted.stdout)
    assert report == {
        "accepted": True,
        "content_bytes_read": 0,
        "content_digests_pinned": False,
        "extra_files_verified": True,
        "files": 2,
        "hardlinked_files": 0,
        "inventory_sha256": inventory_sha256,
        "logical_bytes": 9,
        "manifest_sha256": manifest_sha256,
        "metadata_identity_verified": True,
        "mode": "fast",
        "schema": 2,
        "trusted_source_pairwise_verified": False,
    }

    source_home = root / "source-home"
    shutil.copytree(home, source_home)
    assert run(home, manifest, inventory, mode="full").returncode != 0
    assert run(home, manifest, inventory, source_home=source_home).returncode != 0
    assert run(
        home, manifest, inventory, mode="full", source_home=home
    ).returncode != 0
    aliased_source = root / "aliased-source-home"
    shutil.copytree(home, aliased_source, copy_function=os.link)
    assert run(
        home, manifest, inventory, mode="full", source_home=aliased_source
    ).returncode != 0
    full = run(home, manifest, inventory, mode="full", source_home=source_home)
    assert full.returncode == 0, full.stderr
    full_report = json.loads(full.stdout)
    assert full_report["content_digests_pinned"] is False
    assert full_report["trusted_source_pairwise_verified"] is True
    assert full_report["content_bytes_read"] == 18
    assert full_report["mode"] == "full"

    (home / "ROMs/n64/Game.z64").write_bytes(b"bad")
    same_size_fast = run(home, manifest, inventory)
    assert same_size_fast.returncode == 0, same_size_fast.stderr
    assert run(
        home, manifest, inventory, mode="full", source_home=source_home
    ).returncode != 0
    (home / "ROMs/n64/Game.z64").write_bytes(b"rom")

    (home / "ROMs/n64/extra.z64").write_bytes(b"extra")
    assert run(home, manifest, inventory).returncode != 0
    (home / "ROMs/n64/extra.z64").unlink()
    (home / "ROMs/n64/empty-extra").mkdir()
    assert run(home, manifest, inventory).returncode != 0
    (home / "ROMs/n64/empty-extra").rmdir()

    assert run(
        home,
        manifest,
        inventory,
        manifest_sha256="0" * 64,
        inventory_sha256=inventory_sha256,
    ).returncode != 0
    assert run(
        home,
        manifest,
        inventory,
        manifest_sha256=manifest_sha256,
        inventory_sha256="0" * 64,
    ).returncode != 0

    (home / "ROMs/n64/Game.z64").write_bytes(b"bad-size")
    assert run(home, manifest, inventory).returncode != 0
    (home / "ROMs/n64/Game.z64").write_bytes(b"rom")

    duplicate = inventory.read_text(encoding="utf-8").splitlines()[0]
    inventory.write_text(duplicate + "\n" + duplicate + "\n", encoding="utf-8")
    assert run(home, manifest, inventory).returncode != 0

    home, manifest, inventory = write_fixture(root / "links")
    target = home / "ROMs/n64/Game.z64"
    target.unlink()
    target.symlink_to("/etc/passwd")
    assert run(home, manifest, inventory).returncode != 0

    home, manifest, inventory = write_fixture(root / "parent-link")
    outside = root / "outside-esde"
    shutil.move(home / "ES-DE", outside)
    (home / "ES-DE").symlink_to(outside, target_is_directory=True)
    assert run(home, manifest, inventory).returncode != 0

    home, manifest, inventory = write_fixture(root / "traversal")
    inventory.write_text('{"path":"../escape","size":1}\n', encoding="utf-8")
    assert run(home, manifest, inventory).returncode != 0

    home, manifest, inventory = write_fixture(root / "unmanaged-esde")
    inventory.write_text(
        '{"path":"ES-DE/settings/es_settings.xml","size":1}\n',
        encoding="utf-8",
    )
    assert run(home, manifest, inventory).returncode != 0

    home, manifest, inventory = write_fixture(root / "linked-inventory")
    linked_root = root / "linked-inventory-input"
    linked_root.mkdir()
    linked = linked_root / "files.jsonl"
    linked.symlink_to(inventory)
    assert run(home, manifest, linked).returncode != 0

print(
    "  [OK] ES-DE metadata/tree identity and trusted-source pairwise content verify"
)
