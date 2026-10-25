"""Explicit runtime states for one live caption session."""

from __future__ import annotations

from enum import StrEnum


class RuntimeState(StrEnum):
    """Short-lived processing states, separate from saved-session status."""

    IDLE = "idle"
    STARTING = "starting"
    LIVE = "live"
    PAUSING = "pausing"
    PAUSED = "paused"
    RESUMING = "resuming"
    STOPPING = "stopping"
    ERROR = "error"


class InvalidRuntimeTransition(RuntimeError):
    """Raised when the requested transition is not part of the workflow."""


_ALLOWED_TRANSITIONS: dict[RuntimeState, frozenset[RuntimeState]] = {
    RuntimeState.IDLE: frozenset({RuntimeState.STARTING}),
    RuntimeState.STARTING: frozenset({RuntimeState.LIVE, RuntimeState.ERROR}),
    RuntimeState.LIVE: frozenset(
        {RuntimeState.PAUSING, RuntimeState.STOPPING, RuntimeState.ERROR}
    ),
    RuntimeState.PAUSING: frozenset({RuntimeState.PAUSED, RuntimeState.ERROR}),
    RuntimeState.PAUSED: frozenset(
        {RuntimeState.RESUMING, RuntimeState.STOPPING, RuntimeState.ERROR}
    ),
    RuntimeState.RESUMING: frozenset({RuntimeState.LIVE, RuntimeState.ERROR}),
    RuntimeState.STOPPING: frozenset({RuntimeState.IDLE, RuntimeState.ERROR}),
    RuntimeState.ERROR: frozenset({RuntimeState.IDLE, RuntimeState.STOPPING}),
}


class RuntimeStateMachine:
    """Own and validate the current live-processing state."""

    def __init__(self) -> None:
        self._state = RuntimeState.IDLE

    @property
    def state(self) -> RuntimeState:
        return self._state

    def can_transition(self, target: RuntimeState) -> bool:
        return target in _ALLOWED_TRANSITIONS[self._state]

    def transition(self, target: RuntimeState) -> RuntimeState:
        if not self.can_transition(target):
            raise InvalidRuntimeTransition(
                f"Cannot change runtime state from {self._state.value} "
                f"to {target.value}."
            )
        self._state = target
        return self._state

