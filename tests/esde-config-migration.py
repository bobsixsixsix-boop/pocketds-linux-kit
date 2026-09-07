#!/usr/bin/env python3
"""Verify ES-DE 3.x path migration is preserving and fail-closed."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "components/emulation/pocketds-es-de-prepare.py"
MANAGED_SYSTEMS = ROOT / "components/emulation/es_systems.xml"
SHARED_SYSTEMS = ROOT / "components/emulation/es_systems-shared.xml"
XML_DECLARATION = re.compile(r"\A\ufeff?\s*<\?xml\s+[^?]*\?>", re.IGNORECASE)

SPEC = importlib.util.spec_from_file_location("pocketds_esde_prepare", HELPER)
assert SPEC is not None and SPEC.loader is not None
PREPARE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PREPARE
SPEC.loader.exec_module(PREPARE)

shared_root = ET.parse(SHARED_SYSTEMS).getroot()
shared_psx = next(
    system for system in shared_root.findall("system") if system.findtext("name") == "psx"
)
assert {".pbp", ".PBP"}.issubset(
    set((shared_psx.findtext("extension") or "").split())
)
shared_mame = next(
    system for system in shared_root.findall("system") if system.findtext("name") == "mame"
)
assert (shared_mame.findtext("platform") or "").split() == ["arcade"]


def settings_entries(path: Path) -> list[ET.Element]:
    content = path.read_text(encoding="utf-8")
    body = XML_DECLARATION.sub("", content, count=1)
    document = ET.fromstring(f"<document>{body}</document>")
    return [
        node
        for node in document.iter("string")
        if node.attrib.get("name") == "ROMDirectory"
    ]


def custom_collections(path: Path) -> list[ET.Element]:
    content = path.read_text(encoding="utf-8")
    body = XML_DECLARATION.sub("", content, count=1)
    document = ET.fromstring(f"<document>{body}</document>")
    return [
        node
        for node in document.iter("string")
        if node.attrib.get("name") == "CollectionSystemsCustom"
    ]


def command(home: Path, managed: Path = MANAGED_SYSTEMS) -> list[str]:
    return [
        sys.executable,
        str(HELPER),
        "--home",
        str(home),
        "--managed-systems",
        str(managed),
    ]


def run_prepare(
    home: Path,
    managed: Path = MANAGED_SYSTEMS,
    extra: tuple[str, ...] = (),
) -> dict[str, object]:
    result = subprocess.run(
        command(home, managed) + list(extra),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def run_failure(
    home: Path,
    managed: Path = MANAGED_SYSTEMS,
    extra: tuple[str, ...] = (),
) -> None:
    result = subprocess.run(
        command(home, managed) + list(extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert result.returncode != 0
    assert result.stdout == b""


def run_check_only(
    home: Path,
    managed: Path = MANAGED_SYSTEMS,
    extra: tuple[str, ...] = (),
) -> dict[str, object]:
    result = subprocess.run(
        command(home, managed) + list(extra) + ["--check-only"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(result.stdout)


with tempfile.TemporaryDirectory(prefix="pocketds-esde-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    settings.parent.mkdir(parents=True)
    original = (
        '<?xml version="1.0"?>\n'
        '<settings>\n'
        '  <!-- preserve this user note -->\n'
        '  <?pocketds-user keep?>\n'
        '  <bool name="UserChoice" value="true" />\n'
        '  <string name="ROMDirectory" value="" />\n'
        '</settings>\n'
    )
    settings.write_text(original, encoding="utf-8")

    first = run_prepare(home)
    assert first["settings_changed"] is True
    assert first["systems_changed"] is True
    assert first["rom_directory"] == str(home / "ROMs")
    assert first["required_rom_directory"] is None
    assert first["rom_directory_requirement_met"] is True
    migrated = settings.read_text(encoding="utf-8")
    assert "preserve this user note" in migrated
    assert "pocketds-user keep" in migrated
    assert '<bool name="UserChoice" value="true" />' in migrated
    entries = settings_entries(settings)
    assert len(entries) == 1
    assert entries[0].attrib["value"] == str(home / "ROMs")
    systems_path = home / "ES-DE/custom_systems/es_systems.xml"
    systems = systems_path.read_text(encoding="utf-8")
    assert "/home/pocketds" not in systems
    assert str(home / "ROMs/nes") in systems
    assert ET.parse(systems_path).getroot().tag == "systemList"
    assert not (home / "ROMs").exists(), "migration must not touch ROM content"
    backups = list(
        (home / ".local/state/pocketds-linux-kit/backups").glob(
            "*/ES-DE/settings/es_settings.xml"
        )
    )
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original

    second = run_prepare(home)
    assert second["settings_changed"] is False
    assert second["systems_changed"] is False

    settings.write_text(
        '<settings><string name="ROMDirectory" '
        'value="/user/chosen/roms" /></settings>\n',
        encoding="utf-8",
    )
    before = settings.read_bytes()
    third = run_prepare(home)
    assert third["settings_changed"] is False
    assert third["rom_directory"] == "/user/chosen/roms"
    assert settings.read_bytes() == before

    run_failure(
        home,
        extra=("--require-rom-directory", str(home / "ROMs")),
    )
    assert settings.read_bytes() == before


with tempfile.TemporaryDirectory(prefix="pocketds-esde-missing-rom-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '<settings><bool name="UserChoice" value="true" /></settings>\n',
        encoding="utf-8",
    )
    before = settings.read_bytes()
    check = run_check_only(home)
    assert check == {
        "check_only": True,
        "required_rom_directory": None,
        "rom_directory": str(home / "ROMs"),
        "rom_directory_requirement_met": True,
        "rom_directory_forced": False,
        "rom_content_touched": False,
        "settings_would_change": True,
        "systems_managed_migration": False,
        "systems_would_change": True,
    }
    assert settings.read_bytes() == before
    assert not (home / "ES-DE/custom_systems/es_systems.xml").exists()
    assert not (home / ".local/state/pocketds-linux-kit").exists()
    result = run_prepare(home)
    assert result["settings_changed"] is True
    entries = settings_entries(settings)
    assert len(entries) == 1
    assert entries[0].attrib["value"] == str(home / "ROMs")
    required = run_check_only(
        home,
        extra=("--require-rom-directory", str(home / "ROMs")),
    )
    assert required["rom_directory"] == str(home / "ROMs")
    assert required["required_rom_directory"] == str(home / "ROMs")
    assert required["rom_directory_requirement_met"] is True


with tempfile.TemporaryDirectory(prefix="pocketds-esde-force-rom-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '<settings><string name="ROMDirectory" value="/old/tree" />'
        '<bool name="UserChoice" value="true" /></settings>\n',
        encoding="utf-8",
    )
    shared_roms = home / "mounted-card/Roms"
    forced = run_prepare(
        home,
        extra=(
            "--set-rom-directory",
            str(shared_roms),
            "--require-rom-directory",
            str(shared_roms),
        ),
    )
    assert forced["settings_changed"] is True
    assert forced["rom_directory"] == str(shared_roms)
    assert forced["rom_directory_forced"] is True
    assert settings_entries(settings)[0].attrib["value"] == str(shared_roms)
    assert '<bool name="UserChoice" value="true" />' in settings.read_text(
        encoding="utf-8"
    )


with tempfile.TemporaryDirectory(prefix="pocketds-esde-shared-home-") as temp:
    root = Path(temp).resolve()
    home = root / "account"
    esde_home = root / "card/PocketDS/Frontends"
    home.mkdir(parents=True)
    (esde_home / "ES-DE/collections").mkdir(parents=True)
    collection = esde_home / "ES-DE/collections/custom-精选集.cfg"
    collection.write_text("keep-this-collection\n", encoding="utf-8")
    shared_roms = root / "card/Roms"
    result = run_prepare(
        home,
        extra=(
            "--esde-home",
            str(esde_home),
            "--force-managed-systems",
            "--set-rom-directory",
            str(shared_roms),
            "--require-rom-directory",
            str(shared_roms),
            "--enable-collection",
            "精选集",
            "--parse-gamelist-only",
        ),
    )
    settings = esde_home / "ES-DE/settings/es_settings.xml"
    systems = esde_home / "ES-DE/custom_systems/es_systems.xml"
    assert result["settings"] == str(settings)
    assert settings_entries(settings)[0].attrib["value"] == str(shared_roms)
    systems_text = systems.read_text(encoding="utf-8")
    assert str(home) in systems_text
    assert str(esde_home) not in systems_text
    assert collection.read_text(encoding="utf-8") == "keep-this-collection\n"
    assert not (esde_home / ".local").exists()

    updated_managed = root / "updated-es_systems.xml"
    updated_managed.write_text(
        MANAGED_SYSTEMS.read_text(encoding="utf-8").replace(
            "Nintendo Entertainment System", "Nintendo Entertainment System Updated", 1
        ),
        encoding="utf-8",
    )
    update = run_prepare(
        home,
        managed=updated_managed,
        extra=(
            "--esde-home",
            str(esde_home),
            "--force-managed-systems",
            "--set-rom-directory",
            str(shared_roms),
            "--require-rom-directory",
            str(shared_roms),
        ),
    )
    assert update["systems_changed"] is True
    assert "Nintendo Entertainment System Updated" in systems.read_text(
        encoding="utf-8"
    )


with tempfile.TemporaryDirectory(prefix="pocketds-esde-required-rom-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '<settings><string name="ROMDirectory" value="/wrong/tree" /></settings>\n',
        encoding="utf-8",
    )
    before = settings.read_bytes()
    run_failure(
        home,
        extra=("--require-rom-directory", str(home / "ROMs")),
    )
    assert settings.read_bytes() == before
    assert not (home / "ES-DE/custom_systems/es_systems.xml").exists()
    assert not (home / ".local/state/pocketds-linux-kit").exists()


with tempfile.TemporaryDirectory(prefix="pocketds-esde-special-home-") as temp:
    home = (Path(temp) / "user & handheld").resolve()
    home.mkdir()
    result = run_prepare(home)
    assert result["settings_changed"] is True
    settings = home / "ES-DE/settings/es_settings.xml"
    systems = home / "ES-DE/custom_systems/es_systems.xml"
    assert "&amp;" in settings.read_text(encoding="utf-8")
    assert "&amp;" in systems.read_text(encoding="utf-8")
    assert settings_entries(settings)[0].attrib["value"] == str(home / "ROMs")
    assert "pocketds-settings-document" not in settings.read_text(encoding="utf-8")
    assert str(home / "ROMs/nes") in [
        node.text for node in ET.parse(systems).getroot().findall("./system/path")
    ]


with tempfile.TemporaryDirectory(prefix="pocketds-esde-invalid-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    settings.parent.mkdir(parents=True)
    invalid_cases = (
        '<settings><string name="ROMDirectory" value="" />',
        '<settings><string name="ROMDirectory" value="" />'
        '<string name="ROMDirectory" value="/other" /></settings>',
        '<settings><string name="ROMDirectory" value="" /></settings>'
        '<string name="ROMDirectory" value="/other" />',
        '<!DOCTYPE settings><settings></settings>',
        '<settings></settings><unsupported value="true" />',
    )
    for content in invalid_cases:
        settings.write_text(content, encoding="utf-8")
        before = settings.read_bytes()
        run_failure(home)
        assert settings.read_bytes() == before
        assert not (home / "ES-DE/custom_systems/es_systems.xml").exists()


with tempfile.TemporaryDirectory(prefix="pocketds-esde-flat-native-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    settings.parent.mkdir(parents=True)
    original = (
        '<?xml version="1.0"?>\n'
        '<bool name="ShowHiddenFiles" value="true" />\n'
        f'<string name="ROMDirectory" value="{home / "ROMs"}" />\n'
    )
    settings.write_text(original, encoding="utf-8")
    report = run_check_only(home)
    assert report["settings_would_change"] is False
    assert report["systems_would_change"] is True
    assert settings.read_text(encoding="utf-8") == original


with tempfile.TemporaryDirectory(prefix="pocketds-esde-collection-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '<settings><string name="ROMDirectory" value="/user/roms" />'
        '<string name="CollectionSystemsCustom" value="动作" /></settings>\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        command(home)
        + ["--enable-collection", "精选集", "--parse-gamelist-only"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(result.stdout)["settings_changed"] is True
    entries = custom_collections(settings)
    assert len(entries) == 1
    assert entries[0].attrib["value"] == "动作,精选集"
    assert '<bool name="ParseGamelistOnly" value="true" />' in settings.read_text(
        encoding="utf-8"
    )
    second = subprocess.run(
        command(home)
        + ["--enable-collection", "精选集", "--parse-gamelist-only"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(second.stdout)["settings_changed"] is False
    invalid = subprocess.run(
        command(home) + ["--enable-collection", "bad,name"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert invalid.returncode != 0
    assert custom_collections(settings)[0].attrib["value"] == "动作,精选集"


with tempfile.TemporaryDirectory(prefix="pocketds-esde-invalid-systems-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    systems = home / "ES-DE/custom_systems/es_systems.xml"
    systems.parent.mkdir(parents=True)
    systems.write_text("<systemList>", encoding="utf-8")
    before = systems.read_bytes()
    run_failure(home)
    assert systems.read_bytes() == before
    assert not settings.exists(), "all inputs must preflight before any write"


with tempfile.TemporaryDirectory(prefix="pocketds-esde-symlink-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    settings.parent.mkdir(parents=True)
    target = home / "outside.xml"
    target.write_text("<settings></settings>\n", encoding="utf-8")
    settings.symlink_to(target)
    before = target.read_bytes()
    run_failure(home)
    assert settings.is_symlink()
    assert target.read_bytes() == before


with tempfile.TemporaryDirectory(prefix="pocketds-esde-parent-symlink-") as temp:
    home = Path(temp).resolve()
    outside = home / "outside-esde"
    outside.mkdir()
    (home / "ES-DE").symlink_to(outside, target_is_directory=True)
    run_failure(home)
    assert not list(outside.iterdir())
    assert not (home / ".local/state/pocketds-linux-kit").exists()


with tempfile.TemporaryDirectory(prefix="pocketds-esde-hardlink-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    settings.parent.mkdir(parents=True)
    target = home / "outside.xml"
    target.write_text("<settings></settings>\n", encoding="utf-8")
    os.link(target, settings)
    before = target.read_bytes()
    run_failure(home)
    assert target.read_bytes() == before


with tempfile.TemporaryDirectory(prefix="pocketds-esde-oversized-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    settings.parent.mkdir(parents=True)
    with settings.open("wb") as stream:
        stream.truncate(1024 * 1024 + 1)
    run_failure(home)
    assert settings.stat().st_size == 1024 * 1024 + 1


with tempfile.TemporaryDirectory(prefix="pocketds-esde-managed-upgrade-") as temp:
    root = Path(temp).resolve()
    old_managed = root / "old-es_systems.xml"
    old_managed.write_text(
        MANAGED_SYSTEMS.read_text(encoding="utf-8").replace(
            ".cue .chd .m3u .pbp .iso .ccd .img .zip .7z",
            ".cue .chd .m3u .pbp .iso .ccd .zip .7z",
        ),
        encoding="utf-8",
    )

    managed_home = root / "managed-home"
    managed_home.mkdir()
    run_prepare(managed_home, old_managed)
    managed_systems = managed_home / "ES-DE/custom_systems/es_systems.xml"
    assert ".ccd .zip" in managed_systems.read_text(encoding="utf-8")
    upgraded = run_prepare(
        managed_home,
        extra=(
            "--previous-managed-systems",
            str(old_managed),
            "--require-rom-directory",
            str(managed_home / "ROMs"),
        ),
    )
    assert upgraded["systems_changed"] is True
    assert upgraded["systems_managed_migration"] is True
    assert ".ccd .img .zip" in managed_systems.read_text(encoding="utf-8")

    raw_home = root / "raw-managed-home"
    raw_settings = raw_home / "ES-DE/settings/es_settings.xml"
    raw_systems = raw_home / "ES-DE/custom_systems/es_systems.xml"
    raw_settings.parent.mkdir(parents=True)
    raw_systems.parent.mkdir(parents=True)
    raw_settings.write_text(
        f'<settings><string name="ROMDirectory" value="{raw_home / "ROMs"}" /></settings>\n',
        encoding="utf-8",
    )
    raw_systems.write_bytes(old_managed.read_bytes())
    raw_upgrade = run_prepare(
        raw_home,
        extra=(
            "--previous-managed-systems",
            str(old_managed),
            "--require-rom-directory",
            str(raw_home / "ROMs"),
        ),
    )
    assert raw_upgrade["systems_managed_migration"] is True
    assert ".ccd .img .zip" in raw_systems.read_text(encoding="utf-8")

    custom_home = root / "custom-home"
    custom_home.mkdir()
    run_prepare(custom_home, old_managed)
    custom_systems = custom_home / "ES-DE/custom_systems/es_systems.xml"
    customized = custom_systems.read_text(encoding="utf-8").replace(
        "</systemList>", "  <!-- user customization -->\n</systemList>"
    )
    custom_systems.write_text(customized, encoding="utf-8")
    preserved = run_prepare(
        custom_home,
        extra=(
            "--previous-managed-systems",
            str(old_managed),
            "--require-rom-directory",
            str(custom_home / "ROMs"),
        ),
    )
    assert preserved["systems_changed"] is False
    assert preserved["systems_managed_migration"] is False
    assert custom_systems.read_text(encoding="utf-8") == customized


with tempfile.TemporaryDirectory(prefix="pocketds-esde-transaction-") as temp:
    home = Path(temp).resolve()
    settings = home / "ES-DE/settings/es_settings.xml"
    systems = home / "ES-DE/custom_systems/es_systems.xml"
    settings.parent.mkdir(parents=True)
    original = '<settings><string name="ROMDirectory" value="" /></settings>\n'
    settings.write_text(original, encoding="utf-8")
    settings_snapshot = PREPARE.snapshot_text(settings, home, "ES-DE settings")
    systems_snapshot = PREPARE.snapshot_text(systems, home, "ES-DE custom systems")
    settings_plan, _rom = PREPARE.plan_settings(
        settings_snapshot, home / "ROMs", [], False
    )
    systems_plan, _migrated = PREPARE.plan_systems(
        systems_snapshot, MANAGED_SYSTEMS, None, home / "missing.xml", home
    )
    real_atomic_write = PREPARE.atomic_write
    failed = False

    def fail_systems_once(path: Path, content: str, mode: int = 0o600) -> None:
        global failed
        if path == systems and not failed:
            failed = True
            raise OSError("injected systems write failure")
        real_atomic_write(path, content, mode)

    PREPARE.atomic_write = fail_systems_once
    try:
        try:
            PREPARE.apply_transaction(
                (
                    (settings_plan, "ES-DE settings"),
                    (systems_plan, "ES-DE custom systems"),
                ),
                home,
                home / "backups/transaction",
                home / "ROMs",
                home / "ROMs",
            )
        except OSError as error:
            assert "injected systems write failure" in str(error)
        else:
            raise AssertionError("injected second-file failure was accepted")
    finally:
        PREPARE.atomic_write = real_atomic_write
    assert settings.read_text(encoding="utf-8") == original
    assert not systems.exists()

    settings_snapshot = PREPARE.snapshot_text(settings, home, "ES-DE settings")
    systems_snapshot = PREPARE.snapshot_text(systems, home, "ES-DE custom systems")
    settings_plan, _rom = PREPARE.plan_settings(
        settings_snapshot, home / "ROMs", [], False
    )
    systems_plan, _migrated = PREPARE.plan_systems(
        systems_snapshot, MANAGED_SYSTEMS, None, home / "missing.xml", home
    )
    concurrent = (
        '<settings><string name="ROMDirectory" value="/concurrent" /></settings>\n'
    )
    settings.write_text(concurrent, encoding="utf-8")
    try:
        PREPARE.apply_transaction(
            (
                (settings_plan, "ES-DE settings"),
                (systems_plan, "ES-DE custom systems"),
            ),
            home,
            home / "backups/cas",
            home / "ROMs",
            home / "ROMs",
        )
    except RuntimeError as error:
        assert "changed after preflight" in str(error)
    else:
        raise AssertionError("stale preflight snapshot was accepted")
    assert settings.read_text(encoding="utf-8") == concurrent
    assert not systems.exists()


print("  [OK] ES-DE migration is valid, preserving, bounded and fail-closed")
