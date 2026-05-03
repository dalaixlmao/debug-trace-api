---
name: python-idiomatic-design
description: Python idioms that replace Java/C#-style patterns. Load when writing
  class structures, singletons, builders, registries, context managers, or any code
  where Java/C# habits would produce un-Pythonic output. Covers module-as-singleton,
  Pydantic-as-builder, dataclass usage, ABC vs Protocol, context managers for cleanup.
trigger: Use when writing class structures, registries, configuration objects, or
  anything where the AI might default to Java/C# idioms. Especially relevant for
  Singleton, Builder, and ABC-vs-Protocol decisions.
---

# Python Idiomatic Design

Most "design patterns" guides are Java-flavored. Python has cleaner alternatives.
Without this skill the AI tends to produce un-Pythonic code that looks like Java
with `def`. Apply these rules before writing any class structure, registry, or
configuration object in DebugTrace.

---

## 1. Modules ARE Singletons

> "In Python, modules themselves are singletons." — Hello Interview

Python imports are cached in `sys.modules`. The first import runs the module body;
every subsequent import returns the same object. You never need a class to enforce
single-instance semantics.

❌ Java-ism: class-based singleton with `__new__`
```python
class AdapterRegistry:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._registry = {}
        return cls._instance

    def register(self, lang: str, klass: type) -> None:
        self._registry[lang] = klass
```

✅ Pythonic: module-level state
```python
# factory.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.base import DebugAdapter

_REGISTRY: dict[str, type[DebugAdapter]] = {}

def register(lang: str, klass: type[DebugAdapter]) -> None:
    _REGISTRY[lang] = klass

def get_adapter(lang: str) -> type[DebugAdapter]:
    if lang not in _REGISTRY:
        raise KeyError(f"No adapter registered for language: {lang!r}")
    return _REGISTRY[lang]
```

If a code reviewer insists on a class-shaped API, the **only acceptable compromise**
is a module-level `get_instance()` function that returns a lazily-created object.
**Never** use `__new__` override — it surprises readers and adds nothing in Python.

---

## 2. Pydantic IS the Builder

For request/response models, never write a hand-rolled `Builder` class with
`.url(...).method(...).build()`. Use Pydantic v2. For internal data-only objects
with a few fields and sensible defaults, use `@dataclass`.

A `Builder` class for 2–5 fields adds ~30 lines, a second object to track, and
a bespoke API for no benefit over Pydantic's named arguments.

❌ Java-ism: hand-rolled Builder
```python
class DebugRequestBuilder:
    def __init__(self):
        self._language = None
        self._source_code = None
        self._breakpoints = []

    def language(self, lang: str) -> "DebugRequestBuilder":
        self._language = lang
        return self

    def source_code(self, code: str) -> "DebugRequestBuilder":
        self._source_code = code
        return self

    def breakpoints(self, bps: list[int]) -> "DebugRequestBuilder":
        self._breakpoints = bps
        return self

    def build(self) -> "DebugRequest":
        return DebugRequest(
            language=self._language,
            source_code=self._source_code,
            breakpoints=self._breakpoints,
        )
```

✅ Pythonic: Pydantic v2 model
```python
from pydantic import BaseModel, Field

class DebugRequest(BaseModel):
    language: str
    source_code: str
    breakpoints: list[int] = Field(default_factory=list)
    timeout_seconds: float = 10.0

# Construction is just named arguments — no builder needed:
req = DebugRequest(language="python", source_code="x = 1", breakpoints=[1])
```

For internal value objects that don't cross API boundaries, `@dataclass` is
lighter:
```python
from dataclasses import dataclass, field

@dataclass
class DebugStep:
    line: int
    variables: dict[str, str] = field(default_factory=dict)
    stdout: str = ""
```

```python
from pydantic import BaseModel

class DebugResponse(BaseModel):
    steps: list[DebugStep]
    exit_code: int
    error: str | None = None
```

---

## 3. ABC vs Protocol

For `DebugAdapter`, **ABC is the right choice** in this project.

| | ABC | Protocol |
|---|---|---|
| Subclass must implement | Enforced at instantiation | Only checked by type-checker |
| Explicit inheritance required | Yes (`class Foo(DebugAdapter)`) | No (structural) |
| Reader clarity | High — grep for `DebugAdapter` finds all adapters | Lower — any duck-type match counts |
| Best for | Explicit plugin families | Third-party / external classes |

Protocol would also work, but ABC is less surprising to new contributors and gives
a runtime `TypeError` when a method is not implemented, not a silent type-error.
**Use ABC throughout this project for consistency.**

