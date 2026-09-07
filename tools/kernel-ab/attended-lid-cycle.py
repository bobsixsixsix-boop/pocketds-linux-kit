#!/usr/bin/python3
"""One attended v4 Hall or closed-RTC cycle with a new, non-reusable receipt.

This is not daily-sleep enablement. The existing root guard alone owns RTC and
the logind request. No display mutation or input grab is performed by this test.
Run under a root transient service with cleanup as ExecStopPost and >=15 s stop
timeout. Agree the physical schedule before arming; never coordinate by a
suspended SSH stream. Hall: open 30 s after closing. RTC: open RTC+60 s after
closing. Each invocation performs at most one suspend, with no automatic retry.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import uuid

NOTES = "05ffb67550d15176df5302978a51cf1834fa70869e663f8e01c440c253116092"
IMAGES = {
    "v3": ("a136aeb060d38f9a305336af36577c5971e7c7e81e78839a608a60d30ff24c71", 19077120),
    "v4": ("57322cb6dc3bce822289bcd6bde5e5934362efc2547120ebc88be0eea7a89d92", 18993152),
}
LAYOUT_SHA = "c687bc5e857e1720d123375528efe9a853a36eeaf2b7ee9c559cc28f7d8f7a33"
GUARD_SHA = "68877ca5eb98bde3ebe5f4d1e01953b2e732965619cd0da75c97058dfcdcf36f"
POLICY_SHA = "d3caafee10b81d3718f13fb076b1703a294a3490c1650771e9ee6f33d1b97211"
POLICY = Path("/etc/systemd/sleep.conf.d/80-pocketds-sleep.conf")
GUARD = Path("/usr/local/libexec/pocketds-deep-suspend")
HERE = Path(__file__).resolve().parent


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def run(*argv, timeout=10):
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=True)
    require(len(completed.stdout) <= 1048576, "oversized command result")
    return completed.stdout.strip()


def root_file(path, expected=None, maximum=262144, minimum=1):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and minimum <= info.st_size <= maximum,
                "untrusted root file: " + path.name)
        data = stream.read(maximum + 1)
    require(len(data) <= maximum, "oversized root file")
    if expected:
        require(hashlib.sha256(data).hexdigest() == expected, "changed root file: " + path.name)
    return data


def load_trusted(path, digest, name):
    # Compile the validated bytes, not a second pathname read/import.
    code = root_file(path, digest)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader=None))
    module.__file__ = str(path)
    exec(compile(code, str(path), "exec"), module.__dict__)
    return module


def identity_parameters(args):
    require(str(uuid.UUID(args.boot_id)) == args.boot_id, "use exact canonical boot UUID")
    require(type(args.expected_success) is int and 0 <= args.expected_success <= 1000,
            "invalid expected cycle count")
    require(args.image in IMAGES, "only the known v3 or v4 disk image is supported")
    require(args.receipt.parent == Path("/run") and
            re.fullmatch(r"pds-lid-cycle-[a-z0-9][a-z0-9-]{0,63}", args.receipt.name),
            "use a new /run/pds-lid-cycle-* receipt")
    require(args.cycle in ("hall", "rtc") and args.rtc_seconds in (120, 180), "invalid cycle")
    for key in ("verifier_sha256", "light_sha256", "lid_mode_sha256", "kwin_sha256",
                "powerdevil_sha256", "observer_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", getattr(args, key)) is not None,
                "missing reviewed hash: " + key)


def preflight(args):
    identity_parameters(args)
    require(os.geteuid() == 0, "root required")
    require(not os.path.lexists(args.receipt), "receipt already consumed; choose a new reviewed cycle")
    verifier = load_trusted(HERE / "pocketds-daily-suspend.py", args.verifier_sha256, "cycle_verifier")
    # Explicit, process-local candidate substitution only; never write the daily gate.
    verifier.NOTES = NOTES
    verifier.IMAGE = IMAGES[args.image][0]
    state = verifier.deployment_ready()  # Storage health before persistent image reads.
    require(state["boot_id"] == args.boot_id, "boot differs from explicitly attended boot")
    require(state["counters"] == {"success": args.expected_success, "fail": 0}, "counter drift")
    require(Path("/boot/boot/Image").stat().st_size == IMAGES[args.image][1], "disk image size differs")
    require(not os.path.lexists(verifier.OPT_IN), "daily opt-in must remain absent for this harness")
    require(not os.path.lexists(verifier.BLOCK) and not os.path.lexists(verifier.STATE),
            "failed or unfinished daily sleep transaction")
    root_file(POLICY, POLICY_SHA)
    root_file(GUARD, GUARD_SHA)
    root_file(HERE / "observe-lid-cycle.py", args.observer_sha256)
    mounted = subprocess.run(["/usr/bin/mountpoint", "-q", str(POLICY)]).returncode
    require(mounted == 32, "baseline sleep policy is already mounted or unavailable")
    require(json.loads(run(str(GUARD), "check"))["safely_blocked"], "daily sleep is not safely blocked")
    layout = load_trusted(HERE / "attended-stable-layout-test.py", LAYOUT_SHA, "cycle_layout")
    layout.CONFIG_SHA, layout.HELPER_SHA = args.kwin_sha256, args.light_sha256
    return verifier, layout, state


def candidate(layout, args, closed):
    layout.pinned_user_file(Path("/home/pocketds/.local/libexec/pocketds/pocketds-lid-mode"),
                            args.lid_mode_sha256)
    layout.pinned_user_file(Path("/home/pocketds/.config/powerdevilrc"), args.powerdevil_sha256)
    return layout.candidate_check(closed=closed)


def lid_closed():
    reply = run("/usr/bin/busctl", "--timeout=1s", "get-property", "org.freedesktop.login1",
                "/org/freedesktop/login1", "org.freedesktop.login1.Manager", "LidClosed", timeout=2)
    require(reply in ("b true", "b false"), "unknown lid state")
    return reply == "b true"


def boot_seconds():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def wait_for_close():
    require(not lid_closed(), "start open, then close at the agreed T0")
    end, closed_at = boot_seconds() + 180, None
    while boot_seconds() < end:
        now = boot_seconds()
        if lid_closed():
            closed_at = now if closed_at is None else closed_at
            if now - closed_at >= 3:
                return closed_at
        else:
            closed_at = None
        time.sleep(0.1)
    raise RuntimeError("physical-close window expired; this receipt is consumed")


def read_observations(receipt):
    path = receipt / "observations/events.jsonl"
    data = root_file(path, maximum=16 * 1024 * 1024, minimum=0)
    # A writer may be in its final line while readiness is checked.
    return [json.loads(line) for line in data.splitlines() if line.startswith(b"{") and line.endswith(b"}")]


def start_observer(args):
    output = open(args.receipt / "observer.log", "xb")
    child = subprocess.Popen(["/usr/bin/python3", str(HERE / "observe-lid-cycle.py"),
                              str(args.receipt / "observations"), "--duration", "540"],
                             stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    output.close()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            require(child.poll() is None, "observer failed before arming")
            try:
                records = read_observations(args.receipt)
            except FileNotFoundError:
                records = []
            require(not any(r["event"] in ("native-input-overrun", "native-lid-invalid", "observation-failed")
                            for r in records), "observer lost native evidence before arming")
            if any(r["event"] == "ready" and r["boot_id"] == args.boot_id
                   and r.get("evidence_schema") == "pocketds.lid-observer.v2"
                   and r.get("input_clock") == "CLOCK_BOOTTIME" for r in records) and \
               any(r["event"] == "fast-state" for r in records) and \
               any(r["event"] == "drm-state" and "value" in r.get("state", {}) for r in records) and \
               all(any(r["event"] == event and r.get("code") == 0 for r in records)
                   for event in ("outputs", "dpms")):
                return child
            time.sleep(0.1)
        raise RuntimeError("observer did not become ready")
    except BaseException:
        child.terminate()
        child.wait(timeout=10)
        raise


def stop_observer(child):
    if child is None:
        return
    child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=3)


def cleanup_policy(receipt):
    """Also safe as ExecStopPost; never unmount another transaction's policy."""
    override = receipt / "attended.conf"
    status = subprocess.run(["/usr/bin/mountpoint", "-q", str(POLICY)]).returncode
    if status == 32:
        return
    info = receipt.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
            "untrusted cleanup receipt")
    root_file(override)
    require(status == 0 and override.exists() and os.path.samefile(POLICY, override),
            "policy mount is not this transaction; preserve and report it")
    run("/usr/bin/umount", str(POLICY))
    require(subprocess.run(["/usr/bin/mountpoint", "-q", str(POLICY)]).returncode == 32,
            "temporary policy was not removed")


