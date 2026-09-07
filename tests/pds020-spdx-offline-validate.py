#!/usr/bin/env python3
"""Tests for pinned offline SPDX 3.0.1 structural and semantic validation."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/pds020-spdx-offline-validate.py"
LOCK = ROOT / "packaging/image/spdx-validation-lock.json"
SPEC = importlib.util.spec_from_file_location("pds020_spdx_offline_test", SOURCE)
assert SPEC and SPEC.loader
spdx = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = spdx
SPEC.loader.exec_module(spdx)


class FakeValidator:
    errors: list[object] = []

    def __init__(self, _schema: object) -> None:
        pass

    @staticmethod
    def check_schema(_schema: object) -> None:
        return None

    def iter_errors(self, _document: object) -> list[object]:
        return self.errors


class FakeJsonSchema:
    Draft202012Validator = FakeValidator


class FakeGraph:
    def __init__(self) -> None:
        self.kind = "empty"

    def parse(self, *, data: str, format: str) -> "FakeGraph":
        self.kind = format
        self.data = data
        return self

    def __len__(self) -> int:
        return 47 if self.kind == "json-ld" else 3737


def document() -> dict[str, object]:
    return {
        "@context": "https://spdx.org/rdf/3.0.1/spdx-context.jsonld",
        "type": "SpdxDocument",
        "spdxId": "https://example.invalid/document",
    }


def context() -> dict[str, object]:
    return {"@context": {"type": "@type", "spdxId": "@id"}}


def conforming_shacl(**_kwargs: object) -> tuple[bool, FakeGraph, str]:
    return True, FakeGraph(), "conforms"


class LockAndFileTests(unittest.TestCase):
    def test_real_lock_pins_official_301_materials_and_fedora_validators(self) -> None:
        lock = spdx.load_lock(LOCK)
        self.assertEqual(lock["specification"], spdx.SPECIFICATION)
        self.assertEqual(lock["policy"], spdx.POLICY)
        self.assertEqual(set(lock["artifacts"]), set(spdx.ARTIFACT_URLS))
        self.assertEqual(set(lock["validator"]), set(spdx.PACKAGE_NAMES))

    def test_duplicate_unknown_or_mutated_lock_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-spdx-lock-") as name:
            path = Path(name) / "lock.json"
            source = LOCK.read_text(encoding="utf-8")
            path.write_text(source.replace('"schema": 1', '"schema": 1, "schema": 1', 1))
            with self.assertRaisesRegex(spdx.SpdxValidationError, "duplicate"):
                spdx.load_lock(path)
            value = json.loads(source)
            value["policy"]["network_allowed"] = True
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(spdx.SpdxValidationError, "policy"):
                spdx.load_lock(path)

    def test_bound_artifact_rejects_hash_size_symlink_and_hardlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-spdx-artifact-") as name:
            root = Path(name)
            path = root / "artifact"
            path.write_bytes(b"official")
            record = {
                "size": 8,
                "sha256": hashlib.sha256(b"official").hexdigest(),
            }
            self.assertEqual(
                spdx.verify_bound_artifact(path, record, 100, "fixture"), b"official"
            )
            record["sha256"] = "0" * 64
            with self.assertRaisesRegex(spdx.SpdxValidationError, "differs"):
                spdx.verify_bound_artifact(path, record, 100, "fixture")
            link = root / "link"
            link.symlink_to(path)
            with self.assertRaisesRegex(spdx.SpdxValidationError, "unsafe"):
                spdx.read_regular(link, 100, "fixture")
            hard = root / "hard"
            os.link(path, hard)
            with self.assertRaisesRegex(spdx.SpdxValidationError, "metadata"):
                spdx.read_regular(path, 100, "fixture")

    def test_validator_package_query_requires_every_exact_identity(self) -> None:
        expected = {key: f"{name}-1.fc44.noarch" for key, name in spdx.PACKAGE_NAMES.items()}
        with mock.patch.object(spdx, "safe_executable"):
            with mock.patch.object(spdx.subprocess, "run") as run:
                run.side_effect = [
                    mock.Mock(returncode=0, stdout=(expected[key] + "\n").encode(), stderr=b"")
                    for key in spdx.PACKAGE_NAMES
                ]
                self.assertEqual(
                    spdx.verify_validator_packages(Path("/usr/bin/rpm"), expected), expected
                )
                self.assertEqual(run.call_count, 4)
        changed = dict(expected)
        changed["pyshacl"] += ".drift"
        with mock.patch.object(spdx, "safe_executable"):
            with mock.patch.object(
                spdx.subprocess,
                "run",
                return_value=mock.Mock(
                    returncode=0,
                    stdout=(expected["python"] + "\n").encode(),
                    stderr=b"",
                ),
            ):
                with self.assertRaisesRegex(spdx.SpdxValidationError, "differs"):
                    spdx.verify_validator_packages(Path("/usr/bin/rpm"), changed)


class ValidationTests(unittest.TestCase):
    def test_both_official_validation_layers_must_pass_offline(self) -> None:
        report = spdx.validate_document(
            document(),
            context(),
            {"type": "object"},
            "@prefix sh: <http://www.w3.org/ns/shacl#> .",
            FakeJsonSchema,
            FakeGraph,
            conforming_shacl,
        )
        self.assertEqual(report["rdf_triple_count"], 47)
        self.assertEqual(report["semantic_result_triple_count"], 3737)

    def test_json_schema_failure_cannot_reach_semantic_pass(self) -> None:
        FakeValidator.errors = [object()]
        try:
            with self.assertRaisesRegex(spdx.SpdxValidationError, "JSON Schema"):
                spdx.validate_document(
                    document(), context(), {}, "model", FakeJsonSchema, FakeGraph, conforming_shacl
                )
        finally:
            FakeValidator.errors = []

    def test_shacl_failure_and_nested_context_fail_closed(self) -> None:
        def failing_shacl(**_kwargs: object) -> tuple[bool, FakeGraph, str]:
            return False, FakeGraph(), "private detail"

        with self.assertRaisesRegex(spdx.SpdxValidationError, "OWL/SHACL"):
            spdx.validate_document(
                document(), context(), {}, "model", FakeJsonSchema, FakeGraph, failing_shacl
            )
        changed = document()
        changed["element"] = [{"@context": "https://attacker.invalid/context"}]
        with self.assertRaisesRegex(spdx.SpdxValidationError, "nested"):
            spdx.validate_document(
                changed, context(), {}, "model", FakeJsonSchema, FakeGraph, conforming_shacl
            )

    def test_strict_json_rejects_duplicate_nonfinite_and_non_utf8(self) -> None:
        for data in (b'{"a":1,"a":2}', b'{"a":NaN}', "{}".encode("utf-16")):
            with self.subTest(data=data[:12]):
                with self.assertRaises(spdx.SpdxValidationError):
                    spdx.strict_json(data, "fixture")

    def test_only_known_identical_official_schema_duplicates_are_accepted(self) -> None:
        accepted = (
            b'{"extension":{"type":"array"},"extension":{"type":"array"},'
            b'"prop_Element_extension":{"$ref":"x"},'
            b'"prop_Element_extension":{"$ref":"x"}}'
        )
        parsed = spdx.official_schema_json(accepted)
        self.assertEqual(parsed["extension"], {"type": "array"})
        for rejected in (
            accepted.replace(b'"type":"array"}', b'"type":"string"}', 1),
            b'{"unknown":1,"unknown":1}',
            b'{"extension":{},"prop_Element_extension":{}}',
        ):
            with self.subTest(rejected=rejected[:40]):
                with self.assertRaises(spdx.SpdxValidationError):
                    spdx.official_schema_json(rejected)

    def test_report_is_private_new_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-spdx-report-") as name:
            path = Path(name) / "report.json"
            spdx.write_report(str(path), {"verified": True})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                spdx.write_report(str(path), {"verified": False})
            output = io.BytesIO()
            with mock.patch.object(sys, "stdout", mock.Mock(buffer=output)):
                spdx.write_report("-", {"verified": True})
            self.assertTrue(json.loads(output.getvalue())["verified"])


class StaticTests(unittest.TestCase):
    def test_validator_has_no_network_download_or_legal_authorization_path(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            "urlopen",
            "requests.",
            "curl ",
            "wget ",
            "pip install",
            "dnf install",
            "do_owl_imports=True",
            '"network_allowed": True',
            '"release_ready": True',
        ):
            self.assertNotIn(forbidden, source)
        for required in (
            "Draft202012Validator",
            'format="json-ld"',
            'format="turtle"',
            "do_owl_imports=False",
            '"legal_conclusion": "NOT_DETERMINED"',
            '"rights_or_redistribution_inferred": False',
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
