from __future__ import annotations

import logging

import pytest

from debug_service.observers import EventBus, LoggingObserver


class CaptureObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def on_event(self, event: str, payload: dict[str, object]) -> None:
        self.events.append((event, payload))


class BrokenObserver:
    def on_event(self, event: str, payload: dict[str, object]) -> None:
        raise RuntimeError("metrics backend unavailable")


def test_event_bus_notifies_subscribers_in_order() -> None:
    observer = CaptureObserver()
    bus = EventBus()
    bus.subscribe(observer)

    bus.publish("debug_started", {"language": "python"})
    bus.publish("debug_completed", {"step_count": 0})

    assert observer.events == [
        ("debug_started", {"language": "python"}),
        ("debug_completed", {"step_count": 0}),
    ]


def test_observer_failures_do_not_break_publish(caplog: pytest.LogCaptureFixture) -> None:
    observer = CaptureObserver()
    bus = EventBus()
    bus.subscribe(BrokenObserver())
    bus.subscribe(observer)

    with caplog.at_level(logging.ERROR):
        bus.publish("debug_started", {"language": "python"})

    assert observer.events == [("debug_started", {"language": "python"})]
    assert "observer failed: BrokenObserver" in caplog.text


def test_logging_observer_writes_event(caplog: pytest.LogCaptureFixture) -> None:
    observer = LoggingObserver()

    with caplog.at_level(logging.INFO):
        observer.on_event("debug_completed", {"step_count": 2})

    assert "debug_completed" in caplog.text
    assert "step_count" in caplog.text
