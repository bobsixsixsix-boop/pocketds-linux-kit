#!/usr/bin/env python3
"""Run one telemetry sample and validate its public cache contract."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
DAEMON = ROOT / "components" / "telemetry" / "pocketds-gpu-telemetry.py"

with tempfile.TemporaryDirectory(prefix="pocketds-gpu-telemetry-") as temp:
    output = Path(temp) / "status.json"
    result = subprocess.run(
        [str(DAEMON), "--once", "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert data["source"] == "msm-drm-fdinfo"
    assert data["semantics"] == "observable-drm-client-load"
    assert isinstance(data["sample_unix_ms"], int)
    assert data["gpu_percent"] is None or 0 <= data["gpu_percent"] <= 100
    assert data["gpu_freq_hz"] is None or data["gpu_freq_hz"] >= 0
    assert data["gpu_temp_c"] is None or data["gpu_temp_c"] >= 0
    assert isinstance(data["gpu_clients"], int) and data["gpu_clients"] >= 0
    assert data["display_status"] == "unavailable"
    assert data["display_source"] == "kwin-supportInformation"
    assert data["display_semantics"] == "physical-output-refresh-not-app-fps"
    assert data["display_physical_mode_semantics"] == "logical-size-times-scale-rounded"
    assert data["display_sample_unix_ms"] is None
    assert data["display_app_fps"] is None
    assert data["display_app_fps_status"] == "unavailable"
    for field in (
        "display_session", "display_backend", "display_compositor",
        "display_renderer", "display_dsi1_physical_width_px",
        "display_dsi2_physical_width_px", "display_dsi1_refresh_hz",
        "display_dsi2_refresh_hz",
    ):
        assert data[field] is None
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

print("  [OK] GPU telemetry atomic cache schema and permissions")
