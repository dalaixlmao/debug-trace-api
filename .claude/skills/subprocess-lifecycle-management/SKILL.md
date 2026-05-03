---
name: subprocess-lifecycle-management
description: How to spawn, monitor, time-out, and clean up subprocesses in DebugTrace adapters. Load when implementing or modifying any code that uses subprocess.Popen, subprocess.run, or spawns external debuggers (dlv, node, java, lldb-launched binaries). Story 3-6 critical reading.
user-invocable: false
---

# Subprocess Lifecycle Management

Four of the five adapters spawn external processes — `dlv` (Story 3), an lldb-launched binary (Story 4), `java` (Story 5), and `node --inspect-brk` (Story 6). Each MUST clean up reliably, even on timeout or exception. Load this skill alongside `debug-adapter-implementation` when working on any of those four adapters.

---

## Section 1: The Cleanup Contract

Every adapter that spawns a subprocess MUST guarantee all three paths terminate the child before `debug()` returns:

| Path | Guarantee |
|---|---|
| **Success** | Subprocess terminated + waited after the last step is collected. |
| **Exception** (user code raises, protocol error, etc.) | `finally` block fires; subprocess terminated before exception propagates. |
| **Timeout** | `with_timeout` (decorators.py) raises `DebugTimeoutError` in the calling thread, but does **not** kill the subprocess — the adapter's own `try/finally` is responsible. See Section 6 for the interaction detail. |

Additionally:
- No file handles remain open (temp source files, pipe FDs, socket FDs).
- No sockets remain bound (Delve API port, JDWP port, Node inspector port).

**Verification:** within 2 seconds of any request completing, the following must all return empty:

```bash
pgrep -f "dlv exec"
pgrep -f "node --inspect"
pgrep -f "java.*DebugClient"
pgrep -f "/tmp/.*/prog"     # C++ compiled binary
```

---

## Section 2: The Standard try/finally Pattern

This is the canonical shape. Do not deviate — every subprocess-spawning `_trace` method must look like this.

```python
import subprocess
from subprocess import PIPE

def _trace(self, tmp: str) -> list[DebugStep]:
    proc = None                           # ← initialize BEFORE the try
    try:
        proc = subprocess.Popen(
            ["<debugger>", "<args>"],
            stdout=PIPE,
            stderr=PIPE,
            cwd=tmp,
        )
        # ... drive the debugger protocol, collect steps ...
        return result
    finally:
        if proc and proc.poll() is None:  # only if running
            proc.terminate()              # SIGTERM — lets child clean up
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()               # SIGKILL — last resort
                proc.wait()               # always wait; no zombies
```

### Why each line matters

- **`proc = None` before `try`** — if `Popen` itself raises (binary not found, permission error), `proc` is still defined in `finally`. Without it, `finally` would hit `NameError`.
- **`proc.poll() is None`** — skip the terminate if the process already exited cleanly; avoids `ProcessLookupError` on a reaped PID.
- **`terminate()` before `kill()`** — `SIGTERM` gives the child a chance to flush buffers and release its own resources (e.g., Delve releases the debug port). `SIGKILL` skips that.
- **`proc.wait()` unconditionally** — without it the child stays as a zombie in the process table, holding the PID and any open FDs until the parent exits.

### Multiple subprocesses

If `_trace` spawns more than one process (e.g., the compiled binary *and* `dlv exec`), track them all:

```python
procs: list[subprocess.Popen] = []
try:
    procs.append(subprocess.Popen([...]))
    procs.append(subprocess.Popen([...]))
    # ...
finally:
    for p in reversed(procs):      # reverse = child-first, then parent
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
```

---

## Section 3: Free Port Allocation

Never hardcode a port. Hardcoded ports cause "Address already in use" failures whenever two requests run concurrently in CI.

```python
import socket

def _free_port() -> int:
    """Ask the OS for an available port, then release it for the subprocess."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
```

Using `with` ensures the socket is closed before the function returns. The OS will not immediately reuse the port, giving the subprocess a narrow window to bind it.

