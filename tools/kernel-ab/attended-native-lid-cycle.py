#!/usr/bin/python3
"""Observe one native KDE lid suspend with a temporary 300-second RTC rescue.

No sleep request, persistent policy change, lid setting write or display operation
occurs here. An incomplete supervision after attempting RTC arm blocks further
daily sleep in /run before exiting. Closed/unknown returns block before clearing
owned RTC rescue. Daily sleep and LidAction=sleep must already be configured.
Wait for ready while open, close within 180 seconds, open 30 seconds after
closure. Each run consumes a new receipt. Daily operation does not use this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import time
import types
import uuid

HERE = Path(__file__).resolve().parent
DAILY = Path("/usr/local/libexec/pocketds-daily-suspend")
RTC_SECONDS = 300  # Existing guard maximum; never extend its accepted range.
CLOSE_WINDOW_SECONDS = 180
OPEN_AFTER_CLOSE_SECONDS = 30
PREPARE_AFTER_CLOSE_SECONDS = 15
MINIMUM_RESCUE_MARGIN_SECONDS = 60


class NativeSession:
    """Passive use of the existing guard's RTC, inhibitor and lifecycle APIs."""
    def __init__(self, guard, adapter, emit, lid_closed, clock=None, block_return=None):
        self.guard, self.adapter, self.emit, self.lid_closed = guard, adapter, emit, lid_closed
        self.block_return = block_return
        self.clock = clock or (lambda: time.clock_gettime(time.CLOCK_BOOTTIME))
        self.lifecycle = guard.Lifecycle()
        self.loop = guard.GLib.MainLoop()
        self.inhibitor = -1
        self.subscription = self.timer = 0
        self.alarm = None
        self.arm_attempted = self.block_written = False
        self.armed_at = None
        self.closed_at = None
        self.error = ""
        self.result = {"native_lifecycle": "incomplete", "rtc_cleanup": "not-armed"}
        self.prepare_values = []

    def release_inhibitor(self):
        if self.inhibitor >= 0:
            os.close(self.inhibitor)
            self.inhibitor = -1

    def block_daily(self, reason):
        if self.block_written:
            return
        if self.block_return is None:
            raise RuntimeError("no runtime blocker for incomplete native supervision; preserve RTC")
        try:
            self.block_return()
        except BaseException as error:
            self.result["daily_block"] = "write-failed"
            self.result["daily_block_error"] = str(error)[:240]
            raise
        self.block_written = True
        self.result["daily_block"] = ("written-before-rtc-cleanup" if reason == "closed-return"
                                      else "written-before-supervision-exit")
        self.emit("native-supervision-blocked", reason=reason)

    def prepared(self, _bus, _sender, _path, _interface, _signal, parameters):
        try:
            values = parameters.unpack()
            if len(values) != 1 or type(values[0]) is not bool:
                self.error = "invalid native PrepareForSleep signal"
                return
            active = values[0]
            self.prepare_values.append(active)
            self.emit("native-prepare-for-sleep", active=active, boottime=self.clock())
            if active and not self.lifecycle.request_sent:
                # This mark records an OBSERVED native request. No request API
                # is called by this tool; KDE/logind owns dispatch and inhibitors.
                self.lifecycle.mark_request_sent()
                try:
                    if not self.lid_closed():
                        self.error = "native sleep started with lid open"
                    else:
                        now = self.clock()
                        if self.closed_at is None:
                            self.closed_at = now
                        if (self.closed_at - self.armed_at > CLOSE_WINDOW_SECONDS or
                                now - self.closed_at > PREPARE_AFTER_CLOSE_SECONDS):
                            self.error = "native prepare missed the physical-close window"
                except Exception:
                    self.error = "lid state unknown at native prepare"
            action = self.lifecycle.prepare_for_sleep(active)
            if action == "release-delay-inhibitor":
                if self.error and self.arm_attempted:
                    # A late/open/unknown prepare must not pass the systemd
                    # daily precheck while waiting for the next GLib tick.
                    self.block_daily("invalid-native-prepare")
                self.release_inhibitor()
            elif action == "cleanup-rtc":
                if self.prepare_values != [True, False]:
                    raise RuntimeError("not exactly one native true/false pair; preserve RTC")
                if self.error:
                    self.block_daily("return-after-supervision-error")
                    self.result["rtc_cleanup"] = "preserved-after-supervision-error"
                    self.loop.quit()
                    return
                try:
                    open_confirmed = self.lid_closed() is False
                except Exception:
                    open_confirmed = False
                if not open_confirmed:
                    # PowerDevil may retry a closed Unknown wake after 10 s.
                    # Block that next systemd precheck BEFORE removing rescue.
                    self.block_daily("closed-return")
                    self.error = "native resume remained closed or lid was unknown; daily sleep blocked"
                    self.emit("native-closed-return-blocked")
                self.clear_owned_alarm()
                self.result["native_lifecycle"] = "returned" if open_confirmed else "returned-but-blocked"
                self.loop.quit()
        except Exception as error:
            self.error = str(error)[:240]
            self.result["rtc_cleanup"] = "preserved-on-error"
            self.loop.quit()

    def clear_owned_alarm(self):
        current = self.adapter.alarm()
        if current is None:
            self.result["rtc_cleanup"] = "already-empty-after-resume"
            return
        if self.alarm is None or current != self.alarm:
            raise RuntimeError("RTC ownership changed; preserve foreign alarm")
        if not self.adapter.clear_rtc():
            raise RuntimeError("owned RTC alarm did not clear")
        self.result["rtc_cleanup"] = "cleared-after-native-true-false"
        self.emit("native-resume-rtc-cleanup", alarm=self.alarm)

    def tick(self):
        now = self.clock()
        elapsed = now - self.armed_at
        if not self.lifecycle.saw_prepare:
            try:
                closed = self.lid_closed()
                if closed and self.closed_at is None:
                    self.closed_at = now
                elif not closed and self.closed_at is not None:
                    self.error = "lid reopened before native prepare"
            except Exception:
                self.error = "lid state unavailable while awaiting native prepare"
        late_close = (not self.lifecycle.saw_prepare and
                      (self.closed_at is None and elapsed > CLOSE_WINDOW_SECONDS or
                       self.closed_at is not None and
                       (self.closed_at - self.armed_at > CLOSE_WINDOW_SECONDS or
                        now - self.closed_at > PREPARE_AFTER_CLOSE_SECONDS)))
        if self.error or elapsed < 0 or elapsed > RTC_SECONDS + 35 or late_close:
            self.error = self.error or ("native close/prepare window expired" if not self.lifecycle.saw_prepare
                                        else "native lifecycle timed out")
            self.result["rtc_cleanup"] = "preserved-without-complete-lifecycle"
            self.loop.quit()
            return self.guard.GLib.SOURCE_REMOVE
        return self.guard.GLib.SOURCE_CONTINUE

    def run(self):
        completed = False
        try:
            if not self.guard.MIN_RTC_SECONDS <= RTC_SECONDS <= self.guard.MAX_RTC_SECONDS:
                raise RuntimeError("native rescue is outside the unchanged guard range")
            self.inhibitor = self.adapter.take_delay_inhibitor()
            self.subscription = self.adapter.subscribe(self.prepared)
            if self.adapter.preparing_for_sleep() or self.lid_closed():
                raise RuntimeError("must arm while open and not preparing for sleep")
            if self.adapter.alarm() is not None:
                raise RuntimeError("an RTC alarm already exists")
            self.result["rtc_cleanup"] = "preserved-until-native-true-false"
            # arm_rtc can write successfully and then fail its readback. That
            # ambiguous ownership must still block a future unobserved sleep.
            self.arm_attempted = True
            self.alarm = self.adapter.arm_rtc(RTC_SECONDS)
            # A closure during arm is refused; retain rescue for a potentially
            # pending KDE request instead of racing to clear it.
            if self.adapter.preparing_for_sleep() or self.lid_closed():
                raise RuntimeError("lid changed before native test became ready")
            remaining = self.alarm - self.adapter.rtc_epoch()
            if remaining < (CLOSE_WINDOW_SECONDS + OPEN_AFTER_CLOSE_SECONDS +
                            MINIMUM_RESCUE_MARGIN_SECONDS):
                raise RuntimeError("RTC rescue margin is too short; test never became ready")
            self.armed_at = self.clock()
            self.result.update(armed_boottime=self.armed_at, rtc_seconds=RTC_SECONDS,
                               rtc_remaining_at_ready_seconds=remaining)
            self.emit("armed-waiting-for-native-close", rtc_seconds=RTC_SECONDS,
                      close_within_seconds=CLOSE_WINDOW_SECONDS,
                      open_after_close_seconds=OPEN_AFTER_CLOSE_SECONDS,
                      rtc_remaining_seconds=remaining, alarm=self.alarm)
            self.timer = self.guard.GLib.timeout_add(250, self.tick)
            self.loop.run()
            self.result["prepare_values"] = self.prepare_values
            if self.error:
                raise RuntimeError(self.error)
            if not self.lifecycle.saw_resume or self.prepare_values != [True, False]:
                raise RuntimeError("native sleep did not complete exactly once")
            completed = True
            return self.result
        except BaseException as error:
            self.result["supervision_error"] = str(error)[:240]
            raise
        finally:
            # No blind finally RTC clear: a native request could still be in flight.
            try:
                if self.arm_attempted and not completed:
                    self.block_daily("incomplete-native-supervision")
            finally:
                try:
                    try:
                        if self.timer:
                            self.guard.GLib.source_remove(self.timer)
                        if self.subscription:
                            self.adapter.unsubscribe(self.subscription)
                    except BaseException as error:
                        self.result["supervision_error"] = str(error)[:240]
                        if self.arm_attempted:
                            self.block_daily("supervision-cleanup-error")
                        raise
                finally:
                    self.release_inhibitor()


