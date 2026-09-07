#!/usr/bin/env python3
"""Focused offline tests for the user-configured Pocket DS transcription API client."""

from __future__ import annotations

import ctypes.util
import json
import importlib.util
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
KEYBOARD = ROOT / "components/keyboard"
sys.path.insert(0, str(KEYBOARD))

import voice_artifacts as voice  # noqa: E402
from keyboard_adapter import VoiceSessionController, VoiceState  # noqa: E402

PROVISION_SCRIPT = ROOT / "scripts/pocketds-asr-api-provision.py"
PROVISION_SPEC = importlib.util.spec_from_file_location(
    "pocketds_asr_api_runtime_lock_test", PROVISION_SCRIPT
)
assert PROVISION_SPEC is not None and PROVISION_SPEC.loader is not None
provision = importlib.util.module_from_spec(PROVISION_SPEC)
sys.modules[PROVISION_SPEC.name] = provision
PROVISION_SPEC.loader.exec_module(provision)


FAKE_API_KEY = "fixture-api-key-000000000001"
FAKE_MODEL = "fixture/transcribe-v1"
FAKE_ENDPOINT = "https://transcribe.example.invalid:8443/custom/audio/transcriptions"
CONFIG = voice.AsrApiConfig(FAKE_ENDPOINT, FAKE_API_KEY, FAKE_MODEL)


