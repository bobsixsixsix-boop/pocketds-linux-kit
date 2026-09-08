#!/usr/bin/env python3
"""Isolated fixtures only: no real RPM query, ldd call or device access."""
from __future__ import annotations

import importlib.util
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("powerdevil_runtime_abi",
                                            ROOT / "scripts/powerdevil-runtime-abi.py")
abi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(abi)


class RuntimeAbiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pds-runtime-abi-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plugin = self.write("usr/lib64/qt6/qml/test/libplugin.so", b"\x7fELFmock-plugin")
        self.calls = []

    def write(self, relative, data, mode=0o644):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def check(self, result=(0, "libQt6Core.so.6 => /usr/lib64/libQt6Core.so.6 (0x1000)\n")):
        def runner(argv, **kwargs):
            self.calls.append((argv, kwargs))
            if isinstance(result, Exception):
                raise result
            return result
        return abi.check_runtime(root=self.root, runner=runner, owner_uid=os.getuid())

    def errors(self, report):
        return report["errors"] + [error for item in report["files"] for error in item["errors"]]

    def test_exit_zero_still_rejects_current_missing_constructor(self):
        symbol = "_ZN23QUntypedPropertyBindingC1EP23QPropertyBindingPrivate"
        report = self.check((0, f"undefined symbol: {symbol}, version Qt_6\t({self.plugin})\n"))
        self.assertEqual(report["status"], "fail")
        self.assertIn({"kind": "undefined-symbol", "symbol": symbol, "version": "Qt_6"}, self.errors(report))
        self.assertNotIn(str(self.root), repr(report))

    def test_exit_zero_missing_library_is_rejected(self):
        report = self.check((0, "\tlibQt6Core.so.6 => not found\n"))
        self.assertIn({"kind": "library-not-found", "library": "libQt6Core.so.6"}, self.errors(report))

    def test_missing_symbol_version_and_unfamiliar_errors_fail_closed(self):
        for text, kind in (("/lib/a.so: version `Qt_6' not found (required by /b.so)",
                            "symbol-version-not-found"),
                           ("loader: an unexpected dependency was not found", "unresolved-loader-error")):
            with self.subTest(kind=kind):
                self.assertEqual(self.errors(self.check((0, text)))[0]["kind"], kind)

    def test_command_timeout_fails(self):
        report = self.check(subprocess.TimeoutExpired("ldd", 4))
        self.assertEqual(self.errors(report), [{"kind": "ldd-timeout"}])

    def test_build_id_absolute_symlink_and_hardlink_are_one_elf(self):
        alias = self.root / "usr/lib/.build-id/ab/fixture"
        alias.parent.mkdir(parents=True)
        alias.symlink_to("../../../lib64/qt6/qml/test/libplugin.so")
        absolute = self.root / "usr/lib64/libalias.so"
        absolute.symlink_to("/usr/lib64/qt6/qml/test/libplugin.so")
        hardlink = self.root / "usr/lib64/libhardlink.so"
        os.link(self.plugin, hardlink)
        self.write("usr/share/doc/example.txt", b"not an ELF")
        report = self.check()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(len(report["files"]), 1)
        self.assertEqual(len(report["files"][0]["aliases"]), 4)
        self.assertEqual(len(self.calls), 1)

    def test_success_uses_target_environment_without_staged_or_inherited_loader_paths(self):
        with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/untrusted", "LD_PRELOAD": "/untrusted.so"}):
            report = self.check()
        self.assertEqual(report["status"], "pass")
        argv, options = self.calls[0]
        self.assertEqual(argv, ["/usr/bin/ldd", "-r", str(self.plugin)])
        self.assertEqual(options["env"], abi.LOADER_ENV)
        self.assertNotIn("LD_LIBRARY_PATH", options["env"])
        self.assertNotIn("LD_PRELOAD", options["env"])
        self.assertLessEqual(options["timeout"], abi.COMMAND_TIMEOUT)

    def test_package_internal_missing_library_is_explicit(self):
        self.write("usr/lib64/libpowerdevilcore.so.2", b"\x7fELFmock-core")
        report = self.check((0, "libpowerdevilcore.so.2 => not found\n"))
        self.assertTrue(all(error["kind"] == "package-library-not-found" for error in self.errors(report)))

    def test_existing_rpath_resolution_inside_stage_is_reported(self):
        internal = self.write("usr/lib64/libpowerdevilcore.so.2", b"\x7fELFmock-core")
        report = self.check((0, f"libpowerdevilcore.so.2 => {internal} (0x1000)\n"))
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["files"][0]["package_library_resolutions"], [
            {"library": "libpowerdevilcore.so.2", "path": "/usr/lib64/libpowerdevilcore.so.2"}])

    def test_group_writable_elf_and_wrong_root_owner_are_rejected_before_ldd(self):
        self.plugin.chmod(0o664)
        self.assertEqual(self.errors(self.check())[0]["kind"], "writable-path")
        self.assertEqual(self.calls, [])
        report = abi.check_runtime(root=self.root, owner_uid=os.getuid() + 1,
                                   runner=lambda *_args, **_kwargs: self.fail("must not run"))
        self.assertEqual(self.errors(report)[0]["kind"], "unsafe-root")

    def test_build_id_escape_or_dangling_binary_fails(self):
        alias = self.root / "usr/lib64/libescape.so"
        for target, kind in (("../../../../outside.so", "symlink-escape"),
                             ("missing.so", "file-or-command-unavailable")):
            with self.subTest(target=target):
                alias.symlink_to(target)
                self.assertEqual(self.errors(self.check())[0]["kind"], kind)
                alias.unlink()
        self.assertEqual(self.calls, [])

    def test_unbundled_documentation_link_does_not_block_staged_elf_check(self):
        alias = self.root / "usr/share/doc/HTML/common"
        alias.parent.mkdir(parents=True)
        alias.symlink_to("/usr/share/doc/HTML/external-common")
        self.assertEqual(self.check()["status"], "pass")

    def test_rpm_table_skips_only_explicit_not_installed_state(self):
        def runner(argv, **kwargs):
            self.assertEqual(argv[-1], "powerdevil")
            self.assertIn("FILESTATES", argv[3])
            return 0, "0\t/usr/lib64/plugin.so\n2\t/usr/share/doc/missing.txt\n"
        paths, skipped = abi.rpm_paths(runner, 10, lambda: 0)
        self.assertEqual(paths, ["/usr/lib64/plugin.so"])
        self.assertEqual(skipped, 1)
        for output in ("1\t/usr/lib64/plugin.so\n", "0\t/../../escaped\n", "invalid\n"):
            with self.subTest(output=output), self.assertRaises(abi.CheckError):
                abi.rpm_paths(lambda *_args, **_kwargs: (0, output), 10, lambda: 0)

    def test_total_budget_and_output_limit(self):
        with mock.patch.object(abi, "TOTAL_TIMEOUT", 0):
            self.assertEqual(self.errors(self.check())[0]["kind"], "total-timeout")
        self.assertEqual(self.calls, [])
        with mock.patch.object(abi, "MAX_OUTPUT_BYTES", 8):
            self.assertEqual(self.errors(self.check((0, "x" * 9)))[0]["kind"], "command-output-limit")

    def test_file_change_during_ldd_fails(self):
        def runner(*_args, **_kwargs):
            self.plugin.write_bytes(b"\x7fELFchanged during relocation check")
            return 0, ""
        report = abi.check_runtime(root=self.root, runner=runner, owner_uid=os.getuid())
        self.assertEqual(self.errors(report)[0]["kind"], "file-changed")

    def test_empty_tree_and_elf_size_limit_do_not_pass(self):
        with mock.patch.object(abi, "MAX_ELF_BYTES", 4):
            self.assertEqual(self.errors(self.check())[0]["kind"], "elf-size-or-count-limit")
        self.plugin.unlink()
        self.assertEqual(self.errors(self.check())[0]["kind"], "no-elf-files")

    def test_full_22_file_gate_rejects_qml_failure_when_other_21_pass(self):
        for i in range(21):
            self.write(f"usr/lib64/other/plugin{i}.so", b"\x7fELFfixture" + str(i).encode())
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            if argv[-1] == str(self.plugin):
                return 0, "undefined symbol: _ZN23QUntypedPropertyBindingC1EP23QPropertyBindingPrivate, version Qt_6.11_PRIVATE_API"
            return 0, ""
        report = abi.check_runtime(root=self.root, runner=runner, owner_uid=os.getuid(), expected_elf_count=22)
        self.assertEqual(report["elf_count"], 22)
        self.assertEqual(len(calls), 22)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(sum(row["status"] == "pass" for row in report["files"]), 21)
        failed = next(row for row in report["files"] if row["status"] == "fail")
        self.assertEqual(failed["sha256"], hashlib.sha256(self.plugin.read_bytes()).hexdigest())
        self.assertEqual(failed["errors"][0]["version"], "Qt_6.11_PRIVATE_API")

    def test_partial_package_cannot_pass_full_count_gate(self):
        report = abi.check_runtime(root=self.root, runner=lambda *_a, **_k: self.fail("must not run"),
                                   owner_uid=os.getuid(), expected_elf_count=22)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["errors"], [{"kind": "unexpected-elf-count"}])


    def test_checksum_rejects_growth_during_bounded_read(self):
        before = self.plugin.stat()
        original = os.read
        changed = False
        def growing(fd, size):
            nonlocal changed
            if not changed:
                changed = True
                with self.plugin.open("ab") as stream: stream.write(b"growth")
            return original(fd, size)
        with mock.patch.object(abi.os, "read", side_effect=growing):
            with self.assertRaises(abi.CheckError) as error:
                abi.elf_checksum(self.plugin, before, "/usr/lib64/plugin.so")
        self.assertEqual(error.exception.kind, "file-changed")



if __name__ == "__main__":
    unittest.main()
