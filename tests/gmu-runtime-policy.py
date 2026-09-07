#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "components/system/pocketds-gmu-runtime"
UNIT = ROOT / "components/system/pocketds-gmu-runtime.service"
INSTALLER = ROOT / "scripts/install.sh"


helper_text = HELPER.read_text(encoding="utf-8")
unit_text = UNIT.read_text(encoding="utf-8")
installer_text = INSTALLER.read_text(encoding="utf-8")

assert "/sys/devices/platform/soc@0/3d6a000.gmu/power" in helper_text
for forbidden in ("/sys/class/devfreq", "min_freq", "max_freq", "governor"):
    assert forbidden not in helper_text

assert "Before=display-manager.service" in unit_text
assert "ExecStart=/usr/local/libexec/pocketds-gmu-runtime on" in unit_text
assert "ExecStop=" not in unit_text
assert "WantedBy=graphical.target" in unit_text

assert "components/system/pocketds-gmu-runtime" in installer_text
assert "/usr/local/libexec/pocketds-gmu-runtime" in installer_text
assert "components/system/pocketds-gmu-runtime.service" in installer_text
assert "/etc/systemd/system/pocketds-gmu-runtime.service" in installer_text
assert "systemctl enable --now pocketds-gmu-runtime.service" in installer_text

with tempfile.TemporaryDirectory() as temp_dir:
    power_dir = Path(temp_dir) / "power"
    power_dir.mkdir()
    control = power_dir / "control"
    runtime_status = power_dir / "runtime_status"
    control.write_text("auto\n", encoding="utf-8")
    runtime_status.write_text("suspended\n", encoding="utf-8")

    result = subprocess.run(
        [str(HELPER), "--test-root", str(power_dir), "on"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert control.read_text(encoding="utf-8") == "on\n"
    assert runtime_status.read_text(encoding="utf-8") == "suspended\n"
    assert "control=on" in result.stdout

    subprocess.run(
        [str(HELPER), "--test-root", str(power_dir), "auto"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert control.read_text(encoding="utf-8") == "auto\n"

    before = control.read_bytes()
    invalid = subprocess.run(
        [str(HELPER), "--test-root", str(power_dir), "turbo"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 2
    assert control.read_bytes() == before

print("gmu runtime policy: PASS")
