---
name: design-patterns
description: The eight design patterns used in DebugTrace API and where each one fits. Load when designing class structures, reviewing pattern usage, or asking "should I introduce a pattern here?". Includes anti-patterns deliberately rejected from the project (class-based singleton, hand-rolled builder, decorator-based registry).
when_to_use: "is this design good, reviewing adapter, refactoring, implementation work, writing new code"
user-invocable: true
---

> **Hello Interview stance:** most real-world services use 0–2 patterns well; 3+ starts to smell like over-engineering. DebugTrace uses eight — but each one maps directly to a PRD requirement (§2, §8). No pattern was added speculatively.

---

## Section 1: Pattern → Component Map

From SUPER_PRD §2 and §8 Pattern Audit.

| Pattern       | Component / File                        | Why it's there                                          |
|---------------|-----------------------------------------|---------------------------------------------------------|
| Facade        | `DebugService` (`service.py`)           | Route sees one method; LLDB/Delve/JDWP are invisible    |
| Strategy      | `DebugAdapter` ABC + 5 adapters         | Same `.debug()` interface, five interchangeable impls   |
| Factory       | `DebugAdapterFactory.get()`             | OCP extension point — new language = one new dict entry |
| Singleton     | `_REGISTRY` module-level dict           | Created once per process; no `__new__` override needed  |
| Builder       | Pydantic models (`models.py`)           | Validates and assembles `DebugRequest` / `DebugResponse`|
| Decorator     | `with_timeout`, `with_validation`       | Cross-cutting concerns applied at factory boundary      |
| Observer      | `EventBus` + subscribers (`observers.py`) | Logging/metrics without coupling to adapters          |
| State Machine | `DebugSession` + `State` enum           | Explicit `IDLE → COMPILING → LAUNCHING → STEPPING → DONE/ERROR` |

---

## Section 2: Each Pattern in Detail

---

### Facade — `service.py`

#### (A) When to reach for it
The route handler needs to trigger a multi-step operation (factory lookup → session creation → adapter dispatch → event publishing) without knowing any of it. One class, one public method, nothing else visible. Every orchestrator **is** a facade — you don't need to announce it.

#### (B) How it shows up here
```python
# service.py
class DebugService:
    def __init__(self, factory: DebugAdapterFactory, event_bus: EventBus):
        self._factory = factory
        self._bus = event_bus

    async def debug(self, req: DebugRequest) -> DebugResponse:
        adapter = self._factory.get(req.language)
        session = DebugSession(req.language, self._bus)
        steps = session.run(adapter, req.code)
        return DebugResponse(steps=steps, output=req.code)

# router.py — the route is thin by design
@router.post("/debug")
async def debug_endpoint(req: DebugRequest, svc: DebugService = Depends()):
    return await svc.debug(req)
```

#### (C) When NOT to reach for it
Don't add a second facade layer (e.g., a `DebugOrchestrator` that wraps `DebugService`). There's already one facade. More layers add indirection without adding encapsulation.

---

### Strategy — `adapters/*.py`

#### (A) When to reach for it
Five concrete behaviors (one per language), same interface, runtime selection based on a string key. The service never branches on language — it just calls `adapter.debug()`. Textbook Strategy.

#### (B) How it shows up here
```python
# adapters/base.py
class DebugAdapter(ABC):
    @abstractmethod
    async def debug(self, req: DebugRequest) -> DebugResponse: ...

# adapters/python_adapter.py
class PythonAdapter(DebugAdapter):
    async def debug(self, req: DebugRequest) -> DebugResponse:
        proc = await asyncio.create_subprocess_exec("python", "-m", "debugpy", ...)
        try:
            return await self._run(proc, req)
        finally:
            proc.terminate(); await proc.wait()

# adapters/go_adapter.py
class GoAdapter(DebugAdapter):
    async def debug(self, req: DebugRequest) -> DebugResponse:
        proc = await asyncio.create_subprocess_exec("dlv", "exec", ...)
        try:
            return await self._run(proc, req)
        finally:
            proc.terminate(); await proc.wait()
            self._cleanup_dlv_socket()
```

#### (C) When NOT to reach for it
Don't use Strategy for things that vary by configuration rather than runtime type. A single adapter's log verbosity level is a parameter — `adapter.debug(req, verbose=True)` — not a separate class. If there's no runtime dispatch on a string key, it's not Strategy.

---

### Factory — `factory.py`

#### (A) When to reach for it
You need to turn a string key (`"python"`, `"go"`) into a concrete object without the caller knowing the concrete type. The OCP requirement — adding a language touches exactly one file — is the signal.

#### (B) How it shows up here
```python
# factory.py
_REGISTRY: dict[str, type[DebugAdapter]] = {
    "python": PythonAdapter,
    "go":     GoAdapter,
    "cpp":    CppAdapter,
    "java":   JavaAdapter,
    "js":     JsAdapter,
}

class DebugAdapterFactory:
    def get(self, language: str) -> DebugAdapter:
        cls = _REGISTRY.get(language)
        if cls is None:
            raise UnsupportedLanguageError(language)
        instance = cls()
        instance.debug = with_validation(with_timeout(30.0)(instance.debug))
        return instance
```

