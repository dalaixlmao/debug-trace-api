from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from debug_service.adapters.base import DebugAdapter
from debug_service.exceptions import AdapterFailureError, CompileError
from debug_service.models import DebugStep


class GoAdapter(DebugAdapter):
    """Strategy implementation that traces Go code with Delve."""

    def debug(self, code: str) -> list[DebugStep]:
        with tempfile.TemporaryDirectory(prefix="debugtrace-go-") as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "main.go"
            binary_path = tmp_path / "prog"
            source_path.write_text(code, encoding="utf-8")

            self._compile(source_path, binary_path, tmp_path)
            return self._trace(source_path, binary_path)

    def _compile(self, source_path: Path, binary_path: Path, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                "go",
                "build",
                "-gcflags=all=-N -l",
                "-o",
                str(binary_path),
                str(source_path),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).replace(str(tmp_path) + "/", "")
            raise CompileError(detail)

    def _trace(self, source_path: Path, binary_path: Path) -> list[DebugStep]:
        proc: subprocess.Popen[bytes] | None = None
        sock: socket.socket | None = None
        try:
            port = _free_port()
            proc = subprocess.Popen(
                [
                    "dlv",
                    "exec",
                    str(binary_path),
                    "--headless",
                    "--api-version=2",
                    f"--listen=127.0.0.1:{port}",
                    "--accept-multiclient",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _wait_for_port(port)
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            sock.settimeout(5)
            rpc = _DelveRpcClient(sock)

            main_line = _find_main_line(source_path)
            rpc.call(
                "RPCServer.CreateBreakpoint",
                {"breakPoint": {"file": str(source_path), "line": main_line}},
            )
            state = _state_from_command(rpc.call("RPCServer.Command", {"name": "continue"}))

            steps: list[DebugStep] = []
            seen: set[tuple[int, str]] = set()
            while _in_main(state):
                line = state["currentThread"]["line"]
                variables = self._local_variables(rpc)
                key = (line, json.dumps(variables, sort_keys=True, default=str))
                if key not in seen:
                    steps.append(DebugStep(line=line, variables=variables))
                    seen.add(key)
                state = _state_from_command(rpc.call("RPCServer.Command", {"name": "next"}))

            return steps
        finally:
            if sock is not None:
                sock.close()
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

    def _local_variables(self, rpc: "_DelveRpcClient") -> dict[str, Any]:
        response = rpc.call(
            "RPCServer.ListLocalVars",
            {
                "scope": {"goroutineID": -1, "frame": 0},
                "cfg": {
                    "followPointers": True,
                    "maxVariableRecurse": 4,
                    "maxStringLen": 256,
                    "maxArrayValues": 256,
                    "maxStructFields": -1,
                },
            },
        )
        variables = response.get("result", {}).get("Variables", [])
        return {
            variable["name"]: _extract_delve_value(variable)
            for variable in variables
            if variable.get("name") and not variable.get("name", "").startswith("~")
        }


class _DelveRpcClient:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._next_id = 1
        self._buffer = b""

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": [params or {}],
            "id": request_id,
        }
        self._sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")

        while True:
            response = self._read_message()
            if response.get("id") != request_id:
                continue
            if response.get("error"):
                raise AdapterFailureError(str(response["error"]))
            return response

    def _read_message(self) -> dict[str, Any]:
        while b"\n" not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise AdapterFailureError("delve connection closed")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise AdapterFailureError(f"delve port {port} did not open within {timeout}s")


def _find_main_line(source_path: Path) -> int:
    for index, line in enumerate(source_path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip().startswith("func main()"):
            return index
    return 1


def _state_from_command(response: dict[str, Any]) -> dict[str, Any]:
    return response.get("result", {}).get("State", {})


def _in_main(state: dict[str, Any]) -> bool:
    if state.get("exited"):
        return False
    thread = state.get("currentThread") or {}
    function = thread.get("function") or {}
    return function.get("name") == "main.main"


def _extract_delve_value(variable: dict[str, Any], depth: int = 0) -> Any:
    if depth > 4:
        return "..."

    kind = variable.get("kind", 0)
    value = variable.get("value", "")
    children = variable.get("children", [])

    if kind == 1:
        return value == "true"
    if 2 <= kind <= 11:
        return int(value, 0) if value else 0
    if kind in (13, 14):
        return float(value) if value else 0.0
    if kind == 24:
        return value
    if kind in (17, 23):
        return [_extract_delve_value(child, depth + 1) for child in children]
    if kind == 20:
        return _extract_delve_value(children[0], depth + 1) if children else None
    if kind == 21:
        return {
            _extract_delve_value(children[index], depth + 1): _extract_delve_value(
                children[index + 1],
                depth + 1,
            )
            for index in range(0, len(children) - 1, 2)
        }
    if kind == 22:
        return _extract_delve_value(children[0], depth + 1) if children else None
    if kind == 25:
        return {
            child["name"]: _extract_delve_value(child, depth + 1)
            for child in children
            if child.get("name")
        }
    return value


def go_toolchain_available() -> bool:
    return shutil.which("go") is not None and shutil.which("dlv") is not None
