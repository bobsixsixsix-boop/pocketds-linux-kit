#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "reproducible-build" / "payload-evidence.py"
MODULE_SPEC = importlib.util.spec_from_file_location("pocketds_kernel_payload_evidence", SOURCE)
assert MODULE_SPEC and MODULE_SPEC.loader
payload_evidence = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = payload_evidence
MODULE_SPEC.loader.exec_module(payload_evidence)


def record(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "filename": path.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifacts = root / "artifacts"
        self.recipes = root / "recipes"
        self.reference = root / "reference"
        self.rebuilt = root / "rebuilt"
        for directory in (self.artifacts, self.recipes, self.reference, self.rebuilt):
            directory.mkdir()
        artifact_content = {
            name: (name + "\n").encode()
            for name in payload_evidence.ARTIFACT_NAMES
        }
        artifact_content["reference_rpm"] = b"signed-reference-rpm\n"
        artifact_content["attested_rebuilt_rpm"] = b"independent-rebuild-rpm\n"
        self.artifact_paths: dict[str, Path] = {}
        for name, content in artifact_content.items():
            path = self.artifacts / f"{name}.rpm"
            path.write_bytes(content)
            self.artifact_paths[name] = path
        self.recipe_paths: dict[str, Path] = {}
        for name in payload_evidence.RECIPE_FILE_NAMES:
            path = self.recipes / f"{name}.txt"
            path.write_text(name + "\n", encoding="utf-8")
            self.recipe_paths[name] = path
        key_paths = {
            "raw_image": "lib/modules/release/Image",
            "boot_image": "boot/Image-release",
            "system_map": "boot/System.map-release",
            "dtb": "boot/dtb-release/qcom/pocketds.dtb",
            "config": "boot/config-release",
        }
        for tree in (self.reference, self.rebuilt):
            for index, relative in enumerate(key_paths.values()):
                path = tree / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"payload-{index}\n".encode())
            (tree / "lib/modules/release/build").symlink_to("/usr/src/kernels/release")
        records, summary = payload_evidence.scan_tree(self.reference)
        by_path = {item["path"]: item for item in records}
        keys = {
            name: {
                "path": relative,
                "sha256": by_path[relative]["sha256"],
                "size": by_path[relative]["size"],
            }
            for name, relative in key_paths.items()
        }
        self.package_records = records
        self.lock: dict[str, object] = {
            "schema_version": 1,
            "copr_build_id": 42,
            "package_nevra": "kernel-fixture.aarch64",
            "source_commit": "a" * 40,
            "source_date_epoch": 1700000000,
            "environment": {
                "architecture": "aarch64",
                "distribution": "Fedora 44",
                "original_mock_version": "6.7",
                "reproducer_mock_version": "6.8",
                "compiler_nevra": "gcc-fixture",
                "libfaketime_nevra": "libfaketime-fixture",
                "build_user": "mockbuild",
                "build_host": "fixture",
                "build_timestamp": "Thu Jan 1 00:00:00 UTC 2026",
                "initramfs_regular_mtime_epoch": 1700000000,
                "initramfs_generated_mtime_epoch": 1700000100,
            },
            "artifacts": {
                name: record(path) for name, path in self.artifact_paths.items()
            },
            "recipe_files": {
                name: record(path) for name, path in self.recipe_paths.items()
            },
            "expected_payload": {
                **summary,
                "rpm_entry_count": summary["entry_count"],
                "implicit_directory_count": 0,
                "key_files": keys,
            },
        }

    def collect(self, raw: dict[str, object] | None = None) -> dict[str, object]:
        lock = payload_evidence.validate_lock(self.lock if raw is None else raw)
        return payload_evidence.collect(
            lock,
            artifact_dir=self.artifacts,
            recipe_root=self.recipes,
            reference_root=self.reference,
            rebuilt_root=self.rebuilt,
            package_manifest_reader=lambda _path, _expected: self.package_records,
        )


class KernelPayloadReproductionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_matching_payload_passes_without_claiming_rpm_container_identity(self) -> None:
        report = self.fixture.collect()
        self.assertTrue(report["comparison"]["payload_manifest_byte_identical"])
        self.assertFalse(report["comparison"]["rpm_container_byte_identical"])
        self.assertTrue(report["gates"]["source_to_binary_reproducible_build_proven"])
        self.assertFalse(report["gates"]["rollback_artifact_prevalidated"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["network"])

    def test_regular_file_content_difference_is_rejected(self) -> None:
        (self.fixture.rebuilt / "boot/config-release").write_bytes(b"payload-X\n")
        with self.assertRaisesRegex(payload_evidence.ReproductionError, "payload differs"):
            self.fixture.collect()

    def test_regular_file_mode_difference_is_rejected(self) -> None:
        (self.fixture.rebuilt / "boot/config-release").chmod(0o600)
        with self.assertRaisesRegex(payload_evidence.ReproductionError, "payload differs"):
            self.fixture.collect()

    def test_symlink_target_difference_is_rejected(self) -> None:
        link = self.fixture.rebuilt / "lib/modules/release/build"
        link.unlink()
        link.symlink_to("/wrong")
        with self.assertRaisesRegex(payload_evidence.ReproductionError, "payload differs"):
            self.fixture.collect()

    def test_special_payload_entry_is_rejected(self) -> None:
        fifo = self.fixture.rebuilt / "unexpected-fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(payload_evidence.ReproductionError, "unsupported payload"):
            self.fixture.collect()

    def test_locked_artifact_difference_is_rejected(self) -> None:
        self.fixture.artifact_paths["gcc"].write_bytes(b"tampered-gcc-rpm---\n")
        with self.assertRaisesRegex(payload_evidence.ReproductionError, "locked file differs"):
            self.fixture.collect()

    def test_candidate_rpm_header_manifest_difference_is_rejected(self) -> None:
        lock = payload_evidence.validate_lock(self.fixture.lock)
        changed = copy.deepcopy(self.fixture.package_records)
        changed[0]["mode"] = 0o600

        def package_reader(
            path: Path, _expected: dict[str, object]
        ) -> list[dict[str, object]]:
            if path.name == "attested_rebuilt_rpm.rpm":
                return changed
            return self.fixture.package_records

        with self.assertRaisesRegex(payload_evidence.ReproductionError, "RPM header payload"):
            payload_evidence.collect(
                lock,
                artifact_dir=self.fixture.artifacts,
                recipe_root=self.fixture.recipes,
                reference_root=self.fixture.reference,
                rebuilt_root=self.fixture.rebuilt,
                package_manifest_reader=package_reader,
            )

    def test_rpm_query_parser_rejects_non_sha256_digest_algorithm(self) -> None:
        with self.assertRaisesRegex(payload_evidence.ReproductionError, "SHA-256"):
            payload_evidence.parse_rpm_query("1\n")

    def test_rpm_query_parser_preserves_file_directory_and_symlink_metadata(self) -> None:
        digest = "b" * 64
        output = (
            "8\n"
            f"/boot/config\t3\t33188\t{digest}\t\n"
            "/boot/dtb\t0\t16877\t\t\n"
            "/lib/build\t8\t41471\t\t/usr/src\n"
        )
        self.assertEqual(
            payload_evidence.parse_rpm_query(output),
            [
                {
                    "path": "boot/config",
                    "type": "file",
                    "mode": 0o644,
                    "size": 3,
                    "sha256": digest,
                },
                {"path": "boot/dtb", "type": "directory", "mode": 0o755},
                {
                    "path": "lib/build",
                    "type": "symlink",
                    "mode": 0o777,
                    "target": "/usr/src",
                },
            ],
        )
        with self.assertRaisesRegex(payload_evidence.ReproductionError, "unsafe path"):
            payload_evidence.parse_rpm_query(
                f"8\n//boot/config\t3\t33188\t{digest}\t\n"
            )
        self.assertEqual(
            payload_evidence.parse_rpm_query(
                f"8\nkernel.spec\t3\t33188\t{digest}\t\n",
                absolute_paths=False,
            )[0]["path"],
            "kernel.spec",
        )
        with self.assertRaisesRegex(payload_evidence.ReproductionError, "invalid RPM source"):
            payload_evidence.parse_rpm_query(
                f"8\n/kernel.spec\t3\t33188\t{digest}\t\n",
                absolute_paths=False,
            )

    def test_unknown_lock_field_is_rejected(self) -> None:
        raw = copy.deepcopy(self.fixture.lock)
        raw["helpful"] = True
        with self.assertRaisesRegex(payload_evidence.ReproductionError, "lock fields differ"):
            self.fixture.collect(raw)

    def test_duplicate_json_key_is_rejected(self) -> None:
        path = Path(self.temporary.name) / "duplicate.json"
        path.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
        with self.assertRaisesRegex(payload_evidence.ReproductionError, "duplicate JSON key"):
            payload_evidence.load_lock(path)

    def test_cli_has_no_network_build_install_or_output_path(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SOURCE), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        for forbidden in ("--download", "--build", "--install", "--output", "--lock"):
            self.assertNotIn(forbidden, result.stdout)
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "os.system", "shell=True"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
