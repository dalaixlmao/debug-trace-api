---
name: debug-adapter-implementation
description: How to implement, modify, or extend a DebugAdapter Strategy in the DebugTrace API project. Load when working on Stories 2-6 of SUPER_PRD.md, or when adding a new language. Covers the adapter contract, the per-language gotcha index, the OCP boundary, and the standard skeleton each adapter follows.
user-invocable: false
---

# Debug Adapter Implementation Guide

This skill governs all work on the five language adapters (Stories 2–6 in SUPER_PRD.md) and any future language additions. Load it before touching any file under `adapters/`.

---

## Section 1: The Contract

Every adapter is a **Strategy** (SUPER_PRD §2 pattern map). The ABC lives in `adapters/base.py`:

```python
from abc import ABC, abstractmethod
from ..models import DebugStep

class DebugAdapter(ABC):
    """Strategy interface. One method, by design (ISP)."""

    @abstractmethod
    def debug(self, code: str) -> list[DebugStep]:
        """Execute `code` under a debugger and return a per-line trace."""
```

One method. No properties. No class-level state. The interface is intentionally minimal — adding methods here forces all five adapters to change (OCP violation + LSP risk).

### Non-negotiable constraints every implementation MUST satisfy

| Constraint | Rule | Why |
|---|---|---|
| **Purity** | Same `code` in → same trace out (modulo nondeterminism the adapter controls) | Adapters are called from a stateless HTTP handler; callers expect determinism. Avoid surfacing non-deterministic runtime output (e.g. memory addresses in `repr`). |
| **No cross-call state** | No subprocess reuse, no module-level cache, no instance fields populated during `debug()` and read on the next call | Two concurrent requests may run the same adapter; shared state = data races. |
| **Subprocess cleanup** | Every spawned process MUST be terminated before `debug()` returns, even on exception | SUPER_PRD §10: "No subprocess (`dlv`, `node`, `java`, lldb) survives 5 s after request finish." Always use `try/finally`. Load the `subprocess-lifecycle-management` skill for `pgrep`/`kill` helpers. |
| **JSON-serializable variables** | `set`/`frozenset` → `list`; `bytes` → `repr()`; cap recursion at depth 4–5 | `DebugStep.variables` is serialized directly into the HTTP response by Pydantic. |
| **Let typed errors bubble** | Do not catch `CompileError`, `AdapterFailureError`, or `DebugTimeoutError` inside `debug()` | The service layer and HTTP route own error-to-status-code mapping (SUPER_PRD §1.4). Swallowing them here loses the correct HTTP status. |

---

## Section 2: The Standard Skeleton

Every adapter follows this shape. Fill in the blanks; don't deviate structurally without a documented reason.

```python
import tempfile
import subprocess
from .base import DebugAdapter
from ..models import DebugStep
from ..exceptions import CompileError, AdapterFailureError


class _LangAdapter(DebugAdapter):
    """Strategy implementation for <language>. See SUPER_PRD §4.X."""

    def debug(self, code: str) -> list[DebugStep]:
        with tempfile.TemporaryDirectory() as tmp:
            self._write_source(code, tmp)
            self._compile(tmp)          # raises CompileError on non-zero exit
            try:
                return self._trace(tmp)
            finally:
                self._cleanup_subprocesses()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _write_source(self, code: str, tmp: str) -> None:
        """Write user code to an appropriately named file inside tmp."""
        source_path = f"{tmp}/main.<ext>"
        with open(source_path, "w") as f:
            f.write(code)

    def _compile(self, tmp: str) -> None:
        """Run the language compiler; raise CompileError with sanitized stderr on failure."""
        result = subprocess.run(
            ["<compiler>", "<flags>", f"{tmp}/main.<ext>"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            # Strip temp-dir prefix to prevent path leakage to clients (SUPER_PRD §1.5)
            sanitized = result.stderr.replace(tmp + "/", "")
            raise CompileError(sanitized)

    def _trace(self, tmp: str) -> list[DebugStep]:
        """Drive the debugger protocol and collect one DebugStep per user line."""
        steps: list[DebugStep] = []
        # ... launch debugger, step through, collect variables ...
        return steps

    def _cleanup_subprocesses(self) -> None:
        """Terminate any subprocess handle stored during _trace.
        Called unconditionally in the try/finally above."""
        if hasattr(self, "_proc") and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
```

### Serialization helper (shared across adapters)

