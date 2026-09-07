#!/usr/bin/env python3
"""Validate battery units, missing fields and impossible-value rejection."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "components" / "control-panel" / "pocketds-panelctl.cpp"
assert "volume_cache_max_age_ms = 750" in SOURCE.read_text(encoding="utf-8")


def write(path: Path, value: object) -> None:
    path.write_text(f"{value}\n", encoding="utf-8")


def command_env(supply_root: Path, runtime: Path, tools: Path, counter: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["POCKETDS_POWER_SUPPLY_ROOT"] = str(supply_root)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env["PATH"] = str(tools) + os.pathsep + env.get("PATH", "")
    env["TEST_WPCTL_COUNT"] = str(counter)
    return env


def status(
    binary: Path, supply_root: Path, runtime: Path, tools: Path, counter: Path
) -> dict[str, object]:
    env = command_env(supply_root, runtime, tools, counter)
    result = subprocess.run(
        [str(binary), "status"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


with tempfile.TemporaryDirectory(prefix="pocketds-panel-battery-") as temp:
    root = Path(temp)
    binary = root / "pocketds-panelctl"
    subprocess.run(
        ["c++", "-std=c++17", "-O2", str(SOURCE), "-o", str(binary)],
        check=True,
        timeout=30,
    )
    supplies = root / "power_supply"
    runtime = root / "run"
    tools = root / "bin"
    counter = root / "wpctl-count"
    battery = supplies / "battery"
    usb = supplies / "usb"
    battery.mkdir(parents=True)
    usb.mkdir()
    runtime.mkdir()
    tools.mkdir()
    wpctl = tools / "wpctl"
    wpctl.write_text(
        """#!/usr/bin/env python3
import os
from pathlib import Path
import sys
if sys.argv[1] == "get-volume":
    counter = Path(os.environ["TEST_WPCTL_COUNT"])
    value = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(value))
    print("Volume: 0.42")
    raise SystemExit(0)
if sys.argv[1] in ("set-volume", "set-mute"):
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    wpctl.chmod(0o755)

    for name, value in {
        "type": "Battery",
        "present": 1,
        "status": "Discharging",
        "capacity": 42,
        "health": "Good",
        "voltage_now": 4_000_000,
        "current_now": -1_500_000,
        "power_now": 52_900_000,
        "temp": 315,
        "cycle_count": 12,
    }.items():
        write(battery / name, value)
    write(usb / "type", "USB")
    write(usb / "online", 0)

    data = status(binary, supplies, runtime, tools, counter)
    assert data["battery_percent"] == 42
    assert data["battery_state"] == "discharging"
    assert data["battery_external_power"] is False
    assert data["battery_temp_c"] == 31.5
    assert data["battery_voltage_v"] == 4
    assert data["battery_current_a"] == -1.5
    assert data["battery_power_w"] == 6
    assert data["battery_power_source"] == "voltage-current"
    assert data["system_power_w"] == 6
    assert data["system_power_source"] == "battery-discharge"
    assert data["battery_health"] == "Good"
    assert data["battery_cycles"] == 12
    assert data["battery_time_to_empty_s"] is None
    assert data["battery_time_to_full_s"] is None
    assert data["volume"] == 42
    assert int(counter.read_text()) == 1
    status(binary, supplies, runtime, tools, counter)
    assert int(counter.read_text()) == 1

    volume = subprocess.run(
        [str(binary), "volume", "55"],
        env=command_env(supplies, runtime, tools, counter),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert volume.returncode == 0, volume.stderr
    status(binary, supplies, runtime, tools, counter)
    assert int(counter.read_text()) == 2

    recovery_result = runtime / "pocketds-plasma-recovery-result.json"
    request_id = "0123456789abcdef0123456789abcdef"
    recovery_result.write_text(
        json.dumps(
            {
                "worker_started_unix_ms": int(time.time() * 1000),
                "ok": True,
                "request_id": request_id,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    recovery_result.chmod(0o600)
    recovered = status(binary, supplies, runtime, tools, counter)
    assert recovered["recovery_status"] == "ok"
    assert recovered["recovery_request_id"] == request_id
    assert recovered["recovery_error"] is None
    assert recovered["recovery_age_ms"] is not None

    recovery_request = runtime / "pocketds-plasma-recovery.request"
    recovery_request.write_text("stale fixture\n", encoding="utf-8")
    recovery_request.chmod(0o600)
    old = time.time() - 100
    os.utime(recovery_request, (old, old))
    blocked = status(binary, supplies, runtime, tools, counter)
    assert blocked["recovery_request_status"] == "stale"
    assert blocked["recovery_request_age_ms"] >= 90_000

    write(usb / "voltage_now", 4_804_000)
    write(usb / "current_now", 1_056_000)
    write(usb / "power_now", 99_000_000)
    write(usb / "online", 1)
    unrecognized_usb = supplies / "unrecognized-usb"
    unrecognized_usb.mkdir()
    write(unrecognized_usb / "type", "USB_VENDOR")
    write(unrecognized_usb / "online", 1)
    write(unrecognized_usb / "voltage_now", 5_000_000)
    write(unrecognized_usb / "current_now", 3_000_000)
    external = status(binary, supplies, runtime, tools, counter)
    assert external["battery_external_power"] is True
    assert external["system_power_w"] == 5.07
    assert external["system_power_source"] == "usb-input"

    write(battery / "current_now", "invalid")
    invalid = status(binary, supplies, runtime, tools, counter)
    assert invalid["battery_current_a"] is None
    assert invalid["battery_power_w"] is None
    assert invalid["battery_power_source"] == "unavailable"
    assert invalid["system_power_w"] == 5.07
    assert invalid["system_power_source"] == "usb-input"

    write(usb / "current_now", "invalid")
    unavailable = status(binary, supplies, runtime, tools, counter)
    assert unavailable["system_power_w"] is None
    assert unavailable["system_power_source"] == "unavailable"

    write(usb / "current_now", -(2**63))
    extreme = status(binary, supplies, runtime, tools, counter)
    assert extreme["system_power_w"] is None
    assert extreme["system_power_source"] == "unavailable"

    write(usb / "current_now", 1_056_000)
    shutil.rmtree(battery)
    missing = status(binary, supplies, runtime, tools, counter)
    assert missing["battery_state"] == "unavailable"
    assert missing["battery_percent"] is None
    assert missing["battery_power_w"] is None
    assert missing["system_power_w"] == 5.07
    assert missing["system_power_source"] == "usb-input"

    write(usb / "online", 0)
    no_source = status(binary, supplies, runtime, tools, counter)
    assert no_source["system_power_w"] is None
    assert no_source["system_power_source"] == "unavailable"

print("  [OK] panel battery/system-power/recovery fixtures plus volume cache and invalidation")