def clocks():
    return {"boot": boot_seconds(), "awake": time.monotonic()}


def dispatch_identity(verifier, args):
    verifier.storage_health()
    require(verifier.value("/proc/sys/kernel/random/boot_id") == args.boot_id, "boot changed before dispatch")
    require(verifier.counters() == {"success": args.expected_success, "fail": 0}, "cycle drift before dispatch")
    require(not any(os.path.lexists(path) for path in (verifier.OPT_IN, verifier.STATE, verifier.BLOCK)),
            "daily ownership or transaction changed before dispatch")


def wake_source():
    irq = Path("/sys/power/pm_wakeup_irq").read_text().strip()
    require(irq.isdigit(), "missing wake IRQ")
    lines = [line.strip() for line in Path("/proc/interrupts").read_text().splitlines()
             if line.lstrip().startswith(irq + ":")]
    require(len(lines) == 1, "wake IRQ identity unavailable")
    return {"irq": int(irq), "identity": lines[0]}


class EvidenceUncertain(RuntimeError):
    """A sampling/clock boundary cannot establish a physical failure or a pass."""
    assessment_status = "indeterminate"


def evidence_require(condition, message):
    if not condition:
        raise EvidenceUncertain(message)


def evidence_ns(record, key):
    value = record.get(key)
    evidence_require(type(value) is int and value >= 0, "missing/invalid evidence timestamp: " + key)
    return value


