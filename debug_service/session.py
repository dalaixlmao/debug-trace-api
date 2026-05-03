from __future__ import annotations

from enum import Enum, auto

from debug_service.exceptions import IllegalStateTransitionError
from debug_service.models import DebugStep
from debug_service.observers import EventBus


class State(Enum):
    IDLE = auto()
    COMPILING = auto()
    LAUNCHING = auto()
    STEPPING = auto()
    DONE = auto()
    ERROR = auto()


_LEGAL: dict[State, set[State]] = {
    State.IDLE: {State.COMPILING, State.LAUNCHING, State.ERROR},
    State.COMPILING: {State.LAUNCHING, State.ERROR},
    State.LAUNCHING: {State.STEPPING, State.ERROR},
    State.STEPPING: {State.DONE, State.ERROR},
    State.DONE: set(),
    State.ERROR: set(),
}

_COMPILED_LANGUAGES = {"go", "cpp", "java"}


class DebugSession:
    def __init__(self, language: str, bus: EventBus) -> None:
        self.language = language
        self._bus = bus
        self._state = State.IDLE
        self.error: Exception | None = None

    @property
    def state(self) -> State:
        return self._state

    def transition_to(self, target: State) -> None:
        if target not in _LEGAL[self._state]:
            raise IllegalStateTransitionError(
                f"illegal transition: {self._state.name} -> {target.name}"
            )
        self._state = target

    def run(self, adapter: object, code: str) -> list[DebugStep]:
        self._bus.publish("debug_started", {"language": self.language})
        try:
            if self.language in _COMPILED_LANGUAGES:
                self.transition_to(State.COMPILING)
                self.transition_to(State.LAUNCHING)
            else:
                self.transition_to(State.LAUNCHING)

            self.transition_to(State.STEPPING)
            steps = adapter.debug(code)
            for step in steps:
                self._bus.publish("debug_step_captured", {"line": step.line})

            self.transition_to(State.DONE)
            self._bus.publish("debug_completed", {"step_count": len(steps)})
            return steps
        except Exception as exc:
            self.error = exc
            self._state = State.ERROR
            self._bus.publish(
                "debug_failed",
                {"error_type": type(exc).__name__, "message": str(exc)},
            )
            raise
