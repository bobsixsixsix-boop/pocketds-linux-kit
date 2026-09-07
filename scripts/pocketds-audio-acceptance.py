#!/usr/bin/env python3
"""Read-only Pocket DS MM1 audio acceptance gate.

The command never creates an audio stream, records samples, changes a mixer,
switches a profile/route, or restarts a service.  The production profile is
required to expose the already-verified physical microphone source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


UCM_FILES = ("HiFi.conf", "SM8550-APS.conf")
EXPECTED_CAPTURE_CONTROLS = (
    "MultiMedia3 Mixer VA_CODEC_DMA_TX_0",
    "MultiMedia4 Mixer VA_CODEC_DMA_TX_0",
    "VA DEC0 MUX",
    "VA DEC1 MUX",
    "VA DMIC MUX0",
    "VA DMIC MUX1",
    "VA_AIF1_CAP Mixer DEC0",
    "VA_AIF1_CAP Mixer DEC1",
    "VA_DEC0 Volume",
    "VA_DEC1 Volume",
)


@dataclass(frozen=True)
class Step:
    name: str
    argv: tuple[str, ...]
    timeout: float = 6.0


@dataclass
class Result:
    name: str
    status: str
    returncode: int
    seconds: float
    stdout: str
    stderr: str


@dataclass
class Gate:
    name: str
    status: str
    message: str
    evidence: dict[str, object]


def build_plan() -> list[Step]:
    """Return inspection-only commands; even arecord is enumeration-only."""
    return [
        Step("kernel-release", ("uname", "-r")),
        Step("alsa-cards", ("cat", "/proc/asound/cards")),
        Step("alsa-pcm-map", ("cat", "/proc/asound/pcm")),
        Step(
            "ucm-package",
            ("rpm", "-q", "--qf", "%{NAME} %{EVR} %{ARCH}\\n", "alsa-ucm"),
        ),
        Step(
            "services",
            (
                "systemctl", "--user", "show", "pipewire.service",
                "pipewire-pulse.service", "wireplumber.service", "-p", "Id",
                "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts",
            ),
        ),
        Step("graph", ("wpctl", "status", "-n")),
        Step("default-sink", ("pactl", "get-default-sink")),
        Step("default-source", ("pactl", "get-default-source")),
        Step("sinks", ("pactl", "list", "short", "sinks")),
        Step("sources", ("pactl", "list", "short", "sources")),
        Step("playback-enumeration", ("aplay", "-l")),
        Step("capture-enumeration", ("arecord", "-l")),
        Step("mixer-state", ("amixer", "-c", "0", "contents")),
    ]


class Runner:
    def __init__(self, env: dict[str, str] | None = None) -> None:
        self.env = env or os.environ.copy()
        self.child: subprocess.Popen[str] | None = None
        self.cancelled = False

    def cancel(self, *_unused: object) -> None:
        self.cancelled = True
        if self.child is not None and self.child.poll() is None:
            try:
                os.killpg(self.child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run(self, step: Step) -> Result:
        if self.cancelled:
            return Result(step.name, "cancelled", 130, 0.0, "", "")
        started = time.monotonic()
        try:
            self.child = subprocess.Popen(
                step.argv,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=self.env,
            )
        except OSError as error:
            return Result(step.name, "error", 127, 0.0, "", str(error))
        try:
            stdout, stderr = self.child.communicate(timeout=step.timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.child.pid, signal.SIGTERM)
                stdout, stderr = self.child.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid, signal.SIGKILL)
                stdout, stderr = self.child.communicate()
            return Result(
                step.name,
                "cancelled" if self.cancelled else "timeout",
                130 if self.cancelled else 124,
                round(time.monotonic() - started, 4),
                stdout,
                stderr,
            )
        finally:
            child = self.child
            self.child = None
        code = child.returncode
        if self.cancelled:
            return Result(
                step.name,
                "cancelled",
                130,
                round(time.monotonic() - started, 4),
                stdout,
                stderr,
            )
        return Result(
            step.name,
            "pass" if code == 0 else "fail",
            code,
            round(time.monotonic() - started, 4),
            stdout,
            stderr,
        )


def sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def hash_gate(repo_dir: Path, live_dir: Path) -> Gate:
    evidence: dict[str, object] = {}
    mismatches: list[str] = []
    for name in UCM_FILES:
        repo_hash = sha256(repo_dir / name)
        live_hash = sha256(live_dir / name)
        evidence[name] = {"repo": repo_hash, "live": live_hash}
        if repo_hash is None or live_hash is None:
            mismatches.append(f"{name}:missing")
        elif repo_hash != live_hash:
            mismatches.append(f"{name}:mismatch")
    return Gate(
        "repo-vs-live-hash",
        "fail" if mismatches else "pass",
        ", ".join(mismatches) if mismatches else "repository and live UCM hashes match",
        evidence,
    )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def topology_gate(repo_dir: Path, expect_physical_mic: bool) -> Gate:
    hifi = _read(repo_dir / "HiFi.conf")
    card = _read(repo_dir / "SM8550-APS.conf")
    expected = {
        "speaker-device": 'SectionDevice."Speaker"' in hifi,
        "speaker-mm1-pcm": 'PlaybackPCM "hw:${CardId},0"' in hifi,
        "displayport-device": 'SectionDevice."DisplayPort"' in hifi,
        "displayport-mm2-pcm": 'PlaybackPCM "hw:${CardId},1"' in hifi,
        "card-mm1-boot": "WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1' 1,0" in card,
    }
    mic_present = 'SectionDevice."Mic"' in hifi
    expected["mic-policy"] = mic_present if expect_physical_mic else not mic_present
    if expect_physical_mic:
        expected["mic-mm3-pcm"] = 'CapturePCM "hw:${CardId},2"' in hifi
        expected["mic-dmic0-enable-include"] = "DMIC0EnableSeq.conf" in hifi
        expected["mic-dmic0-disable-include"] = "DMIC0DisableSeq.conf" in hifi
        expected["mic-dmic1-enable-include"] = "DMIC1EnableSeq.conf" in hifi
        expected["mic-dmic1-disable-include"] = "DMIC1DisableSeq.conf" in hifi
    failures = [name for name, okay in expected.items() if not okay]
    return Gate(
        "ucm-topology",
        "fail" if failures else "pass",
        ", ".join(failures) if failures else "MM1 speaker/MM2 DP and microphone policy match",
        expected,
    )


def static_route_pair_gate(repo_dir: Path) -> Gate:
    hifi = _read(repo_dir / "HiFi.conf")
    card = _read(repo_dir / "SM8550-APS.conf")
    expected = {
        "verb-wsa-mm1-machine-pair": "WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1' 1,0" in hifi,
        "verb-dp-mm1-off": "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia1' 0,0" in hifi,
        "device-wsa-mm2-off": "WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia2' 0,0" in hifi,
        "device-dp-mm2-on": "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia2' 1,1" in hifi,
        "device-dp-mm2-off": "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia2' 0,0" in hifi,
        "boot-wsa-mm1-machine-pair": "WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1' 1,0" in card,
        "boot-dp-mm1-off": "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia1' 0,0" in card,
        "boot-dp-mm2-off": "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia2' 0,0" in card,
    }
    failures = [name for name, okay in expected.items() if not okay]
    return Gate(
        "ucm-route-pairs",
        "fail" if failures else "pass",
        ", ".join(failures)
        if failures
        else "frontend route values match the machine-verified UCM policy",
        expected,
    )


def _result(results: list[Result], name: str) -> Result | None:
    return next((item for item in results if item.name == name), None)


def _control_value(contents: str, control: str) -> str:
    for block in re.split(r"(?=numid=)", contents):
        if f"name='{control}'" not in block:
            continue
        match = re.search(r"^\s*: values=(.+)$", block, re.MULTILINE)
        return match.group(1).strip() if match else ""
    return ""


def live_route_pair_gate(results: list[Result]) -> Gate:
    mixer = _result(results, "mixer-state")
    text = mixer.stdout if mixer and mixer.status == "pass" else ""
    expected = {
        "WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia1": "on,off",
        "WSA_CODEC_DMA_RX_0 Audio Mixer MultiMedia2": "off,off",
        "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia1": "off,off",
        "DISPLAY_PORT_RX_0 Audio Mixer MultiMedia2": "off,off",
    }
    actual = {name: _control_value(text, name) for name in expected}
    failures = [name for name, wanted in expected.items() if actual[name] != wanted]
    return Gate(
        "live-route-pairs",
        "fail" if failures else "pass",
        "; ".join(f"{name}={actual[name] or 'missing'}" for name in failures)
        if failures else "live speaker and DP frontend route values are coherent",
        {"expected": expected, "actual": actual},
    )


def capture_capability_gate(results: list[Result], manifest_path: Path) -> Gate:
    """Prove the non-streaming kernel/UCM prerequisites for the mic overlay."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return Gate(
            "physical-microphone-capability",
            "fail",
            f"locked microphone manifest unavailable: {error}",
            {},
        )

    kernel = _result(results, "kernel-release")
    cards = _result(results, "alsa-cards")
    pcm = _result(results, "alsa-pcm-map")
    capture = _result(results, "capture-enumeration")
    mixer = _result(results, "mixer-state")
    package = _result(results, "ucm-package")
    kernel_text = kernel.stdout.strip() if kernel and kernel.status == "pass" else ""
    cards_text = cards.stdout if cards and cards.status == "pass" else ""
    pcm_text = pcm.stdout if pcm and pcm.status == "pass" else ""
    capture_text = capture.stdout if capture and capture.status == "pass" else ""
    mixer_text = mixer.stdout if mixer and mixer.status == "pass" else ""
    package_text = package.stdout.strip() if package and package.status == "pass" else ""

    locked_kernel = manifest.get("kernel", {})
    expected = {
        "locked-kernel-release": kernel_text == locked_kernel.get("release"),
        "sm8550-aps-card": "SM8550-APS" in cards_text,
        "mm3-proc-capture": "00-02: MultiMedia3 Capture" in pcm_text,
        "mm4-proc-capture": "00-03: MultiMedia4 Capture" in pcm_text,
        "mm3-arecord-capture": "device 2: MultiMedia3 Capture" in capture_text,
        "mm4-arecord-capture": "device 3: MultiMedia4 Capture" in capture_text,
        "alsa-ucm-package": package_text.startswith("alsa-ucm "),
    }
    controls = {
        name: f"name='{name}'" in mixer_text for name in EXPECTED_CAPTURE_CONTROLS
    }
    expected.update({f"control:{name}": present for name, present in controls.items()})
    failures = [name for name, okay in expected.items() if not okay]
    current_route = {
        name: _control_value(mixer_text, name)
        for name in (
            "MultiMedia3 Mixer VA_CODEC_DMA_TX_0",
            "MultiMedia4 Mixer VA_CODEC_DMA_TX_0",
            "VA DEC0 MUX",
            "VA DEC1 MUX",
            "VA DMIC MUX0",
            "VA DMIC MUX1",
            "VA_AIF1_CAP Mixer DEC0",
            "VA_AIF1_CAP Mixer DEC1",
            "VA_DEC0 Volume",
            "VA_DEC1 Volume",
        )
    }
    return Gate(
        "physical-microphone-capability",
        "fail" if failures else "pass",
        ", ".join(failures)
        if failures
        else "kernel capture PCMs and VA DMIC0/1 controls match the locked overlay prerequisites",
        {
            "expected": expected,
            "kernel_release": kernel_text,
            "kernel_source_commit": locked_kernel.get("source_commit"),
            "ucm_package": package_text,
            "controls": controls,
            "current_route_read_only": current_route,
            "stream_tested": False,
        },
    )