def load_common(expected):
    path = HERE / "attended-lid-cycle.py"
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & 0o022 or not 0 < info.st_size <= 262144):
            raise RuntimeError("untrusted shared attended harness")
        data = stream.read(262145)
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("shared attended harness differs from reviewed hash")
    module = types.ModuleType("native_lid_common")
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)
    return module


def native_mode(common, daily):
    # A root supervisor/runuser process is not the active local desktop actor.
    # Query settings without asking polkit about that helper's own session.
    uid = pwd.getpwnam("pocketds").pw_uid
    prefix = ("/usr/sbin/runuser", "-u", "pocketds", "--", "/usr/bin/env",
              "HOME=/home/pocketds", f"XDG_RUNTIME_DIR=/run/user/{uid}")
    state = json.loads(common.run(*prefix,
        "/home/pocketds/.local/libexec/pocketds/pocketds-lid-mode", "owner", timeout=3))
    common.require(isinstance(state, dict) and state.get("mode") == "sleep",
                   "native lid action is not configured for sleep")
    for profile in ("AC", "Battery", "LowBattery"):
        mode = common.run(*prefix, "/usr/bin/kreadconfig6", "--file", "/home/pocketds/.config/powerdevilrc",
            "--group", profile, "--group", "SuspendAndShutdown", "--key", "SleepMode", "--default", "missing",
            timeout=2)
        common.require(mode == "1", "native SleepMode must be suspend to RAM in every profile")
    common.require(common.lid_closed() is False, "native test requires an open lid")

    # This verifier checks the unique bus owner, process UID and exact mapped
    # plugin. Bind polkit to that same process lifetime, not an unverified PID.
    actor = daily.powerdevil_resume_ready()
    started = daily.process_start(actor["pid"])
    common.run("/usr/bin/pkcheck", "--action-id", "org.freedesktop.login1.suspend",
               "--process", f"{actor['pid']},{started},{uid}", timeout=4)
    capability = json.loads(common.run(*prefix, "/usr/bin/busctl",
        f"--address=unix:path=/run/user/{uid}/bus", "--auto-start=no", "--timeout=1s", "--json=short",
        "call", actor["owner"], "/org/freedesktop/PowerManagement",
        "org.freedesktop.PowerManagement", "CanSuspend", timeout=2))
    common.require(isinstance(capability, dict) and capability.get("type") == "b"
                   and isinstance(capability.get("data"), list) and len(capability["data"]) == 1
                   and capability["data"][0] is True, "PowerDevil has not enabled its native suspend capability")
    common.require(daily.powerdevil_resume_ready() == actor and daily.process_start(actor["pid"]) == started,
                   "PowerDevil actor changed during native authorization")
    common.require(common.lid_closed() is False, "lid closed during native authorization")
    return {**actor, "start_time_ticks": started, "uid": uid, "suspend_authorized": True,
            "can_suspend": True}