def raw_lid_bounds(record):
    evidence_require(record.get("input_clock") == "CLOCK_BOOTTIME",
                     "native event clock is not verified BOOTTIME; legacy receipt is unchanged")
    stamp = evidence_ns(record, "input_boottime_ns")
    sec, usec = record.get("input_sec"), record.get("input_usec")
    evidence_require(type(sec) is int and sec >= 0 and type(usec) is int and 0 <= usec < 1_000_000
                     and stamp == sec * 1_000_000_000 + usec * 1000,
                     "native event timestamp does not match its original timeval")
    evidence_require(stamp <= evidence_ns(record, "boottime_ns"), "native event arrived before its timestamp")
    # input_event timeval is truncated to microseconds, so retain its interval.
    return stamp, stamp + 999


def sample_window(record):
    start = evidence_ns(record, "sample_started_boottime_ns")
    end = evidence_ns(record, "sample_finished_boottime_ns")
    mono_start = evidence_ns(record, "sample_started_monotonic_ns")
    mono_end = evidence_ns(record, "sample_finished_monotonic_ns")
    evidence_require(start <= end <= evidence_ns(record, "boottime_ns") and mono_start <= mono_end,
                     "sample window is reversed or later than receipt")
    return start, end, start - mono_start, end - mono_end


def closed_rtc_samples(records, prepare, opened):
    tolerance = 5_000_000  # Only syscall/read scheduling tolerance, never a deleted tail.
    def gap(record):
        return evidence_ns(record, "boottime_ns") - evidence_ns(record, "monotonic_ns")
    before_gap, after_gap = map(gap, prepare)
    evidence_require(after_gap - before_gap >= 1_000_000_000,
                     "no unambiguous suspended BOOTTIME/MONOTONIC interval")
    # The observer can run after thaw BEFORE logind delivers PrepareForSleep(false).
    # Find that clock epoch in every record, including the first fast sample.
    post_records = [r for r in records
                    if evidence_ns(r, "boottime_ns") >= evidence_ns(prepare[0], "boottime_ns")
                    and abs(gap(r) - after_gap) <= tolerance]
    evidence_require(post_records, "no post-thaw observations")
    start = min(evidence_ns(r, "boottime_ns") for r in post_records)
    open_lo, open_hi = raw_lid_bounds(opened)
    evidence_require(open_lo - start >= 20_000_000_000,
                     "lid reopened before closed-return observation completed")
    samples, boundary_samples = [], []
    for record in sorted((r for r in records if r["event"] == "fast-state"),
                         key=lambda r: evidence_ns(r, "sample_finished_boottime_ns")):
        beginning, end, begin_gap, end_gap = sample_window(record)
        if end < start and abs(end_gap - after_gap) > tolerance:
            continue
        evidence_require(abs(end_gap - after_gap) <= tolerance,
                         "another or uncertain sleep clock epoch in observations")
        if beginning >= open_hi:
            continue  # Proven post-opening, irrespective of DBus/evdev delivery order.
        evidence_require(abs(begin_gap - after_gap) <= tolerance,
                         "sample straddles suspend/thaw; closed-return state is indeterminate")
        dark = record.get("backlights_dark_proxy")
        if end >= open_lo:
            boundary_samples.append({"started_boottime_ns": beginning, "finished_boottime_ns": end,
                                     "classification": "straddles-physical-open", "backlights_dark_proxy": dark})
            evidence_require(dark is True,
                             "nonblack/unknown sample straddles physical opening; no hardware verdict")
            continue
        evidence_require(type(dark) is bool, "closed backlight readback is unavailable")
        require(dark, "closed RTC return was not continuously blank before physical opening")
        samples.append(record)
    evidence_require(len(samples) >= 100, "too few fully closed post-thaw samples")
    evidence_require(evidence_ns(samples[0], "sample_started_boottime_ns") - start < 500_000_000
                     and open_lo - evidence_ns(samples[-1], "sample_finished_boottime_ns") < 500_000_000,
                     "closed-return boundary observation missing")
    evidence_require(all(evidence_ns(b, "sample_started_boottime_ns")
                         - evidence_ns(a, "sample_finished_boottime_ns") < 2_000_000_000
                         for a, b in zip(samples, samples[1:])), "closed-return observation has a gap")
    return {"first_post_thaw_boottime_ns": start,
            "physical_open_bounds_boottime_ns": [open_lo, open_hi],
            "fully_closed_samples": len(samples), "boundary_samples": boundary_samples}


