#!/usr/bin/env python3
"""Static regression checks for Pocket DS speaker, microphone and DP UCM."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HIFI = (ROOT / "components/audio/HiFi.conf").read_text(encoding="utf-8")
CARD = (ROOT / "components/audio/SM8550-APS.conf").read_text(encoding="utf-8")
KEYBOARD = (ROOT / "components/keyboard/pocketds-keyboard.py").read_text(
    encoding="utf-8"
)


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"[FAIL] missing {label}: {needle}")


def require_count(text: str, needle: str, count: int, label: str) -> None:
    actual = text.count(needle)
    if actual != count:
        raise SystemExit(
            f"[FAIL] {label}: expected {count} occurrence(s), found {actual}: {needle}"
        )


require(HIFI, "SectionDevice.\"Speaker\"", "Speaker device")
require(HIFI, 'PlaybackPCM "hw:${CardId},0"', "Speaker MM1 PCM")
require(HIFI, "SectionDevice.\"DisplayPort\"", "DisplayPort device")
require(HIFI, 'PlaybackPCM "hw:${CardId},1"', "DisplayPort MM2 PCM")
require(HIFI, 'SectionDevice."Mic"', "system microphone device")
require(HIFI, 'CapturePCM "hw:${CardId},2"', "microphone MM3 PCM")
require(HIFI, "CaptureChannels 2", "raw stereo microphone contract")
require(HIFI, "DMIC0EnableSeq.conf", "DMIC0 enable sequence")
require(HIFI, "DMIC0DisableSeq.conf", "DMIC0 disable sequence")
require(HIFI, "DMIC1EnableSeq.conf", "DMIC1 enable sequence")
require(HIFI, "DMIC1DisableSeq.conf", "DMIC1 disable sequence")
require(HIFI, "WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1' 1,0", "machine-valid Speaker MM1 route")
require(HIFI, "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia2' 1,1", "stereo DisplayPort MM2 route")
require(HIFI, "MultiMedia3 Mixer VA_CODEC_DMA_TX_0' 1,0", "machine-valid microphone frontend")
require(HIFI, "MultiMedia3 Mixer VA_CODEC_DMA_TX_0' 0,0", "microphone frontend disable")
require(CARD, "WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1' 1,0", "machine-valid boot Speaker route")
require(CARD, "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia1' 0,0", "stereo boot MM1 DP isolation")
require(CARD, "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia2' 0,0", "stereo boot MM2 DP default off")
require_count(
    CARD,
    "WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia2' 1",
    0,
    "boot sequence must not couple Speaker to MM2",
)
for unsafe in ("numid=", "configure_microphone"):
    if unsafe in KEYBOARD:
        raise SystemExit(f"[FAIL] keyboard retains unsafe microphone route: {unsafe}")
require(KEYBOARD, 'query(["get-default-source"])', "physical source check")
require(KEYBOARD, "self.start_microphone_session(generation)", "asynchronous source check")
require(KEYBOARD, 'if entry["mute"]:', "system microphone mute guard")
require(KEYBOARD, "if not any(values):", "zero microphone volume guard")
require(KEYBOARD, 'source.endswith(".monitor")', "monitor source rejection")
require(KEYBOARD, '"arecord"', "bounded direct ALSA recorder")
require(KEYBOARD, '"plughw:0,2"', "verified Pocket DS microphone PCM")
require(KEYBOARD, '"S16_LE"', "microphone sample format")
require(KEYBOARD, '"16000"', "microphone sample rate")
require(KEYBOARD, 'microphone_source == INTERNAL_MICROPHONE_SOURCE', "internal-only direct ALSA route")
require(KEYBOARD, '"parecord"', "external microphone recorder")
require(KEYBOARD, 'f"--device={microphone_source}"', "pinned external microphone")
require(KEYBOARD, '"--property=node.dont-fallback=true"', "no fallback when external source vanishes")
require(KEYBOARD, '"--property=node.dont-reconnect=true"', "no reconnect to another microphone")
require(KEYBOARD, '"--property=node.dont-move=true"', "no default-source-driven movement")
if "@DEFAULT_SOURCE@" in KEYBOARD:
    raise SystemExit("[FAIL] keyboard re-resolves the default source after validation")

print("  [OK] speaker, system microphone and DP routes match writable controls")
