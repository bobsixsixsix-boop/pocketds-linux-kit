#!/usr/bin/env python3
"""Tests for private, fresh and bounded keyboard ASR artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/keyboard"))

from voice_artifacts import (  # noqa: E402
    MAX_MODEL_BYTES,
    MAX_SENSEVOICE_OUTPUT_BYTES,
    VoiceArtifactError,
    parse_sensevoice_transcript,
    normalize_transcript,
    remove_owned_artifact,
    run_bounded_command,
    sensevoice_backend_available,
    sensevoice_command,
)


class CleanupTests(unittest.TestCase):
    def test_cleanup_is_missing_safe_and_unlinks_only_the_symlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds011-voice-") as temporary:
            root = Path(temporary)
            path = root / "voice.wav"
            self.assertFalse(remove_owned_artifact(path))
            path.write_bytes(b"voice")
            self.assertTrue(remove_owned_artifact(path))
            target = root / "keep.wav"
            target.write_bytes(b"keep")
            path.symlink_to(target)
            self.assertTrue(remove_owned_artifact(path))
            self.assertEqual(target.read_bytes(), b"keep")

    def test_cleanup_refuses_hardlinks_and_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds011-voice-") as temporary:
            root = Path(temporary)
            path = root / "voice.wav"
            path.write_bytes(b"voice")
            hardlink = root / "hardlink.wav"
            os.link(path, hardlink)
            with self.assertRaises(VoiceArtifactError):
                remove_owned_artifact(path)
            directory = root / "directory"
            directory.mkdir()
            with self.assertRaises(VoiceArtifactError):
                remove_owned_artifact(directory)


class SenseVoiceTests(unittest.TestCase):
    def test_exact_chinese_speech_json_is_normalized(self) -> None:
        payload = {
            "lang": "<|zh|>",
            "emotion": "<|NEUTRAL|>",
            "event": "<|Speech|>",
            "text": "  开放时间\n早上９点。  ",
            "tokens": ["开", "放"],
        }
        self.assertEqual(
            parse_sensevoice_transcript(json.dumps(payload).encode()),
            "开放时间 早上9点。",
        )

    def test_non_speech_wrong_language_duplicate_nonfinite_and_size_fail(self) -> None:
        for content in (
            b'{"lang":"<|en|>","event":"<|Speech|>","text":"hello"}',
            b'{"lang":"<|zh|>","event":"<|Music|>","text":"la"}',
            b'{"lang":"<|zh|>","event":"<|Speech|>","text":"a","text":"b"}',
            b'{"lang":"<|zh|>","event":"<|Speech|>","text":"a","score":NaN}',
            b'{"lang":"<|zh|>","event":"<|Speech|>","text":"bad\\u001b[31m"}',
            b"\xff",
            b"x" * (MAX_SENSEVOICE_OUTPUT_BYTES + 1),
        ):
            with self.subTest(content=content[:80]):
                with self.assertRaises(VoiceArtifactError):
                    parse_sensevoice_transcript(content)

    def test_backend_files_require_owner_links_bounds_modes_and_execute(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds011-sensevoice-") as temporary:
            root = Path(temporary)
            binary = root / "sherpa-onnx-offline"
            library = root / "libonnxruntime.so"
            model = root / "model.int8.onnx"
            tokens = root / "tokens.txt"
            binary.write_bytes(b"binary")
            library.write_bytes(b"library")
            model.write_bytes(b"model")
            tokens.write_bytes(b"tokens")
            binary.chmod(0o755)
            model.chmod(0o644)
            tokens.chmod(0o644)
            self.assertTrue(
                sensevoice_backend_available(binary, library, model, tokens)
            )
            library.unlink()
            self.assertFalse(
                sensevoice_backend_available(binary, library, model, tokens)
            )
            library.write_bytes(b"library")
            binary.chmod(0o644)
            self.assertFalse(
                sensevoice_backend_available(binary, library, model, tokens)
            )
            binary.chmod(0o755)
            hardlink = root / "tokens-hardlink"
            os.link(tokens, hardlink)
            self.assertFalse(
                sensevoice_backend_available(binary, library, model, tokens)
            )
            hardlink.unlink()
            with model.open("wb") as stream:
                stream.truncate(MAX_MODEL_BYTES + 1)
            self.assertFalse(
                sensevoice_backend_available(binary, library, model, tokens)
            )

    def test_command_is_fixed_local_bounded_and_shell_free(self) -> None:
        command = sensevoice_command(
            Path("/safe/bin"),
            Path("/safe/model"),
            Path("/safe/tokens"),
            Path("/safe/input.wav"),
        )
        self.assertEqual(command[0], "/safe/bin")
        self.assertIn("--sense-voice-language=zh", command)
        self.assertIn("--sense-voice-use-itn=1", command)
        self.assertIn("--num-threads=4", command)
        self.assertEqual(command[-1], "/safe/input.wav")
        self.assertNotIn("sh", command)
        with self.assertRaises(VoiceArtifactError):
            sensevoice_command(Path("a"), Path("b"), Path("c"), Path("d"), threads=5)
        with self.assertRaises(VoiceArtifactError):
            normalize_transcript("hidden\u200btext")

    def test_runner_bounds_stdout_and_timeout_without_a_shell(self) -> None:
        return_code, output = run_bounded_command(
            [sys.executable, "-c", "print('ok', end='')"],
            timeout=2,
            maximum=16,
        )
        self.assertEqual(return_code, 0)
        self.assertEqual(output, b"ok")
        with self.assertRaises(VoiceArtifactError):
            run_bounded_command(
                [sys.executable, "-c", "print('x' * 10000, end='')"],
                timeout=2,
                maximum=64,
            )
        with self.assertRaises(VoiceArtifactError):
            run_bounded_command("not-an-argv", timeout=1, maximum=64)
        with self.assertRaises(VoiceArtifactError):
            run_bounded_command(
                [sys.executable, "-c", "import time; time.sleep(2)"],
                timeout=0.1,
                maximum=64,
            )

    def test_runner_kills_backend_when_session_generation_is_cancelled(self) -> None:
        checks = 0

        def cancelled() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 3

        started = time.monotonic()
        with self.assertRaisesRegex(VoiceArtifactError, "cancelled"):
            run_bounded_command(
                [
                    sys.executable,
                    "-c",
                    "import os, time; os.close(1); time.sleep(30)",
                ],
                timeout=30,
                maximum=64,
                cancelled=cancelled,
            )
        self.assertLess(time.monotonic() - started, 1)

    def test_runner_cancellation_kills_backend_grandchild_process_group(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds011-process-group-") as temporary:
            child_pid_path = Path(temporary) / "child.pid"
            parent_code = (
                "import pathlib, subprocess, sys, time; "
                "child=subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(30)']); "
                f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
                "time.sleep(30)"
            )

            def cancelled() -> bool:
                return child_pid_path.is_file()

            child_pid = None
            try:
                with self.assertRaisesRegex(VoiceArtifactError, "cancelled"):
                    run_bounded_command(
                        [sys.executable, "-c", parent_code],
                        timeout=30,
                        maximum=64,
                        cancelled=cancelled,
                    )
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                deadline = time.monotonic() + 2
                while self.process_is_running(child_pid) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(self.process_is_running(child_pid))
            finally:
                if child_pid is not None and self.process_is_running(child_pid):
                    os.kill(child_pid, signal.SIGKILL)

    @staticmethod
    def process_is_running(process_id: int) -> bool:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return False
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(process_id)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        state = result.stdout.strip()
        return result.returncode == 0 and bool(state) and not state.startswith("Z")


if __name__ == "__main__":
    unittest.main(verbosity=2)
