#!/usr/bin/env python3
"""Read-only pre-sign audit of a hardened pocketds-userspace binary RPM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import subprocess
import sys
import time
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.json"
MAX_RPM_BYTES = 16 * 1024 * 1024
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_PAYLOAD_BYTES = 128 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BROAD_NOPASSWD = re.compile(rb"\bNOPASSWD\s*:\s*ALL(?:\s*(?:,|$))", re.IGNORECASE)
FORBIDDEN_PATH = "/etc/sudoers.d/10-wheel-nopasswd"
FORBIDDEN_TEXT = b"10-wheel-nopasswd"
NEWC_MAGICS = {b"070701", b"070702"}
SCRIPT_TAGS = (
    "PREIN",
    "POSTIN",
    "PREUN",
    "POSTUN",
    "PRETRANS",
    "POSTTRANS",
    "TRIGGERSCRIPTS",
    "FILETRIGGERSCRIPTS",
    "TRANSFILETRIGGERSCRIPTS",
)


class AuditError(RuntimeError):
    """The RPM, payload or audit tool contract failed closed."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AuditError("RPM audit JSON contains a duplicate key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> Any:
    raise AuditError(f"RPM audit JSON contains non-finite number {value}")


def strict_json(data: bytes | str, label: str) -> Any:
    try:
        text = data.decode("utf-8", errors="strict") if isinstance(data, bytes) else data
        if not isinstance(text, str):
            raise AuditError(f"{label} is not JSON text")
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise AuditError(f"{label} is invalid UTF-8 JSON") from exc


def exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AuditError(f"{label} fields are incomplete or unsupported")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AuditError("required RPM audit input is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > maximum
        ):
            raise AuditError("RPM audit input has unsafe metadata")
        data = bytearray()
        remaining = metadata.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise AuditError("RPM audit input changed while being read")
            data.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise AuditError("RPM audit input grew while being read")
        return bytes(data)
    finally:
        os.close(descriptor)


