#!/usr/bin/env python3
"""Require one whole-stack transaction for every input install entrypoint."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
UNIFIED = (ROOT / "scripts/install-game-input-stack.py").read_text(encoding="utf-8")
WRAPPER = (ROOT / "scripts/install-input.sh").read_text(encoding="utf-8")

assert 'exec python3 "$repo_root/scripts/install-game-input-stack.py"' in WRAPPER
for forbidden in ("sudo ", "systemctl ", "install -m", "cp -a", "mv -f"):
    assert forbidden not in WRAPPER, forbidden
assert "Partial input activation is retired" in WRAPPER

for target in (
    "/usr/local/lib/pocketds/controller_test_gate.py",
    "/usr/local/libexec/pocketds-controller-test.py",
    "/etc/systemd/system/pocketds-controller-test.service",
    "/usr/local/libexec/pocketds-input-mode",
    ".local/libexec/pocketds-input-mode",
    "/usr/bin/pocketds-toggle-joymouse",
    "/usr/bin/pocketds-mode-listener",
    "/usr/local/libexec/pocketds-input-held-modifiers",
    "/usr/share/inputplumber/capability_maps/ayaneo_mcu_xbox.yaml",
    "/usr/share/inputplumber/profiles/pocketds-gamepad.yaml",
    "/usr/share/inputplumber/profiles/pocketds-joymouse.yaml",
    "/etc/systemd/system/inputplumber.service.d/10-pocketds-manage-all-devices.conf",
    "/etc/systemd/system/pocketds-mode-listener.service.d/20-input-state.conf",
    "/usr/lib/tmpfiles.d/pocketds-input-mode.conf",
):
    assert target in UNIFIED, target

tmpfiles = (ROOT / "components/inputplumber/pocketds-input-mode.conf").read_text(
    encoding="utf-8"
)
assert "f /run/pocketds-input-mode.lock 0640 root pocketds -" in tmpfiles
assert "d /run/pocketds-input-mode 2770 root pocketds -" in tmpfiles
listener = (
    ROOT / "components/inputplumber/20-pocketds-mode-listener-input-state.conf"
).read_text(encoding="utf-8")
for directive in (
    "Requires=systemd-tmpfiles-setup.service",
    "After=systemd-tmpfiles-setup.service inputplumber.service",
    "ReadWritePaths=/run/pocketds-input-mode",
    "Requires=pocketds-controller-test.service",
    "After=pocketds-controller-test.service",
    "ExecStartPre=/usr/local/libexec/pocketds-input-mode bootstrap",
):
    assert directive in listener, directive

assert "systemctl restart inputplumber" not in UNIFIED
assert "systemctl stop inputplumber" not in UNIFIED
assert "systemctl disable inputplumber" not in UNIFIED

main = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
assert main.index('python3 "$repo_root/scripts/install-game-input-stack.py"') < main.index("\ninstall_apps\n")
for entrypoint in (
    "scripts/install.sh",
    "scripts/install-telemetry.sh",
    "scripts/deploy-boot-switch-offline-internal.sh",
    "scripts/import-live.sh",
):
    source = (ROOT / entrypoint).read_text(encoding="utf-8")
    assert (
        "for ui_file in ControllerDiagram.qml ControllerTestSession.qml main.qml; do"
        in source
    ), entrypoint
    assert "components/control-panel/plasmoid/contents/ui/$ui_file" in source, entrypoint

makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
for operation in ("STAGE", "APPLY", "ROLLBACK"):
    assert f"POCKETDS-{operation}-TEN-UI-FILES" in makefile, operation
    assert f"POCKETDS-{operation}-EIGHT-UI-FILES" not in makefile, operation

print("  [OK] every input entrypoint uses the whole-stack transaction")
