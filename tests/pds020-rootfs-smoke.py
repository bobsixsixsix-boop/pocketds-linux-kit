#!/usr/bin/env python3
"""Tests for the read-only PDS-020 final rootfs smoke."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pds020-rootfs-smoke.py"
COMPOSITION_LOCK = ROOT / "packaging/image/composition-lock.json"
BLUEPRINT = ROOT / "packaging/image/pocketds-rootfs.template.toml"
ROOTFS_LOCK = ROOT / "packaging/pocketds-userspace/rootfs-transaction-lock.pds2.json"
BUILD_LOCK = ROOT / "packaging/pocketds-userspace/build-lock.pds2.json"
FAN = ROOT / "packaging/pocketds-userspace/pocketds-fancontrol.pds1"
SPEC = importlib.util.spec_from_file_location("pds020_rootfs_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


def completed(
    command: list[str], returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def passing_audit() -> dict[str, object]:
    return {
        "schema": 1,
        "read_only": True,
        "standard": {
            "sbom": "SPDX-3.0.1-JSON-LD",
            "sbom_validation": "structural-preflight-only",
        },
        "checks": [
            {
                "check_id": check_id,
                "status": "pass",
                "summary": "fixture passed",
                "evidence": {},
            }
            for check_id in sorted(smoke.RELEASE_AUDIT_CHECK_IDS)
        ],
        "blockers": [],
        "release_ready": True,
        "privacy": {"paths_disclosed": False},
    }


class Fixture:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.repo = base / "repository"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        blueprint = self.repo / "packaging/image" / BLUEPRINT.name
        blueprint.parent.mkdir(parents=True)
        blueprint.write_bytes(BLUEPRINT.read_bytes())
        self.root = base / "mounted-root"
        (self.root / "etc").mkdir(parents=True)
        (self.root / "usr").mkdir()
        (self.root / "etc/os-release").write_text(
            'NAME="Fedora Linux"\nID=fedora\nVERSION_ID=44\n', encoding="utf-8"
        )
        self.artifact = base / "container.tar"
        self.artifact.write_bytes(b"fixture osbuild container tar\n")
        self.rpm = base / "rpm"
        self.rpm.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        self.rpm.chmod(0o755)
        self.composition_lock = base / "composition-lock.json"
        self.composition_lock.write_bytes(COMPOSITION_LOCK.read_bytes())
        self.build_lock = base / "build-lock.json"
        self.build_lock.write_bytes(BUILD_LOCK.read_bytes())
        self.rootfs_lock = base / "rootfs-lock.json"
        self.manifest = self._manifest()
        lock = json.loads(ROOTFS_LOCK.read_text(encoding="utf-8"))
        lock["installed_stage"]["package_manifest_sha256"] = hashlib.sha256(
            self.manifest
        ).hexdigest()
        self.rootfs_lock.write_text(json.dumps(lock), encoding="utf-8")
        self.lock = lock
        self.build = json.loads(BUILD_LOCK.read_text(encoding="utf-8"))
        self._payload()

    def _manifest(self) -> bytes:
        rows = [
            f"fixture{index:03d}\t0\t1\t1.fc44\taarch64"
            for index in range(321)
        ]
        package = json.loads(BUILD_LOCK.read_text(encoding="utf-8"))["package"]
        rows.append(
            "\t".join(
                (
                    package["name"],
                    "0",
                    package["version"],
                    package["release"],
                    package["arch"],
                )
            )
        )
        return "".join(f"{row}\n" for row in sorted(rows)).encode()

    def _payload(self) -> None:
        for absolute in self.lock["installed_contract"]["critical_payload_paths"]:
            path = self.root.joinpath(*Path(absolute).parts[1:])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(FAN.read_bytes() if absolute.endswith("fancontrol") else b"fixture\n")
        wants = self.root / "etc/systemd/system/multi-user.target.wants"
        wants.mkdir(parents=True)
        for unit, target in self.lock["installed_contract"]["system_wants"].items():
            (wants / unit).symlink_to(target)

    def runner(self, command: list[str], _label: str, **_kwargs: object):
        if "-qa" in command:
            return completed(command, stdout=self.manifest)
        if "-V" in command:
            return completed(command)
        if "--qf" in command:
            package = self.build["package"]
            value = (
                f"{package['name']}-{package['version']}-{package['release']}."
                f"{package['arch']}\n"
            ).encode()
            return completed(command, stdout=value)
        if "-q" in command:
            name = command[-1]
            return completed(
                command,
                returncode=1,
                stdout=f"package {name} is not installed\n".encode(),
            )
        raise AssertionError(command)

    def verify(self) -> dict[str, object]:
        with mock.patch.object(smoke.audit, "run_audit", return_value=passing_audit()), mock.patch.object(
            smoke,
            "read_selinux_label",
            return_value=b"system_u:object_r:bin_t:s0",
        ):
            return smoke.verify(
                self.root,
                self.artifact,
                self.repo,
                self.composition_lock,
                self.rootfs_lock,
                self.build_lock,
                self.rpm,
                runner=self.runner,
            )


class RootfsSmokeTests(unittest.TestCase):
    def test_exact_offline_fixture_passes_without_release_overclaim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-smoke-") as name:
            fixture = Fixture(Path(name))
            report = fixture.verify()
        self.assertTrue(report["accepted"])
        self.assertEqual(report["package_count"], 322)
        self.assertEqual(report["architecture"], "aarch64")
        self.assertEqual(report["selinux_label_count"], 3)
        self.assertTrue(report["mounted_root_audit_passed"])
        self.assertFalse(report["network"])
        self.assertFalse(report["services_started"])
        self.assertFalse(report["device_root_touched"])
        self.assertFalse(report["release_ready"])

    def test_fedora_standard_os_release_link_is_the_only_link_accepted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-smoke-") as name:
            fixture = Fixture(Path(name))
            source = fixture.root / "etc/os-release"
            canonical = fixture.root / "usr/lib/os-release"
            canonical.parent.mkdir(parents=True, exist_ok=True)
            source.replace(canonical)
            source.symlink_to("../usr/lib/os-release")
            self.assertEqual(
                smoke.parse_os_release(fixture.root),
                {"id": "fedora", "version_id": "44"},
            )
            source.unlink()
            source.symlink_to("../../outside")
            with self.assertRaisesRegex(smoke.RootfsSmokeError, "link is unsafe"):
                smoke.parse_os_release(fixture.root)

    def test_package_duplicate_and_architecture_drift_fail(self) -> None:
        for mutation, error in (
            (lambda rows: rows + rows[:1], "malformed or duplicated"),
            (
                lambda rows: [rows[0].rsplit(b"\t", 1)[0] + b"\tx86_64", *rows[1:]],
                "architecture differs",
            ),
        ):
            with self.subTest(error=error):
                with tempfile.TemporaryDirectory(
                    prefix="pds020-rootfs-smoke-"
                ) as name:
                    fixture = Fixture(Path(name))
                    rows = fixture.manifest.splitlines()
                    output = b"\n".join(mutation(rows)) + b"\n"

                    def runner(command, _label, **_kwargs):
                        return completed(command, stdout=output)

                    with self.assertRaisesRegex(smoke.RootfsSmokeError, error):
                        smoke.package_manifest(fixture.rpm, fixture.root, runner)

    def test_composition_shapes_and_safety_boundary_fail_closed(self) -> None:
        for mutation, error in (
            (
                lambda lock: lock.__setitem__("builder", []),
                "composition builder fields",
            ),
            (
                lambda lock: lock["boundary"].__setitem__("flashable", True),
                "safe rootfs target",
            ),
            (
                lambda lock: lock["identity_policy"].__setitem__(
                    "machine_id_initialized", True
                ),
                "safe rootfs target",
            ),
        ):
            with self.subTest(error=error):
                with tempfile.TemporaryDirectory(
                    prefix="pds020-rootfs-smoke-"
                ) as name:
                    fixture = Fixture(Path(name))
                    lock = json.loads(
                        fixture.composition_lock.read_text(encoding="utf-8")
                    )
                    mutation(lock)
                    fixture.composition_lock.write_text(
                        json.dumps(lock), encoding="utf-8"
                    )
                    with self.assertRaisesRegex(smoke.RootfsSmokeError, error):
                        fixture.verify()

    def test_artifact_payload_and_system_wants_are_content_bound(self) -> None:
        for mutation, error in (
            ("artifact", "artifact.*unsafe"),
            ("fan", "fan controller differs"),
            ("wants", "system wants link differs"),
        ):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory(
                    prefix="pds020-rootfs-smoke-"
                ) as name:
                    fixture = Fixture(Path(name))
                    if mutation == "artifact":
                        fixture.artifact.write_bytes(b"")
                    elif mutation == "fan":
                        (fixture.root / "usr/bin/pocketds-fancontrol").write_bytes(
                            b"changed\n"
                        )
                    else:
                        link = (
                            fixture.root
                            / "etc/systemd/system/multi-user.target.wants"
                            / "pocketds-fancontrol.service"
                        )
                        link.unlink()
                        link.symlink_to("/wrong")
                    with self.assertRaisesRegex(smoke.RootfsSmokeError, error):
                        fixture.verify()

    def test_selinux_and_mounted_root_audit_are_mandatory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-smoke-") as name:
            fixture = Fixture(Path(name))
            with mock.patch.object(
                smoke,
                "read_selinux_label",
                side_effect=smoke.RootfsSmokeError("rootfs SELinux label is missing"),
            ):
                with self.assertRaisesRegex(smoke.RootfsSmokeError, "SELinux label"):
                    smoke.verify(
                        fixture.root,
                        fixture.artifact,
                        fixture.repo,
                        fixture.composition_lock,
                        fixture.rootfs_lock,
                        fixture.build_lock,
                        fixture.rpm,
                        runner=fixture.runner,
                    )
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-smoke-") as name:
            fixture = Fixture(Path(name))
            report = passing_audit()
            report["checks"][0]["status"] = "fail"
            report["blockers"] = [report["checks"][0]["check_id"]]
            report["release_ready"] = False
            with mock.patch.object(smoke.audit, "run_audit", return_value=report), mock.patch.object(
                smoke,
                "read_selinux_label",
                return_value=b"system_u:object_r:bin_t:s0",
            ):
                with self.assertRaisesRegex(smoke.RootfsSmokeError, "did not pass"):
                    smoke.verify(
                        fixture.root,
                        fixture.artifact,
                        fixture.repo,
                        fixture.composition_lock,
                        fixture.rootfs_lock,
                        fixture.build_lock,
                        fixture.rpm,
                        runner=fixture.runner,
                    )

        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-smoke-") as name:
            fixture = Fixture(Path(name))
            report = passing_audit()
            report["checks"][0]["check_id"] = "lookalike-passing-check"
            with mock.patch.object(smoke.audit, "run_audit", return_value=report), mock.patch.object(
                smoke,
                "read_selinux_label",
                return_value=b"system_u:object_r:bin_t:s0",
            ):
                with self.assertRaisesRegex(smoke.RootfsSmokeError, "did not pass"):
                    smoke.verify(
                        fixture.root,
                        fixture.artifact,
                        fixture.repo,
                        fixture.composition_lock,
                        fixture.rootfs_lock,
                        fixture.build_lock,
                        fixture.rpm,
                        runner=fixture.runner,
                    )

    def test_live_root_is_forbidden_before_tool_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-smoke-") as name:
            fixture = Fixture(Path(name))
            with mock.patch.object(
                smoke.audit,
                "resolve_target",
                return_value=(Path("/"), "live-development-root"),
            ), mock.patch.object(smoke, "safe_executable") as executable:
                with self.assertRaisesRegex(smoke.RootfsSmokeError, "live root"):
                    smoke.verify(
                        fixture.root,
                        fixture.artifact,
                        fixture.repo,
                        fixture.composition_lock,
                        fixture.rootfs_lock,
                        fixture.build_lock,
                        fixture.rpm,
                        runner=fixture.runner,
                    )
                executable.assert_not_called()

    def test_output_is_private_new_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pds020-rootfs-smoke-") as name:
            output = Path(name) / "report.json"
            smoke.validate_output(output)
            smoke.write_output(output, {"accepted": True})
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(smoke.RootfsSmokeError):
                smoke.validate_output(output)

    def test_plan_and_execution_gate_are_explicit(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(smoke.main([]), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["planned"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["release_ready"])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(smoke.main(["--execute"]), 1)
        self.assertFalse(json.loads(output.getvalue())["completed"])

    def test_source_has_no_mutation_network_or_service_start_path(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "os.remove",
            "os.unlink",
            "shutil",
            "systemctl",
            "dnf",
            "curl",
            "wget",
            "requests",
            "socket",
            "shell=True",
            "--install",
            "--erase",
            "--rebuilddb",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('"network": False', source)
        self.assertIn('"services_started": False', source)
        self.assertIn('"device_root_touched": False', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
