---
name: fastapi-pydantic-patterns
description: FastAPI + Pydantic v2 conventions for the HTTP layer of DebugTrace API. Load when working on Story 1 (the /debug endpoint), the Pydantic request/response models, the exception → HTTP status mapping, or dependency injection in main.py.
user-invocable: false
---

# FastAPI + Pydantic v2 Patterns for DebugTrace API

This skill covers `main.py`, `models.py`, and `exceptions.py` — the HTTP layer defined in Story 1 of SUPER_PRD.md. Load it alongside `design-principles` when working on any of these files.

---

## Section 1: The Thin-Endpoint Rule

The FastAPI route does exactly **three things**:

1. Receive the Pydantic-validated `DebugRequest`.
2. Call `DebugService.debug(req)`.
3. Catch typed exceptions and map them to `HTTPException`.

That's the entire route body. No language branching, no adapter calls, no business logic.

```python
# main.py — the complete route handler
@app.post("/debug", response_model=list[DebugStep])
def debug_endpoint(req: DebugRequest):
    try:
        return service.debug(req)
    except DebugTraceError as e:
        raise HTTPException(status_code=STATUS_FOR[type(e)],
                            detail=e.payload())
```

**If you find yourself writing any of the following inside this function, stop:**

| Temptation | Where it actually belongs |
|---|---|
| `if req.language == "python": ...` | `factory.py` — language dispatch is the factory's job |
| `adapter = GoAdapter()` | `factory.py` — concrete adapter instantiation lives only there |
| `if not req.code: raise ...` | `models.py` — `Field(min_length=1)` rejects it before the route runs |
| `logging.info(...)` | `observers.py` — cross-cutting logging goes through the event bus |

The `@app.exception_handler` alternative (registering a handler via decorator) is deliberately avoided here. A single `try/except DebugTraceError` block at the call site is simpler, and the mapping dict (Section 2) keeps it just as declarative. SUPER_PRD §1.4 and §1.5 call this out explicitly.

---

## Section 2: Exception → Status Mapping (Declarative)

Maintain one dict in `main.py`. It is the sole source of truth for which exception class maps to which HTTP status code.

```python
# main.py
from debug_service.exceptions import (
    DebugTraceError,
    UnsupportedLanguageError,
    EmptyCodeError,
    CompileError,
    DebugTimeoutError,
    AdapterFailureError,
)

STATUS_FOR: dict[type[DebugTraceError], int] = {
    UnsupportedLanguageError: 400,
    EmptyCodeError:           400,
    CompileError:             422,
    DebugTimeoutError:        408,
    AdapterFailureError:      500,
}
```

**Adding a new error type:** define the exception class in `exceptions.py`, add one entry to this dict. Done. Never extend the route with an `isinstance` chain — the dict is the extension point.

**The exception hierarchy** (`exceptions.py`):

```python
class DebugTraceError(Exception):
    def payload(self) -> dict: ...      # subclasses override

class UnsupportedLanguageError(DebugTraceError):
    def payload(self): return {"error": "unsupported_language", "detail": str(self)}

class EmptyCodeError(DebugTraceError):
    def payload(self): return {"error": "empty_code", "detail": "code must not be empty"}

class CompileError(DebugTraceError):
    def payload(self):
        import re
        msg = self.args[0] if self.args else ""
        sanitized = re.sub(r"/tmp/\S+/", "<tmp>/", msg)  # strip path leakage
        return {"error": "compile_error", "detail": sanitized}

class DebugTimeoutError(DebugTraceError):
    def payload(self): return {"error": "timeout", "detail": "exceeded request budget"}

class AdapterFailureError(DebugTraceError):
    def payload(self): return {"error": "internal_error", "detail": "debug session failed"}
```

The `payload()` method keeps the route clean — the route never formats error bodies.

---

## Section 3: Pydantic v2 Model Patterns

Use `BaseModel` + `Field` for all validation. Never write manual `__init__` validators for things Pydantic handles natively.