Put this in `adapters/serialization.py` and import it from each adapter. Do NOT duplicate it.

```python
def serialize(v, depth: int = 0):
    """Recursively convert v to a JSON-safe value."""
    if depth > 4:
        return "..."
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [serialize(x, depth + 1) for x in v]
    if isinstance(v, (set, frozenset)):
        return [serialize(x, depth + 1) for x in v]   # sets → lists
    if isinstance(v, dict):
        return {str(k): serialize(val, depth + 1) for k, val in v.items()}
    if isinstance(v, bytes):
        return repr(v)
    if hasattr(v, "__dict__"):
        return {k: serialize(val, depth + 1)
                for k, val in vars(v).items() if not k.startswith("__")}
    return repr(v)
```

### Per-language skeleton deviations

| Adapter | Deviations from the standard skeleton |
|---|---|
| **Python** | No `_compile` (uses `compile()` + `exec` in-process). No subprocess → no `_cleanup_subprocesses`. `sys.settrace` is the "debugger". No `tempfile.TemporaryDirectory` needed. |
| **JavaScript** | Compile is implicit in `node --inspect-brk` launch; merge compile into `_trace`. The temp dir holds the `.js` source file. |
| **Java** | `javac -g` is the compile step, but the output class name must be extracted from source before writing. The class file goes into `tmp`; the `java` process is launched from there. |

---

## Section 3: Per-Language Gotcha Index

Cross-reference SUPER_PRD §4 "Known Pitfalls" subsections for full prose. Listed in priority order (most painful first).

### Python (Story 2 — `adapters/python_adapter.py`)

1. **Always `sys.settrace(None)` in `finally`** — leaving the tracer installed after an exception silently cripples every subsequent request handled by the same worker process.

   ```python
   sys.settrace(tracer)
   try:
       exec(compiled, user_globals)
   finally:
       sys.settrace(None)    # ← must always run
   ```

2. **Filter by `frame.f_code.co_filename == "<string>"`** — without this filter, every line of every imported stdlib module (`os.py`, `abc.py`, `typing.py`, …) appears in the trace. `"<string>"` is the filename Python assigns when you `compile(code, "<string>", "exec")`.

3. **Exclude callables and `__dunder__` names** from the variables snapshot on every step:
   ```python
   {k: serialize(v) for k, v in frame.f_locals.items()
    if not k.startswith("__") and not callable(v)}
   ```

4. **Cap `serialize` depth at 4** — cyclic objects (e.g. `a = []; a.append(a)`) cause infinite recursion without the depth guard.

5. **Copy `frame.f_locals` before storing** — CPython reuses the locals dict between line events; a stored reference will mutate as execution continues, giving the wrong snapshot.

6. **Fresh `user_globals = {}` per call** — never use `globals()` from the adapter module; globals from call N must not leak into call N+1 (SUPER_PRD §2.2 AC #3).

### Go (Story 3 — `adapters/go_adapter.py`)

1. **Build flags `go build -gcflags=all=-N -l`** — both flags are mandatory. `-N` disables optimizations; `-l` disables inlining. Either missing → empty or absent variable info.

   ```bash
   go build -gcflags=all=-N -l -o /tmp/main /tmp/main.go
   ```

2. **Delve JSON response path is `state["result"]["State"]`** — NOT `state["result"]`. One envelope level deeper than you expect; off-by-one here yields `None` silently.

3. **Stop condition: `exited` state OR `function != "main.main"`** — without the function guard the adapter traces runtime bootstrap frames before `main.main` is entered.

4. **Map children arrive interleaved `[k, v, k, v, ...]`** — zip consecutive pairs:
   ```python
   children = variable["children"]
   result = {children[i]["value"]: serialize_delve(children[i+1])
             for i in range(0, len(children), 2)}
   ```

5. **Allocate a free port dynamically** — bind to `localhost:0`, read back the OS-assigned port. Hardcoded ports cause flaky failures under concurrent CI runs.

   ```python
   import socket
   with socket.socket() as s:
       s.bind(("localhost", 0))
       port = s.getsockname()[1]
   ```

### C++ (Story 4 — `adapters/cpp_adapter.py`)

1. **`clang++ -g -O0`** — both flags required. `-g` emits DWARF symbols; `-O0` prevents variable elision. Missing either → empty `variables`.

   ```bash
   clang++ -g -O0 -o /tmp/main /tmp/main.cpp
   ```

2. **`debugger.SetAsync(False)` before `CreateTarget`** — without this, LLDB events arrive asynchronously and the stepping logic races, producing out-of-order or missing steps.

3. **Skip line `4294967295` (`0xFFFFFFFF`, `UINT32_MAX`)** — LLDB emits this sentinel value as the line number for synthetic/internal frames. Include it and you get garbage step entries.

   ```python
   if frame.GetLineEntry().GetLine() == 0xFFFFFFFF:
       continue
   ```

4. **LLDB Python binding requires Xcode's Python** — on macOS `import lldb` only works from `/usr/bin/python3` (Xcode's). The venv's interpreter cannot find the lldb shared library. Run the LLDB tracing logic in a subprocess using `/usr/bin/python3`.

