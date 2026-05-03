from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from itertools import count
from pathlib import Path
from typing import Any

import websocket

from debug_service.adapters.base import DebugAdapter
from debug_service.exceptions import AdapterFailureError
from debug_service.models import DebugStep


NODE_INTERNALS = {
    "arguments",
    "exports",
    "require",
    "module",
    "__filename",
    "__dirname",
}
MAP_FN = (
    "function() { const r = []; this.forEach((v, k) => r.push([k, v])); "
    "return JSON.stringify(r); }"
)
SET_FN = "function() { return JSON.stringify([...this]); }"
_WS_URL_RE = re.compile(r"ws://\S+")


class JavaScriptAdapter(DebugAdapter):
    """Strategy implementation that traces JavaScript code with V8 Inspector."""

    def debug(self, code: str) -> list[DebugStep]:
        with tempfile.TemporaryDirectory(prefix="debugtrace-js-") as tmp:
            source_path = Path(tmp) / "script.js"
            source_path.write_text(code, encoding="utf-8")
            return self._trace(source_path)

    def _trace(self, source_path: Path) -> list[DebugStep]:
        proc: subprocess.Popen[bytes] | None = None
        ws: websocket.WebSocket | None = None
        try:
            proc = subprocess.Popen(
                ["node", "--inspect-brk=0", str(source_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            ws_url = self._read_ws_url(proc)
            ws = websocket.create_connection(ws_url, timeout=5)
            ws.settimeout(5)
            session = _CdpSession(ws)
            return session.trace(source_path.name)
        finally:
            if ws is not None:
                ws.close()
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

    def _read_ws_url(self, proc: subprocess.Popen[bytes]) -> str:
        if proc.stderr is None:
            raise AdapterFailureError("node inspector stderr was not captured")
        for raw_line in iter(proc.stderr.readline, b""):
            line = raw_line.decode("utf-8", errors="replace")
            match = _WS_URL_RE.search(line)
            if match:
                return match.group(0)
            if proc.poll() is not None:
                break
        raise AdapterFailureError("node did not print inspector websocket url")


class _CdpSession:
    def __init__(self, ws: websocket.WebSocket) -> None:
        self._ws = ws
        self._ids = count(1)
        self._pending: dict[int, dict[str, Any]] = {}
        self._script_urls: dict[str, str] = {}

    def trace(self, script_name: str) -> list[DebugStep]:
        steps: list[DebugStep] = []
        self._send("Debugger.enable")
        self._send("Runtime.runIfWaitingForDebugger")

        while True:
            try:
                message = self._recv()
            except websocket.WebSocketTimeoutException:
                break
            except websocket.WebSocketConnectionClosedException:
                break

            method = message.get("method")
            if method == "Debugger.scriptParsed":
                params = message["params"]
                self._script_urls[params["scriptId"]] = params.get("url", "")
            elif method == "Debugger.paused":
                if self._record_pause(message, script_name, steps):
                    self._send("Debugger.stepOver")
                else:
                    self._send("Debugger.resume")
            elif method == "Inspector.detached":
                break
        return steps

    def _record_pause(
        self,
        message: dict[str, Any],
        script_name: str,
        steps: list[DebugStep],
    ) -> bool:
        frames = message["params"].get("callFrames", [])
        if not frames:
            return False
        frame = frames[0]
        location = frame["location"]
        url = self._script_urls.get(location["scriptId"], frame.get("url", ""))
        if not url.endswith(script_name):
            return False

        variables = self._extract_locals(frame)
        steps.append(
            DebugStep(line=location["lineNumber"] + 1, variables=variables)
        )
        return True

    def _extract_locals(self, frame: dict[str, Any]) -> dict[str, Any]:
        for scope in frame.get("scopeChain", []):
            if scope.get("type") != "local":
                continue
            object_id = scope["object"].get("objectId")
            if not object_id:
                continue
            props = self._get_props(object_id)
            return {
                prop["name"]: self._extract(prop.get("value", {}))
                for prop in props
                if self._is_user_local(prop)
            }
        return {}

    def _is_user_local(self, prop: dict[str, Any]) -> bool:
        name = prop.get("name", "")
        return name not in NODE_INTERNALS and not name.startswith("__")

    def _extract(self, value: dict[str, Any], depth: int = 0) -> Any:
        if depth > 4:
            return "..."
        value_type = value.get("type")
        subtype = value.get("subtype")
        if value_type is None and "objectId" not in value:
            return "<uninitialized>"
        if value_type in {"number", "boolean", "string"}:
            return value.get("value")
        if value_type in {"undefined", "function", "symbol"}:
            return None
        if subtype == "null":
            return None

        object_id = value.get("objectId")
        if not object_id:
            return value.get("description", repr(value))
        description = value.get("description", "")
        if subtype == "array":
            props = self._get_props(object_id)
            indexed = [
                prop
                for prop in props
                if prop.get("name", "").isdigit() and "value" in prop
            ]
            indexed.sort(key=lambda prop: int(prop["name"]))
            return [self._extract(prop["value"], depth + 1) for prop in indexed]
        if subtype == "map" or description.startswith("Map("):
            return self._extract_map(object_id)
        if subtype == "set" or description.startswith("Set("):
            return self._extract_set(object_id)
        if value_type == "object":
            return {
                prop["name"]: self._extract(prop["value"], depth + 1)
                for prop in self._get_props(object_id)
                if "value" in prop
                and not prop.get("name", "").startswith("__")
                and not prop.get("name", "").isdigit()
            }
        return description or repr(value)

    def _extract_map(self, object_id: str) -> dict[str, Any]:
        raw = self._call_function_on(object_id, MAP_FN)
        pairs = json.loads(raw.get("value", "[]"))
        return {str(key): value for key, value in pairs}

    def _extract_set(self, object_id: str) -> list[Any]:
        raw = self._call_function_on(object_id, SET_FN)
        return json.loads(raw.get("value", "[]"))

    def _get_props(self, object_id: str) -> list[dict[str, Any]]:
        response = self._send(
            "Runtime.getProperties",
            {"objectId": object_id, "ownProperties": True},
        )
        return response.get("result", [])

    def _call_function_on(self, object_id: str, declaration: str) -> dict[str, Any]:
        response = self._send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": declaration,
                "returnByValue": True,
            },
        )
        return response.get("result", {})

    def _send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = next(self._ids)
        payload: dict[str, Any] = {"id": message_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._ws.send(json.dumps(payload))
        return self._wait_for_response(message_id)

    def _wait_for_response(self, message_id: int) -> dict[str, Any]:
        if message_id in self._pending:
            return self._pending.pop(message_id)
        while True:
            message = self._recv()
            response_id = message.get("id")
            if response_id == message_id:
                if "error" in message:
                    raise AdapterFailureError(str(message["error"]))
                return message.get("result", {})
            if response_id is not None:
                self._pending[response_id] = message
                continue
            self._handle_event(message)

    def _recv(self) -> dict[str, Any]:
        return json.loads(self._ws.recv())

    def _handle_event(self, message: dict[str, Any]) -> None:
        if message.get("method") == "Debugger.scriptParsed":
            params = message["params"]
            self._script_urls[params["scriptId"]] = params.get("url", "")


def node_toolchain_available() -> bool:
    return shutil.which("node") is not None
