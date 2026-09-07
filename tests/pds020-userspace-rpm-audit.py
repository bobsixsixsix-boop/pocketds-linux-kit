#!/usr/bin/env python3
"""Offline tests for the hardened pocketds-userspace binary RPM audit."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-userspace-rpm-audit.py"
SOURCE_LOCK = ROOT / "packaging/pocketds-userspace/source-lock.json"
SPEC = importlib.util.spec_from_file_location("pds020_userspace_rpm_audit", SOURCE)
assert SPEC and SPEC.loader
auditor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = auditor
SPEC.loader.exec_module(auditor)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def field(value: int) -> bytes:
    return f"{value:08x}".encode("ascii")


def align4(data: bytearray) -> None:
    data.extend(b"\0" * (-len(data) % 4))


def newc(entries: list[dict]) -> bytes:
    result = bytearray()
    for index, entry in enumerate(entries, start=1):
        name = entry["name"].encode("utf-8") + b"\0"
        content = entry["content"]
        header = b"".join(
            (
                b"070701",
                field(index),
                field(entry["mode"]),
                field(entry.get("uid", 0)),
                field(entry.get("gid", 0)),
                field(1),
                field(0),
                field(len(content)),
                field(0),
                field(0),
                field(0),
                field(0),
                field(len(name)),
                field(0),
            )
        )
        assert len(header) == 110
        result.extend(header)
        result.extend(name)
        align4(result)
        result.extend(content)
        align4(result)
    trailer = b"TRAILER!!!\0"
    result.extend(
        b"".join(
            (
                b"070701",
                field(0),
                field(0),
                field(0),
                field(0),
                field(1),
                field(0),
                field(0),
                field(0),
                field(0),
                field(0),
                field(0),
                field(len(trailer)),
                field(0),
            )
        )
    )
    result.extend(trailer)
    align4(result)
    result.extend(b"\0" * (-len(result) % 512))
    return bytes(result)


class Fixture:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.binary_rpm = base / "pocketds-userspace.rpm"
        self.binary_rpm.write_bytes(b"synthetic hardened binary RPM\n")
        source_lock = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
        fan_record = source_lock["transform"]["fan_controller_override"]
        fan_content = (SOURCE_LOCK.parent / fan_record["path"]).read_bytes()
        self.entries = [
            {
                "name": "./etc/pocketds.conf",
                "mode": stat.S_IFREG | 0o644,
                "content": b"safe=true\n",
            },
            {
                "name": "./etc/sudoers.d/90-pocketds-safe",
                "mode": stat.S_IFREG | 0o440,
                "content": (
                    b"pocketds ALL=(root) NOPASSWD: "
                    b"/usr/local/libexec/pocketds-panel-root *\n"
                ),
            },
            {
                "name": "./usr/bin/pocketds-helper",
                "mode": stat.S_IFREG | 0o755,
                "content": b"#!/bin/sh\nexit 0\n",
            },
            {
                "name": "." + fan_record["payload_path"],
                "mode": stat.S_IFREG | int(fan_record["payload_mode"], 8),
                "content": fan_content,
            },
        ]
        self.identity = [
            "pocketds-userspace",
            0,
            "20260507",
            "20260730174706.pds1.fc44",
            "noarch",
            "pocketds-userspace-20260507-20260730174706.pds1.fc44.src.rpm",
            4,
            8,
        ]
        self.scripts = [None, "systemctl daemon-reload || :", None, None] + [
            None
        ] * (len(auditor.SCRIPT_TAGS) - 4)
        self.fixture_json = base / "fixture.json"
        self.cpio = base / "payload.cpio"
        self.rpm = base / "rpm"
        self.rpm2archive = base / "rpm2archive"
        self.rpm.write_text(
            "#!/usr/bin/python3\n"
            "import json, pathlib, sys\n"
            "root=pathlib.Path(__file__).parent\n"
            "data=json.loads((root/'fixture.json').read_text())\n"
            "q=sys.argv[sys.argv.index('--queryformat')+1]\n"
            "if '%{NAME:json}' in q:\n"
            "  values=data['identity']\n"
            "elif '%{FILENAMES:json}' in q:\n"
            "  values=[]\n"
            "  for item in data['files']:\n"
            "    values.extend([item['path'],item['mode'],item['size'],item['sha256']])\n"
            "else:\n"
            "  values=data['scripts']\n"
            "for value in values:\n"
            "  print('(none)' if value is None else json.dumps(value,separators=(',',':')))\n",
            encoding="utf-8",
        )
        self.rpm2archive.write_text(
            "#!/usr/bin/python3\n"
            "import pathlib, sys\n"
            "sys.stdout.buffer.write((pathlib.Path(__file__).parent/'payload.cpio').read_bytes())\n",
            encoding="utf-8",
        )
        self.rpm.chmod(0o700)
        self.rpm2archive.chmod(0o700)
        self.write()

    def header_files(self) -> list[dict]:
        files = []
        for entry in self.entries:
            path = entry["name"]
            if path.startswith("./"):
                path = path[1:]
            content = entry["content"]
            files.append(
                {
                    "path": path,
                    "mode": entry["mode"],
                    "size": len(content),
                    "sha256": digest(content)
                    if stat.S_ISREG(entry["mode"])
                    else "",
                }
            )
        return files

    def write(self) -> None:
        self.fixture_json.write_text(
            json.dumps(
                {
                    "identity": self.identity,
                    "files": self.header_files(),
                    "scripts": self.scripts,
                }
            ),
            encoding="utf-8",
        )
        self.cpio.write_bytes(newc(self.entries))

    def audit(self, source_lock=SOURCE_LOCK):
        return auditor.audit(
            source_lock,
            self.binary_rpm,
            self.rpm,
            self.rpm2archive,
        )


class AuditTests(unittest.TestCase):
    def test_exact_fixture_is_payload_safe_but_not_release_ready(self):
        with tempfile.TemporaryDirectory(prefix="pds020-binary-rpm-") as name:
            fixture = Fixture(Path(name))
            report = fixture.audit()
            self.assertTrue(report["payload_safe"])
            self.assertTrue(report["global_nopasswd_absent"])
            self.assertFalse(report["signature_verified"])
            self.assertFalse(report["release_ready"])
            self.assertTrue(report["fan_controller_locked"])
            self.assertEqual(
                report["fan_controller_sha256"],
                digest(fixture.entries[-1]["content"]),
            )
            self.assertEqual(report["payload_file_count"], 4)

    def test_fan_controller_payload_drift_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-binary-rpm-") as name:
            fixture = Fixture(Path(name))
            fixture.entries[-1]["content"] += b"# drift\n"
            fixture.write()
            with self.assertRaisesRegex(auditor.AuditError, "locked fan controller"):
                fixture.audit()

    def test_identity_drift_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-binary-rpm-") as name:
            fixture = Fixture(Path(name))
            fixture.identity[3] = "20260730174706.fc44"
            fixture.write()
            with self.assertRaisesRegex(auditor.AuditError, "identity"):
                fixture.audit()

    def test_source_lock_and_query_json_are_strict(self):
        with tempfile.TemporaryDirectory(prefix="pds020-binary-rpm-") as name:
            fixture = Fixture(Path(name))
            lock = Path(name) / "source-lock.json"
            original = SOURCE_LOCK.read_text(encoding="utf-8")
            lock.write_text('{"schema":999,' + original[1:], encoding="utf-8")
            with self.assertRaisesRegex(auditor.AuditError, "duplicate key"):
                fixture.audit(lock)

            value = json.loads(original)
            value["schema"] = True
            lock.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(auditor.AuditError, "schema"):
                fixture.audit(lock)

            lock.write_bytes(json.dumps(value).encode("utf-16"))
            with self.assertRaisesRegex(auditor.AuditError, "UTF-8 JSON"):
                fixture.audit(lock)

        with self.assertRaisesRegex(auditor.AuditError, "duplicate key"):
            auditor.json_lines(b'{"x":0,"x":1}\n', 1, "fixture")
        for value in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(auditor.AuditError, "non-finite"):
                    auditor.json_lines(value + b"\n", 1, "fixture")

    def test_rpm_identity_numeric_types_are_exact(self):
        for index, value in (
            (1, False),
            (1, 0.0),
            (6, 4.0),
            (6, []),
            (6, {}),
            (7, 8.0),
        ):
            with self.subTest(index=index, value=value):
                with tempfile.TemporaryDirectory(
                    prefix="pds020-binary-rpm-"
                ) as name:
                    fixture = Fixture(Path(name))
                    fixture.identity[index] = value
                    fixture.write()
                    with self.assertRaisesRegex(auditor.AuditError, "identity"):
                        fixture.audit()

    def test_forbidden_global_rule_path_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-binary-rpm-") as name:
            fixture = Fixture(Path(name))
            fixture.entries[1]["name"] = "./etc/sudoers.d/10-wheel-nopasswd"
            fixture.write()
            with self.assertRaisesRegex(auditor.AuditError, "global sudo rule"):
                fixture.audit()

    def test_broad_sudo_content_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-binary-rpm-") as name:
            fixture = Fixture(Path(name))
            fixture.entries[1]["content"] = b"%wheel ALL=(ALL) NOPASSWD: ALL\n"
            fixture.write()
            with self.assertRaisesRegex(auditor.AuditError, "broad passwordless"):
                fixture.audit()

    def test_scriptlet_cannot_restore_forbidden_rule(self):
        with tempfile.TemporaryDirectory(prefix="pds020-binary-rpm-") as name:
            fixture = Fixture(Path(name))
            fixture.scripts[1] = "install rule /etc/sudoers.d/10-wheel-nopasswd"
            fixture.write()
            with self.assertRaisesRegex(auditor.AuditError, "scriptlet"):
                fixture.audit()

    def test_header_payload_digest_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-binary-rpm-") as name:
            fixture = Fixture(Path(name))
            data = json.loads(fixture.fixture_json.read_text(encoding="utf-8"))
            data["files"][0]["sha256"] = "0" * 64
            fixture.fixture_json.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(auditor.AuditError, "metadata differ"):
                fixture.audit()

    def test_unsafe_or_non_root_cpio_entry_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="pds020-binary-rpm-") as name:
            fixture = Fixture(Path(name))
            fixture.entries[0]["uid"] = 1000
            fixture.write()
            with self.assertRaisesRegex(auditor.AuditError, "not root-owned"):
                fixture.audit()
            fixture.entries[0]["uid"] = 0
            fixture.entries[0]["name"] = "../escape"
            fixture.write()
            with self.assertRaisesRegex(auditor.AuditError, "unsafe payload path"):
                fixture.audit()
            fixture.entries[0]["name"] = "./etc/sudoers.d/linked-policy"
            fixture.entries[0]["mode"] = stat.S_IFLNK | 0o777
            fixture.entries[0]["content"] = b"/etc/sudoers"
            fixture.write()
            with self.assertRaisesRegex(auditor.AuditError, "sudoers symlink"):
                fixture.audit()
            malformed = bytearray(newc(fixture.entries))
            malformed[102:110] = b"00000001"
            with self.assertRaisesRegex(auditor.AuditError, "checksum"):
                auditor.parse_newc(bytes(malformed))
            malformed = bytearray(newc(fixture.entries))
            cursor = 0
            padding_offset = None
            while cursor + 110 <= len(malformed):
                name_size = int(malformed[cursor + 94 : cursor + 102], 16)
                size = int(malformed[cursor + 54 : cursor + 62], 16)
                name_end = cursor + 110 + name_size
                content_start = (name_end + 3) & ~3
                content_end = content_start + size
                next_cursor = (content_end + 3) & ~3
                if content_start > name_end:
                    padding_offset = name_end
                    break
                if next_cursor > content_end:
                    padding_offset = content_end
                    break
                cursor = next_cursor
            self.assertIsNotNone(padding_offset)
            malformed[padding_offset] = 1
            with self.assertRaisesRegex(auditor.AuditError, "padding"):
                auditor.parse_newc(bytes(malformed))

    def test_auditor_has_no_install_build_network_or_write_path(self):
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "urllib.request",
            "requests",
            "socket",
            "curl ",
            "wget ",
            "dnf ",
            "rpm -i",
            "rpm -U",
            "rpmbuild",
            "shell=True",
            "write_bytes",
            "write_text",
            "os.remove",
            "os.unlink",
            "shutil",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"read_only": True', source)
        self.assertIn('"signature_verified": False', source)
        self.assertIn('"release_ready": False', source)
        with tempfile.TemporaryDirectory(prefix="pds020-output-bound-") as name:
            emitter = Path(name) / "emitter"
            emitter.write_text(
                "#!/usr/bin/python3\nimport sys\nsys.stdout.write('x' * 2048)\n",
                encoding="utf-8",
            )
            emitter.chmod(0o700)
            with self.assertRaisesRegex(auditor.AuditError, "output bound"):
                auditor.run_bounded([str(emitter)], 1024, "bounded fixture")


if __name__ == "__main__":
    unittest.main(verbosity=2)