```python
# adapters/base.py
from abc import ABC, abstractmethod
from models import DebugRequest, DebugResponse

class DebugAdapter(ABC):
    @abstractmethod
    def run(self, request: DebugRequest) -> DebugResponse:
        ...
```

Each concrete adapter inherits and implements `run`. If `run` is missing,
`TypeError: Can't instantiate abstract class` fires at construction — not at call
time, not silently.

---

## 4. Context Managers > try/finally for Cleanup

Where possible, prefer the `with` statement over manual cleanup. It is harder to
accidentally skip, composes naturally with early returns, and reads as intent.

✅ Prefer context manager
```python
import tempfile

def compile_and_run(source: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / "main.cpp"
        src_path.write_text(source)
        # tmp is cleaned up automatically, even if an exception is raised
        ...
```

For subprocess management you may still need `try/finally` because `subprocess.Popen`
doesn't compose cleanly with `with` when you need to kill on timeout (the context
manager only waits, it doesn't kill). See the `subprocess-lifecycle-management`
skill for the correct pattern there. The rule is: **use `with` by default; fall
back to `try/finally` only when the resource's `__exit__` semantics don't match
what you need.**

---

## 5. Type Hints — Yes, but Pragmatic

Use modern lowercase generics (Python 3.9+). Import `from __future__ import annotations`
at the top of files that have forward references or circular imports.

✅ Modern style
```python
from __future__ import annotations
from typing import Any

def parse_output(raw: dict[str, Any]) -> list[DebugStep]:
    ...

def find_adapter(lang: str) -> type[DebugAdapter] | None:
    return _REGISTRY.get(lang)
```

❌ Legacy style (don't write new code like this)
```python
from typing import Dict, List, Optional, Type

def parse_output(raw: Dict[str, Any]) -> List[DebugStep]:
    ...

def find_adapter(lang: str) -> Optional[Type[DebugAdapter]]:
    ...
```

Don't over-type internal helpers where the types add no clarity. A five-line
private function with obvious argument names doesn't need annotations. Public
interfaces and Pydantic models always do.

---

## 6. Common Java-isms to Reject

| Java-ism | Pythonic replacement |
|---|---|
| `class Foo: def getName(self)` | expose attribute directly, or `@property` |
| Getters + setters for every field | direct attribute access; Pydantic if validation needed |
| Singleton via `__new__` override | module-level state |
| Builder for 2–3 field objects | `@dataclass` with defaults |
| `AbstractFactoryProducerFactory` | just a function |
| `instanceof` checks everywhere | duck typing or `isinstance` once at the boundary |

❌
```python
class AdapterFactory:
    def createAdapter(self, language: str) -> DebugAdapter:
        if language == "python":
            return PythonAdapter()
        elif language == "go":
            return GoAdapter()
```

✅
```python
# factory.py
def get_adapter(language: str) -> DebugAdapter:
    klass = _REGISTRY.get(language)
    if klass is None:
        raise ValueError(f"Unsupported language: {language!r}")
    return klass()
```

---

## 7. Patterns Specific to This Project

**Path manipulation** — use `pathlib.Path`, not `os.path` string concatenation.
```python
# ❌
import os
src = os.path.join(tmpdir, "main.go")

# ✅
from pathlib import Path
src = Path(tmpdir) / "main.go"
src.write_text(source_code)
```

**Subprocess when you don't need live output** — use `subprocess.run` with
`capture_output=True, text=True` instead of `Popen` + `communicate`.
```python
result = subprocess.run(
    ["go", "build", "-o", str(binary), str(src)],
    capture_output=True,
    text=True,
    timeout=30,
)
if result.returncode != 0:
    raise CompilationError(result.stderr)
```

**Free-port allocation** — same pattern across all subprocess-spawning adapters.
```python
import socket

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]
```

**Thread-bounded timeouts** — use `concurrent.futures`, never `signal.SIGALRM`
(SIGALRM only works on the main thread and is incompatible with multi-worker servers).
```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError

with ThreadPoolExecutor(max_workers=1) as pool:
    future = pool.submit(_run_debug_session, adapter_proc)
    try:
        result = future.result(timeout=request.timeout_seconds)
    except TimeoutError:
        adapter_proc.kill()
        raise DebugTimeoutError(request.timeout_seconds)
```

**Tempdir cleanup that survives exceptions** — always use the context manager form.
```python
with tempfile.TemporaryDirectory() as tmp:
    # any exception here still triggers cleanup
    _write_sources(Path(tmp), request)
    return _execute(Path(tmp), request)
```