def identity_parameters(common, args):
    # The common CLI accepts only its actively dispatched 120/180-second tests.
    # Native timing is fixed here; do not disguise a 300-second rescue as 120.
    common.require(str(uuid.UUID(args.boot_id)) == args.boot_id, "use exact canonical boot UUID")
    common.require(type(args.expected_success) is int and 0 <= args.expected_success <= 1000,
                   "invalid expected cycle count")
    common.require(args.receipt.parent == Path("/run") and
                   re.fullmatch(r"pds-lid-cycle-[a-z0-9][a-z0-9-]{0,63}", args.receipt.name),
                   "use a new /run/pds-lid-cycle-* receipt")
    for key in ("common", "verifier", "light", "lid_mode", "kwin", "powerdevil", "observer"):
        common.require(re.fullmatch(r"[0-9a-f]{64}", getattr(args, key + "_sha256")) is not None,
                       "missing reviewed hash: " + key)


def preflight(common, args):
    identity_parameters(common, args)
    common.require(os.geteuid() == 0 and not os.path.lexists(args.receipt), "root and a fresh receipt required")
    daily = common.load_trusted(DAILY, args.verifier_sha256, "native_daily")
    common.require(daily.NOTES == common.NOTES and daily.IMAGE == common.IMAGES["v4"][0],
                   "installed daily verifier is not the reviewed v4 gate")
    daily.eligible()
    state = daily.deployment_ready()
    daily.effective_policy()
    common.require(state["boot_id"] == args.boot_id and
                   state["counters"] == {"success": args.expected_success, "fail": 0}, "boot/count drift")
    common.require(not os.path.lexists(daily.STATE), "unfinished native sleep transaction")
    common.root_file(HERE / "observe-lid-cycle.py", args.observer_sha256)
    guard = common.load_trusted(common.GUARD, common.GUARD_SHA, "native_rtc_guard")
    adapter = guard.LiveAdapter()
    common.require(guard.collect_preflight(adapter)["execution_ready"], "native sleep/RTC policy is not ready")
    layout = common.load_trusted(HERE / "attended-stable-layout-test.py", common.LAYOUT_SHA, "native_layout")
    layout.CONFIG_SHA, layout.HELPER_SHA = args.kwin_sha256, args.light_sha256
    common.candidate(layout, args, False)
    state["native_actor"] = native_mode(common, daily)
    return daily, guard, adapter, layout, state


