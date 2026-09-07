#!/usr/bin/env python3
"""Static safety contract for the disabled PDS-005 experiment."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
EXP = ROOT / "experiments/pds005-panel-shell"
QML = (EXP / "qml/Main.qml").read_text(encoding="utf-8")
MAIN = (EXP / "src/main.cpp").read_text(encoding="utf-8")
UNIT = (EXP / "systemd/pocketds-panel-shell.service").read_text(encoding="utf-8")

for captured in ("820", "615", "819", "614", "816", "608", "283", "720"):
    assert not re.search(rf"(?<!\d){captured}(?!\d)", QML), captured
    assert not re.search(rf"(?<!\d){captured}(?!\d)", MAIN), captured

for anchor in ("AnchorTop", "AnchorBottom", "AnchorLeft", "AnchorRight"):
    assert anchor in QML
assert "screen: panelController.lowerScreen" in QML
assert 'QLatin1String("DSI-2")' in MAIN
assert "KeyboardInteractivityNone" in QML
assert "wantsToBeOnActiveScreen: false" in QML
assert "ScreenFromQWindow" in QML

methods = re.findall(r"Q_SCRIPTABLE void (\w+)\(", MAIN)
assert methods == ["Show", "Hide", "Toggle"], methods
assert "Type=dbus" in UNIT
assert "BusName=org.pocketds.Panel" in UNIT

all_source = "\n".join(
    path.read_text(encoding="utf-8")
    for path in EXP.rglob("*")
    if path.is_file() and "__pycache__" not in path.parts
)
for forbidden in (
    "killWindow",
    "KillUnit",
    "killall",
    "pkill",
    "kwin_wayland",
    "RestartUnit",
):
    assert forbidden not in all_source, forbidden

for installer in ROOT.glob("scripts/install*.sh"):
    source = installer.read_text(encoding="utf-8")
    assert "pds005-panel-shell" not in source, installer
    assert "pocketds-panel-shell" not in source, installer

makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
assert "pds005-panel-shell" not in makefile
assert "pocketds-panel-shell" not in makefile

print("  [OK] disabled LayerShell experiment has dynamic DSI-2 geometry and no installer path")