```python
# models.py
from __future__ import annotations
from typing import Any
from enum import Enum
from pydantic import BaseModel, Field


class Language(str, Enum):
    PYTHON     = "python"
    GO         = "go"
    CPP        = "cpp"
    JAVA       = "java"
    JAVASCRIPT = "javascript"


class DebugRequest(BaseModel):
    language: Language            # unknown value → Pydantic 422 automatically
    code: str = Field(min_length=1)  # empty string → Pydantic 422 automatically


class DebugStep(BaseModel):
    line:      int          = Field(ge=0)
    variables: dict[str, Any]
```

### Why `Language(str, Enum)` not plain `Enum`

`str` mixin makes the enum JSON-serialize to its string value (`"python"`, not `"Language.PYTHON"`). Required for `response_model` serialization to work correctly.

### Pydantic's automatic 422 vs. your 400

| Invalid input | Who rejects it | Status |
|---|---|---|
| Missing `language` field | Pydantic (before route runs) | 422 |
| Unknown `language` value (e.g. `"rust"`) | `UnsupportedLanguageError` raised by factory | 400 via `STATUS_FOR` |
| Missing `code` field | Pydantic | 422 |
| `code: ""` (empty string) | `Field(min_length=1)` → Pydantic | 422 |
| `code` is valid but semantically empty | `EmptyCodeError` raised by service | 400 via `STATUS_FOR` |

SUPER_PRD §1.3 AC #3 and AC #4 depend on this split being correct. Don't collapse them — the distinction matters for clients.

### v2 config options (when needed)

```python
class DebugStep(BaseModel):
    model_config = {"populate_by_name": True}  # v2 replaces allow_population_by_field_name
```

Use `model_config` (class attribute, dict), not the v1 inner `class Config`. Mixing them causes silent v2 ignorance of the v1 config.

---

## Section 4: Dependency Injection at Startup

Wire the dependency graph once at module load, before `app` is created. Keep it flat — no `Depends()`, no factory functions, no `@lru_cache`.

```python
# main.py — module-level wiring
from debug_service.factory   import DebugAdapterFactory
from debug_service.observers import EventBus, LoggingObserver
from debug_service.service   import DebugService

factory = DebugAdapterFactory()
bus     = EventBus()
bus.subscribe(LoggingObserver())
service = DebugService(factory=factory, event_bus=bus)

app = FastAPI()

@app.post("/debug", response_model=list[DebugStep])
def debug_endpoint(req: DebugRequest):
    ...
```

**Why not `Depends(get_service)`?** `DebugService` is a singleton — created once, shared for the process lifetime. `Depends` is designed for per-request resources (DB sessions, auth tokens). Using it here adds complexity with no benefit and would be a YAGNI violation per `design-principles`.

**For tests**, swap dependencies by patching the module-level names or by passing a fresh `service` object directly:

```python
# tests/conftest.py
@pytest.fixture
def test_app():
    from debug_service import main as m
    fake_factory = DebugAdapterFactory()
    # inject test doubles here
    m.service = DebugService(factory=fake_factory, event_bus=EventBus())
    yield m.app
    # restore if needed
```

---

## Section 5: Sanitizing Error Details

Never send absolute filesystem paths or raw stack traces to clients. This prevents path leakage (SUPER_PRD §1.5).

The `CompileError.payload()` example from Section 2 covers the compiler stderr case. General rule applied in every `payload()` implementation:

```python
import re

def _sanitize(msg: str) -> str:
    """Strip /tmp/<uuid>/ prefixes from compiler/debugger output."""
    return re.sub(r"/tmp/\S+/", "<tmp>/", msg)
```

What to sanitize:
- Temp directory paths from `tempfile.TemporaryDirectory` (appear in Go/C++/Java compiler output).
- Python tracebacks — `AdapterFailureError.payload()` returns a fixed string, never `traceback.format_exc()`.
- File paths injected by LLDB or Delve into their error messages.

