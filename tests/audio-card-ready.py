#!/usr/bin/env python3
"""Unit and installation-policy checks for the cold-boot audio barrier."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "components/audio/pocketds-audio-card-ready.py"
DROPIN = (ROOT / "components/audio/90-pocketds-audio-card-ready.conf").read_text(
    encoding="utf-8"
)
INSTALLER = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
SPEC = importlib.util.spec_from_file_location("pocketds_audio_card_ready", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def fixture_runner(command: tuple[str, ...]) -> tuple[int, str]:
    if command == ("amixer", "-c", "0", "controls"):
        return 0, "\n".join(f"name='{name}'" for name in module.REQUIRED_CONTROLS)
    if command == ("aplay", "-l"):
        return 0, module.PLAYBACK_MARKER
    if command == ("arecord", "-l"):
        return 0, module.CAPTURE_MARKER
    raise AssertionError(command)


with tempfile.TemporaryDirectory() as directory:
    control = Path(directory) / "controlC0"
    assert module.probe_card(control, fixture_runner) == [str(control)]
    control.touch()
    assert module.probe_card(control, fixture_runner) == []

    def missing_capture(command: tuple[str, ...]) -> tuple[int, str]:
        code, output = fixture_runner(command)
        if command == ("arecord", "-l"):
            return code, ""
        return code, output

    assert module.probe_card(control, missing_capture) == ["MultiMedia3 capture PCM"]

samples = iter((["deferred"], [], []))
ready, missing = module.wait_until_ready(1, 0.001, 2, lambda: next(samples))
assert ready and missing == []

assert DROPIN.count("ExecStartPre=") == 2
assert "pocketds-audio-card-ready --timeout 30" in DROPIN
assert "90-pocketds-audio-card-ready.conf" in INSTALLER
assert "pocketds-audio-card-ready.py" in INSTALLER
assert "wireplumber.service.d/90-pocketds-audio-card-ready.conf" in INSTALLER

print("  [OK] cold-boot audio waits for stable playback, capture and mixer controls")
