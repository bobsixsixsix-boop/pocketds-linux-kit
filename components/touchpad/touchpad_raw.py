#!/usr/bin/env python3
"""Small Type-B multi-touch frame decoder for the Pocket DS lower screen."""

from __future__ import annotations

from dataclasses import dataclass
from array import array
import errno
import fcntl
import time
from typing import Hashable


@dataclass(frozen=True)
class RawTouchAction:
    kind: str
    contact: Hashable | None = None
    x: float = 0.0
    y: float = 0.0


@dataclass(frozen=True)
class RoutedTouchAction:
    kind: str
    target: str | None = None
    contact: Hashable | None = None
    x: float = 0.0
    y: float = 0.0
    inside: bool = False


class RawTouchRouter:
    """Route physical DSI-2 coordinates into scaled GTK touch targets."""

    SOURCE_WIDTH = 1024.0
    SOURCE_HEIGHT = 768.0

    def __init__(self) -> None:
        self.window_width = 0.0
        self.window_height = 0.0
        self.regions: list[tuple[str, tuple[float, float, float, float]]] = []
        self.contacts: dict[Hashable, str | None] = {}

    def configure(
        self,
        window_width: float,
        window_height: float,
        regions: list[tuple[str, tuple[float, float, float, float]]],
    ) -> bool:
        if window_width <= 0 or window_height <= 0:
            return False
        if any(width <= 0 or height <= 0 for _, (_, _, width, height) in regions):
            return False
        self.window_width = float(window_width)
        self.window_height = float(window_height)
        self.regions = list(regions)
        self.contacts.clear()
        return True

    @staticmethod
    def _inside(
        region: tuple[float, float, float, float], x: float, y: float
    ) -> bool:
        left, top, width, height = region
        return left <= x < left + width and top <= y < top + height

    def _local_point(self, x: float, y: float) -> tuple[float, float]:
        return (
            x * self.window_width / self.SOURCE_WIDTH,
            y * self.window_height / self.SOURCE_HEIGHT,
        )

    def _target_at(self, x: float, y: float) -> str | None:
        local_x, local_y = self._local_point(x, y)
        for name, region in self.regions:
            if self._inside(region, local_x, local_y):
                return name
        return None

    def _inside_target(self, target: str, x: float, y: float) -> bool:
        local_x, local_y = self._local_point(x, y)
        return any(
            name == target and self._inside(region, local_x, local_y)
            for name, region in self.regions
        )

    def route(self, action: RawTouchAction) -> RoutedTouchAction | None:
        if action.kind == "cancel":
            self.contacts.clear()
            return RoutedTouchAction("cancel")
        if action.contact is None:
            return None
        if action.kind == "begin":
            target = self._target_at(action.x, action.y)
            self.contacts[action.contact] = target
        else:
            if action.contact not in self.contacts:
                return None
            target = self.contacts[action.contact]
        inside = bool(
            target is not None
            and self._inside_target(target, action.x, action.y)
        )
        routed = RoutedTouchAction(
            action.kind,
            target,
            action.contact,
            action.x,
            action.y,
            inside,
        )
        if action.kind == "end":
            self.contacts.pop(action.contact, None)
        return routed if target is not None else None

    def reset(self) -> None:
        self.contacts.clear()


class PointerButtonLatch:
    """Keep one virtual button held until every independent owner releases."""

    def __init__(self, press, release) -> None:
        self.press = press
        self.release = release
        self.owners: set[Hashable] = set()

    @property
    def held(self) -> bool:
        return bool(self.owners)

    def acquire(self, owner: Hashable) -> bool:
        if owner in self.owners:
            return False
        if not self.owners:
            self.press()
        self.owners.add(owner)
        return True

    def relinquish(self, owner: Hashable) -> bool:
        if owner not in self.owners:
            return False
        self.owners.remove(owner)
        if not self.owners:
            self.release()
        return True

    def clear(self) -> bool:
        if not self.owners:
            return False
        self.owners.clear()
        self.release()
        return True