def source_gate(results: list[Result], expect_physical_mic: bool) -> Gate:
    default_result = _result(results, "default-source")
    sources_result = _result(results, "sources")
    default = default_result.stdout.strip() if default_result and default_result.status == "pass" else ""
    sources = sources_result.stdout if sources_result and sources_result.status == "pass" else ""
    names = [line.split("\t")[1] for line in sources.splitlines() if len(line.split("\t")) > 1]
    physical = [name for name in names if ".monitor" not in name and not name.endswith(".monitor")]
    default_is_physical = bool(default and ".monitor" not in default and not default.endswith(".monitor"))
    evidence = {"default": default, "physical_sources": physical, "all_sources": names}
    if expect_physical_mic:
        okay = default_is_physical and bool(physical)
        return Gate(
            "physical-microphone-source",
            "pass" if okay else "fail",
            default if okay else "monitor/missing source rejected in physical microphone mode",
            evidence,
        )
    if default_is_physical or physical:
        return Gate(
            "physical-microphone-source",
            "warn",
            "a physical source exists although the default MM1 source has no Mic device",
            evidence,
        )
    return Gate(
        "physical-microphone-source",
        "skip",
        "base profile intentionally has no physical mic; monitor source is not accepted as one",
        evidence,
    )


def service_gate(results: list[Result]) -> Gate:
    result = _result(results, "services")
    text = result.stdout if result and result.status == "pass" else ""
    okay = text.count("ActiveState=active") >= 3 and text.count("NRestarts=0") >= 3
    return Gate(
        "audio-services",
        "pass" if okay else "fail",
        "PipeWire/Pulse/WirePlumber active without restarts" if okay else "service state is incomplete or unhealthy",
        {"active_count": text.count("ActiveState=active"), "zero_restart_count": text.count("NRestarts=0")},
    )


