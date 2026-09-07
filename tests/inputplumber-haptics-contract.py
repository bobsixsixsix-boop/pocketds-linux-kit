#!/usr/bin/env python3
"""Repository contract for the pinned InputPlumber haptics sidecar."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components" / "inputplumber"


def read(name: str) -> bytes:
    return (COMPONENT / name).read_bytes()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


lock = json.loads(read("inputplumber-haptics-source-lock.json"))
patch = read(lock["downstream_patch"])
profile = read("01-ayaneo-controller-haptics.yaml")
dropin = read("30-pocketds-haptics-candidate.conf")
policy = read("org.pocketds.InputPlumber.Haptics.policy")
installer = (ROOT / "scripts" / "pocketds-inputplumber-haptics.py").read_text()
main_installer = (ROOT / "scripts" / "install.sh").read_text()
readme = read("README.md").decode()

assert lock["schema"] == "pocketds.inputplumber-haptics-source.v1"
assert lock["upstream_commit"] == "d3932cb4e0de3eb47688e381b5eb5334ebac6254"
assert (len(patch), sha(patch)) == (
    lock["downstream_patch_size"],
    lock["downstream_patch_sha256"],
)
assert lock["binary_size"] == 9377664
assert lock["binary_sha256"] == (
    "4dbb8a7dc494e27abdbe3b38191dabfaef54caa8f6f2c7357bc84008bbfb488b"
)
for entry in lock["additional_patches"]:
    payload = read(entry["source"])
    assert (len(payload), sha(payload)) == (entry["size"], entry["sha256"])
assert lock["vendored_dependency_count"] == 343
assert lock["rollback_binary"] == "/usr/bin/inputplumber"

patch_text = patch.decode()
assert patch_text.count("diff --git ") == 3
assert set(
    line.split(" a/", 1)[1].split(" b/", 1)[0]
    for line in patch_text.splitlines()
    if line.startswith("diff --git a/")
) == {
    "bindings/dbus-xml/org.shadowblip.Output.ForceFeedback.xml",
    "rootfs/usr/share/polkit-1/actions/org.shadowblip.InputPlumber.policy",
    "src/dbus/interface/force_feedback.rs",
}
for token in (
    'method name="Pulse"',
    'arg name="value" type="d" direction="in"',
    'arg name="duration_ms" type="u" direction="in"',
    "const MAX_PULSE_VALUE: f64 = 0.70;",
    "const MIN_PULSE_DURATION_MS: u32 = 5;",
    "const MAX_PULSE_DURATION_MS: u32 = 50;",
    '"org.shadowblip.Output.ForceFeedback.Pulse"',
    "tokio::time::sleep(duration).await;",
    ".stop()",
    "bounds_keyboard_pulse_strength_and_duration",
):
    assert token in patch_text, token

payload_data = {
    "device_profile": profile,
    "service_dropin": dropin,
    "polkit_policy": policy,
}
for name, data in payload_data.items():
    entry = lock["install_payloads"][name]
    assert (len(data), sha(data)) == (entry["size"], entry["sha256"])

profile_text = profile.decode()
for token in (
    "value: AYANEO Pocket DS",
    'vendor_id: "4001"',
    'product_id: "0428"',
    "vendor_id: 0x4001",
    "product_id: 0x0428",
    "interface_num: 0",
    "- xbox-series",
    "- dbus",
):
    assert token in profile_text, token
assert profile_text.count("hidraw:") == 1
assert "ExecStart=/usr/local/libexec/pocketds-inputplumber-haptics" in dropin.decode()
assert policy.decode().count('id="org.shadowblip.Output.ForceFeedback.Pulse"') == 1

for token in (
    'SYSTEM_BINARY = "/usr/bin/inputplumber"',
    '"plan"',
    '"apply"',
    '"rollback"',
    '"--binary"',
    "fcntl.LOCK_EX | fcntl.LOCK_NB",
    'RECOVERABLE_PHASES = {"prepared", "apply-failed", "applied"}',
    "recover or rollback unfinished transaction(s)",
    "APPLY-INPUTPLUMBER-HAPTICS-D3932CB4",
    "ROLLBACK-INPUTPLUMBER-HAPTICS-D3932CB4",
):
    assert token in installer, token
assert "systemctl" not in installer
assert "/dev/hidraw" not in installer
assert lock["upstream_commit"] in readme
assert lock["binary_sha256"] in readme
assert str(lock["binary_size"]) in readme
assert lock["binary_sha256"] in main_installer
for token in (
    "/usr/local/libexec/pocketds-inputplumber-haptics",
    "/etc/inputplumber/devices.d/01-ayaneo-controller.yaml",
    "/etc/systemd/system/inputplumber.service.d/30-pocketds-haptics-candidate.conf",
    "/usr/share/polkit-1/actions/org.pocketds.InputPlumber.Haptics.policy",
    "INCOMPLETE: keyboard and game vibration",
):
    assert token in main_installer, token

print("  [OK] pinned Pulse patch, payload hashes, and sidecar scope match")
