from __future__ import annotations

import pytest

from debug_service.exceptions import IllegalStateTransitionError
from debug_service.models import DebugStep
from debug_service.observers import EventBus
from debug_service.session import DebugSession, State


class CaptureObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def on_event(self, event: str, payload: dict[str, object]) -> None:
        self.events.append((event, payload))


class FakeAdapter:
    def __init__(
        self,
        steps: list[DebugStep] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._steps = steps or [DebugStep(line=1, variables={"x": 1})]
        self._raises = raises

    def debug(self, code: str) -> list[DebugStep]:
        if self._raises is not None:
            raise self._raises
        return self._steps


def test_happy_path_transitions_to_done() -> None:
    session = DebugSession("python", EventBus())

    steps = session.run(FakeAdapter(), "x=1")

    assert steps == [DebugStep(line=1, variables={"x": 1})]
    assert session.state == State.DONE


def test_compiled_language_walks_compile_state() -> None:
    observer = CaptureObserver()
    bus = EventBus()
    bus.subscribe(observer)
    session = DebugSession("go", bus)

    session.run(FakeAdapter(), "package main")

    assert session.state == State.DONE
    assert [event for event, _ in observer.events] == [
        "debug_started",
        "debug_step_captured",
        "debug_completed",
    ]


def test_failure_path_preserves_error_and_emits_failure() -> None:
    observer = CaptureObserver()
    bus = EventBus()
    bus.subscribe(observer)
    error = ValueError("boom")
    session = DebugSession("python", bus)

    with pytest.raises(ValueError):
        session.run(FakeAdapter(raises=error), "x=1")

    assert session.state == State.ERROR
    assert session.error is error
    assert [event for event, _ in observer.events] == [
        "debug_started",
        "debug_failed",
    ]
    assert observer.events[-1][1]["error_type"] == "ValueError"


def test_illegal_transition_raises() -> None:
    session = DebugSession("python", EventBus())

    with pytest.raises(IllegalStateTransitionError):
        session.transition_to(State.STEPPING)


def test_terminal_states_do_not_transition() -> None:
    session = DebugSession("python", EventBus())
    session.run(FakeAdapter(), "x=1")

    with pytest.raises(IllegalStateTransitionError):
        session.transition_to(State.ERROR)
