#!/usr/bin/env python3
"""Deterministic gesture engine for the Pocket DS lower-screen touchpad."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Hashable


@dataclass(frozen=True)
class GestureAction:
    kind: str
    x: int = 0
    y: int = 0


@dataclass
class TouchPoint:
    start_x: float
    start_y: float
    x: float
    y: float
    started_ms: int
    maximum_travel: float = 0.0

    def update(self, x: float, y: float) -> tuple[float, float]:
        delta_x = x - self.x
        delta_y = y - self.y
        self.x = x
        self.y = y
        self.maximum_travel = max(
            self.maximum_travel,
            math.hypot(x - self.start_x, y - self.start_y),
        )
        return delta_x, delta_y


class TouchpadGestureEngine:
    """Translate one-, two-, and three-finger streams into pointer actions.

    Four or more contacts cancel the gesture instead of guessing.  Linux input
    emission deliberately stays outside this class so gesture behavior
    can be smoke-tested without a display server or `/dev/uinput`.
    """

    def __init__(
        self,
        *,
        pointer_gain: float = 1.35,
        tap_travel_px: float = 12.0,
        tap_duration_ms: int = 280,
        double_tap_ms: int = 330,
        double_tap_distance_px: float = 36.0,
        scroll_step_px: float = 18.0,
        pinch_step_ratio: float = 0.055,
    ) -> None:
        self.pointer_gain = pointer_gain
        self.tap_travel_px = tap_travel_px
        self.tap_duration_ms = tap_duration_ms
        self.double_tap_ms = double_tap_ms
        self.double_tap_distance_px = double_tap_distance_px
        self.scroll_step_px = scroll_step_px
        self.pinch_step_ratio = pinch_step_ratio
        self.points: dict[Hashable, TouchPoint] = {}
        self.mode = "idle"
        self.primary: Hashable | None = None
        self.pointer_remainder_x = 0.0
        self.pointer_remainder_y = 0.0
        self.last_tap: tuple[int, float, float] | None = None
        self.two_ids: tuple[Hashable, Hashable] | None = None
        self.two_dirty: set[Hashable] = set()
        self.two_started_ms = 0
        self.two_maximum_travel = 0.0
        self.two_last_centroid = (0.0, 0.0)
        self.two_last_distance = 0.0
        self.two_scroll_x = 0.0
        self.two_scroll_y = 0.0
        self.two_pinch = 0.0
        self.two_latched = ""
        self.two_tap_candidate = False
        self.three_ids: tuple[Hashable, Hashable, Hashable] | None = None
        self.three_last_centroid = (0.0, 0.0)
        self.three_pending_x = 0.0
        self.three_pending_y = 0.0
        self.three_drag_threshold_px = 7.0

    @staticmethod
    def _whole_steps(value: float, threshold: float) -> int:
        return int(value / threshold)

    def _reset_when_empty(self) -> None:
        if self.points:
            return
        self.mode = "idle"
        self.primary = None
        self.two_ids = None
        self.two_dirty.clear()
        self.two_tap_candidate = False
        self.three_ids = None
        self.three_pending_x = 0.0
        self.three_pending_y = 0.0

    def _begin_two_finger(self, now_ms: int) -> None:
        ids = tuple(self.points.keys())
        self.two_ids = (ids[0], ids[1])
        first = self.points[ids[0]]
        second = self.points[ids[1]]
        self.mode = "two"
        self.primary = None
        self.two_dirty.clear()
        self.two_started_ms = now_ms
        self.two_maximum_travel = max(
            first.maximum_travel, second.maximum_travel
        )
        self.two_last_centroid = (
            (first.x + second.x) / 2.0,
            (first.y + second.y) / 2.0,
        )
        self.two_last_distance = max(
            1.0, math.hypot(first.x - second.x, first.y - second.y)
        )
        self.two_scroll_x = 0.0
        self.two_scroll_y = 0.0
        self.two_pinch = 0.0
        self.two_latched = ""
        self.two_tap_candidate = False
        self.last_tap = None

    def _begin_three_finger(self) -> None:
        ids = tuple(self.points.keys())
        self.three_ids = (ids[0], ids[1], ids[2])
        first, second, third = (self.points[contact] for contact in self.three_ids)
        self.mode = "three"
        self.primary = None
        self.three_last_centroid = (
            (first.x + second.x + third.x) / 3.0,
            (first.y + second.y + third.y) / 3.0,
        )
        self.three_pending_x = 0.0
        self.three_pending_y = 0.0
        self.pointer_remainder_x = 0.0
        self.pointer_remainder_y = 0.0
        self.last_tap = None
        self.two_ids = None
        self.two_dirty.clear()
        self.two_tap_candidate = False

    def _clear_three_finger(self) -> None:
        self.three_ids = None
        self.three_pending_x = 0.0
        self.three_pending_y = 0.0

    def touch_begin(
        self, contact: Hashable, x: float, y: float, now_ms: int
    ) -> list[GestureAction]:
        actions: list[GestureAction] = []
        if contact in self.points:
            return actions
        self.points[contact] = TouchPoint(x, y, x, y, now_ms)
        count = len(self.points)
        if count == 1:
            self.primary = contact
            self.pointer_remainder_x = 0.0
            self.pointer_remainder_y = 0.0
            if self.last_tap is not None:
                tap_ms, tap_x, tap_y = self.last_tap
                if (
                    0 <= now_ms - tap_ms <= self.double_tap_ms
                    and math.hypot(x - tap_x, y - tap_y)
                    <= self.double_tap_distance_px
                ):
                    self.mode = "drag"
                    self.last_tap = None
                    actions.append(GestureAction("left_down"))
                    return actions
            self.mode = "pointer"
            return actions
        if count == 2 and self.mode != "drag":
            self._begin_two_finger(now_ms)
            return actions
        if count == 3 and self.mode == "two":
            self._begin_three_finger()
            return actions

        if self.mode in {"drag", "three_drag"}:
            actions.append(GestureAction("left_up"))
        self.mode = "ignore"
        self.primary = None
        self.last_tap = None
        return actions

    def _pointer_move(
        self, delta_x: float, delta_y: float
    ) -> list[GestureAction]:
        self.pointer_remainder_x += delta_x * self.pointer_gain
        self.pointer_remainder_y += delta_y * self.pointer_gain
        move_x = int(self.pointer_remainder_x)
        move_y = int(self.pointer_remainder_y)
        if move_x == 0 and move_y == 0:
            return []
        self.pointer_remainder_x -= move_x
        self.pointer_remainder_y -= move_y
        return [GestureAction("move", move_x, move_y)]

    def _two_finger_move(self) -> list[GestureAction]:
        if self.two_ids is None or not all(
            contact in self.points for contact in self.two_ids
        ):
            return []
        if not all(contact in self.two_dirty for contact in self.two_ids):
            return []
        self.two_dirty.clear()
        first = self.points[self.two_ids[0]]
        second = self.points[self.two_ids[1]]
        self.two_maximum_travel = max(
            self.two_maximum_travel,
            first.maximum_travel,
            second.maximum_travel,
        )
        centroid = ((first.x + second.x) / 2.0, (first.y + second.y) / 2.0)
        distance = max(1.0, math.hypot(first.x - second.x, first.y - second.y))
        delta_x = centroid[0] - self.two_last_centroid[0]
        delta_y = centroid[1] - self.two_last_centroid[1]
        ratio_delta = math.log(distance / self.two_last_distance)
        self.two_last_centroid = centroid
        self.two_last_distance = distance

        if self.two_latched != "scroll":
            self.two_pinch += ratio_delta
            if abs(self.two_pinch) >= self.pinch_step_ratio:
                self.two_latched = "pinch"
        if self.two_latched == "pinch":
            steps = self._whole_steps(self.two_pinch, self.pinch_step_ratio)
            if steps == 0:
                return []
            self.two_pinch -= steps * self.pinch_step_ratio
            return [GestureAction("zoom", y=steps)]

        self.two_scroll_x += delta_x
        self.two_scroll_y += delta_y
        if (
            self.two_latched == ""
            and math.hypot(self.two_scroll_x, self.two_scroll_y) >= 7.0
        ):
            self.two_latched = "scroll"
            self.two_pinch = 0.0
        if self.two_latched != "scroll":
            return []
        horizontal = self._whole_steps(self.two_scroll_x, self.scroll_step_px)
        vertical = self._whole_steps(self.two_scroll_y, self.scroll_step_px)
        if horizontal == 0 and vertical == 0:
            return []
        self.two_scroll_x -= horizontal * self.scroll_step_px
        self.two_scroll_y -= vertical * self.scroll_step_px
        return [GestureAction("scroll", horizontal, vertical)]

    def _three_finger_move(self) -> list[GestureAction]:
        if self.three_ids is None or not all(
            contact in self.points for contact in self.three_ids
        ):
            return []
        points = [self.points[contact] for contact in self.three_ids]
        centroid = (
            sum(point.x for point in points) / 3.0,
            sum(point.y for point in points) / 3.0,
        )
        delta_x = centroid[0] - self.three_last_centroid[0]
        delta_y = centroid[1] - self.three_last_centroid[1]
        self.three_last_centroid = centroid

        if self.mode == "three":
            self.three_pending_x += delta_x
            self.three_pending_y += delta_y
            if (
                math.hypot(self.three_pending_x, self.three_pending_y)
                < self.three_drag_threshold_px
            ):
                return []
            self.mode = "three_drag"
            actions = [GestureAction("left_down")]
            actions.extend(
                self._pointer_move(self.three_pending_x, self.three_pending_y)
            )
            self.three_pending_x = 0.0
            self.three_pending_y = 0.0
            return actions
        return self._pointer_move(delta_x, delta_y)

    def touch_update(
        self, contact: Hashable, x: float, y: float, now_ms: int
    ) -> list[GestureAction]:
        del now_ms
        point = self.points.get(contact)
        if point is None:
            return []
        delta_x, delta_y = point.update(x, y)
        if self.mode in {"pointer", "drag"} and contact == self.primary:
            return self._pointer_move(delta_x, delta_y)
        if self.mode == "two":
            self.two_dirty.add(contact)
            return self._two_finger_move()
        if self.mode == "two_wait" and self.two_tap_candidate:
            self.two_tap_candidate = point.maximum_travel <= self.tap_travel_px
        if self.mode in {"three", "three_drag"}:
            return self._three_finger_move()
        return []

    def touch_end(
        self, contact: Hashable, x: float, y: float, now_ms: int
    ) -> list[GestureAction]:
        point = self.points.get(contact)
        if point is None:
            return []
        point.update(x, y)
        actions: list[GestureAction] = []
        mode = self.mode
        del self.points[contact]

        if mode == "drag":
            actions.append(GestureAction("left_up"))
            self.mode = "ignore" if self.points else "idle"
            self.primary = None
        elif mode in {"three", "three_drag"}:
            if mode == "three_drag":
                actions.append(GestureAction("left_up"))
            self.mode = "ignore" if self.points else "idle"
            self.primary = None
            self._clear_three_finger()
        elif mode == "pointer":
            duration = now_ms - point.started_ms
            if (
                not self.points
                and 0 <= duration <= self.tap_duration_ms
                and point.maximum_travel <= self.tap_travel_px
            ):
                actions.append(GestureAction("left_click"))
                self.last_tap = (now_ms, point.start_x, point.start_y)
            elif not self.points:
                self.last_tap = None
        elif mode == "two":
            self.two_maximum_travel = max(
                self.two_maximum_travel, point.maximum_travel
            )
            self.two_tap_candidate = (
                self.two_latched == ""
                and 0 <= now_ms - self.two_started_ms <= self.tap_duration_ms + 80
                and self.two_maximum_travel <= self.tap_travel_px
            )
            self.mode = "two_wait"
        elif mode == "two_wait" and not self.points:
            if (
                self.two_tap_candidate
                and 0 <= now_ms - self.two_started_ms <= self.tap_duration_ms + 80
                and point.maximum_travel <= self.tap_travel_px
            ):
                actions.append(GestureAction("right_click"))
            self.last_tap = None

        self._reset_when_empty()
        return actions

    def cancel(self) -> list[GestureAction]:
        actions = (
            [GestureAction("left_up")]
            if self.mode in {"drag", "three_drag"}
            else []
        )
        self.points.clear()
        self.last_tap = None
        self.mode = "idle"
        self.primary = None
        self.two_ids = None
        self.two_dirty.clear()
        self.two_tap_candidate = False
        self._clear_three_finger()
        return actions
