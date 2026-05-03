---
name: adapter-testing-strategy
description: Testing conventions for DebugTrace API: pytest layout, the real-vs-fake adapter decision, subprocess cleanup verification, the OCP regression test, and how to test the timeout/observer cross-cutting decorators. Load when writing or reviewing any test in tests/.
user-invocable: false
---

# Adapter Testing Strategy

Load this skill before writing or reviewing any test in `tests/`. It governs test layering, the real-vs-fake adapter decision, subprocess leak verification, and the OCP regression test that enforces Story 7's one-file-change guarantee.

---

## Section 1: The Three Layers of Tests

Each part of the stack has a designated test style. Mixing the layers is the most common source of both slow tests and false confidence.

| Layer | What it covers | Toolchain needed | Target speed |
|---|---|---|---|
| **(A) Unit** | `service.py`, `session.py`, `decorators.py`, `observers.py` | None — `FakeAdapter` only | < 50 ms per test |
| **(B) Integration** | Each concrete adapter — real subprocess, real debugger | `dlv`, `clang++`, `javac`, `node` | 1–3 s per test |
| **(C) End-to-end** | Full HTTP path via `TestClient` + real adapters | All toolchains | 2–5 s per language |

**Layer A** tests catch logic bugs in the service and cross-cutting concerns. They must never spawn subprocesses — if a layer-A test is slow, a real adapter has leaked into it.

**Layer B** tests catch the per-language gotchas catalogued in `debug-adapter-implementation`. The subprocess IS the thing under test; you cannot abstract it away. See Section 2.

**Layer C** tests are one-per-language smoke tests that prove the HTTP route, service, factory, and adapter all wire together correctly. They run in CI and are skipped locally when the toolchain is absent.

### Directory layout

```
tests/
├── conftest.py                  # shared fixtures: FakeAdapter, no_leaked_subprocs, skip marks
├── fixtures/
│   ├── python.snippet
│   ├── go.snippet
│   ├── cpp.snippet
│   ├── java.snippet
│   └── javascript.snippet
├── test_endpoint.py             # layer C — TestClient, one test per language
├── test_service.py              # layer A — FakeAdapter, session state machine
├── test_decorators.py           # layer A — timeout, validation decorators
├── test_observers.py            # layer A — CaptureObserver
├── test_ocp.py                  # static OCP boundary check
├── test_python_adapter.py       # layer B — real sys.settrace
├── test_go_adapter.py           # layer B — real dlv
├── test_cpp_adapter.py          # layer B — real clang++ + lldb
├── test_java_adapter.py         # layer B — real javac + JDWP
└── test_javascript_adapter.py   # layer B — real node --inspect-brk
```

---

## Section 2: Real vs. Fake — The Decision Tree

```
Is the SUT service.py / session.py / decorators.py / observers.py?
  └─ YES → FakeAdapter. Always. See Section 2a.

Is the SUT a concrete adapter (GoAdapter, JavaAdapter, …)?
  └─ YES → Real toolchain. See Section 2b.

Is this a full-stack endpoint test?
  └─ YES → Real adapters + toolchain skip guards. See Section 2c.
```

### 2a — FakeAdapter (layer A)

```python
# tests/conftest.py
import time
from debug_service.adapters.base import DebugAdapter
from debug_service.models import DebugStep
from debug_service.exceptions import DebugTraceError


class FakeAdapter(DebugAdapter):
    """Controllable test double. No subprocesses, no I/O."""

    def __init__(
        self,
        steps: list[DebugStep] | None = None,
        raises: Exception | None = None,
        sleep: float = 0,
    ):
        self._steps  = steps or []
        self._raises = raises
        self._sleep  = sleep

    def debug(self, code: str) -> list[DebugStep]:
        if self._sleep:
            time.sleep(self._sleep)
        if self._raises:
            raise self._raises
        return self._steps
```

