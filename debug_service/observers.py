from __future__ import annotations

from typing import Protocol


class Observer(Protocol):
    def notify(self, event: str, payload: dict[str, object]) -> None:
        ...


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Observer] = []

    def subscribe(self, observer: Observer) -> None:
        self._subscribers.append(observer)

    def publish(self, event: str, payload: dict[str, object]) -> None:
        for observer in self._subscribers:
            observer.notify(event, payload)


class LoggingObserver:
    def notify(self, event: str, payload: dict[str, object]) -> None:
        return None
