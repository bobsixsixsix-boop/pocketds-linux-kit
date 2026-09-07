#!/usr/bin/env python3
"""Tests for the privacy-minimal PDS-018 supervised matrix evaluator."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds018-audio-matrix-evaluate.py"
SPEC = importlib.util.spec_from_file_location("pds018_audio_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BINDING = {
    "repo_revision": "a" * 40,
    "hifi_sha256": "b" * 64,
    "card_sha256": "c" * 64,
}


def common(attempts: int) -> dict[str, object]:
    return {
        "attempts": attempts,
        "passes": attempts,
        "failures": 0,
        "cancellations": 0,
        "pipewire_restart_delta": 0,
        "wireplumber_restart_delta": 0,
        "auto_null_observed": False,
        "xrun_count": 0,
        "route_mismatch_count": 0,
        "observer_confirmed": True,
    }


def complete_evidence() -> dict[str, object]:
    cases = {
        "speaker-left": common(1),
        "speaker-right": common(1),
        "speaker-stereo": common(1),
        "physical-microphone": {
            **common(3),
            "consent_confirmed": True,
            "monitor_source_used": False,
            "capture_cleanup_failures": 0,
            "clipping_count": 0,
        },
        "displayport-hotplug": {
            **common(20),
            "dp_sink_present_during_test": True,
            "speaker_recovered_after_disconnect": True,
        },
        "cold-boot": {**common(5), "speaker_recovered": True},
        "deep-resume": {**common(10), "speaker_recovered": True},
    }
    return {"schema": MODULE.EVIDENCE_SCHEMA, "binding": BINDING.copy(), "cases": cases}


class EvaluationTests(unittest.TestCase):
    def test_complete_supervised_matrix_passes_without_private_data(self) -> None:
        binding, cases = MODULE.parse_evidence(complete_evidence())
        report = MODULE.evaluate(binding, cases, BINDING)
        self.assertTrue(report["complete"])
        self.assertEqual(report["passed_case_count"], 7)
        serialized = json.dumps(report)
        for private in (
            "audio.wav",
            "SERIAL-PRIVATE",
            "/home/private",
            "PRIVATE TRANSCRIPT CONTENT",
        ):
            self.assertNotIn(private, serialized)

    def test_missing_case_is_incomplete_not_fabricated(self) -> None:
        raw = complete_evidence()
        del raw["cases"]["physical-microphone"]
        binding, cases = MODULE.parse_evidence(raw)
        report = MODULE.evaluate(binding, cases, BINDING)
        self.assertFalse(report["complete"])
        mic = next(item for item in report["cases"] if item["case_id"] == "physical-microphone")
        self.assertFalse(mic["present"])
        self.assertEqual(mic["attempts"], 0)

    def test_threshold_failure_and_observed_fault_fail_gates(self) -> None:
        raw = complete_evidence()
        raw["cases"]["displayport-hotplug"]["attempts"] = 19
        raw["cases"]["displayport-hotplug"]["passes"] = 19
        raw["cases"]["speaker-left"]["xrun_count"] = 1
        binding, cases = MODULE.parse_evidence(raw)
        report = MODULE.evaluate(binding, cases, BINDING)
        self.assertFalse(report["complete"])
        dp = next(item for item in report["cases"] if item["case_id"] == "displayport-hotplug")
        left = next(item for item in report["cases"] if item["case_id"] == "speaker-left")
        self.assertFalse(dp["gates"]["attempt_floor"])
        self.assertFalse(left["gates"]["no_xrun"])

    def test_microphone_requires_consent_physical_source_and_cleanup(self) -> None:
        raw = complete_evidence()
        mic = raw["cases"]["physical-microphone"]
        mic["consent_confirmed"] = False
        mic["monitor_source_used"] = True
        mic["capture_cleanup_failures"] = 1
        binding, cases = MODULE.parse_evidence(raw)
        report = MODULE.evaluate(binding, cases, BINDING)
        result = next(item for item in report["cases"] if item["case_id"] == "physical-microphone")
        self.assertFalse(result["pass"])
        self.assertFalse(result["gates"]["explicit_consent"])
        self.assertFalse(result["gates"]["physical_source_not_monitor"])
        self.assertFalse(result["gates"]["temporary_capture_cleaned"])

    def test_unknown_extra_wrong_type_and_bad_outcomes_are_rejected(self) -> None:
        raw = complete_evidence()
        raw["cases"]["invented"] = common(1)
        with self.assertRaises(MODULE.MatrixError):
            MODULE.parse_evidence(raw)

    def test_source_binding_mismatch_never_completes(self) -> None:
        binding, cases = MODULE.parse_evidence(complete_evidence())
        expected = {**BINDING, "repo_revision": "d" * 40}
        report = MODULE.evaluate(binding, cases, expected)
        self.assertFalse(report["binding_matches_current_source"])
        self.assertFalse(report["complete"])

    def test_binding_requires_exact_lowercase_hashes(self) -> None:
        for value in ("a" * 39, "A" * 40, "g" * 40, True):
            raw = complete_evidence()
            raw["binding"]["repo_revision"] = value
            with self.assertRaises(MODULE.MatrixError):
                MODULE.parse_evidence(raw)
        raw = complete_evidence()
        raw["cases"]["speaker-left"]["notes"] = "private"
        with self.assertRaises(MODULE.MatrixError):
            MODULE.parse_evidence(raw)
        raw = complete_evidence()
        raw["cases"]["speaker-left"]["attempts"] = True
        with self.assertRaises(MODULE.MatrixError):
            MODULE.parse_evidence(raw)
        raw = complete_evidence()
        raw["cases"]["speaker-left"]["failures"] = 1
        with self.assertRaises(MODULE.MatrixError):
            MODULE.parse_evidence(raw)

    def test_expected_binding_hashes_owned_regular_repo_sources(self) -> None:
        binding = MODULE.expected_binding("a" * 40, ROOT / "components/audio")
        self.assertEqual(binding["repo_revision"], "a" * 40)
        self.assertRegex(binding["hifi_sha256"], r"^[0-9a-f]{64}$")
        with tempfile.TemporaryDirectory(prefix="pds018-ucm-") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("ucm", encoding="utf-8")
            (root / "HiFi.conf").symlink_to(target)
            (root / "SM8550-APS.conf").write_text("ucm", encoding="utf-8")
            with self.assertRaises(MODULE.MatrixError):
                MODULE.expected_binding("a" * 40, root)


class FileTests(unittest.TestCase):
    def test_strict_json_rejects_duplicates_nonfinite_and_non_utf8(self) -> None:
        for content in (
            b'{"schema":1,"schema":1}',
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b"\xff",
        ):
            with self.assertRaises(MODULE.MatrixError):
                MODULE.strict_json(content)

    def test_private_reader_rejects_public_links_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds018-matrix-") as temporary:
            root = Path(temporary)
            path = root / "evidence.json"
            path.write_text(json.dumps(complete_evidence()), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(MODULE.MatrixError):
                MODULE.read_private(path)
            path.chmod(0o600)
            hardlink = root / "hardlink"
            os.link(path, hardlink)
            with self.assertRaises(MODULE.MatrixError):
                MODULE.read_private(path)
            hardlink.unlink()
            path.unlink()
            target = root / "target"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o600)
            path.symlink_to(target)
            with self.assertRaises(MODULE.MatrixError):
                MODULE.read_private(path)
            path.unlink()
            with path.open("wb") as stream:
                stream.truncate(MODULE.MAX_EVIDENCE_BYTES + 1)
            path.chmod(0o600)
            with self.assertRaises(MODULE.MatrixError):
                MODULE.read_private(path)

    def test_report_is_private_new_and_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds018-report-") as temporary:
            path = Path(temporary) / "report.json"
            MODULE.write_report({"complete": False}, str(path))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_report({"complete": True}, str(path))


class StaticTests(unittest.TestCase):
    def test_evaluator_cannot_play_record_route_or_change_power(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "paplay",
            "pw-play",
            "speaker-test",
            "arecord",
            "pw-record",
            "pactl",
            "amixer",
            "systemctl",
            "suspend",
            "reboot",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
