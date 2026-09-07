#!/usr/bin/env python3
"""Pure mock/static tests for PDS-008 KWin display telemetry."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DAEMON = ROOT / "components/telemetry/pocketds-gpu-telemetry.py"
SPEC = importlib.util.spec_from_file_location("pocketds_gpu_telemetry", DAEMON)
assert SPEC and SPEC.loader
telemetry = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = telemetry
SPEC.loader.exec_module(telemetry)

KWIN_FIXTURE = f"""\
KWin support information

Operation Mode: Wayland

LogicalOutput backend
==============
Name: DRM
Atomic Mode Setting on GPU 0: true

Screens
{"=" * 7}
Number of Screens: 2

Screen 0:
---------
Name: DSI-1
Enabled: 1
Geometry: 0,0,1280x720
Physical size: 160x89mm
Scale: 1.5
Refresh Rate: 165000
Adaptive Sync: incapable
Screen 1:
---------
Name: DSI-2
Enabled: 1
Geometry: 283,720,819x614
Physical size: 76x102mm
Scale: 1.25
Refresh Rate: 59999
Adaptive Sync: incapable

Compositing
===========
Compositing is active
Compositing Type: OpenGL
OpenGL vendor string: freedreno
OpenGL renderer string: FD740
OpenGL version string: 4.6 (Core Profile) Mesa 26.1.8
OpenGL platform interface: EGL
"""


class DisplayParserTests(unittest.TestCase):
    def test_exact_dual_screen_fixture_is_flattened(self):
        data = telemetry.parse_kwin_support_information(KWIN_FIXTURE)
        self.assertEqual(data["display_status"], "ok")
        self.assertEqual(data["display_session"], "Wayland")
        self.assertEqual(data["display_backend"], "DRM")
        self.assertEqual(data["display_compositor"], "OpenGL")
        self.assertEqual(data["display_renderer"], "FD740")
        self.assertEqual(data["display_gl_vendor"], "freedreno")
        self.assertEqual(data["display_gl_platform"], "EGL")
        self.assertEqual(data["display_dsi1_physical_width_px"], 1920)
        self.assertEqual(data["display_dsi1_physical_height_px"], 1080)
        self.assertEqual(data["display_dsi1_physical_width_mm"], 160)
        self.assertEqual(data["display_dsi1_physical_height_mm"], 89)
        self.assertEqual(data["display_dsi1_logical_width"], 1280)
        self.assertEqual(data["display_dsi1_logical_height"], 720)
        self.assertEqual(data["display_dsi1_scale"], 1.5)
        self.assertEqual(data["display_dsi1_refresh_hz"], 165.0)
        self.assertEqual(data["display_dsi2_logical_x"], 283)
        self.assertEqual(data["display_dsi2_logical_y"], 720)
        self.assertEqual(data["display_dsi2_logical_width"], 819)
        self.assertEqual(data["display_dsi2_logical_height"], 614)
        self.assertEqual(data["display_dsi2_scale"], 1.25)
        self.assertEqual(data["display_dsi2_physical_width_px"], 1024)
        self.assertEqual(data["display_dsi2_physical_height_px"], 768)
        self.assertEqual(data["display_dsi2_refresh_hz"], 59.999)
        self.assertIsNone(data["display_app_fps"])
        self.assertEqual(data["display_app_fps_status"], "unavailable")
        self.assertEqual(
            data["display_semantics"], "physical-output-refresh-not-app-fps"
        )
        self.assertEqual(
            data["display_physical_mode_semantics"],
            "logical-size-times-scale-rounded",
        )

    def test_missing_screen_is_partial_and_never_fabricated(self):
        data = telemetry.parse_kwin_support_information(
            KWIN_FIXTURE.replace("Name: DSI-2", "Name: HDMI-A-1")
        )
        self.assertEqual(data["display_status"], "partial")
        self.assertIsNone(data["display_dsi2_refresh_hz"])
        self.assertIsNone(data["display_dsi2_logical_width"])
        self.assertIsNone(data["display_app_fps"])

    def test_busctl_json_contract(self):
        reply = json.dumps({"type": "s", "data": [KWIN_FIXTURE]})
        self.assertEqual(telemetry.decode_busctl_reply(reply), KWIN_FIXTURE)
        with self.assertRaises(ValueError):
            telemetry.decode_busctl_reply(json.dumps({"type": "u", "data": [1]}))

    def test_bus_failure_returns_null_display_without_raising(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="no bus")
        with mock.patch.object(telemetry.subprocess, "run", return_value=failed):
            data = telemetry.query_kwin_display()
        self.assertEqual(data["display_status"], "unavailable")
        self.assertTrue(all(data[field] is None for field in telemetry.DISPLAY_FIELDS))
        self.assertIsNone(data["display_app_fps"])


class AsyncBoundaryTests(unittest.TestCase):
    def test_slow_kwin_query_does_not_block_snapshot_reader(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_query():
            entered.set()
            release.wait(2)
            data = telemetry.parse_kwin_support_information(KWIN_FIXTURE)
            data["display_sample_unix_ms"] = int(time.time() * 1000)
            return data

        sampler = telemetry.KWinDisplaySampler(interval=60, query=slow_query)
        sampler.start()
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        self.assertEqual(sampler.snapshot()["display_status"], "unavailable")
        self.assertLess(time.monotonic() - started, 0.05)
        release.set()
        self.assertTrue(sampler.wait_for_update(1))
        self.assertEqual(sampler.snapshot()["display_status"], "ok")
        sampler.close()


class GpuSamplerProcessDiscoveryTests(unittest.TestCase):
    class FakeObserver:
        def __init__(self):
            self.ready = False
            self.discovery_calls: list[list[Path]] = []
            self.fdinfo = Path("/proc/4242/fdinfo/9")

        @staticmethod
        def thermal_sources():
            return {}

        @staticmethod
        def process_directories():
            return {"4242": Path("/proc/4242")}

        def discover_drm_fdinfo(self, processes):
            self.discovery_calls.append(list(processes))
            return [self.fdinfo] if self.ready else []

    def test_known_process_is_retried_when_it_opens_drm_late(self):
        observer = self.FakeObserver()
        sampler = telemetry.GpuSampler(
            observer,
            process_rescan_interval=10.0,
            full_rescan_interval=300.0,
            late_client_retry_window=180.0,
        )

        sampler._refresh_paths(0.0)
        self.assertEqual(sampler.known_processes, {"4242"})
        self.assertEqual(sampler.fdinfo_paths, [])
        self.assertEqual(len(observer.discovery_calls), 1)

        observer.ready = True
        sampler._refresh_paths(9.9)
        self.assertEqual(len(observer.discovery_calls), 1)
        sampler._refresh_paths(10.0)

        self.assertEqual(len(observer.discovery_calls), 2)
        self.assertEqual(sampler.fdinfo_paths, [observer.fdinfo])

    def test_retry_window_expires_before_the_full_rescan(self):
        observer = self.FakeObserver()
        sampler = telemetry.GpuSampler(observer, late_client_retry_window=180.0)
        sampler._refresh_paths(0.0)
        observer.discovery_calls.clear()

        sampler._refresh_paths(181.0)

        self.assertEqual(observer.discovery_calls, [])
        self.assertEqual(sampler.retry_processes_until, {})


class PanelContractTests(unittest.TestCase):
    def _cache(self, display_sample_unix_ms: int) -> dict[str, object]:
        data: dict[str, object] = {
            "schema": 1,
            "source": "msm-drm-fdinfo",
            "semantics": "observable-drm-client-load",
            "sample_unix_ms": int(time.time() * 1000),
            "gpu_percent": 12.5,
            "gpu_freq_hz": 680000000,
            "gpu_temp_c": 42.0,
            "gpu_clients": 2,
        }
        display = telemetry.parse_kwin_support_information(KWIN_FIXTURE)
        display["display_sample_unix_ms"] = display_sample_unix_ms
        data.update(display)
        return data

    def test_panelctl_flattens_cache_and_fails_stale_values_closed(self):
        with tempfile.TemporaryDirectory(prefix="pocketds-display-panel-") as name:
            temp = Path(name)
            helper = temp / "pocketds-panelctl"
            subprocess.run(
                [
                    "c++", "-std=c++17", "-O0",
                    str(ROOT / "components/control-panel/pocketds-panelctl.cpp"),
                    "-o", str(helper),
                ],
                check=True,
                timeout=30,
            )
            cache = temp / "pocketds-gpu-status.json"
            cache.write_text(
                json.dumps(self._cache(int(time.time() * 1000))), encoding="utf-8"
            )
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = str(temp)
            result = subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            )
            data = json.loads(result.stdout)
            self.assertEqual(data["display_status"], "ok")
            self.assertEqual(data["display_dsi1_refresh_hz"], 165)
            self.assertEqual(data["display_dsi2_refresh_hz"], 59.999)
            self.assertEqual(data["display_dsi1_physical_width_px"], 1920)
            self.assertEqual(data["display_dsi2_physical_width_px"], 1024)
            self.assertEqual(data["display_session"], "Wayland")
            self.assertEqual(data["display_backend"], "DRM")
            self.assertEqual(data["display_compositor"], "OpenGL")
            self.assertEqual(data["display_renderer"], "FD740")
            self.assertIsNone(data["display_app_fps"])
            self.assertEqual(data["display_app_fps_status"], "unavailable")
            self.assertEqual(data["display_app_fps_source"], "mangohud-retroarch")

            cache.write_text(
                json.dumps(self._cache(int(time.time() * 1000) - 130000)),
                encoding="utf-8",
            )
            stale = json.loads(
                subprocess.run(
                    [str(helper), "status"], env=env, capture_output=True, text=True,
                    check=True, timeout=10,
                ).stdout
            )
            self.assertEqual(stale["display_status"], "stale")
            self.assertIsNone(stale["display_dsi1_refresh_hz"])
            self.assertIsNone(stale["display_dsi2_refresh_hz"])
            self.assertIsNone(stale["display_app_fps"])

    def test_panelctl_prefers_gamescope_presented_fps_without_app_matching(self):
        with tempfile.TemporaryDirectory(prefix="pocketds-gamescope-fps-") as name:
            temp = Path(name)
            helper = temp / "pocketds-panelctl"
            subprocess.run(
                ["c++", "-std=c++17", "-O0",
                 str(ROOT / "components/control-panel/pocketds-panelctl.cpp"),
                 "-o", str(helper)],
                check=True, timeout=30,
            )
            presented = temp / "pocketds-gamescope-fps"
            presented.write_text("59.7\n", encoding="utf-8")
            presented.chmod(0o600)
            moonlight = (
                temp / "app/com.moonlight_stream.Moonlight/pocketds-rendered-fps"
            )
            moonlight.parent.mkdir(parents=True)
            moonlight.write_text("90.0\n", encoding="utf-8")
            moonlight.chmod(0o600)
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = str(temp)

            data = json.loads(subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout)
            self.assertEqual(data["display_app_fps_status"], "ok")
            self.assertEqual(data["display_app_fps"], 59.7)
            self.assertEqual(data["display_app_fps_source"], "gamescope-presented")

            old = time.time() - 10
            os.utime(presented, (old, old))
            fallback = json.loads(subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout)
            self.assertEqual(fallback["display_app_fps_status"], "ok")
            self.assertEqual(fallback["display_app_fps"], 90)
            self.assertEqual(
                fallback["display_app_fps_source"], "moonlight-rendered"
            )

            moonlight.unlink()
            very_old = time.time() - 3600
            os.utime(presented, (very_old, very_old))
            stale = json.loads(subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout)
            self.assertEqual(stale["display_app_fps_status"], "stale")
            self.assertIsNone(stale["display_app_fps"])
            self.assertEqual(stale["display_app_fps_age_ms"], 600000)
            self.assertEqual(
                stale["display_app_fps_source"], "gamescope-presented"
            )

    def test_panelctl_reads_and_sets_persistent_game_fps_limit(self):
        with tempfile.TemporaryDirectory(prefix="pocketds-game-limit-") as name:
            temp = Path(name)
            helper = temp / "pocketds-panelctl"
            subprocess.run(
                ["c++", "-std=c++17", "-O0",
                 str(ROOT / "components/control-panel/pocketds-panelctl.cpp"),
                 "-o", str(helper)],
                check=True, timeout=30,
            )
            home = temp / "home"
            runtime = temp / "run"
            config_home = temp / "config"
            limiter = home / ".local/bin/pocketds-game-limit"
            limiter.parent.mkdir(parents=True)
            limiter.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$1\" >\"$LIMIT_LOG\"\n",
                encoding="utf-8",
            )
            limiter.chmod(0o755)
            runtime.mkdir()
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_RUNTIME_DIR": str(runtime),
                "LIMIT_LOG": str(temp / "limit.log"),
            })

            default = json.loads(subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout)
            self.assertEqual(default["game_fps_limit"], 0)
            self.assertEqual(default["game_fps_limit_status"], "default")
            self.assertFalse(default["game_fps_runtime_active"])

            config = config_home / "pocketds-linux-kit/game-fps-limit"
            config.parent.mkdir(parents=True)
            config.write_text("30\n", encoding="utf-8")
            config.chmod(0o600)
            configured = json.loads(subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout)
            self.assertEqual(configured["game_fps_limit"], 30)
            self.assertEqual(configured["game_fps_limit_status"], "ok")

            subprocess.run(
                [str(helper), "game-limit", "40"], env=env,
                check=True, timeout=10,
            )
            self.assertEqual((temp / "limit.log").read_text(), "40\n")

            config.chmod(0o644)
            invalid = json.loads(subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout)
            self.assertEqual(invalid["game_fps_limit"], 0)
            self.assertEqual(invalid["game_fps_limit_status"], "invalid")

    def test_panelctl_reads_recent_managed_game_fps_and_rejects_stale_data(self):
        with tempfile.TemporaryDirectory(prefix="pocketds-display-fps-") as name:
            temp = Path(name)
            helper = temp / "pocketds-panelctl"
            subprocess.run(
                ["c++", "-std=c++17", "-O0",
                 str(ROOT / "components/control-panel/pocketds-panelctl.cpp"),
                 "-o", str(helper)],
                check=True, timeout=30,
            )
            session = temp / "pocketds-top-fps.fixture"
            session.mkdir(mode=0o700)
            log = session / "retroarch_2026-08-30_00-00-00.csv"
            log.write_text(
                "fps,frametime\n59,16.9\n61,16.4\n320,3.1\n58,17.2\n60,16.7\n",
                encoding="utf-8",
            )
            (temp / "pocketds-top-fps.active").write_text(
                str(session) + "\n", encoding="utf-8"
            )
            (temp / "pocketds-top-fps.active").chmod(0o600)
            moonlight = (
                temp / "app/com.moonlight_stream.Moonlight/pocketds-rendered-fps"
            )
            moonlight.parent.mkdir(parents=True)
            moonlight.write_text("not-a-number\n", encoding="utf-8")
            moonlight.chmod(0o600)
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = str(temp)
            data = json.loads(subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout)
            self.assertEqual(data["display_app_fps_status"], "ok")
            self.assertEqual(data["display_app_fps"], 60)
            self.assertLessEqual(data["display_app_fps_age_ms"], 2000)
            self.assertEqual(data["display_app_fps_source"], "mangohud-retroarch")

            old = time.time() - 10
            os.utime(log, (old, old))
            stale = json.loads(subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout)
            self.assertEqual(stale["display_app_fps_status"], "stale")
            self.assertIsNone(stale["display_app_fps"])
            self.assertEqual(stale["display_app_fps_source"], "mangohud-retroarch")

    def test_panelctl_reads_recent_private_steam_mangohud_fps(self):
        with tempfile.TemporaryDirectory(prefix="pocketds-steam-fps-") as name:
            temp = Path(name)
            helper = temp / "pocketds-panelctl"
            subprocess.run(
                ["c++", "-std=c++17", "-O0",
                 str(ROOT / "components/control-panel/pocketds-panelctl.cpp"),
                 "-o", str(helper)],
                check=True, timeout=30,
            )
            shared = temp / "shared"
            shared.mkdir(mode=0o700)
            session = shared / "session.fixture"
            session.mkdir(mode=0o700)
            log = session / "VampireSurvivors_2026-09-01_12-00-00.csv"
            log.write_text(
                "fps,frametime\n57,17.5\n60,16.7\n61,16.4\n59,16.9\n60,16.7\n",
                encoding="utf-8",
            )
            active = temp / "pocketds-steam-fps.active"
            active.write_text(str(session) + "\n", encoding="utf-8")
            active.chmod(0o600)
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = str(temp)
            env["POCKETDS_STEAM_FPS_ROOT"] = str(shared)

            data = json.loads(subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout)
            self.assertEqual(data["display_app_fps_status"], "ok")
            self.assertEqual(data["display_app_fps"], 60)
            self.assertEqual(data["display_app_fps_source"], "mangohud-steam")
            self.assertLessEqual(data["display_app_fps_age_ms"], 2000)

            wrong = session / "not-a-log.txt"
            wrong.write_text("999,1\n", encoding="utf-8")
            old = time.time() - 10
            os.utime(log, (old, old))
            stale = json.loads(subprocess.run(
                [str(helper), "status"], env=env, capture_output=True, text=True,
                check=True, timeout=10,
            ).stdout)
            self.assertEqual(stale["display_app_fps_status"], "stale")
            self.assertIsNone(stale["display_app_fps"])
            self.assertEqual(stale["display_app_fps_source"], "mangohud-steam")

    def test_panelctl_prefers_strict_recent_moonlight_rendered_fps(self):
        with tempfile.TemporaryDirectory(prefix="pocketds-moonlight-fps-") as name:
            temp = Path(name)
            helper = temp / "pocketds-panelctl"
            subprocess.run(
                ["c++", "-std=c++17", "-O0",
                 str(ROOT / "components/control-panel/pocketds-panelctl.cpp"),
                 "-o", str(helper)],
                check=True, timeout=30,
            )
            session = temp / "pocketds-top-fps.fixture"
            session.mkdir(mode=0o700)
            log = session / "retroarch_2026-08-30_00-00-00.csv"
            log.write_text(
                "fps,frametime\n30,33.3\n30,33.3\n30,33.3\n", encoding="utf-8"
            )
            active = temp / "pocketds-top-fps.active"
            active.write_text(str(session) + "\n", encoding="utf-8")
            active.chmod(0o600)
            moonlight = (
                temp / "app/com.moonlight_stream.Moonlight/pocketds-rendered-fps"
            )
            moonlight.parent.mkdir(parents=True)
            moonlight.write_text("59.8\r\n", encoding="utf-8")
            moonlight.chmod(0o600)
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = str(temp)

            def status() -> dict[str, object]:
                return json.loads(subprocess.run(
                    [str(helper), "status"], env=env, capture_output=True, text=True,
                    check=True, timeout=10,
                ).stdout)

            data = status()
            self.assertEqual(data["display_app_fps_status"], "ok")
            self.assertEqual(data["display_app_fps"], 59.8)
            self.assertEqual(data["display_app_fps_source"], "moonlight-rendered")
            self.assertLessEqual(data["display_app_fps_age_ms"], 2000)

            active.unlink()
            for invalid in (
                "", " 60\n", "+60\n", "60.\n", "60 fps\n", "60\n61\n",
                "nan\n", "inf\n", "-1\n", "1000.1\n",
            ):
                with self.subTest(invalid=repr(invalid)):
                    moonlight.write_text(invalid, encoding="utf-8")
                    moonlight.chmod(0o600)
                    rejected = status()
                    self.assertEqual(rejected["display_app_fps_status"], "invalid")
                    self.assertIsNone(rejected["display_app_fps"])
                    self.assertEqual(
                        rejected["display_app_fps_source"], "moonlight-rendered"
                    )

            moonlight.write_text("60\n", encoding="utf-8")
            moonlight.chmod(0o644)
            exposed = status()
            self.assertEqual(exposed["display_app_fps_status"], "invalid")
            self.assertIsNone(exposed["display_app_fps"])
            self.assertEqual(exposed["display_app_fps_source"], "moonlight-rendered")

            moonlight.unlink()
            os.mkfifo(moonlight, mode=0o600)
            started = time.monotonic()
            special = status()
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(special["display_app_fps_status"], "invalid")
            self.assertIsNone(special["display_app_fps"])
            moonlight.unlink()
            moonlight.write_text("60\n", encoding="utf-8")
            moonlight.chmod(0o600)
            old = time.time() - 10
            os.utime(moonlight, (old, old))
            stale = status()
            self.assertEqual(stale["display_app_fps_status"], "stale")
            self.assertIsNone(stale["display_app_fps"])
            self.assertEqual(stale["display_app_fps_source"], "moonlight-rendered")
            self.assertGreaterEqual(stale["display_app_fps_age_ms"], 9000)

    def test_qml_and_default_install_make_fps_semantics_explicit(self):
        qml = (ROOT / "components/control-panel/plasmoid/contents/ui/main.qml").read_text(
            encoding="utf-8"
        )
        self.assertIn("displayFpsText", qml)
        self.assertIn("当前游戏 · 实时", qml)
        self.assertIn("物理刷新率不冒充游戏 FPS", qml)
        installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        self.assertIn("components/telemetry/pocketds-gpu-telemetry.py", installer)
        self.assertIn("pocketds-gpu-telemetry.service", installer)
        self.assertIn("systemctl --user restart pocketds-gpu-telemetry.service", installer)
        telemetry_installer = (ROOT / "scripts/install-telemetry.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("components/control-panel/plasmoid/metadata.json", telemetry_installer)
        self.assertIn("pocketds-gpu-telemetry.service", telemetry_installer)
        self.assertIn(
            "systemctl --user restart pocketds-gpu-telemetry.service",
            telemetry_installer,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