class ExclusiveTouchGuard:
    """Own a touchscreen until the touchpad overlay is hidden or exits."""

    def __init__(self) -> None:
        self.device = None

    @property
    def active(self) -> bool:
        return self.device is not None

    def acquire(self, device) -> bool:
        if device is None:
            return False
        if self.device is device:
            return True
        if self.device is not None and not self.release():
            return False
        try:
            device.grab()
        except OSError:
            return False
        self.device = device
        return True

    def release(self) -> bool:
        device = self.device
        if device is None:
            return True
        try:
            device.ungrab()
        except OSError as error:
            if error.errno not in {errno.EBADF, errno.ENODEV}:
                return False
        self.device = None
        return True

    def device_closed(self, device) -> None:
        if self.device is device:
            self.device = None


class ContextMenuTouchGuard:
    """Temporarily keep direct touchscreen events away from popup menus."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.device = None
        self.deadline = 0.0

    @property
    def active(self) -> bool:
        return self.device is not None

    def arm(self, device, timeout_s: float = 12.0) -> bool:
        if device is None:
            return False
        if self.device is not None and self.device is not device:
            self.release()
        if self.device is None:
            try:
                device.grab()
            except OSError:
                return False
            self.device = device
        self.deadline = self.clock() + timeout_s
        return True

    def _clear(self) -> None:
        self.device = None
        self.deadline = 0.0

    def release(self) -> bool:
        device = self.device
        if device is None:
            return True
        try:
            device.ungrab()
        except OSError as error:
            if error.errno not in {errno.EBADF, errno.ENODEV}:
                return False
        self._clear()
        return True

    def device_closed(self, device) -> None:
        if self.device is device:
            self._clear()

    def expire(self, active_contacts: bool = False) -> bool:
        if (
            self.device is None
            or active_contacts
            or self.clock() < self.deadline
        ):
            return False
        return self.release()


@dataclass
class _Slot:
    tracking_id: int | None = None
    x: int | None = None
    y: int | None = None


@dataclass(frozen=True)
class _Committed:
    x: float
    y: float
    forwarded: bool


@dataclass(frozen=True)
class TypeBSnapshot:
    current_slot: int
    # Each entry is (tracking_id, raw_x, raw_y), indexed by kernel slot.
    slots: tuple[tuple[int, int, int], ...]


def _type_b_snapshot(device) -> TypeBSnapshot:
    """Read evdev's cached state; these ioctls do not access the touch hardware."""
    slot_info = device.absinfo(0x2f)  # ABS_MT_SLOT
    if (
        slot_info is None or slot_info.min != 0
        or not 0 <= slot_info.max < 64
        or not 0 <= slot_info.value <= slot_info.max
    ):
        raise OSError(errno.EINVAL, "invalid Type-B slot range")
    count = slot_info.max + 1
    axes = []
    for code in (0x39, 0x35, 0x36):  # TRACKING_ID, POSITION_X, POSITION_Y
        values = array("i", [code] + [0] * count)
        # Linux asm-generic _IOR('E', 0x0a, int[count + 1]).
        request = 0x80000000 | (len(values) * values.itemsize << 16) | (ord("E") << 8) | 0x0a
        fcntl.ioctl(device.fd, request, values, True)
        axes.append(tuple(values[1:]))
    slots = tuple(zip(*axes))
    active_ids = [tracking for tracking, _, _ in slots if tracking >= 0]
    if (
        len(active_ids) != len(set(active_ids))
        or any(tracking < -1 or not 0 <= x < 768 or not 0 <= y < 1024
               for tracking, x, y in slots)
    ):
        raise OSError(errno.EINVAL, "invalid lower touchscreen slot state")
    return TypeBSnapshot(slot_info.value, slots)


