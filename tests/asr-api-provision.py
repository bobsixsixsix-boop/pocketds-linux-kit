#!/usr/bin/env python3
"""Offline checks for explicit API setup, atomic replacement and secret boundaries."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components/keyboard"))
import voice_artifacts as voice
SPEC = importlib.util.spec_from_file_location("asr_api_provision_test", ROOT / "scripts/pocketds-asr-api-provision.py")
provision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision)
CONFIG = voice.AsrApiConfig("https://transcribe.example.invalid/v1/audio/transcriptions", "fixture-key-private", "fixture/model")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pds-api-config-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = self.root / "asr-api"

    def load(self):
        with mock.patch.multiple(voice, ASR_API_PRIVATE_DIR=self.private,
            ASR_API_CONFIG_FILE=self.private / "config.json", ASR_API_LOCK_FILE=self.private / ".config.lock"):
            return voice.load_asr_api_config()

    def test_first_setup_replace_check_and_remove_only_touch_api_config(self):
        legacy = self.root / "personal-voice-settings"
        legacy.write_text("untouched")
        self.assertFalse(provision.check(private_dir=self.private))
        self.assertFalse(self.private.exists())
        provision.provision(CONFIG, private_dir=self.private)
        self.assertEqual(self.load(), CONFIG)
        self.assertEqual(self.private.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.private / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.private / ".config.lock").stat().st_mode & 0o777, 0o600)
        changed = voice.AsrApiConfig("https://second.example.invalid/custom/transcribe", "other-fixture-key", "other-model")
        provision.provision(changed, private_dir=self.private)
        self.assertEqual(self.load(), changed)
        self.assertTrue(provision.check(private_dir=self.private))
        provision.remove(private_dir=self.private)
        self.assertFalse(provision.check(private_dir=self.private))
        self.assertEqual(legacy.read_text(), "untouched")
        self.assertEqual({p.name for p in self.private.iterdir()}, {".config.lock"})

    def test_no_endpoint_key_or_model_is_inferred(self):
        encoded = json.loads(voice.encode_asr_api_config(CONFIG))
        for field in ("endpoint", "api_key", "model"):
            for value in (None, "", True, 1, []):
                data = dict(encoded, **{field:value})
                with self.assertRaises(voice.AsrApiError):
                    voice.parse_asr_api_config(json.dumps(data).encode())
            data = dict(encoded)
            del data[field]
            with self.assertRaises(voice.AsrApiError):
                voice.parse_asr_api_config(json.dumps(data).encode())

    def test_https_endpoint_cannot_contain_credentials_query_fragment_or_controls(self):
        invalid = ["http://example.invalid/v1/audio/transcriptions", "https://key@example.invalid/asr",
            "https://user:pass@example.invalid/asr", "https://example.invalid/asr?key=value",
            "https://example.invalid/asr#fragment", "https://example.invalid/asr?", "https://example.invalid/asr#",
            "https://example.invalid", "https:///asr", "https://example.invalid:0/asr", "https://example.invalid:65536/asr",
            "https://example.invalid/asr" + chr(10) + "injection", "https://example.invalid/as r",
            "https://example.invalid/" + chr(92) + "asr", "https://example.invalid//other"]
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(voice.AsrApiError):
                    provision.provision(voice.AsrApiConfig(endpoint, CONFIG.api_key, CONFIG.model), private_dir=self.private)
        self.assertFalse(self.private.exists())

    def test_model_rejects_multipart_injection_and_key_rejects_header_injection(self):
        for value in ("bad" + chr(13) + chr(10) + "injected", "bad" + chr(0), "x" * 4097):
            for config in (voice.AsrApiConfig(CONFIG.endpoint,value,CONFIG.model),
                           voice.AsrApiConfig(CONFIG.endpoint,CONFIG.api_key,value)):
                with self.assertRaises(voice.AsrApiError):
                    voice.encode_asr_api_config(config)

    def test_config_schema_duplicates_unknown_fields_nonfinite_and_deep_json_fail(self):
        data = voice.encode_asr_api_config(CONFIG)
        cases = [b"{}", b"[]", data.replace(b'"schema":1', b'"schema":1,"schema":1'),
                 data.replace(b'"schema":1', b'"schema":true'),
                 data.replace(b'"schema":1', b'"schema":NaN'),
                 data.replace(b'"schema":1', b'"schema":1,"other":1'),
                 b"[" * 2000 + b"]" * 2000, b"x" * (voice.ASR_API_MAX_CONFIG_BYTES + 1)]
        for case in cases:
            with self.assertRaises(voice.AsrApiError):
                voice.parse_asr_api_config(case)

    def test_public_linked_fifo_and_wrong_owner_configuration_is_refused(self):
        provision.provision(CONFIG, private_dir=self.private)
        target = self.private / "config.json"
        content = target.read_bytes()
        target.chmod(0o644)
        with self.assertRaises(provision.ProvisionError):
            provision.provision(CONFIG, private_dir=self.private)
        target.chmod(0o600)
        linked = self.root / "hardlink"
        os.link(target, linked)
        with self.assertRaises(voice.AsrApiError): self.load()
        linked.unlink()
        target.unlink()
        target.symlink_to(self.root / "unrelated")
        with self.assertRaises(provision.ProvisionError): provision.check(private_dir=self.private)
        target.unlink()
        os.mkfifo(target, 0o600)
        with self.assertRaises(provision.ProvisionError): provision.check(private_dir=self.private)
        with self.assertRaises(voice.AsrApiError): self.load()
        target.unlink()
        target.write_bytes(content)
        target.chmod(0o600)
        with mock.patch.object(voice.os, "getuid", return_value=os.getuid()+1):
            with self.assertRaises(voice.AsrApiError): self.load()

    def test_fifo_lock_is_rejected_without_blocking(self):
        provision.provision(CONFIG, private_dir=self.private)
        lock = self.private / ".config.lock"
        lock.unlink()
        os.mkfifo(lock, 0o600)
        with self.assertRaises(voice.AsrApiError): self.load()
        with self.assertRaises(provision.ProvisionError): provision.check(private_dir=self.private)

    def test_config_permission_change_during_read_cannot_authorize_upload(self):
        provision.provision(CONFIG, private_dir=self.private)
        original_read = voice.os.read
        target = self.private / "config.json"
        def change_permissions(descriptor, maximum):
            data = original_read(descriptor, maximum)
            target.chmod(0o644)
            return data
        with mock.patch.object(voice.os, "read", side_effect=change_permissions):
            with self.assertRaises(voice.AsrApiError): self.load()

    def test_unsafe_directory_is_not_repaired_silently(self):
        self.private.mkdir(mode=0o755)
        with self.assertRaises(provision.ProvisionError): provision.provision(CONFIG, private_dir=self.private)
        self.private.rmdir()
        other = self.root / "elsewhere"
        other.mkdir(mode=0o700)
        self.private.symlink_to(other)
        with self.assertRaises(provision.ProvisionError): provision.provision(CONFIG, private_dir=self.private)
        self.assertEqual(list(other.iterdir()), [])

    def test_replace_failure_preserves_complete_old_config_and_cleans_staging(self):
        provision.provision(CONFIG, private_dir=self.private)
        old = (self.private / "config.json").read_bytes()
        replacement = voice.AsrApiConfig(CONFIG.endpoint,"replacement-fixture-key",CONFIG.model)
        with mock.patch.object(provision.os,"replace",side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError): provision.provision(replacement, private_dir=self.private)
        self.assertEqual((self.private / "config.json").read_bytes(), old)
        self.assertEqual({p.name for p in self.private.iterdir()}, {"config.json", ".config.lock"})

    def test_cli_does_not_echo_accidental_secret_arguments_or_config_errors(self):
        for argv in (["provision", CONFIG.api_key], ["--api-key",CONFIG.api_key], []):
            result = subprocess.run([sys.executable,str(ROOT / "scripts/pocketds-asr-api-provision.py"),*argv],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertNotIn(CONFIG.api_key,result.stdout+result.stderr)
        with mock.patch.object(provision,"PRIVATE_DIR",self.private), mock.patch.object(provision,"_read_configuration",return_value=CONFIG):
            output=io.StringIO()
            with contextlib.redirect_stdout(output): self.assertEqual(provision.main(["provision"]),0)
            self.assertNotIn(CONFIG.api_key, output.getvalue())
            self.assertNotIn(CONFIG.endpoint, output.getvalue())
        self.assertEqual(self.load(),CONFIG)

    def test_noninteractive_config_requires_explicit_opt_in(self):
        with mock.patch.object(provision,"_read_configuration") as read:
            with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(provision.main(["provision","--stdin"]),2)
            read.assert_not_called()
        for name in ("--confirm-stdin","--fd3"):
            with mock.patch.object(provision,"PRIVATE_DIR",self.private), mock.patch.object(provision,"_read_configuration",return_value=CONFIG) as read:
                with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(provision.main(["provision",name]),0)
                read.assert_called_once_with("stdin" if name == "--confirm-stdin" else "fd3")

    def test_fd_reader_is_bounded_and_restore_of_private_presets_is_absent(self):
        read,write=os.pipe()
        try:
            os.write(write,b"0123456789")
            os.close(write)
            write=-1
            with self.assertRaises(provision.ProvisionError): provision._read_line_from_fd(read,4)
        finally:
            os.close(read)
            if write>=0: os.close(write)
        installer=(ROOT / "scripts/install.sh").read_text()
        self.assertNotIn("-restore.py",installer)
        self.assertNotIn("personal-preset",installer)
        self.assertNotIn("config.json",installer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
