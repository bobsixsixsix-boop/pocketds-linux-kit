#!/usr/bin/env python3
"""Validate the current source's read-only status schema before deployment.

POCKETDS_PANELCTL may explicitly select an installed or fixture helper for a
separate deployment check; the default must not depend on the installed version.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"  [FAIL] {message}", file=sys.stderr)
    raise SystemExit(1)


def read_status() -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="pds-panel-schema-") as temporary:
        configured = os.environ.get("POCKETDS_PANELCTL")
        if configured:
            helper = Path(configured)
            if not helper.is_file():
                fail(f"explicit panel helper does not exist: {helper}")
        else:
            helper = Path(temporary) / "panelctl"
            command = ["c++", "-std=c++17", "-O1", "-Wall", "-Wextra"]
            if sys.platform == "darwin":
                command.append("-Dst_mtim=st_mtimespec")
            subprocess.run(
                command + [str(ROOT / "components/control-panel/pocketds-panelctl.cpp"),
                           "-o", str(helper)],
                check=True, timeout=45,
            )
        return subprocess.run(
            [str(helper), "status"], text=True, capture_output=True, timeout=10, check=False
        )


result = read_status()
if result.returncode != 0:
    fail(f"helper exited {result.returncode}: {result.stderr.strip()}")

try:
    data = json.loads(result.stdout)
except json.JSONDecodeError as exc:
    fail(f"invalid JSON: {exc}")

required_types = {
    "cpu_total": int,
    "cpu_idle": int,
    "cpu_ghz": (int, float),
    "cpu_cores": int,
    "rx_bytes": int,
    "tx_bytes": int,
    "fan_profile": str,
    "fan_status": str,
    "top_brightness": int,
    "bottom_brightness": int,
    "brightness_write_status": str,
    "volume": int,
    "muted": bool,
    "power_profile": str,
    "lid_auto_poweroff_minutes": int,
    "lid_auto_poweroff_status": str,
    "game_fps_limit": int,
    "game_fps_limit_status": str,
    "game_fps_runtime_active": bool,
    "input_mode": str,
    "gpu_status": str,
    "gpu_source": str,
    "display_status": str,
    "display_source": str,
    "display_semantics": str,
    "display_physical_mode_semantics": str,
    "display_app_fps_status": str,
    "display_app_fps_source": str,
    "battery_state": str,
    "battery_power_source": str,
    "system_power_source": str,
    "recovery_status": str,
    "recovery_request_status": str,
}

for key, expected in required_types.items():
    if key not in data:
        fail(f"missing key: {key}")
    if not isinstance(data[key], expected):
        fail(f"{key} has type {type(data[key]).__name__}, expected {expected}")

for key in ("top_brightness", "bottom_brightness"):
    if not 0 <= data[key] <= 100:
        fail(f"{key} out of range: {data[key]}")
if not 0 <= data["volume"] <= 150:
    fail(f"volume out of range: {data['volume']}")
if not 1 <= data["cpu_cores"] <= 512:
    fail(f"cpu_cores out of range: {data['cpu_cores']}")
if not 0 <= data["cpu_idle"] <= data["cpu_total"]:
    fail("cpu_idle must be between zero and cpu_total")
if data.get("quota") is not None and not isinstance(data["quota"], dict):
    fail("quota must be null or an object")

nullable_numbers = (
    "temp_c",
    "fan_percent",
    "fan_sample_age_ms",
    "gpu_percent",
    "gpu_freq_hz",
    "gpu_temp_c",
    "gpu_clients",
    "gpu_sample_age_ms",
    "display_sample_age_ms",
    "display_app_fps",
    "display_app_fps_age_ms",
    "display_dsi1_physical_width_px",
    "display_dsi1_physical_height_px",
    "display_dsi1_physical_width_mm",
    "display_dsi1_physical_height_mm",
    "display_dsi1_logical_x",
    "display_dsi1_logical_y",
    "display_dsi1_logical_width",
    "display_dsi1_logical_height",
    "display_dsi1_scale",
    "display_dsi1_refresh_hz",
    "display_dsi2_physical_width_px",
    "display_dsi2_physical_height_px",
    "display_dsi2_physical_width_mm",
    "display_dsi2_physical_height_mm",
    "display_dsi2_logical_x",
    "display_dsi2_logical_y",
    "display_dsi2_logical_width",
    "display_dsi2_logical_height",
    "display_dsi2_scale",
    "display_dsi2_refresh_hz",
    "battery_percent",
    "battery_temp_c",
    "battery_voltage_v",
    "battery_current_a",
    "battery_power_w",
    "battery_cycles",
    "battery_time_to_empty_s",
    "battery_time_to_full_s",
    "system_power_w",
    "recovery_age_ms",
    "recovery_request_age_ms",
)
for key in nullable_numbers:
    if key not in data:
        fail(f"missing key: {key}")
    if data[key] is not None and not isinstance(data[key], (int, float)):
        fail(f"{key} must be null or numeric")
if data["fan_status"] not in ("ok", "stale", "unavailable", "invalid"):
    fail(f"unknown fan_status: {data['fan_status']}")
if data["fan_status"] == "ok":
    if (type(data["fan_percent"]) is not int or not 0 <= data["fan_percent"] <= 100
        or type(data["temp_c"]) is not int or not -50 <= data["temp_c"] <= 150
        or type(data["fan_sample_age_ms"]) is not int or not 0 <= data["fan_sample_age_ms"] <= 7000
        or data["fan_profile"] not in ("quiet", "moderate", "aggressive", "custom", "auto", "off")):
        fail("fresh fan fields are invalid")
elif data["fan_percent"] is not None or data["temp_c"] is not None or data["fan_profile"] != "unavailable":
    fail("unavailable fan telemetry must not retain temperature, percent or profile")
if data["gpu_status"] not in ("ok", "stale", "unavailable"):
    fail(f"unknown gpu_status: {data['gpu_status']}")
if data["display_status"] not in ("ok", "partial", "stale", "unavailable"):
    fail(f"unknown display_status: {data['display_status']}")
if data["display_source"] != "kwin-supportInformation":
    fail(f"unknown display source: {data['display_source']}")
if data["display_semantics"] != "physical-output-refresh-not-app-fps":
    fail(f"unknown display semantics: {data['display_semantics']}")
if data["display_physical_mode_semantics"] != "logical-size-times-scale-rounded":
    fail(f"unknown physical mode semantics: {data['display_physical_mode_semantics']}")
if data["display_app_fps_source"] not in (
    "gamescope-presented", "moonlight-rendered",
    "mangohud-retroarch", "mangohud-steam"
):
    fail(f"unknown application FPS source: {data['display_app_fps_source']}")
if data["display_app_fps_status"] not in (
    "ok", "starting", "stale", "unavailable", "invalid"
):
    fail(f"unknown application FPS status: {data['display_app_fps_status']}")
if (data["display_app_fps_status"] == "ok") != (data["display_app_fps"] is not None):
    fail("application FPS value and status must fail closed together")
if data["display_app_fps"] is not None and not 0 <= data["display_app_fps"] <= 1000:
    fail(f"application FPS out of range: {data['display_app_fps']}")
if data["power_profile"] not in (
    "powersave", "balanced", "performance", "unknown"
):
    fail(f"unknown power_profile: {data['power_profile']}")
if data["brightness_write_status"] not in ("ok", "unavailable", "invalid"):
    fail(f"unknown brightness_write_status: {data['brightness_write_status']}")
if data["lid_auto_poweroff_minutes"] not in (0, 60, 120, 240, 480):
    fail(f"unknown lid auto poweroff timeout: {data['lid_auto_poweroff_minutes']}")
if data["lid_auto_poweroff_status"] not in (
    "default", "ok", "invalid", "unavailable"
):
    fail(f"unknown lid auto poweroff status: {data['lid_auto_poweroff_status']}")
if (
    data["lid_auto_poweroff_status"] != "ok"
    and data["lid_auto_poweroff_minutes"] != 0
):
    fail("non-ok lid auto poweroff status must fail closed to zero")
if data["game_fps_limit"] not in (0, 24, 30, 40, 60, 120):
    fail(f"unknown game FPS limit: {data['game_fps_limit']}")
if data["game_fps_limit_status"] not in (
    "default", "ok", "invalid", "unavailable"
):
    fail(f"unknown game FPS limit status: {data['game_fps_limit_status']}")
if data["game_fps_limit_status"] != "ok" and data["game_fps_limit"] != 0:
    fail("non-ok game FPS limit status must fail closed to zero")
if data["input_mode"] not in ("joymouse", "gamepad", "other", "unavailable"):
    fail(f"unknown input_mode: {data['input_mode']}")
if data["gpu_percent"] is not None and not 0 <= data["gpu_percent"] <= 100:
    fail(f"gpu_percent out of range: {data['gpu_percent']}")
if data["gpu_status"] != "ok":
    for key in ("gpu_percent", "gpu_freq_hz", "gpu_temp_c", "gpu_clients"):
        if data[key] is not None:
            fail(f"{key} must be null while GPU status is {data['gpu_status']}")
for key in ("display_session", "display_backend", "display_compositor", "display_renderer"):
    if key not in data:
        fail(f"missing key: {key}")
    if data[key] is not None and not isinstance(data[key], str):
        fail(f"{key} must be null or a string")
for key in ("display_dsi1_enabled", "display_dsi2_enabled"):
    if key not in data:
        fail(f"missing key: {key}")
    if data[key] is not None and not isinstance(data[key], bool):
        fail(f"{key} must be null or boolean")
for key in ("display_dsi1_refresh_hz", "display_dsi2_refresh_hz"):
    if data[key] is not None and not 1 <= data[key] <= 1000:
        fail(f"{key} out of range: {data[key]}")
if data["display_status"] in ("stale", "unavailable"):
    for key in (
        "display_session", "display_backend", "display_compositor", "display_renderer",
        "display_dsi1_refresh_hz", "display_dsi2_refresh_hz",
    ):
        if data[key] is not None:
            fail(f"{key} must be null while display status is {data['display_status']}")
if data["battery_state"] not in (
    "charging", "discharging", "full", "not-charging", "unknown", "unavailable"
):
    fail(f"unknown battery_state: {data['battery_state']}")
if data["battery_percent"] is not None and not 0 <= data["battery_percent"] <= 100:
    fail(f"battery_percent out of range: {data['battery_percent']}")
if data["battery_temp_c"] is not None and not -20 <= data["battery_temp_c"] <= 100:
    fail(f"battery_temp_c out of range: {data['battery_temp_c']}")
if data["battery_power_w"] is not None and not 0 <= data["battery_power_w"] <= 200:
    fail(f"battery_power_w out of range: {data['battery_power_w']}")
if data.get("battery_external_power") is not None and not isinstance(
    data["battery_external_power"], bool
):
    fail("battery_external_power must be null or boolean")
if data.get("battery_health") is not None and not isinstance(data["battery_health"], str):
    fail("battery_health must be null or a string")
if data["battery_time_to_empty_s"] is not None or data["battery_time_to_full_s"] is not None:
    fail("battery time estimates must remain null until charge/energy capacity is trustworthy")
if data["system_power_source"] not in (
    "unavailable", "usb-input", "battery-discharge"
):
    fail(f"unknown system_power_source: {data['system_power_source']}")
if data["system_power_w"] is not None and not 0.01 <= data["system_power_w"] <= 200:
    fail(f"system_power_w out of range: {data['system_power_w']}")
if (data["system_power_source"] == "unavailable") != (data["system_power_w"] is None):
    fail("system power source and value must fail closed together")
if data["recovery_status"] not in ("none", "ok", "failed", "stale", "invalid"):
    fail(f"unknown recovery_status: {data['recovery_status']}")
if data["recovery_request_status"] not in ("none", "pending", "stale", "invalid"):
    fail(f"unknown recovery_request_status: {data['recovery_request_status']}")
for key in ("recovery_request_id", "recovery_error"):
    if key not in data:
        fail(f"missing key: {key}")
    if data[key] is not None and not isinstance(data[key], str):
        fail(f"{key} must be null or a string")

print("  [OK] panel status JSON schema and basic ranges")
