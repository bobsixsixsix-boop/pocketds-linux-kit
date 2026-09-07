#!/usr/bin/env python3
"""Continuous production-daemon regressions with simulated backlights and time."""
import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
import unittest
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('brightness', ROOT / 'components/brightness/pocketds-brightness.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class DaemonTests(unittest.TestCase):
    def run_daemon(self, top_power=0, relight=False, wake=False, lid_open=True):
        now = [0.0]
        powers = {'top': top_power, 'bottom': 4}
        raw = {'top': 100, 'bottom': 0}
        writes, samples, waits = [], [], []
        state = app.make_state(56, 68, 'test')

        def snapshot():
            samples.append(now[0])
            return {screen: {'raw': value, 'max': 100, 'percent': max(5, value)}
                    for screen, value in raw.items()}

        def write(screen, value):
            writes.append((now[0], screen, value, powers[screen]))
            raw[screen] = value

        class Event:
            def wait(self, interval):
                waits.append(interval)
                now[0] += interval
                if now[0] == 0.5 and relight:
                    raw['bottom'] = 68
                if now[0] == 1.5 and wake:
                    powers['bottom'] = 0
                if now[0] >= 3:
                    app.STOP_REQUESTED = True
                return app.STOP_REQUESTED

        with patch.multiple(app, STOP_REQUESTED=False, STOP_EVENT=Event(),
                initialize_state=lambda **kwargs: (state, 'existing'),
                backlight_power_snapshot=lambda: dict(powers), hardware_snapshot=snapshot,
                read_int=lambda path: powers['bottom'], set_hardware=write,
                lid_is_open=lambda: lid_open,
                blank_bottom_backlight=lambda: write('bottom', 0)), \
                patch.object(app.time, 'monotonic', side_effect=lambda: now[0]), redirect_stdout(io.StringIO()):
            self.assertEqual(app.daemon(1, 0), 0)
        return raw, writes, samples, waits

    def test_off_bottom_does_not_stop_top_drift_repair(self):
        raw, writes, samples, waits = self.run_daemon()
        self.assertEqual(raw, {'top': 56, 'bottom': 0})
        self.assertEqual([(screen, value) for _, screen, value, _ in writes], [('top', 56)])
        self.assertLessEqual(writes[0][0], 2)
        self.assertLessEqual(len(samples), 5)  # no full snapshots on every fast tick
        self.assertTrue(all(wait <= 0.25 for wait in waits))

    def test_external_bottom_relight_is_blanked_again(self):
        raw, writes, _, _ = self.run_daemon(relight=True)
        self.assertEqual(raw['bottom'], 0)
        self.assertIn((1.0, 'bottom', 0, 4), writes)
        self.assertFalse(any(screen == 'bottom' and value > 0 for _, screen, value, _ in writes))

    def test_both_off_never_restores_user_brightness(self):
        _, writes, _, _ = self.run_daemon(top_power=4, relight=True)
        self.assertEqual([(screen, value) for _, screen, value, _ in writes], [('bottom', 0)])

    def test_wake_restores_bottom_within_fast_poll(self):
        raw, writes, _, _ = self.run_daemon(wake=True)
        self.assertEqual(raw['bottom'], 68)
        self.assertIn((1.5, 'bottom', 68, 0), writes)

    def test_rtc_wake_with_closed_lid_never_restores_raw_brightness(self):
        raw, writes, _, _ = self.run_daemon(top_power=4, wake=True, lid_open=False)
        self.assertEqual(raw['bottom'], 0)
        self.assertFalse(any(value > 0 for _, _, value, _ in writes))

    def test_power_off_between_sample_and_write_does_not_relight(self):
        state = app.make_state(56, 68, 'test')
        with patch.object(app, 'drift', return_value={'top': {}, 'bottom': {}}), \
                patch.object(app, 'lid_is_open', return_value=True), \
                patch.object(app, 'backlight_power_snapshot', return_value={'top': 4, 'bottom': 4}), \
                patch.object(app, 'set_hardware') as write:
            self.assertEqual(app.reconcile(state, screens=('top', 'bottom'))['applied'], [])
            write.assert_not_called()

    def test_lid_close_between_two_positive_writes_stops_second_write(self):
        state = app.make_state(56, 68, 'test')
        with patch.object(app, 'drift', return_value={'top': {}, 'bottom': {}}), \
                patch.object(app, 'lid_is_open', side_effect=[True, False]), \
                patch.object(app, 'backlight_power_snapshot', return_value={'top': 0, 'bottom': 0}), \
                patch.object(app, 'set_hardware') as write:
            self.assertEqual(app.reconcile(state, screens=('top', 'bottom'))['applied'], ['top'])
            write.assert_called_once_with('top', 56)

    def test_lid_read_requires_exact_open_and_fails_closed_on_timeout(self):
        for code, text, expected in ((0, 'b false\n', True), (0, 'b true\n', False),
                                     (1, 'b false\n', False), (0, 'false', False)):
            reply = subprocess.CompletedProcess([], code, text, '')
            with self.subTest(text=text, code=code), patch.object(app.subprocess, 'run', return_value=reply):
                self.assertEqual(app.lid_is_open(), expected)
        with patch.object(app.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 0.6)):
            self.assertFalse(app.lid_is_open())


if __name__ == '__main__':
    unittest.main()