def wav_bytes(sample_count: int = 160) -> bytes:
    samples = b"\x00\x00" * sample_count
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
    data = b"data" + struct.pack("<I", len(samples)) + samples
    payload = b"WAVE" + fmt + data
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def response(
    status: int,
    payload: dict[str, object] | bytes,
    *headers: tuple[str, str],
) -> voice.AsrApiHttpResponse:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return voice.AsrApiHttpResponse(
        status,
        (("Content-Type", "application/json; charset=utf-8"),) + headers,
        body,
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


class FakeTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.wav_sizes: list[int] = []
        self.fixed_credentials_seen = False

    def post_wav(self, **kwargs: object) -> voice.AsrApiHttpResponse:
        self.calls += 1
        self.wav_sizes.append(len(kwargs["wav"]))
        self.fixed_credentials_seen = (
            kwargs["config"] == CONFIG
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, voice.AsrApiHttpResponse)
        return outcome


class ClientFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pds-asr_api-test-")
        self.root = Path(self.temporary.name)
        self.wav = self.root / "voice.wav"
        self.config = self.root / "config.json"
        self.lock = self.root / ".config.lock"
        self.wav.write_bytes(wav_bytes())
        self.config.write_bytes(voice.encode_asr_api_config(CONFIG))
        self.lock.touch(mode=0o600)
        for path in (self.wav, self.config, self.lock):
            path.chmod(0o600)
        self.path_patch = mock.patch.multiple(
            voice,
            ASR_API_PRIVATE_DIR=self.root,
            ASR_API_CONFIG_FILE=self.config,
            ASR_API_LOCK_FILE=self.lock,
        )
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temporary.cleanup()

    def client(
        self, transport: object, clock: FakeClock | None = None
    ) -> voice.AsrApiClient:
        if clock is None:
            clock = FakeClock()
        return voice.AsrApiClient(
            transport=transport,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )


class SuccessAndProtocolTests(ClientFixture):
    def test_success_is_normalized_and_empty_text_is_explicit_no_result(self) -> None:
        transport = FakeTransport(
            [
                response(200, {"text": "  今天天气\n怎么样  "}),
                response(200, {"text": " \n\t "}),
            ]
        )
        client = self.client(transport)
        self.assertEqual(client.transcribe(self.wav), "今天天气 怎么样")
        self.assertIsNone(client.transcribe(self.wav))
        self.assertEqual(transport.calls, 2)
        self.assertTrue(transport.fixed_credentials_seen)
        self.assertEqual(transport.wav_sizes, [len(wav_bytes()), len(wav_bytes())])

    def test_duplicate_nonfinite_extra_wrong_and_oversized_json_fail_closed(self) -> None:
        cases = (
            b'{"text":"one","text":"two"}',
            b'{"text":"one","score":NaN}',
            b'{"text":{"nested":"no"}}',
            b"[]",
            b"x" * (voice.ASR_API_MAX_RESPONSE_BYTES + 1),
        )
        for content in cases:
            with self.subTest(content=content[:64]):
                with self.assertRaises(voice.AsrApiError):
                    voice.parse_asr_api_transcript(content)

    def test_response_headers_body_and_error_json_are_bounded(self) -> None:
        cases = (
            voice.AsrApiHttpResponse(200, (), b'{"text":"ok"}'),
            voice.AsrApiHttpResponse(
                200,
                (("Content-Type", "application/json"), ("content-type", "application/json")),
                b'{"text":"ok"}',
            ),
            voice.AsrApiHttpResponse(
                200,
                (("Content-Type", "application/json"),),
                b"x" * (voice.ASR_API_MAX_RESPONSE_BYTES + 1),
            ),
            response(401, {"error": "bad", "extra": True}),
        )
        for item in cases:
            with self.subTest(item=item):
                transport = FakeTransport([item])
                with self.assertRaises(voice.AsrApiError):
                    self.client(transport).transcribe(self.wav)
                self.assertEqual(transport.calls, 1)


class CredentialAndWavTests(ClientFixture):
    def test_feedback_only_account_is_unconfigured_without_secret_reads_or_lock_creation(self) -> None:
        for path in (self.config, self.lock):
            path.unlink()
        feedback = self.root / "feedback.json"
        feedback.write_text('{"audio_enabled":true}\n')
        before = {path.name: path.stat().st_ino for path in self.root.iterdir()}
        transport = FakeTransport([])
        with (
            mock.patch.object(voice, "_read_private_config_bytes") as secret_reader,
            self.assertRaisesRegex(voice.AsrApiError, "尚未配置"),
        ):
            self.client(transport).transcribe(self.wav)
        secret_reader.assert_not_called()
        self.assertEqual(transport.calls, 0)
        self.assertFalse(self.lock.exists())
        self.assertEqual(before, {path.name: path.stat().st_ino for path in self.root.iterdir()})

    def test_missing_private_directory_is_unconfigured_and_not_created(self) -> None:
        private = self.root / "not-configured"
        with (
            mock.patch.multiple(
                voice,
                ASR_API_PRIVATE_DIR=private,
                ASR_API_CONFIG_FILE=private / "config.json",
                ASR_API_LOCK_FILE=private / ".config.lock",
            ),
            self.assertRaisesRegex(voice.AsrApiError, "尚未配置"),
        ):
            voice.load_asr_api_config()
        self.assertFalse(private.exists())

    def test_missing_lock_with_existing_config_fails_without_reading_it(self):
        self.lock.unlink()
        with (mock.patch.object(voice, "_read_private_config_bytes") as reader,
              self.assertRaisesRegex(voice.AsrApiError, "凭据锁不可用")):
            voice.load_asr_api_config()
        reader.assert_not_called()
        self.assertFalse(self.lock.exists())

    def test_runtime_context_never_explicitly_unlocks_inherited_description(self) -> None:
        real_flock = voice.fcntl.flock
        operations: list[int] = []

        def record_flock(descriptor: int, operation: int) -> None:
            operations.append(operation)
            return real_flock(descriptor, operation)

        with mock.patch.object(voice.fcntl, "flock", side_effect=record_flock):
            with voice._locked_asr_api_credentials() as (credentials, lock_fd):
                self.assertEqual(credentials.api_key, FAKE_API_KEY)
                self.assertGreaterEqual(lock_fd, 0)
        self.assertTrue(any(operation & voice.fcntl.LOCK_SH for operation in operations))
        self.assertNotIn(voice.fcntl.LOCK_UN, operations)

    @unittest.skipUnless(hasattr(os, "fork"), "inherited flock fixture needs fork")
    def test_parent_close_keeps_shared_lock_until_inherited_child_exits(self) -> None:
        context = voice._locked_asr_api_credentials()
        _credentials, _lock_fd = context.__enter__()
        ready_read, ready_write = os.pipe()
        release_read, release_write = os.pipe()
        child = os.fork()
        if child == 0:
            try:
                os.close(ready_read)
                os.close(release_write)
                os.write(ready_write, b"1")
                os.read(release_read, 1)
            finally:
                os._exit(0)
        os.close(ready_write)
        os.close(release_read)
        probe = -1
        try:
            self.assertEqual(os.read(ready_read, 1), b"1")
            context.__exit__(None, None, None)
            probe = os.open(self.lock, os.O_RDONLY)
            with self.assertRaises((BlockingIOError, OSError)) as caught:
                voice.fcntl.flock(
                    probe, voice.fcntl.LOCK_EX | voice.fcntl.LOCK_NB
                )
            if isinstance(caught.exception, OSError):
                self.assertIn(caught.exception.errno, (voice.errno.EACCES, voice.errno.EAGAIN))
            os.write(release_write, b"1")
            os.waitpid(child, 0)
            child = -1
            voice.fcntl.flock(probe, voice.fcntl.LOCK_EX | voice.fcntl.LOCK_NB)
        finally:
            try:
                context.__exit__(None, None, None)
            except Exception:
                pass
            if child > 0:
                try:
                    os.write(release_write, b"1")
                except OSError:
                    pass
                os.waitpid(child, 0)
            for descriptor in (probe, ready_read, release_write):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    def test_linux_parent_disables_and_verifies_process_dumpability(self) -> None:
        class FakePrctl:
            argtypes: object = None
            restype: object = None

            def __init__(self, *, fail: bool = False) -> None:
                self.dumpable = 1
                self.fail = fail

            def __call__(
                self,
                operation: int,
                value: int,
                _third: int,
                _fourth: int,
                _fifth: int,
            ) -> int:
                if self.fail:
                    return -1
                if operation == voice._PR_SET_DUMPABLE:
                    self.dumpable = value
                    return 0
                if operation == voice._PR_GET_DUMPABLE:
                    return self.dumpable
                return -1

        class FakeLibrary:
            def __init__(self, prctl: FakePrctl) -> None:
                self.prctl = prctl

        control = FakePrctl()
        with (
            mock.patch.object(voice.sys, "platform", "linux"),
            mock.patch.object(
                voice.ctypes, "CDLL", return_value=FakeLibrary(control)
            ),
        ):
            self.assertTrue(voice.disable_process_dumpability())
        self.assertEqual(control.dumpable, 0)

        with (
            mock.patch.object(voice.sys, "platform", "linux"),
            mock.patch.object(
                voice.ctypes,
                "CDLL",
                return_value=FakeLibrary(FakePrctl(fail=True)),
            ),
            self.assertRaisesRegex(voice.VoiceArtifactError, "core-dump"),
        ):
            voice.disable_process_dumpability()

    def test_explicit_endpoint_key_model_and_standard_usage_response(self):
        self.assertEqual(voice.load_asr_api_config(), CONFIG)
        self.assertEqual(voice.parse_asr_api_transcript(
            b'{"text":"accepted","usage":{"type":"tokens","total_tokens":4}}'), "accepted")
        for key in ("", "bad\r\nheader", "x" * 4097):
            with self.assertRaises(voice.AsrApiError):
                voice.encode_asr_api_config(voice.AsrApiConfig(FAKE_ENDPOINT, key, FAKE_MODEL))

    def test_config_requires_owner_0600_regular_single_link(self) -> None:
        for target in (self.config,):
            with self.subTest(target=target.name, case="public"):
                target.chmod(0o640)
                with self.assertRaises(voice.AsrApiError):
                    voice.load_asr_api_config()
                target.chmod(0o600)
            with self.subTest(target=target.name, case="hardlink"):
                linked = self.root / (target.name + ".hardlink")
                os.link(target, linked)
                with self.assertRaises(voice.AsrApiError):
                    voice.load_asr_api_config()
                linked.unlink()
            with self.subTest(target=target.name, case="symlink"):
                saved = self.root / (target.name + ".saved")
                target.rename(saved)
                target.symlink_to(saved)
                with self.assertRaises(voice.AsrApiError):
                    voice.load_asr_api_config()
                target.unlink()
                saved.rename(target)

        self.root.chmod(0o755)
        with self.assertRaises(voice.AsrApiError):
            voice.load_asr_api_config()
        self.root.chmod(0o700)

        real_root = self.root.with_name(self.root.name + "-real")
        self.root.rename(real_root)
        self.root.symlink_to(real_root, target_is_directory=True)
        try:
            with self.assertRaises(voice.AsrApiError):
                voice.load_asr_api_config()
        finally:
            self.root.unlink()
            real_root.rename(self.root)

    def test_runtime_lock_requires_fixed_0600_regular_single_link_inode(self) -> None:
        self.lock.chmod(0o640)
        with self.assertRaises(voice.AsrApiError):
            voice.load_asr_api_config()
        self.lock.chmod(0o600)

        hardlink = self.root / ".config.lock-hardlink"
        os.link(self.lock, hardlink)
        with self.assertRaises(voice.AsrApiError):
            voice.load_asr_api_config()
        hardlink.unlink()

        saved = self.root / ".config.lock-saved"
        self.lock.rename(saved)
        self.lock.symlink_to(saved)
        try:
            with self.assertRaises(voice.AsrApiError):
                voice.load_asr_api_config()
        finally:
            self.lock.unlink()
            saved.rename(self.lock)

        original_flock = voice.fcntl.flock
        replaced = False

        def replace_after_open(descriptor: int, operation: int) -> None:
            nonlocal replaced
            if operation & voice.fcntl.LOCK_SH and not replaced:
                replaced = True
                old = self.root / ".config.lock-old"
                self.lock.rename(old)
                self.lock.touch(mode=0o600)
                self.lock.chmod(0o600)
            return original_flock(descriptor, operation)

        with (
            mock.patch.object(voice.fcntl, "flock", side_effect=replace_after_open),
            self.assertRaises(voice.AsrApiError),
        ):
            voice.load_asr_api_config()

    def test_wav_requires_private_single_link_complete_pcm_riff(self) -> None:
        transport = FakeTransport([response(200, {"text": "ok"})] * 5)
        self.wav.chmod(0o644)
        with self.assertRaises(voice.AsrApiError):
            self.client(transport).transcribe(self.wav)
        self.wav.chmod(0o600)

        linked = self.root / "voice-hardlink.wav"
        os.link(self.wav, linked)
        with self.assertRaises(voice.AsrApiError):
            self.client(transport).transcribe(self.wav)
        linked.unlink()

        self.wav.write_bytes(b"not-a-wave")
        self.wav.chmod(0o600)
        with self.assertRaises(voice.AsrApiError):
            self.client(transport).transcribe(self.wav)

        bad_format = bytearray(wav_bytes())
        struct.pack_into("<I", bad_format, 24, 48_000)
        self.wav.write_bytes(bad_format)
        self.wav.chmod(0o600)
        with self.assertRaises(voice.AsrApiError):
            self.client(transport).transcribe(self.wav)

        with self.wav.open("wb") as stream:
            stream.truncate(voice.ASR_API_MAX_WAV_BYTES + 1)
        self.wav.chmod(0o600)
        with self.assertRaises(voice.AsrApiError):
            self.client(transport).transcribe(self.wav)
        self.assertEqual(transport.calls, 0)

    def test_final_wav_requires_exact_0600_mode(self) -> None:
        self.wav.chmod(0o400)
        with self.assertRaises(voice.AsrApiError):
            voice.read_private_wav(self.wav)

    def test_final_wav_rejects_chmod_during_read(self) -> None:
        real_read = os.read
        mutated = False

        def read_then_chmod(descriptor: int, maximum: int) -> bytes:
            nonlocal mutated
            content = real_read(descriptor, maximum)
            if content and not mutated:
                mutated = True
                self.wav.chmod(0o400)
            return content

        try:
            with (
                mock.patch.object(voice.os, "read", side_effect=read_then_chmod),
                self.assertRaises(voice.AsrApiError),
            ):
                voice.read_private_wav(self.wav)
        finally:
            self.wav.chmod(0o600)

    def test_final_wav_rejects_hardlink_created_during_read(self) -> None:
        real_read = os.read
        linked = self.root / "voice-mid-read-hardlink.wav"
        mutated = False

        def read_then_link(descriptor: int, maximum: int) -> bytes:
            nonlocal mutated
            content = real_read(descriptor, maximum)
            if content and not mutated:
                mutated = True
                os.link(self.wav, linked)
            return content

        try:
            with (
                mock.patch.object(voice.os, "read", side_effect=read_then_link),
                self.assertRaises(voice.AsrApiError),
            ):
                voice.read_private_wav(self.wav)
        finally:
            linked.unlink(missing_ok=True)

    def test_final_wav_rejects_canonical_path_swap_during_read(self) -> None:
        real_read = os.read
        original = self.root / "voice-mid-read-original.wav"
        mutated = False

        def read_then_replace(descriptor: int, maximum: int) -> bytes:
            nonlocal mutated
            content = real_read(descriptor, maximum)
            if content and not mutated:
                mutated = True
                self.wav.rename(original)
                self.wav.write_bytes(wav_bytes())
                self.wav.chmod(0o600)
            return content

        try:
            with (
                mock.patch.object(voice.os, "read", side_effect=read_then_replace),
                self.assertRaises(voice.AsrApiError),
            ):
                voice.read_private_wav(self.wav)
        finally:
            original.unlink(missing_ok=True)

    def test_final_wav_rejects_truncation_during_read(self) -> None:
        real_read = os.read
        mutated = False

        def read_then_truncate(descriptor: int, maximum: int) -> bytes:
            nonlocal mutated
            content = real_read(descriptor, maximum)
            if content and not mutated:
                mutated = True
                os.truncate(self.wav, 44)
            return content

        with (
            mock.patch.object(voice.os, "read", side_effect=read_then_truncate),
            self.assertRaises(voice.AsrApiError),
        ):
            voice.read_private_wav(self.wav)

    def test_final_wav_rejects_growth_during_read(self) -> None:
        real_read = os.read
        mutated = False

        def read_then_grow(descriptor: int, maximum: int) -> bytes:
            nonlocal mutated
            content = real_read(descriptor, maximum)
            if content and not mutated:
                mutated = True
                with self.wav.open("ab") as stream:
                    stream.write(b"\x00\x00")
            return content

        with (
            mock.patch.object(voice.os, "read", side_effect=read_then_grow),
            self.assertRaises(voice.AsrApiError),
        ):
            voice.read_private_wav(self.wav)


class RetryAndCancellationTests(ClientFixture):



    def test_rejections_and_redirects_never_retry_or_echo_body(self):
        for status in (301, 302, 307, 308, 400, 401, 403, 404, 413, 422, 429, 500):
            for body in ({"error":{"message":FAKE_API_KEY}}, b"<html>private error</html>"):
                transport = FakeTransport([response(status, body, ("Location", "https://other.example.invalid"))])
                with self.assertRaises(voice.AsrApiError) as raised:
                    self.client(transport).transcribe(self.wav)
                self.assertEqual(transport.calls, 1)
                self.assertEqual(raised.exception.status, status)
                self.assertNotIn(FAKE_API_KEY, str(raised.exception))
                self.assertNotIn("private error", str(raised.exception))

    def test_502_and_503_retry_at_most_twice_and_retry_after_is_bounded(self) -> None:
        for status, retry_after, expected_delay in (
            (502, None, 7.0),
            (503, "1", 2.0),
            (503, "999", 7.0),
        ):
            with self.subTest(status=status, retry_after=retry_after):
                headers = () if retry_after is None else (("Retry-After", retry_after),)
                transport = FakeTransport(
                    [response(status, {"error": "temporary"}, *headers) for _ in range(3)]
                )
                clock = FakeClock()
                with self.assertRaises(voice.AsrApiError):
                    self.client(transport, clock).transcribe(self.wav)
                self.assertEqual(transport.calls, 3)
                self.assertAlmostEqual(clock.now - 100.0, expected_delay)

    def test_502_503_retry_before_parsing_empty_or_html_error_body(self) -> None:
        html = voice.AsrApiHttpResponse(
            502,
            (("Content-Type", "text/html"),),
            b"<html>temporary gateway failure</html>",
        )
        empty = voice.AsrApiHttpResponse(503, (), b"")
        transport = FakeTransport([html, empty, response(200, {"text": "recovered"})])
        self.assertEqual(self.client(transport).transcribe(self.wav), "recovered")
        self.assertEqual(transport.calls, 3)

        exhausted = FakeTransport([html, empty, html])
        with self.assertRaisesRegex(voice.AsrApiError, "服务重试已用尽"):
            self.client(exhausted).transcribe(self.wav)
        self.assertEqual(exhausted.calls, 3)

    def test_network_failure_retries_twice_then_stops(self) -> None:
        transport = FakeTransport([OSError(), OSError(), OSError()])
        clock = FakeClock()
        with self.assertRaisesRegex(voice.AsrApiError, "网络重试已用尽"):
            self.client(transport, clock).transcribe(self.wav)
        self.assertEqual(transport.calls, 3)
        self.assertAlmostEqual(clock.now - 100.0, 7.0)

    def test_cancellation_interrupts_retry_wait_without_replay(self) -> None:
        transport = FakeTransport([OSError(), response(200, {"text": "late"})])
        clock = FakeClock()
        with self.assertRaises(voice.AsrApiCancelled):
            self.client(transport, clock).transcribe(
                self.wav,
                cancelled=lambda: clock.now >= 100.1,
            )
        self.assertEqual(transport.calls, 1)
        self.assertLess(clock.now - 100.0, 0.2)

    def test_single_flight_rejects_a_concurrent_client(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class BlockingTransport:
            def post_wav(self, **_kwargs: object) -> voice.AsrApiHttpResponse:
                entered.set()
                release.wait(timeout=2)
                return response(200, {"text": "done"})

        first_result: list[object] = []
        first = self.client(BlockingTransport())

        def run_first() -> None:
            try:
                first_result.append(first.transcribe(self.wav))
            except Exception as exc:  # pragma: no cover - diagnostic capture
                first_result.append(exc)

        thread = threading.Thread(target=run_first)
        thread.start()
        self.assertTrue(entered.wait(timeout=1))
        try:
            with self.assertRaisesRegex(voice.AsrApiError, "正在进行"):
                self.client(FakeTransport([response(200, {"text": "second"})])).transcribe(
                    self.wav
                )
        finally:
            release.set()
            thread.join(timeout=2)
        self.assertEqual(first_result, ["done"])

    def test_provisioner_rollback_waits_for_runtime_snapshot_and_request(self) -> None:
        private = self.root / "provisioned"
        provision.provision(CONFIG, private_dir=private)
        runtime_wav = private / "voice.wav"
        runtime_wav.write_bytes(wav_bytes())
        runtime_wav.chmod(0o600)
        entered_request = threading.Event()
        release_request = threading.Event()

        class BlockingTransport:
            def post_wav(self, **_kwargs: object) -> voice.AsrApiHttpResponse:
                entered_request.set()
                self.assert_runtime_files_still_present()
                release_request.wait(timeout=3)
                return response(200, {"text": "locked"})

            @staticmethod
            def assert_runtime_files_still_present() -> None:
                assert (private / provision.CONFIG_NAME).is_file()
                assert (private / provision.CONFIG_NAME).is_file()

        transcribe_result: list[object] = []
        rollback_result: list[object] = []
        rollback_attempted = threading.Event()
        original_acquire = provision._acquire_private_lock

        def observed_exclusive_lock(directory_fd: int) -> int:
            rollback_attempted.set()
            return original_acquire(directory_fd)

        def run_transcribe() -> None:
            try:
                transcribe_result.append(
                    voice.AsrApiClient(transport=BlockingTransport()).transcribe(
                        runtime_wav
                    )
                )
            except Exception as exc:  # pragma: no cover - diagnostic capture
                transcribe_result.append(exc)

        def run_rollback() -> None:
            try:
                provision.remove(private_dir=private)
                rollback_result.append("rolled-back")
            except Exception as exc:  # pragma: no cover - diagnostic capture
                rollback_result.append(exc)

        with (
            mock.patch.multiple(
                voice,
                ASR_API_PRIVATE_DIR=private,
                ASR_API_CONFIG_FILE=private / provision.CONFIG_NAME,
                ASR_API_LOCK_FILE=private / provision.LOCK_NAME,
            ),
            mock.patch.object(
                provision,
                "_acquire_private_lock",
                side_effect=observed_exclusive_lock,
            ),
        ):
            transcribe_thread = threading.Thread(target=run_transcribe)
            transcribe_thread.start()
            self.assertTrue(entered_request.wait(timeout=1))
            rollback_thread = threading.Thread(target=run_rollback)
            rollback_thread.start()
            self.assertTrue(rollback_attempted.wait(timeout=1))
            time.sleep(0.1)
            self.assertTrue(rollback_thread.is_alive())
            self.assertTrue((private / provision.CONFIG_NAME).is_file())
            release_request.set()
            transcribe_thread.join(timeout=2)
            rollback_thread.join(timeout=2)

        self.assertFalse(transcribe_thread.is_alive())
        self.assertFalse(rollback_thread.is_alive())
        self.assertEqual(transcribe_result, ["locked"])
        self.assertEqual(rollback_result, ["rolled-back"])
        self.assertFalse((private / provision.CONFIG_NAME).exists())


class FixedTransportAndTargetGateTests(ClientFixture):
    @unittest.skipUnless(
        sys.platform.startswith("linux")
        and os.getuid() != 0
        and ctypes.util.find_library("seccomp") is not None,
        "real worker confinement requires non-root Linux and libseccomp",
    )
    def test_real_worker_confinement_denies_fork_before_https_primitives(self) -> None:
        code = (
            "import errno,os,socket,ssl,sys;"
            f"sys.path.insert(0,{str(KEYBOARD)!r});"
            "import voice_artifacts as v;"
            "assert v._confine_asr_api_worker_before_secret() is True;"
            "socket.getaddrinfo('localhost',443);ssl.create_default_context();"
            "\ntry: os.fork()\n"
            "except OSError as e:\n"
            " assert e.errno in (errno.EAGAIN,errno.EPERM)\n"
            "else: os._exit(91)\n"
            "print('worker-confinement=PASS')"
        )
        result = subprocess.run(
            (sys.executable, "-I", "-B", "-c", code),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8"},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout, b"worker-confinement=PASS\n")

    def test_https_transport_uses_configured_host_path_multipart_and_verified_tls(self) -> None:
        seen: dict[str, object] = {}

        class Socket:
            def settimeout(self, value: float) -> None:
                seen["read_timeout"] = value

        class HttpResult:
            status = 200

            @staticmethod
            def getheaders() -> list[tuple[str, str]]:
                return [("Content-Type", "application/json")]

            def read(self, _maximum: int) -> bytes:
                if seen.get("read"):
                    return b""
                seen["read"] = True
                return b'{"text":"ok"}'

        class Connection:
            def __init__(self, host: str, **kwargs: object) -> None:
                seen["host"] = host
                seen.update(kwargs)
                self.sock = Socket()

            @staticmethod
            def connect() -> None:
                seen["connected"] = True

            def request(
                self,
                method: str,
                path: str,
                *,
                body: bytes,
                headers: dict[str, str],
            ) -> None:
                seen.update(method=method, path=path, body=body, headers=headers)

            @staticmethod
            def getresponse() -> HttpResult:
                return HttpResult()

            @staticmethod
            def close() -> None:
                return None

        with mock.patch.object(voice.http.client, "HTTPSConnection", Connection):
            result = voice._perform_asr_api_https_request(
                CONFIG, wav_bytes(),
                10.0,
            )
        self.assertEqual(result.status, 200)
        self.assertEqual(seen["host"], "transcribe.example.invalid")
        self.assertEqual(seen["port"], 8443)
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/custom/audio/transcriptions")
        self.assertTrue(seen["connected"])
        context = seen["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, voice.ssl.CERT_REQUIRED)
        headers = seen["headers"]
        self.assertEqual(headers["Accept"], "application/json")
        self.assertTrue(headers["Content-Type"].startswith("multipart/form-data; boundary="))
        self.assertEqual(headers["Authorization"], "Bearer " + FAKE_API_KEY)
        self.assertNotIn("X-Device-Id", headers)
        self.assertNotIn("X-Client-Version", headers)
        import email.parser
        import email.policy
        parsed = email.parser.BytesParser(policy=email.policy.default).parsebytes(
            ("Content-Type: " + headers["Content-Type"] + "\r\n\r\n").encode() + seen["body"])
        parts = {part.get_param("name", header="content-disposition"): part for part in parsed.iter_parts()}
        self.assertEqual(set(parts), {"model", "response_format", "file"})
        self.assertEqual(parts["model"].get_payload(decode=True).decode(), FAKE_MODEL)
        self.assertEqual(parts["response_format"].get_payload(decode=True), b"json")
        self.assertEqual(parts["file"].get_payload(decode=True), wav_bytes())
        self.assertEqual(parts["file"].get_filename(), "audio.wav")
        self.assertNotIn(FAKE_API_KEY.encode(), seen["body"])

    def test_https_transport_allows_slow_first_byte_within_total_deadline(self) -> None:
        class Socket:
            def settimeout(self, _value: float) -> None:
                return None

        class HttpResult:
            status = 200

            @staticmethod
            def getheaders() -> list[tuple[str, str]]:
                return [("Content-Type", "application/json")]

            def read(self, _maximum: int) -> bytes:
                if getattr(self, "finished", False):
                    return b""
                self.finished = True
                return b'{"text":"slow but valid"}'

        class Connection:
            def __init__(self, _host: str, **_kwargs: object) -> None:
                self.sock = Socket()

            @staticmethod
            def connect() -> None:
                return None

            @staticmethod
            def request(
                _method: str,
                _path: str,
                *,
                body: bytes,
                headers: dict[str, str],
            ) -> None:
                assert body
                assert headers["Accept"] == "application/json"

            @staticmethod
            def getresponse() -> HttpResult:
                time.sleep(1.1)
                return HttpResult()

            @staticmethod
            def close() -> None:
                return None

        started = time.monotonic()
        with mock.patch.object(voice.http.client, "HTTPSConnection", Connection):
            result = voice._perform_asr_api_https_request(
                CONFIG, wav_bytes(),
                3.0,
            )
        self.assertGreaterEqual(time.monotonic() - started, 1.0)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.body, b'{"text":"slow but valid"}')

    def test_https_transport_enforces_bounded_total_deadline(self) -> None:
        marker = self.root / "request-after-deadline"
        code = (
            "import pathlib,sys,time;"
            "sys.stdin.buffer.read();time.sleep(1.0);"
            f"pathlib.Path({str(marker)!r}).touch()"
        )
        processes: list[object] = []
        real_popen = voice.subprocess.Popen

        def capture_process(*args: object, **kwargs: object) -> object:
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        started = time.monotonic()
        with (
            mock.patch.object(
                voice,
                "_asr_api_worker_command",
                return_value=(sys.executable, "-c", code),
            ),
            mock.patch.object(voice.subprocess, "Popen", side_effect=capture_process),
            self.assertRaises(voice.AsrApiError),
        ):
            voice._AsrApiHttpsTransport().post_wav(
                config=CONFIG,
                wav=wav_bytes(),
                deadline=time.monotonic() + 0.15,
                cancelled=lambda: False,
            )
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        with self.assertRaises(ProcessLookupError):
            os.killpg(processes[0].pid, 0)
        time.sleep(0.15)
        self.assertFalse(marker.exists())
        self.assertEqual(voice.ASR_API_TOTAL_TIMEOUT_SECONDS, 75.0)
        self.assertEqual(voice.ASR_API_CONNECT_TIMEOUT_SECONDS, 10.0)

    def test_worker_protocol_uses_stdin_only_and_returns_bounded_response(self) -> None:
        production_command = voice._asr_api_worker_command()
        self.assertEqual(production_command[1:3], ("-I", "-B"))
        self.assertEqual(production_command[-1], "--asr_api-https-worker")
        self.assertNotIn(FAKE_API_KEY, repr(production_command))
        self.assertNotIn(FAKE_MODEL, repr(production_command))
        expected = response(200, {"text": "isolated"})
        encoded = voice._encode_asr_api_worker_response(
            voice._ASR_API_WORKER_OK, expected
        )
        code = (
            "import sys;data=sys.stdin.buffer.read();"
            "assert data.startswith(b'PDSASQ02');"
            f"sys.stdout.buffer.write(bytes.fromhex({encoded.hex()!r}))"
        )
        real_popen = voice.subprocess.Popen
        invocations: list[tuple[object, object]] = []

        def inspect_process(args: object, **kwargs: object) -> object:
            serialized = repr(args) + repr(kwargs.get("env"))
            self.assertNotIn(FAKE_API_KEY, serialized)
            self.assertNotIn(FAKE_MODEL, serialized)
            invocations.append((args, kwargs.get("env")))
            return real_popen(args, **kwargs)

        with (
            mock.patch.object(
                voice,
                "_asr_api_worker_command",
                return_value=(sys.executable, "-c", code),
            ),
            mock.patch.object(voice.subprocess, "Popen", side_effect=inspect_process),
        ):
            actual = voice._AsrApiHttpsTransport().post_wav(
                config=CONFIG,
                wav=wav_bytes(),
                deadline=time.monotonic() + 3.0,
                cancelled=lambda: False,
            )
        self.assertEqual(actual, expected)
        self.assertEqual(len(invocations), 1)
        argv, environment = invocations[0]
        self.assertEqual(argv, [sys.executable, "-c", code])
        self.assertEqual(
            set(environment),
            {"PATH", "LANG", "LC_ALL"},
        )

    def test_cancel_kills_and_reaps_worker_before_return_without_late_request(self) -> None:
        marker = self.root / "request-after-cancel"
        code = (
            "import pathlib,sys,time;"
            "sys.stdin.buffer.read();time.sleep(0.6);"
            f"pathlib.Path({str(marker)!r}).touch()"
        )
        processes: list[object] = []
        real_popen = voice.subprocess.Popen

        def capture_process(*args: object, **kwargs: object) -> object:
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        started = time.monotonic()
        with (
            mock.patch.object(
                voice,
                "_asr_api_worker_command",
                return_value=(sys.executable, "-c", code),
            ),
            mock.patch.object(voice.subprocess, "Popen", side_effect=capture_process),
            self.assertRaises(voice.AsrApiCancelled),
        ):
            voice._AsrApiHttpsTransport().post_wav(
                config=CONFIG,
                wav=wav_bytes(),
                deadline=time.monotonic() + 3.0,
                cancelled=lambda: time.monotonic() - started >= 0.1,
            )
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        with self.assertRaises(ProcessLookupError):
            os.killpg(processes[0].pid, 0)
        time.sleep(0.65)
        self.assertFalse(marker.exists())

    def test_worker_start_failure_releases_transport_flight_lock(self) -> None:
        with (
            mock.patch.object(
                voice.subprocess,
                "Popen",
                side_effect=OSError("synthetic start failure"),
            ),
            self.assertRaises(voice.AsrApiError),
        ):
            voice._AsrApiHttpsTransport().post_wav(
                config=CONFIG,
                wav=wav_bytes(),
                deadline=time.monotonic() + 3.0,
                cancelled=lambda: False,
            )
        self.assertTrue(voice._ASR_API_TRANSPORT_FLIGHT.acquire(blocking=False))
        voice._ASR_API_TRANSPORT_FLIGHT.release()

    def test_parent_rejects_worker_stdout_overflow_and_reaps_process_group(self) -> None:
        code = (
            "import sys;sys.stdin.buffer.read();"
            f"sys.stdout.buffer.write(b'x'*{voice._ASR_API_MAX_WORKER_RESPONSE_BYTES + 1})"
        )
        processes: list[object] = []
        real_popen = voice.subprocess.Popen

        def capture_process(*args: object, **kwargs: object) -> object:
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with (
            mock.patch.object(
                voice,
                "_asr_api_worker_command",
                return_value=(sys.executable, "-c", code),
            ),
            mock.patch.object(voice.subprocess, "Popen", side_effect=capture_process),
            self.assertRaises(voice.AsrApiError),
        ):
            voice._AsrApiHttpsTransport().post_wav(
                config=CONFIG,
                wav=wav_bytes(),
                deadline=time.monotonic() + 3.0,
                cancelled=lambda: False,
            )
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        with self.assertRaises(ProcessLookupError):
            os.killpg(processes[0].pid, 0)

    def test_worker_inherits_shared_lock_until_request_exit(self) -> None:
        private = self.root / "worker-lock-inheritance"
        provision.provision(CONFIG, private_dir=private)
        encoded = voice._encode_asr_api_worker_response(
            voice._ASR_API_WORKER_OK,
            response(200, {"text": "child-held-lock"}),
        )
        marker = self.root / "worker-has-started"
        code = (
            "import pathlib,sys,time;sys.stdin.buffer.read();"
            f"pathlib.Path({str(marker)!r}).touch();time.sleep(0.6);"
            f"sys.stdout.buffer.write(bytes.fromhex({encoded.hex()!r}))"
        )
        lock_fd = os.open(private / provision.LOCK_NAME, os.O_RDONLY)
        voice.fcntl.flock(lock_fd, voice.fcntl.LOCK_SH)
        transport_result: list[object] = []
        rollback_result: list[object] = []
        rollback_attempted = threading.Event()
        original_acquire = provision._acquire_private_lock

        def run_transport() -> None:
            try:
                transport_result.append(
                    voice._AsrApiHttpsTransport().post_wav(
                        config=CONFIG,
                        wav=wav_bytes(),
                        deadline=time.monotonic() + 3.0,
                        cancelled=lambda: False,
                        credential_lock_fd=lock_fd,
                    )
                )
            except Exception as exc:  # pragma: no cover - diagnostic capture
                transport_result.append(exc)

        def observed_exclusive_lock(directory_fd: int) -> int:
            rollback_attempted.set()
            return original_acquire(directory_fd)

        def run_rollback() -> None:
            try:
                provision.remove(private_dir=private)
                rollback_result.append("rolled-back")
            except Exception as exc:  # pragma: no cover - diagnostic capture
                rollback_result.append(exc)

        transport_thread = threading.Thread(target=run_transport)
        rollback_thread: threading.Thread | None = None
        try:
            with mock.patch.object(
                voice,
                "_asr_api_worker_command",
                return_value=(sys.executable, "-c", code),
            ):
                transport_thread.start()
                deadline = time.monotonic() + 1.0
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())

                # Drop the parent's copy. Only the pass_fds copy in the worker
                # can now keep the shared flock alive.
                os.close(lock_fd)
                lock_fd = -1
                with mock.patch.object(
                    provision,
                    "_acquire_private_lock",
                    side_effect=observed_exclusive_lock,
                ):
                    rollback_thread = threading.Thread(target=run_rollback)
                    rollback_thread.start()
                    self.assertTrue(rollback_attempted.wait(timeout=1))
                    time.sleep(0.1)
                    self.assertTrue(rollback_thread.is_alive())
                    transport_thread.join(timeout=2)
                    rollback_thread.join(timeout=2)
        finally:
            if lock_fd >= 0:
                os.close(lock_fd)
            transport_thread.join(timeout=2)

        self.assertFalse(transport_thread.is_alive())
        self.assertIsNotNone(rollback_thread)
        assert rollback_thread is not None
        self.assertFalse(rollback_thread.is_alive())
        self.assertEqual(
            transport_result,
            [response(200, {"text": "child-held-lock"})],
        )
        self.assertEqual(rollback_result, ["rolled-back"])

    def test_worker_disables_dumpability_before_reading_pipe_or_fails_closed(self) -> None:
        class GuardedInput(io.BytesIO):
            def __init__(self, payload: bytes, gate: list[str]) -> None:
                super().__init__(payload)
                self.gate = gate

            def read(self, maximum: int = -1) -> bytes:
                self.assert_gate()
                return super().read(maximum)

            def assert_gate(self) -> None:
                if self.gate != ["dump-disabled", "process-confined"]:
                    raise AssertionError("credential pipe read before worker gates")

        request_bytes = voice._encode_asr_api_worker_request(
            CONFIG, wav_bytes(), 2.0
        )
        gate: list[str] = []
        output = io.BytesIO()

        def disable() -> bool:
            gate.append("dump-disabled")
            return True

        def confine() -> bool:
            gate.append("process-confined")
            return True

        def fake_request(
            config: voice.AsrApiConfig, wav: bytes, timeout: float
        ) -> voice.AsrApiHttpResponse:
            self.assertEqual(config, CONFIG)
            self.assertEqual(wav, wav_bytes())
            self.assertEqual(timeout, 2.0)
            return response(200, {"text": "gated"})

        with (
            mock.patch.object(voice, "disable_process_dumpability", side_effect=disable),
            mock.patch.object(
                voice, "_confine_asr_api_worker_before_secret", side_effect=confine
            ),
        ):
            return_code = voice._asr_api_https_worker_main(
                input_stream=GuardedInput(request_bytes, gate),
                output_stream=output,
                request=fake_request,
            )
        self.assertEqual(return_code, 0)
        self.assertEqual(
            voice._decode_asr_api_worker_response(output.getvalue()),
            response(200, {"text": "gated"}),
        )

        unread = GuardedInput(request_bytes, [])
        failed_output = io.BytesIO()
        with mock.patch.object(
            voice,
            "disable_process_dumpability",
            side_effect=voice.VoiceArtifactError("unavailable"),
        ):
            return_code = voice._asr_api_https_worker_main(
                input_stream=unread,
                output_stream=failed_output,
                request=fake_request,
            )
        self.assertEqual(return_code, voice._ASR_API_WORKER_DUMPABILITY_EXIT)
        self.assertEqual(unread.tell(), 0)
        self.assertEqual(failed_output.getvalue(), b"")

        confinement_unread = GuardedInput(request_bytes, ["dump-disabled"])
        with (
            mock.patch.object(voice, "disable_process_dumpability", return_value=True),
            mock.patch.object(
                voice,
                "_confine_asr_api_worker_before_secret",
                side_effect=voice.VoiceArtifactError("unavailable"),
            ),
        ):
            return_code = voice._asr_api_https_worker_main(
                input_stream=confinement_unread,
                output_stream=io.BytesIO(),
                request=fake_request,
            )
        self.assertEqual(return_code, voice._ASR_API_WORKER_CONFINEMENT_EXIT)
        self.assertEqual(confinement_unread.tell(), 0)

    def test_worker_protocol_rejects_all_length_magic_kind_and_header_edges(self) -> None:
        request_bytes = voice._encode_asr_api_worker_request(
            CONFIG, wav_bytes(), 2.0
        )
        bad_magic = bytearray(request_bytes)
        bad_magic[:8] = b"BADMAGIC"
        short_access = bytearray(request_bytes)
        struct.pack_into("!I", short_access, 8, 0)
        nan_timeout = bytearray(request_bytes)
        struct.pack_into("!d", nan_timeout, voice._ASR_API_WORKER_REQUEST.size - 8, float("nan"))
        for malformed in (
            b"",
            request_bytes[:-1],
            request_bytes + b"extra",
            bytes(bad_magic),
            bytes(short_access),
            bytes(nan_timeout),
        ):
            with self.subTest(request_size=len(malformed)):
                with self.assertRaises(voice.AsrApiError):
                    voice._decode_asr_api_worker_request(malformed)

        valid = voice._encode_asr_api_worker_response(
            voice._ASR_API_WORKER_OK,
            response(200, {"text": "bounded"}),
        )
        bad_response_magic = bytearray(valid)
        bad_response_magic[:8] = b"BADMAGIC"
        bad_kind = bytearray(valid)
        bad_kind[8] = 255
        oversized_body = bytearray(valid)
        struct.pack_into(
            "!I",
            oversized_body,
            11,
            voice.ASR_API_MAX_RESPONSE_BYTES + 1,
        )
        for malformed in (
            b"",
            valid[:-1],
            valid + b"extra",
            bytes(bad_response_magic),
            bytes(bad_kind),
            bytes(oversized_body),
            b"x" * (voice._ASR_API_MAX_WORKER_RESPONSE_BYTES + 1),
        ):
            with self.subTest(response_size=len(malformed)):
                with self.assertRaises(voice.AsrApiError):
                    voice._decode_asr_api_worker_response(malformed)

        duplicate_headers = voice.AsrApiHttpResponse(
            200,
            (("Content-Type", "application/json"), ("content-type", "application/json")),
            b'{"text":"bad"}',
        )
        with self.assertRaises(voice.AsrApiError):
            voice._encode_asr_api_worker_response(
                voice._ASR_API_WORKER_OK, duplicate_headers
            )
        with self.assertRaises(voice._AsrApiNetworkError):
            voice._decode_asr_api_worker_response(
                voice._encode_asr_api_worker_response(
                    voice._ASR_API_WORKER_NETWORK_ERROR
                )
            )
        with self.assertRaises(voice.AsrApiError):
            voice._decode_asr_api_worker_response(
                voice._encode_asr_api_worker_response(
                    voice._ASR_API_WORKER_PROTOCOL_ERROR
                )
            )

    def test_secrets_are_redacted_from_repr_and_all_protocol_errors(self) -> None:
        credentials = CONFIG
        hidden_response = voice.AsrApiHttpResponse(
            500,
            (),
            (FAKE_API_KEY + FAKE_MODEL).encode("ascii"),
        )
        self.assertNotIn(FAKE_API_KEY, repr(credentials))
        self.assertNotIn(FAKE_MODEL, repr(credentials))
        self.assertNotIn(FAKE_API_KEY, repr(hidden_response))
        self.assertNotIn(FAKE_MODEL, repr(hidden_response))

        malformed = (
            voice._encode_asr_api_worker_request(
                CONFIG, wav_bytes(), 2.0
            )
            + b"extra"
        )
        try:
            voice._decode_asr_api_worker_request(malformed)
        except voice.AsrApiError as exc:
            rendered = str(exc) + repr(exc)
            self.assertNotIn(FAKE_API_KEY, rendered)
            self.assertNotIn(FAKE_MODEL, rendered)
        else:  # pragma: no cover - fail if malformed input was accepted
            self.fail("malformed credential-bearing protocol was accepted")

    def test_generation_and_strong_target_change_discard_before_paste(self) -> None:
        snapshots: list[object] = []
        controller = VoiceSessionController(snapshots.append)
        original = (7, ("atspi", ":1.20", "/field/one"))
        changed = (8, ("atspi", ":1.21", "/field/two"))
        generation = controller.begin_recording(original)
        self.assertTrue(
            controller.begin_stopping(
                generation,
                original,
                target_is_valid=True,
            )
        )
        self.assertTrue(
            controller.begin_recognition(
                generation,
                original,
                target_is_valid=True,
            )
        )
        self.assertFalse(
            controller.prepare_paste(generation, changed, target_is_valid=True)
        )
        self.assertEqual(controller.state, VoiceState.DISCARDED)

    def test_runtime_has_no_retired_backend_and_cloud_precedes_local_fallback(self) -> None:
        runtime_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in KEYBOARD.glob("*.py")
        ).lower()
        retired_name = "wx" + "asr"
        self.assertNotIn(retired_name, runtime_sources)
        keyboard = (KEYBOARD / "pocketds-keyboard.py").read_text(encoding="utf-8")
        recognition = keyboard[
            keyboard.index("    def _recognize_voice(self, generation):") :
            keyboard.index("    def commit_voice_text(self, text, generation):")
        ]
        self.assertLess(
            recognition.index("self.asr_api.transcribe("),
            recognition.index("if sensevoice_backend_available("),
        )
        self.assertLess(
            recognition.index("if sensevoice_backend_available("),
            recognition.index("if not WHISPER_BIN.is_file()"),
        )
        self.assertIn("self.voice.prepare_paste(", keyboard)
        self.assertIn("self.voice.consume_paste(", keyboard)


if __name__ == "__main__":
    unittest.main(verbosity=2)
