#!/usr/bin/env python3
"""Pure fixture tests for the read-only fan acceptance collector."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parent.parent
COLLECTOR = ROOT / "scripts" / "pocketds-fan-acceptance.py"
spec = importlib.util.spec_from_file_location("pocketds_fan_acceptance", COLLECTOR)
assert spec and spec.loader
fan = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fan
spec.loader.exec_module(fan)


def put(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


with tempfile.TemporaryDirectory(prefix="pocketds-fan-acceptance-") as temp:
    root = Path(temp)
    sys_root = root / "sys"
    proc_root = root / "proc"
    state = root / "run/pocketds-fancontrol/state"
    profile = root / "etc/pocketds-fancontrol/profile"
    gpu = root / "run/user/1000/pocketds-gpu-status.json"
    controller = root / "usr/bin/pocketds-fancontrol"

    put(sys_root / "class/hwmon/hwmon40/name", "pwmfan\n")
    put(sys_root / "class/hwmon/hwmon40/pwm1", "51\n")
    put(sys_root / "class/hwmon/hwmon40/fan1_input", "2031\n")
    put(sys_root / "devices/virtual/thermal/thermal_zone0/type", "cpu7-top-thermal\n")
    put(sys_root / "devices/virtual/thermal/thermal_zone0/temp", "44000\n")
    put(sys_root / "devices/virtual/thermal/thermal_zone1/type", "battery\n")
    put(sys_root / "devices/virtual/thermal/thermal_zone1/temp", "31000\n")
    put(sys_root / "class/video4linux/video0/name", "qcom-iris-decoder\n")
    put(sys_root / "class/video4linux/video1/name", "qcom-iris-encoder\n")
    put(
        state,
        "profile=moderate\ntemp_c=42\nhotspot_c=44\n"
        "target_pwm=51\npwm=51\n",
    )
    put(profile, "moderate\n")
    put(proc_root / "stat", "cpu  100 0 50 850 0 0 0 0 0 0\n")
    put(proc_root / "123/comm", "fixture-browser\n")
    (proc_root / "123/fd").mkdir(parents=True)
    (proc_root / "123/fd/7").symlink_to("/dev/video0")
    put(
        gpu,
        json.dumps(
            {
                "schema": 1,
                "source": "msm-drm-fdinfo",
                "semantics": "observable-drm-client-load",
                "sample_unix_ms": round(time.time() * 1000),
                "gpu_percent": 12.5,
                "gpu_freq_hz": 430000000,
                "gpu_temp_c": 43.2,
            }
        ),
    )
    gpu.chmod(0o600)
    put(controller, "fixture audited controller\n")
    fan.AUDITED_CONTROLLER_SHA256 = hashlib.sha256(
        controller.read_bytes()
    ).hexdigest()
    paths = fan.Paths(sys_root, proc_root, state, profile, gpu, controller)

    sample1, counters, errors = fan.collect_sample(paths, None, 6.0)
    assert errors == []
    assert sample1["collector_integrity"] == "PASS"
    assert sample1["profile"] == {
        "configured": "moderate",
        "runtime": "moderate",
        "consistent": True,
    }
    assert sample1["temperature"]["live_hotspot_c"] == 44.0
    assert sample1["temperature"]["live_zone_count"] == 1
    assert sample1["temperature"]["live_scan_age_ms"] == 0
    assert sample1["fan"]["target_pwm"]["value"] == 51
    assert sample1["fan"]["actual_pwm"]["value"] == 51
    assert sample1["fan"]["actual_rpm"] == {
        "available": True,
        "value": 2031,
        "source": "hwmon-fan1_input",
    }
    assert sample1["compute"]["cpu_percent"] is None
    assert sample1["compute"]["gpu"]["gpu_percent"] == 12.5
    assert sample1["video_decode"]["decoder_fd_observed"] is True
    assert sample1["video_decode"]["scan_age_ms"] == 0
    assert sample1["video_decode"]["open_decoder_fds"] == [
        {
            "process_comm": "fixture-browser",
            "node": "/dev/video0",
            "node_name": "qcom-iris-decoder",
        }
    ]

    put(proc_root / "stat", "cpu  130 0 60 910 0 0 0 0 0 0\n")
    sample2, _counters, errors = fan.collect_sample(
        paths,
        counters,
        6.0,
        sample1["video_decode"],
        {
            "live_hotspot_c": sample1["temperature"]["live_hotspot_c"],
            "live_zone_count": sample1["temperature"]["live_zone_count"],
            "scan_unix_ms": sample1["temperature"]["live_scan_unix_ms"],
            "scan_age_ms": 0,
        },
    )
    assert errors == []
    assert sample2["compute"]["cpu_percent"] == 40.0
    assert sample2["video_decode"]["scan_age_ms"] >= 0
    assert sample2["temperature"]["live_scan_age_ms"] >= 0
    summary = fan.summarize([sample1, sample2], 1.5)
    assert summary["collector_integrity"] == "PASS"
    assert summary["controller_policy_evaluation"] == "PASS"
    assert summary["profile_consistent_in_all_samples"] is True
    assert summary["pwm"]["actual_transitions"] == 0
    assert summary["rpm"] == {
        "available_in_all_samples": True,
        "min": 2031,
        "max": 2031,
    }
    assert summary["decoder_fd_observed"] is True
    assert summary["acoustic_acceptance"]["status"] == "NOT_EVALUATED"

    inconsistent = dict(sample2)
    inconsistent["profile"] = dict(sample2["profile"], consistent=False)
    rejected = fan.summarize([sample1, inconsistent], 1.5)
    assert rejected["collector_integrity"] == "PASS"
    assert rejected["controller_policy_evaluation"] == "FAIL"

    (sys_root / "class/hwmon/hwmon40/fan1_input").unlink()
    no_rpm, _counters, errors = fan.collect_sample(paths, counters, 6.0)
    assert errors == []
    assert no_rpm["fan"]["actual_rpm"] == {
        "available": False,
        "value": None,
        "source": None,
    }

    (sys_root / "class/hwmon/hwmon40/pwm1").unlink()
    missing_pwm, _counters, errors = fan.collect_sample(paths, counters, 6.0)
    assert "actual-pwm-missing" in errors
    assert missing_pwm["collector_integrity"] == "FAIL"

    gpu.chmod(0o644)
    public_gpu = fan.read_gpu_cache(gpu, time.time())
    assert public_gpu["available"] is False
    gpu.chmod(0o600)
    linked_gpu = gpu.with_name("gpu-hardlink.json")
    os.link(gpu, linked_gpu)
    assert fan.read_gpu_cache(gpu, time.time())["available"] is False
    linked_gpu.unlink()
    gpu.unlink()
    target_gpu = gpu.with_name("gpu-target.json")
    put(target_gpu, "{}")
    target_gpu.chmod(0o600)
    gpu.symlink_to(target_gpu)
    assert fan.read_gpu_cache(gpu, time.time())["available"] is False
    gpu.unlink()
    with gpu.open("wb") as stream:
        stream.truncate(fan.MAX_GPU_CACHE_BYTES + 1)
    gpu.chmod(0o600)
    assert fan.read_gpu_cache(gpu, time.time())["available"] is False

    output = root / "fan-evidence.jsonl"
    stream = fan.open_private_output(str(output))
    stream.write("private\n")
    stream.close()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    try:
        fan.open_private_output(str(output))
    except FileExistsError:
        pass
    else:
        raise AssertionError("fan evidence output was overwritten")
    assert output.read_text(encoding="utf-8") == "private\n"

source = COLLECTOR.read_text(encoding="utf-8")
assert "subprocess" not in source
assert "os.system" not in source
assert "fancontrol-set-profile" not in source
assert "write_pwm" not in source
assert "--workload" not in source
assert fan.parse_args([]).samples == 1
assert fan.parse_args([]).output == "-"
print("  [OK] fan collector keeps PWM/RPM/target and decode evidence distinct")
