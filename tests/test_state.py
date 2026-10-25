import pytest

from app.core.state import (
    InvalidRuntimeTransition,
    RuntimeState,
    RuntimeStateMachine,
)


def test_normal_live_pause_resume_and_stop_transitions() -> None:
    machine = RuntimeStateMachine()

    assert machine.state is RuntimeState.IDLE
    for state in (
        RuntimeState.STARTING,
        RuntimeState.LIVE,
        RuntimeState.PAUSING,
        RuntimeState.PAUSED,
        RuntimeState.RESUMING,
        RuntimeState.LIVE,
        RuntimeState.STOPPING,
        RuntimeState.IDLE,
    ):
        assert machine.transition(state) is state


def test_invalid_transition_does_not_change_state() -> None:
    machine = RuntimeStateMachine()

    with pytest.raises(InvalidRuntimeTransition, match="idle to paused"):
        machine.transition(RuntimeState.PAUSED)

    assert machine.state is RuntimeState.IDLE


def test_error_can_recover_or_stop_safely() -> None:
    machine = RuntimeStateMachine()
    machine.transition(RuntimeState.STARTING)
    machine.transition(RuntimeState.ERROR)

    assert machine.can_transition(RuntimeState.IDLE)
    assert machine.can_transition(RuntimeState.STOPPING)

