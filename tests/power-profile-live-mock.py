#!/usr/bin/env python3
"""Run the opt-in TuneD acceptance harness against a synthetic sysfs."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests/power-profile-live.sh"

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    state = root / "state"
    state.mkdir()
    paths = {
        "active": root / "etc/tuned/active_profile",
        "fan": root / "etc/pocketds-fancontrol/profile",
        "governor": root / "sys/devices/system/cpu/cpufreq/policy0/scaling_governor",
        "cpu0": root / "sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq",
        "cpu3": root / "sys/devices/system/cpu/cpufreq/policy3/scaling_max_freq",
        "cpu7": root / "sys/devices/system/cpu/cpufreq/policy7/scaling_max_freq",
        "gpu_governor": root / "sys/class/devfreq/3d00000.gpu/governor",
        "gpu_max": root / "sys/class/devfreq/3d00000.gpu/max_freq",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unknown\n", encoding="utf-8")
    paths["active"].write_text("pocketds-balanced\n", encoding="utf-8")
    paths["fan"].write_text("moderate\n", encoding="utf-8")

    panelctl = root / "panelctl"
    panelctl.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['POCKETDS_TEST_ROOT'])
profiles = {
 'powersave': ('pocketds-powersave','schedutil','1670400','2188800','2476800','simple_ondemand','550000000','quiet'),
 'balanced': ('pocketds-balanced','schedutil','2016000','2803200','2956800','simple_ondemand','1000000000','moderate'),
 'performance': ('pocketds-performance','performance','2016000','2803200','2956800','performance','1000000000','aggressive'),
}
paths = ['etc/tuned/active_profile','sys/devices/system/cpu/cpufreq/policy0/scaling_governor','sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq','sys/devices/system/cpu/cpufreq/policy3/scaling_max_freq','sys/devices/system/cpu/cpufreq/policy7/scaling_max_freq','sys/class/devfreq/3d00000.gpu/governor','sys/class/devfreq/3d00000.gpu/max_freq','etc/pocketds-fancontrol/profile']
if sys.argv[1:] == ['status']:
 print(json.dumps({'power_profile': (root/'etc/tuned/active_profile').read_text().strip().removeprefix('pocketds-').replace('powersave','powersave')}))
 raise SystemExit
if len(sys.argv) != 3 or sys.argv[1] != 'power' or sys.argv[2] not in profiles:
 raise SystemExit(2)
actions = root/'state/actions'
failure_marker = root/'state/failure-injected'
attempt = len(actions.read_text().splitlines()) + 1 if actions.exists() else 1
if os.environ.get('POCKETDS_FAIL_ACTION_NUMBER') == str(attempt) and not failure_marker.exists():
 failure_marker.write_text('injected\\n')
 raise SystemExit(17)
for path, value in zip(paths, profiles[sys.argv[2]]):
 (root/path).write_text(value+'\\n')
with actions.open('a') as stream:
 stream.write(sys.argv[2]+'\\n')
""",
        encoding="utf-8",
    )
    tuned = root / "tuned-adm"
    tuned.write_text(
        "#!/usr/bin/env sh\n[ \"${1:-}\" = verify ] && [ \"$#\" -eq 1 ]\n",
        encoding="utf-8",
    )
    panelctl.chmod(0o755)
    tuned.chmod(0o755)

    env = os.environ.copy()
    env.update({
        "POCKETDS_TEST_ROOT": str(root),
        "POCKETDS_PANELCTL": str(panelctl),
        "POCKETDS_TUNED_ADM": str(tuned),
    })
    rejected = subprocess.run([HARNESS], env=env, capture_output=True, text=True)
    assert rejected.returncode == 2
    result = subprocess.run([HARNESS, "--apply"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "[PASS] 1/3 powersave" in result.stdout
    assert "[PASS] 2/3 balanced" in result.stdout
    assert "[PASS] 3/3 performance" in result.stdout
    assert paths["active"].read_text().strip() == "pocketds-balanced"
    actions = (state / "actions").read_text(encoding="utf-8").splitlines()
    assert actions == ["powersave", "balanced", "performance", "balanced"]

    (state / "actions").write_text("", encoding="utf-8")
    rejected_stress = subprocess.run(
        [HARNESS, "--apply", "--stress-100", "--confirm", "POCKETDS-POWER-100"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert rejected_stress.returncode == 2
    stress_env = {**env, "POCKETDS_ALLOW_POWER_STRESS": "YES"}
    result = subprocess.run(
        [HARNESS, "--apply", "--stress-100", "--confirm", "POCKETDS-POWER-100"],
        env=stress_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("[PASS]") == 101
    assert "[PASS] 100/100 powersave" in result.stdout
    assert "exact 100-switch matrix" in result.stdout
    assert paths["active"].read_text().strip() == "pocketds-balanced"
    actions = (state / "actions").read_text(encoding="utf-8").splitlines()
    expected = [["powersave", "balanced", "performance"][index % 3] for index in range(100)]
    assert actions == expected + ["balanced"]

    (state / "actions").write_text("", encoding="utf-8")
    failure_env = {**stress_env, "POCKETDS_FAIL_ACTION_NUMBER": "5"}
    failed = subprocess.run(
        [HARNESS, "--apply", "--stress-100", "--confirm", "POCKETDS-POWER-100"],
        env=failure_env,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert paths["active"].read_text().strip() == "pocketds-balanced"
    actions = (state / "actions").read_text(encoding="utf-8").splitlines()
    assert actions == ["powersave", "balanced", "performance", "powersave", "balanced"]

source = HARNESS.read_text(encoding="utf-8")
assert "tuned-adm profile" not in source
assert "scaling_governor" in source and ">" not in "\n".join(
    line for line in source.splitlines() if "scaling_governor" in line
)
assert "trap restore_original EXIT INT TERM" in source
assert "POCKETDS_ALLOW_POWER_STRESS" in source
assert "POCKETDS-POWER-100" in source
print("  [OK] live profile harness is opt-in, exhaustive and reversible")
