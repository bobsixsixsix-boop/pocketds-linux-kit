#!/usr/bin/env python3
"""Offline identity, lifecycle, observation and classification checks; never sleeps."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/kernel-ab" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app = load("attended_new", "attended-lid-cycle.py")
observer = load("lid_observer", "observe-lid-cycle.py")


def arguments():
    return SimpleNamespace(command="run", boot_id="72a9286e-7eae-46cf-966a-6afc7d2102be",
        expected_success=3, image="v4", cycle="hall", rtc_seconds=120,
        receipt=Path("/run/pds-lid-cycle-test-new"),
        **{key + "_sha256": "a" * 64 for key in
           ("verifier", "light", "lid_mode", "kwin", "powerdevil", "observer")})


def records(cycle="hall"):
    resumed, opened = (30, 30) if cycle == "hall" else (120, 180)
    def record(event, seconds, **fields):
        stamp = round(seconds * 1e9)
        gap = (resumed - 10) * 1_000_000_000 if seconds >= resumed else 0
        return {"event": event, "boottime_ns": stamp, "monotonic_ns": stamp - gap,
                "unix_ns": stamp + 1_700_000_000_000_000_000, **fields}
    def native(closed, physical, received):
        stamp = round(physical * 1e9)
        return record("native-lid", received, closed=closed, input_clock="CLOCK_BOOTTIME",
                      input_boottime_ns=stamp, input_sec=stamp // 1_000_000_000,
                      input_usec=stamp % 1_000_000_000 // 1000)
    events = [
        record("ready", 0, evidence_schema="pocketds.lid-observer.v2", input_clock="CLOCK_BOOTTIME"),
        native(True, 0, 0.05),
        record("prepare-for-sleep", 5, active=True),
        record("prepare-for-sleep", resumed + 0.02, active=False),
    ]
    if cycle == "rtc":
        for index in range(resumed * 10, opened * 10):
            start, end = record("start", index / 10), record("end", index / 10 + 0.001)
            events.append(record("fast-state", index / 10 + 0.001,
                sample_started_boottime_ns=start["boottime_ns"], sample_finished_boottime_ns=end["boottime_ns"],
                sample_started_monotonic_ns=start["monotonic_ns"], sample_finished_monotonic_ns=end["monotonic_ns"],
                backlights_dark_proxy=True, physical_dark_proxy=True))
    events.append(native(False, opened, opened + 0.1))
    return events


def rtc_sample(start, end, dark=True, *, start_gap=110, end_gap=110):
    """A scripted physical read window, independent of DBus/input delivery."""
    ns = lambda seconds: round(seconds * 1e9)
    return {"event": "fast-state", "boottime_ns": ns(end), "monotonic_ns": ns(end - end_gap),
            "sample_started_boottime_ns": ns(start), "sample_finished_boottime_ns": ns(end),
            "sample_started_monotonic_ns": ns(start - start_gap),
            "sample_finished_monotonic_ns": ns(end - end_gap),
            "backlights_dark_proxy": dark, "physical_dark_proxy": dark}


class ParameterTests(unittest.TestCase):
    def test_observer_readiness_requires_new_clock_schema_and_no_event_loss(self):
        good = [{"event": "ready", "boot_id": arguments().boot_id,
                 "evidence_schema": "pocketds.lid-observer.v2", "input_clock": "CLOCK_BOOTTIME"},
                {"event": "fast-state"}, {"event": "drm-state", "state": {"value": "fixture"}},
                {"event": "outputs", "code": 0}, {"event": "dpms", "code": 0}]
        for case in ("ready", "legacy", "clock-unverified", "overrun"):
            sample = [dict(row) for row in good]
            if case == "legacy":
                del sample[0]["evidence_schema"]
            elif case == "clock-unverified":
                del sample[0]["input_clock"]
            elif case == "overrun":
                sample.append({"event": "native-input-overrun"})
            child = Mock()
            child.poll.return_value = None
            with self.subTest(case=case), patch("builtins.open", return_value=Mock()), \
                 patch.object(app.subprocess, "Popen", return_value=child), \
                 patch.object(app, "read_observations", return_value=sample), \
                 patch.object(app.time, "monotonic", side_effect=[0, 0, 16]), \
                 patch.object(app.time, "sleep"):
                if case == "ready":
                    self.assertIs(app.start_observer(arguments()), child)
                    child.terminate.assert_not_called()
                else:
                    with self.assertRaises(RuntimeError):
                        app.start_observer(arguments())
                    child.terminate.assert_called_once()

    def test_exact_boot_count_known_images_and_reviewed_hashes(self):
        for image in ("v3", "v4"):
            args = arguments()
            args.image = image
            app.identity_parameters(args)
        for key, value in (("boot_id", "wrong"), ("boot_id", "72A9286E-7EAE-46CF-966A-6AFC7D2102BE"),
                           ("expected_success", True), ("expected_success", -1),
                           ("image", "raw-v4"), ("rtc_seconds", 60),
                           ("cycle", "loop"), ("light_sha256", "a" * 63),
                           ("receipt", Path("/tmp/pds-lid-cycle-test")),
                           ("receipt", Path("/run/pds-lid-cycle-test/child"))):
            args = arguments()
            setattr(args, key, value)
            with self.subTest(key=key, value=value), self.assertRaises((RuntimeError, ValueError)):
                app.identity_parameters(args)

    def test_private_verifier_requires_v4_runtime_even_with_v3_disk(self):
        for image in ("v3", "v4"):
            args, verifier, layout = arguments(), Mock(), Mock()
            args.image = image
            verifier.deployment_ready.return_value = {
                "boot_id": args.boot_id, "counters": {"success": 3, "fail": 0}}
            with patch.object(app.os, "geteuid", return_value=0), \
                 patch.object(app.os.path, "lexists", return_value=False), \
                 patch.object(app, "load_trusted", side_effect=[verifier, layout]), \
                 patch.object(app, "root_file"), \
                 patch.object(app.Path, "stat", return_value=SimpleNamespace(st_size=app.IMAGES[image][1])), \
                 patch.object(app.subprocess, "run", return_value=SimpleNamespace(returncode=32)), \
                 patch.object(app, "run", return_value='{"safely_blocked":true}'):
                app.preflight(args)
            self.assertEqual(verifier.NOTES, app.NOTES)
            self.assertEqual(verifier.IMAGE, app.IMAGES[image][0])
            verifier.deployment_ready.assert_called_once()

    def test_consumed_receipt_refuses_before_import_or_hardware_probe(self):
        with patch.object(app.os, "geteuid", return_value=0), \
             patch.object(app.os.path, "lexists", return_value=True), \
             patch.object(app, "load_trusted") as importer:
            with self.assertRaisesRegex(RuntimeError, "already consumed"):
                app.preflight(arguments())
        importer.assert_not_called()


class EvidenceTests(unittest.TestCase):
    def test_hall_success_remains_pending_physical_acceptance(self):
        result = app.assess("hall", records(), {"identity": "202: Lid Switch"})
        self.assertEqual(result["physical_open_after_close_seconds"], 30)
        self.assertEqual(result["physical_display_acceptance"], "pending")
        self.assertEqual(result["closed_black"], "not exercised")

    def test_rtc_first_is_never_accepted_as_hall(self):
        with self.assertRaisesRegex(RuntimeError, "Hall not exercised"):
            app.assess("hall", records(), {"identity": "166: pm8xxx_rtc_alarm"})

    def test_wrong_hall_schedule_or_incomplete_lifecycle_refuses(self):
        for change in ("late-open", "extra-prepare", "no-close", "lost-events"):
            sample = records()
            if change == "late-open":
                sample[-1].update(input_boottime_ns=119_000_000_000, input_sec=119,
                                  boottime_ns=119_100_000_000)
            elif change == "extra-prepare":
                sample += [{"event": "prepare-for-sleep", "active": True, "boottime_ns": 40e9}]
            elif change == "no-close":
                sample.pop(1)
            else:
                sample += [{"event": "native-input-overrun"}]
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                app.assess("hall", sample, {"identity": "202: Lid Switch"})

    def test_closed_rtc_requires_continuous_observed_black_until_open(self):
        result = app.assess("rtc", records("rtc"), {"identity": "166: pm8xxx_rtc_alarm"})
        self.assertEqual(result["closed_black"], "observed")
        for change in ("lit", "gap", "early-open", "missing-boundary", "wrong-irq"):
            sample, wake = records("rtc"), {"identity": "166: pm8xxx_rtc_alarm"}
            if change == "lit":
                sample[10]["backlights_dark_proxy"] = False
            elif change == "gap":
                del sample[100:140]
            elif change == "early-open":
                sample[-1].update(input_boottime_ns=130_000_000_000, input_sec=130,
                                  boottime_ns=130_100_000_000)
            elif change == "missing-boundary":
                del sample[3:13]
            else:
                wake["identity"] = "202: Lid Switch"
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                app.assess("rtc", sample, wake)

    def test_first_thawed_sample_before_prepare_false_is_audited(self):
        sample = records("rtc")
        first = next(r for r in sample if r["event"] == "fast-state")
        self.assertLess(first["sample_finished_boottime_ns"], sample[3]["boottime_ns"])
        first["backlights_dark_proxy"] = False
        with self.assertRaisesRegex(RuntimeError, "not continuously blank before physical opening"):
            app.assess("rtc", sample, {"identity": "rtc_alarm"})

    def test_late_closed_nonblack_sample_is_not_hidden_in_an_excluded_tail(self):
        sample = records("rtc")
        sample.insert(-1, rtc_sample(179.95, 179.96, False))
        with self.assertRaisesRegex(RuntimeError, "not continuously blank before physical opening"):
            app.assess("rtc", sample, {"identity": "rtc_alarm"})

    def test_logind_open_before_evdev_delivery_does_not_create_closed_relight(self):
        sample = records("rtc")
        # Physical open is 180.0; logind sees it at 180.01, observer reads bright
        # at 180.02, and its raw evdev record only arrives at 180.1.
        sample.insert(-1, {"event": "logind-lid", "closed": False,
                          "boottime_ns": 180_010_000_000, "monotonic_ns": 70_010_000_000})
        sample.insert(-1, rtc_sample(180.02, 180.03, False))
        result = app.assess("rtc", sample, {"identity": "rtc_alarm"})
        self.assertEqual(result["closed_black"], "observed")
        self.assertEqual(result["timing_evidence"]["fully_closed_samples"], 600)

    def test_raw_clock_makes_wallclock_and_event_delivery_latency_irrelevant(self):
        sample = records()
        sample[-1]["boottime_ns"] += 9_000_000_000
        sample[-1]["unix_ns"] -= 120_000_000_000
        self.assertEqual(app.assess("hall", sample, {"identity": "Lid Switch"})[
                         "physical_open_after_close_seconds"], 30)

    def test_nonblack_or_unavailable_sample_crossing_open_is_indeterminate(self):
        for value in (False, None):
            sample = records("rtc")
            sample.insert(-1, rtc_sample(179.99, 180.01, value))
            with self.subTest(value=value), self.assertRaisesRegex(
                    app.EvidenceUncertain, "straddles physical opening"):
                app.assess("rtc", sample, {"identity": "rtc_alarm"})

    def test_dark_window_crossing_open_is_explicitly_classified(self):
        sample = records("rtc")
        sample.insert(-1, rtc_sample(179.99, 180.01))
        result = app.assess("rtc", sample, {"identity": "rtc_alarm"})
        self.assertEqual(result["timing_evidence"]["fully_closed_samples"], 600)
        self.assertEqual(result["timing_evidence"]["boundary_samples"], [{
            "started_boottime_ns": 179_990_000_000, "finished_boottime_ns": 180_010_000_000,
            "classification": "straddles-physical-open", "backlights_dark_proxy": True}])

    def test_fast_read_spanning_suspend_is_not_a_post_thaw_readback(self):
        sample = records("rtc")
        sample.insert(3, rtc_sample(5.2, 120.0005, start_gap=0))
        with self.assertRaisesRegex(app.EvidenceUncertain, "straddles suspend/thaw"):
            app.assess("rtc", sample, {"identity": "rtc_alarm"})

    def test_unknown_clock_legacy_schema_and_corrupt_timeval_are_never_converted(self):
        for change in ("legacy", "clock", "timeval", "future"):
            sample = records()
            if change == "legacy":
                del sample[0]["evidence_schema"]
            elif change == "clock":
                sample[-1]["input_clock"] = "CLOCK_REALTIME"
            elif change == "timeval":
                sample[-1]["input_usec"] = 1_000_000
            else:
                sample[-1]["boottime_ns"] = 29_000_000_000
            with self.subTest(change=change), self.assertRaises(app.EvidenceUncertain):
                app.assess("hall", sample, {"identity": "Lid Switch"})

    def test_missing_reversed_or_different_epoch_read_window_is_indeterminate(self):
        for change in ("missing", "reversed", "epoch", "unknown-readback", "no-suspend"):
            sample = records("rtc")
            first = next(r for r in sample if r["event"] == "fast-state")
            if change == "missing":
                del first["sample_started_boottime_ns"]
            elif change == "reversed":
                first["sample_started_boottime_ns"] = first["sample_finished_boottime_ns"] + 1
            elif change == "epoch":
                first["sample_finished_monotonic_ns"] -= 1_000_000_000
            elif change == "unknown-readback":
                first["backlights_dark_proxy"] = None
            else:
                sample[3]["monotonic_ns"] = sample[3]["boottime_ns"]
            with self.subTest(change=change), self.assertRaises(app.EvidenceUncertain):
                app.assess("rtc", sample, {"identity": "rtc_alarm"})

    def test_observer_only_retains_native_switch_and_overrun(self):
        pack = lambda kind, code, value: observer.EVENT.pack(1, 2, kind, code, value)
        events = observer.lid_events(pack(1, 30, 1) + pack(3, 0, 20) + pack(5, 0, 1)
                                     + pack(5, 0, 0) + pack(0, 3, 0) + pack(5, 0, 2))
        self.assertEqual([e["event"] for e in events],
                         ["native-lid", "native-lid", "native-input-overrun", "native-lid-invalid"])
        self.assertEqual((events[0]["input_sec"], events[0]["input_usec"],
                          events[0]["input_clock"], events[0]["input_boottime_ns"]),
                         (1, 2, "CLOCK_BOOTTIME", 1_000_002_000))
        with self.assertRaises(RuntimeError):
            observer.lid_events(b"x")
        with self.assertRaisesRegex(RuntimeError, "timestamp"):
            observer.lid_events(observer.EVENT.pack(1, 1_000_000, 5, 0, 1))

    def test_physical_dark_requires_both_backlights_and_actual_lower_zero(self):
        def sample():
            lights = {name: {"bl_power": {"value": "4"}} for name in observer.BACKLIGHTS}
            lights[observer.BACKLIGHTS[1]].update({key: {"value": "0"}
                for key in ("brightness", "actual_brightness")})
            return {"lid_closed": True, "backlights": lights}
        self.assertTrue(observer.physical_dark(sample()))
        for name, key in [(name, "bl_power") for name in observer.BACKLIGHTS] + [
            (observer.BACKLIGHTS[1], "brightness"), (observer.BACKLIGHTS[1], "actual_brightness")]:
            data = sample()
            data["backlights"][name][key] = {"error": "OSError"}
            self.assertFalse(observer.physical_dark(data))
        for value in (None, False, 1, "true"):
            data = sample()
            data["lid_closed"] = value
            self.assertFalse(observer.physical_dark(data))
            self.assertTrue(observer.backlights_dark(data))

    def test_native_clock_is_set_on_the_owned_fd_before_reading_switch_state(self):
        with patch.object(observer.os, "open", return_value=17) as opened, \
             patch.object(observer.os, "fstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR)), \
             patch.object(observer.fcntl, "ioctl") as ioctl, patch.object(observer.os, "close") as close:
            self.assertEqual(observer.open_native_device(Path("/dev/input/event-test")), (17, False))
        opened.assert_called_once_with(Path("/dev/input/event-test"), os.O_RDONLY | os.O_NONBLOCK |
                                       os.O_NOFOLLOW | os.O_CLOEXEC)
        self.assertEqual(ioctl.call_args_list[0].args, (17, 0x400445A0, observer.struct.pack("i", 7)))
        self.assertEqual(ioctl.call_args_list[1].args[0:2], (17, 0x8020451B))
        self.assertEqual(ioctl.call_count, 2)
        close.assert_not_called()

    def test_clock_setup_failure_closes_only_owned_fd_before_readiness(self):
        with patch.object(observer.os, "open", return_value=17), \
             patch.object(observer.os, "fstat", return_value=SimpleNamespace(st_mode=stat.S_IFCHR)), \
             patch.object(observer.fcntl, "ioctl", side_effect=OSError("clock unsupported")) as ioctl, \
             patch.object(observer.os, "close") as close:
            with self.assertRaisesRegex(OSError, "clock unsupported"):
                observer.open_native_device(Path("/dev/input/event-test"))
        ioctl.assert_called_once()
        close.assert_called_once_with(17)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux UAPI header check")
    def test_clock_ioctl_matches_linux_headers_without_executing_ioctl(self):
        compiler = shutil.which("cc")
        if not compiler or not Path("/usr/include/linux/input.h").is_file():
            self.skipTest("optional C compiler or Linux input UAPI headers unavailable")
        source = '#define _GNU_SOURCE\n#include <linux/input.h>\n#include <time.h>\n#include <stdio.h>\n'
        source += 'int main(void) { printf("%lu %d\\n", (unsigned long)EVIOCSCLOCKID, CLOCK_BOOTTIME); }\n'
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "input-uapi-values"
            built = subprocess.run([compiler, "-x", "c", "-o", str(binary), "-"],
                                   input=source, text=True, capture_output=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stderr)
            actual = subprocess.check_output([str(binary)], text=True, timeout=5).split()
        self.assertEqual([int(value) for value in actual], [observer.EVIOCSCLOCKID, observer.LINUX_CLOCK_BOOTTIME])

    def test_fast_record_bounds_every_read_not_just_delivery(self):
        instance = object.__new__(observer.Observer)
        instance.record, instance.lid = Mock(), True
        with patch.object(observer, "small", return_value="0"), \
             patch.object(observer, "timestamp", side_effect=[
                 {"boottime_ns": 120_000_000_000, "monotonic_ns": 10_000_000_000},
                 {"boottime_ns": 120_003_000_000, "monotonic_ns": 10_003_000_000}]):
            instance.fast()
        record = instance.record.emit.call_args.kwargs
        self.assertEqual(record["sample_started_boottime_ns"], 120_000_000_000)
        self.assertEqual(record["sample_finished_boottime_ns"], 120_003_000_000)
        self.assertEqual(record["sample_started_monotonic_ns"], 10_000_000_000)
        self.assertEqual(record["sample_finished_monotonic_ns"], 10_003_000_000)

    def test_fast_never_reads_upper_actual_brightness_dsi_transaction(self):
        instance = object.__new__(observer.Observer)
        instance.record = Mock()
        forbidden = Path("/sys/class/backlight") / observer.BACKLIGHTS[0] / "actual_brightness"
        for closed in (True, False, None):
            instance.lid = closed
            paths = []
            def read_cached(path, limit=65536):
                path = Path(path)
                paths.append(path)
                self.assertNotEqual(path, forbidden, "upper actual_brightness performs a DSI read")
                return "0"
            with self.subTest(closed=closed), patch.object(observer, "small", side_effect=read_cached), \
                 patch.object(observer, "timestamp", return_value={"boottime_ns": 100, "monotonic_ns": 100}):
                instance.fast()
            self.assertNotIn(forbidden, paths)
            self.assertIn(Path("/sys/class/backlight") / observer.BACKLIGHTS[1] / "actual_brightness", paths)
            emitted = instance.record.emit.call_args.kwargs["backlights"]
            self.assertEqual(set(emitted[observer.BACKLIGHTS[0]]),
                             {"bl_power", "brightness", "max_brightness"})


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.args = arguments()
        self.args.receipt = self.root / "new-receipt"
        self.verifier, self.layout, self.child = Mock(), Mock(), Mock()
        self.child.poll.return_value = None
        self.verifier.value.side_effect = [self.args.boot_id, ""]
        self.verifier.counters.return_value = {"success": 4, "fail": 0}
        self.state = {"boot_id": self.args.boot_id, "counters": {"success": 3, "fail": 0}}
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(app, "preflight", return_value=(self.verifier, self.layout, self.state)))
        self.candidate = self.stack.enter_context(patch.object(app, "candidate", return_value={"outputs": "fixture"}))
        self.lid = self.stack.enter_context(patch.object(app, "lid_closed", return_value=False))
        self.stack.enter_context(patch.object(app, "start_observer", return_value=self.child))
        self.stop = self.stack.enter_context(patch.object(app, "stop_observer"))
        self.stack.enter_context(patch.object(app, "wait_for_close", return_value=100))
        self.dispatch_check = self.stack.enter_context(patch.object(app, "dispatch_identity"))
        self.stack.enter_context(patch.object(app, "root_file", return_value=b"[Sleep]\nAllowSuspend=no\n"))
        self.stack.enter_context(patch.object(app, "clocks", side_effect=[{"boot": 100, "awake": 100},
                                                                         {"boot": 130, "awake": 105}]))
        self.clock = self.stack.enter_context(patch.object(app, "boot_seconds", side_effect=[104, 200, 1000]))
        self.stack.enter_context(patch.object(app.signal, "signal"))
        self.stack.enter_context(patch.object(app.os, "umask"))
        self.events = []
        def call(*argv, **_kwargs):
            self.events.append(argv)
            return '{"execution_ready":true,"safely_blocked":true}'
        self.call = self.stack.enter_context(patch.object(app, "run", side_effect=call))
        self.cleanup = self.stack.enter_context(patch.object(app, "cleanup_policy",
                                                           side_effect=lambda _: self.events.append(("cleanup",))))
        self.sleep = self.stack.enter_context(patch.object(app.subprocess, "run", return_value=SimpleNamespace(returncode=0)))
        self.stack.enter_context(patch.object(app, "wake_source", return_value={"identity": "202: Lid Switch"}))
        self.stack.enter_context(patch.object(app, "read_observations", return_value=records()))
        self.stack.enter_context(patch.object(app, "open_display_check", return_value={"both": "on"}))

    def run_cycle(self):
        # Open at initial arm, closed at dispatch, open at final readback.
        self.lid.side_effect = [False, True, False]
        return app.perform(self.args)

    def test_check_never_allocates_receipt_starts_observer_or_dispatches(self):
        self.args.command = "check"
        app.perform(self.args)
        self.assertFalse(self.args.receipt.exists())
        self.sleep.assert_not_called()
        self.cleanup.assert_not_called()

    def test_one_guard_dispatch_cleanup_and_pending_physical_result(self):
        result = self.run_cycle()
        self.assertEqual(result["kernel_cycle"], "returned")
        self.assertEqual(result["suspended_seconds"], 25)
        self.assertEqual(result["physical_display_acceptance"], "pending")
        self.assertEqual(self.sleep.call_count, 1)
        self.assertEqual(self.sleep.call_args.args[0], [str(app.GUARD), "run", "120"])
        self.assertEqual(self.sleep.call_args.kwargs["timeout"], 175)
        self.assertEqual(self.cleanup.call_count, 2)
        self.stop.assert_called_once_with(self.child)
        self.assertTrue((self.args.receipt / "result.json").exists())

    def test_open_or_late_dispatch_never_sleeps_and_keeps_consumed_receipt(self):
        self.clock.side_effect = [116]
        with self.assertRaisesRegex(RuntimeError, "15-second bound"):
            self.run_cycle()
        self.sleep.assert_not_called()
        self.cleanup.assert_called_once()
        self.assertTrue((self.args.receipt / "result.json").exists())

    def test_candidate_drift_refuses_before_policy_bind(self):
        self.candidate.side_effect = [{}, RuntimeError("candidate drift")]
        with self.assertRaisesRegex(RuntimeError, "candidate drift"):
            self.run_cycle()
        self.call.assert_not_called()
        self.sleep.assert_not_called()
        self.cleanup.assert_called_once()

    def test_guard_timeout_always_cleans_policy_without_new_request(self):
        self.sleep.side_effect = subprocess.TimeoutExpired("guard", 175)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_cycle()
        self.assertEqual(self.sleep.call_count, 1)
        self.cleanup.assert_called_once()
        self.assertIn("error", json.loads((self.args.receipt / "result.json").read_text()))

    def test_dispatch_identity_drift_refuses_without_sleep(self):
        self.dispatch_check.side_effect = RuntimeError("cycle drift before dispatch")
        with self.assertRaisesRegex(RuntimeError, "cycle drift before dispatch"):
            self.run_cycle()
        self.sleep.assert_not_called()
        self.cleanup.assert_called_once()

    def test_180_second_rescue_still_dispatches_exactly_once(self):
        self.args.rtc_seconds = 180
        self.run_cycle()
        self.assertEqual(self.sleep.call_count, 1)
        self.assertEqual(self.sleep.call_args.args[0], [str(app.GUARD), "run", "180"])
        self.assertEqual(self.sleep.call_args.kwargs["timeout"], 235)

    def test_storage_fault_stops_persistent_post_checks(self):
        self.verifier.storage_health.side_effect = RuntimeError("storage fault")
        with self.assertRaisesRegex(RuntimeError, "storage fault"):
            self.run_cycle()
        self.assertEqual(self.candidate.call_count, 3)  # No post-return configuration read.
        self.cleanup.assert_called_once()

    def test_indeterminate_boundary_is_recorded_without_claiming_hardware_failure(self):
        with patch.object(app, "assess", side_effect=app.EvidenceUncertain("opening boundary overlaps")):
            with self.assertRaises(app.EvidenceUncertain):
                self.run_cycle()
        result = json.loads((self.args.receipt / "result.json").read_text())
        self.assertEqual(result["assessment_status"], "indeterminate")
        self.assertEqual(result["closed_black"], "indeterminate")
        self.assertEqual(result["physical_display_acceptance"], "pending")

    def test_cleanup_error_is_recorded_and_never_claims_acceptance(self):
        self.cleanup.side_effect = RuntimeError("unmount failed")
        with self.assertRaisesRegex(RuntimeError, "unmount failed"):
            self.run_cycle()
        result = json.loads((self.args.receipt / "result.json").read_text())
        self.assertIn("policy_cleanup_error", result)
        self.assertEqual(result["physical_display_acceptance"], "pending")
        self.stop.assert_called_once_with(self.child)


class CleanupTests(unittest.TestCase):
    def test_already_unmounted_cleanup_is_noop(self):
        with patch.object(app.subprocess, "run", return_value=SimpleNamespace(returncode=32)), \
             patch.object(app, "run") as command:
            app.cleanup_policy(Path("/run/pds-lid-cycle-unused"))
        command.assert_not_called()

    def test_only_own_bind_mount_is_unmounted(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory)
            (receipt / "attended.conf").write_text("[Sleep]\nAllowSuspend=yes\n")
            with patch.object(app.subprocess, "run", side_effect=[SimpleNamespace(returncode=0),
                                                                  SimpleNamespace(returncode=32)]), \
                 patch.object(app.Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)), \
                 patch.object(app, "root_file"), patch.object(app.os.path, "samefile", return_value=True), \
                 patch.object(app, "run") as command:
                app.cleanup_policy(receipt)
                command.assert_called_once_with("/usr/bin/umount", str(app.POLICY))
            with patch.object(app.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                 patch.object(app.Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)), \
                 patch.object(app, "root_file"), patch.object(app.os.path, "samefile", return_value=False), \
                 patch.object(app, "run") as command:
                with self.assertRaisesRegex(RuntimeError, "not this transaction"):
                    app.cleanup_policy(receipt)
                command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
