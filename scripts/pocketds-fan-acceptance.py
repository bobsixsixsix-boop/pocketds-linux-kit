#!/usr/bin/env python3
"""Collect bounded, read-only Pocket DS fan acceptance evidence as JSONL.

The collector never changes the fan profile, writes PWM, starts a workload,
launches a browser, or restarts a service.  It reports missing evidence as
null/NOT_EVALUATED rather than substituting PWM for tachometer RPM or assuming
that an open video node proves successful frame decoding.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any


SCHEMA = 1
CONTROL_INTERVAL_S = 1.5
VIDEO_SCAN_INTERVAL_S = 10.0
THERMAL_SCAN_INTERVAL_S = 10.0
RAMP_UP_PER_TICK = 18
RAMP_DOWN_PER_TICK = 3
AUDITED_CONTROLLER_SHA256 = (
    "9f04ff7113a3e2ea8412fca17a44cd728c20f6cbb7ab5cd463d8f66dd006f0fe"
)
MAX_GPU_CACHE_BYTES = 262_144


@dataclass(frozen=True)
class Paths:
    sys_root: Path = Path("/sys")
    proc_root: Path = Path("/proc")
    state_file: Path = Path("/run/pocketds-fancontrol/state")
    profile_file: Path = Path("/etc/pocketds-fancontrol/profile")
    gpu_cache: Path = Path("/run/user/1000/pocketds-gpu-status.json")
    controller: Path = Path("/usr/bin/pocketds-fancontrol")


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def read_int(path: Path) -> int | None:
    value = read_text(path)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def age_ms(path: Path, now_s: float) -> int | None:
    try:
        return max(0, round((now_s - path.stat().st_mtime) * 1000))
    except OSError:
        return None


def parse_state(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    text = read_text(path)
    if text is None:
        return result
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def state_int(state: dict[str, str], key: str) -> int | None:
    try:
        return int(state[key])
    except (KeyError, ValueError):
        return None


def discover_fan_hwmon(sys_root: Path) -> dict[str, Any]:
    base = sys_root / "class" / "hwmon"
    for directory in sorted(base.glob("hwmon*")):
        name = read_text(directory / "name")
        if name not in {"pwmfan", "pwm-fan"}:
            continue
        rpm_path = directory / "fan1_input"
        rpm = read_int(rpm_path)
        return {
            "name": name,
            "pwm_path": directory / "pwm1",
            "pwm": read_int(directory / "pwm1"),
            "rpm_path": rpm_path,
            "rpm_available": rpm is not None,
            "rpm": rpm,
        }
    return {
        "name": None,
        "pwm_path": None,
        "pwm": None,
        "rpm_path": None,
        "rpm_available": False,
        "rpm": None,
    }


def live_temperatures(sys_root: Path) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    base = sys_root / "devices" / "virtual" / "thermal"
    for directory in sorted(base.glob("thermal_zone*")):
        zone_type = read_text(directory / "type")
        if not zone_type or not zone_type.startswith(("cpu", "gpu")):
            continue
        value = read_int(directory / "temp")
        if value is None:
            continue
        zones.append({"type": zone_type, "temp_c": round(value / 1000.0, 1)})
    return zones


def cpu_counters(proc_root: Path) -> tuple[int, int] | None:
    text = read_text(proc_root / "stat")
    if text is None:
        return None
    line = next((item for item in text.splitlines() if item.startswith("cpu ")), None)
    if line is None:
        return None
    try:
        values = [int(item) for item in line.split()[1:]]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def cpu_percent(
    previous: tuple[int, int] | None, current: tuple[int, int] | None
) -> float | None:
    if previous is None or current is None:
        return None
    idle_delta = current[0] - previous[0]
    total_delta = current[1] - previous[1]
    if total_delta <= 0 or idle_delta < 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta)), 2)


def read_gpu_cache(path: Path, now_s: float) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_nlink != 1
                or not 0 < metadata.st_size <= MAX_GPU_CACHE_BYTES
            ):
                raise ValueError("unsafe GPU cache")
            remaining = metadata.st_size
            content = bytearray()
            while remaining:
                block = os.read(descriptor, min(65_536, remaining))
                if not block:
                    raise ValueError("GPU cache changed while reading")
                content.extend(block)
                remaining -= len(block)
            if os.read(descriptor, 1):
                raise ValueError("GPU cache grew while reading")
        finally:
            os.close(descriptor)
        payload = json.loads(bytes(content).decode("utf-8", errors="strict"))
        if not isinstance(payload, dict):
            raise ValueError("GPU cache root is not an object")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {
            "available": False,
            "source": None,
            "semantics": None,
            "cache_age_ms": None,
            "gpu_percent": None,
            "gpu_freq_hz": None,
            "gpu_temp_c": None,
        }
    sample_ms = payload.get("sample_unix_ms")
    cache_age = None
    if isinstance(sample_ms, (int, float)):
        cache_age = max(0, round(now_s * 1000 - sample_ms))
    return {
        "available": True,
        "source": payload.get("source"),
        "semantics": payload.get("semantics"),
        "cache_age_ms": cache_age,
        "gpu_percent": payload.get("gpu_percent"),
        "gpu_freq_hz": payload.get("gpu_freq_hz"),
        "gpu_temp_c": payload.get("gpu_temp_c"),
    }


def video_nodes(sys_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for directory in sorted((sys_root / "class" / "video4linux").glob("video*")):
        name = read_text(directory / "name")
        if name:
            result[f"/dev/{directory.name}"] = name
    return result


def open_video_fds(
    proc_root: Path, nodes: dict[str, str], process_limit: int = 4096
) -> dict[str, Any]:
    evidence: list[dict[str, str]] = []
    considered = 0
    denied = 0
    own_uid = os.getuid()
    try:
        processes = sorted(
            (item for item in proc_root.iterdir() if item.name.isdigit()),
            key=lambda item: int(item.name),
        )
    except OSError:
        processes = []
    for process in processes[:process_limit]:
        try:
            if process.stat().st_uid != own_uid:
                continue
        except OSError:
            continue
        considered += 1
        comm = read_text(process / "comm") or "unknown"
        try:
            descriptors = list((process / "fd").iterdir())
        except PermissionError:
            denied += 1
            continue
        except OSError:
            continue
        seen: set[str] = set()
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            if target not in nodes or target in seen:
                continue
            seen.add(target)
            evidence.append(
                {
                    "process_comm": comm[:64],
                    "node": target,
                    "node_name": nodes[target],
                }
            )
    decoder = [item for item in evidence if "decoder" in item["node_name"].lower()]
    return {
        "semantics": "open-video-node-fd; supporting evidence, not decoded-frame proof",
        "decoder_nodes": [
            {"node": node, "name": name}
            for node, name in sorted(nodes.items())
            if "decoder" in name.lower()
        ],
        "open_video_fds": evidence,
        "open_decoder_fds": decoder,
        "decoder_fd_observed": bool(decoder),
        "processes_considered": considered,
        "permission_denied_processes": denied,
        "process_limit": process_limit,
    }


def collect_sample(
    paths: Paths,
    previous_cpu: tuple[int, int] | None,
    max_state_age_s: float,
    cached_video: dict[str, Any] | None = None,
    cached_live_thermal: dict[str, Any] | None = None,
    fan_hwmon_locator: dict[str, Any] | None = None,
    cached_controller_hash: str | None = None,
) -> tuple[dict[str, Any], tuple[int, int] | None, list[str]]:
    now_s = time.time()
    state = parse_state(paths.state_file)
    configured_profile = read_text(paths.profile_file)
    state_age = age_ms(paths.state_file, now_s)
    hwmon = dict(fan_hwmon_locator or discover_fan_hwmon(paths.sys_root))
    if hwmon["pwm_path"] is not None:
        hwmon["pwm"] = read_int(hwmon["pwm_path"])
    if hwmon["rpm_path"] is not None:
        hwmon["rpm"] = read_int(hwmon["rpm_path"])
        hwmon["rpm_available"] = hwmon["rpm"] is not None
    if cached_live_thermal is None:
        zones = live_temperatures(paths.sys_root)
        live_thermal = {
            "live_hotspot_c": max(
                (zone["temp_c"] for zone in zones), default=None
            ),
            "live_zone_count": len(zones),
            "scan_unix_ms": round(now_s * 1000),
            "scan_age_ms": 0,
        }
    else:
        live_thermal = dict(cached_live_thermal)
        scanned_ms = live_thermal.get("scan_unix_ms")
        live_thermal["scan_age_ms"] = (
            max(0, round(now_s * 1000 - scanned_ms))
            if isinstance(scanned_ms, (int, float))
            else None
        )
    current_cpu = cpu_counters(paths.proc_root)
    gpu = read_gpu_cache(paths.gpu_cache, now_s)
    nodes = video_nodes(paths.sys_root)
    if cached_video is None:
        video = open_video_fds(paths.proc_root, nodes)
        video["scan_unix_ms"] = round(now_s * 1000)
        video["scan_age_ms"] = 0
    else:
        video = dict(cached_video)
        scanned_ms = video.get("scan_unix_ms")
        video["scan_age_ms"] = (
            max(0, round(now_s * 1000 - scanned_ms))
            if isinstance(scanned_ms, (int, float))
            else None
        )
    controller_hash = cached_controller_hash or sha256_file(paths.controller)

    runtime_profile = state.get("profile")
    state_pwm = state_int(state, "pwm")
    target_pwm = state_int(state, "target_pwm")
    controller_temp = state_int(state, "temp_c")
    controller_hotspot = state_int(state, "hotspot_c")
    actual_pwm = hwmon["pwm"]
    live_hotspot = live_thermal["live_hotspot_c"]

    errors: list[str] = []
    if controller_hash != AUDITED_CONTROLLER_SHA256:
        errors.append("controller-hash-not-audited")
    if runtime_profile is None:
        errors.append("runtime-profile-missing")
    for key, value in (
        ("controller-temp-missing", controller_temp),
        ("controller-hotspot-missing", controller_hotspot),
        ("target-pwm-missing", target_pwm),
        ("controller-pwm-missing", state_pwm),
        ("actual-pwm-missing", actual_pwm),
        ("live-hotspot-missing", live_hotspot),
    ):
        if value is None:
            errors.append(key)
    if state_age is None or state_age > max_state_age_s * 1000:
        errors.append("controller-state-stale")

    sample = {
        "schema": SCHEMA,
        "type": "sample",
        "read_only": True,
        "sample_unix_ms": round(now_s * 1000),
        "collector_integrity": "PASS" if not errors else "FAIL",
        "errors": errors,
        "controller": {
            "sha256": controller_hash,
            "audited_sha256": AUDITED_CONTROLLER_SHA256,
            "audited_match": controller_hash == AUDITED_CONTROLLER_SHA256,
            "state_age_ms": state_age,
        },
        "profile": {
            "configured": configured_profile,
            "runtime": runtime_profile,
            "consistent": (
                configured_profile == runtime_profile
                if configured_profile is not None and runtime_profile is not None
                else None
            ),
        },
        "temperature": {
            "controller_smoothed_c": controller_temp,
            "controller_hotspot_c": controller_hotspot,
            "live_hotspot_c": live_hotspot,
            "live_zone_count": live_thermal["live_zone_count"],
            "live_scan_unix_ms": live_thermal["scan_unix_ms"],
            "live_scan_age_ms": live_thermal["scan_age_ms"],
        },
        "fan": {
            "target_pwm": {
                "value": target_pwm,
                "source": "controller-state",
                "scale": "0..255",
            },
            "controller_pwm": {
                "value": state_pwm,
                "source": "controller-state",
                "scale": "0..255",
            },
            "actual_pwm": {
                "value": actual_pwm,
                "source": "hwmon-pwm1",
                "scale": "0..255",
                "matches_controller_state": (
                    actual_pwm == state_pwm
                    if actual_pwm is not None and state_pwm is not None
                    else None
                ),
            },
            "actual_rpm": {
                "available": hwmon["rpm_available"],
                "value": hwmon["rpm"],
                "source": "hwmon-fan1_input" if hwmon["rpm_available"] else None,
            },
        },
        "compute": {
            "cpu_percent": cpu_percent(previous_cpu, current_cpu),
            "cpu_semantics": "aggregate-/proc/stat-delta",
            "gpu": gpu,
        },
        "video_decode": video,
    }
    return sample, current_cpu, errors


def transitions(samples: list[dict[str, Any]], key: str) -> int:
    values = [sample["fan"][key]["value"] for sample in samples]
    return sum(left != right for left, right in zip(values, values[1:]))


def extrema(samples: list[dict[str, Any]], path: tuple[str, ...]) -> tuple[Any, Any]:
    values: list[Any] = []
    for sample in samples:
        value: Any = sample
        for key in path:
            value = value[key]
        if isinstance(value, (int, float)):
            values.append(value)
    return (min(values), max(values)) if values else (None, None)


def ramp_violations(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for index, (previous, current) in enumerate(zip(samples, samples[1:]), start=1):
        old = previous["fan"]["actual_pwm"]["value"]
        new = current["fan"]["actual_pwm"]["value"]
        elapsed_ms = current["sample_unix_ms"] - previous["sample_unix_ms"]
        if not isinstance(old, int) or not isinstance(new, int) or elapsed_ms <= 0:
            continue
        # The collector and controller are not phase locked.  One extra tick
        # is allowed for a controller write immediately around either sample.
        ticks = math.ceil(elapsed_ms / (CONTROL_INTERVAL_S * 1000)) + 1
        delta = new - old
        hotspot = current["temperature"]["controller_hotspot_c"]
        if delta > RAMP_UP_PER_TICK * ticks and not (
            isinstance(hotspot, int) and hotspot >= 95 and new == 255
        ):
            violations.append({"sample_index": index, "direction": "up", "delta": delta})
        if delta < -(RAMP_DOWN_PER_TICK * ticks):
            violations.append(
                {"sample_index": index, "direction": "down", "delta": delta}
            )
    return violations


def safety_violations(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        hotspot = sample["temperature"]["controller_hotspot_c"]
        target = sample["fan"]["target_pwm"]["value"]
        actual = sample["fan"]["actual_pwm"]["value"]
        if not isinstance(hotspot, int):
            continue
        if hotspot >= 95 and actual != 255:
            violations.append({"sample_index": index, "rule": "hotspot>=95=>pwm=255"})
        elif hotspot >= 88 and (not isinstance(target, int) or target < 204):
            violations.append({"sample_index": index, "rule": "hotspot>=88=>target>=204"})
        elif hotspot >= 84 and (not isinstance(target, int) or target < 153):
            violations.append({"sample_index": index, "rule": "hotspot>=84=>target>=153"})
    return violations


def summarize(samples: list[dict[str, Any]], elapsed_s: float) -> dict[str, Any]:
    pwm_min, pwm_max = extrema(samples, ("fan", "actual_pwm", "value"))
    rpm_min, rpm_max = extrema(samples, ("fan", "actual_rpm", "value"))
    hotspot_min, hotspot_max = extrema(
        samples, ("temperature", "controller_hotspot_c")
    )
    cpu_min, cpu_max = extrema(samples, ("compute", "cpu_percent"))
    gpu_min, gpu_max = extrema(samples, ("compute", "gpu", "gpu_percent"))
    profiles = sorted(
        {
            sample["profile"]["runtime"]
            for sample in samples
            if sample["profile"]["runtime"] is not None
        }
    )
    ramp = ramp_violations(samples)
    safety = safety_violations(samples)
    integrity = all(sample["collector_integrity"] == "PASS" for sample in samples)
    audited = all(sample["controller"]["audited_match"] for sample in samples)
    profiles_consistent = all(
        sample["profile"]["consistent"] is True for sample in samples
    )
    profile_changed = len(profiles) > 1
    if not audited:
        policy_status = "NOT_EVALUATED"
    elif ramp or safety or profile_changed or not profiles_consistent:
        policy_status = "FAIL"
    else:
        policy_status = "PASS"
    return {
        "schema": SCHEMA,
        "type": "summary",
        "read_only": True,
        "sample_count": len(samples),
        "elapsed_s": round(elapsed_s, 3),
        "collector_integrity": "PASS" if integrity else "FAIL",
        "controller_policy_evaluation": policy_status,
        "profile_values": profiles,
        "profile_changed": profile_changed,
        "profile_consistent_in_all_samples": profiles_consistent,
        "pwm": {
            "min": pwm_min,
            "max": pwm_max,
            "actual_transitions": transitions(samples, "actual_pwm"),
            "target_transitions": transitions(samples, "target_pwm"),
            "ramp_envelope_violations": ramp,
            "safety_floor_violations": safety,
        },
        "rpm": {
            "available_in_all_samples": all(
                sample["fan"]["actual_rpm"]["available"] for sample in samples
            ),
            "min": rpm_min,
            "max": rpm_max,
        },
        "temperature_hotspot_c": {"min": hotspot_min, "max": hotspot_max},
        "cpu_percent": {"min": cpu_min, "max": cpu_max},
        "gpu_percent": {"min": gpu_min, "max": gpu_max},
        "decoder_fd_observed": any(
            sample["video_decode"]["decoder_fd_observed"] for sample in samples
        ),
        "acoustic_acceptance": {
            "status": "NOT_EVALUATED",
            "reason": "requires a user-present, repeatable video and listening protocol",
        },
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=CONTROL_INTERVAL_S)
    parser.add_argument("--output", default="-", help="JSONL path; '-' is stdout")
    parser.add_argument("--max-state-age", type=float, default=6.0)
    parser.add_argument("--sys-root", type=Path, default=Path("/sys"))
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument(
        "--state-file", type=Path, default=Path("/run/pocketds-fancontrol/state")
    )
    parser.add_argument(
        "--profile-file", type=Path, default=Path("/etc/pocketds-fancontrol/profile")
    )
    parser.add_argument(
        "--gpu-cache", type=Path, default=runtime / "pocketds-gpu-status.json"
    )
    parser.add_argument(
        "--controller", type=Path, default=Path("/usr/bin/pocketds-fancontrol")
    )
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 2400:
        parser.error("--samples must be between 1 and 2400")
    if not 0.5 <= args.interval <= 60:
        parser.error("--interval must be between 0.5 and 60 seconds")
    if not 1 <= args.max_state_age <= 60:
        parser.error("--max-state-age must be between 1 and 60 seconds")
    return args


def emit(stream: Any, record: dict[str, Any]) -> None:
    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


def open_private_output(destination: str):
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(destination, flags, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    paths = Paths(
        sys_root=args.sys_root,
        proc_root=args.proc_root,
        state_file=args.state_file,
        profile_file=args.profile_file,
        gpu_cache=args.gpu_cache,
        controller=args.controller,
    )
    stream = sys.stdout if args.output == "-" else open_private_output(args.output)
    samples: list[dict[str, Any]] = []
    previous_cpu = None
    cached_video = None
    last_video_scan = 0.0
    cached_live_thermal = None
    last_thermal_scan = 0.0
    fan_hwmon_locator = discover_fan_hwmon(paths.sys_root)
    cached_controller_hash = sha256_file(paths.controller)
    started = time.monotonic()
    try:
        for index in range(args.samples):
            now = time.monotonic()
            if cached_video is None or now - last_video_scan >= VIDEO_SCAN_INTERVAL_S:
                cached_video = None
                last_video_scan = now
            if (
                cached_live_thermal is None
                or now - last_thermal_scan >= THERMAL_SCAN_INTERVAL_S
            ):
                cached_live_thermal = None
                last_thermal_scan = now
            sample, previous_cpu, _errors = collect_sample(
                paths,
                previous_cpu,
                args.max_state_age,
                cached_video,
                cached_live_thermal,
                fan_hwmon_locator,
                cached_controller_hash,
            )
            cached_video = sample["video_decode"]
            cached_live_thermal = {
                "live_hotspot_c": sample["temperature"]["live_hotspot_c"],
                "live_zone_count": sample["temperature"]["live_zone_count"],
                "scan_unix_ms": sample["temperature"]["live_scan_unix_ms"],
                "scan_age_ms": sample["temperature"]["live_scan_age_ms"],
            }
            samples.append(sample)
            emit(stream, sample)
            if index + 1 < args.samples:
                time.sleep(args.interval)
        summary = summarize(samples, time.monotonic() - started)
        emit(stream, summary)
    finally:
        if stream is not sys.stdout:
            stream.close()
    if summary["collector_integrity"] != "PASS":
        return 2
    if summary["controller_policy_evaluation"] != "PASS":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