5. **`GetVariables(True, True, False, True)`** — positional args are `(arguments, locals, statics, in_scope_only)`. Wrong order silently returns the wrong subset.

### Java (Story 5 — `adapters/java_adapter.py`)

1. **`javac -g`** — mandatory. Without `-g`, the JVM strips the LocalVariableTable attribute and `frame.visibleVariables()` (JDWP) returns nothing.

   ```bash
   javac -g -d /tmp /tmp/UserCode.java
   ```

2. **Extract class name with regex** — `re.search(r'public class (\w+)', code)`. Do NOT use `split`. Split-based extraction breaks when `public class` is not at the start of a line or has preceding annotations.

3. **JDWP cold-start race: poll the port** — don't `time.sleep(0.5)` blindly. Poll with a retry loop:

   ```python
   import socket, time
   for _ in range(30):
       try:
           with socket.create_connection(("localhost", jdwp_port), timeout=0.2):
               break
       except OSError:
           time.sleep(0.1)
   ```

4. **`deleteEventRequests` before each new `StepRequest`** — stale `StepRequest` objects accumulate on the `VirtualMachine` and fire multiple times per step, producing duplicate trace entries.

5. **`ClassPrepareRequest` must use `SUSPEND_ALL`** — any other suspend policy lets other threads run during the prepare event and produces out-of-order variable state.

6. **`StepRequest` needs `addClassFilter(className)`** — without it, the adapter steps into JDK internals (`java.lang.*`, `sun.*`, `jdk.*`).

7. **`field()` must walk the `superclass()` chain** — fields declared in a superclass are not directly visible on the subtype via JDWP; walk up with `type = type.superclass()` until `None`.

### JavaScript (Story 6 — `adapters/javascript_adapter.py`)

1. **`ws.settimeout(5)`** — required for Node v25+. Without it, the WebSocket handshake hangs indefinitely on some build variants.

2. **Build `scriptId → url` map from `Debugger.scriptParsed` events** — Node v25 dropped `[[Entries]]` internal slot enumeration. Register a handler for `Debugger.scriptParsed` before enabling the debugger and capture the mapping there.

3. **`Map`/`Set` entries via `Runtime.callFunctionOn`** — use a small helper function injected over CDP rather than relying on internal slots:

   ```python
   entries_js = "(function(m){ return [...m.entries()].map(e=>({k:e[0],v:e[1]})) })"
   result = cdp("Runtime.callFunctionOn", {
       "functionDeclaration": entries_js,
       "objectId": obj_id,
       "returnByValue": True,
   })
   ```

4. **Uninitialized `let` bindings have `type === null` and no `objectId`** — return `"<uninitialized>"` instead of crashing on the missing field:

   ```python
   if prop.get("value", {}).get("type") is None:
       variables[prop["name"]] = "<uninitialized>"
       continue
   ```

5. **Filter Node module wrapper variables**: `exports`, `require`, `module`, `__filename`, `__dirname` — these are injected by Node's CommonJS wrapper and appear in the first frame's scope; exclude them from every step.

---

## Section 4: Adding a New Language

Follow this checklist exactly. It is intentionally minimal — the OCP boundary means nothing outside these five steps should change.

### Step 1 — Extend the `Language` enum (`models.py`)

```python
class Language(str, Enum):
    PYTHON = "python"
    GO = "go"
    CPP = "cpp"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    RUST = "rust"          # ← add one line
```

### Step 2 — Create `adapters/rust_adapter.py`