def resync_type_b(device, tracker) -> None:
    """Discard queued history and seed a bounded, quiet cached snapshot.

    Caller serializes this with *whole* device.read() batches. Any batch already
    in the caller must also be discarded after this returns. A changing stream
    is retried, never presented as a fresh down at a possibly stale position.
    """
    remaining = 4096

    def drain():
        nonlocal remaining
        consumed = False
        while True:
            try:
                batch = device.read()
                count = 0
                for _event in batch:
                    consumed = True
                    count += 1
                    remaining -= 1
                    if remaining < 0:
                        raise OSError(errno.EOVERFLOW, "touch resync queue limit")
                if not count:
                    return consumed
            except BlockingIOError:
                return consumed

    tracker.reset()
    for _attempt in range(3):
        drain()
        first = _type_b_snapshot(device)
        if drain():
            continue
        second = _type_b_snapshot(device)
        if drain() or first != second:
            continue
        tracker.seed(second)
        return
    raise OSError(errno.EAGAIN, "touch state changed during resync")


def lower_touch_to_screen(raw_x: int, raw_y: int) -> tuple[float, float]:
    """Map the portrait 768x1024 sensor to DSI-2 rotated right."""
    return 1023.0 - raw_y, float(raw_x)


class TypeBTouchFrame:
    """Convert Linux multi-touch slots into begin/update/end actions."""

    def __init__(self) -> None:
        self.current_slot = 0
        self.slots: dict[int, _Slot] = {}
        self.committed: dict[int, _Committed] = {}

    def set_slot(self, slot: int) -> None:
        self.current_slot = slot
        self.slots.setdefault(slot, _Slot())

    def set_tracking_id(self, tracking_id: int) -> None:
        slot = self.slots.setdefault(self.current_slot, _Slot())
        if tracking_id < 0:
            slot.tracking_id = None
            return
        slot.tracking_id = tracking_id
        # Type-B axes belong to the slot, not its tracking ID. The kernel
        # omits unchanged X/Y values even when a new contact reuses that slot.
        # Keep known axes until reset() invalidates the whole stream.

    def set_x(self, x: int) -> None:
        self.slots.setdefault(self.current_slot, _Slot()).x = x

    def set_y(self, y: int) -> None:
        self.slots.setdefault(self.current_slot, _Slot()).y = y

    def seed(self, snapshot: TypeBSnapshot) -> None:
        self.reset()
        self.current_slot = snapshot.current_slot
        for number, (tracking, x, y) in enumerate(snapshot.slots):
            self.slots[number] = _Slot(None if tracking < 0 else tracking, x, y)
            if tracking >= 0:
                # A finger present at handover is not a new click/key press.
                px, py = lower_touch_to_screen(x, y)
                self.committed[tracking] = _Committed(px, py, False)

    @staticmethod
    def _inside(region: tuple[float, float, float, float], x: float, y: float) -> bool:
        left, top, width, height = region
        return left <= x < left + width and top <= y < top + height

    def sync(self, region: tuple[float, float, float, float]) -> list[RawTouchAction]:
        snapshot: dict[int, tuple[float, float]] = {}
        for slot in self.slots.values():
            if slot.tracking_id is None or slot.x is None or slot.y is None:
                continue
            snapshot[slot.tracking_id] = lower_touch_to_screen(slot.x, slot.y)

        actions: list[RawTouchAction] = []
        for contact, previous in self.committed.items():
            if contact not in snapshot and previous.forwarded:
                actions.append(
                    RawTouchAction("end", contact, previous.x, previous.y)
                )

        next_committed: dict[int, _Committed] = {}
        for contact, (x, y) in snapshot.items():
            previous = self.committed.get(contact)
            if previous is None:
                forwarded = self._inside(region, x, y)
                next_committed[contact] = _Committed(x, y, forwarded)
                if forwarded:
                    actions.append(RawTouchAction("begin", contact, x, y))
                continue
            next_committed[contact] = _Committed(x, y, previous.forwarded)

        for contact, current in next_committed.items():
            previous = self.committed.get(contact)
            if (
                previous is not None
                and current.forwarded
                and (current.x != previous.x or current.y != previous.y)
            ):
                actions.append(
                    RawTouchAction("update", contact, current.x, current.y)
                )

        self.committed = next_committed
        return actions

    def reset(self) -> list[RawTouchAction]:
        had_forwarded_contact = any(
            contact.forwarded for contact in self.committed.values()
        )
        self.current_slot = 0
        self.slots.clear()
        self.committed.clear()
        return [RawTouchAction("cancel")] if had_forwarded_contact else []