**Race caveat:** there is an inherent TOCTOU race between `close()` and the subprocess binding. In CI under high parallelism this can still collide. Mitigation — retry once with a fresh port on `"Address already in use"`:

```python
def _start_debugger_with_retry(self, tmp: str) -> tuple[subprocess.Popen, int]:
    for _ in range(2):
        port = _free_port()
        try:
            proc = subprocess.Popen(["dlv", "exec", "--headless",
                                     f"--listen=127.0.0.1:{port}", ...])
            return proc, port
        except OSError as e:
            if "Address already in use" in str(e):
                continue
            raise
    raise AdapterFailureError("could not bind debugger port after 2 attempts")
```

---

## Section 4: Polling vs. Sleeping for Readiness

**Bad pattern — fixed sleep:**
```python
proc = subprocess.Popen(["dlv", ...])
time.sleep(0.5)   # hoping dlv is ready — flakes on slow CI machines
```

**Good pattern — poll the port:**
```python
import socket, time
from ..exceptions import AdapterFailureError

def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    """Block until the given port accepts TCP connections, or raise."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AdapterFailureError(f"port {port} did not open within {timeout}s")
```

Call `_wait_for_port` immediately after `Popen`, before sending any protocol message. The 0.05 s sleep inside the loop keeps CPU usage negligible while still detecting readiness within ~50 ms of the process being ready.

**Where this pattern is required:**

| Adapter | Subprocess | Port |
|---|---|---|
| Go — Story 3 | `dlv exec --headless --listen=:PORT` | Delve DAP/JSON-RPC port |
| Java — Story 5 | `java -agentlib:jdwp=transport=dt_socket,server=y,address=PORT` | JDWP port |
| JavaScript — Story 6 | `node --inspect-brk=127.0.0.1:PORT` | V8 Inspector WebSocket port |

C++ (Story 4) drives LLDB in-process via the Python binding — no port needed.

---

## Section 5: stdout/stderr Handling

### Mode A — Synchronous capture (compile steps)

Use `subprocess.run` with `capture_output=True` when you wait for the process to finish before reading output. This is correct for compile steps where you want all of stdout/stderr at once.

```python
result = subprocess.run(
    ["go", "build", "-gcflags=all=-N -l", "-o", f"{tmp}/prog", f"{tmp}/main.go"],
    capture_output=True,
    text=True,
    cwd=tmp,
)
if result.returncode != 0:
    sanitized = result.stderr.replace(tmp + "/", "")
    raise CompileError(sanitized)
```

### Mode B — Asynchronous line-by-line (live stream from long-running process)

Use `Popen` with `stderr=PIPE` and read lines incrementally when you need to extract data from a running process's output stream — e.g., reading the WebSocket URL that Node prints on stderr (Story 6).

```python
import re

proc = subprocess.Popen(
    ["node", "--inspect-brk=127.0.0.1:0", f"{tmp}/code.js"],
    stderr=PIPE,
    stdout=PIPE,
)

ws_url = None
for line in iter(proc.stderr.readline, b""):
    text = line.decode()
    m = re.search(r"ws://[\w.:/]+", text)
    if m:
        ws_url = m.group(0)
        break
if ws_url is None:
    raise AdapterFailureError("node did not print inspector URL")
```

### What to avoid

| Anti-pattern | Consequence |
|---|---|
| `proc.stdout.read()` on a running process | Blocks until EOF (= process exit). Deadlock if the process is waiting for you to read stderr. |
| `proc.communicate()` mid-session | Same deadlock risk; `communicate()` is only safe when you don't send further input and want to wait for termination. |
| Mixing Mode A and Mode B on the same `Popen` | Use one or the other; mixing leads to partially consumed pipes and deadlock. |

---

## Section 6: Timeout Interaction

The `@with_timeout` decorator in `decorators.py` raises `DebugTimeoutError` after the wall-clock budget expires. It does this by running `debug()` in a separate thread and joining with a timeout. **It does not kill the thread or any subprocess the thread spawned.**