Use constructor parameters to cover every branch in the service/session:
- `FakeAdapter()` — happy path, empty trace.
- `FakeAdapter(steps=[DebugStep(line=1, variables={"x": 1})])` — happy path with data.
- `FakeAdapter(raises=CompileError("bad syntax"))` — compile-error path.
- `FakeAdapter(raises=AdapterFailureError("boom"))` — 500 path.
- `FakeAdapter(sleep=2)` — timeout path (combine with `with_timeout`).

### 2b — Real toolchain (layer B)

Mocking `subprocess.Popen` in an adapter test defeats the purpose. The bugs documented in `debug-adapter-implementation` Section 3 — wrong Delve response path, LLDB `UINT32_MAX` sentinel, JDWP `StepRequest` accumulation — only surface when the real subprocess runs.

Each layer-B test file follows this pattern (Go shown; others are analogous):

```python
# tests/test_go_adapter.py
import pytest
from debug_service.adapters.go_adapter import GoAdapter
from .conftest import skip_if_no_go, assert_no_leaked_subprocesses


@skip_if_no_go
class TestGoAdapter:

    def test_basic_trace(self, go_snippet):
        steps = GoAdapter().debug(go_snippet)
        assert len(steps) >= 1
        assert all(s.line > 0 for s in steps)

    def test_variables_present(self, go_snippet):
        steps = GoAdapter().debug(go_snippet)
        # at least one step has non-empty variables
        assert any(s.variables for s in steps)

    def test_compile_error_raises(self):
        from debug_service.exceptions import CompileError
        with pytest.raises(CompileError):
            GoAdapter().debug("package main\nfunc main() { bad }")

    def test_no_subprocess_leak(self, go_snippet):
        GoAdapter().debug(go_snippet)
        assert_no_leaked_subprocesses(["dlv exec"])
```

SUPER_PRD §4.3 (Go Known Pitfalls) is the checklist that drives these tests.

### 2c — End-to-end (layer C)

```python
# tests/test_endpoint.py
from fastapi.testclient import TestClient
from debug_service.main import app
from .conftest import skip_if_no_go, skip_if_no_clang, skip_if_no_java, skip_if_no_node

client = TestClient(app)

@skip_if_no_go
def test_go_end_to_end():
    r = client.post("/debug", json={"language": "go",
                                    "code": "package main\nfunc main(){x:=1;_=x}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list) and len(r.json()) >= 1
```

---

## Section 3: Subprocess Cleanup Verification

Every layer-B test MUST verify no subprocess survives. Add both a helper function and an `autouse` fixture.

```python
# tests/conftest.py
import subprocess, time

_LEAK_PATTERNS = [
    "dlv exec",
    "node --inspect",
    "java.*DebugClient",
    r"/tmp/.*/prog",     # C++ compiled binary
]


def assert_no_leaked_subprocesses(patterns: list[str] | None = None) -> None:
    """Fail if any process matching a pattern is still running."""
    for pattern in (patterns or _LEAK_PATTERNS):
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True
        )
        assert result.stdout.strip() == "", (
            f"Subprocess leak — still running after test: {pattern!r}\n"
            f"PIDs: {result.stdout.strip()}"
        )


@pytest.fixture(autouse=True)
def no_leaked_subprocs():
    yield
    time.sleep(0.3)   # allow the adapter's finally block to complete
    assert_no_leaked_subprocesses()
```

The 0.3 s sleep is deliberate: the `with_timeout` decorator may raise `DebugTimeoutError` in the calling thread while the adapter thread's `finally` is still running. 300 ms is enough for `proc.terminate()` + `proc.wait(2)` to complete in nearly all cases.

If a leak is detected, the first debugging step is:

```bash
ps aux | grep -E "dlv|node --inspect|java.*Debug|/tmp/.*prog"
```

Then check that the adapter's `_trace` method has a `try/finally` calling `_cleanup_subprocesses`. See `subprocess-lifecycle-management` Section 2 for the canonical pattern.

---

## Section 4: The OCP Regression Test

Story 7 requires that adding a language touches exactly one file (`factory.py`). Two tests enforce this.

### 4a — Dynamic: inject-and-retrieve via registry

