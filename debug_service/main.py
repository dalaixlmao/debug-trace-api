from __future__ import annotations

from fastapi import FastAPI, HTTPException

from debug_service.exceptions import (
    AdapterFailureError,
    CompileError,
    DebugTimeoutError,
    DebugTraceError,
    EmptyCodeError,
    UnsupportedLanguageError,
)
from debug_service.factory import DebugAdapterFactory
from debug_service.models import DebugRequest, DebugStep
from debug_service.observers import EventBus, LoggingObserver
from debug_service.service import DebugService


STATUS_FOR: dict[type[DebugTraceError], int] = {
    UnsupportedLanguageError: 400,
    EmptyCodeError: 400,
    CompileError: 422,
    DebugTimeoutError: 408,
    AdapterFailureError: 500,
}

factory = DebugAdapterFactory()
bus = EventBus()
bus.subscribe(LoggingObserver())
service = DebugService(factory=factory, event_bus=bus)

app = FastAPI()


@app.post("/debug", response_model=list[DebugStep])
def debug_endpoint(req: DebugRequest) -> list[DebugStep]:
    try:
        return service.debug(req)
    except DebugTraceError as exc:
        status_code = STATUS_FOR[type(exc)]
        raise HTTPException(status_code=status_code, detail=exc.payload()) from exc
