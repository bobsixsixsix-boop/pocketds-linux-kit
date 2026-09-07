#!/usr/bin/env python3
"""Compose the fixed q6apm Image with the source-verified native-lid DTB."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import zlib


KERNEL_RELEASE = "7.1.0-101.20260829000000.pocketds.q6apmcs1.fc44.aarch64"
PACKAGE_BOOT_SIZE = 17090560
PACKAGE_BOOT_SHA256 = "eefdbf93211e6295cfa6c503daa5c7729b32494edcf609d6f3a7402f47eda7c1"
RAW_IMAGE_SIZE = 34515456
RAW_IMAGE_SHA256 = "30766024b63cc223bce3c873a8578784c8344bac805f668d875453e5b970994a"
NATIVE_DTB_SIZE = 135820
NATIVE_DTB_SHA256 = "195345de379195df466b999cf7c7ba53764dfd9c0bb12b18214083e530979556"
MAX_INPUT = 128 * 1024 * 1024


class ComposeError(RuntimeError):
    pass


def read_regular(path: Path, maximum: int = MAX_INPUT) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ComposeError(f"not a private regular input: {path}")
        if metadata.st_size <= 0 or metadata.st_size > maximum:
            raise ComposeError(f"invalid input size: {path}")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ComposeError(f"short read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(after, field) != getattr(metadata, field) for field in stable):
            raise ComposeError(f"input changed while reading: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def check_dtb(content: bytes, name: str) -> None:
    if len(content) < 40 or struct.unpack_from(">I", content, 0)[0] != 0xD00DFEED:
        raise ComposeError(f"{name} has no FDT header")
    if struct.unpack_from(">I", content, 4)[0] != len(content):
        raise ComposeError(f"{name} FDT total size differs")


def parse_package_boot(boot: bytes, raw: bytes) -> tuple[bytes, bytes, bytes, int]:
    if len(boot) < 2048 or boot[:8] != b"ANDROID!":
        raise ComposeError("package boot image is not Android bootimg v0")
    kernel_size = struct.unpack_from("<I", boot, 8)[0]
    ramdisk_size = struct.unpack_from("<I", boot, 16)[0]
    second_size = struct.unpack_from("<I", boot, 24)[0]
    page_size = struct.unpack_from("<I", boot, 36)[0]
    if page_size != 2048 or ramdisk_size != 0 or second_size != 0:
        raise ComposeError("unexpected bootimg v0 layout")
    end = page_size + kernel_size
    padded_end = page_size + ((kernel_size + page_size - 1) // page_size) * page_size
    if end > len(boot) or padded_end != len(boot) or any(boot[end:padded_end]):
        raise ComposeError("package boot length or padding differs")
    blob = boot[page_size:end]
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    unpacked = decoder.decompress(blob) + decoder.flush()
    if not decoder.eof or unpacked != raw or not decoder.unused_data:
        raise ComposeError("package gzip stream does not bind to raw Image")
    package_dtb = decoder.unused_data
    check_dtb(package_dtb, "package DTB")
    gzip_bytes = blob[: len(blob) - len(package_dtb)]
    return boot[:page_size], gzip_bytes, package_dtb, page_size


def compose(header_bytes: bytes, gzip_bytes: bytes, dtb: bytes, page_size: int) -> bytes:
    kernel = gzip_bytes + dtb
    header = bytearray(header_bytes)
    struct.pack_into("<I", header, 8, len(kernel))
    identifier = hashlib.sha1(
        kernel
        + struct.pack("<I", len(kernel))
        + struct.pack("<I", 0)
        + struct.pack("<I", 0)
    ).digest()
    header[576:608] = identifier + bytes(12)
    return bytes(header) + kernel + bytes((-len(kernel)) % page_size)


def write_new(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    created = True
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ComposeError("runtime image write stopped")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        descriptor = -1
        if created:
            path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-boot", type=Path, required=True)
    parser.add_argument("--raw-image", type=Path, required=True)
    parser.add_argument("--native-dtb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        boot = read_regular(args.package_boot)
        raw = read_regular(args.raw_image)
        dtb = read_regular(args.native_dtb)
        if len(boot) != PACKAGE_BOOT_SIZE or sha256(boot) != PACKAGE_BOOT_SHA256:
            raise ComposeError("package boot image is not the independently reproduced q6apm candidate")
        if len(raw) != RAW_IMAGE_SIZE or sha256(raw) != RAW_IMAGE_SHA256:
            raise ComposeError("raw Image is not the independently reproduced q6apm candidate")
        if len(dtb) != NATIVE_DTB_SIZE or sha256(dtb) != NATIVE_DTB_SHA256:
            raise ComposeError("native-lid DTB is not the locked source-verified artifact")
        check_dtb(dtb, "native-lid DTB")
        header, gzip_bytes, package_dtb, page_size = parse_package_boot(boot, raw)
        image = compose(header, gzip_bytes, dtb, page_size)
        # Parse the composed artifact again and require the new DTB boundary.
        _, new_gzip, new_dtb, _ = parse_package_boot(image, raw)
        if new_gzip != gzip_bytes or new_dtb != dtb:
            raise ComposeError("composed runtime image failed round-trip verification")
        write_new(args.output, image)
        report = {
            "schema": "pocketds.q6apm-runtime-composition.v1",
            "kernel_release": KERNEL_RELEASE,
            "inputs": {
                "package_boot": {"size": len(boot), "sha256": sha256(boot)},
                "raw_image": {"size": len(raw), "sha256": sha256(raw)},
                "package_dtb": {"size": len(package_dtb), "sha256": sha256(package_dtb)},
                "native_lid_dtb": {"size": len(dtb), "sha256": sha256(dtb)},
                "gzip_stream": {"size": len(gzip_bytes), "sha256": sha256(gzip_bytes)},
            },
            "output": {"filename": args.output.name, "size": len(image), "sha256": sha256(image)},
            "gates": {
                "package_boot_bound_to_raw_image": True,
                "native_lid_dtb_locked": True,
                "gzip_stream_preserved": True,
                "round_trip_verified": True,
                "deployed": False,
                "runtime_authorized": False,
            },
        }
        json.dump(report, sys.stdout, sort_keys=True, indent=2)
        sys.stdout.write("\n")
        return 0
    except (ComposeError, OSError, zlib.error) as error:
        print(f"compose-runtime: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
