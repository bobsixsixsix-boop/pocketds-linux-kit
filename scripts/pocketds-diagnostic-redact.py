#!/usr/bin/env python3
"""Redact stable identifiers from allowlisted Pocket DS diagnostics."""

from __future__ import annotations

import argparse
import ipaddress
import os
import pwd
import re
from pathlib import Path
import socket
import stat


HOME = re.compile(r"(?<![A-Za-z0-9_.-])/home/[^/\s]+")
MAC = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])")
IPV4 = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
IPV6_CANDIDATE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
UUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
MACHINE_ID = re.compile(r"(?i)\b[0-9a-f]{32}\b")
SERIAL_VALUE = re.compile(
    r"(?im)(\b(?:serial(?:[_ -]?number)?|serialno)\s*[:=]\s*)[^\r\n]*"
)
MAX_FILE_BYTES = 8 * 1024 * 1024


def redact_ipv4(match: re.Match[str]) -> str:
    try:
        ipaddress.IPv4Address(match.group(0))
    except ipaddress.AddressValueError:
        return match.group(0)
    return "<IPv4>"


def redact_ipv6(match: re.Match[str]) -> str:
    candidate = match.group(0)
    try:
        ipaddress.IPv6Address(candidate)
    except ipaddress.AddressValueError:
        return candidate
    return "<IPv6>"


def redact(text: str, identifiers: set[str] | None = None) -> str:
    text = HOME.sub("/home/<user>", text)
    text = MAC.sub("<MAC>", text)
    text = IPV4.sub(redact_ipv4, text)
    text = IPV6_CANDIDATE.sub(redact_ipv6, text)
    text = UUID.sub("<UUID>", text)
    text = MACHINE_ID.sub("<ID>", text)
    text = SERIAL_VALUE.sub(r"\1<redacted>", text)
    for identifier in sorted(identifiers or set(), key=len, reverse=True):
        if len(identifier) < 3:
            continue
        boundary = r"[A-Za-z0-9_.-]"
        text = re.sub(
            rf"(?<!{boundary}){re.escape(identifier)}(?!{boundary})",
            "<local-identity>",
            text,
            flags=re.IGNORECASE,
        )
    return text


def local_identifiers() -> set[str]:
    result: set[str] = set()
    try:
        result.add(socket.gethostname())
    except OSError:
        pass
    try:
        username = pwd.getpwuid(os.getuid()).pw_name
        if username != "root":
            result.add(username)
    except (KeyError, OSError):
        pass
    return {value for value in result if value and value.lower() != "localhost"}


def redact_file(path: Path, identifiers: set[str] | None = None) -> None:
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("refusing unsafe diagnostic input") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_FILE_BYTES
        ):
            raise RuntimeError("refusing unsafe or oversized diagnostic input")
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65536, remaining))
            if not block:
                raise RuntimeError("diagnostic input changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise RuntimeError("diagnostic input grew while reading")
        payload = redact(
            bytes(content).decode("utf-8", errors="replace"), identifiers
        ).encode("utf-8")
        if len(payload) > MAX_FILE_BYTES:
            raise RuntimeError("redacted diagnostic output is oversized")
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeError("diagnostic redaction write failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    identifiers = local_identifiers()
    for path in args.files:
        try:
            redact_file(path, identifiers)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
