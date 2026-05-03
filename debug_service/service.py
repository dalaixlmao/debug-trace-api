from __future__ import annotations

from debug_service.factory import DebugAdapterFactory
from debug_service.models import DebugRequest, DebugStep
from debug_service.observers import EventBus
from debug_service.session import DebugSession


class DebugService:
    def __init__(
        self,
        factory: DebugAdapterFactory,
        event_bus: EventBus,
    ) -> None:
        self._factory = factory
        self._event_bus = event_bus

    def debug(self, request: DebugRequest) -> list[DebugStep]:
        adapter = self._factory.get(request.language)
        session = DebugSession(request.language, self._event_bus)
        return session.run(adapter, request.code)