Implement `RustAdapter(DebugAdapter)` using the standard skeleton from Section 2. Conventions:
- `tempfile.TemporaryDirectory` scopes all artifacts.
- Raise `CompileError` (sanitized stderr — strip temp-dir prefix) on compile failure.
- Raise `AdapterFailureError` if the debugger protocol returns an unexpected response.
- `try/finally` around the trace subprocess.

### Step 3 — Register in `factory.py` (ONE line change)

```python
# factory.py — the only file with legitimate adapter imports
from .adapters.rust_adapter import RustAdapter   # new import

_REGISTRY: dict[str, type[DebugAdapter]] = {
    "python":     PythonAdapter,
    "go":         GoAdapter,
    "cpp":        CppAdapter,
    "java":       JavaAdapter,
    "javascript": JavaScriptAdapter,
    "rust":       RustAdapter,      # ← one new entry
}
```

### Step 4 — Write `tests/test_rust_adapter.py`

Minimum test surface:
- Happy path: a simple snippet produces `>= 1` steps with correct line numbers.
- Variables snapshot is non-empty and JSON-serializable (`json.dumps(step.variables)` succeeds).
- Compile error on bad syntax raises `CompileError`, not a generic exception.
- No subprocess survives (use `pgrep` fixture from `subprocess-lifecycle-management` skill).

### Step 5 — Run the full OCP verification suite

```bash
# OCP check (see Section 5)
grep -rE "from \.adapters\.\w+_adapter import" debug_service/ | grep -v factory.py

# Full test suite
pytest tests/ -q

# Story 7 AC #5: one-file-change assertion
git diff --name-only main | grep -v "factory.py\|rust_adapter.py\|test_rust"
# Expected: empty (only the three files above should appear in the diff)
```

### Hard stop

If you find yourself editing `main.py`, `service.py`, `session.py`, `decorators.py`, or any *existing* adapter file during a new-language addition — **stop**. Something has gone wrong with the OCP boundary. The only legitimate edits are `models.py` (enum), `factory.py` (registry entry + import), and the two new files (`rust_adapter.py`, `test_rust_adapter.py`).

---

## Section 5: The OCP Boundary Check

Run after **any** adapter work — new adapter, bug fix, or refactor.

```bash
# 1. Nothing except factory.py imports a concrete adapter
grep -rE "from \.adapters\.\w+_adapter import" debug_service/ | grep -v factory.py
# Expected: empty output

# 2. No adapter imports another adapter
grep -rE "from \.\w+_adapter import" debug_service/adapters/ | grep -v __init__
# Expected: empty output

# 3. Belt-and-suspenders: only factory.py and tests import *Adapter class names
grep -rn "Adapter" debug_service/ | grep -v "factory.py\|base.py\|test_\|DebugAdapter"
# Expected: empty output (or only type-annotation imports of DebugAdapter from base)
```

These three checks are equivalent to Story 7 AC #5 in SUPER_PRD.md and are required before any PR that touches the adapter layer.

---

## Section 6: Common Adapter Bugs and Diagnostics

| Symptom | Likely root cause | Diagnostic command |
|---|---|---|
| Trace contains entries from runtime/stdlib | Source-URL / `co_filename` filter missing or comparing against the wrong value | Add `print(frame.f_code.co_filename)` temporarily and inspect |
| `variables` dict is always empty | Missing debug compile flags (`-g`, `-gcflags=all=-N -l`, `javac -g`) | `file <binary>` to check DWARF; `javap -verbose <Class>` for LocalVariableTable |
| Subprocess survives request | Missing `try/finally` around `_trace`, or `_cleanup_subprocesses` not implemented | `pgrep dlv; pgrep node; pgrep java` after a test request |
| Port conflict in CI | Hardcoded port number | Replace with dynamic allocation (bind port 0, read back) |
| Race condition on debugger startup | Fixed `time.sleep` instead of poll-for-ready | Replace sleep with TCP connection retry loop |
| Duplicate trace steps (Java) | `StepRequest` not deleted before re-installing | Call `vm.eventRequestManager().deleteEventRequests(stepRequests)` |
| `RecursionError` during serialization | Depth cap absent or too large in `serialize()` | Ensure `depth > 4: return "..."` guard is present |
| `TypeError: Object of type set is not JSON serializable` | `_serialize` returning raw `set` | Map `set`/`frozenset` to `list` in the serialization helper |
| `UnsupportedLanguageError` after adding new language | `Language` enum not updated or typo in `_REGISTRY` key | Check enum value matches `_REGISTRY` key exactly (case-sensitive) |