def assess(cycle, records, wake):
    """Evidence classification only: user display/touch acceptance stays pending."""
    require(not any(r["event"] in ("native-input-overrun", "native-lid-invalid", "observation-failed")
                    for r in records), "incomplete native observation")
    evidence_require(any(r["event"] == "ready" and r.get("evidence_schema") == "pocketds.lid-observer.v2"
                         and r.get("input_clock") == "CLOCK_BOOTTIME" for r in records),
                     "observer timestamp schema is not verified; legacy receipt is unchanged")
    prepare = [r for r in records if r["event"] == "prepare-for-sleep"]
    require([r["active"] for r in prepare] == [True, False], "not one observed true/false lifecycle")
    closed = [r for r in records if r["event"] == "native-lid" and r["closed"] is True]
    opened = [r for r in records if r["event"] == "native-lid" and r["closed"] is False]
    require(len(closed) == len(opened) == 1, "missing or repeated physical lid close/open evidence")
    close_lo, close_hi = raw_lid_bounds(closed[0])
    open_lo, open_hi = raw_lid_bounds(opened[0])
    require(close_hi < evidence_ns(prepare[0], "boottime_ns") < open_lo, "not closed before sleep")
    elapsed = (open_lo - close_lo) / 1e9
    audit = {}
    if cycle == "hall":
        require("Lid Switch" in wake["identity"], "Hall not exercised: another wake source returned first")
        require(25 <= elapsed <= 35, "Hall opening did not follow the agreed 30-second schedule")
    else:
        require("rtc_alarm" in wake["identity"], "closed-RTC condition not exercised")
        audit = closed_rtc_samples(records, prepare, opened[0])
    return {"wake_condition": "observed", "physical_open_after_close_seconds": round(elapsed, 3),
            "closed_black": "observed" if cycle == "rtc" else "not exercised",
            "physical_display_acceptance": "pending", "physical_touch_acceptance": "pending",
            "timing_evidence": {"input_clock": "CLOCK_BOOTTIME", **audit}}


def open_display_check(layout):
    states = sorted(line.strip() for line in layout.screen_query("--dpms", "show").splitlines())
    require(states == ["dpms mode for screen DSI-1: on", "dpms mode for screen DSI-2: on"],
            "both DPMS states did not recover on opening")
    lights = {name: (layout.BACKLIGHT / name / "bl_power").read_text().strip()
              for name in ("ae94000.dsi.0", "sy7758-backlight")}
    require(all(value == "0" for value in lights.values()), "a backlight remains blank after opening")
    return {"dpms": states, "bl_power": lights}


