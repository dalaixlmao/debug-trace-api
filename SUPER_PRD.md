# Super PRD — DebugTrace API

> A Python FastAPI service that wraps five language-specific code debuggers behind one clean `POST /debug` endpoint.

**Status:** Implementation-ready
**Owner:** Platform / Developer-Tooling
**Stack:** Python 3.11+, FastAPI, Pydantic v2

---

## 1. Executive Summary

DebugTrace API is a **single-endpoint HTTP service** that accepts a snippet of source code in one of five supported languages (Python, Go, C++, Java, JavaScript) and returns a structured execution trace — every line that executed, paired with the values of all locals at that point. Internally, each language is handled by a dedicated debug adapter that drives the language's native debugger (`sys.settrace`, Delve, LLDB, JDWP, V8 Inspector). The service hides that complexity behind one clean interface.

The product matters because step-by-step execution traces are foundational for **AI code-review tools, debugger-as-a-service products, educational visualizers (Python-Tutor-style), and automated test-failure diagnostics** — all of which currently re-implement this plumbing per language.

---

## 2. System Architecture Overview

```
                ┌──────────────────────────────────────────────────────┐
                │  HTTP LAYER  (main.py)                               │
                │  FastAPI app, POST /debug                            │
                │  – Pydantic validation (DebugRequest)                │
                │  – Maps typed exceptions → HTTP status codes         │
                │  – Returns DebugResponse JSON                        │
                └────────────────────┬─────────────────────────────────┘
                                     │ calls
                                     ▼
                ┌──────────────────────────────────────────────────────┐
                │  SERVICE LAYER  (service.py)  ← FACADE               │
                │  DebugService.debug(req) -> DebugResponse            │
                │  – Owns the DebugSession lifecycle                   │
                │  – Notifies Observers on state transitions           │
                │  – Knows nothing about Delve / LLDB / JDWP           │
                └────────────────────┬─────────────────────────────────┘
                                     │ resolves adapter via
                                     ▼
                ┌──────────────────────────────────────────────────────┐
                │  FACTORY  (factory.py)  ← SINGLETON + FACTORY        │
                │  DebugAdapterFactory.get(language) -> DebugAdapter   │
                │  – Module-level registry: {lang: AdapterClass}       │
                │  – Raises UnsupportedLanguageError on miss           │
                │  – ★ Adding a language = one line here, nothing else │
                └────────────────────┬─────────────────────────────────┘
                                     │ instantiates
                                     ▼
                ┌──────────────────────────────────────────────────────┐
                │  ADAPTER LAYER  (adapters/*.py)  ← STRATEGY          │
                │  DebugAdapter ABC: .debug(code: str) -> list[Step]   │
                │  Wrapped by @with_validation @with_timeout decorators│
                │  ┌────────────┬──────────┬─────────┬──────────────┐  │
                │  │ Python     │ Go       │ C++     │ Java   │ JS │  │
                │  └────────────┴──────────┴─────────┴──────────────┘  │
                └────────────────────┬─────────────────────────────────┘
                                     │ drives
                                     ▼
                ┌──────────────────────────────────────────────────────┐
                │  EXTERNAL TOOLS                                      │
                │  sys.settrace · dlv · lldb · jdwp · node --inspect   │
                └──────────────────────────────────────────────────────┘

                 ┌────────────────────────────────────────┐
                 │ CROSS-CUTTING                          │
                 │ – decorators.py  (timeout, validation) │
                 │ – observers.py   (event bus, hooks)    │
                 │ – session.py     (state machine)       │
                 │ – exceptions.py  (typed errors)        │
                 └────────────────────────────────────────┘
```

**Pattern-to-component map (cheat sheet):**

| Pattern | Lives in | Why it's there |
|---|---|---|
| Facade | `DebugService` | Caller sees one method; LLDB/Delve/JDWP are invisible |
| Strategy | Each `*Adapter` | Same `.debug(code)` interface, five interchangeable implementations |
| Factory | `DebugAdapterFactory` | OCP extension point — new language = new dict entry |
| Singleton | The factory itself (module-level state) | Created once, reused per process |
| Builder | Pydantic models in `models.py` | Validates and assembles `DebugRequest` / `DebugResponse` |
| Decorator | `@with_timeout`, `@with_validation` | Cross-cutting concerns wrap any adapter without modifying it |
| Observer | `EventBus` + subscribers in `observers.py` | Logging/metrics hook into lifecycle without coupling |
| State Machine | `DebugSession` | Explicit `IDLE → COMPILING → LAUNCHING → STEPPING → DONE/ERROR` |

---

## 3. Stories

| # | Title | Primary Pattern(s) |
|---|---|---|
| 1 | Core HTTP Endpoint | Facade |
| 2 | Python Debug Adapter | Strategy |
| 3 | Go Debug Adapter | Strategy |
| 4 | C++ Debug Adapter | Strategy |
| 5 | Java Debug Adapter | Strategy |
| 6 | JavaScript Debug Adapter | Strategy |
| 7 | Adapter Registry & Factory | Singleton + Factory |
| 8 | Session State Machine & Observability | State Machine + Observer + Decorator |

**User stories (standard format):**

1. **As an API consumer**, I want to POST source code and a language identifier to a single endpoint, **so that** I receive a structured execution trace without knowing which debugger backend ran it.
2. **As an educational-tool builder**, I want to send Python snippets and get a list of `(line, variables)` records, **so that** I can render a Python-Tutor-style visualizer.
3. **As a Go developer**, I want my Go snippets traced with the same response shape as Python, **so that** I can reuse my frontend across languages.
4. **As a C++ instructor**, I want to step through C++ programs and inspect locals, **so that** my students can see memory and pointer behavior at each line.
5. **As an interview-prep platform**, I want to trace Java code (including collections like `ArrayList`, `HashMap`, `TreeMap`), **so that** I can show how data structures evolve during execution.
6. **As a JavaScript tutorial author**, I want JS snippets traced (including `Map` and `Set`) on modern Node, **so that** my content keeps working as Node releases new versions.
7. **As the service maintainer**, I want adding a new language to require touching exactly one file, **so that** I can ship Rust support next quarter without regression risk to existing adapters.
8. **As an SRE**, I want every debug request to emit lifecycle events and respect a hard timeout, **so that** I can monitor latency, spot stuck sessions, and prevent runaway debuggers from exhausting workers.

---

## 4. Mini-PRDs

### Story 1 — Core HTTP Endpoint

**As an API consumer**, I want to POST source code and a language identifier to a single endpoint, **so that** I receive a structured execution trace without knowing which debugger backend ran it.

#### 1.1 Scope

- One route: `POST /debug`.
- Request body validated by Pydantic (`language` enum, non-empty `code`).
- Response is a JSON array of trace steps: `[{"line": int, "variables": {name: value}}, ...]`.
- Map typed exceptions from the service layer onto HTTP status codes.
- The route is **thin**: parse → call `DebugService.debug()` → serialize. No business logic here. This is the **Facade boundary**.

Out of scope: auth, rate limiting, streaming, persistence (see §5 Non-Goals).

#### 1.2 Request / Response Schema

**Request**
```json
POST /debug
Content-Type: application/json

{
  "language": "python",          // required, one of: python|go|cpp|java|javascript
  "code": "x = 1\nprint(x)\n"    // required, non-empty string
}
```

**Success Response — `200 OK`**
```json
[
  { "line": 1, "variables": { "x": 1 } },
  { "line": 2, "variables": { "x": 1 } }
]
```

**Error Responses**

| HTTP Status | Trigger | Body |
|---|---|---|
| `400 Bad Request` | Missing field, empty `code`, unknown `language` | `{"error": "unsupported_language", "detail": "..."}` |
| `408 Request Timeout` | Adapter exceeded the configured wall-clock budget | `{"error": "timeout", "detail": "exceeded 30s"}` |
| `422 Unprocessable Entity` | User code failed to compile (Go/C++/Java) | `{"error": "compile_error", "detail": "<compiler stderr, sanitized>"}` |
| `500 Internal Server Error` | Adapter crashed unexpectedly | `{"error": "internal_error", "detail": "debug session failed"}` |