def sink_gate(results: list[Result]) -> Gate:
    result = _result(results, "default-sink")
    sink = result.stdout.strip() if result and result.status == "pass" else ""
    okay = bool(sink and "auto_null" not in sink and "Speaker" in sink)
    return Gate("default-speaker-sink", "pass" if okay else "fail", sink or "missing default speaker sink", {"sink": sink})


def evaluate(
    results: list[Result], repo_dir: Path, live_dir: Path, expect_physical_mic: bool,
    mic_manifest: Path | None = None,
) -> list[Gate]:
    gates = [
        hash_gate(repo_dir, live_dir),
        topology_gate(repo_dir, expect_physical_mic),
        static_route_pair_gate(repo_dir),
        service_gate(results),
        sink_gate(results),
        source_gate(results, expect_physical_mic),
        live_route_pair_gate(results),
    ]
    if mic_manifest is not None:
        gates.insert(3, capture_capability_gate(results, mic_manifest))
    return gates


def parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--repo-ucm-dir", type=Path, default=root / "components/audio")
    value.add_argument(
        "--live-ucm-dir",
        type=Path,
        default=Path("/usr/share/alsa/ucm2/Qualcomm/sm8550/APS"),
    )
    value.add_argument(
        "--mic-manifest",
        type=Path,
        default=None,
        help="optional historical capture-candidate capability contract",
    )
    value.add_argument(
        "--expect-physical-mic",
        action="store_true",
        default=True,
        help="compatibility flag; the production profile always requires the physical microphone",
    )
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    runner = Runner()
    signal.signal(signal.SIGINT, runner.cancel)
    signal.signal(signal.SIGTERM, runner.cancel)
    results: list[Result] = []
    for step in build_plan():
        result = runner.run(step)
        results.append(result)
        if result.status == "cancelled":
            break
    gates = evaluate(
        results,
        args.repo_ucm_dir,
        args.live_ucm_dir,
        args.expect_physical_mic,
        args.mic_manifest,
    )
    failed = any(result.status != "pass" for result in results) or any(gate.status == "fail" for gate in gates)
    report = {
        "schema": 1,
        "mode": "silent-inspect-production-mic",
        "result": "FAIL" if failed else "PASS",
        "repo_ucm_dir": str(args.repo_ucm_dir),
        "live_ucm_dir": str(args.live_ucm_dir),
        "steps": [asdict(result) for result in results],
        "gates": [asdict(gate) for gate in gates],
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"[{result.status.upper()}] {result.name} ({result.seconds:.3f}s)")
        for gate in gates:
            print(f"[{gate.status.upper()}] {gate.name}: {gate.message}")
        print(f"audio acceptance: {report['result']}")
    if any(result.status == "cancelled" for result in results):
        return 130
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