#### (C) When NOT to reach for it
Don't factory-ize objects that don't have varying types. `EventBus` has one kind — just instantiate it directly. Don't add a second factory for sub-problems (e.g., a `DebugSessionFactory`); sessions are cheap dataclasses, not polymorphic dispatches.

---

### Singleton — `_REGISTRY` module-level dict

#### (A) When to reach for it
You need exactly one instance of something per process. In Python, **modules are already singletons** (imported once, cached by the interpreter). Module-level state is the idiomatic solution.

#### (B) How it shows up here
```python
# factory.py
_REGISTRY: dict[str, type[DebugAdapter]] = {   # one dict, one process
    "python": PythonAdapter,
    ...
}

# main.py
factory = DebugAdapterFactory()   # thin stateless wrapper over _REGISTRY
bus = EventBus()
bus.subscribe(LoggingObserver())
```
`DebugAdapterFactory()` can be instantiated multiple times — each instance delegates to the same module-level `_REGISTRY`. Hello Interview is explicit: "modules themselves are singletons."

#### (C) When NOT to reach for it
Don't override `__new__` to enforce a single instance. That's not idiomatic Python and adds complexity for zero gain. Don't use a class-based singleton for anything in this project — module-level state is enough everywhere.

---

### Builder — `models.py` (Pydantic)

#### (A) When to reach for it
You need to validate and assemble a complex object from raw input (HTTP request body). Pydantic v2 handles construction, type coercion, and validation in one step — no separate builder class needed.

#### (B) How it shows up here
```python
# models.py
class DebugRequest(BaseModel):
    language: str
    code: str

class DebugStep(BaseModel):
    line: int
    locals: dict[str, str]

class DebugResponse(BaseModel):
    steps: list[DebugStep]
    output: str

# Usage — Pydantic IS the builder:
req = DebugRequest(language="python", code="x = 1")  # validates on construction
```
`DebugRequest(language="python", code="x = 1")` is equivalent to a full Builder chain. Validation is free.

#### (C) When NOT to reach for it
Don't write a hand-rolled `DebugRequestBuilder` class. The pattern is already satisfied by Pydantic. Adding another layer violates KISS and DRY. Any model with fewer than ~8 fields and no conditional construction logic doesn't need a builder.

---

### Decorator — `decorators.py`

#### (A) When to reach for it
A cross-cutting concern (timeout enforcement, input validation) must wrap every adapter call without modifying any adapter. Applied once, at the factory boundary.

#### (B) How it shows up here
```python
# decorators.py
def with_validation(fn):
    @functools.wraps(fn)
    def wrapper(self, code: str, *a, **kw):
        if not isinstance(code, str) or not code.strip():
            raise EmptyCodeError("code must be a non-empty string")
        return fn(self, code, *a, **kw)
    return wrapper

def with_timeout(seconds: float = 30.0):
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

# factory.py — applied once, order matters (validation outermost)
instance.debug = with_validation(with_timeout(30.0)(instance.debug))
```

#### (C) When NOT to reach for it
Don't decorate things that don't have cross-cutting concerns. Only timeout enforcement and input validation qualify here. Don't use a decorator to add logging inside adapters — that's the Observer's job. Don't apply these decorators in `DebugAdapter` itself; that forces every adapter to re-decorate.

---

### Observer — `observers.py`

#### (A) When to reach for it
Multiple consumers (logging, metrics, tracing) need lifecycle events from `DebugSession` without `DebugSession` coupling to any of them. Adding a new consumer must require zero changes to the session or adapters.

#### (B) How it shows up here
```python
# observers.py
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
                logging.exception("observer failed: %s", type(s).__name__)

class LoggingObserver(Observer):
    def on_event(self, name: str, payload: dict) -> None:
        logging.info("%s %s", name, payload)

# Adding MetricsObserver — zero changes to session or bus:
# class MetricsObserver(Observer):
#     def on_event(self, name, payload): statsd.increment(name)
```

#### (C) When NOT to reach for it
Don't publish events from inside adapters. Adapters return data; they don't publish events. Event publishing belongs in `DebugSession.run()` after it has the adapter's output. Publishing from adapters couples them to the bus (DIP violation). Don't let observer exceptions propagate — wrap `on_event` in `try/except` inside `publish`.

---

### State Machine — `session.py`

#### (A) When to reach for it
A session has distinct lifecycle phases with explicit legal transitions. Illegal transitions must raise immediately. The `IDLE → COMPILING → LAUNCHING → STEPPING → DONE/ERROR` sequence is explicit in the PRD.

