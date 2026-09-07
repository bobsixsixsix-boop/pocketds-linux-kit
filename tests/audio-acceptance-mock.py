#!/usr/bin/env python3
"""Mock/static tests for the silent PDS-018B audio acceptance gate."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pocketds_audio_acceptance", ROOT / "scripts/pocketds-audio-acceptance.py"
)
assert SPEC and SPEC.loader
audio = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audio
SPEC.loader.exec_module(audio)


def result(name: str, stdout: str, status: str = "pass"):
    return audio.Result(name, status, 0 if status == "pass" else 1, 0.0, stdout, "")


def healthy_results(source: str = "alsa_input.platform-sound.HiFi__Mic__source"):
    values = (
        ("WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1", "on,off"),
        ("WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia2", "off,off"),
        ("DISPLAY_PORT_RX_0 Audio Mixer MultiMedia1", "off,off"),
        ("DISPLAY_PORT_RX_0 Audio Mixer MultiMedia2", "off,off"),
        ("MultiMedia3 Mixer VA_CODEC_DMA_TX_0", "on,off"),
        ("MultiMedia4 Mixer VA_CODEC_DMA_TX_0", "off,off"),
        ("VA DEC0 MUX", "0"),
        ("VA DEC1 MUX", "0"),
        ("VA DMIC MUX0", "1"),
        ("VA DMIC MUX1", "0"),
        ("VA_AIF1_CAP Mixer DEC0", "on"),
        ("VA_AIF1_CAP Mixer DEC1", "off"),
        ("VA_DEC0 Volume", "100"),
        ("VA_DEC1 Volume", "84"),
    )
    mixer = "\n".join(
        f"numid={number},name='{control}'\n : values={value}"
        for number, (control, value) in enumerate(values, 70)
    )
    return [
        result("kernel-release", "7.1.0-100.20260730172655.pocketds.fc44.aarch64\n"),
        result("alsa-cards", "0 [SM8550APS]: sm8550 - SM8550-APS\n"),
        result(
            "alsa-pcm-map",
            "00-02: MultiMedia3 Capture (*) : : capture 1\n"
            "00-03: MultiMedia4 Capture (*) : : capture 1\n",
        ),
        result("ucm-package", "alsa-ucm 1.2.16.1-1.fc44 noarch\n"),
        result("services", "ActiveState=active\nNRestarts=0\n" * 3),
        result("default-sink", "alsa_output.platform-sound.HiFi__Speaker__sink\n"),
        result("default-source", source + "\n"),
        result("sources", f"46\t{source}\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"),
        result(
            "capture-enumeration",
            "card 0: SM8550APS [SM8550-APS], device 2: MultiMedia3 Capture (*) []\n"
            "card 0: SM8550APS [SM8550-APS], device 3: MultiMedia4 Capture (*) []\n",
        ),
        result("mixer-state", mixer),
    ]


class AudioAcceptanceTests(unittest.TestCase):
    def test_default_plan_is_strictly_read_only_and_silent(self):
        plan = audio.build_plan()
        commands = [token for step in plan for token in step.argv]
        forbidden = {
            "cset", "set-default-sink", "set-default-source", "set-card-profile",
            "restart", "start", "stop", "speaker-test", "paplay", "parecord",
            "pw-play", "pw-record",
        }
        self.assertFalse(forbidden.intersection(commands))
        self.assertEqual(next(step for step in plan if step.argv[0] == "arecord").argv, ("arecord", "-l"))

    def test_default_cli_checks_the_production_mic_without_historical_kernel_lock(self):
        args = audio.parser().parse_args([])
        self.assertIs(args.expect_physical_mic, True)
        self.assertIsNone(args.mic_manifest)

    def test_authoritative_production_source_passes_static_gates(self):
        repo = ROOT / "components/audio"
        topology = audio.topology_gate(repo, expect_physical_mic=True)
        routes = audio.static_route_pair_gate(repo)
        self.assertEqual(topology.status, "pass", topology)
        self.assertEqual(routes.status, "pass", routes)

    def test_historical_experimental_overlay_remains_archived_and_not_installed(self):
        experiment = ROOT / "experiments/audio/physical-mic"
        manifest = json.loads((experiment / "manifest.json").read_text(encoding="utf-8"))
        self.assertIs(manifest["default_install"], False)
        self.assertEqual(manifest["status"], "experimental")
        self.assertEqual(manifest["base_commit"], "88d307fd727ca073b3249f63a9d451eacfc887be")
        self.assertEqual(manifest["speaker_mm1_pair"], "1,0")
        self.assertEqual(
            manifest["base_hifi_sha256"],
            "18b874b47ff7e83e8b81f014e78ccedbe188b327af14cb44dbf3c9717d279258",
        )
        self.assertEqual(
            manifest["base_card_sha256"],
            "8d882cb105842de399f51b5c0f42ea161dfd7a8b43227d8d9087187b08bfd92f",
        )
        self.assertEqual(manifest["overlay_hifi_sha256"], audio.sha256(experiment / "HiFi.conf"))
        self.assertEqual(manifest["overlay_card_sha256"], audio.sha256(experiment / "SM8550-APS.conf"))
        self.assertEqual(audio.topology_gate(experiment, True).status, "pass")
        self.assertIn("DMIC1EnableSeq.conf", (experiment / "HiFi.conf").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["kernel"]["source_commit"],
            "a4975ce7c8eec079bd0cf7ec3890e100e016813f",
        )
        self.assertEqual(
            manifest["ucm_reference"]["commit"],
            "00175aa645c482111d096c3d8230f182a875d286",
        )
        self.assertTrue(manifest["validation"]["kernel_capture_prerequisites"])
        for path in (ROOT / "scripts/install.sh", ROOT / "scripts/import-live.sh"):
            self.assertNotIn("experiments/audio", path.read_text(encoding="utf-8"))
        self.assertIn('SectionDevice."Mic"', (ROOT / "components/audio/HiFi.conf").read_text(encoding="utf-8"))

    def test_repo_live_hash_gate_passes_exact_copy_and_fails_drift(self):
        source = ROOT / "components/audio"
        with tempfile.TemporaryDirectory() as name:
            live = Path(name)
            for filename in audio.UCM_FILES:
                (live / filename).write_bytes((source / filename).read_bytes())
            self.assertEqual(audio.hash_gate(source, live).status, "pass")
            (live / "HiFi.conf").write_text("drift\n", encoding="utf-8")
            gate = audio.hash_gate(source, live)
            self.assertEqual(gate.status, "fail")
            self.assertIn("HiFi.conf:mismatch", gate.message)

    def test_monitor_source_is_never_accepted_as_physical_mic(self):
        results = healthy_results("alsa_output.platform-sound.HiFi__Speaker__sink.monitor")
        base = audio.source_gate(results, expect_physical_mic=False)
        experiment = audio.source_gate(results, expect_physical_mic=True)
        self.assertEqual(base.status, "skip")
        self.assertEqual(experiment.status, "fail")

    def test_real_source_passes_the_production_source_gate(self):
        source = "alsa_input.platform-sound.HiFi__Mic__source"
        gate = audio.source_gate(healthy_results(source), expect_physical_mic=True)
        self.assertEqual(gate.status, "pass", gate)

    def test_live_route_pair_gate_rejects_machine_pair_mismatch(self):
        results = healthy_results()
        mixer = next(item for item in results if item.name == "mixer-state")
        mixer.stdout = mixer.stdout.replace("values=on,off", "values=on,on", 1)
        gate = audio.live_route_pair_gate(results)
        self.assertEqual(gate.status, "fail")
        self.assertIn("on,on", gate.message)

    def test_physical_mic_capability_matches_locked_kernel_and_controls(self):
        manifest = ROOT / "experiments/audio/physical-mic/manifest.json"
        gate = audio.capture_capability_gate(healthy_results(), manifest)
        self.assertEqual(gate.status, "pass", gate)
        self.assertFalse(gate.evidence["stream_tested"])
        self.assertEqual(
            gate.evidence["current_route_read_only"]["VA DMIC MUX0"], "1"
        )

    def test_physical_mic_capability_fails_on_kernel_or_control_drift(self):
        results = healthy_results()
        next(item for item in results if item.name == "kernel-release").stdout = "7.2.0-drift\n"
        mixer = next(item for item in results if item.name == "mixer-state")
        mixer.stdout = mixer.stdout.replace("name='VA DMIC MUX1'", "name='missing-control'")
        gate = audio.capture_capability_gate(
            results, ROOT / "experiments/audio/physical-mic/manifest.json"
        )
        self.assertEqual(gate.status, "fail")
        self.assertIn("locked-kernel-release", gate.message)
        self.assertIn("control:VA DMIC MUX1", gate.message)

    def test_evaluator_passes_base_fixture_with_matching_hashes(self):
        source = ROOT / "components/audio"
        with tempfile.TemporaryDirectory() as name:
            live = Path(name)
            for filename in audio.UCM_FILES:
                (live / filename).write_bytes((source / filename).read_bytes())
            gates = audio.evaluate(healthy_results(), source, live, True)
        self.assertFalse([gate for gate in gates if gate.status == "fail"], gates)

    def test_runner_cancellation_terminates_process_group(self):
        runner = audio.Runner()
        timer = threading.Timer(0.1, runner.cancel)
        timer.start()
        started = time.monotonic()
        item = runner.run(
            audio.Step("sleep", (sys.executable, "-c", "import time; time.sleep(30)"), 10.0)
        )
        timer.cancel()
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(item.status, "cancelled")
        self.assertEqual(item.returncode, 130)


if __name__ == "__main__":
    unittest.main()