What is safe to return verbatim:
- Compiler error text after path stripping (line numbers, error messages).
- The `DebugTimeoutError` fixed message.

---

## Section 6: What NOT to Use

These FastAPI features are explicitly out of scope for this project (SUPER_PRD §5 Non-Goals). Reaching for any of them is a signal the wrong layer is being modified.

| Feature | Why it's excluded |
|---|---|
| `app.middleware(...)` | Cross-cutting concerns belong in `decorators.py` at the adapter boundary, not at the HTTP boundary |
| `Depends(get_service)` | `DebugService` is a singleton; `Depends` is for per-request resources |
| `BackgroundTasks` | All debug requests are synchronous; no streaming in scope |
| `WebSocket` endpoints | Not in scope (SUPER_PRD §5) |
| Multiple `APIRouter` instances | Single endpoint — one router is unnecessary indirection |
| `app.exception_handler` decorator | The `try/except` + `STATUS_FOR` dict in the route is simpler and sufficient |
| Pydantic v1 `class Config` inner class | This project uses Pydantic v2; use `model_config = {...}` instead |

---

## Section 7: Test Pattern

Use FastAPI's `TestClient` (wraps `httpx`, synchronous). Tests live in `tests/test_endpoint.py`.

```python
# tests/test_endpoint.py
from fastapi.testclient import TestClient
from debug_service.main import app

client = TestClient(app)


def test_python_happy_path():
    r = client.post("/debug", json={"language": "python", "code": "x = 1\n"})
    assert r.status_code == 200
    steps = r.json()
    assert isinstance(steps, list) and len(steps) >= 1
    assert "line" in steps[0] and "variables" in steps[0]


def test_unsupported_language():
    r = client.post("/debug", json={"language": "rust", "code": "fn main(){}"})
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_language"


def test_empty_code():
    r = client.post("/debug", json={"language": "python", "code": ""})
    assert r.status_code in (400, 422)   # Field(min_length=1) makes this 422


def test_missing_code_field():
    r = client.post("/debug", json={"language": "python"})
    assert r.status_code == 422           # Pydantic rejects missing required field


def test_compile_error_go():
    r = client.post("/debug", json={"language": "go",
                                    "code": "package main\nfunc main() { bad }"})
    assert r.status_code == 422
    assert r.json()["error"] == "compile_error"
    assert "<tmp>/" in r.json()["detail"] or "bad" in r.json()["detail"]
```

### Injecting test doubles for timeout / adapter failure

```python
# tests/test_endpoint.py
import time
import pytest
from debug_service.adapters.base import DebugAdapter
from debug_service.models import DebugStep


class _SlowAdapter(DebugAdapter):
    def debug(self, code: str) -> list[DebugStep]:
        time.sleep(60)
        return []


class _CrashingAdapter(DebugAdapter):
    def debug(self, code: str) -> list[DebugStep]:
        raise RuntimeError("unexpected internal failure")


@pytest.fixture
def patched_registry(monkeypatch):
    from debug_service import factory as fmod
    monkeypatch.setitem(fmod._REGISTRY, "slow",     _SlowAdapter)
    monkeypatch.setitem(fmod._REGISTRY, "crashing", _CrashingAdapter)
    # extend Language enum is not needed for registry-only tests


def test_timeout_returns_408(patched_registry):
    # relies on the @with_timeout decorator being set to a short budget in test config
    r = client.post("/debug", json={"language": "slow", "code": "x=1"})
    assert r.status_code == 408


def test_adapter_crash_returns_500(patched_registry):
    r = client.post("/debug", json={"language": "crashing", "code": "x=1"})
    assert r.status_code == 500
    assert r.json()["error"] == "internal_error"
```

`monkeypatch.setitem` on `_REGISTRY` avoids touching the `Language` enum — the factory's `get()` looks up the string key directly, so test-only language strings are sufficient for exercising the endpoint's error-mapping paths.
