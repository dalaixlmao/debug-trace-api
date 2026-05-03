---
name: design-principles
description: The general software and SOLID design principles that apply to every line of code in DebugTrace. Use when writing, reviewing, or refactoring; load before any implementation work. Covers SOLID, KISS, YAGNI, DRY, Separation of Concerns, Law of Demeter — with the project's specific stance on each.
when_to_use: "is this design good, reviewing adapter, refactoring, implementation work, writing new code"
user-invocable: true
---

## Section 1: The Hierarchy

Rank these in priority order for DebugTrace. When principles conflict, the higher one wins.

1. **KISS** — top priority. The most-violated principle in code review. If you find yourself writing a base class, a registry class, or an abstraction layer that doesn't directly correspond to a PRD requirement, stop.
2. **YAGNI** — second. Reject scaffolding for "future languages", plugin systems, or configurable adapter behavior. The PRD (§5) defines exactly five languages. Build for those five.
3. **DRY** — third. Extract genuinely-shared logic, but tolerate small duplication if extraction creates artificial coupling or makes each site harder to read independently.
4. **SOLID** — applied selectively where it earns its keep (see §2 below). Not applied wholesale.
5. **Separation of Concerns** — implicit in the layered architecture: HTTP → Service → Factory → Adapter → External tool. Never skip a layer.
6. **Law of Demeter** — minor. Mainly: avoid deep chain expressions by extracting intermediate variables at each step.

---

## Section 2: Each Principle With a DebugTrace Example

### KISS — Keep It Simple, Stupid

Complexity is a liability, not an investment. Satisfy the acceptance criteria and stop.

**Good — module-level dict registry in `factory.py`:**
```python
# factory.py
from adapters.python_adapter import PythonAdapter
from adapters.go_adapter import GoAdapter

_REGISTRY: dict[str, type[DebugAdapter]] = {
    "python": PythonAdapter,
    "go":     GoAdapter,
    "cpp":    CppAdapter,
    "java":   JavaAdapter,
    "js":     JsAdapter,
}

def get_adapter(language: str) -> DebugAdapter:
    cls = _REGISTRY.get(language)
    if cls is None:
        raise UnsupportedLanguageError(language)
    return cls()
```

**Tempting but wrong — plugin loader with entry-point discovery:**
```python
# DO NOT DO THIS
import importlib.metadata

def get_adapter(language: str) -> DebugAdapter:
    for ep in importlib.metadata.entry_points(group="debugtrace.adapters"):
        if ep.name == language:
            return ep.load()()
    raise UnsupportedLanguageError(language)
```
There are no third-party plugins. Entry-point discovery adds 20 lines and a packaging contract for zero gain.

---

### YAGNI — You Aren't Gonna Need It

Don't build anything not demanded by a current acceptance criterion. No configurability "in case someone wants to change it later."

**Good — hardcoded 30 s timeout in `service.py`:**
```python
# service.py
TIMEOUT_SECONDS = 30

async def debug(request: DebugRequest) -> DebugResponse:
    adapter = factory.get_adapter(request.language)
    return await asyncio.wait_for(adapter.debug(request), timeout=TIMEOUT_SECONDS)
```

**Tempting but wrong — per-adapter configurable timeout in the request body:**
```python
# DO NOT DO THIS
class DebugRequest(BaseModel):
    language: str
    code: str
    timeout: int = 30          # "handy for slow Java compilations"
    adapter_options: dict = {} # "future flexibility"
```
Neither field is in the PRD. Every extra field is a vector for abuse and a maintenance burden. Delete it.

---

### DRY — Don't Repeat Yourself

Extract logic that is genuinely identical across call sites. But: textual similarity is not the same as semantic identity.

**Good — a single timeout decorator applied at the factory:**
```python
# factory.py
def _with_timeout(adapter: DebugAdapter) -> DebugAdapter:
    original = adapter.debug
    async def debug(req):
        return await asyncio.wait_for(original(req), timeout=30)
    adapter.debug = debug
    return adapter
```
One place to change the timeout. One place to change the error type.

**The DRY exception — per-adapter `try/finally` subprocess cleanup:**
```python
# python_adapter.py
async def debug(self, req: DebugRequest) -> DebugResponse:
    proc = await asyncio.create_subprocess_exec("python", "-m", "debugpy", ...)
    try:
        return await self._run(proc, req)
    finally:
        proc.terminate()        # SIGTERM then wait
        await proc.wait()

# go_adapter.py
async def debug(self, req: DebugRequest) -> DebugResponse:
    proc = await asyncio.create_subprocess_exec("dlv", "exec", ...)
    try:
        return await self._run(proc, req)
    finally:
        proc.terminate()
        await proc.wait()
        self._cleanup_dlv_socket()   # dlv-specific extra step
```
Each `finally` block is semantically different — Go needs socket cleanup, Java needs JVM teardown, lldb needs `debugger.Destroy()`. Extracting them into one function would require per-adapter branches inside the helper, which is worse than the duplication. **This is the rare case where duplication beats extraction.**

