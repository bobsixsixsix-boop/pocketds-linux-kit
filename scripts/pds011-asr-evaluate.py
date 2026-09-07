#!/usr/bin/env python3
"""Evaluate private Chinese ASR results without emitting corpus text."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import unicodedata
from typing import Sequence


CORPUS_SCHEMA = "pocketds.asr-corpus.v1"
RESULT_SCHEMA = "pocketds.asr-results.v1"
REPORT_SCHEMA = "pocketds.asr-evaluation.v1"
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_CASES = 200
MAX_TEXT_CHARS = 4_096
MAX_TOTAL_EDIT_CELLS = 5_000_000
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
DIGEST = re.compile(r"[0-9a-f]{64}")


class EvaluationError(RuntimeError):
    """An unsafe file, invalid schema or mismatched benchmark result."""


def read_private_json(path: Path, *, expected_uid: int | None = None) -> object:
    owner = os.getuid() if expected_uid is None else expected_uid
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvaluationError("benchmark input is unavailable or linked") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= MAX_INPUT_BYTES
        ):
            raise EvaluationError("benchmark input is public, unsafe or oversized")
        remaining = metadata.st_size
        content = bytearray()
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise EvaluationError("benchmark input changed while reading")
            content.extend(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise EvaluationError("benchmark input grew while reading")
    finally:
        os.close(descriptor)
    try:
        return json.loads(bytes(content).decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError("benchmark input is not valid UTF-8 JSON") from exc


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise EvaluationError(f"invalid {name}")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise EvaluationError(f"invalid {name}")
    return value


def _bounded_text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS or "\x00" in value:
        raise EvaluationError(f"invalid {name}")
    if not allow_empty and not value.strip():
        raise EvaluationError(f"empty {name}")
    return value


def parse_corpus(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "corpus_id",
        "language",
        "cases",
    }:
        raise EvaluationError("invalid corpus root")
    if payload["schema"] != CORPUS_SCHEMA or payload["language"] != "zh-CN":
        raise EvaluationError("unsupported corpus schema or language")
    corpus_id = _identifier(payload["corpus_id"], "corpus_id")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= MAX_CASES:
        raise EvaluationError("invalid corpus case count")
    cases: dict[str, dict[str, str]] = {}
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict) or set(raw_case) != {
            "id",
            "audio_sha256",
            "reference",
        }:
            raise EvaluationError("invalid corpus case")
        case_id = _identifier(raw_case["id"], "case id")
        if case_id in cases:
            raise EvaluationError("duplicate corpus case id")
        reference = _bounded_text(raw_case["reference"], "reference")
        if not normalize_text(reference):
            raise EvaluationError("reference normalizes to empty")
        cases[case_id] = {
            "audio_sha256": _digest(raw_case["audio_sha256"], "audio digest"),
            "reference": reference,
        }
    return {"corpus_id": corpus_id, "cases": cases}


def parse_results(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "corpus_id",
        "backend_id",
        "model_sha256",
        "cases",
    }:
        raise EvaluationError("invalid result root")
    if payload["schema"] != RESULT_SCHEMA:
        raise EvaluationError("unsupported result schema")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= MAX_CASES:
        raise EvaluationError("invalid result case count")
    cases: dict[str, dict[str, object]] = {}
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict) or set(raw_case) != {
            "id",
            "audio_sha256",
            "transcript",
            "latency_ms",
            "peak_rss_bytes",
            "returncode",
        }:
            raise EvaluationError("invalid result case")
        case_id = _identifier(raw_case["id"], "result case id")
        if case_id in cases:
            raise EvaluationError("duplicate result case id")
        transcript = _bounded_text(
            raw_case["transcript"], "transcript", allow_empty=True
        )
        latency = raw_case["latency_ms"]
        peak_rss = raw_case["peak_rss_bytes"]
        returncode = raw_case["returncode"]
        if (
            isinstance(latency, bool)
            or not isinstance(latency, int)
            or not 0 <= latency <= 300_000
            or isinstance(peak_rss, bool)
            or not isinstance(peak_rss, int)
            or not 0 <= peak_rss <= 16 * 1024**3
            or isinstance(returncode, bool)
            or not isinstance(returncode, int)
            or not -255 <= returncode <= 255
        ):
            raise EvaluationError("invalid result metrics")
        cases[case_id] = {
            "audio_sha256": _digest(raw_case["audio_sha256"], "result audio digest"),
            "transcript": transcript,
            "latency_ms": latency,
            "peak_rss_bytes": peak_rss,
            "returncode": returncode,
        }
    return {
        "corpus_id": _identifier(payload["corpus_id"], "result corpus_id"),
        "backend_id": _identifier(payload["backend_id"], "backend_id"),
        "model_sha256": _digest(payload["model_sha256"], "model digest"),
        "cases": cases,
    }


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character)[0] in {"L", "N"}
    )


def edit_distance(reference: str, hypothesis: str) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, source in enumerate(reference, start=1):
        current = [row]
        for column, target in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (source != target),
                )
            )
        previous = current
    return previous[-1]


def percentile(values: list[int | float], percentile_value: float) -> int | float:
    if not values:
        raise EvaluationError("cannot compute an empty percentile")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


def evaluate(
    corpus: dict[str, object],
    results: dict[str, object],
    *,
    max_micro_cer: float,
    max_p95_latency_ms: int,
    max_peak_rss_bytes: int,
) -> dict[str, object]:
    corpus_cases = corpus["cases"]
    result_cases = results["cases"]
    assert isinstance(corpus_cases, dict) and isinstance(result_cases, dict)
    if corpus["corpus_id"] != results["corpus_id"]:
        raise EvaluationError("corpus id mismatch")
    if set(corpus_cases) != set(result_cases):
        raise EvaluationError("result case set mismatch")

    total_reference = 0
    total_edits = 0
    case_cers: list[float] = []
    latencies: list[int] = []
    peak_rss_values: list[int] = []
    failure_count = 0
    edit_cells = 0
    for case_id in sorted(corpus_cases):
        expected = corpus_cases[case_id]
        observed = result_cases[case_id]
        assert isinstance(expected, dict) and isinstance(observed, dict)
        if expected["audio_sha256"] != observed["audio_sha256"]:
            raise EvaluationError("audio digest mismatch")
        reference = normalize_text(str(expected["reference"]))
        returncode = int(observed["returncode"])
        transcript = normalize_text(str(observed["transcript"])) if returncode == 0 else ""
        edit_cells += (len(reference) + 1) * (len(transcript) + 1)
        if edit_cells > MAX_TOTAL_EDIT_CELLS:
            raise EvaluationError("ASR edit-distance workload exceeds the safety budget")
        if returncode != 0 or not transcript:
            failure_count += 1
        edits = edit_distance(reference, transcript)
        total_reference += len(reference)
        total_edits += edits
        case_cers.append(edits / len(reference))
        latencies.append(int(observed["latency_ms"]))
        peak_rss_values.append(int(observed["peak_rss_bytes"]))
    micro_cer = total_edits / total_reference
    p95_latency = int(percentile(latencies, 0.95))
    peak_rss = max(peak_rss_values)
    gates = {
        "zero_backend_failures": failure_count == 0,
        "micro_cer_within_limit": micro_cer <= max_micro_cer,
        "p95_latency_within_limit": p95_latency <= max_p95_latency_ms,
        "peak_rss_within_limit": peak_rss <= max_peak_rss_bytes,
    }
    return {
        "schema": REPORT_SCHEMA,
        "privacy": {
            "audio_hashes_emitted": False,
            "paths_emitted": False,
            "reference_text_emitted": False,
            "transcripts_emitted": False,
        },
        "corpus_id": corpus["corpus_id"],
        "backend_id": results["backend_id"],
        "model_sha256": results["model_sha256"],
        "case_count": len(corpus_cases),
        "failure_count": failure_count,
        "total_reference_characters": total_reference,
        "edit_distance_cells": edit_cells,
        "micro_cer": round(micro_cer, 6),
        "mean_case_cer": round(sum(case_cers) / len(case_cers), 6),
        "p95_case_cer": round(float(percentile(case_cers, 0.95)), 6),
        "p50_latency_ms": int(percentile(latencies, 0.50)),
        "p95_latency_ms": p95_latency,
        "maximum_peak_rss_bytes": peak_rss,
        "limits": {
            "max_micro_cer": max_micro_cer,
            "max_p95_latency_ms": max_p95_latency_ms,
            "max_peak_rss_bytes": max_peak_rss_bytes,
        },
        "gates": gates,
        "accepted": all(gates.values()),
    }


def write_report(report: dict[str, object], destination: str) -> None:
    content = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if destination == "-":
        sys.stdout.buffer.write(content)
        return
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(destination, flags, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short ASR report write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", default="-")
    parser.add_argument("--max-micro-cer", type=float, required=True)
    parser.add_argument("--max-p95-latency-ms", type=int, required=True)
    parser.add_argument("--max-peak-rss-bytes", type=int, required=True)
    arguments = parser.parse_args(argv)
    if (
        not 0 <= arguments.max_micro_cer <= 1
        or not 1 <= arguments.max_p95_latency_ms <= 300_000
        or not 1 <= arguments.max_peak_rss_bytes <= 16 * 1024**3
    ):
        parser.error("benchmark limits are out of range")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        corpus = parse_corpus(read_private_json(arguments.corpus))
        results = parse_results(read_private_json(arguments.results))
        report = evaluate(
            corpus,
            results,
            max_micro_cer=arguments.max_micro_cer,
            max_p95_latency_ms=arguments.max_p95_latency_ms,
            max_peak_rss_bytes=arguments.max_peak_rss_bytes,
        )
        write_report(report, arguments.output)
    except (EvaluationError, OSError) as exc:
        print(f"ASR evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
