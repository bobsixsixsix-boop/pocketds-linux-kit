#!/usr/bin/env python3
"""Static and unit checks for the speaker-only PipeWire enhancement."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENHANCEMENT_SOURCE = (
    ROOT / "components/audio/pocketds-speaker-enhancement.py"
).read_text(encoding="utf-8")
CONFIG = (ROOT / "components/audio/90-pocketds-speaker-enhancement.conf").read_text(
    encoding="utf-8"
)
SERVICE = (ROOT / "components/audio/pocketds-speaker-enhancement.service").read_text(
    encoding="utf-8"
)
FILTER_SERVICE = (
    ROOT / "components/audio/filter-chain-pocketds-speaker.conf"
).read_text(encoding="utf-8")
INSTALLER = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
SPEC = importlib.util.spec_from_file_location(
    "pocketds_speaker_enhancement",
    ROOT / "components/audio/pocketds-speaker-enhancement.py",
)
assert SPEC and SPEC.loader
enhancement = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = enhancement
SPEC.loader.exec_module(enhancement)


def require(needle: str, text: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"[FAIL] missing {label}: {needle}")


require("libpipewire-module-filter-chain", CONFIG, "filter-chain module")
require('media.class = Audio/Sink', CONFIG, "virtual sink")
require(
    'target.object = "alsa_output.platform-sound.HiFi__Speaker__sink"',
    CONFIG,
    "speaker-only target",
)
require('node.name = "effect_input.pocketds_speaker_enhanced"', CONFIG, "stable sink name")
for label in ("bq_highpass", "bq_lowshelf", "bq_peaking", "bq_highshelf"):
    require(f"label = {label}", CONFIG, f"{label} stage")
require('label = clamp', CONFIG, "limiter input ceiling")
require('"Gain 1" = 1.5 "Gain 2" = -0.5', CONFIG, "cubic soft limiter")
require('"Mult" = 0.96', CONFIG, "output ceiling")

for forbidden in (
    "Audio/Source",
    "HiFi__Mic__source",
    "bluez_",
    "WSA_COMP",
    "VISENSE",
    "CPS",
    "amixer",
):
    if forbidden in CONFIG:
        raise SystemExit(f"[FAIL] enhancement crosses the speaker-only boundary: {forbidden}")
if CONFIG.count("target.object") != 1:
    raise SystemExit("[FAIL] enhancement must have exactly one explicit playback target")

require("Requires=filter-chain.service", SERVICE, "isolated filter daemon")
require("ExecStart=%h/.local/libexec/pocketds/pocketds-speaker-enhancement activate", SERVICE, "activation")
require("ExecStop=%h/.local/libexec/pocketds/pocketds-speaker-enhancement deactivate", SERVICE, "volume-safe rollback")
require("PartOf=pocketds-speaker-enhancement.service", FILTER_SERVICE, "shared filter lifecycle")

require("90-pocketds-speaker-enhancement.conf", INSTALLER, "filter installation")
require("pocketds-speaker-enhancement.py", INSTALLER, "controller installation")
require("pocketds-speaker-enhancement.service", INSTALLER, "service installation")
require("filter-chain-pocketds-speaker.conf", INSTALLER, "filter lifecycle installation")
require("enable pocketds-speaker-enhancement.service", INSTALLER, "service enablement")
require("start_updated_user_service pocketds-speaker-enhancement.service", INSTALLER, "changed service activation")
require("start_updated_user_service filter-chain.service", INSTALLER, "changed filter configuration activation")
require('environment["LC_ALL"] = "C"', ENHANCEMENT_SOURCE, "locale-stable pactl output")

assert enhancement.BACKING_VOLUME_PERCENT == 100
assert enhancement.virtual_volume_for(41) == 41
assert enhancement.virtual_volume_for(70) == 70
assert enhancement.virtual_volume_for(100) == 100
assert enhancement.effective_volume_for(41, 100) == 41
assert enhancement.effective_volume_for(100, 100) == 100


class FakePactl:
    def __init__(self, default: str) -> None:
        self.default = default
        self.sinks = {
            enhancement.PHYSICAL_SINK: {"volume": 41, "muted": False},
            enhancement.ENHANCED_SINK: {"volume": 100, "muted": False},
            "bluez_output.test": {"volume": 50, "muted": False},
        }

    def __call__(self, *args: str, check: bool = True) -> str:
        del check
        if args == ("list", "short", "sinks"):
            return "\n".join(
                f"{index}\t{name}\tPipeWire\tfloat32le 2ch 48000Hz\tIDLE"
                for index, name in enumerate(self.sinks, 40)
            )
        if args == ("get-default-sink",):
            return self.default
        if args[0] == "get-sink-volume":
            return f"Volume: front-left: 0 / {self.sinks[args[1]]['volume']}% / 0.00 dB"
        if args[0] == "get-sink-mute":
            return "Mute: yes" if self.sinks[args[1]]["muted"] else "Mute: no"
        if args[0] == "set-sink-volume":
            self.sinks[args[1]]["volume"] = int(args[2].rstrip("%"))
            return ""
        if args[0] == "set-sink-mute":
            self.sinks[args[1]]["muted"] = args[2] == "1"
            return ""
        if args[0] == "set-default-sink":
            self.default = args[1]
            return ""
        raise AssertionError(args)


original_pactl = enhancement.run_pactl
original_state_path = enhancement.STATE_PATH
try:
    with tempfile.TemporaryDirectory() as directory:
        enhancement.STATE_PATH = Path(directory) / "state.json"
        fake = FakePactl(enhancement.PHYSICAL_SINK)
        enhancement.run_pactl = fake
        enhancement.activate()
        assert fake.default == enhancement.ENHANCED_SINK
        assert fake.sinks[enhancement.PHYSICAL_SINK]["volume"] == 100
        assert fake.sinks[enhancement.ENHANCED_SINK]["volume"] == 41
        enhancement.deactivate()
        assert fake.default == enhancement.PHYSICAL_SINK
        assert fake.sinks[enhancement.PHYSICAL_SINK]["volume"] == 41

        fake = FakePactl("bluez_output.test")
        enhancement.run_pactl = fake
        enhancement.activate()
        assert fake.default == "bluez_output.test"
finally:
    enhancement.run_pactl = original_pactl
    enhancement.STATE_PATH = original_state_path

print("  [OK] speaker enhancement is routed, bounded, reversible and volume-safe")