```
Thread A (request handler):
  calls adapter.debug(code)
    ↓ timeout decorator moves adapter.debug into Thread B
Thread B (adapter):
  proc = Popen(["dlv", ...])
  ... stepping loop ...    ← still running after timeout fires
Thread A:
  join(timeout=30) → DebugTimeoutError raised in Thread A
  Thread B: still running, will clean up when the loop exits naturally
```

**Implications:**

1. If a single debugger RPC call takes 30 seconds (e.g., Delve waiting for a breakpoint that never fires), the thread will be stuck in that call even after the timeout fires. The subprocess lives until that call returns or the process is killed externally.

2. The subprocess will eventually be cleaned up — when Thread B's `try/finally` runs. But that may be seconds or minutes after the HTTP response was already sent.

**Mitigations — set socket-level timeouts on all RPC clients:**

```python
# Delve (Story 3) — set on the socket before use
sock = socket.create_connection(("127.0.0.1", port))
sock.settimeout(5)         # each recv() call times out in 5 s

# Node WebSocket (Story 6)
ws = websocket.WebSocket()
ws.settimeout(5)           # required for Node v25+

# Java JDWP (Story 5) — handled inside the JDI client's connect timeout
```

With socket timeouts, the RPC call raises `socket.timeout` quickly, the loop exits, and `finally` runs, terminating the subprocess well within the timeout window.

---

## Section 7: Verification Commands

Run these manually after any subprocess-related change, and add the `pgrep` checks as a pytest fixture in `conftest.py`.

### Manual smoke test

```bash
# Start the server
uvicorn debug_service.main:app --port 8000 &

# Fire a request that will time out (infinite loop)
curl -X POST http://localhost:8000/debug \
  -d '{"language":"python","code":"while True: pass"}' \
  -H 'Content-Type: application/json' &

# Wait for the timeout (default 30 s) plus a buffer
sleep 35

# Verify no leaks — all four must print nothing
pgrep -f "dlv exec"       && echo "LEAK: dlv"
pgrep -f "node --inspect" && echo "LEAK: node"
pgrep -f "java.*Debug"    && echo "LEAK: java"
pgrep -f "/tmp/.*/prog"   && echo "LEAK: cpp binary"
echo "leak check complete"
```

### pytest fixture

```python
# tests/conftest.py
import subprocess
import pytest

PROCESS_PATTERNS = ["dlv exec", "node --inspect", "java.*Debug", r"/tmp/.*/prog"]

@pytest.fixture(autouse=True)
def assert_no_subprocess_leak():
    yield
    for pattern in PROCESS_PATTERNS:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True
        )
        assert result.stdout.strip() == "", (
            f"Subprocess leak detected matching '{pattern}':\n{result.stdout}"
        )
```

---

## Section 8: Common Bugs

| Bug | Symptom | Fix |
|---|---|---|
| `proc.terminate()` without `wait()` | Zombie processes accumulate; `pgrep` still shows the PID | Always call `proc.wait()` (or `proc.wait(timeout=N)` + `proc.kill()` + `proc.wait()`) |
| Catching `subprocess.TimeoutExpired` without calling `kill()` | Process survives; `wait()` blocks the finally forever | After catching `TimeoutExpired`, always `proc.kill(); proc.wait()` |
| Hardcoded port | "Address already in use" in CI when two tests run in parallel | Use `_free_port()` from Section 3 |
| `time.sleep(N)` for readiness | Flaky failures on slow CI — process not ready when sleep ends | Use `_wait_for_port()` poll loop from Section 4 |
| `proc.stdout.read()` on running process | Deadlock — blocks until EOF which never comes | Use `readline()` in a loop (Mode B) or `subprocess.run` only after process exits |
| `with subprocess.Popen(...) as proc:` | Looks clean but `__exit__` calls `wait()` only — won't terminate a running process | Use the explicit `try/finally` pattern from Section 2 instead |
| `proc = None` inside the `try` block | If `Popen` raises, `finally` hits `NameError: proc` | Declare `proc = None` *before* the `try` block |
| No socket timeout on RPC client | After `DebugTimeoutError`, adapter thread stays stuck in a blocking `recv()` call | Set `sock.settimeout(5)` or `ws.settimeout(5)` on every RPC socket (Section 6) |