---

### SRP — Single Responsibility Principle

A class has exactly one reason to change.

**Good — `PythonAdapter` only does Python tracing:**
```python
class PythonAdapter(DebugAdapter):
    async def debug(self, req: DebugRequest) -> DebugResponse:
        # Starts debugpy, collects frames, returns DebugResponse
        ...
```
If the Python debugpy protocol changes, only this file changes.

**Wrong — `PythonAdapter` also writing log entries:**
```python
class PythonAdapter(DebugAdapter):
    async def debug(self, req: DebugRequest) -> DebugResponse:
        logger.info("Starting Python debug session for user %s", req.user_id)
        ...
        logger.info("Session complete. Frames: %d", len(frames))
```
Now `PythonAdapter` changes for two reasons: debugpy protocol changes AND log format changes. Move structured logging to the service layer or a middleware.

---

### OCP — Open/Closed Principle

Open for extension, closed for modification — **at the factory boundary only**.

**Good — adding Rust = touching exactly one line in `factory.py`:**
```python
_REGISTRY = {
    "python": PythonAdapter,
    "go":     GoAdapter,
    "cpp":    CppAdapter,
    "java":   JavaAdapter,
    "js":     JsAdapter,
    "rust":   RustAdapter,   # ← one new line
}
```
`main.py`, `service.py`, `router.py` — untouched.

**Wrong — adding `if language == "rust"` in `service.py`:**
```python
# DO NOT DO THIS — service.py
async def debug(request: DebugRequest) -> DebugResponse:
    if request.language == "rust":
        adapter = RustAdapter()
    else:
        adapter = factory.get_adapter(request.language)
```
OCP violation. The service layer is now closed to modification — until someone adds another `if`.

---

### LSP — Liskov Substitution Principle

Any `DebugAdapter` subclass must work wherever the ABC is expected, with no caller-side special-casing.

**Good — every adapter satisfies the contract:**
```python
class DebugAdapter(ABC):
    @abstractmethod
    async def debug(self, req: DebugRequest) -> DebugResponse: ...

class GoAdapter(DebugAdapter):
    async def debug(self, req: DebugRequest) -> DebugResponse:
        # dlv exec → parse → return DebugResponse
        ...
```
`DebugService` calls `adapter.debug(req)` without knowing which adapter it holds.

**Wrong — `CppAdapter` requires a pre-call to `compile()`:**
```python
# DO NOT DO THIS
class CppAdapter(DebugAdapter):
    async def compile(self, code: str) -> Path: ...   # extra requirement
    async def debug(self, req: DebugRequest) -> DebugResponse:
        binary = await self.compile(req.code)         # silently depends on prior call
        ...
```
Any caller that skips `compile()` gets a crash. The ABC contract is violated. Move compilation inside `debug()`.

---

### ISP — Interface Segregation Principle

Clients should not be forced to depend on methods they don't use.

**Good — `DebugAdapter` ABC has exactly one abstract method:**
```python
class DebugAdapter(ABC):
    @abstractmethod
    async def debug(self, req: DebugRequest) -> DebugResponse: ...
```
Every adapter implements exactly what the service calls. No dead methods.

**Wrong — forcing adapters to implement `validate()`, `cleanup()`, `name()`:**
```python
# DO NOT DO THIS
class DebugAdapter(ABC):
    @abstractmethod
    async def debug(self, req: DebugRequest) -> DebugResponse: ...
    @abstractmethod
    def validate(self, code: str) -> bool: ...   # service doesn't call this
    @abstractmethod
    def cleanup(self) -> None: ...               # lifecycle is internal
    @abstractmethod
    def name(self) -> str: ...                   # only used in error messages
```
Now every new adapter must implement four methods to satisfy the ABC, even though the service only calls one.

---

### DIP — Dependency Inversion Principle

High-level modules depend on abstractions, not concretions. At the service/factory and observer boundaries.

**Good — `DebugService` depends on the factory abstraction:**
```python
# service.py
class DebugService:
    def __init__(self, factory: DebugAdapterFactory):
        self._factory = factory

    async def debug(self, req: DebugRequest) -> DebugResponse:
        adapter = self._factory.get_adapter(req.language)
        return await adapter.debug(req)
```
Tests inject a `FakeFactory`. Production injects the real one. `DebugService` never mentions `PythonAdapter`.

**Wrong — `DebugService` calling `PythonAdapter()` directly:**
```python
# DO NOT DO THIS
class DebugService:
    async def debug(self, req: DebugRequest) -> DebugResponse:
        if req.language == "python":
            return await PythonAdapter().debug(req)
```
Now the service is coupled to a concrete class. Adding a language requires modifying the service. Testing requires a real Python install.

---

### Separation of Concerns

The five layers must stay independent. Each layer speaks only to the layer directly below it.

