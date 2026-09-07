#!/usr/bin/env python3
"""Tests for the privacy-minimal PDS-011 ASR evaluator."""

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
SCRIPT = ROOT / "scripts/pds011-asr-evaluate.py"
SPEC = importlib.util.spec_from_file_location("pds011_asr_evaluate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def corpus(reference: str = "你好，Pocket DS！") -> dict[str, object]:
    return {
        "schema": MODULE.CORPUS_SCHEMA,
        "corpus_id": "private-zh-v1",
        "language": "zh-CN",
        "cases": [
            {"id": "case-01", "audio_sha256": DIGEST_A, "reference": reference}
        ],
    }


def results(transcript: str = "你好 pocket ds", returncode: int = 0) -> dict[str, object]:
    return {
        "schema": MODULE.RESULT_SCHEMA,
        "corpus_id": "private-zh-v1",
        "backend_id": "fixture-backend",
        "model_sha256": DIGEST_B,
        "cases": [
            {
                "id": "case-01",
                "audio_sha256": DIGEST_A,
                "transcript": transcript,
                "latency_ms": 1200,
                "peak_rss_bytes": 200_000_000,
                "returncode": returncode,
            }
        ],
    }


class MetricTests(unittest.TestCase):
    def test_nfkc_case_and_punctuation_normalization_is_exact(self) -> None:
        parsed_corpus = MODULE.parse_corpus(corpus())
        parsed_results = MODULE.parse_results(results())
        report = MODULE.evaluate(
            parsed_corpus,
            parsed_results,
            max_micro_cer=0,
            max_p95_latency_ms=1500,
            max_peak_rss_bytes=300_000_000,
        )
        self.assertTrue(report["accepted"])
        self.assertEqual(report["micro_cer"], 0)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("你好", serialized)
        self.assertNotIn(DIGEST_A, serialized)

    def test_errors_fail_declared_quality_gate_without_emitting_text(self) -> None:
        report = MODULE.evaluate(
            MODULE.parse_corpus(corpus("天地玄黄")),
            MODULE.parse_results(results("天地洪荒")),
            max_micro_cer=0.1,
            max_p95_latency_ms=1500,
            max_peak_rss_bytes=300_000_000,
        )
        self.assertEqual(report["micro_cer"], 0.5)
        self.assertFalse(report["gates"]["micro_cer_within_limit"])
        self.assertFalse(report["accepted"])

    def test_backend_failure_counts_as_empty_hypothesis(self) -> None:
        report = MODULE.evaluate(
            MODULE.parse_corpus(corpus("测试")),
            MODULE.parse_results(results("ignored", returncode=1)),
            max_micro_cer=1,
            max_p95_latency_ms=1500,
            max_peak_rss_bytes=300_000_000,
        )
        self.assertEqual(report["failure_count"], 1)
        self.assertEqual(report["micro_cer"], 1)
        self.assertFalse(report["gates"]["zero_backend_failures"])

    def test_edit_distance_workload_is_bounded_before_quadratic_work(self) -> None:
        long_text = "测" * 3000
        with self.assertRaisesRegex(MODULE.EvaluationError, "safety budget"):
            MODULE.evaluate(
                MODULE.parse_corpus(corpus(long_text)),
                MODULE.parse_results(results(long_text)),
                max_micro_cer=1,
                max_p95_latency_ms=1500,
                max_peak_rss_bytes=300_000_000,
            )


class SchemaTests(unittest.TestCase):
    def test_duplicate_mismatch_and_audio_drift_fail_closed(self) -> None:
        duplicate = corpus()
        duplicate["cases"].append(dict(duplicate["cases"][0]))
        with self.assertRaises(MODULE.EvaluationError):
            MODULE.parse_corpus(duplicate)
        parsed_corpus = MODULE.parse_corpus(corpus())
        parsed_results = MODULE.parse_results(results())
        parsed_results["corpus_id"] = "another-corpus"
        with self.assertRaises(MODULE.EvaluationError):
            MODULE.evaluate(
                parsed_corpus,
                parsed_results,
                max_micro_cer=1,
                max_p95_latency_ms=2000,
                max_peak_rss_bytes=300_000_000,
            )
        parsed_results = MODULE.parse_results(results())
        parsed_results["cases"]["case-01"]["audio_sha256"] = "c" * 64
        with self.assertRaises(MODULE.EvaluationError):
            MODULE.evaluate(
                parsed_corpus,
                parsed_results,
                max_micro_cer=1,
                max_p95_latency_ms=2000,
                max_peak_rss_bytes=300_000_000,
            )


class FileTests(unittest.TestCase):
    def test_private_reader_rejects_public_symlink_hardlink_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds011-eval-") as temporary:
            root = Path(temporary)
            path = root / "corpus.json"
            path.write_text(json.dumps(corpus()), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(MODULE.EvaluationError):
                MODULE.read_private_json(path)
            path.chmod(0o600)
            hardlink = root / "hardlink.json"
            os.link(path, hardlink)
            with self.assertRaises(MODULE.EvaluationError):
                MODULE.read_private_json(path)
            hardlink.unlink()
            path.unlink()
            target = root / "target.json"
            target.write_text(json.dumps(corpus()), encoding="utf-8")
            target.chmod(0o600)
            path.symlink_to(target)
            with self.assertRaises(MODULE.EvaluationError):
                MODULE.read_private_json(path)
            path.unlink()
            with path.open("wb") as stream:
                stream.truncate(MODULE.MAX_INPUT_BYTES + 1)
            path.chmod(0o600)
            with self.assertRaises(MODULE.EvaluationError):
                MODULE.read_private_json(path)

    def test_report_is_private_new_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds011-eval-") as temporary:
            output = Path(temporary) / "report.json"
            report = {"accepted": False}
            MODULE.write_report(report, str(output))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_report(report, str(output))


if __name__ == "__main__":
    unittest.main(verbosity=2)