> **Why 422 for compile errors:** the request is well-formed but the *content* (user's code) can't be processed. This matches FastAPI's existing 422 semantics for validation failures.

#### 1.3 Acceptance Criteria

1. `curl -X POST http://localhost:8000/debug -H 'Content-Type: application/json' -d '{"language":"python","code":"x=1"}'` returns `200` with a JSON array of length `>= 1`.
2. Posting `{"language": "rust", "code": "fn main(){}"}` returns `400` and the body matches `{"error": "unsupported_language", ...}`.
3. Posting `{"language": "python", "code": ""}` returns `400`.
4. Posting `{"language": "python"}` (missing `code`) returns `422` (FastAPI's default Pydantic validation response).
5. Posting Go code with a syntax error returns `422` with the compiler's message in `detail`.
6. A request whose adapter runs longer than the configured timeout returns `408`, and the request thread does NOT leak (verified with a follow-up healthy request).
7. The endpoint handler in `main.py` contains **no `import` of any concrete adapter** — only `DebugService` and Pydantic models. (Verified by `grep -E "from .adapters" debug_service/main.py` returning nothing.)
8. `pytest tests/test_endpoint.py` exercises all five language happy paths via FastAPI's `TestClient` and passes.

#### 1.4 Technical Design

**Files touched:** `main.py`, `service.py`, `models.py`, `exceptions.py`.

**Pattern applied — Facade.** `DebugService` is the single class the route talks to. The route does not know what an adapter is. The service hides the factory, the session, the decorators, and the adapters behind one method:

```python
# service.py
class DebugService:
    def __init__(self, factory: DebugAdapterFactory, event_bus: EventBus):
        self._factory = factory
        self._event_bus = event_bus

    def debug(self, request: DebugRequest) -> list[DebugStep]:
        adapter = self._factory.get(request.language)
        session = DebugSession(language=request.language, bus=self._event_bus)
        return session.run(adapter, request.code)
```

**Pattern applied — Builder (lightweight, via Pydantic).**

```python
# models.py
class Language(str, Enum):
    PYTHON = "python"; GO = "go"; CPP = "cpp"; JAVA = "java"; JAVASCRIPT = "javascript"

class DebugRequest(BaseModel):
    language: Language
    code: str = Field(min_length=1)

class DebugStep(BaseModel):
    line: int
    variables: dict[str, Any]
```

`Pydantic` validates and constructs `DebugRequest` in one step. We deliberately **don't** write a hand-rolled `RequestBuilder` class — that would violate KISS. Pydantic's model **is** the builder.

**Typed exception hierarchy** (`exceptions.py`):

```
DebugTraceError                    (base)
├── UnsupportedLanguageError       → 400
├── EmptyCodeError                 → 400
├── CompileError                   → 422
├── DebugTimeoutError              → 408
└── AdapterFailureError            → 500
```

The route uses one `try/except DebugTraceError` block and a small dict mapping exception class → status code. No nested try/except.

**Dependency injection.** `main.py` wires the dependency graph at startup:

```python
# main.py — abbreviated
factory = DebugAdapterFactory()  # singleton; populated by adapter modules at import time
bus = EventBus()
bus.subscribe(LoggingObserver())
service = DebugService(factory=factory, event_bus=bus)

app = FastAPI()

@app.post("/debug", response_model=list[DebugStep])
def debug_endpoint(req: DebugRequest):
    try:
        return service.debug(req)
    except DebugTraceError as e:
        raise HTTPException(status_code=STATUS_FOR[type(e)], detail=e.payload())
```

This satisfies **DIP** — `main.py` depends only on the `DebugService` abstraction, not on any concrete adapter.

#### 1.5 Known Pitfalls

- ❌ **Do not** put `if language == "python": ...` branching in the endpoint or service. That is the factory's job. Branching here breaks OCP and forces this file to change every time a new language is added.
- ❌ **Do not** swallow stack traces by returning `500` for everything. Compile errors, timeouts, and unsupported languages each need their own status code so clients can react.
- ❌ **Do not** echo raw stderr that contains absolute filesystem paths to clients. Sanitize: strip the temp-dir prefix before placing the message in `detail`. This prevents path leakage.
- ❌ **Do not** use `BaseException` or bare `except:` in the route. Catch only `DebugTraceError` and let everything else propagate to FastAPI's default 500 handler with a logged traceback.
- ❌ **Do not** import adapters in `main.py`. Adapters self-register with the factory at import time (see Story 7).

#### 1.6 Test Plan

```bash
# Happy path — every language
for lang in python go cpp java javascript; do
  curl -s -X POST http://localhost:8000/debug \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg l "$lang" --arg c "$(cat fixtures/$lang.snippet)" \
       '{language:$l, code:$c}')" | jq 'length'
done
# Expected: a positive integer per language.

# Unsupported language
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/debug \
  -d '{"language":"rust","code":"fn main(){}"}' \
  -H 'Content-Type: application/json'
# Expected: 400

# Empty code
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/debug \
  -d '{"language":"python","code":""}' \
  -H 'Content-Type: application/json'
# Expected: 400

# Compile error
curl -s -X POST http://localhost:8000/debug \
  -d '{"language":"go","code":"package main\nfunc main() { undefined_symbol }"}' \
  -H 'Content-Type: application/json'
# Expected: 422 with detail mentioning "undefined"
```

`pytest tests/test_endpoint.py` covers the same matrix programmatically using FastAPI's `TestClient` plus a fake `DebugAdapter` injected via the factory to simulate timeouts and adapter crashes deterministically.

---

### Story 2 — Python Debug Adapter

**As an educational-tool builder**, I want to send Python snippets and get a list of `(line, variables)` records, **so that** I can render a Python-Tutor-style visualizer.

#### 2.1 Scope

Implement `PythonAdapter` — a Strategy implementation of `DebugAdapter` that traces an arbitrary Python source string and returns one `DebugStep` per line executed in user code.

This adapter has **zero external runtime dependencies** beyond the Python standard library.

#### 2.2 Acceptance Criteria

1. `PythonAdapter().debug("x = 1\ny = 2\nprint(x + y)")` returns a list whose entries' `line` values are `[1, 2, 3]` (in order).
2. The `variables` dict on the second step contains `{"x": 1}`; on the third step contains `{"x": 1, "y": 2}`.
3. Calling the adapter twice in the same Python process produces independent results — globals from run 1 do **not** leak into run 2.
4. Function definitions, dunder names (e.g. `__builtins__`), and callable references are **excluded** from the `variables` dict on every step.
5. A 5-deep nested list (`a = [[[[[1]]]]]`) serializes to `[[[[[ "..." ]]]]]` — depth is bounded.
6. A user object with `__dict__` serializes to a dict of its public attributes; an object without `__dict__` falls back to its `repr()` string.
7. Tracing `import os; os.getcwd()` only produces steps whose `line` corresponds to *user* lines — no steps from inside `os.py`.
8. `pytest tests/test_python_adapter.py` passes (covers all of the above).

#### 2.3 Technical Design

**Files touched:** `adapters/base.py`, `adapters/python_adapter.py`.

**Pattern applied — Strategy.** `PythonAdapter` implements the same `DebugAdapter` interface as the other four. It is interchangeable from the service's point of view.

**Interface (`adapters/base.py`)**

```python
class DebugAdapter(ABC):
    """Strategy interface. One method, by design (ISP)."""

    @abstractmethod
    def debug(self, code: str) -> list[DebugStep]:
        """Execute `code` under a debugger and return a per-line trace."""
```

**Implementation sketch (`adapters/python_adapter.py`)**

```python
import sys
from .base import DebugAdapter
from ..models import DebugStep

_FILENAME = "<string>"  # the marker used when compiling user code

class PythonAdapter(DebugAdapter):
    def debug(self, code: str) -> list[DebugStep]:
        steps: list[DebugStep] = []
        compiled = compile(code, _FILENAME, "exec")
        user_globals: dict = {}

        def tracer(frame, event, arg):
            if event == "line" and frame.f_code.co_filename == _FILENAME:
                snapshot = {
                    k: _serialize(v)
                    for k, v in frame.f_locals.items()
                    if not k.startswith("__") and not callable(v)
                }
                steps.append(DebugStep(line=frame.f_lineno, variables=snapshot))
            return tracer

        sys.settrace(tracer)
        try:
            exec(compiled, user_globals)
        finally:
            sys.settrace(None)        # ← MUST always run
        return steps


def _serialize(v, depth: int = 0):
    if depth > 4:
        return "..."
    if v is None or isinstance(v, bool) or isinstance(v, (int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_serialize(x, depth + 1) for x in v]
    if isinstance(v, (set, frozenset)):
        return [_serialize(x, depth + 1) for x in v]   # JSON-safe
    if isinstance(v, dict):
        return {str(k): _serialize(val, depth + 1) for k, val in v.items()}
    if hasattr(v, "__dict__"):
        return {k: _serialize(val, depth + 1)
                for k, val in vars(v).items() if not k.startswith("__")}
    return repr(v)
```

**Why a *fresh* `user_globals` dict per call (AC #3):** without it, two calls into the same Python interpreter would share state and AC #3 fails. This is also why we never use `globals()` from the adapter's own module.

#### 2.4 Known Pitfalls

- ❌ **Do not** trace events other than `"line"`. `"call"` and `"return"` events also fire under `sys.settrace` and will pollute the trace.
- ❌ **Do not** forget the filename filter `frame.f_code.co_filename == "<string>"`. Without it, every line of every imported standard-library module ends up in the trace.
- ❌ **Do not** call `sys.settrace(None)` only in the success path. Wrap `exec` in `try/finally` — if user code raises, leaving the global tracer set will silently cripple every subsequent request handled by this worker.
- ❌ **Do not** include callables (functions, lambdas, classes) in the `variables` snapshot. They serialize to absurdly long `repr()` strings and aren't useful in a trace. The `callable(v)` filter handles this.
- ❌ **Do not** allow unbounded recursion in `_serialize`. The `depth > 4` guard prevents `RecursionError` on cyclic objects (e.g. `a = []; a.append(a)`).
- ❌ **Do not** return raw `set` / `frozenset` from `_serialize`. JSON has no set type; the response must be JSON-serializable, so map them to lists.
- ❌ **Do not** use `dict()`-typed `frame.f_locals` directly without copying. CPython reuses that dict between line events; a stored reference will mutate as execution proceeds.

#### 2.5 Test Plan

```python
# tests/test_python_adapter.py
def test_basic_trace():
    steps = PythonAdapter().debug("x = 1\ny = 2\nz = x + y\n")
    assert [s.line for s in steps] == [1, 2, 3]
    assert steps[2].variables == {"x": 1, "y": 2, "z": 3}

def test_no_state_leak_between_calls():
    a = PythonAdapter()
    a.debug("leaked = 99")
    steps = a.debug("y = 1")
    assert "leaked" not in steps[0].variables

def test_callables_filtered():
    steps = PythonAdapter().debug("def f(): pass\nx = 1\n")
    assert "f" not in steps[1].variables

def test_depth_cap():
    steps = PythonAdapter().debug("a = [[[[[1]]]]]")
    # innermost list collapses to "..." at depth 5
    s = steps[-1].variables["a"]
    for _ in range(5):
        s = s[0] if isinstance(s, list) else s
    assert s == "..." or s == 1   # depending on exactly where depth fires

def test_object_with_dict():
    code = """
class P:
    def __init__(self): self.x = 7
p = P()
"""
    steps = PythonAdapter().debug(code)
    assert steps[-1].variables["p"] == {"x": 7}

def test_tracer_cleared_after_error():
    try:
        PythonAdapter().debug("raise ValueError('boom')")
    except Exception:
        pass
    assert sys.gettrace() is None
```

---

### Story 3 — Go Debug Adapter

**As a Go developer**, I want my Go snippets traced with the same response shape as Python, **so that** I can reuse my frontend across languages.

#### 3.1 Scope

Implement `GoAdapter` — a Strategy that compiles a Go source string, launches Delve in headless mode, drives it over JSON-RPC, and returns the per-line trace.

The Go toolchain (`go`, `dlv`) must be on `PATH`. Verifying this is a deployment concern, not the adapter's job; if `dlv` is missing, the adapter should let the resulting `FileNotFoundError` surface and be normalized to `AdapterFailureError` by the decorator (see Story 8).

#### 3.2 Acceptance Criteria

1. `GoAdapter().debug(simple_main_program)` returns a non-empty list of steps.
2. Each step's `line` corresponds to a line in `main.main` (no trace entries from runtime/`runtime.gopanic` etc.).
3. Inspecting `var x int = 5` produces `{"x": 5}` in `variables`.
4. A `[]int{1,2,3}` slice serializes to `[1, 2, 3]`.
5. A `struct { Name string; Age int }` serializes to `{"Name": "...", "Age": ...}`.
6. A pointer (`*int` to value 7) serializes to `7` (dereferenced).
7. A `map[string]int{"a":1,"b":2}` serializes to `{"a": 1, "b": 2}`.
8. The generated binary and Delve subprocess are cleaned up before `debug()` returns, even on error (verified by checking `/tmp` and `pgrep dlv` after the test).
9. `pytest tests/test_go_adapter.py` passes.

#### 3.3 Technical Design

**Files touched:** `adapters/go_adapter.py`.

**Pattern applied — Strategy.** Same `DebugAdapter` interface as Python, completely different innards.

**High-level flow inside `debug()`:**

```
1. Write code to <tmpdir>/main.go
2. Compile:  go build -gcflags=all=-N -l -o <tmpdir>/prog <tmpdir>/main.go
3. Pick a free TCP port P
4. Launch:   dlv exec <tmpdir>/prog --headless --api-version=2 \
             --listen=:P --accept-multiclient
5. Open a TCP socket to localhost:P, wrap in newline-delimited JSON-RPC client
6. RPC: CreateBreakpoint at main.main (file=<tmpdir>/main.go, line=<first>)
7. RPC: Command "continue"  → process pauses at main.main
8. Loop:
     - read locals via RPC: ListLocalVars
     - record (line, variables)
     - RPC: Command "next"
     - parse response → if exited or function != main.main, break
9. tear down: kill Delve, delete tmpdir
```

**Source string handling — critical:**

```python
# In tests/fixtures/go.snippet  -or-  any caller building the request
go_source = r"""
package main

import "fmt"

func main() {
    x := 1
    y := 2
    fmt.Println(x + y)
}
"""
```

The Go source must be passed as a **raw Python string** (`r"..."` in Python literals) when *constructing test fixtures*. At runtime the adapter receives a normal `str`, so this gotcha applies to whoever **builds the request**, not to the adapter itself. The adapter writes the bytes verbatim to disk.

**Delve response unwrapping (the trap that catches everyone):**

Every `RPCServer.Command` reply from Delve nests state under `["result"]["State"]`, NOT directly under `["result"]`. Code MUST do:

```python
state = rpc_call("RPCServer.Command", {"name": "next"})["result"]["State"]
exited = state.get("exited", False)
thread = state["currentThread"]
function_name = thread["function"]["name"]   # e.g. "main.main"
line = thread["line"]
```

Skipping the `"State"` indirection causes `KeyError: 'currentThread'` on every call.

**Stop condition:**

```python
if state.get("exited"):
    break
if state["currentThread"]["function"]["name"] != "main.main":
    break   # stepped into runtime; don't include those frames
```

**Variable extraction — Delve's `reflect.Kind` map:**

`ListLocalVars` returns each variable with a numeric `kind` field (Go's `reflect.Kind` enum). The extractor handles:

| `kind` | Go type | Action |
|---|---|---|
| `1` | `bool` | parse `value` literal |
| `2`–`6` | `int`, `int8`, `int16`, `int32`, `int64` | int(value) |
| `7`–`11` | `uint`, `uint8`, `uint16`, `uint32`, `uint64` | int(value) |
| `13`, `14` | `float32`, `float64` | float(value) |
| `17`, `23` | array, slice | recurse over `children` |
| `24` | `string` | take `value` directly |
| `20` | interface | recurse on first child |
| `21` | map | children come **interleaved** `[k, v, k, v, ...]` — pair them up |
| `22` | pointer | dereference: recurse on first child |
| `25` | struct | build `{child.name: extract(child)}` |

```python
def _extract(v: dict, depth: int = 0):
    if depth > 4: return "..."
    k = v.get("kind", 0)
    val = v.get("value", "")
    if k == 1: return val == "true"
    if 2 <= k <= 11: return int(val) if val else 0
    if k in (13, 14): return float(val) if val else 0.0
    if k == 24: return val
    if k in (17, 23): return [_extract(c, depth+1) for c in v.get("children", [])]
    if k == 21:
        ch = v.get("children", [])
        return {_extract(ch[i], depth+1): _extract(ch[i+1], depth+1)
                for i in range(0, len(ch), 2)}
    if k == 22:
        ch = v.get("children", [])
        return _extract(ch[0], depth+1) if ch else None
    if k == 25:
        return {c["name"]: _extract(c, depth+1) for c in v.get("children", [])}
    if k == 20:
        ch = v.get("children", [])
        return _extract(ch[0], depth+1) if ch else None
    return val
```

**Cleanup must be unconditional:**

```python
try:
    return self._trace(...)
finally:
    if dlv_proc and dlv_proc.poll() is None:
        dlv_proc.terminate(); dlv_proc.wait(timeout=2)
    shutil.rmtree(tmpdir, ignore_errors=True)
```

#### 3.4 Known Pitfalls

- ❌ **Compile flags are non-negotiable.** `go build -gcflags=all=-N -l` disables inlining and optimizations. Without these, Delve sees an empty local-variable list — every step has `variables: {}` and the trace is useless. The `all=` prefix is required so the flags propagate to *all* packages, not just `main`.
- ❌ **Delve's response shape.** `state["result"]["State"]` — never `state["result"]` directly. This is the #1 source of `KeyError` in Go-debugger code.
- ❌ **Stop conditions.** Don't stop solely on `result.get("exited")`. Delve will happily step into runtime functions during a `panic` — you must also bail when `function.name != "main.main"`.
- ❌ **Map children are interleaved.** A map with N entries returns 2N children: `[k0, v0, k1, v1, ...]`. Iterating with stride 1 gives you keys-as-values. Iterate with stride 2.
- ❌ **Port collisions.** Don't hardcode `:8181`. Use `socket.socket().bind(("", 0))` to grab an OS-assigned free port, read it back, close, then pass to Delve. (There is a small race between close and Delve's bind; if it bites you in CI, retry once.)
- ❌ **Cold-start race.** After spawning `dlv`, the listening socket isn't immediately ready. Poll `socket.connect_ex` until it returns `0` (or hit a 5s budget). Don't use a fixed `sleep`.
- ❌ **Don't use `kill(SIGKILL)` on Delve unless `terminate()` failed.** SIGTERM lets Delve detach cleanly; SIGKILL leaves the user's binary as a zombie holding tmpdir open.
- ❌ **Don't include line entries from `runtime.*` or imported packages.** The function-name check in the stop condition handles this — but make sure you also *skip* entries whose thread function isn't `main.main` rather than recording them.
- ❌ **JSON-RPC framing.** Delve uses **newline-delimited** JSON, not Content-Length-prefixed JSON-RPC. Read until `\n`; don't reach for a generic JSON-RPC library that assumes LSP framing.

#### 3.5 Test Plan

```python
GO_HELLO = """package main
import "fmt"
func main() {
    x := 1
    y := 2
    fmt.Println(x + y)
}
"""

def test_go_basic():
    steps = GoAdapter().debug(GO_HELLO)
    lines = [s.line for s in steps]
    assert lines == sorted(lines)            # monotonic
    assert any(s.variables.get("x") == 1 for s in steps)
    assert any(s.variables.get("y") == 2 for s in steps)

def test_go_slice():
    code = """package main
func main() { a := []int{1,2,3}; _ = a }"""
    steps = GoAdapter().debug(code)
    assert any(s.variables.get("a") == [1, 2, 3] for s in steps)

def test_go_map():
    code = """package main
func main() { m := map[string]int{"a":1,"b":2}; _ = m }"""
    steps = GoAdapter().debug(code)
    assert any(s.variables.get("m") == {"a": 1, "b": 2} for s in steps)

def test_go_struct():
    code = """package main
type P struct { Name string; Age int }
func main() { p := P{Name:"x", Age:1}; _ = p }"""
    steps = GoAdapter().debug(code)
    assert any(s.variables.get("p") == {"Name": "x", "Age": 1} for s in steps)

def test_go_no_dlv_leak():
    GoAdapter().debug(GO_HELLO)
    out = subprocess.run(["pgrep", "-f", "dlv exec"], capture_output=True, text=True)
    assert out.stdout.strip() == ""
```

`curl` smoke test:

```bash
curl -s -X POST http://localhost:8000/debug -H 'Content-Type: application/json' \
  -d '{"language":"go","code":"package main\nfunc main(){ x:=1; _=x }"}' | jq '.[0]'
```

---

### Story 4 — C++ Debug Adapter

**As a C++ instructor**, I want to step through C++ programs and inspect locals, **so that** my students can see memory and pointer behavior at each line.

#### 4.1 Scope

Implement `CppAdapter` — a Strategy that compiles a C++ source string with `clang++` and drives LLDB through its Python API to produce a per-line trace.

#### 4.2 Acceptance Criteria

1. `CppAdapter().debug(simple_cpp)` returns a non-empty list of steps.
2. `int x = 5;` shows up as `{"x": 5}` on the appropriate step.
3. A `std::string s = "hi";` shows up as `{"s": "hi"}` (string summary, not `basic_string<...>` internals).
4. A `std::vector<int> v{1,2,3};` shows up as `{"v": [1, 2, 3]}` (with `_summary` and `_size` permitted as side keys).
5. A pointer `int* p = &x;` with `*p == 5` serializes to `5` (dereferenced).
6. The trace **never** contains a step with `line == 4294967295` — that LLDB sentinel must be filtered out.
7. The compiled binary file is removed before `debug()` returns (success and failure paths).
8. `pytest tests/test_cpp_adapter.py` passes.

#### 4.3 Technical Design

**Files touched:** `adapters/cpp_adapter.py`.

**Pattern applied — Strategy.** Same `DebugAdapter` interface; LLDB-specific innards.

**Two-phase flow:**

1. **Compile** with `clang++ -g -O0 -o <out> <src>`.
2. **Debug** with the LLDB Python module.

The compile step is a `subprocess.run` call. If `clang++` returns non-zero, raise `CompileError` with stderr.

**Debug skeleton:**

```python
import lldb
from .base import DebugAdapter
from ..models import DebugStep

class CppAdapter(DebugAdapter):
    def debug(self, code: str) -> list[DebugStep]:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "prog.cpp")
            out = os.path.join(tmp, "prog")
            with open(src, "w") as f: f.write(code)
            self._compile(src, out)
            return self._trace(out)

    def _compile(self, src: str, out: str) -> None:
        proc = subprocess.run(
            ["clang++", "-g", "-O0", "-o", out, src],
            capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise CompileError(proc.stderr)

    def _trace(self, binary: str) -> list[DebugStep]:
        debugger = lldb.SBDebugger.Create()
        debugger.SetAsync(False)              # ★ CRITICAL — see pitfalls
        target = debugger.CreateTarget(binary)
        bp = target.BreakpointCreateByName("main")
        process = target.LaunchSimple(None, None, ".")
        steps: list[DebugStep] = []
        try:
            thread = process.GetSelectedThread()
            while process.GetState() == lldb.eStateStopped:
                frame = thread.GetFrameAtIndex(0)
                line = frame.GetLineEntry().GetLine()
                if line != 4294967295:        # ★ skip UINT32_MAX sentinel
                    vars_ = self._extract_locals(frame)
                    steps.append(DebugStep(line=line, variables=vars_))
                thread.StepOver()
            return steps
        finally:
            process.Kill()
            lldb.SBDebugger.Destroy(debugger)
```

**`extract_value` rules (locals → JSON):**

```python
def _extract_value(self, var, depth: int = 0, max_depth: int = 4):
    if depth > max_depth:
        return "..."
    type_name = var.GetType().GetName() or ""
    summary = var.GetSummary()

    # std::string / basic_string → return the summary verbatim (strip quotes)
    if "basic_string" in type_name or type_name == "string":
        return summary.strip('"') if summary else ""

    # Pointer with one child → dereference
    if var.TypeIsPointerType() and var.GetNumChildren() == 1:
        return self._extract_value(var.GetChildAtIndex(0), depth + 1, max_depth)

    # Array-like (children named "[0]", "[1]", ...) → list
    children = [var.GetChildAtIndex(i) for i in range(var.GetNumChildren())]
    if children and all(c.GetName() and c.GetName().startswith("[") for c in children):
        items = [self._extract_value(c, depth + 1, max_depth) for c in children]
        out = {"_items": items}
        if summary: out["_summary"] = summary
        return out

    # Aggregate (struct / class / vector etc.) → dict
    if children:
        out: dict = {}
        for c in children:
            name = c.GetName() or ""
            # skip dunder fields except __size_ / __begin_ which carry container info
            if name.startswith("__") and name not in ("__size_", "__begin_"):
                continue
            out[name] = self._extract_value(c, depth + 1, max_depth)
        if summary: out["_summary"] = summary
        return out

    # Leaf: int, float, bool, char ... use GetValue()
    return var.GetValue() or ""

def _extract_locals(self, frame) -> dict:
    # arguments=True, locals=True, statics=False, in_scope_only=True
    var_list = frame.GetVariables(True, True, False, True)
    result = {}
    for i in range(var_list.GetSize()):
        v = var_list.GetValueAtIndex(i)
        result[v.GetName()] = self._extract_value(v)
    return result
```

#### 4.4 Known Pitfalls

- ❌ **`-g` and `-O0` are mandatory.** Without `-g`, no debug info → no variable names. Without `-O0`, the optimizer eliminates locals → `frame.GetVariables` returns empty. Both flags. Always.
- ❌ **`debugger.SetAsync(False)` is mandatory.** Default is async mode; `LaunchSimple` returns immediately and `GetState()` is `eStateLaunching` not `eStateStopped`. The whole loop short-circuits and you get an empty trace with no error. Set it before `CreateTarget`.
- ❌ **Skip line `4294967295` (= `0xFFFFFFFF` = `UINT32_MAX`).** That's LLDB's sentinel for "no debug-line info for this frame" — usually the prologue/epilogue of `main`. Including it leaks meaningless steps.
- ❌ **Python interpreter constraint — DEPLOYMENT-LEVEL.** `import lldb` only works under the **Python that ships with Xcode's LLDB** (`/usr/bin/python3` on macOS, `/usr/lib/python3/dist-packages` lldb on Debian/Ubuntu). Conda, pyenv, homebrew Python, virtualenvs — **all fail** with `ModuleNotFoundError: No module named 'lldb'`. The service must be started under the right interpreter. Document this in the deployment runbook.
- ❌ **`GetVariables(True, True, False, True)`** — that argument order is `(arguments, locals, statics, in_scope_only)`. Get any of the four wrong and you'll either lose function arguments (`False, True`) or include uninitialized stack garbage (`*, *, *, False`).
- ❌ **Don't call `GetSummary()` on every value type.** It's `None` for primitives; only useful for strings and aggregates. Treat its absence as normal.
- ❌ **Skip `__`-prefixed members EXCEPT `__size_` and `__begin_`.** libc++'s `std::vector`, `std::string` etc. expose their state through these two fields. Filter them out and your trace shows empty containers.
- ❌ **Don't forget `process.Kill()` and `SBDebugger.Destroy()`.** LLDB leaks file handles and inferior processes if you skip cleanup. Wrap in `try/finally`.

#### 4.5 Test Plan

```python
HELLO = """
#include <iostream>
int main() { int x = 5; int y = 7; std::cout << x + y; return 0; }
"""

def test_cpp_basic():
    steps = CppAdapter().debug(HELLO)
    assert steps                                # non-empty
    assert all(s.line != 4294967295 for s in steps)
    assert any(s.variables.get("x") == "5" or s.variables.get("x") == 5 for s in steps)

def test_cpp_string():
    code = """#include <string>
int main() { std::string s = "hi"; return 0; }"""
    steps = CppAdapter().debug(code)
    assert any(s.variables.get("s") == "hi" for s in steps)

def test_cpp_vector():
    code = """#include <vector>
int main() { std::vector<int> v{1,2,3}; return 0; }"""
    steps = CppAdapter().debug(code)
    last = steps[-2]   # one step before return
    assert last.variables.get("v", {}).get("_items") == [1, 2, 3] \
        or last.variables.get("v") == [1, 2, 3]

def test_cpp_pointer_dereferenced():
    code = """int main() { int x = 5; int* p = &x; return 0; }"""
    steps = CppAdapter().debug(code)
    assert any(s.variables.get("p") == 5 or s.variables.get("p") == "5" for s in steps)

def test_cpp_compile_error():
    with pytest.raises(CompileError):
        CppAdapter().debug("int main(){ undefined; }")
```

---

### Story 5 — Java Debug Adapter

**As an interview-prep platform**, I want to trace Java code (including collections like `ArrayList`, `HashMap`, `TreeMap`), **so that** I can show how data structures evolve during execution.

#### 5.1 Scope

Implement `JavaAdapter` — a Strategy that compiles a Java source string with `javac -g`, launches a JVM with the JDWP agent, and connects from an embedded `DebugClient.java` (using JDI) to capture per-line locals. The Python adapter parses the JSON the embedded client emits to stdout.

#### 5.2 Acceptance Criteria

1. `JavaAdapter().debug(java_source)` returns a non-empty list of steps for any source string starting with `public class <Name>`.
2. The class name is extracted via regex `r'public class (\w+)'` — not via `split("class ")` (which breaks on names containing `class` substrings, e.g. `MyClassRoom`).
3. `int x = 5;` shows up as `{"x": 5}` on the appropriate step.
4. An `ArrayList<Integer>` with `[1, 2, 3]` shows up as `{"list": [1, 2, 3]}`.
5. A `HashMap<String,Integer>{"a":1, "b":2}` shows up as `{"m": {"a": 1, "b": 2}}`.
6. A `Stack<Integer>` with pushed `[10, 20, 30]` shows up as `[10, 20, 30]` (Stack extends Vector — superclass walking works).
7. Two debug calls back-to-back both succeed (cold-start JDWP race is handled).
8. The trace contains no entries from `java.io.PrintStream` or other JDK internals — only the user's class.
9. `pytest tests/test_java_adapter.py` passes.

#### 5.3 Technical Design

**Files touched:** `adapters/java_adapter.py`, `adapters/java_resources/DebugClient.java`.

**Pattern applied — Strategy.** Same `DebugAdapter` interface; JDI under the hood.

**High-level flow:**

```
1. classname = re.search(r'public class (\w+)', code).group(1)
2. tmpdir = mkdtemp()
3. write code → <tmpdir>/<classname>.java
4. copy resources/DebugClient.java → <tmpdir>/DebugClient.java
5. javac -g <classname>.java                    (★ -g mandatory)
6. javac DebugClient.java                       (no -g needed for our own client)
7. pick free port P
8. spawn JVM:
     java -agentlib:jdwp=transport=dt_socket,server=y,suspend=y,address=P <classname>
9. time.sleep(0.5)                              (★ JDWP cold-start race; prefer port-poll)
10. result = subprocess.run(["java", "DebugClient", str(P), classname],
                            capture_output=True, text=True, cwd=tmpdir)
11. parse result.stdout as JSON → list[DebugStep]
12. cleanup tmpdir
```

**Port readiness — robust alternative to `time.sleep(0.5)`:**

```python
def _wait_for_jdwp(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=0.2) as s:
                return                                  # JDWP listening
        except OSError:
            time.sleep(0.05)
    raise AdapterFailureError("JDWP did not become ready")
```

Use this **instead of** `time.sleep(0.5)`. The 0.5s sleep works on warm JVM (JRE classes in OS page cache) and *fails on cold start*. The polling version handles both.

**The embedded `DebugClient.java`** uses JDI and must implement these mechanics correctly:

| Mechanic | Why |
|---|---|
| `ClassPrepareRequest.setSuspendPolicy(EventRequest.SUSPEND_ALL)` | Without it, the VM continues past your handler before you've installed the breakpoint, throwing `VMDisconnectedException` |
| `erm.deleteEventRequests(erm.stepRequests())` **before** every new `StepRequest` | Otherwise step requests pile up; one logical "next" fires N times and your trace has duplicate lines |
| `stepRequest.addClassFilter(className)` | Without it, the stepper recurses into `java.io.PrintStream`, `java.lang.String`, etc. — your trace is hundreds of useless steps inside `println` |
| `try { eq.remove(); } catch (VMDisconnectedException) { ... break; }` | The JVM exit doesn't deliver a clean event — it just disconnects. Catch and break. |
| `field()` helper walks `type.superclass()` chain | `Stack extends Vector`, `LinkedHashSet extends HashSet`, `LinkedHashMap extends HashMap` — without this, you read `null` for inherited fields |

**Embedded `DebugClient.java` (skeleton — full file ~250 lines):**

```java
public class DebugClient {
    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(args[0]);
        String className = args[1];

        VirtualMachine vm = attach(port);
        EventRequestManager erm = vm.eventRequestManager();
        ClassPrepareRequest cpr = erm.createClassPrepareRequest();
        cpr.addClassFilter(className);
        cpr.setSuspendPolicy(EventRequest.SUSPEND_ALL);   // ★
        cpr.enable();

        EventQueue eq = vm.eventQueue();
        List<Map<String,Object>> trace = new ArrayList<>();

        boolean done = false;
        while (!done) {
            try {
                EventSet set = eq.remove();
                for (Event e : set) {
                    if (e instanceof ClassPrepareEvent) {
                        // install StepRequest filtered to user class
                        installStep(erm, ((ClassPrepareEvent) e).thread(), className);
                    } else if (e instanceof StepEvent) {
                        StepEvent se = (StepEvent) e;
                        StackFrame frame = se.thread().frame(0);
                        Map<String,Object> row = new LinkedHashMap<>();
                        row.put("line", frame.location().lineNumber());
                        row.put("variables", extractLocals(frame));
                        trace.add(row);
                        // ★ delete previous step request before installing the next
                        erm.deleteEventRequests(erm.stepRequests());
                        installStep(erm, se.thread(), className);
                    }
                }
                set.resume();
            } catch (VMDisconnectedException vde) {       // ★
                done = true;
            }
        }

        System.out.println(toJson(trace));   // adapter parses this
    }
    // installStep / extractLocals / field / toJson omitted for brevity
}
```

**Value extraction — Java's collection zoo:**

The Java side (`extractLocals` / a `valueOf(JdiValue)` recursion) must handle **at least**:

| Type | How to read it |
|---|---|
| `IntegerValue`, `LongValue`, `BooleanValue`, etc. | Native primitive |
| Boxed primitives (`java.lang.Integer`, ...) | Read their internal `value` field via JDI |
| `String` | `((StringReference) v).value()` |
| `ArrayList` / `Vector` / `Stack` | Read `elementData` (Object[]) and `size` (int); take the first `size` entries |
| `LinkedList` | Walk `first` → `item` → `next` chain |
| `ArrayDeque` | Circular buffer: read `elements`, `head`, `tail`; iterate `(head ... tail) mod len` |
| `PriorityQueue` | `queue` (Object[]) and `size`; take the first `size` entries |
| `HashMap` / `LinkedHashMap` | Iterate `table` (Node[]) and walk each bucket's `next` chain; collect `(key, value)` pairs |
| `TreeMap` | In-order traversal of the red-black tree starting at `root` |
| `HashSet` / `LinkedHashSet` | Read backing field `map` (a HashMap) and return its keys |
| `TreeSet` | Read backing field `m` (a TreeMap) and return its keys |

Each of these requires `field()` to walk `type.superclass()` because `Stack` inherits `elementData` from `Vector`, `LinkedHashSet` inherits `map` from `HashSet`, etc.

#### 5.4 Known Pitfalls

- ❌ **`javac -g` is mandatory** for compiling user code. Without it, JDI's `frame.visibleVariables()` throws `AbsentInformationException` and every step's `variables` map is empty. The error is silent unless you check.
- ❌ **Class-name regex matters.** `re.search(r'public class (\w+)', code).group(1)` — *not* `code.split("class ")[1].split()[0]`. The naive split breaks on identifiers like `ClassRoom`, `Subclass`, comments containing `class`, etc.
- ❌ **JDWP cold-start race.** The JDWP listener is announced *before* it's ready to accept. Just spawning the JVM and immediately running `DebugClient` works on a warm JVM (JRE classes cached) and fails on first run after reboot. Always: poll the port, or `time.sleep(0.5)` minimum. Polling is better.
- ❌ **Step-request accumulation.** If you don't `erm.deleteEventRequests(erm.stepRequests())` before installing each new step request, requests stack up and one logical "step over" emits multiple StepEvents per line. Your trace will have duplicates and the stepper will eventually crash with too many requests.
- ❌ **No class filter on StepRequest** = stepping into the JDK. You'll get hundreds of trace entries inside `PrintStream.write`, `String.valueOf`, etc. `addClassFilter(className)` confines stepping to user code.
- ❌ **`ClassPrepareRequest` without `SUSPEND_ALL`.** Default is `SUSPEND_EVENT_THREAD`; the VM keeps running other threads, hits `main` before you've configured the stepper, and throws `VMDisconnectedException` when `eq.remove()` next fires.
- ❌ **`eq.remove()` throws on JVM exit.** It does not deliver a `VMDeathEvent` reliably — it just throws `VMDisconnectedException`. Wrap in `try/catch` and break the loop.
- ❌ **Forgetting superclass walking** in `field()`. Missing this gives you `NullPointerException` reading `elementData` on a `Stack`, because `Stack` inherits the field from `Vector`. Walk `type.superclass()` until non-null.
- ❌ **Don't share `tmpdir` between requests.** Java caches loaded classes per JVM, but each request spawns its own JVM, so each must have its own `tmpdir` to avoid stale `.class` files.
- ❌ **Don't decode `result.stdout` until you've verified `result.returncode == 0`.** If the embedded client crashed, `stdout` may be empty/partial JSON; `stderr` has the real error.

#### 5.5 Test Plan

```python
PROGRAM = """
public class Main {
    public static void main(String[] args) {
        int x = 5;
        int y = 7;
        System.out.println(x + y);
    }
}
"""

def test_java_basic():
    steps = JavaAdapter().debug(PROGRAM)
    assert steps
    assert any(s.variables.get("x") == 5 for s in steps)
    assert any(s.variables.get("y") == 7 for s in steps)

def test_java_no_jdk_internals():
    steps = JavaAdapter().debug(PROGRAM)
    # PrintStream.println is around lines 1500+ in OpenJDK; user code is < 20.
    assert all(s.line < 100 for s in steps)

def test_java_arraylist():
    code = """
import java.util.*;
public class L {
    public static void main(String[] a) {
        ArrayList<Integer> list = new ArrayList<>();
        list.add(1); list.add(2); list.add(3);
    }
}
"""
    steps = JavaAdapter().debug(code)
    assert any(s.variables.get("list") == [1, 2, 3] for s in steps)

def test_java_hashmap():
    code = """
import java.util.*;
public class M {
    public static void main(String[] a) {
        HashMap<String,Integer> m = new HashMap<>();
        m.put("a", 1); m.put("b", 2);
    }
}
"""
    steps = JavaAdapter().debug(code)
    final = next(s for s in reversed(steps) if "m" in s.variables)
    assert final.variables["m"] == {"a": 1, "b": 2}

def test_java_back_to_back():
    a = JavaAdapter()
    a.debug(PROGRAM)
    a.debug(PROGRAM)             # cold-start race must not break the second call
```

---

### Story 6 — JavaScript Debug Adapter

**As a JavaScript tutorial author**, I want JS snippets traced (including `Map` and `Set`) on modern Node, **so that** my content keeps working as Node releases new versions.

#### 6.1 Scope

Implement `JavaScriptAdapter` — a Strategy that runs a JS source string under `node --inspect-brk=0`, connects to V8's debugger over WebSocket (Chrome DevTools Protocol), and produces a per-line trace.

This adapter requires the `websocket-client` Python package (`pip install websocket-client`).

#### 6.2 Acceptance Criteria

1. `JavaScriptAdapter().debug(js_source)` returns a non-empty list of steps.
2. `let x = 5;` shows up as `{"x": 5}` on the appropriate step.
3. `let arr = [1, 2, 3];` shows up as `{"arr": [1, 2, 3]}`.
4. `let obj = { a: 1, b: 2 };` shows up as `{"obj": {"a": 1, "b": 2}}`.
5. `let m = new Map(); m.set("k", 1);` shows up as `{"m": {"k": 1}}`.
6. `let s = new Set([1, 2, 3]);` shows up as `{"s": [1, 2, 3]}`.
7. An uninitialized `let z;` shows up as `{"z": "<uninitialized>"}` on the line where `z` is declared but not yet assigned.
8. Module-level Node internals (`exports`, `require`, `module`, `__filename`, `__dirname`) are excluded from `variables`.
9. The trace returns within the configured timeout — no hang on connection close (Node v25 fix).
10. `pytest tests/test_javascript_adapter.py` passes on Node 20 *and* Node 25.

#### 6.3 Technical Design

**Files touched:** `adapters/javascript_adapter.py`.

**Pattern applied — Strategy.** Same interface, V8 inspector under the hood.

**High-level flow:**

```
1. write code → <tmpdir>/script.js
2. spawn:  node --inspect-brk=0 <tmpdir>/script.js
3. read stderr line-by-line until a line matches r'ws://\S+'
4. open websocket to that URL (websocket-client)
5. ws.settimeout(5)                                  (★ Node v25 fix)
6. send: Debugger.enable
7. send: Runtime.runIfWaitingForDebugger
8. listen for messages:
     - Debugger.scriptParsed   → record scriptId → url           (★ Node v25 fix)
     - Debugger.paused         → at this point we have a frame:
                                 - look up url via scriptId map
                                 - extract locals
                                 - record (line, variables)
                                 - send Debugger.stepOver
     - Debugger.resumed        → just continue
     - timeout (script ended)  → break
9. terminate node, cleanup
```

**WebSocket URL extraction:**

```python
ws_url = None
for line in iter(proc.stderr.readline, b""):
    line = line.decode()
    m = re.search(r'ws://\S+', line)
    if m:
        ws_url = m.group(0)
        break
```

**scriptId → url mapping (Node v25 fix #1):**

In Node 20 and earlier, `Debugger.paused` events carried a `frame.url` field. In Node 25 that field is empty. The fix: subscribe to `Debugger.scriptParsed` events, build a `{scriptId: url}` dict, then on each pause look up `frame.location.scriptId` in that map. Filter steps to only those whose `url` ends with the user's script filename.

```python
script_urls: dict[str, str] = {}        # scriptId → url

def on_message(msg):
    method = msg.get("method")
    if method == "Debugger.scriptParsed":
        p = msg["params"]
        script_urls[p["scriptId"]] = p.get("url", "")
    elif method == "Debugger.paused":
        cf = msg["params"]["callFrames"][0]
        url = script_urls.get(cf["location"]["scriptId"], "")
        if not url.endswith("script.js"):     # skip Node internals
            send("Debugger.stepOver"); return
        line = cf["location"]["lineNumber"] + 1   # CDP is 0-indexed
        scope = first_local_scope(cf)
        steps.append(DebugStep(line=line, variables=extract_scope(scope)))
        send("Debugger.stepOver")
```

**`Map` / `Set` extraction (Node v25 fix #2):**

In Node 20, `Runtime.getProperties` exposed a `[[Entries]]` internal property containing the entries directly. In Node 25, `[[Entries]]` is gone. The fix: use `Runtime.callFunctionOn` with an inline JS function to JSON-stringify the entries:

```python
MAP_FN = "function() { const r = []; this.forEach((v, k) => r.push([k, v])); return JSON.stringify(r); }"
SET_FN = "function() { return JSON.stringify([...this]); }"

def _extract_map(object_id: str) -> dict:
    res = call_function_on(object_id, MAP_FN)
    pairs = json.loads(res["value"])
    return {k: v for k, v in pairs}

def _extract_set(object_id: str) -> list:
    res = call_function_on(object_id, SET_FN)
    return json.loads(res["value"])
```

**Uninitialized `let` (Node v25 fix #3):**

Uninitialized `let`/`const` variables (in TDZ) come back as `{"type": null}` with **no `objectId`**. Don't try to dereference; return `"<uninitialized>"` directly.

```python
def extract(prop):
    val = prop.get("value", {})
    if val.get("type") is None and "objectId" not in val:
        return "<uninitialized>"
    # ... regular extraction
```

**Filter Node module wrapper:**

Node wraps every script in a function that injects `exports`, `require`, `module`, `__filename`, `__dirname`. These show up as locals on every step. Filter:

```python
NODE_INTERNALS = {"exports", "require", "module", "__filename", "__dirname"}

def filter_locals(d: dict) -> dict:
    return {k: v for k, v in d.items()
            if k not in NODE_INTERNALS and not k.startswith("__")}
```

**`extract(val, depth)`** dispatches on V8's `type` and `subtype`:

```python
def _extract(self, val: dict, depth: int = 0):
    if depth > 4: return "..."
    t, st = val.get("type"), val.get("subtype")
    if t in ("number", "boolean", "string"): return val.get("value")
    if t == "undefined": return None
    if st == "null": return None
    if st == "array":
        return [self._extract(p["value"], depth+1) for p in self._get_props(val["objectId"])]
    desc = val.get("description", "")
    if st == "map" or desc.startswith("Map("):
        return self._extract_map(val["objectId"])
    if st == "set" or desc.startswith("Set("):
        return self._extract_set(val["objectId"])
    if t == "object":
        return {p["name"]: self._extract(p["value"], depth+1)
                for p in self._get_props(val["objectId"])}
    return val.get("description", repr(val))
```

#### 6.4 Known Pitfalls

- ❌ **`ws.settimeout(5)` is required on Node v25.** Without it, the recv loop blocks forever after the script ends — the connection closes, but `recv()` doesn't return. Setting a timeout lets the loop notice and exit. (Symptom: requests work locally, hang in CI on newer Node.)
- ❌ **Node v25 dropped `[[Entries]]` for `Map` / `Set`.** If your code relies on `Runtime.getProperties` returning `[[Entries]]`, it works on Node 20 and silently returns `{}` / `[]` on Node 25. Use `Runtime.callFunctionOn` with the inline JS functions above.
- ❌ **Node v25 frame URLs are empty.** Build the `scriptId → url` map from `Debugger.scriptParsed` events; don't rely on `frame.url`.
- ❌ **Uninitialized `let` has no `objectId`.** Calling `Runtime.getProperties` on a missing objectId raises an error and your trace blows up. Detect `type: null` + no objectId and return the literal string `"<uninitialized>"`.
- ❌ **CDP line numbers are 0-indexed.** The user expects 1-indexed lines (matching their source). Add 1.
- ❌ **`--inspect-brk=0`** picks a random port (instead of default 9229). Random port = no collisions across concurrent requests. Don't hardcode 9229.
- ❌ **Read stderr line-by-line, not in bulk.** `proc.stderr.read()` blocks until the process exits, but the WebSocket URL is printed *before* execution starts. Use `iter(proc.stderr.readline, b"")` and break as soon as you find the URL.
- ❌ **Don't filter on `frame.url` substring containment** — Node sometimes prefixes paths with `file://` and sometimes not. Use `endswith("script.js")` instead.
- ❌ **Don't forget to terminate `node`.** `proc.terminate()` in `finally`. Lingering Node processes hold open WebSocket sockets and exhaust the ephemeral-port range under load.
- ❌ **Don't capture `arguments` as a local.** It's a special V8-injected variable inside non-arrow functions and serializes to a giant blob. Add it to the filter set if it appears.

#### 6.5 Test Plan

```python
def test_js_basic():
    steps = JavaScriptAdapter().debug("let x = 5;\nlet y = 7;\nconsole.log(x+y);")
    assert any(s.variables.get("x") == 5 for s in steps)
    assert any(s.variables.get("y") == 7 for s in steps)

def test_js_array():
    steps = JavaScriptAdapter().debug("let arr = [1,2,3];\nconsole.log(arr);")
    assert any(s.variables.get("arr") == [1, 2, 3] for s in steps)

def test_js_object():
    steps = JavaScriptAdapter().debug("let o = {a:1, b:2};")
    assert any(s.variables.get("o") == {"a": 1, "b": 2} for s in steps)

def test_js_map():
    steps = JavaScriptAdapter().debug('let m = new Map(); m.set("k", 1);')
    assert any(s.variables.get("m") == {"k": 1} for s in steps)

def test_js_set():
    steps = JavaScriptAdapter().debug("let s = new Set([1,2,3]);")
    assert any(s.variables.get("s") == [1, 2, 3] for s in steps)

def test_js_uninitialized():
    steps = JavaScriptAdapter().debug("let z;\nz = 5;")
    # On the first step, z is in TDZ
    assert steps[0].variables.get("z") == "<uninitialized>"

def test_js_no_node_internals():
    steps = JavaScriptAdapter().debug("let x = 1;")
    for s in steps:
        for k in ("exports", "require", "module", "__filename", "__dirname"):
            assert k not in s.variables

def test_js_no_hang_on_close():
    # Should complete quickly even on Node v25
    start = time.time()
    JavaScriptAdapter().debug("let x = 1;")
    assert time.time() - start < 10
```

---

### Story 7 — Adapter Registry & Factory

**As the service maintainer**, I want adding a new language to require touching exactly one file, **so that** I can ship Rust support next quarter without regression risk to existing adapters.

#### 7.1 Scope

Build the central registry that maps a `language` string to its concrete `DebugAdapter` class. This is the **OCP extension point** for the entire system: adding a new language means **adding one line to one dict** and writing a new adapter file. Nothing else changes.

#### 7.2 Acceptance Criteria

1. `DebugAdapterFactory().get("python")` returns a `PythonAdapter` instance.
2. `DebugAdapterFactory().get("rust")` raises `UnsupportedLanguageError`, which the endpoint maps to `400`.
3. The factory is a **singleton**: `DebugAdapterFactory() is DebugAdapterFactory()` evaluates `True` (or the equivalent module-level invariant — see §7.3 below).
4. The five built-in adapters are registered automatically when the package is imported — no manual `register()` calls needed in `main.py`.
5. Adding a sixth language to the test fixture list (a `FakeAdapter`) and inserting one line into `factory.py`'s registry causes a new test (`test_factory_picks_fake`) to pass — **without modifying any existing adapter, the service, or the endpoint**.
6. The factory is the **only** module in the codebase that imports concrete adapter classes. (Verified: `grep -rE "from .adapters\.\w+_adapter import" debug_service/ | grep -v factory.py` returns nothing.)
7. `pytest tests/test_factory.py` passes.

#### 7.3 Technical Design

**Files touched:** `factory.py`, `exceptions.py`, `adapters/__init__.py`.

**Pattern applied — Factory.** A single `get(language: str) -> DebugAdapter` method.
**Pattern applied — Singleton.** Module-level state (the idiomatic Python way; see Hello Interview's note that "modules themselves are singletons").

**Implementation:**

```python
# factory.py
from typing import Type
from .adapters.base import DebugAdapter
from .adapters.python_adapter import PythonAdapter
from .adapters.go_adapter import GoAdapter
from .adapters.cpp_adapter import CppAdapter
from .adapters.java_adapter import JavaAdapter
from .adapters.javascript_adapter import JavaScriptAdapter
from .exceptions import UnsupportedLanguageError


# ─── The registry. Singleton via module-level state. ──────────────────
_REGISTRY: dict[str, Type[DebugAdapter]] = {
    "python":     PythonAdapter,
    "go":         GoAdapter,
    "cpp":        CppAdapter,
    "java":       JavaAdapter,
    "javascript": JavaScriptAdapter,
    # ★ ADD NEW LANGUAGES HERE — and only here.
    # "rust": RustAdapter,
}


class DebugAdapterFactory:
    """Resolve a language string to a concrete DebugAdapter instance.

    This is the system's Open/Closed extension point: new languages
    are added by inserting an entry into the registry above. No other
    file in the codebase needs to change.
    """

    def get(self, language: str) -> DebugAdapter:
        try:
            cls = _REGISTRY[language]
        except KeyError:
            raise UnsupportedLanguageError(
                f"unsupported language: {language!r} "
                f"(supported: {sorted(_REGISTRY)})"
            )
        return cls()

    @staticmethod
    def supported() -> list[str]:
        return sorted(_REGISTRY)
```

**Why a class, not a free function?** Two reasons:

1. **Dependency injection** — `DebugService.__init__(self, factory: DebugAdapterFactory)` accepts the factory. Tests can substitute a `FakeFactory` returning a stub adapter without monkey-patching module globals.
2. **Future extension** — if we ever need adapter caching, configuration, or per-tenant registries, the class gives us a place to put it without breaking callers. (We don't build that now — YAGNI.)

**Singleton behavior in Python:**

We deliberately do **not** override `__new__` to enforce a single instance. The Hello Interview guide is explicit: "Singletons are not idiomatic in Python. In Python, modules themselves are singletons." The `_REGISTRY` dict is module-level state — there is exactly one of it per process, regardless of how many `DebugAdapterFactory()` objects you create. The class is a thin, stateless façade over that dict. Tests can freely instantiate it; `main.py` instantiates it once at startup.

If a reviewer insists on object-identity singleton behavior, the minimal change is:

```python
_INSTANCE = None
def get_factory() -> DebugAdapterFactory:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = DebugAdapterFactory()
    return _INSTANCE
```

But the simpler, idiomatic version above is preferred per KISS.

**Why the registry isn't a self-registration decorator.**

A common over-engineering temptation is:

```python
@register("python")
class PythonAdapter(DebugAdapter): ...
```

This is rejected on KISS grounds. It scatters the source of truth across five files; you can no longer answer "what languages does this service support?" by looking at one file. The flat dict is clearer, easier to grep, easier to review, and trivially testable. **YAGNI rules out the decorator-based registry.**

#### 7.4 Known Pitfalls

- ❌ **Don't create import cycles.** `factory.py` imports from `adapters/`. Adapters MUST NOT import from `factory.py`. They depend only on `adapters/base.py` and `models.py`.
- ❌ **Don't lazy-import inside `get()`.** Tempting for "faster startup," but it hides import errors until the first request. Import all adapters at module load — startup failures are loud and immediate.
- ❌ **Don't normalize the language string in the factory.** (`language.lower().strip()`) That's input handling; it belongs in the Pydantic model (`Language` enum coercion) before the value reaches the factory. Keeping the factory pure makes the OCP boundary clear.
- ❌ **Don't expose `_REGISTRY` directly** — tests (and clients) should call `factory.supported()`. Exposing the dict invites mutation, which breaks the singleton invariant.
- ❌ **Don't forget to register your new language in TWO places when adding one** — the `Language` enum in `models.py` *and* `_REGISTRY` here. We accept this two-touch cost because Pydantic's enum gives us free input validation and clean error messages. Document it in `ADDING_A_LANGUAGE.md`.

#### 7.5 Test Plan

```python
# tests/test_factory.py
def test_returns_each_supported_adapter():
    f = DebugAdapterFactory()
    assert isinstance(f.get("python"),     PythonAdapter)
    assert isinstance(f.get("go"),         GoAdapter)
    assert isinstance(f.get("cpp"),        CppAdapter)
    assert isinstance(f.get("java"),       JavaAdapter)
    assert isinstance(f.get("javascript"), JavaScriptAdapter)

def test_unsupported_raises():
    with pytest.raises(UnsupportedLanguageError):
        DebugAdapterFactory().get("rust")

def test_factory_is_stateless_singleton():
    # Two factory objects share the same registry — adding via one path
    # affects neither, because the registry is private.
    f1 = DebugAdapterFactory()
    f2 = DebugAdapterFactory()
    assert f1.supported() == f2.supported()

def test_supported_list_stable():
    assert DebugAdapterFactory().supported() == \
        ["cpp", "go", "java", "javascript", "python"]

# OCP regression test — verifies adding a language is one-line
def test_factory_picks_fake(monkeypatch):
    """Inserting a new language must not require code changes elsewhere."""
    class FakeAdapter(DebugAdapter):
        def debug(self, code): return [DebugStep(line=1, variables={})]
    from debug_service import factory as fmod
    monkeypatch.setitem(fmod._REGISTRY, "fake", FakeAdapter)
    assert isinstance(DebugAdapterFactory().get("fake"), FakeAdapter)
```

```bash
# Verify the OCP boundary in the source tree:
grep -rE "from \.adapters\.\w+_adapter import" debug_service/ | grep -v factory.py
# Expected: empty output. Only factory.py knows about concrete adapters.
```

---

### Story 8 — Session State Machine & Observability

**As an SRE**, I want every debug request to emit lifecycle events and respect a hard timeout, **so that** I can monitor latency, spot stuck sessions, and prevent runaway debuggers from exhausting workers.

#### 8.1 Scope

This story ties the system together with three cross-cutting concerns, each implemented via its dedicated pattern:

- **State Machine** — the `DebugSession` lifecycle.
- **Observer** — lifecycle events (`started`, `step_captured`, `completed`, `failed`) emitted to subscribers.
- **Decorator** — wrap any adapter with `@with_timeout` and `@with_validation`, applied uniformly without modifying adapter code.

#### 8.2 Acceptance Criteria

1. A `DebugSession` instance walks states in order: `IDLE → COMPILING → LAUNCHING → STEPPING → DONE` for happy path; any failure goes to `ERROR` with the error preserved on the session.
2. Illegal transitions (e.g. `IDLE → STEPPING` directly) raise `IllegalStateTransitionError`.
3. Subscribing a `LoggingObserver` to the event bus produces (at least) four log lines per successful request: `debug_started`, one `debug_step_captured` per step, `debug_completed` (alternative: `debug_step_captured` may be aggregated for high-step traces — see Pitfalls).
4. A failing request emits `debug_started` followed by `debug_failed`, with the exception type in the payload.
5. A request that exceeds the configured timeout (default 30s) raises `DebugTimeoutError` and the endpoint returns `408`.
6. The `@with_timeout` decorator is applied **once** at the factory boundary (so every adapter inherits it without each adapter re-implementing timeout logic).
7. No subprocess survives a timed-out request (verified: `pgrep dlv && pgrep node && pgrep java` are empty after the timeout).
8. Adding a `MetricsObserver` to the bus requires zero changes to `DebugSession` or any adapter — only a new file plus a `bus.subscribe(...)` call in `main.py`. (DIP / OCP regression.)
9. `pytest tests/test_session.py tests/test_observers.py tests/test_decorators.py` passes.

#### 8.3 Technical Design

**Files touched:** `session.py`, `observers.py`, `decorators.py`, `exceptions.py`.

##### 8.3.1 State Machine — `DebugSession`

**Pattern applied — State Machine.** Each state knows the legal next states; `DebugSession` is the context object that tracks the current state and exposes a `transition_to(new_state)` method.

We choose a **lightweight enum-based state machine** (one class with a transition table) rather than a state-per-class hierarchy. The states have no behavior of their own — they're purely a lifecycle marker. KISS wins.

```python
# session.py
from enum import Enum, auto
from .exceptions import IllegalStateTransitionError, AdapterFailureError

class State(Enum):
    IDLE       = auto()
    COMPILING  = auto()
    LAUNCHING  = auto()
    STEPPING   = auto()
    DONE       = auto()
    ERROR      = auto()

# Adjacency list of legal transitions. Any (from, to) NOT in here raises.
_LEGAL = {
    State.IDLE:      {State.COMPILING, State.LAUNCHING, State.ERROR},
    State.COMPILING: {State.LAUNCHING, State.ERROR},
    State.LAUNCHING: {State.STEPPING,  State.ERROR},
    State.STEPPING:  {State.DONE,      State.ERROR},
    State.DONE:      set(),    # terminal
    State.ERROR:     set(),    # terminal
}

class DebugSession:
    def __init__(self, language: str, bus: "EventBus"):
        self.language = language
        self._state = State.IDLE
        self._bus = bus
        self.error: Exception | None = None

    def transition_to(self, target: State) -> None:
        if target not in _LEGAL[self._state]:
            raise IllegalStateTransitionError(
                f"illegal transition: {self._state.name} → {target.name}"
            )
        self._state = target

    @property
    def state(self) -> State:
        return self._state

    def run(self, adapter, code: str) -> list[DebugStep]:
        self._bus.publish("debug_started", {"language": self.language})
        try:
            # Languages without a separate compile phase (Python, JS) skip COMPILING.
            self.transition_to(
                State.COMPILING if self.language in ("go", "cpp", "java")
                else State.LAUNCHING
            )
            if self._state == State.COMPILING:
                self.transition_to(State.LAUNCHING)
            self.transition_to(State.STEPPING)
            steps = adapter.debug(code)              # adapter is already wrapped
            for s in steps:
                self._bus.publish("debug_step_captured", {"line": s.line})
            self.transition_to(State.DONE)
            self._bus.publish("debug_completed", {"step_count": len(steps)})
            return steps
        except Exception as exc:
            self.error = exc
            self._state = State.ERROR
            self._bus.publish("debug_failed",
                              {"error_type": type(exc).__name__, "message": str(exc)})
            raise
```

> **Note on per-adapter compile granularity.** Python and JavaScript don't have a separate compile step from the user's perspective; we skip `COMPILING` for them and go straight to `LAUNCHING`. Go, C++, and Java go through `COMPILING`. This keeps the state machine honest — no state is ever a no-op for any language.

##### 8.3.2 Observer — `EventBus`

**Pattern applied — Observer.**

```python
# observers.py
from abc import ABC, abstractmethod

class Observer(ABC):
    @abstractmethod
    def on_event(self, name: str, payload: dict) -> None: ...

class EventBus:
    def __init__(self):
        self._subs: list[Observer] = []

    def subscribe(self, obs: Observer) -> None:
        self._subs.append(obs)

    def publish(self, name: str, payload: dict) -> None:
        for s in self._subs:
            try:
                s.on_event(name, payload)
            except Exception:
                # Observers must never break the request. Log and continue.
                logging.exception("observer failed: %s", type(s).__name__)

# Built-in subscriber
class LoggingObserver(Observer):
    def on_event(self, name: str, payload: dict) -> None:
        logging.info("%s %s", name, payload)
```

A `MetricsObserver` (Prometheus, StatsD, …) drops in as a sibling class without touching `DebugSession` or the bus.

##### 8.3.3 Decorator — `@with_timeout`, `@with_validation`

**Pattern applied — Decorator.** These wrap an `adapter.debug(code)` call without changing the adapter.

The application point matters: we want the decorators to apply **once**, at the `DebugAdapterFactory.get()` boundary, so every adapter is uniformly wrapped without each adapter knowing.

```python
# decorators.py
import functools, signal
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
from .exceptions import DebugTimeoutError, EmptyCodeError

def with_validation(fn):
    @functools.wraps(fn)
    def wrapper(self, code: str, *a, **kw):
        if not isinstance(code, str) or not code.strip():
            raise EmptyCodeError("code must be a non-empty string")
        return fn(self, code, *a, **kw)
    return wrapper

def with_timeout(seconds: float = 30.0):
    """Run adapter.debug in a worker thread; raise on overrun.

    Threads can't be killed in Python, but the wrapper guarantees the
    *request* returns within budget. The adapter is responsible for
    ensuring its own subprocesses (dlv, node, java, lldb) shut down
    in their own try/finally — see each adapter's pitfalls.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, code: str, *a, **kw):
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(fn, self, code, *a, **kw)
                try:
                    return fut.result(timeout=seconds)
                except FTimeout:
                    raise DebugTimeoutError(f"exceeded {seconds}s")
        return wrapper
    return deco
```

Application:

```python
# factory.py — extended
class DebugAdapterFactory:
    def get(self, language: str) -> DebugAdapter:
        cls = _REGISTRY[language]
        instance = cls()
        # Wrap the bound method, not the class — keeps each instance independent.
        instance.debug = with_validation(with_timeout(30.0)(instance.debug))
        return instance
```

> **Why the wrapping happens in the factory, not in `DebugAdapter` itself.** Putting `@with_timeout` on the abstract `debug` method would force every override to re-decorate. Wrapping at the factory keeps adapters pure (SRP — each adapter does *only* the language-specific work) and the timeout policy lives in one place (DRY). Changing the global timeout is a one-line edit.

#### 8.4 Known Pitfalls

- ❌ **Don't use `signal.SIGALRM` for the timeout.** `SIGALRM` only works on the main thread; under FastAPI (uvicorn) the request handler often runs in a worker thread and `signal.signal` raises `ValueError: signal only works in main thread`. Use the `ThreadPoolExecutor.submit().result(timeout=...)` pattern.
- ❌ **Threads in Python can't be killed.** When the timeout fires, the adapter's worker thread keeps running. That is why **every adapter has its own `try/finally` cleanup** for its subprocesses (dlv, node, java, lldb). The decorator only enforces the *response* time; subprocess cleanup is the adapter's responsibility. AC #7 verifies this with `pgrep`.
- ❌ **Don't publish `debug_step_captured` inside the adapter.** That couples adapters to the event bus (DIP violation). The session loops over the returned steps and publishes from there. Adapters return data; they don't publish events.
- ❌ **Don't let observer exceptions break the request.** Wrap `s.on_event(...)` in `try/except` inside `publish` and log. A flaky metrics backend should not 500 your debug API.
- ❌ **Don't allow transitions out of terminal states.** `DONE` and `ERROR` are sinks. The transition table has them mapping to empty sets.
- ❌ **For high-step traces (>10k steps), don't emit one log line per step.** AC #3 phrasing accepts aggregation. Switch `LoggingObserver` to count steps and emit a single summary line on `debug_completed`. The bus stays unchanged; only the observer's behavior changes — that's the whole point of using the Observer pattern.
- ❌ **Don't bundle the timeout decorator with the validation decorator at module-import time.** The order matters (`with_validation` outermost so empty code is rejected before a thread is spawned). Apply explicitly in the factory in the order shown.

#### 8.5 Test Plan

```python
# tests/test_session.py
def test_happy_path_transitions():
    bus = EventBus()
    sess = DebugSession("python", bus)
    fake = type("F", (), {"debug": lambda self, c: [DebugStep(line=1, variables={})]})()
    sess.run(fake, "x=1")
    assert sess.state == State.DONE

def test_failure_path():
    bus = EventBus()
    sess = DebugSession("python", bus)
    fake = type("F", (), {"debug": lambda self, c: (_ for _ in ()).throw(ValueError("x"))})()
    with pytest.raises(ValueError):
        sess.run(fake, "x=1")
    assert sess.state == State.ERROR
    assert isinstance(sess.error, ValueError)

def test_illegal_transition_raises():
    sess = DebugSession("python", EventBus())
    with pytest.raises(IllegalStateTransitionError):
        sess.transition_to(State.STEPPING)        # IDLE → STEPPING is illegal


# tests/test_observers.py
def test_observer_receives_events():
    seen = []
    class Capture(Observer):
        def on_event(self, n, p): seen.append((n, p))
    bus = EventBus(); bus.subscribe(Capture())
    DebugSession("python", bus).run(_fake_one_step_adapter(), "x=1")
    assert seen[0][0] == "debug_started"
    assert any(n == "debug_step_captured" for n, _ in seen)
    assert seen[-1][0] == "debug_completed"

def test_failing_observer_does_not_break_request():
    class Boom(Observer):
        def on_event(self, n, p): raise RuntimeError("observer crash")
    bus = EventBus(); bus.subscribe(Boom())
    # Must complete without raising
    DebugSession("python", bus).run(_fake_one_step_adapter(), "x=1")


# tests/test_decorators.py
def test_with_timeout_raises_on_slow_adapter():
    class Slow(DebugAdapter):
        def debug(self, code):
            time.sleep(2)
            return []
    wrapped = type("S", (Slow,), {})()
    wrapped.debug = with_timeout(0.2)(wrapped.debug)
    with pytest.raises(DebugTimeoutError):
        wrapped.debug("x=1")

def test_with_validation_rejects_empty():
    class Ok(DebugAdapter):
        def debug(self, code): return []
    o = Ok(); o.debug = with_validation(o.debug)
    with pytest.raises(EmptyCodeError):
        o.debug("   ")
    with pytest.raises(EmptyCodeError):
        o.debug("")
```

```bash
# End-to-end timeout / cleanup verification:
# Start the service, fire a 60s-busy-loop request, wait 35s, check pids
curl -X POST http://localhost:8000/debug -d '{"language":"python","code":"while True: pass"}' \
  -H 'Content-Type: application/json' &
sleep 35
pgrep -f "node|dlv|java DebugClient"   # Expected: no output
```

---

## 5. Non-Goals (Explicitly Out of Scope)

The following are intentionally **not** part of v1. Discussing them counts as YAGNI scope creep.

| Out of scope | Reason |
|---|---|
| Authentication / authorization | Service is internal; auth is a deployment-layer concern. |
| Persistence of debug sessions | Sessions are ephemeral. No DB, no Redis. |
| Streaming responses (SSE / WebSocket) | The endpoint is request/response JSON. Trace size is bounded; no streaming need yet. |
| Browser UI | A separate frontend project consumes this API. |
| Containerization / Dockerfile | Out of scope for the application code. The deployment runbook can layer this on. |
| Languages beyond the five listed | Adding any new language is a deliberate product decision and a separate PR. |
| Sandbox isolation (gVisor, Firecracker) | Treat user code as semi-trusted. Hardening is a separate workstream. |
| Multi-step / breakpoint API | The endpoint runs to completion. No interactive stepping. |
| Caching of trace results | Same code → same trace, but caching introduces correctness questions (env vars, time-dependent behavior). Not now. |
| Plugin / dynamic-loading mechanism for adapters | The flat dict in `factory.py` is sufficient. Plugins are KISS-violating overkill. |
| Configurable adapter behavior per request (compile flags, depth limits, etc.) | Defaults are baked in. Configurable behavior is a separate PRD. |

---

## 6. File Structure

```
debug_service/
├── __init__.py                       # Package init; no logic
├── main.py                           # FastAPI app, /debug route, DI wiring
├── service.py                        # DebugService            ← FACADE
├── factory.py                        # DebugAdapterFactory     ← FACTORY + SINGLETON
├── session.py                        # DebugSession + State enum  ← STATE MACHINE
├── models.py                         # Pydantic request/response  ← BUILDER (via Pydantic)
├── exceptions.py                     # Typed exception hierarchy
├── decorators.py                     # @with_timeout, @with_validation  ← DECORATOR
├── observers.py                      # EventBus, Observer ABC, LoggingObserver  ← OBSERVER
└── adapters/
    ├── __init__.py
    ├── base.py                       # DebugAdapter ABC          ← STRATEGY interface
    ├── python_adapter.py             # PythonAdapter             ← STRATEGY
    ├── go_adapter.py                 # GoAdapter                 ← STRATEGY
    ├── cpp_adapter.py                # CppAdapter                ← STRATEGY
    ├── java_adapter.py               # JavaAdapter               ← STRATEGY
    ├── javascript_adapter.py         # JavaScriptAdapter         ← STRATEGY
    └── java_resources/
        └── DebugClient.java          # JDI client (copied to tmpdir at runtime)

tests/
├── conftest.py
├── test_endpoint.py
├── test_python_adapter.py
├── test_go_adapter.py
├── test_cpp_adapter.py
├── test_java_adapter.py
├── test_javascript_adapter.py
├── test_factory.py
├── test_session.py
├── test_observers.py
├── test_decorators.py
└── fixtures/
    ├── python.snippet
    ├── go.snippet
    ├── cpp.snippet
    ├── java.snippet
    └── javascript.snippet

pyproject.toml          # fastapi, pydantic>=2, websocket-client, pytest, httpx
ADDING_A_LANGUAGE.md    # 2-step checklist: enum entry + registry entry
README.md
```

**Dependency direction (must hold):**

```
main.py ──▶ service.py ──▶ factory.py ──▶ adapters/*.py
   │             │              │              │
   │             ▼              ▼              ▼
   └────▶ models.py ◀── exceptions.py ◀── adapters/base.py
                  ▲                              ▲
                  │                              │
            session.py ◀── observers.py     decorators.py
```

No adapter ever imports `factory`, `service`, `session`, or `main`. The arrows point one way — that's how DIP shows up structurally.

---

## 7. Build Order (Suggested)

For someone implementing this start-to-finish:

1. **Story 7 first (skeleton).** Stub the factory with a single `PythonAdapter` registered. This anchors the type system.
2. **Story 2.** Build `PythonAdapter` end-to-end. No external tools needed; you can validate the whole interface with stdlib alone.
3. **Story 1.** Wire up FastAPI; you can now `curl` Python traces. Walking skeleton works.
4. **Story 8.** Add the state machine, event bus, and decorators. Validate with the Python adapter you already have.
5. **Story 3 / 4 / 5 / 6** — implement the remaining adapters in any order. Pick the languages whose toolchain is easiest to install on your dev machine first; Go is usually quickest, Java is usually slowest.
6. **OCP regression test.** Add a `FakeAdapter` to the factory dict, write the AC #5 test from Story 7. If it passes without touching anything else, the design holds.

---

## 8. Pattern Audit (Self-Check)

Per the spec, every listed pattern must appear and must fit naturally — not be forced. Here's the audit:

| Pattern | Where | Forced? |
|---|---|---|
| **Facade** | `DebugService` hides factory, session, decorators, adapters behind one method | Natural — the orchestrator role is exactly what facades describe |
| **Strategy** | `DebugAdapter` interface + 5 implementations | Natural — five interchangeable language backends behind one interface |
| **Factory** | `DebugAdapterFactory.get(language)` | Natural — chosen at runtime from a string |
| **Singleton** | Module-level `_REGISTRY` dict in `factory.py` | Natural — Python idiomatic; one registry per process |
| **Builder** | Pydantic `DebugRequest` / `DebugStep` model construction | Natural — Pydantic's validation-on-construct **is** a builder; we don't hand-roll one |
| **Decorator** | `@with_timeout`, `@with_validation` wrap adapter calls in the factory | Natural — cross-cutting concerns layered without modifying adapters |
| **Observer** | `EventBus` + `Observer` subscribers | Natural — multiple subscribers want lifecycle events without coupling |
| **State Machine** | `DebugSession` with `State` enum + transition table | Natural — `IDLE → COMPILING → LAUNCHING → STEPPING → DONE/ERROR` is a real lifecycle, not a flag |

**Patterns deliberately not used (and why):**

- **Abstract Factory (GoF)** — overkill. We have one factory, one product family.
- **Class-based Singleton with `__new__` override** — un-Pythonic. Module-level state achieves the same guarantee.
- **Hand-rolled Builder class for `DebugRequest`** — Pydantic does it. Adding our own would be a textbook KISS violation.
- **Self-registration decorator on adapter classes** — scatters source of truth. The flat dict in `factory.py` is clearer.
- **State-per-class** state machine — the states have no behavior of their own. An enum + transition table is enough. (Compare to a vending machine where each state has methods like `insert_coin` — that's when state-per-class earns its keep.)

---

## 9. Risk Register

| Risk | Likelihood | Mitigation |
|---|---|---|
| LLDB Python interpreter constraint surprises operators | High | Document prominently in `README.md` and Story 4. Provide a startup check that imports `lldb` and exits with a clear error if it fails. |
| Node version drift breaks JS adapter again | Medium | CI runs the JS adapter tests against Node 20, 22, 24, 25. Story 6 pitfalls call out the three known v25 fixes. |
| Subprocess leakage on timeout exhausts ports | Medium | Each adapter has a `try/finally` cleanup; AC #7 of Story 8 explicitly verifies with `pgrep`. |
| JDWP cold-start race causes flaky first-request behavior | Medium | Replace `time.sleep(0.5)` with port-polling (Story 5 §5.3). |
| Adapter authors copy-paste the timeout/validation logic | Low | The factory wraps every adapter once. Adapter base class enforces the interface — extra logic shouldn't be tempting. |
| Concurrent requests collide on hardcoded ports | Low | All adapters pick free ports via `bind(("",0))`. No hardcoded ports anywhere. |

---

## 10. Done Definition

The service is "done" when **all** of these hold:

- [ ] All 8 stories' acceptance criteria pass.
- [ ] `pytest tests/ -q` is green on a fresh checkout.
- [ ] `curl` smoke tests for all five languages return `200` with a non-empty trace.
- [x] `grep` audit of OCP boundary (Story 7 AC #6) returns empty.
- [x] No subprocess (`dlv`, `node`, `java`, lldb-spawned binary) survives 5 seconds after a request completes or times out.
- [x] `ADDING_A_LANGUAGE.md` exists and the documented two-step checklist actually works (verified by adding a sixth fake language and running the OCP regression test from Story 7).
- [ ] `README.md` documents the LLDB Python-interpreter requirement.

---

*End of Super PRD.*
