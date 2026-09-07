#!/usr/bin/env python3
"""Fail-closed transaction tests for the Pocket DS ABL BootMode switcher."""

from __future__ import annotations

import base64
import gzip
import importlib.util
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "components/system/pocketds-boot-mode.py"
SPEC = importlib.util.spec_from_file_location("pocketds_boot_mode", MODULE_PATH)
assert SPEC and SPEC.loader
boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot)

BASE_IMAGE = gzip.decompress(
    base64.b64decode(
        "H4sIAAAAAAAC/3P0cwny93TRdfL3D1FkZGRkGGAw4A4YBaNgFIyCUTAKRsEoGLB2"
        "EC8DG0NQfnJ2XmaFY1JOWGoRCboN9Qz1LPCazgHETvn5Jb75KanUdzsf1PTg/N"
        "Ki5FTq2gEyXZABZGZOcGpOanJJZn4e8bodIx39XP0VAoABm1qi4BKMaTo7ELukl"
        "tEiYEbBKBgFo2AQAQAuKxxuABAAAA=="
    )
)
assert len(BASE_IMAGE) == boot.IMAGE_SIZE
assert boot.sha256(BASE_IMAGE) == boot.NORMALIZED_SHA256


def run_case(initial_mode: int, source_mode: int, target_mode: int) -> None:
    with tempfile.TemporaryDirectory(prefix="pocketds-boot-mode-test-") as temporary:
        root = Path(temporary)
        device = root / "devinfo"
        model = root / "model"
        backups = root / "backups"
        image = bytearray(BASE_IMAGE)
        image[boot.BOOT_MODE_OFFSET] = initial_mode
        image[boot.BOOT_SOURCE_OFFSET] = source_mode
        device.write_bytes(image)
        model.write_bytes(b"AYANEO Pocket DS\0")

        result = boot.switch_mode(
            device,
            target_mode,
            model_path=model,
            backup_root=backups,
            require_block=False,
        )
        after = device.read_bytes()
        expected = bytearray(image)
        expected[boot.BOOT_MODE_OFFSET] = target_mode
        assert after == expected
        assert result["changed"] is (initial_mode != target_mode)
        assert result["boot_source"] == source_mode
        backup = Path(str(result["backup"]))
        assert backup.read_bytes() == bytes(image)
        assert (backup.parent / "SHA256SUMS").read_text(encoding="ascii") == (
            f"{boot.sha256(bytes(image))}  devinfo-before.img\n"
        )


for initial in (boot.LINUX_MODE, boot.ANDROID_MODE):
    for source in boot.ALLOWED_BOOT_SOURCES:
        for target in (boot.LINUX_MODE, boot.ANDROID_MODE):
            run_case(initial, source, target)

with tempfile.TemporaryDirectory(prefix="pocketds-boot-mode-reject-") as temporary:
    root = Path(temporary)
    model = root / "model"
    model.write_bytes(b"AYANEO Pocket DS\0")
    backups = root / "backups"

    unexpected = bytearray(BASE_IMAGE)
    unexpected[123] ^= 1
    device = root / "unexpected"
    device.write_bytes(unexpected)
    try:
        boot.switch_mode(
            device,
            boot.ANDROID_MODE,
            model_path=model,
            backup_root=backups,
            require_block=False,
        )
    except boot.BootModeError:
        pass
    else:
        raise AssertionError("unexpected devinfo mutation was accepted")

    bad_source = bytearray(BASE_IMAGE)
    bad_source[boot.BOOT_SOURCE_OFFSET] = 2
    device = root / "bad-source"
    device.write_bytes(bad_source)
    try:
        boot.switch_mode(
            device,
            boot.ANDROID_MODE,
            model_path=model,
            backup_root=backups,
            require_block=False,
        )
    except boot.BootModeError:
        pass
    else:
        raise AssertionError("unexpected BootSourceMode was accepted")

print("  [OK] BootMode switch changes one byte, preserves source and keeps a verified backup")