```python
# tests/test_ocp.py
import pytest
from debug_service.adapters.base import DebugAdapter
from debug_service.factory import DebugAdapterFactory
from debug_service.models import DebugStep


def test_factory_picks_new_language(monkeypatch):
    """OCP guarantee: a new language requires only a registry entry."""

    class _FakeAdapter(DebugAdapter):
        def debug(self, code: str) -> list[DebugStep]:
            return [DebugStep(line=1, variables={})]

    from debug_service import factory as fmod
    monkeypatch.setitem(fmod._REGISTRY, "fake", _FakeAdapter)

    adapter = DebugAdapterFactory().get("fake")
    assert isinstance(adapter, _FakeAdapter)
    assert adapter.debug("anything")[0].line == 1
```

### 4b — Static: grep for boundary violations

```python
# tests/test_ocp.py (continued)
import subprocess

def test_ocp_boundary_holds():
    """No file other than factory.py may import a concrete adapter."""
    result = subprocess.run(
        ["grep", "-rE", r"from \.adapters\.\w+_adapter import", "debug_service/"],
        capture_output=True, text=True,
    )
    leaks = [
        line for line in result.stdout.splitlines()
        if "factory.py" not in line
    ]
    assert leaks == [], (
        "OCP boundary broken — concrete adapter imported outside factory.py:\n"
        + "\n".join(leaks)
    )
```

Run both after every adapter addition. The dynamic test catches registration bugs; the static test catches import discipline violations.

---

## Section 5: Toolchain Skip Marks

Define all skip marks in `conftest.py` so every test file imports from one place.

```python
# tests/conftest.py
import shutil, pytest

skip_if_no_go = pytest.mark.skipif(
    shutil.which("go") is None or shutil.which("dlv") is None,
    reason="go and dlv must both be installed",
)
skip_if_no_clang = pytest.mark.skipif(
    shutil.which("clang++") is None,
    reason="clang++ not installed",
)
skip_if_no_java = pytest.mark.skipif(
    shutil.which("javac") is None or shutil.which("java") is None,
    reason="JDK not installed",
)
skip_if_no_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node not installed",
)
```

Apply at class level to skip an entire test class when the toolchain is absent:

```python
@skip_if_no_clang
class TestCppAdapter:
    ...
```

CI runs with all toolchains installed. Local dev environments skip what they don't have — no `pytest.ini` changes needed.

---

## Section 6: Testing the Timeout Decorator

### Fast unit test — use FakeAdapter (layer A)

```python
# tests/test_decorators.py
import time, pytest
from debug_service.adapters.base import DebugAdapter
from debug_service.decorators import with_timeout
from debug_service.exceptions import DebugTimeoutError
from debug_service.models import DebugStep


def test_with_timeout_raises_on_slow_adapter():
    class _Slow(DebugAdapter):
        def debug(self, code: str) -> list[DebugStep]:
            time.sleep(5)
            return []

    adapter = _Slow()
    adapter.debug = with_timeout(0.1)(adapter.debug)

    with pytest.raises(DebugTimeoutError):
        adapter.debug("x=1")


def test_with_timeout_returns_normally_on_fast_adapter():
    steps = [DebugStep(line=1, variables={})]

    class _Fast(DebugAdapter):
        def debug(self, code: str) -> list[DebugStep]:
            return steps

    adapter = _Fast()
    adapter.debug = with_timeout(5)(adapter.debug)
    assert adapter.debug("x=1") == steps
```

### Subprocess leak under timeout (layer B, per adapter)

The unit test above proves `DebugTimeoutError` is raised. This integration test proves the subprocess is cleaned up when it fires on a real adapter — the two facts are independent and both required.

```python
# tests/test_javascript_adapter.py
@skip_if_no_node
def test_js_subprocess_cleaned_up_on_timeout():
    from debug_service.adapters.javascript_adapter import JavaScriptAdapter
    from debug_service.decorators import with_timeout

    adapter = JavaScriptAdapter()
    adapter.debug = with_timeout(0.1)(adapter.debug)

    with pytest.raises(DebugTimeoutError):
        adapter.debug("while(true) {}")

    time.sleep(1.0)   # adapter thread's finally may still be running
    assert_no_leaked_subprocesses(["node --inspect"])
```

