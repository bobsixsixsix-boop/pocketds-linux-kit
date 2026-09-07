#!/usr/bin/env python3
"""Tests for the locked, offline SenseVoice artifact installer."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds011-sensevoice-prepare.py"
SPEC = importlib.util.spec_from_file_location("pds011_sensevoice_prepare", SOURCE)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record(archive_path: str, target: str, mode: int, data: bytes) -> dict[str, object]:
    return {
        "archive_path": archive_path,
        "target": target,
        "mode": mode,
        "size": len(data),
        "sha256": digest(data),
    }


def write_archive(path: Path, entries: list[tuple[str, bytes | None, int]]) -> None:
    with tarfile.open(path, "w:bz2", format=tarfile.PAX_FORMAT) as bundle:
        for name, data, mode in entries:
            member = tarfile.TarInfo(name)
            member.uid = os.getuid()
            member.gid = os.getgid()
            member.mtime = 0
            member.mode = mode
            if data is None:
                member.type = tarfile.DIRTYPE
                bundle.addfile(member)
            else:
                member.size = len(data)
                bundle.addfile(member, io.BytesIO(data))
    path.chmod(0o600)


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.repository = root / "repository"
        self.destination = root / "destination"
        self.repository.mkdir(mode=0o700)
        self.destination.mkdir(mode=0o700)
        self.engine_root = "engine-v1"
        self.model_root = "sense-model"
        self.binary = (
            b"#!/usr/bin/env python3\n"
            b"import json\n"
            b"print(json.dumps({'lang':'<|zh|>','event':'<|Speech|>',"
            b"'text':'fixture transcript'}))\n"
        )
        self.library = b"synthetic onnx runtime\n"
        self.model = b"synthetic int8 model\n"
        self.tokens = b"synthetic tokens\n"
        self.wav = b"RIFF synthetic wav\n"
        self.engine_archive = root / "engine.tar.bz2"
        self.model_archive = root / "model.tar.bz2"
        write_archive(
            self.engine_archive,
            [
                (self.engine_root, None, 0o755),
                (f"{self.engine_root}/bin", None, 0o755),
                (f"{self.engine_root}/bin/sherpa-onnx-offline", self.binary, 0o755),
                (f"{self.engine_root}/lib", None, 0o755),
                (f"{self.engine_root}/lib/libonnxruntime.so", self.library, 0o644),
            ],
        )
        write_archive(
            self.model_archive,
            [
                (self.model_root, None, 0o755),
                (f"{self.model_root}/model.int8.onnx", self.model, 0o644),
                (f"{self.model_root}/tokens.txt", self.tokens, 0o644),
                (f"{self.model_root}/test_wavs", None, 0o755),
                (f"{self.model_root}/test_wavs/zh.wav", self.wav, 0o644),
            ],
        )
        license_dir = self.repository / "components/keyboard/licenses"
        license_dir.mkdir(parents=True)
        self.license_data = [b"engine license\n", b"model license\n", b"notice\n"]
        license_names = ["engine.txt", "model.txt", "NOTICE.md"]
        licenses = []
        for index, (name, data) in enumerate(zip(license_names, self.license_data)):
            path = license_dir / name
            path.write_bytes(data)
            licenses.append(
                {
                    "component": f"component {index}",
                    "license": f"license {index}",
                    "source_url": None,
                    "source_commit": None,
                    "repository_path": f"components/keyboard/licenses/{name}",
                    "target": f".local/share/licenses/pocketds-sensevoice/{name}",
                    "mode": 0o644,
                    "size": len(data),
                    "sha256": digest(data),
                    "current_upstream_recheck_at_release": index == 1,
                }
            )
        self.lock = {
            "schema": 1,
            "platform": {"os": "linux", "arch": "aarch64"},
            "download_ready": True,
            "install_ready": True,
            "release_ready": False,
            "release_blockers": ["fixture remains blocked"],
            "engine": {
                "name": "sherpa-onnx",
                "version": "1.13.2",
                "tag": "v1.13.2",
                "source_commit": "a" * 40,
                "source_url": "https://github.com/k2-fsa/sherpa-onnx/tree/fixture",
                "release_url": "https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.2",
                "archive": self.archive_record(self.engine_archive, self.engine_root, 5),
                "runtime_files": [
                    record(
                        f"{self.engine_root}/bin/sherpa-onnx-offline",
                        ".local/libexec/pocketds-keyboard/sherpa-onnx-offline",
                        0o755,
                        self.binary,
                    ),
                    record(
                        f"{self.engine_root}/lib/libonnxruntime.so",
                        ".local/libexec/pocketds-keyboard/libonnxruntime.so",
                        0o644,
                        self.library,
                    ),
                ],
                "version_evidence": {
                    "binary": "sherpa-onnx-version",
                    "version": "1.13.2",
                    "git_sha1": "aaaaaaaa",
                    "build_date": "fixture",
                },
            },
            "model": {
                "name": "fixture-sensevoice-model",
                "source_model": "SenseVoiceSmall",
                "source_author": "FunASR/FunAudioLLM",
                "documentation": "https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html",
                "archive": self.archive_record(self.model_archive, self.model_root, 5),
                "runtime_files": [
                    record(
                        f"{self.model_root}/model.int8.onnx",
                        ".local/share/pocketds-keyboard/sensevoice/model.int8.onnx",
                        0o644,
                        self.model,
                    ),
                    record(
                        f"{self.model_root}/tokens.txt",
                        ".local/share/pocketds-keyboard/sensevoice/tokens.txt",
                        0o644,
                        self.tokens,
                    ),
                ],
                "smoke_fixture": {
                    "archive_path": f"{self.model_root}/test_wavs/zh.wav",
                    "size": len(self.wav),
                    "sha256": digest(self.wav),
                    "language": "<|zh|>",
                    "event": "<|Speech|>",
                    "text": "fixture transcript",
                },
            },
            "licenses": licenses,
        }
        self.lock_path = self.repository / "lock.json"
        self.write_lock()

    @staticmethod
    def archive_record(path: Path, root: str, member_count: int) -> dict[str, object]:
        data = path.read_bytes()
        return {
            "filename": path.name,
            "url": f"https://github.com/fixture/releases/download/v1/{path.name}",
            "size": len(data),
            "sha256": digest(data),
            "member_count": member_count,
            "root": root,
        }

    def write_lock(self) -> None:
        self.lock_path.write_text(json.dumps(self.lock), encoding="utf-8")
        self.lock_path.chmod(0o600)

    def run(self, *, execute: bool = False, confirmation: str | None = None):
        self.write_lock()
        return prepare.prepare(
            lock_path=self.lock_path,
            engine_archive_path=self.engine_archive,
            model_archive_path=self.model_archive,
            repository_root=self.repository,
            destination_root=self.destination,
            execute=execute,
            confirmation=confirmation,
            host_os="linux",
            host_arch="aarch64",
        )


class PrepareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pds011-prepare-")
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_checked_in_lock_is_complete_but_not_release_ready(self) -> None:
        locked = prepare.validate_lock(prepare.strict_json(prepare.DEFAULT_LOCK))
        self.assertEqual(len(locked["engine_files"]), 2)
        self.assertEqual(len(locked["model_files"]), 2)
        self.assertEqual(len(locked["licenses"]), 3)
        self.assertTrue(locked["release_blockers"])

    def test_plan_verifies_every_input_without_writing(self) -> None:
        before = sorted(path.relative_to(self.fixture.destination) for path in self.fixture.destination.rglob("*"))
        report = self.fixture.run()
        after = sorted(path.relative_to(self.fixture.destination) for path in self.fixture.destination.rglob("*"))
        self.assertEqual(before, after)
        self.assertTrue(report["read_only"])
        self.assertEqual(report["files_to_install"], 7)
        self.assertEqual(report["runtime_smoke"], "not-run-in-plan")

    def test_execute_requires_confirmation_and_exact_platform(self) -> None:
        with self.assertRaisesRegex(prepare.PrepareError, "requires --confirm"):
            self.fixture.run(execute=True)
        self.fixture.write_lock()
        with self.assertRaisesRegex(prepare.PrepareError, "Linux aarch64"):
            prepare.prepare(
                lock_path=self.fixture.lock_path,
                engine_archive_path=self.fixture.engine_archive,
                model_archive_path=self.fixture.model_archive,
                repository_root=self.fixture.repository,
                destination_root=self.fixture.destination,
                execute=True,
                confirmation=prepare.CONFIRMATION,
                host_os="darwin",
                host_arch="arm64",
            )

    def test_execute_installs_minimal_runtime_and_is_idempotent(self) -> None:
        report = self.fixture.run(execute=True, confirmation=prepare.CONFIRMATION)
        self.assertEqual(report["runtime_smoke"], "pass")
        self.assertEqual(report["files_installed"], 7)
        second = self.fixture.run(execute=True, confirmation=prepare.CONFIRMATION)
        self.assertEqual(second["files_installed"], 0)
        self.assertEqual(second["files_unchanged"], 7)
        self.assertEqual(
            (self.fixture.destination / ".local/libexec/pocketds-keyboard").iterdir().__next__().stat().st_uid,
            os.getuid(),
        )

    def test_archive_tamper_and_target_drift_fail_closed(self) -> None:
        self.fixture.engine_archive.write_bytes(self.fixture.engine_archive.read_bytes() + b"x")
        with self.assertRaisesRegex(prepare.PrepareError, "digest"):
            self.fixture.run()
        self.fixture = Fixture(Path(self.temporary.name) / "fresh")
        target = self.fixture.destination / ".local/libexec/pocketds-keyboard/sherpa-onnx-offline"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"drift")
        target.chmod(0o755)
        with self.assertRaisesRegex(prepare.PrepareError, "drifted"):
            self.fixture.run()
        self.assertEqual(target.read_bytes(), b"drift")

    def test_hash_consistent_symlink_member_is_still_rejected(self) -> None:
        path = self.fixture.engine_archive
        root = self.fixture.engine_root
        with tarfile.open(path, "w:bz2") as bundle:
            directory = tarfile.TarInfo(root)
            directory.type = tarfile.DIRTYPE
            bundle.addfile(directory)
            link = tarfile.TarInfo(f"{root}/linked")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            bundle.addfile(link)
        path.chmod(0o600)
        archive = prepare.archive_record(
            self.fixture.archive_record(path, root, 2), "fixture"
        )
        with self.assertRaisesRegex(prepare.PrepareError, "linked or special"):
            prepare.verify_archive(path, archive, [], owner=os.getuid())

    def test_smoke_mismatch_rolls_back_only_new_files(self) -> None:
        self.fixture.lock["model"]["smoke_fixture"]["text"] = "wrong expected text"
        with self.assertRaisesRegex(prepare.PrepareError, "smoke result drifted"):
            self.fixture.run(execute=True, confirmation=prepare.CONFIRMATION)
        files = [path for path in self.fixture.destination.rglob("*") if path.is_file()]
        self.assertEqual(files, [])

    def test_repository_license_drift_and_lock_duplicate_key_fail(self) -> None:
        license_path = self.fixture.repository / "components/keyboard/licenses/model.txt"
        license_path.write_bytes(b"changed model license\n")
        with self.assertRaisesRegex(prepare.PrepareError, "license identity"):
            self.fixture.run()
        self.fixture.lock_path.write_text(
            '{"schema":1,"schema":1}', encoding="utf-8"
        )
        self.fixture.lock_path.chmod(0o600)
        with self.assertRaisesRegex(prepare.PrepareError, "duplicate key"):
            prepare.strict_json(self.fixture.lock_path)

    def test_source_has_no_network_or_overwrite_fallback(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("urllib", "requests", "curl ", "wget ", "shutil.copy"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("target appeared after preflight", source)
        self.assertIn("installed runtime smoke result drifted", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