def require_tool(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
        raise AuditError(f"{label} is missing or unsafe")


def run_bounded(command: list[str], maximum: int, label: str) -> bytes:
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
        )
    except OSError as exc:
        raise AuditError(f"{label} could not run") from exc
    assert process.stdout is not None and process.stderr is not None
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    limits = {process.stdout: maximum, process.stderr: MAX_METADATA_BYTES}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    deadline = time.monotonic() + 120
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuditError(f"{label} timed out")
            events = selector.select(min(remaining, 1.0))
            if not events and process.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ) for key in selector.get_map().values()
                ]
            for key, _mask in events:
                stream = key.fileobj
                chunk = os.read(stream.fileno(), 65_536)
                if not chunk:
                    selector.unregister(stream)
                    continue
                streams[stream].extend(chunk)
                if len(streams[stream]) > limits[stream]:
                    raise AuditError(f"{label} exceeded its output bound")
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (AuditError, OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    stdout = bytes(streams[process.stdout])
    if returncode != 0 or not stdout:
        raise AuditError(f"{label} failed or returned no output")
    return stdout


def json_lines(data: bytes, expected: int, label: str) -> list[Any]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise AuditError(f"{label} is not UTF-8") from exc
    lines = text.splitlines()
    if len(lines) != expected:
        raise AuditError(f"{label} field count is invalid")
    values: list[Any] = []
    for line in lines:
        if line == "(none)":
            values.append(None)
            continue
        values.append(strict_json(line, label))
    return values


def parse_hardened_version(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        raise AuditError("hardened package version is missing")
    match = re.fullmatch(
        r"(?P<version>[A-Za-z0-9.+_~]+)-(?P<release>[A-Za-z0-9.+_~]+\.fc[0-9]+)",
        value,
    )
    if match is None:
        raise AuditError("hardened package version is invalid")
    return match["version"], match["release"]


def query_identity(rpm: Path, binary_rpm: Path, lock: dict[str, Any]) -> dict[str, Any]:
    query = "\\n".join(
        f"%{{{tag}:json}}"
        for tag in (
            "NAME",
            "EPOCHNUM",
            "VERSION",
            "RELEASE",
            "ARCH",
            "SOURCERPM",
            "RPMFORMAT",
            "FILEDIGESTALGO",
        )
    ) + "\\n"
    values = json_lines(
        run_bounded(
            [str(rpm), "-qp", "--queryformat", query, str(binary_rpm)],
            MAX_METADATA_BYTES,
            "RPM identity query",
        ),
        8,
        "RPM identity query",
    )
    name, epoch, version, release, arch, source_rpm, rpm_format, digest_algorithm = values
    package = lock.get("package")
    if not isinstance(package, dict) or package.get("name") != "pocketds-userspace":
        raise AuditError("source lock package identity is invalid")
    expected_version, expected_release = parse_hardened_version(
        package.get("hardened_version")
    )
    expected_source = (
        f"pocketds-userspace-{expected_version}-{expected_release}.src.rpm"
    )
    if (
        name != "pocketds-userspace"
        or type(epoch) is not int
        or epoch != 0
        or version != expected_version
        or release != expected_release
        or arch != "noarch"
        or source_rpm != expected_source
        or type(rpm_format) is not int
        or rpm_format not in {4, 6}
        or type(digest_algorithm) is not int
        or digest_algorithm != 8
    ):
        raise AuditError("binary RPM identity does not match the hardened source lock")
    return {
        "name": name,
        "epoch": epoch,
        "version": version,
        "release": release,
        "arch": arch,
        "source_rpm": source_rpm,
        "rpm_format": rpm_format,
        "file_digest_algorithm": "sha256",
    }


def safe_payload_path(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise AuditError("RPM header contains an unsafe payload path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise AuditError("RPM header contains an unsafe payload path")
    return path.as_posix()


def query_files(rpm: Path, binary_rpm: Path) -> dict[str, dict[str, Any]]:
    query = "[%{FILENAMES:json}\\n%{FILEMODES:json}\\n%{FILESIZES:json}\\n%{FILEDIGESTS:json}\\n]"
    data = run_bounded(
        [str(rpm), "-qp", "--queryformat", query, str(binary_rpm)],
        MAX_METADATA_BYTES,
        "RPM file query",
    )
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise AuditError("RPM file query is not UTF-8") from exc
    lines = text.splitlines()
    if not lines or len(lines) % 4:
        raise AuditError("RPM file query field count is invalid")
    files: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(lines), 4):
        values = [
            strict_json(line, "RPM file query")
            for line in lines[offset : offset + 4]
        ]
        path = safe_payload_path(values[0])
        mode, size, digest = values[1:]
        if (
            path in files
            or type(mode) is not int
            or mode <= 0
            or type(size) is not int
            or size < 0
            or not isinstance(digest, str)
            or (digest and not SHA256_RE.fullmatch(digest))
        ):
            raise AuditError("RPM file metadata is duplicated or invalid")
        if stat.S_ISREG(mode) and not digest:
            raise AuditError("regular RPM payload file lacks a SHA-256 digest")
        if not (
            stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)
        ):
            raise AuditError("special files are forbidden in the RPM payload")
        files[path] = {"mode": mode, "size": size, "sha256": digest or None}
    return files


def query_scriptlets(rpm: Path, binary_rpm: Path) -> list[str]:
    query = "\\n".join(f"%{{{tag}:json}}" for tag in SCRIPT_TAGS) + "\\n"
    values = json_lines(
        run_bounded(
            [str(rpm), "-qp", "--queryformat", query, str(binary_rpm)],
            MAX_METADATA_BYTES,
            "RPM scriptlet query",
        ),
        len(SCRIPT_TAGS),
        "RPM scriptlet query",
    )
    scripts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            scripts.append(value)
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            scripts.extend(value)
        else:
            raise AuditError("RPM scriptlet query has an unsupported value")
    encoded = "\n".join(scripts).encode("utf-8")
    if FORBIDDEN_TEXT.lower() in encoded.lower() or BROAD_NOPASSWD.search(encoded):
        raise AuditError("RPM scriptlet can restore the forbidden sudo policy")
    return scripts


def parse_hex_field(header: bytes, offset: int, label: str) -> int:
    field = header[offset : offset + 8]
    if len(field) != 8 or not re.fullmatch(rb"[0-9a-fA-F]{8}", field):
        raise AuditError(f"cpio {label} field is invalid")
    return int(field, 16)


def align4(value: int) -> int:
    return (value + 3) & ~3


def normalize_cpio_path(value: str) -> str:
    if value.startswith("./"):
        value = value[1:]
    return safe_payload_path(value)


def parse_newc(data: bytes) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    cursor = 0
    trailer_seen = False
    while cursor < len(data):
        if len(data) - cursor < 110:
            if trailer_seen and not data[cursor:].strip(b"\0"):
                cursor = len(data)
                break
            raise AuditError("cpio header is truncated")
        header = data[cursor : cursor + 110]
        magic = header[:6]
        if magic not in NEWC_MAGICS:
            raise AuditError("cpio magic is unsupported")
        mode = parse_hex_field(header, 14, "mode")
        uid = parse_hex_field(header, 22, "uid")
        gid = parse_hex_field(header, 30, "gid")
        size = parse_hex_field(header, 54, "size")
        name_size = parse_hex_field(header, 94, "name size")
        checksum = parse_hex_field(header, 102, "checksum")
        if name_size <= 1 or name_size > 4096:
            raise AuditError("cpio path length is unsafe")
        name_start = cursor + 110
        name_end = name_start + name_size
        if name_end > len(data) or data[name_end - 1] != 0:
            raise AuditError("cpio path is truncated or unterminated")
        try:
            name = data[name_start : name_end - 1].decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise AuditError("cpio path is not UTF-8") from exc
        content_start = align4(name_end)
        content_end = content_start + size
        if content_end > len(data):
            raise AuditError("cpio file content is truncated")
        if data[name_end:content_start].strip(b"\0"):
            raise AuditError("cpio path padding is nonzero")
        content = data[content_start:content_end]
        next_cursor = align4(content_end)
        if data[content_end:next_cursor].strip(b"\0"):
            raise AuditError("cpio content padding is nonzero")
        if (magic == b"070701" and checksum != 0) or (
            magic == b"070702" and checksum != sum(content) & 0xFFFFFFFF
        ):
            raise AuditError("cpio checksum is invalid")
        cursor = next_cursor
        if name == "TRAILER!!!":
            if size != 0 or trailer_seen:
                raise AuditError("cpio trailer is invalid")
            trailer_seen = True
            if data[cursor:].strip(b"\0"):
                raise AuditError("cpio has data after its trailer")
            cursor = len(data)
            break
        path = normalize_cpio_path(name)
        if path in entries or uid != 0 or gid != 0:
            raise AuditError("cpio path is duplicated or not root-owned")
        if not (
            stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)
        ):
            raise AuditError("cpio contains a forbidden special file")
        entries[path] = {
            "mode": mode,
            "size": size,
            "content_sha256": sha256_bytes(content)
            if not stat.S_ISDIR(mode)
            else None,
            "content": content,
        }
    if not trailer_seen or cursor != len(data):
        raise AuditError("cpio trailer is missing")
    return entries


def compare_payload(
    header_files: dict[str, dict[str, Any]],
    payload_files: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if set(header_files) != set(payload_files):
        raise AuditError("RPM header and cpio payload path sets differ")
    manifest: list[dict[str, Any]] = []
    for path in sorted(header_files):
        expected = header_files[path]
        actual = payload_files[path]
        if (
            expected["mode"] != actual["mode"]
            or expected["size"] != actual["size"]
            or (
                expected["sha256"] is not None
                and expected["sha256"] != actual["content_sha256"]
            )
        ):
            raise AuditError("RPM header and cpio payload metadata differ")
        if path == FORBIDDEN_PATH:
            raise AuditError("hardened RPM still contains the global sudo rule")
        is_sudoers = path == "/etc/sudoers" or path.startswith(
            "/etc/sudoers.d/"
        )
        if is_sudoers and stat.S_ISLNK(actual["mode"]):
            raise AuditError("RPM payload contains a sudoers symlink")
        if is_sudoers:
            content = actual["content"]
            if FORBIDDEN_TEXT.lower() in content.lower() or BROAD_NOPASSWD.search(content):
                raise AuditError("RPM payload contains a broad passwordless sudo rule")
        manifest.append(
            {
                "path": path,
                "mode": expected["mode"],
                "size": expected["size"],
                "sha256": actual["content_sha256"],
            }
        )
    return manifest


def audit(
    source_lock_path: Path,
    binary_rpm: Path,
    rpm: Path,
    rpm2archive: Path,
) -> dict[str, Any]:
    source_lock = exact_object(
        strict_json(
            read_regular(source_lock_path, MAX_METADATA_BYTES),
            "source lock",
        ),
        {"schema", "package", "transform"},
        "source lock",
    )
    if type(source_lock["schema"]) is not int or source_lock["schema"] != 1:
        raise AuditError("source lock schema is unsupported")
    package = exact_object(
        source_lock["package"],
        {
            "name",
            "source_version",
            "hardened_version",
            "source_rpm",
            "source_tree",
            "spec",
            "fan_controller_source",
            "forbidden_source",
        },
        "source lock package",
    )
    source_tree = exact_object(
        package["source_tree"],
        {"file_count", "total_size", "manifest_sha256"},
        "source tree record",
    )
    if (
        type(source_tree["file_count"]) is not int
        or source_tree["file_count"] <= 0
        or type(source_tree["total_size"]) is not int
        or source_tree["total_size"] <= 0
        or not isinstance(source_tree["manifest_sha256"], str)
        or not SHA256_RE.fullmatch(source_tree["manifest_sha256"])
    ):
        raise AuditError("source tree record is invalid")
    exact_object(
        package["fan_controller_source"],
        {"path", "size", "sha256"},
        "source fan controller record",
    )
    transform = exact_object(
        source_lock["transform"],
        {
            "replace_exact_lines",
            "remove_exact_lines",
            "insert_after_exact_line",
            "forbidden_tokens",
            "fan_controller_override",
            "hardened_spec_size",
            "hardened_spec_sha256",
        },
        "source lock transform",
    )
    fan_override = exact_object(
        transform["fan_controller_override"],
        {"path", "size", "sha256", "payload_path", "payload_mode"},
        "fan controller override record",
    )
    fan_path = fan_override.get("payload_path")
    fan_size = fan_override.get("size")
    fan_sha256 = fan_override.get("sha256")
    fan_mode = fan_override.get("payload_mode")
    if (
        fan_override.get("path") != "pocketds-fancontrol.pds1"
        or fan_path != "/usr/bin/pocketds-fancontrol"
        or type(fan_size) is not int
        or fan_size <= 0
        or not isinstance(fan_sha256, str)
        or not SHA256_RE.fullmatch(fan_sha256)
        or fan_mode != "0755"
    ):
        raise AuditError("fan controller payload contract is invalid")
    require_tool(rpm, "rpm query tool")
    require_tool(rpm2archive, "rpm2archive tool")
    rpm_data = read_regular(binary_rpm, MAX_RPM_BYTES)
    identity = query_identity(rpm, binary_rpm, source_lock)
    header_files = query_files(rpm, binary_rpm)
    scripts = query_scriptlets(rpm, binary_rpm)
    cpio = run_bounded(
        [
            str(rpm2archive),
            "--nocompression",
            "--format=cpio",
            str(binary_rpm),
        ],
        MAX_PAYLOAD_BYTES,
        "RPM payload conversion",
    )
    payload_files = parse_newc(cpio)
    manifest = compare_payload(header_files, payload_files)
    installed_fan = payload_files.get(fan_path)
    if (
        installed_fan is None
        or installed_fan["mode"] != stat.S_IFREG | int(fan_mode, 8)
        or installed_fan["size"] != fan_size
        or installed_fan["content_sha256"] != fan_sha256
    ):
        raise AuditError("RPM payload does not contain the locked fan controller")
    if read_regular(binary_rpm, MAX_RPM_BYTES) != rpm_data:
        raise AuditError("binary RPM changed during payload audit")
    return {
        "schema": 1,
        "read_only": True,
        "network": False,
        "install": False,
        "build": False,
        "package": identity,
        "binary_rpm_size": len(rpm_data),
        "binary_rpm_sha256": sha256_bytes(rpm_data),
        "payload_file_count": len(manifest),
        "payload_manifest_sha256": sha256_bytes(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "scriptlet_count": len(scripts),
        "scriptlets_sha256": sha256_bytes(
            json.dumps(scripts, separators=(",", ":")).encode("utf-8")
        ),
        "global_nopasswd_absent": True,
        "fan_controller_locked": True,
        "fan_controller_sha256": fan_sha256,
        "payload_safe": True,
        "signature_verified": False,
        "release_ready": False,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary-rpm", type=Path, required=True)
    parser.add_argument("--rpm", type=Path, default=Path("/usr/bin/rpm"))
    parser.add_argument(
        "--rpm2archive", type=Path, default=Path("/usr/bin/rpm2archive")
    )
    parser.add_argument("--source-lock", type=Path, default=DEFAULT_LOCK)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report = audit(
            args.source_lock,
            args.binary_rpm,
            args.rpm,
            args.rpm2archive,
        )
    except (OSError, UnicodeError, TypeError, ValueError, AuditError) as exc:
        print(json.dumps({"payload_safe": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
