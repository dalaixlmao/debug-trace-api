from __future__ import annotations

import json
import re
import select
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from debug_service.adapters.base import DebugAdapter
from debug_service.exceptions import AdapterFailureError, CompileError
from debug_service.models import DebugStep


_CLASS_RE = re.compile(r"public class (\w+)")
_RESOURCE_DIR = Path(__file__).with_name("java_resources")


class JavaAdapter(DebugAdapter):
    """Strategy implementation that traces Java code with JDWP and JDI."""

    def debug(self, code: str) -> list[DebugStep]:
        class_name = _class_name(code)
        with tempfile.TemporaryDirectory(prefix="debugtrace-java-") as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / f"{class_name}.java"
            client_path = tmp_path / "DebugClient.java"
            source_path.write_text(code, encoding="utf-8")
            client_path.write_text(
                (_RESOURCE_DIR / "DebugClient.java").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            self._compile(source_path, tmp_path, debug_symbols=True)
            self._compile(client_path, tmp_path, debug_symbols=False)
            return self._trace(class_name, tmp_path)

    def _compile(self, source_path: Path, tmp_path: Path, *, debug_symbols: bool) -> None:
        command = ["javac"]
        if debug_symbols:
            command.append("-g")
        command.append(str(source_path))
        result = subprocess.run(
            command,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).replace(str(tmp_path) + "/", "")
            raise CompileError(detail)

    def _trace(self, class_name: str, tmp_path: Path) -> list[DebugStep]:
        proc: subprocess.Popen[bytes] | None = None
        try:
            port = _free_port()
            proc = subprocess.Popen(
                [
                    "java",
                    f"-agentlib:jdwp=transport=dt_socket,server=y,suspend=y,address=127.0.0.1:{port}",
                    class_name,
                ],
                cwd=tmp_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            _wait_for_jdwp(proc, port)
            result = subprocess.run(
                ["java", "DebugClient", str(port), class_name],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode != 0:
                raise AdapterFailureError(result.stderr or result.stdout)
            try:
                raw_steps = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise AdapterFailureError("java trace produced invalid json") from exc
            return [
                DebugStep(line=step["line"], variables=step["variables"])
                for step in raw_steps
                if step.get("line", 0) > 0
            ]
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def _class_name(code: str) -> str:
    match = _CLASS_RE.search(code)
    if match is None:
        raise CompileError("Java source must declare public class <Name>")
    return match.group(1)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_jdwp(proc: subprocess.Popen[bytes], port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    stderr = proc.stderr
    if stderr is not None:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AdapterFailureError("java process exited before JDWP became ready")
            ready, _, _ = select.select([stderr], [], [], 0.05)
            if not ready:
                continue
            line = stderr.readline().decode("utf-8", errors="replace")
            if f"address: {port}" in line:
                return
    _wait_for_port(port, max(0.1, deadline - time.monotonic()))


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise AdapterFailureError(f"jdwp port {port} did not open within {timeout}s")


def java_toolchain_available() -> bool:
    return shutil.which("java") is not None and shutil.which("javac") is not None
