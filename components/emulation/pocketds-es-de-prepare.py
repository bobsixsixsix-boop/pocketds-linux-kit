#!/usr/bin/env python3
"""Prepare ES-DE 3.x paths without overwriting user-managed settings or ROMs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import xml.etree.ElementTree as ET


MAX_CONFIG_BYTES = 1024 * 1024
FORBIDDEN_XML_DECLARATIONS = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
XML_DECLARATION = re.compile(r"\A\ufeff?\s*<\?xml\s+[^?]*\?>", re.IGNORECASE)
SETTING_TYPES = {"bool", "int", "float", "string"}
FORBIDDEN_COLLECTION_NAME = re.compile(r'[*",./:;<>\\|]|[\r\n]')


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    exists: bool
    content: str | None
    mode: int
    identity: tuple[int, int, int, int, int] | None


@dataclass(frozen=True)
class FilePlan:
    snapshot: FileSnapshot
    content: str | None
    mode: int
    changed: bool


def stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def validate_parent_chain(path: Path, home: Path, label: str) -> None:
    try:
        relative = path.relative_to(home)
    except ValueError as exc:
        raise ValueError(f"{label} is outside the selected home") from exc
    current = home
    for part in relative.parts[:-1]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"{label} has an unsafe parent directory")


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def backup(path: Path, home: Path, backup_root: Path) -> Path | None:
    if not path.exists():
        return None
    try:
        relative = path.relative_to(home)
    except ValueError:
        relative = Path(path.name)
    target = backup_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise RuntimeError("refusing to replace an existing ES-DE backup")
    shutil.copy2(path, target)
    return target


def read_regular_text(path: Path, label: str) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} is unsafe or unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_CONFIG_BYTES
        ):
            raise ValueError(f"{label} is unsafe or oversized")
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65536, remaining))
            if not block:
                raise ValueError(f"{label} changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ValueError(f"{label} grew while reading")
        try:
            text = bytes(content).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{label} is not UTF-8") from exc
        return text, stat.S_IMODE(metadata.st_mode)
    finally:
        os.close(descriptor)


def snapshot_text(path: Path, home: Path, label: str) -> FileSnapshot:
    validate_parent_chain(path, home, label)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return FileSnapshot(path, False, None, 0o600, None)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > MAX_CONFIG_BYTES
    ):
        raise ValueError(f"{label} is unsafe or oversized")
    content, mode = read_regular_text(path, label)
    try:
        after = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} changed while reading") from exc
    if stat_identity(before) != stat_identity(after):
        raise ValueError(f"{label} changed while reading")
    return FileSnapshot(path, True, content, mode, stat_identity(after))


def assert_snapshot_current(snapshot: FileSnapshot, home: Path, label: str) -> None:
    validate_parent_chain(snapshot.path, home, label)
    try:
        metadata = snapshot.path.lstat()
    except FileNotFoundError:
        if snapshot.exists:
            raise RuntimeError(f"{label} changed after preflight")
        return
    if not snapshot.exists or stat_identity(metadata) != snapshot.identity:
        raise RuntimeError(f"{label} changed after preflight")
    content, mode = read_regular_text(snapshot.path, label)
    if content != snapshot.content or mode != snapshot.mode:
        raise RuntimeError(f"{label} changed after preflight")


def parse_xml(content: str, expected_root: str, label: str) -> ET.Element:
    if FORBIDDEN_XML_DECLARATIONS.search(content):
        raise ValueError(f"{label} contains a forbidden XML declaration")
    try:
        parser = ET.XMLParser(
            target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
        )
        root = ET.fromstring(content, parser=parser)
    except ET.ParseError as exc:
        raise ValueError(f"{label} is malformed XML") from exc
    if root.tag != expected_root:
        raise ValueError(f"{label} has an unexpected root element")
    return root


def parse_settings(content: str) -> ET.Element:
    if FORBIDDEN_XML_DECLARATIONS.search(content):
        raise ValueError("ES-DE settings contains a forbidden XML declaration")
    body = XML_DECLARATION.sub("", content, count=1)
    wrapped = f"<pocketds-settings-document>{body}</pocketds-settings-document>"
    try:
        parser = ET.XMLParser(
            target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
        )
        document = ET.fromstring(wrapped, parser=parser)
    except ET.ParseError as exc:
        raise ValueError("ES-DE settings is malformed XML") from exc

    settings_roots = []
    for node in document:
        if not isinstance(node.tag, str):
            continue
        if node.tag == "settings":
            settings_roots.append(node)
            for child in node:
                if isinstance(child.tag, str) and child.tag not in SETTING_TYPES:
                    raise ValueError("ES-DE settings has an unsupported wrapped element")
        elif node.tag not in SETTING_TYPES:
            raise ValueError("ES-DE settings has an unsupported top-level element")
    if len(settings_roots) > 1:
        raise ValueError("ES-DE settings has multiple settings roots")
    return document


def serialize_xml(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return '<?xml version="1.0"?>\n' + ET.tostring(
        root, encoding="unicode", short_empty_elements=True
    ) + "\n"


def serialize_settings(document: ET.Element) -> str:
    ET.indent(document, space="  ")
    body = "".join(
        ET.tostring(node, encoding="unicode", short_empty_elements=True)
        for node in document
    )
    return '<?xml version="1.0"?>\n' + body.rstrip() + "\n"


def rom_entries(document: ET.Element) -> list[ET.Element]:
    return [
        node
        for node in document.iter("string")
        if node.attrib.get("name") == "ROMDirectory"
    ]


def custom_collection_entries(document: ET.Element) -> list[ET.Element]:
    return [
        node
        for node in document.iter("string")
        if node.attrib.get("name") == "CollectionSystemsCustom"
    ]


def parse_gamelist_only_entries(document: ET.Element) -> list[ET.Element]:
    return [
        node
        for node in document.iter("bool")
        if node.attrib.get("name") == "ParseGamelistOnly"
    ]


def validate_collection_names(names: list[str]) -> list[str]:
    normalized: list[str] = []
    for name in names:
        value = name.strip()
        if not value or len(value) > 128 or FORBIDDEN_COLLECTION_NAME.search(value):
            raise ValueError("invalid ES-DE custom collection name")
        if value not in normalized:
            normalized.append(value)
    return normalized


def settings_insert_parent(document: ET.Element) -> ET.Element:
    for node in document:
        if node.tag == "settings":
            return node
    return document


def systems_source(managed: Path | None, legacy: Path) -> Path:
    if managed is not None and (managed.exists() or managed.is_symlink()):
        return managed
    return legacy


def transformed_systems_content(content: str, home: Path) -> str:
    root = parse_xml(content, "systemList", "ES-DE systems source")
    for node in root.iter():
        if node.text:
            node.text = node.text.replace("/home/pocketds", str(home))
    return serialize_xml(root)


def exact_previous_managed_systems(
    content: str, previous_source_content: str, home: Path
) -> bool:
    # Some earlier installers copied the source byte-for-byte while later
    # helpers emitted its home-transformed canonical XML.  Both are known
    # managed representations, but matching remains exact; any user edit is
    # preserved.
    return content in {
        previous_source_content,
        transformed_systems_content(previous_source_content, home),
    }


def plan_settings(
    snapshot: FileSnapshot,
    rom_directory: Path,
    enable_collections: list[str],
    parse_gamelist_only: bool,
    force_rom_directory: bool = False,
) -> tuple[FilePlan, str]:
    if snapshot.exists:
        assert snapshot.content is not None
        document = parse_settings(snapshot.content)
    else:
        document = ET.Element("pocketds-settings-document")

    entries = rom_entries(document)
    if len(entries) > 1:
        raise ValueError("ES-DE settings has duplicate ROMDirectory entries")
    effective_rom_directory = str(rom_directory)
    changed = False
    if force_rom_directory and entries:
        if entries[0].attrib.get("value") != effective_rom_directory:
            entries[0].set("value", effective_rom_directory)
            changed = True
    elif entries and entries[0].attrib.get("value"):
        effective_rom_directory = entries[0].attrib["value"]
    elif entries:
        entries[0].set("value", effective_rom_directory)
        changed = True
    else:
        ET.SubElement(
            settings_insert_parent(document),
            "string",
            {"name": "ROMDirectory", "value": effective_rom_directory},
        )
        changed = True

    custom_entries = custom_collection_entries(document)
    if len(custom_entries) > 1:
        raise ValueError("ES-DE settings has duplicate custom collection entries")
    configured = [
        item
        for item in (
            custom_entries[0].attrib.get("value", "").split(",")
            if custom_entries
            else []
        )
        if item
    ]
    merged = configured + [name for name in enable_collections if name not in configured]
    if merged != configured:
        if custom_entries:
            custom_entries[0].set("value", ",".join(merged))
        else:
            ET.SubElement(
                settings_insert_parent(document),
                "string",
                {"name": "CollectionSystemsCustom", "value": ",".join(merged)},
            )
        changed = True

    parse_entries = parse_gamelist_only_entries(document)
    if len(parse_entries) > 1:
        raise ValueError("ES-DE settings has duplicate gamelist-only entries")
    if parse_gamelist_only and (
        not parse_entries or parse_entries[0].attrib.get("value") != "true"
    ):
        if parse_entries:
            parse_entries[0].set("value", "true")
        else:
            ET.SubElement(
                settings_insert_parent(document),
                "bool",
                {"name": "ParseGamelistOnly", "value": "true"},
            )
        changed = True

    content = serialize_settings(document) if changed else snapshot.content
    return FilePlan(snapshot, content, snapshot.mode, changed), effective_rom_directory


def plan_systems(
    snapshot: FileSnapshot,
    managed: Path | None,
    previous_managed: Path | None,
    legacy: Path,
    home: Path,
    force_managed: bool = False,
) -> tuple[FilePlan, bool]:
    source = systems_source(managed, legacy)
    source_content = None
    if source.exists() or source.is_symlink():
        source_content, _source_mode = read_regular_text(source, "ES-DE systems source")
        transformed_systems_content(source_content, home)

    previous_source_content = None
    if previous_managed is not None and (
        previous_managed.exists() or previous_managed.is_symlink()
    ):
        previous_source_content, _previous_mode = read_regular_text(
            previous_managed, "previous managed ES-DE systems source"
        )
        transformed_systems_content(previous_source_content, home)

    migrated = False
    if snapshot.exists and snapshot.content:
        parse_xml(snapshot.content, "systemList", "ES-DE custom systems")
        if not force_managed and (
            previous_source_content is None
            or not exact_previous_managed_systems(
                snapshot.content, previous_source_content, home
            )
        ):
            return FilePlan(snapshot, snapshot.content, snapshot.mode, False), False
        if source_content is None:
            raise ValueError("managed systems source is required for legacy migration")
        migrated = True
    elif source_content is None:
        return FilePlan(snapshot, snapshot.content, snapshot.mode, False), False

    assert source_content is not None
    content = transformed_systems_content(source_content, home)
    changed = content != snapshot.content
    return FilePlan(snapshot, content, 0o600, changed), migrated and changed


def effective_rom_directory(content: str, default: Path) -> str:
    document = parse_settings(content)
    entries = rom_entries(document)
    if len(entries) != 1 or not entries[0].attrib.get("value"):
        raise RuntimeError("ES-DE settings has no effective ROMDirectory after write")
    return entries[0].attrib["value"] or str(default)


def restore_snapshot(snapshot: FileSnapshot, home: Path, label: str) -> None:
    validate_parent_chain(snapshot.path, home, label)
    if snapshot.exists:
        assert snapshot.content is not None
        atomic_write(snapshot.path, snapshot.content, snapshot.mode)
    elif snapshot.path.exists() or snapshot.path.is_symlink():
        snapshot.path.unlink()


def apply_transaction(
    plans: tuple[tuple[FilePlan, str], ...],
    home: Path,
    backup_root: Path,
    required_rom_directory: Path | None,
    rom_directory: Path,
) -> str:
    for plan, label in plans:
        assert_snapshot_current(plan.snapshot, home, label)
    for plan, _label in plans:
        if plan.changed and plan.snapshot.exists:
            backup(plan.snapshot.path, home, backup_root)
    # Backup I/O is outside the write transaction but can take time.  Recheck
    # both inputs immediately before the first replacement.
    for plan, label in plans:
        assert_snapshot_current(plan.snapshot, home, label)

    changed: list[tuple[FilePlan, str]] = []
    try:
        for plan, label in plans:
            if not plan.changed:
                continue
            assert plan.content is not None
            validate_parent_chain(plan.snapshot.path, home, label)
            changed.append((plan, label))
            atomic_write(plan.snapshot.path, plan.content, plan.mode)

        settings_plan = plans[0][0]
        settings_after = snapshot_text(
            settings_plan.snapshot.path, home, "ES-DE settings"
        )
        if settings_after.content != settings_plan.content:
            raise RuntimeError("ES-DE settings differs after atomic write")
        actual_rom_directory = effective_rom_directory(
            settings_after.content or "", rom_directory
        )
        if (
            required_rom_directory is not None
            and actual_rom_directory != str(required_rom_directory)
        ):
            raise RuntimeError("ES-DE ROMDirectory changed during commit")

        systems_plan = plans[1][0]
        if systems_plan.content is not None:
            systems_after = snapshot_text(
                systems_plan.snapshot.path, home, "ES-DE custom systems"
            )
            if systems_after.content != systems_plan.content:
                raise RuntimeError("ES-DE custom systems differs after atomic write")
            parse_xml(
                systems_after.content or "",
                "systemList",
                "ES-DE custom systems",
            )
        return actual_rom_directory
    except Exception as error:
        rollback_errors: list[str] = []
        for plan, label in reversed(changed):
            try:
                restore_snapshot(plan.snapshot, home, label)
            except Exception as rollback_error:  # pragma: no cover - fatal I/O path
                rollback_errors.append(f"{label}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                "ES-DE configuration transaction failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from error
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument(
        "--esde-home",
        type=Path,
        help=(
            "base home passed to ES-DE; configuration lives in its ES-DE/ "
            "directory while executable paths still use --home"
        ),
    )
    parser.add_argument("--managed-systems", type=Path)
    parser.add_argument(
        "--previous-managed-systems",
        type=Path,
        help=(
            "previous installed managed source; migrate custom systems only when "
            "its bytes exactly match this source or its transformed output"
        ),
    )
    parser.add_argument(
        "--force-managed-systems",
        action="store_true",
        help=(
            "replace the selected generated es_systems.xml from the managed source; "
            "use only for a dedicated Pocket DS frontend home"
        ),
    )
    parser.add_argument(
        "--enable-collection",
        action="append",
        default=[],
        help="preserve the current list and enable this native ES-DE custom collection",
    )
    parser.add_argument(
        "--parse-gamelist-only",
        action="store_true",
        help="use ES-DE's native no-directory-scan mode after a complete import",
    )
    parser.add_argument(
        "--set-rom-directory",
        type=Path,
        help=(
            "transactionally set ROMDirectory to this normalized absolute path; "
            "without this option a non-empty user value is preserved"
        ),
    )
    parser.add_argument(
        "--require-rom-directory",
        type=Path,
        help=(
            "fail before writing unless the effective ROMDirectory exactly matches "
            "this normalized absolute path"
        ),
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate inputs and report the plan without writing files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    home = args.home.expanduser().resolve()
    esde_home = (
        home if args.esde_home is None else args.esde_home.expanduser().resolve()
    )
    enable_collections = validate_collection_names(args.enable_collection)
    esde = esde_home / "ES-DE"
    settings = esde / "settings" / "es_settings.xml"
    systems = esde / "custom_systems" / "es_systems.xml"
    legacy_systems = home / ".emulationstation" / "custom_systems" / "es_systems.xml"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_root = home / ".local/state/pocketds-linux-kit/backups" / stamp
    rom_directory = home / "ROMs"
    force_rom_directory = args.set_rom_directory is not None
    if args.set_rom_directory is not None:
        rom_directory = args.set_rom_directory.expanduser().resolve()
    required_rom_directory = None
    if args.require_rom_directory is not None:
        required_rom_directory = args.require_rom_directory.expanduser().resolve()

    settings_snapshot = snapshot_text(settings, esde_home, "ES-DE settings")
    systems_snapshot = snapshot_text(systems, esde_home, "ES-DE custom systems")
    settings_plan, planned_rom_directory = plan_settings(
        settings_snapshot,
        rom_directory,
        enable_collections,
        args.parse_gamelist_only,
        force_rom_directory,
    )
    systems_plan, systems_migrated = plan_systems(
        systems_snapshot,
        args.managed_systems,
        args.previous_managed_systems,
        legacy_systems,
        home,
        args.force_managed_systems,
    )
    if (
        required_rom_directory is not None
        and planned_rom_directory != str(required_rom_directory)
    ):
        raise ValueError(
            "effective ES-DE ROMDirectory does not match --require-rom-directory"
        )
    if args.check_only:
        print(
            json.dumps(
                {
                    "check_only": True,
                    "settings_would_change": settings_plan.changed,
                    "systems_would_change": systems_plan.changed,
                    "systems_managed_migration": systems_migrated,
                    "rom_directory": planned_rom_directory,
                    "required_rom_directory": (
                        str(required_rom_directory)
                        if required_rom_directory is not None
                        else None
                    ),
                    "rom_directory_requirement_met": True,
                    "rom_directory_forced": force_rom_directory,
                    "rom_content_touched": False,
                },
                sort_keys=True,
            )
        )
        return 0
    actual_rom_directory = apply_transaction(
        (
            (settings_plan, "ES-DE settings"),
            (systems_plan, "ES-DE custom systems"),
        ),
        esde_home,
        backup_root,
        required_rom_directory,
        rom_directory,
    )
    result = {
        "settings": str(settings),
        "settings_changed": settings_plan.changed,
        "systems": str(systems),
        "systems_changed": systems_plan.changed,
        "systems_managed_migration": systems_migrated,
        "rom_directory": actual_rom_directory,
        "required_rom_directory": (
            str(required_rom_directory) if required_rom_directory is not None else None
        ),
        "rom_directory_requirement_met": True,
        "rom_directory_forced": force_rom_directory,
        "rom_content_touched": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
