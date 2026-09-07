#!/usr/bin/env python3
"""Pure tests for the isolated PDS-012 synthetic ES-DE acceptance fixture."""

from __future__ import annotations

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
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts" / "pocketds-esde-synthetic-acceptance.py"
spec = importlib.util.spec_from_file_location("pocketds_esde_synthetic", SOURCE)
assert spec and spec.loader
synthetic = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = synthetic
spec.loader.exec_module(synthetic)


class SyntheticFixtureTests(unittest.TestCase):
    @staticmethod
    def healthy_fixture() -> object:
        return synthetic.FixtureSummary(
            systems=5,
            games_per_system=1_000,
            candidates=5_000,
            regular_files=5_000,
            symlinks=0,
            special_files=0,
            stopped="complete",
            logical_sha256="a" * 64,
        )

    @staticmethod
    def healthy_iterations() -> list[object]:
        return [
            synthetic.GuiIteration(
                iteration=index,
                startup_seconds=2.0,
                shutdown_seconds=0.5,
                log_bytes=4_096,
                launcher_status=-synthetic.signal.SIGTERM,
                log_startup_ms=1_500,
                complete_session_observed=True,
                summary_has_errors=False,
                input_mode_restored=True,
            )
            for index in range(1, 11)
        ]

    def test_fixture_is_isolated_empty_and_metadata_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pocketds-esde-fixture-test-") as temp:
            home = Path(temp).resolve()
            summary = synthetic.build_fixture(home, systems=3, games_per_system=7)
            self.assertEqual(summary.candidates, 21)
            self.assertEqual(summary.regular_files, 21)
            self.assertEqual(summary.symlinks, 0)
            self.assertEqual(summary.special_files, 0)
            self.assertEqual(summary.stopped, "complete")

            settings = ET.parse(
                home / "ES-DE/settings/es_settings.xml"
            ).getroot()
            rom_setting = settings.find("./string[@name='ROMDirectory']")
            self.assertIsNotNone(rom_setting)
            self.assertEqual(
                Path(rom_setting.attrib["value"]), home / "ROMs"
            )

            systems = ET.parse(
                home / "ES-DE/custom_systems/es_systems.xml"
            ).getroot()
            paths = [Path(node.text) for node in systems.findall("./system/path")]
            self.assertEqual(len(paths), 3)
            self.assertTrue(all(path.is_relative_to(home) for path in paths))
            commands = [node.text for node in systems.findall("./system/command")]
            self.assertEqual(commands, ["/usr/bin/false %ROM%"] * 3)
            gamelists = list((home / "ES-DE/gamelists").glob("*/gamelist.xml"))
            self.assertEqual(len(gamelists), 3)
            self.assertFalse(
                any(
                    path.name == "gamelist.xml"
                    for path in (home / "ROMs").rglob("*")
                )
            )

            candidates = list(
                (home / "ROMs").rglob("*.pdsfixture")
            )
            self.assertEqual(len(candidates), 21)
            self.assertTrue(all(path.stat().st_size == 0 for path in candidates))
            self.assertFalse(any(path.is_symlink() for path in home.rglob("*")))
            self.assertFalse(
                any(
                    path.suffix.lower()
                    in {".png", ".jpg", ".mp4", ".mp3", ".zip", ".7z"}
                    for path in home.rglob("*")
                )
            )
            xml_text = "\n".join(
                path.read_text(encoding="utf-8") for path in home.rglob("*.xml")
            )
            self.assertNotIn("http://", xml_text)
            self.assertNotIn("https://", xml_text)

    def test_fixture_passes_the_real_launcher_rom_directory_preflight(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pocketds-esde-launcher-test-") as temp:
            home = Path(temp).resolve()
            synthetic.build_fixture(home, systems=2, games_per_system=3)
            fake_app = home / "fake-es-de"
            fake_app.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_app.chmod(0o755)
            fake_input = home / "fake-input-mode"
            fake_input.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = status ]; then echo joymouse; exit 0; fi\n"
                "if [ \"$1\" = set ]; then exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_input.chmod(0o755)
            fake_supervisor = home / "fake-game-session"
            fake_supervisor.write_text(
                "#!/bin/sh\n"
                "while [ \"$1\" != -- ]; do shift; done\n"
                "shift\n"
                "exec \"$@\"\n",
                encoding="utf-8",
            )
            fake_supervisor.chmod(0o755)
            fake_runtime = home / "fake-game-runtime"
            fake_runtime.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = -- ]\n"
                "shift\n"
                "exec \"$@\"\n",
                encoding="utf-8",
            )
            fake_runtime.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "POCKETDS_ESDE_APPIMAGE": str(fake_app),
                    "POCKETDS_INPUT_MODE": str(fake_input),
                    "POCKETDS_GAME_SESSION_SUPERVISOR": str(fake_supervisor),
                    "POCKETDS_GAME_RUNTIME": str(fake_runtime),
                    "POCKETDS_ESDE_PREPARE": str(synthetic.PREPARE),
                    "POCKETDS_SKIP_INHIBIT": "1",
                }
            )
            result = subprocess.run(
                [
                    str(synthetic.LAUNCHER),
                    "--home",
                    str(home),
                    "--no-splash",
                    "--no-update-check",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_logical_fixture_hash_is_reproducible_across_temp_paths(self) -> None:
        summaries = []
        for _ in range(2):
            with tempfile.TemporaryDirectory(
                prefix="pocketds-esde-fixture-hash-"
            ) as temp:
                summaries.append(
                    synthetic.build_fixture(Path(temp), systems=2, games_per_system=4)
                )
        self.assertEqual(summaries[0].logical_sha256, summaries[1].logical_sha256)

    def test_default_cli_does_not_run_gui_or_consult_home(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pocketds-esde-home-canary-") as temp:
            home = Path(temp)
            forbidden = home / "ROMs"
            forbidden.mkdir()
            canary = forbidden / "must-not-be-read.pdsfixture"
            canary.write_bytes(b"private-canary")
            marker = home / "gui-was-started"
            fake_executable = home / "must-not-run"
            fake_executable.write_text(
                "#!/bin/sh\ntouch \"${POCKETDS_TEST_GUI_MARKER:?}\"\n",
                encoding="utf-8",
            )
            fake_executable.chmod(0o755)
            before = canary.stat()
            environment = os.environ.copy()
            environment["HOME"] = str(home)
            environment["POCKETDS_TEST_GUI_MARKER"] = str(marker)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE),
                    "--systems",
                    "2",
                    "--games-per-system",
                    "3",
                    "--appimage",
                    str(fake_executable),
                    "--input-mode",
                    str(fake_executable),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "prepare-only")
            self.assertEqual(payload["fixture"]["candidates"], 6)
            after = canary.stat()
            self.assertEqual(before.st_size, after.st_size)
            self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
            self.assertEqual(canary.read_bytes(), b"private-canary")
            self.assertFalse(marker.exists())

    def test_gui_requires_exact_environment_confirmation(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GUI test refused"):
                synthetic._require_gui_opt_in()

    def test_gui_cli_requires_confirmation_output_and_full_matrix(self) -> None:
        valid = [
            str(SOURCE),
            "--run-gui",
            "--confirm",
            synthetic.GUI_CONFIRMATION,
            "--output",
            "new.json",
        ]
        with mock.patch.object(sys, "argv", valid):
            _parser, args = synthetic.parse_args()
            self.assertTrue(args.run_gui)
        for extra in (
            [],
            ["--confirm", "wrong", "--output", "new.json"],
            [
                "--confirm",
                synthetic.GUI_CONFIRMATION,
                "--output",
                "new.json",
                "--iterations",
                "9",
            ],
            [
                "--confirm",
                synthetic.GUI_CONFIRMATION,
                "--output",
                "new.json",
                "--games-per-system",
                "999",
            ],
        ):
            with self.subTest(extra=extra), mock.patch.object(
                sys, "argv", [str(SOURCE), "--run-gui", *extra]
            ), self.assertRaises(SystemExit):
                synthetic.parse_args()

    def test_gui_evaluator_requires_all_ten_objective_and_attended_gates(self) -> None:
        iterations = self.healthy_iterations()
        report = synthetic.evaluate_gui(
            self.healthy_fixture(),
            iterations,
            startup_limit=10,
            shutdown_limit=3,
            repo_before=("b" * 40, True),
            repo_after=("b" * 40, True),
        )
        self.assertTrue(report["accepted"])
        serialized = json.dumps(report)
        self.assertNotIn("/home/", serialized)
        self.assertNotIn("PRIVATE-ROM", serialized)

        for field, value, gate in (
            ("startup_seconds", 10.1, "operator_interactive_within_limit"),
            ("log_startup_ms", 10_001, "internal_startup_within_limit"),
            ("shutdown_seconds", 3.1, "launcher_shutdown_within_limit"),
            ("complete_session_observed", False, "logs_nonempty_complete_and_error_free"),
            ("summary_has_errors", True, "logs_nonempty_complete_and_error_free"),
            ("input_mode_restored", False, "input_mode_restored_every_iteration"),
        ):
            with self.subTest(field=field):
                broken = self.healthy_iterations()
                broken[0] = synthetic.GuiIteration(
                    **{**synthetic.asdict(broken[0]), field: value}
                )
                result = synthetic.evaluate_gui(
                    self.healthy_fixture(),
                    broken,
                    startup_limit=10,
                    shutdown_limit=3,
                    repo_before=("b" * 40, True),
                    repo_after=("b" * 40, True),
                )
                self.assertFalse(result["accepted"])
                self.assertFalse(result["gates"][gate])

    def test_input_status_is_bounded_ascii_and_managed(self) -> None:
        healthy = subprocess.CompletedProcess([], 0, stdout=b"joymouse\n", stderr=b"")
        with mock.patch.object(synthetic.subprocess, "run", return_value=healthy):
            self.assertEqual(synthetic._input_status(Path("/safe/helper")), "joymouse")
        for result in (
            subprocess.CompletedProcess([], 1, stdout=b"", stderr=b""),
            subprocess.CompletedProcess([], 0, stdout=b"unknown\n", stderr=b""),
            subprocess.CompletedProcess([], 0, stdout=b"x" * 257, stderr=b""),
        ):
            with mock.patch.object(
                synthetic.subprocess, "run", return_value=result
            ), self.assertRaises(RuntimeError):
                synthetic._input_status(Path("/safe/helper"))

    def test_gui_report_binds_one_clean_repository_revision(self) -> None:
        result = synthetic.evaluate_gui(
            self.healthy_fixture(),
            self.healthy_iterations(),
            startup_limit=10,
            shutdown_limit=3,
            repo_before=("b" * 40, True),
            repo_after=("c" * 40, True),
        )
        self.assertFalse(result["accepted"])
        self.assertFalse(result["gates"]["repo_clean_revision_unchanged"])
        self.assertIsNone(result["repo_revision"])

    def test_gui_report_is_private_new_and_never_overwritten(self) -> None:
        report = synthetic.evaluate_gui(
            self.healthy_fixture(),
            self.healthy_iterations(),
            startup_limit=10,
            shutdown_limit=3,
            repo_before=("b" * 40, True),
            repo_after=("b" * 40, True),
        )
        with tempfile.TemporaryDirectory(prefix="pds012-gui-report-") as temporary:
            path = Path(temporary) / "report.json"
            synthetic.write_gui_report(report, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                synthetic.write_gui_report(report, path)
        with mock.patch.dict(
            os.environ, {synthetic.GUI_CONFIRMATION_ENV: "yes"}, clear=True
        ):
            with self.assertRaisesRegex(RuntimeError, "GUI test refused"):
                synthetic._require_gui_opt_in()

    def test_operator_can_cancel_without_waiting_for_timeout(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(synthetic.sys, "stdin", io.StringIO("q\n")):
            with mock.patch.object(
                synthetic.select, "select", return_value=([object()], [], [])
            ):
                with mock.patch("builtins.print"):
                    with self.assertRaises(KeyboardInterrupt):
                        synthetic._wait_for_operator(process, 10.0, 1)

    def test_stop_launcher_terminates_a_separate_process_session(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        elapsed = synthetic._stop_launcher(process, 3.0)
        self.assertLess(elapsed, 3.0)
        self.assertIn(
            process.returncode,
            {0, -synthetic.signal.SIGTERM, 128 + synthetic.signal.SIGTERM},
        )

    def test_gui_harness_is_not_installed_or_part_of_default_test(self) -> None:
        source_name = SOURCE.name
        installer = (ROOT / "scripts/install-emulation.sh").read_text(
            encoding="utf-8"
        )
        importer = (ROOT / "scripts/import-live.sh").read_text(encoding="utf-8")
        test_driver = (ROOT / "scripts/test.sh").read_text(encoding="utf-8")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertNotIn(source_name, installer)
        self.assertNotIn(source_name, importer)
        self.assertNotIn("test-emulation-synthetic-gui", test_driver)
        self.assertIn("test-emulation-synthetic-gui:", makefile)
        self.assertIn("--run-gui", makefile)
        self.assertIn("POCKETDS-RUN-ESDE-SYNTHETIC-GUI", makefile)
        self.assertIn("new private GUI report", makefile)
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('"ESDE_APPDATA_DIR": str(home / "ES-DE")', source)
        self.assertIn('"--home",\n                str(home)', source)
        self.assertIn('"--no-update-check"', source)
        self.assertNotIn('"--gamelist-only"', source)


if __name__ == "__main__":
    unittest.main()
