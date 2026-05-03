from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from debug_service.exceptions import (
    AdapterFailureError,
    DebugTimeoutError,
    DebugTraceError,
    EmptyCodeError,
)
from debug_service.factory import DebugAdapterFactory
from debug_service.models import DebugRequest, DebugStep
from debug_service.observers import EventBus


TIMEOUT_SECONDS = 30.0


class DebugService:
    def __init__(
        self,
        factory: DebugAdapterFactory,
        event_bus: EventBus,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self._factory = factory
        self._event_bus = event_bus
        self._timeout_seconds = timeout_seconds

    def debug(self, request: DebugRequest) -> list[DebugStep]:
        if not request.code.strip():
            raise EmptyCodeError()

        adapter = self._factory.get(request.language)
        self._event_bus.publish("debug.started", {"language": request.language})
        executor: ThreadPoolExecutor | None = None
        try:
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(adapter.debug, request.code)
            return future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError as exc:
            raise DebugTimeoutError(f"exceeded {self._timeout_seconds:g}s") from exc
        except DebugTraceError:
            raise
        except Exception as exc:
            raise AdapterFailureError(str(exc)) from exc
        finally:
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            self._event_bus.publish("debug.finished", {"language": request.language})
