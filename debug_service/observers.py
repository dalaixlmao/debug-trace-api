from __future__ import annotations

import logging
from typing import Protocol


class Observer(Protocol):
    def on_event(self, event: str, payload: dict[str, object]) -> None:
        ...


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Observer] = []

    def subscribe(self, observer: Observer) -> None:
        self._subscribers.append(observer)

    def publish(self, event: str, payload: dict[str, object]) -> None:
        for observer in self._subscribers:
            try:
                observer.on_event(event, payload)
            except Exception:
                logging.exception("observer failed: %s", type(observer).__name__)


class LoggingObserver:
    def on_event(self, event: str, payload: dict[str, object]) -> None:
        logging.info("%s %s", event, payload)