```
main.py / router.py   ← HTTP: request parsing, response shaping, status codes
service.py            ← Orchestration: timeout, error normalization
factory.py            ← Dispatch: language → adapter class
adapters/*.py         ← Execution: subprocess lifecycle, protocol parsing
external tools        ← dlv, debugpy, lldb, jdb, node inspect
```

**Good — HTTP layer never imports adapters:**
```python
# router.py
from service import DebugService

@router.post("/debug")
async def debug_endpoint(req: DebugRequest, svc: DebugService = Depends()):
    return await svc.debug(req)
```

**Wrong — endpoint doing exception mapping AND adapter call AND logging:**
```python
# DO NOT DO THIS — router.py
@router.post("/debug")
async def debug_endpoint(req: DebugRequest):
    try:
        adapter = PythonAdapter() if req.language == "python" else GoAdapter()
        result = await adapter.debug(req)
        logger.info("Debug complete")
        return result
    except SubprocessError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
```
Three responsibilities, two layer violations, untestable without a real debugger.

---

### Law of Demeter

Don't chain more than two attribute/key accesses in a single expression. Extract intermediates.

**Good — variables at each step:**
```python
state = response["result"]["State"]
thread = state["currentThread"]
function_name = thread["function"]["name"]
```
When `dlv` renames `"function"` to `"func"`, you find the breakage in one line, not buried in a chain.

**Wrong — one long chain:**
```python
# DO NOT DO THIS
function_name = response["result"]["State"]["currentThread"]["function"]["name"]
```
Fragile to schema change. Opaque in a stack trace. Hard to log intermediate values when debugging.

---

## Section 3: When Principles Conflict

### KISS vs. DRY

> "If extracting common logic adds an abstraction layer, ask: does the common logic actually behave the same in both places, or does it just *look* textually similar? If the latter, leave the duplication."

The subprocess `try/finally` blocks look the same but are not — each adapter's cleanup is different. The timeout wrapper looks the same and *is* the same — extract it.

### KISS vs. OCP

> "OCP is only required at the factory boundary in this project. Don't try to make every class open for extension — that's YAGNI applied to OCP."

The `DebugService` does not need to be OCP. The `router.py` does not need to be OCP. Only `factory.py` must satisfy OCP, because the PRD explicitly requires adding a new language to touch exactly one file.

### DRY vs. SRP

When two classes share logic, extracting it into a third class sometimes forces both to depend on the third. If that third class has no cohesive identity — it's just a "utils" bag — the SRP loss is worse than the duplication. Tolerate the duplication.

---

## Section 4: Self-Check Questions

Run this list after writing each function, class, or module:

- [ ] Is this the simplest thing that could work? If no → simplify before committing.
- [ ] Did I build any code path not required by an acceptance criterion? If yes → delete it.
- [ ] Did I duplicate non-trivial logic that genuinely behaves the same in both places? If yes → extract it.
- [ ] Does this class have exactly one reason to change?
- [ ] Did I import anything that crosses a layer boundary? (e.g., `router.py` importing an adapter)
- [ ] Could a peer who hasn't read this file understand it in 30 seconds?
- [ ] Does adding a new language require touching only `factory.py`?
- [ ] Do all subprocess paths have a `try/finally` that terminates the child process?

---

## Section 5: Common Mistakes in This Project

These patterns have appeared in PRs and should be rejected at review:

**Class-based singleton with `__new__` override**
```python
# DO NOT DO THIS
class AdapterRegistry:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
```
Use a module-level dict. Python modules are already singletons.

**Hand-rolled Builder wrapping Pydantic models**
```python
# DO NOT DO THIS
class DebugResponseBuilder:
    def set_frames(self, frames): self._frames = frames; return self
    def set_output(self, out): self._output = out; return self
    def build(self): return DebugResponse(frames=self._frames, output=self._output)
```
`DebugResponse(frames=frames, output=output)` is the builder. Pydantic provides validation for free.

**Language branches outside the factory**
```python
# DO NOT DO THIS — anywhere except factory.py
if request.language == "go":
    timeout = 45
elif request.language == "java":
    timeout = 60
```
Language-specific behavior belongs in the adapter. Global timeouts belong in `TIMEOUT_SECONDS`.

**"Future-proofing" the adapter interface**
```python
# DO NOT DO THIS
class DebugAdapter(ABC):
    async def pre_debug_hook(self, req): pass    # "might be useful later"
    async def post_debug_hook(self, req): pass   # "for observability someday"
```
YAGNI. Add hooks when there's a concrete requirement.

**Catching bare `Exception` in the endpoint**
```python
# DO NOT DO THIS
@router.post("/debug")
async def debug_endpoint(req):
    try:
        return await svc.debug(req)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
```
Swallows `asyncio.CancelledError`, `KeyboardInterrupt`, and programming errors. Catch specific types: `UnsupportedLanguageError` → 400, `DebugTimeoutError` → 504, `DebugExecutionError` → 422.
