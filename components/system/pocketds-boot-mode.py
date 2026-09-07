#!/usr/bin/env python3
"""Safely switch the ROCKNIX ABL BootMode record on AYANEO Pocket DS."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import time


IMAGE_SIZE = 4096
BOOT_MODE_OFFSET = 0xA34
BOOT_SOURCE_OFFSET = 0xA92
LINUX_MODE = 0
ANDROID_MODE = 1
ALLOWED_BOOT_SOURCES = (0, 1)
NORMALIZED_SHA256 = "fb560ce40bf17c7fc0dbda4b7e17748eb6e2fd30bbc8281ee0fc95acb28e19da"
DEVICE = Path("/dev/disk/by-partlabel/devinfo")
MODEL_PATH = Path("/proc/device-tree/model")
EXPECTED_MODEL = b"AYANEO Pocket DS"
BACKUP_ROOT = Path("/var/lib/pocketds-linux-kit/boot-switch")
BLKGETSIZE64 = 0x80081272


class BootModeError(RuntimeError):
    """The boot-mode transaction could not be proven safe."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalized(image: bytes) -> bytes:
    if len(image) != IMAGE_SIZE:
        raise BootModeError(f"devinfo is {len(image)} bytes, expected {IMAGE_SIZE}")
    if image[BOOT_MODE_OFFSET] not in (LINUX_MODE, ANDROID_MODE):
        raise BootModeError("unexpected BootMode value")
    if image[BOOT_SOURCE_OFFSET] not in ALLOWED_BOOT_SOURCES:
        raise BootModeError("unexpected BootSourceMode value")
    result = bytearray(image)
    result[BOOT_MODE_OFFSET] = LINUX_MODE
    result[BOOT_SOURCE_OFFSET] = 0
    return bytes(result)


def validate_image(image: bytes) -> None:
    if sha256(normalized(image)) != NORMALIZED_SHA256:
        raise BootModeError("devinfo does not match the verified ABL v1.1.8 Pocket DS template")


def read_model(model_path: Path) -> bytes:
    try:
        return model_path.read_bytes().rstrip(b"\0\n")
    except OSError as error:
        raise BootModeError(f"cannot read hardware model: {error}") from error


def save_backup(backup_root: Path, image: bytes, *, require_root_owner: bool) -> Path:
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_stat = backup_root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or (
        require_root_owner and root_stat.st_uid != 0
    ):
        raise BootModeError("backup root is not a root-owned directory")
    transaction = Path(
        tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=backup_root)
    )
    os.chmod(transaction, 0o700)
    image_path = transaction / "devinfo-before.img"
    descriptor = os.open(
        image_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        written = os.write(descriptor, image)
        if written != len(image):
            raise BootModeError("short backup write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(image_path, 0o400)
    receipt = transaction / "SHA256SUMS"
    receipt.write_text(f"{sha256(image)}  devinfo-before.img\n", encoding="ascii")
    os.chmod(receipt, 0o400)
    return image_path


def block_size(descriptor: int) -> int:
    packed = fcntl.ioctl(descriptor, BLKGETSIZE64, struct.pack("Q", 0))
    return struct.unpack("Q", packed)[0]


def switch_mode(
    device: Path,
    target_mode: int,
    *,
    model_path: Path = MODEL_PATH,
    backup_root: Path = BACKUP_ROOT,
    require_block: bool = True,
) -> dict[str, object]:
    if target_mode not in (LINUX_MODE, ANDROID_MODE):
        raise BootModeError("unsupported target mode")
    if read_model(model_path) != EXPECTED_MODEL:
        raise BootModeError("this is not an AYANEO Pocket DS")

    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_SYNC", 0)
    descriptor = os.open(device, flags)
    try:
        device_stat = os.fstat(descriptor)
        if require_block:
            if not stat.S_ISBLK(device_stat.st_mode):
                raise BootModeError("devinfo target is not a block device")
            if block_size(descriptor) != IMAGE_SIZE:
                raise BootModeError("devinfo partition is not exactly 4096 bytes")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = os.pread(descriptor, IMAGE_SIZE, 0)
        validate_image(before)
        backup = save_backup(backup_root, before, require_root_owner=require_block)
        expected = bytearray(before)
        expected[BOOT_MODE_OFFSET] = target_mode

        changed = before[BOOT_MODE_OFFSET] != target_mode
        if changed:
            try:
                if os.pwrite(descriptor, bytes((target_mode,)), BOOT_MODE_OFFSET) != 1:
                    raise BootModeError("short BootMode write")
                os.fsync(descriptor)
                after = os.pread(descriptor, IMAGE_SIZE, 0)
                validate_image(after)
                if after != bytes(expected):
                    raise BootModeError("devinfo readback differs outside BootMode")
            except Exception:
                os.pwrite(descriptor, bytes((before[BOOT_MODE_OFFSET],)), BOOT_MODE_OFFSET)
                os.fsync(descriptor)
                if os.pread(descriptor, IMAGE_SIZE, 0) != before:
                    raise BootModeError("write failed and automatic rollback could not be verified")
                raise
        else:
            after = before

        return {
            "status": "ok",
            "changed": changed,
            "from": "android" if before[BOOT_MODE_OFFSET] else "linux",
            "to": "android" if target_mode else "linux",
            "boot_source": before[BOOT_SOURCE_OFFSET],
            "before_sha256": sha256(before),
            "after_sha256": sha256(after),
            "backup": str(backup),
        }
    finally:
        os.close(descriptor)


def main() -> int:
    import sys

    if len(sys.argv) != 3 or sys.argv[2] != "CONFIRM" or sys.argv[1] not in {
        "linux",
        "android",
    }:
        print("usage: pocketds-boot-mode linux|android CONFIRM", file=sys.stderr)
        return 2
    target = LINUX_MODE if sys.argv[1] == "linux" else ANDROID_MODE
    try:
        result = switch_mode(DEVICE, target)
    except (BootModeError, OSError) as error:
        print(f"boot-mode switch refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
