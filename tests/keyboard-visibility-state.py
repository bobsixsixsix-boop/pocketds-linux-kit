#!/usr/bin/env python3
"""Deterministic tests for keyboard visibility state transitions."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import importlib.util
from pathlib import Path
import sys
from typing import Callable
import unittest


ROOT = Path(__file__).resolve().parent.parent
PROGRAM = ROOT / "components" / "keyboard" / "visibility_state.py"
spec = importlib.util.spec_from_file_location("visibility_state", PROGRAM)
assert spec and spec.loader
visibility_state = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = visibility_state
spec.loader.exec_module(visibility_state)
VisibilityController = visibility_state.VisibilityController
VisibilitySnapshot = visibility_state.VisibilitySnapshot
VisibilityState = visibility_state.VisibilityState


@dataclass
class FakeTimerHandle:
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeScheduler:
    """Deterministic clock which can deliver cancelled callbacks late."""

    def __init__(self) -> None:
        self.now_ms = 0
        self._sequence = 0
        self._events: list[
            tuple[int, int, FakeTimerHandle, Callable[[], None]]
        ] = []

    def call_later(
        self, delay_ms: int, callback: Callable[[], None]
    ) -> FakeTimerHandle:
        handle = FakeTimerHandle()
        self._sequence += 1
        heapq.heappush(
            self._events,
            (self.now_ms + delay_ms, self._sequence, handle, callback),
        )
        return handle

    def advance(self, delta_ms: int, *, deliver_cancelled: bool = False) -> None:
        target = self.now_ms + delta_ms
        while self._events and self._events[0][0] <= target:
            deadline, _sequence, handle, callback = heapq.heappop(self._events)
            self.now_ms = deadline
            if not handle.cancelled or deliver_cancelled:
                callback()
        self.now_ms = target

    @property
    def pending_count(self) -> int:
        return sum(not event[2].cancelled for event in self._events)


class FakeFocus:
    def __init__(self) -> None:
        self.valid = False

    def __call__(self) -> bool:
        return self.valid


class VisibilityControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeScheduler()
        self.focus = FakeFocus()
        self.transitions: list[VisibilitySnapshot] = []
        self.controller = VisibilityController(
            self.clock, self.focus, self.transitions.append
        )

    def test_initial_state_is_hidden_auto(self) -> None:
        self.assertEqual(self.controller.state, VisibilityState.HIDDEN_AUTO)
        self.assertFalse(self.controller.visible)

    def test_manual_open_has_no_900ms_focus_validation(self) -> None:
        self.controller.manual_show()

        self.assertEqual(self.controller.state, VisibilityState.MANUAL_VISIBLE)
        self.assertEqual(self.clock.pending_count, 0)
        self.clock.advance(5_000)
        self.assertEqual(self.controller.state, VisibilityState.MANUAL_VISIBLE)

    def test_auto_open_invalid_focus_hides_after_900ms(self) -> None:
        self.focus.valid = True
        self.controller.editable_focus_gained("field-a")
        self.focus.valid = False

        self.clock.advance(899)
        self.assertEqual(self.controller.state, VisibilityState.AUTO_VISIBLE)
        self.clock.advance(1)
        self.assertEqual(self.controller.state, VisibilityState.HIDDEN_AUTO)

    def test_auto_open_valid_focus_remains_visible(self) -> None:
        self.focus.valid = True
        self.controller.editable_focus_gained("field-a")

        self.clock.advance(900)
        self.assertEqual(self.controller.state, VisibilityState.AUTO_VISIBLE)
        self.assertTrue(self.controller.visible)

    def test_manual_show_beats_a_cancelled_900ms_callback(self) -> None:
        self.focus.valid = True
        self.controller.editable_focus_gained("field-a")
        old_generation = self.controller.generation
        self.clock.advance(100)

        self.controller.manual_show()
        self.focus.valid = False
        self.assertGreater(self.controller.generation, old_generation)

        self.clock.advance(800, deliver_cancelled=True)
        self.assertEqual(self.controller.state, VisibilityState.MANUAL_VISIBLE)

    def test_latest_focus_generation_beats_stale_callbacks(self) -> None:
        self.focus.valid = True
        self.controller.editable_focus_gained("field-a")
        self.clock.advance(100)
        self.focus.valid = False
        self.controller.editable_focus_lost("field-a")
        self.clock.advance(100)
        self.focus.valid = True
        self.controller.editable_focus_gained("field-b")

        self.clock.advance(700, deliver_cancelled=True)
        self.assertEqual(self.controller.state, VisibilityState.AUTO_VISIBLE)
        self.clock.advance(200)
        self.assertEqual(self.controller.state, VisibilityState.AUTO_VISIBLE)

    def test_manual_hide_suppresses_repeated_current_focus_event(self) -> None:
        self.focus.valid = True
        self.controller.editable_focus_gained("field-a")
        self.controller.manual_hide()

        self.controller.editable_focus_gained("field-a")
        self.assertEqual(self.controller.state, VisibilityState.HIDDEN_BY_USER)

    def test_new_focus_cycle_can_auto_open_after_manual_hide(self) -> None:
        self.focus.valid = True
        self.controller.editable_focus_gained("field-a")
        self.controller.manual_hide()
        self.controller.editable_focus_lost("field-a")
        self.controller.editable_focus_gained("field-a")

        self.assertEqual(self.controller.state, VisibilityState.AUTO_VISIBLE)

    def test_stale_focus_loss_cannot_hide_new_field(self) -> None:
        self.focus.valid = True
        self.controller.editable_focus_gained("field-a")
        self.controller.editable_focus_gained("field-b")

        self.controller.editable_focus_lost("field-a")
        self.clock.advance(2_000, deliver_cancelled=True)
        self.assertEqual(self.controller.state, VisibilityState.AUTO_VISIBLE)

    def test_real_focus_loss_hides_after_380ms(self) -> None:
        self.focus.valid = True
        self.controller.editable_focus_gained("field-a")
        self.focus.valid = False
        self.controller.editable_focus_lost("field-a")

        self.clock.advance(379)
        self.assertEqual(self.controller.state, VisibilityState.AUTO_VISIBLE)
        self.clock.advance(1)
        self.assertEqual(self.controller.state, VisibilityState.HIDDEN_AUTO)

    def test_manual_toggle_uses_explicit_user_states(self) -> None:
        self.controller.manual_toggle()
        self.assertEqual(self.controller.state, VisibilityState.MANUAL_VISIBLE)
        self.controller.manual_toggle()
        self.assertEqual(self.controller.state, VisibilityState.HIDDEN_BY_USER)
        self.controller.manual_toggle()
        self.assertEqual(self.controller.state, VisibilityState.MANUAL_VISIBLE)

    def test_shutdown_invalidates_even_late_callback(self) -> None:
        self.focus.valid = True
        self.controller.editable_focus_gained("field-a")
        self.controller.shutdown()
        generation = self.controller.generation
        self.focus.valid = False

        self.clock.advance(900, deliver_cancelled=True)
        self.assertEqual(self.controller.generation, generation)
        self.assertEqual(self.controller.state, VisibilityState.AUTO_VISIBLE)

    def test_fifty_manual_show_hide_cycles_cross_old_900ms_window(self) -> None:
        for cycle in range(50):
            with self.subTest(cycle=cycle):
                self.controller.manual_show()
                self.clock.advance(1_200, deliver_cancelled=True)
                self.assertEqual(
                    self.controller.state, VisibilityState.MANUAL_VISIBLE
                )
                self.controller.manual_hide()
                self.assertEqual(
                    self.controller.state, VisibilityState.HIDDEN_BY_USER
                )


if __name__ == "__main__":
    unittest.main()