Add equivalent tests to `test_go_adapter.py`, `test_java_adapter.py`, and `test_cpp_adapter.py`.

---

## Section 7: Testing Observers

A `CaptureObserver` is the correct test double for the event bus. Never assert on log output or side effects of `LoggingObserver` in unit tests.

```python
# tests/conftest.py
from debug_service.observers import Observer

class CaptureObserver(Observer):
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def on_event(self, name: str, payload: dict) -> None:
        self.events.append((name, payload))
```

```python
# tests/test_observers.py
from debug_service.observers import EventBus
from debug_service.session import DebugSession
from debug_service.models import DebugStep
from .conftest import FakeAdapter, CaptureObserver


def test_lifecycle_events_emitted():
    bus     = EventBus()
    capture = CaptureObserver()
    bus.subscribe(capture)

    session = DebugSession(language="python", bus=bus)
    session.run(
        FakeAdapter(steps=[DebugStep(line=1, variables={"x": 1})]),
        code="x=1",
    )

    names = [name for name, _ in capture.events]
    assert names[0]  == "debug_started"
    assert "debug_step_captured" in names
    assert names[-1] == "debug_completed"


def test_error_event_emitted_on_failure():
    bus     = EventBus()
    capture = CaptureObserver()
    bus.subscribe(capture)

    from debug_service.exceptions import AdapterFailureError
    session = DebugSession(language="python", bus=bus)

    with pytest.raises(AdapterFailureError):
        session.run(FakeAdapter(raises=AdapterFailureError("boom")), "x=1")

    names = [name for name, _ in capture.events]
    assert "debug_error" in names
    assert names[-1] == "debug_error"
```

---

## Section 8: Test Fixtures Directory

`tests/fixtures/` holds one canonical snippet per language. Each snippet is a small but representative program: basic types, a loop, and at least one collection.

```python
# tests/conftest.py
from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures"

@pytest.fixture
def python_snippet() -> str:
    return (_FIXTURES / "python.snippet").read_text()

@pytest.fixture
def go_snippet() -> str:
    return (_FIXTURES / "go.snippet").read_text()

@pytest.fixture
def cpp_snippet() -> str:
    return (_FIXTURES / "cpp.snippet").read_text()

@pytest.fixture
def java_snippet() -> str:
    return (_FIXTURES / "java.snippet").read_text()

@pytest.fixture
def javascript_snippet() -> str:
    return (_FIXTURES / "javascript.snippet").read_text()
```

Snippets are referenced in SUPER_PRD §1.6 test plan (`cat fixtures/$lang.snippet`). Keep them stable — they are the canonical regression inputs.

---

## Section 9: Anti-Patterns

| Anti-pattern | Consequence | What to do instead |
|---|---|---|
| `mock.patch("subprocess.Popen")` in adapter tests | Tests pass; real bugs (wrong Delve path, port race, LLDB sentinel) go undetected | Use the real toolchain; skip if unavailable |
| `assert duration < 0.5` | Flaky in CI on slow runners or under load | Use generous timing bounds or avoid timing assertions entirely |
| `assert [s.line for s in steps] == [1,2,3,4,5,6,7,8]` | Breaks when compiler/runtime version changes line numbering | Assert structure: `all(s.line > 0 for s in steps)` and spot-check specific variables |
| One test function asserting 5 independent facts | One failure masks the other four; hard to bisect | One test, one assertion cluster; use descriptive names |
| Layer-A tests that import a concrete adapter | Slow and couples unit tests to the toolchain | Import only `FakeAdapter`; inject via constructor |
| `time.sleep(N)` for subprocess readiness in a test | Flaky on cold start | Use `_wait_for_port()` from `subprocess-lifecycle-management` Section 4 |
| Asserting on `LoggingObserver` output | Fragile to log format changes | Use `CaptureObserver` in tests; `LoggingObserver` is for production |
