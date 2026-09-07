"""Pure visibility state machine for the Pocket DS desktop keyboard.

This module deliberately has no GTK, GLib, D-Bus, AT-SPI, or systemd
dependency. The UI adapter owns the real window and supplies a scheduler and
an editable-focus probe. That keeps manual visibility separate from the
best-effort accessibility-driven auto-popup path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Hashable, Protocol


class VisibilityState(str, Enum):
    """Authoritative keyboard visibility state."""

    HIDDEN_AUTO = "hidden-auto"
    HIDDEN_BY_USER = "hidden-by-user"
    MANUAL_VISIBLE = "manual-visible"
    AUTO_VISIBLE = "auto-visible"


class TimerHandle(Protocol):
    def cancel(self) -> None:
        """Request cancellation of a scheduled callback."""


class Scheduler(Protocol):
    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> TimerHandle:
        """Run callback after delay_ms and return a cancellation handle."""


@dataclass(frozen=True)
class VisibilitySnapshot:
    state: VisibilityState
    visible: bool
    generation: int
    editable_focus_id: Hashable | None


class VisibilityController:
    """Resolve manual and AT-SPI events into one deterministic state.

    Cancellation is guarded twice: the scheduler handle is cancelled, and
    every callback captures a monotonically increasing generation. The latter
    protects against a callback that GLib queued before cancellation.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        is_editable_focus_valid: Callable[[], bool],
        on_state_changed: Callable[[VisibilitySnapshot], None] | None = None,
        *,
        settle_ms: int = 900,
        focus_loss_ms: int = 380,
    ) -> None:
        if settle_ms < 0 or focus_loss_ms < 0:
            raise ValueError("visibility delays must be non-negative")

        self._scheduler = scheduler
        self._is_editable_focus_valid = is_editable_focus_valid
        self._on_state_changed = on_state_changed
        self._settle_ms = settle_ms
        self._focus_loss_ms = focus_loss_ms

        self._state = VisibilityState.HIDDEN_AUTO
        self._editable_focus_id: Hashable | None = None
        self._suppressed_focus_id: Hashable | None = None
        self._generation = 0
        self._validation_handle: TimerHandle | None = None

    @property
    def state(self) -> VisibilityState:
        return self._state

    @property
    def visible(self) -> bool:
        return self._state in {
            VisibilityState.MANUAL_VISIBLE,
            VisibilityState.AUTO_VISIBLE,
        }

    @property
    def generation(self) -> int:
        return self._generation

    def snapshot(self) -> VisibilitySnapshot:
        return VisibilitySnapshot(
            state=self._state,
            visible=self.visible,
            generation=self._generation,
            editable_focus_id=self._editable_focus_id,
        )

    def manual_show(self) -> None:
        """Show until an explicit manual hide/toggle, regardless of focus."""

        self._invalidate_validation()
        self._suppressed_focus_id = None
        self._set_state(VisibilityState.MANUAL_VISIBLE)

    def manual_hide(self) -> None:
        """Hide and suppress repeated focus events from the current field."""

        self._invalidate_validation()
        self._suppressed_focus_id = self._editable_focus_id
        self._set_state(VisibilityState.HIDDEN_BY_USER)

    def manual_toggle(self) -> None:
        if self.visible:
            self.manual_hide()
        else:
            self.manual_show()

    def editable_focus_gained(self, focus_id: Hashable) -> None:
        """Record a trustworthy editable focus event.

        Repeated events for a field the user explicitly hid stay suppressed.
        Leaving that field clears suppression, so a later focus cycle can
        auto-open normally.
        """

        self._editable_focus_id = focus_id

        if self._state is VisibilityState.MANUAL_VISIBLE:
            return
        if (
            self._state is VisibilityState.HIDDEN_BY_USER
            and focus_id == self._suppressed_focus_id
        ):
            return

        self._suppressed_focus_id = None
        self._set_state(VisibilityState.AUTO_VISIBLE)
        self._schedule_validation(self._settle_ms)

    def editable_focus_lost(self, focus_id: Hashable | None = None) -> None:
        """Record focus loss without allowing a stale object to win."""

        if (
            focus_id is not None
            and self._editable_focus_id is not None
            and focus_id != self._editable_focus_id
        ):
            return

        old_focus_id = self._editable_focus_id
        self._editable_focus_id = None
        if old_focus_id == self._suppressed_focus_id:
            self._suppressed_focus_id = None

        if self._state is VisibilityState.AUTO_VISIBLE:
            self._schedule_validation(self._focus_loss_ms)

    def shutdown(self) -> None:
        """Invalidate all queued work before the UI adapter is destroyed."""

        self._invalidate_validation()

    def _schedule_validation(self, delay_ms: int) -> None:
        self._invalidate_validation()
        token = self._generation
        self._validation_handle = self._scheduler.call_later(
            delay_ms, lambda: self._validate_focus(token)
        )

    def _invalidate_validation(self) -> None:
        self._generation += 1
        if self._validation_handle is not None:
            self._validation_handle.cancel()
            self._validation_handle = None

    def _validate_focus(self, token: int) -> None:
        if token != self._generation:
            return
        self._validation_handle = None
        if self._state is not VisibilityState.AUTO_VISIBLE:
            return
        if self._is_editable_focus_valid():
            return

        self._editable_focus_id = None
        self._set_state(VisibilityState.HIDDEN_AUTO)

    def _set_state(self, state: VisibilityState) -> None:
        if state is self._state:
            return
        self._state = state
        if self._on_state_changed is not None:
            self._on_state_changed(self.snapshot())