def perform(args):
    verifier, layout, state = preflight(args)
    initial = candidate(layout, args, False)
    if args.command == "check":
        return {"preflight": state, "candidate": initial, "lid_closed": lid_closed()}
    require(not lid_closed(), "start this attended test with lid open")
    os.umask(0o077)
    args.receipt.mkdir(mode=0o700)  # O_EXCL semantics: refusal also consumes receipt.
    result, observer = {"kernel_cycle": "incomplete"}, None
    def interrupted(_signum, _frame):
        raise RuntimeError("attended test interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        (args.receipt / "parameters.json").write_text(json.dumps(vars(args), default=str))
        observer = start_observer(args)
        print(json.dumps({"event": "armed-waiting-for-close", "physical_open_after_close_seconds":
                          30 if args.cycle == "hall" else args.rtc_seconds + 60}), flush=True)
        closed_at = wait_for_close()
        candidate(layout, args, True)
        policy = root_file(POLICY, POLICY_SHA)
        require(policy.count(b"AllowSuspend=no") == 1, "unexpected sleep policy")
        (args.receipt / "policy-before.conf").write_bytes(policy)
        override = args.receipt / "attended.conf"
        override.write_bytes(policy.replace(b"AllowSuspend=no", b"AllowSuspend=yes"))
        override.chmod(0o644)
        run("/usr/bin/mount", "--bind", str(override), str(POLICY))
        require(json.loads(run(str(GUARD), "check"))["execution_ready"], "temporary guard not ready")
        dispatch_candidate = candidate(layout, args, True)
        dispatch_identity(verifier, args)
        require(observer.poll() is None, "observer died before dispatch")
        require(lid_closed() and boot_seconds() - closed_at <= 15,
                "lid reopened or dispatch missed the 15-second bound; no late suspend")
        before = clocks()
        (args.receipt / "before.json").write_text(json.dumps(
            {"state": state, "clocks": before, "closed_at": closed_at, "candidate": dispatch_candidate}))
        with open(args.receipt / "guard.jsonl", "xb") as log:
            completed = subprocess.run([str(GUARD), "run", str(args.rtc_seconds)],
                                      stdout=log, stderr=subprocess.STDOUT, timeout=args.rtc_seconds + 55)
        after = clocks()
        verifier.storage_health()  # Do not read persistent configs after a storage failure.
        require(completed.returncode == 0, "root guard did not complete its lifecycle")
        require(verifier.value("/proc/sys/kernel/random/boot_id") == args.boot_id, "boot changed")
        require(verifier.counters() == {"success": args.expected_success + 1, "fail": 0}, "counter drift")
        require(not verifier.value("/sys/class/rtc/rtc0/wakealarm"), "RTC alarm remains")
        wake = wake_source()
        result = {"kernel_cycle": "returned", "wake": wake, "counters": verifier.counters(),
                  "suspended_seconds": round((after["boot"] - before["boot"]) -
                                             (after["awake"] - before["awake"]), 3),
                  "physical_display_acceptance": "pending"}
        cleanup_policy(args.receipt)  # Disable further sleep before post-return observation.
        require(json.loads(run(str(GUARD), "check"))["safely_blocked"], "baseline policy not restored")
        # Bounded local observation survives SSH loss. RTC user opens at RTC+60 s.
        until = boot_seconds() + (35 if args.cycle == "hall" else 95)
        while boot_seconds() < until:
            require(observer.poll() is None, "observer died during post-return evidence")
            time.sleep(0.2)
        result.update(assess(args.cycle, read_observations(args.receipt), wake))
        require(not lid_closed(), "physical lid remains closed at final readback")
        verifier.storage_health()
        result["post_open_candidate"] = candidate(layout, args, False)
        result["post_open_display"] = open_display_check(layout)
        return result
    except BaseException as error:
        result["error"] = str(error)[:240]
        if getattr(error, "assessment_status", None) == "indeterminate":
            result.update(assessment_status="indeterminate", closed_black="indeterminate")
        raise
    finally:
        try:
            cleanup_policy(args.receipt)
        except Exception as error:
            result["policy_cleanup_error"] = str(error)[:240]
            raise
        finally:
            stop_observer(observer)
            (args.receipt / "result.json").write_text(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--receipt", type=Path, required=True)
    for name in ("check", "run"):
        command = sub.add_parser(name)
        command.add_argument("--boot-id", required=True)
        command.add_argument("--expected-success", required=True, type=int)
        command.add_argument("--receipt", type=Path, required=True)
        command.add_argument("--image", choices=IMAGES, required=True)
        command.add_argument("--cycle", choices=("hall", "rtc"), required=True)
        command.add_argument("--rtc-seconds", type=int, choices=(120, 180), default=120)
        for name in ("verifier", "light", "lid-mode", "kwin", "powerdevil", "observer"):
            command.add_argument("--" + name + "-sha256", required=True)
    args = parser.parse_args()
    if args.command == "cleanup":
        require(os.geteuid() == 0 and args.receipt.parent == Path("/run") and
                re.fullmatch(r"pds-lid-cycle-[a-z0-9][a-z0-9-]{0,63}", args.receipt.name),
                "untrusted cleanup receipt")
        cleanup_policy(args.receipt)
    else:
        print(json.dumps(perform(args)), flush=True)


if __name__ == "__main__":
    main()