#### (B) How it shows up here
```python
# session.py
class State(Enum):
    IDLE      = "idle"
    COMPILING = "compiling"
    LAUNCHING = "launching"
    STEPPING  = "stepping"
    DONE      = "done"
    ERROR     = "error"

_LEGAL: dict[State, set[State]] = {
    State.IDLE:      {State.COMPILING, State.LAUNCHING, State.ERROR},
    State.COMPILING: {State.LAUNCHING, State.ERROR},
    State.LAUNCHING: {State.STEPPING,  State.ERROR},
    State.STEPPING:  {State.DONE,      State.ERROR},
    State.DONE:      set(),    # terminal
    State.ERROR:     set(),    # terminal
}

class DebugSession:
    def __init__(self, language: str, bus: EventBus):
        self._state = State.IDLE
        self.language = language
        self._bus = bus

    def transition_to(self, target: State) -> None:
        if target not in _LEGAL[self._state]:
            raise IllegalStateTransitionError(
                f"illegal transition: {self._state.name} → {target.name}"
            )
        self._state = target
```

#### (C) When NOT to reach for it
Don't use state-per-class (GoF State pattern with a class per state). The states here have no behavior — they're labels on a transition table. An enum + `_LEGAL` dict is sufficient and readable. Don't allow transitions out of terminal states (`DONE`, `ERROR` map to empty sets).

---

## Section 3: Patterns Rejected From This Project

These are explicitly off-limits for v1. If you find yourself reaching for one, stop.

| Rejected Pattern | Why |
|------------------|-----|
| **Abstract Factory (GoF, parameterized)** | One factory, one product family. No need to parameterize the factory itself. |
| **Class-based Singleton (`__new__` override)** | Module-level state is idiomatic Python. `__new__` override is a Java idiom transplanted incorrectly. |
| **Hand-rolled Builder class** | Pydantic v2 covers construction and validation. A `DebugRequestBuilder` is a second layer over a solved problem. |
| **Self-registration decorator** (`@register("python")` on adapter classes) | Scatters the source of truth. The flat dict in `factory.py` is clearer, greppable, and easier to audit. |
| **State-per-class State Machine** | States in this project have no behavior — an enum + transition table is enough. Per-class states add five files for zero gain. |
| **Visitor** | Not used anywhere. If you're tempted, you're over-engineering serialization — polymorphism on a type tag is the right shape here. |
| **Command, Memento, Mediator, Chain of Responsibility** | Not in the project. Don't smuggle them in. |

---

## Section 4: Decision Algorithm

```
Should I introduce a pattern?

1. Can you solve the problem with a plain function or a dict?
   → Yes: use that. Done.

2. Is it already covered by the §2 pattern→component map?
   → Yes: use the existing pattern at the existing layer. Done.

3. Are you adding a NEW pattern not in the map?
   → STOP. The PRD's pattern list is closed for v1 (YAGNI).
      File a question against the PRD; don't implement speculatively.

4. Are you adding a SECOND instance of an existing pattern
   (e.g., a second factory, a second facade)?
   → Ask: is this "varying-implementation-by-string-key" (factory territory)
     or just "configurability" (a parameter territory)?
   → If configurability: use a parameter. Not a factory.
   → If truly a new dispatch axis: make the case in the PR.
```

---

## Section 5: Forced Pattern Examples to Reject at Review

These are real failure modes — patterns that *look* helpful but add complexity for no gain.

**"Wrap the EventBus in a Singleton class"**
```python
# DO NOT DO THIS
class EventBus:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
```
`bus = EventBus()` in `main.py` is already a singleton by usage. Modules are singletons. `__new__` adds 5 lines and confuses tests that want isolated buses.

---

**"Add a Visitor for variable serialization"**
```python
# DO NOT DO THIS
class Visitor(ABC):
    def visit_int(self, v: int) -> str: ...
    def visit_str(self, v: str) -> str: ...
    def visit_list(self, v: list) -> str: ...

class JsonSerializeVisitor(Visitor): ...
```
`str(value)` or `json.dumps(value)` covers 100% of the cases in this project. A Visitor hierarchy adds three files for a problem that's already solved.

---

**"Builder for DebugStep"**
```python
# DO NOT DO THIS
class DebugStepBuilder:
    def set_line(self, n: int): self._line = n; return self
    def set_locals(self, d: dict): self._locals = d; return self
    def build(self): return DebugStep(line=self._line, locals=self._locals)
```
`DebugStep` is a 2-field Pydantic model. `DebugStep(line=5, locals={"x": "1"})` is the builder. This class is 7 lines of noise over a solved problem.

---

**"Strategy for log levels in LoggingObserver"**
```python
# DO NOT DO THIS
class LoggingStrategy(ABC):
    @abstractmethod
    def log(self, name: str, payload: dict) -> None: ...

class InfoLoggingStrategy(LoggingStrategy):
    def log(self, name, payload): logging.info("%s %s", name, payload)

class DebugLoggingStrategy(LoggingStrategy):
    def log(self, name, payload): logging.debug("%s %s", name, payload)
```
Log level is a config value — `LOG_LEVEL=DEBUG` in the environment. It is not a runtime behavior swap between interchangeable implementations. `logging.getLogger().setLevel(logging.DEBUG)` is the correct lever. Strategy pattern requires behavior divergence, not parameter divergence.

---

## Cross-reference

- SUPER_PRD §2 — Pattern-to-component map (table)
- SUPER_PRD §8 — Pattern Audit (Story 8: State Machine + Observer + Decorator)
- `.claude/skills/design-principles/SKILL.md` — SOLID, KISS, YAGNI applied to this codebase
