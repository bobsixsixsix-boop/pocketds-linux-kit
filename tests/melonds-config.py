#!/usr/bin/env python3
"""Focused tests for the Pocket DS melonDS dual-window configuration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / "components/emulation/pocketds-melonds-prepare.py"
KWIN = ROOT / "components/emulation/kwin-melonds-dualscreen.js"
SPEC = importlib.util.spec_from_file_location("pocketds_melonds_prepare", PREPARE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


source = """[Instance0.Window0]
ScreenLayout = 3
ScreenSizing = 0
ShowOSD = true
Unrelated = \"keep\"

[Instance0.Window1]
Enabled = false
ScreenLayout = 2
ScreenSizing = 0
ShowOSD = true

[Instance0.Joystick]
A = -1
Down = -1

[Instance0.Keyboard]
HK_FullscreenToggle = -1
"""
patched = MODULE.patch_config(source)
parsed = tomllib.loads(patched)
assert parsed["Instance0"]["Window0"]["ScreenLayout"] == 0
assert parsed["Instance0"]["Window0"]["ScreenSizing"] == 4
assert parsed["Instance0"]["Window0"]["ShowOSD"] is False
assert parsed["Instance0"]["Window0"]["Unrelated"] == "keep"
assert parsed["Instance0"]["Window1"]["Enabled"] is True
assert parsed["Instance0"]["Window1"]["ScreenSizing"] == 5
assert parsed["Instance0"]["Joystick"] == {
    "A": 0,
    "B": 1,
    "X": 2,
    "Y": 3,
    "L": 4,
    "R": 5,
    "Select": 6,
    "Start": 7,
    "Up": 257,
    "Right": 258,
    "Down": 260,
    "Left": 264,
}
assert parsed["Instance0"]["Keyboard"]["HK_FullscreenToggle"] == 16777274
assert MODULE.patch_config(patched) == patched

with tempfile.TemporaryDirectory(prefix="pds-melonds-config-") as temporary:
    path = Path(temporary) / "melonDS.toml"
    path.write_text(source, encoding="utf-8")
    assert MODULE.update(path) is True
    assert MODULE.update(path) is False

with tempfile.TemporaryDirectory(prefix="pds-melonds-bootstrap-") as temporary:
    root = Path(temporary)
    path = root / "config" / "melonDS" / "melonDS.toml"
    save_dir = root / "Saves" / "melonDS"
    state_dir = root / "States" / "melonDS"
    assert MODULE.update(path, save_dir, state_dir) is True
    assert path.is_file() and not path.is_symlink()
    assert path.stat().st_mode & 0o777 == 0o600
    assert save_dir.is_dir() and state_dir.is_dir()
    assert MODULE.update(path, save_dir, state_dir) is False
    bootstrapped = tomllib.loads(path.read_text(encoding="utf-8"))
    assert bootstrapped["Instance0"]["SaveFilePath"] == str(save_dir)
    assert bootstrapped["Instance0"]["SavestatePath"] == str(state_dir)
    assert bootstrapped["Instance0"]["Window0"]["ScreenSizing"] == 4
    assert bootstrapped["Instance0"]["Window1"]["ScreenSizing"] == 5
    assert bootstrapped["Instance0"]["Joystick"]["Start"] == 7

with tempfile.TemporaryDirectory(prefix="pds-melonds-symlink-") as temporary:
    root = Path(temporary)
    target = root / "target"
    target.mkdir()
    parent_link = root / "config"
    parent_link.symlink_to(target, target_is_directory=True)
    try:
        MODULE.update(parent_link / "melonDS.toml")
    except MODULE.PrepareError as exc:
        assert "parent is not a directory" in str(exc)
    else:
        raise AssertionError("symlinked config parent must fail closed")

    file_target = root / "target.toml"
    file_target.write_text("", encoding="utf-8")
    config_link = root / "melonDS.toml"
    config_link.symlink_to(file_target)
    try:
        MODULE.update(config_link)
    except MODULE.PrepareError as exc:
        assert "not a regular file" in str(exc)
    else:
        raise AssertionError("symlinked config file must fail closed")

with tempfile.TemporaryDirectory(prefix="pds-melonds-save-symlink-") as temporary:
    root = Path(temporary)
    path = root / "config" / "melonDS.toml"
    target = root / "target"
    target.mkdir()
    save_dir = root / "Saves"
    save_dir.symlink_to(target, target_is_directory=True)
    try:
        MODULE.update(path, save_dir, root / "States")
    except MODULE.PrepareError as exc:
        assert "parent is not a directory" in str(exc)
    else:
        raise AssertionError("symlinked save directory must fail closed")

kwin = KWIN.read_text(encoding="utf-8")
for required in (
    'window.resourceClass !== "net.kuribo64.melonDS"',
    'window.caption.indexOf("[w1]") === 0',
    'window.caption.indexOf("[w2]") === 0',
    'outputNamed("DSI-1")',
    'outputNamed("DSI-2")',
    "workspace.sendClientToScreen(window, target)",
    "if (window.fullScreen) return",
    "window.output.name !== target.name",
    "window.outputChanged.connect",
    "window.fullScreenChanged.connect",
):
    assert required in kwin, required
assert "window.fullScreen = true" not in kwin

print("  [OK] melonDS top/bottom windows keep exact Pocket DS display roles")