def assess_native(common, records, wake, armed_at):
    result = common.assess("hall", records, wake)
    closed = next(common.raw_lid_bounds(record)[0] / 1e9 for record in records
                  if record["event"] == "native-lid" and record["closed"] is True)
    preparing = next(record["boottime_ns"] / 1e9 for record in records
                     if record["event"] == "prepare-for-sleep" and record["active"] is True)
    common.require(0 <= closed - armed_at <= CLOSE_WINDOW_SECONDS,
                   "physical close was outside the native readiness window")
    common.require(0 <= preparing - closed <= PREPARE_AFTER_CLOSE_SECONDS,
                   "native prepare was late after physical close")
    return result


def block_native_return(daily, boot_id, receipt):
    # Only volatile state; the installed daily precheck enforces this latch.
    daily.save_runtime(daily.BLOCK, {"boot_id": boot_id,
        "reason": "attended native supervision did not finish with a verified open return", "receipt": str(receipt)})
    if not os.path.lexists(daily.BLOCK):
        raise RuntimeError("native return blocker did not persist")


def perform(common, args):
    daily, guard, adapter, layout, state = preflight(common, args)
    if args.command == "check":
        return {"native_preflight": state, "write_executed": False}
    os.umask(0o077)
    args.receipt.mkdir(mode=0o700)
    observer, session = None, None
    result = {"kernel_cycle": "incomplete", "physical_display_acceptance": "pending"}
    with open(args.receipt / "native-supervisor.jsonl", "x", buffering=1) as log:
        def emit(event, **fields):
            record = {"event": event, "clocks": common.clocks(), **fields}
            log.write(json.dumps(record) + "\n")
            print(json.dumps(record), flush=True)
        def interrupted(_signum, _frame):
            raise RuntimeError("native attended observation interrupted")
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        lock = None
        try:
            lock = guard.acquire_lock()
            observer = common.start_observer(args)
            # All persistent gates are rechecked before arm, never after storage failure.
            daily.eligible()
            current = daily.deployment_ready()
            daily.effective_policy()
            common.require(current["boot_id"] == args.boot_id and current["counters"] == state["counters"],
                           "native identity changed before arm")
            common.require(not os.path.lexists(daily.STATE), "native sleep transaction started before arm")
            common.candidate(layout, args, False)
            current["native_actor"] = native_mode(common, daily)
            before = common.clocks()
            (args.receipt / "before.json").write_text(json.dumps({"state": current, "clocks": before,
                "parameters": {**vars(args), "receipt": str(args.receipt), "rtc_seconds": RTC_SECONDS}}))
            session = NativeSession(guard, adapter, emit, common.lid_closed,
                block_return=lambda: block_native_return(daily, args.boot_id, args.receipt))
            result["supervision"] = session.run()
            after = common.clocks()
            daily.storage_health()
            common.require(daily.value("/proc/sys/kernel/random/boot_id") == args.boot_id and
                           daily.counters() == {"success": args.expected_success + 1, "fail": 0},
                           "native cycle boot/count mismatch")
            wake = common.wake_source()
            result.update(kernel_cycle="returned", wake=wake, counters=daily.counters(),
                suspended_seconds=round((after["boot"]-before["boot"]) - (after["awake"]-before["awake"]), 3))
            until = common.boot_seconds() + 35
            while common.boot_seconds() < until:
                common.require(observer.poll() is None, "native observer stopped unexpectedly")
                time.sleep(0.2)
            result.update(assess_native(common, common.read_observations(args.receipt), wake, session.armed_at))
            daily.storage_health()
            common.require(daily.counters() == {"success": args.expected_success + 1, "fail": 0},
                           "another native cycle occurred during observation")
            common.require(not os.path.lexists(daily.STATE) and not os.path.lexists(daily.BLOCK),
                           "daily guard did not finish cleanly")
            common.require(adapter.alarm() is None and not common.lid_closed(), "RTC or lid final state is wrong")
            result["post_open_candidate"] = common.candidate(layout, args, False)
            result["post_open_display"] = common.open_display_check(layout)
            return result
        except BaseException as error:
            result["error"] = str(error)[:240]
            if getattr(error, "assessment_status", None) == "indeterminate":
                result.update(assessment_status="indeterminate", closed_black="indeterminate")
            if session is not None:
                result["supervision"] = session.result
            raise
        finally:
            common.stop_observer(observer)
            if lock is not None:
                lock.close()
            (args.receipt / "result.json").write_text(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "run"))
    parser.add_argument("--boot-id", required=True)
    parser.add_argument("--expected-success", type=int, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    for name in ("common", "verifier", "light", "lid-mode", "kwin", "powerdevil", "observer"):
        parser.add_argument("--" + name + "-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(perform(load_common(args.common_sha256), args)), flush=True)


if __name__ == "__main__":
    main()
