#!/usr/bin/env python3

from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "kernel-ab" / "candidate-build" / "ifpc-evidence.py"
MODULE_SPEC = importlib.util.spec_from_file_location("pocketds_ifpc_evidence", SOURCE)
assert MODULE_SPEC and MODULE_SPEC.loader
ifpc = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = ifpc
MODULE_SPEC.loader.exec_module(ifpc)


def artifact_record(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {"filename": path.name, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}


def member_record(content: bytes) -> dict[str, object]:
    return {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}


def source_manifest_record(name: str, content: bytes) -> dict[str, object]:
    return {"path": name, "type": "file", "mode": 0o644, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def make_boot(raw: bytes, dtb: bytes, image_id: bytes) -> tuple[bytes, bytes]:
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    kernel = compressed + dtb
    page = 2048
    header = bytearray(page)
    header[:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(kernel))
    struct.pack_into("<I", header, 36, page)
    header[576:608] = image_id
    padding = bytes((-len(kernel)) % page)
    return bytes(header) + kernel + padding, compressed


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.baseline_artifacts = root / "baseline-artifacts"
        self.candidate_artifacts = root / "candidate-artifacts"
        self.baseline_root = root / "baseline-root"
        self.candidate_root = root / "candidate-root"
        self.reproduced_artifacts = root / "reproduced-artifacts"
        self.reproduced_root = root / "reproduced-root"
        self.candidate_build = root / "candidate-build"
        self.patches = root / "patches"
        for directory in (
            self.baseline_artifacts,
            self.candidate_artifacts,
            self.baseline_root,
            self.candidate_root,
            self.reproduced_artifacts,
            self.reproduced_root,
            self.candidate_build,
            self.patches,
        ):
            directory.mkdir()

        self.baseline_spec = (
            b"Name: kernel\n"
            b"Source2:        pocketds-initramfs.tar.zst\n\n\nBuildRequires: bash\n"
            b"%prep\n%setup -q -n linux-pocketds-v%{tarfile_release}\n\n\n# Drop the .config\n"
        )
        self.candidate_spec = self.baseline_spec.replace(
            b"Source2:        pocketds-initramfs.tar.zst\n",
            b"Source2:        pocketds-initramfs.tar.zst\nPatch0:         0002-revert-a740-ifpc.patch\n",
        ).replace(
            b"%setup -q -n linux-pocketds-v%{tarfile_release}\n",
            b"%setup -q -n linux-pocketds-v%{tarfile_release}\n\npatch --batch --fuzz=0 --reject-file=- -p1 < %{PATCH0}\n",
        )
        self.revert_patch = b"fixture IFPC revert\n"
        self.spec_patch = b"fixture spec delta\n"
        (self.patches / "0002-revert-a740-ifpc.patch").write_bytes(self.revert_patch)
        (self.candidate_build / "ifpc-kernel-spec.patch").write_bytes(self.spec_patch)

        baseline_raw = bytearray(128)
        candidate_raw = bytearray(128)
        slices = {
            "quirk": (1, bytes.fromhex("b8"), bytes.fromhex("38")),
            "ifpc_pointer": (8, bytes.fromhex("609c40810080ffff"), bytes(8)),
            "build_id": (32, bytes([1]) * 20, bytes([2]) * 20),
            "relr": (60, bytes.fromhex("3f"), bytes.fromhex("2f")),
        }
        for offset, left, right in slices.values():
            baseline_raw[offset : offset + len(left)] = left
            candidate_raw[offset : offset + len(right)] = right
        self.baseline_raw = bytes(baseline_raw)
        self.candidate_raw = bytes(candidate_raw)
        self.dtb = b"fixture-dtb" * 7
        baseline_id = bytes(range(32))
        candidate_id = bytes((value ^ 0x55) if index < 20 else value for index, value in enumerate(range(32)))
        self.baseline_boot, baseline_gzip = make_boot(self.baseline_raw, self.dtb, baseline_id)
        self.candidate_boot, candidate_gzip = make_boot(self.candidate_raw, self.dtb, candidate_id)
        paths = {
            "boot_image": "boot/Image-fixture",
            "raw_image": "lib/modules/fixture/Image",
        }
        for tree, raw, boot in (
            (self.baseline_root, self.baseline_raw, self.baseline_boot),
            (self.candidate_root, self.candidate_raw, self.candidate_boot),
            (self.reproduced_root, self.candidate_raw, self.candidate_boot),
        ):
            (tree / paths["boot_image"]).parent.mkdir(parents=True)
            (tree / paths["boot_image"]).write_bytes(boot)
            (tree / paths["raw_image"]).parent.mkdir(parents=True)
            (tree / paths["raw_image"]).write_bytes(raw)
            (tree / "boot/config-fixture").write_bytes(b"same-config\n")

        self.baseline_tree_records, baseline_summary = ifpc.payload.scan_tree(self.baseline_root)
        self.candidate_tree_records, candidate_summary = ifpc.payload.scan_tree(self.candidate_root)
        self.reproduced_tree_records, reproduced_summary = ifpc.payload.scan_tree(self.reproduced_root)
        assert candidate_summary == reproduced_summary

        baseline_source_path = self.baseline_artifacts / "kernel.src.rpm"
        baseline_rpm_path = self.baseline_artifacts / "reference-kernel.rpm"
        candidate_source_path = self.candidate_artifacts / "kernel-ifpc-revert.src.rpm"
        candidate_rpm_path = self.candidate_artifacts / "kernel-ifpc-revert.aarch64.rpm"
        for path in (baseline_source_path, baseline_rpm_path, candidate_source_path, candidate_rpm_path):
            path.write_bytes((path.name + "\n").encode())
        reproduced_rpm_path = self.reproduced_artifacts / "kernel-ifpc-revert-reproduced.aarch64.rpm"
        reproduced_rpm_path.write_bytes(b"R" * candidate_rpm_path.stat().st_size)
        package_lines = ["a-1.aarch64", "b-1.noarch"]
        package_path = self.candidate_artifacts / "packages.nevra"
        package_path.write_text("\n".join(package_lines) + "\n", encoding="utf-8")
        compiler_lines = ["gcc fixture", "libfaketime fixture"]
        compiler_path = self.candidate_artifacts / "compiler.txt"
        compiler_path.write_text("\n".join(compiler_lines) + "\n", encoding="utf-8")
        date_sha = "1" * 64
        make_sha = "2" * 64
        wrappers_path = self.candidate_artifacts / "wrappers.sha256"
        wrappers_path.write_text(
            f"{date_sha}  /usr/local/bin/date\n{make_sha}  /usr/local/bin/make\n",
            encoding="utf-8",
        )
        log_path = self.candidate_artifacts / "candidate-build-root.log"
        log_path.write_text(
            "patching file drivers/gpu/drm/msm/adreno/a6xx_catalog.c\n"
            "Wrote: /builddir/build/RPMS/kernel-fixture.aarch64.rpm\n",
            encoding="utf-8",
        )
        baseline_spec_path = self.candidate_artifacts / "kernel-baseline.spec"
        candidate_spec_path = self.candidate_artifacts / "kernel-ifpc.spec"
        baseline_spec_path.write_bytes(self.baseline_spec)
        candidate_spec_path.write_bytes(self.candidate_spec)
        reproduced_package_path = self.reproduced_artifacts / "packages.nevra"
        reproduced_compiler_path = self.reproduced_artifacts / "compiler.txt"
        reproduced_wrappers_path = self.reproduced_artifacts / "wrappers.sha256"
        reproduced_log_path = self.reproduced_artifacts / "candidate-build-root.log"
        reproduced_package_path.write_bytes(package_path.read_bytes())
        reproduced_compiler_path.write_bytes(compiler_path.read_bytes())
        reproduced_wrappers_path.write_bytes(wrappers_path.read_bytes())
        reproduced_log_path.write_text(
            "independent build\n"
            "patching file drivers/gpu/drm/msm/adreno/a6xx_catalog.c\n"
            "Wrote: /builddir/build/RPMS/kernel-fixture.aarch64.rpm\n",
            encoding="utf-8",
        )

        self.unchanged_sources = {
            "linux.tar.gz": b"source",
            "initramfs.tar.zst": b"initramfs",
            "kernel.conf": b"config",
        }
        self.baseline_source_records = [
            source_manifest_record("kernel.spec", self.baseline_spec),
            *(source_manifest_record(name, content) for name, content in self.unchanged_sources.items()),
        ]
        self.candidate_source_records = [
            source_manifest_record("0002-revert-a740-ifpc.patch", self.revert_patch),
            source_manifest_record("kernel.spec", self.candidate_spec),
            *(source_manifest_record(name, content) for name, content in self.unchanged_sources.items()),
        ]
        self.baseline_source_records.sort(key=lambda item: item["path"])
        self.candidate_source_records.sort(key=lambda item: item["path"])
        actual_raw_differences = sum(a != b for a, b in zip(self.baseline_raw, self.candidate_raw))
        baseline_header = self.baseline_boot[:2048]
        candidate_header = self.candidate_boot[:2048]
        header_differences = sum(a != b for a, b in zip(baseline_header, candidate_header))
        self.lock = {
            "schema_version": 1,
            "experiment_id": "fixture-ifpc-only",
            "source_commit": "a" * 40,
            "revert_commit": "b" * 40,
            "package_nevra": "kernel-fixture.aarch64",
            "baseline_artifacts": {
                "source_rpm": artifact_record(baseline_source_path),
                "reference_rpm": artifact_record(baseline_rpm_path),
            },
            "candidate_artifacts": {
                "source_rpm": artifact_record(candidate_source_path),
                "binary_rpm": artifact_record(candidate_rpm_path),
                "package_manifest": artifact_record(package_path),
                "compiler_summary": artifact_record(compiler_path),
                "wrapper_manifest": artifact_record(wrappers_path),
                "build_log": artifact_record(log_path),
                "baseline_spec": artifact_record(baseline_spec_path),
                "candidate_spec": artifact_record(candidate_spec_path),
            },
            "reproduced_candidate_artifacts": {
                "binary_rpm": artifact_record(reproduced_rpm_path),
                "package_manifest": artifact_record(reproduced_package_path),
                "compiler_summary": artifact_record(reproduced_compiler_path),
                "wrapper_manifest": artifact_record(reproduced_wrappers_path),
                "build_log": artifact_record(reproduced_log_path),
            },
            "recipe_files": {
                "revert_patch": {"path": "../patches/0002-revert-a740-ifpc.patch", **member_record(self.revert_patch)},
                "spec_patch": {"path": "ifpc-kernel-spec.patch", **member_record(self.spec_patch)},
            },
            "environment": {
                "installed_package_count": len(package_lines),
                "installed_package_manifest_sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
                "compiler_lines": compiler_lines,
                "date_wrapper_sha256": date_sha,
                "make_wrapper_sha256": make_sha,
            },
            "source_rpm": {
                "spec_filename": "kernel.spec",
                "patch_filename": "0002-revert-a740-ifpc.patch",
                "unchanged_members": {name: member_record(content) for name, content in self.unchanged_sources.items()},
                "baseline_spec_sha256": hashlib.sha256(self.baseline_spec).hexdigest(),
                "candidate_spec_sha256": hashlib.sha256(self.candidate_spec).hexdigest(),
                "added_patch_sha256": hashlib.sha256(self.revert_patch).hexdigest(),
                "baseline_entry_count": len(self.baseline_source_records),
                "candidate_entry_count": len(self.candidate_source_records),
            },
            "payload": {
                "baseline_manifest_sha256": baseline_summary["manifest_sha256"],
                "candidate_manifest_sha256": candidate_summary["manifest_sha256"],
                "entry_count": baseline_summary["entry_count"],
                "regular_file_count": baseline_summary["regular_file_count"],
                "directory_count": baseline_summary["directory_count"],
                "symlink_count": baseline_summary["symlink_count"],
                "regular_file_bytes": baseline_summary["regular_file_bytes"],
                "rpm_entry_count": baseline_summary["entry_count"],
                "implicit_directory_count": 0,
                "unchanged_entry_count": baseline_summary["entry_count"] - 2,
                "unchanged_regular_file_count": baseline_summary["regular_file_count"] - 2,
                "changed_files": {
                    "boot_image": {
                        "path": paths["boot_image"],
                        "size": len(self.baseline_boot),
                        "baseline_sha256": hashlib.sha256(self.baseline_boot).hexdigest(),
                        "candidate_sha256": hashlib.sha256(self.candidate_boot).hexdigest(),
                    },
                    "raw_image": {
                        "path": paths["raw_image"],
                        "size": len(self.baseline_raw),
                        "baseline_sha256": hashlib.sha256(self.baseline_raw).hexdigest(),
                        "candidate_sha256": hashlib.sha256(self.candidate_raw).hexdigest(),
                    },
                },
                "raw_image_delta": {
                    "different_byte_count": actual_raw_differences,
                    **{
                        key: value
                        for name, (offset, left, right) in slices.items()
                        for key, value in (
                            (f"{name}_offset", offset),
                            (f"baseline_{name}_hex", left.hex()),
                            (f"candidate_{name}_hex", right.hex()),
                        )
                    },
                },
                "boot_derivation": {
                    "page_size": 2048,
                    "dtb_size": len(self.dtb),
                    "dtb_sha256": hashlib.sha256(self.dtb).hexdigest(),
                    "baseline_kernel_size": len(baseline_gzip) + len(self.dtb),
                    "candidate_kernel_size": len(candidate_gzip) + len(self.dtb),
                    "baseline_gzip_size": len(baseline_gzip),
                    "candidate_gzip_size": len(candidate_gzip),
                    "baseline_gzip_sha256": hashlib.sha256(baseline_gzip).hexdigest(),
                    "candidate_gzip_sha256": hashlib.sha256(candidate_gzip).hexdigest(),
                    "baseline_id_hex": baseline_id.hex(),
                    "candidate_id_hex": candidate_id.hex(),
                    "header_different_byte_count": header_differences,
                },
            },
        }

    def reader(self, path: Path, _expected: dict[str, object], **kwargs: object) -> list[dict[str, object]]:
        if kwargs.get("absolute_paths") is False:
            return copy.deepcopy(
                self.baseline_source_records if path.name == "kernel.src.rpm" else self.candidate_source_records
            )
        if path.name == "reference-kernel.rpm":
            records = self.baseline_tree_records
        elif path.name == "kernel-ifpc-revert-reproduced.aarch64.rpm":
            records = self.reproduced_tree_records
        else:
            records = self.candidate_tree_records
        return copy.deepcopy(records)

    def collect(self, raw: dict[str, object] | None = None) -> dict[str, object]:
        lock = ifpc.validate_lock(self.lock if raw is None else raw)
        with mock.patch.object(ifpc, "HERE", self.candidate_build):
            return ifpc.collect(
                lock,
                baseline_artifact_dir=self.baseline_artifacts,
                candidate_artifact_dir=self.candidate_artifacts,
                reproduced_candidate_artifact_dir=self.reproduced_artifacts,
                baseline_root=self.baseline_root,
                candidate_root=self.candidate_root,
                reproduced_candidate_root=self.reproduced_root,
                rpm_manifest_reader=self.reader,
            )


class IfpcCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_locked_candidate_passes_without_authorizing_install(self) -> None:
        report = self.fixture.collect()
        self.assertTrue(report["gates"]["single_variable_payload_delta_proven"])
        self.assertTrue(report["gates"]["candidate_independently_reproduced"])
        self.assertFalse(report["gates"]["rollback_artifact_prevalidated"])
        self.assertFalse(report["gates"]["candidate_install_authorized"])
        self.assertTrue(report["raw_image_delta"]["no_other_byte_difference"])
        self.assertTrue(report["boot_derivation"]["both_boot_images_derive_from_locked_raw_images"])

    def test_extra_changed_payload_file_is_rejected(self) -> None:
        (self.fixture.candidate_root / "boot/config-fixture").write_bytes(b"changed\n")
        with self.assertRaisesRegex(ifpc.CandidateError, "candidate payload manifest differs"):
            self.fixture.collect()

    def test_independent_reproduction_payload_drift_is_rejected(self) -> None:
        (self.fixture.reproduced_root / "boot/config-fixture").write_bytes(b"changed\n")
        with self.assertRaisesRegex(ifpc.CandidateError, "candidate payload manifest differs"):
            self.fixture.collect()

    def test_unchanged_source_member_drift_is_rejected(self) -> None:
        changed = copy.deepcopy(self.fixture.candidate_source_records)
        next(record for record in changed if record["path"] == "kernel.conf")["sha256"] = "f" * 64

        def reader(path: Path, expected: dict[str, object], **kwargs: object) -> list[dict[str, object]]:
            if kwargs.get("absolute_paths") is False and path.name != "kernel.src.rpm":
                return changed
            return self.fixture.reader(path, expected, **kwargs)

        lock = ifpc.validate_lock(self.fixture.lock)
        with self.assertRaisesRegex(ifpc.CandidateError, "unchanged member differs"):
            ifpc._verify_source_rpms(
                lock,
                self.fixture.baseline_artifacts / "kernel.src.rpm",
                self.fixture.candidate_artifacts / "kernel-ifpc-revert.src.rpm",
                reader,
            )

    def test_unlocked_raw_image_byte_difference_is_rejected(self) -> None:
        path = self.fixture.candidate_root / "lib/modules/fixture/Image"
        changed = bytearray(path.read_bytes())
        changed[100] ^= 1
        path.write_bytes(changed)
        with self.assertRaisesRegex(ifpc.CandidateError, "outside the locked slices"):
            ifpc._verify_raw_delta(
                ifpc.validate_lock(self.fixture.lock),
                self.fixture.baseline_root,
                self.fixture.candidate_root,
            )

    def test_boot_dtb_drift_is_rejected(self) -> None:
        path = self.fixture.candidate_root / "boot/Image-fixture"
        changed = bytearray(path.read_bytes())
        kernel_size = struct.unpack_from("<I", changed, 8)[0]
        changed[2048 + kernel_size - 1] ^= 1
        path.write_bytes(changed)
        with self.assertRaisesRegex(ifpc.CandidateError, "appended DTB differs"):
            ifpc._verify_boot_derivation(
                ifpc.validate_lock(self.fixture.lock),
                self.fixture.baseline_root,
                self.fixture.candidate_root,
            )

    def test_extra_spec_change_is_rejected(self) -> None:
        path = self.fixture.candidate_artifacts / "kernel-ifpc.spec"
        path.write_bytes(path.read_bytes() + b"# extra\n")
        with self.assertRaisesRegex(ifpc.CandidateError, "changes beyond"):
            ifpc._verify_specs(ifpc.validate_lock(self.fixture.lock), self.fixture.candidate_artifacts)

    def test_duplicate_patch_marker_is_rejected(self) -> None:
        path = self.fixture.candidate_artifacts / "candidate-build-root.log"
        path.write_bytes(path.read_bytes() + b"patching file drivers/gpu/drm/msm/adreno/a6xx_catalog.c\n")
        with self.assertRaisesRegex(ifpc.CandidateError, "exactly one target patch"):
            ifpc._verify_build_log(
                ifpc.validate_lock(self.fixture.lock),
                self.fixture.candidate_artifacts,
                self.fixture.lock["candidate_artifacts"],
            )

    def test_unknown_lock_field_and_duplicate_json_key_are_rejected(self) -> None:
        changed = copy.deepcopy(self.fixture.lock)
        changed["helpful"] = True
        with self.assertRaisesRegex(ifpc.CandidateError, "IFPC lock fields differ"):
            ifpc.validate_lock(changed)
        duplicate = self.fixture.root / "duplicate.json"
        duplicate.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
        with self.assertRaisesRegex(ifpc.CandidateError, "duplicate JSON key"):
            ifpc.payload.load_lock(duplicate)

    def test_cli_is_read_only_and_has_no_network_build_install_or_output(self) -> None:
        result = subprocess.run([sys.executable, str(SOURCE), "--help"], check=True, capture_output=True, text=True)
        for forbidden in ("--download", "--build", "--install", "--output", "--lock"):
            self.assertNotIn(forbidden, result.stdout)
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "os.system", "shell=True"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
