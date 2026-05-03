from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from debug_service.adapters.base import DebugAdapter
from debug_service.exceptions import AdapterFailureError, CompileError
from debug_service.models import DebugStep


class CppAdapter(DebugAdapter):
    """Strategy implementation that traces C++ code with LLDB."""

    def debug(self, code: str) -> list[DebugStep]:
        with tempfile.TemporaryDirectory(prefix="debugtrace-cpp-") as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "prog.cpp"
            binary_path = tmp_path / "prog"
            source_path.write_text(code, encoding="utf-8")

            self._compile(source_path, binary_path, tmp_path)
            return self._trace(binary_path, tmp_path)

    def _compile(self, source_path: Path, binary_path: Path, tmp_path: Path) -> None:
        result = subprocess.run(
            ["clang++", "-g", "-O0", "-o", str(binary_path), str(source_path)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).replace(str(tmp_path) + "/", "")
            raise CompileError(detail)

    def _trace(self, binary_path: Path, tmp_path: Path) -> list[DebugStep]:
        runner = _lldb_python()
        if runner is None:
            raise AdapterFailureError("lldb python module is not available")

        script_path = tmp_path / "trace_lldb.py"
        script_path.write_text(_LLDB_TRACE_SCRIPT, encoding="utf-8")
        result = subprocess.run(
            [runner, str(script_path), str(binary_path)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AdapterFailureError(result.stderr or result.stdout)

        try:
            raw_steps = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AdapterFailureError("lldb trace produced invalid json") from exc
        return [
            DebugStep(line=step["line"], variables=step["variables"])
            for step in raw_steps
            if step.get("line") != UINT32_MAX
        ]


UINT32_MAX = 0xFFFFFFFF


_LLDB_TRACE_SCRIPT = textwrap.dedent(
    r'''
    from __future__ import annotations

    import json
    import sys
    import time

    import lldb

    UINT32_MAX = 0xFFFFFFFF


    def wait_until_stopped_or_exited(process, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = process.GetState()
            if state in {lldb.eStateStopped, lldb.eStateExited, lldb.eStateCrashed, lldb.eStateDetached}:
                return state
            time.sleep(0.02)
        return process.GetState()


    def extract_value(value, depth=0, max_depth=4):
        if depth > max_depth:
            return "..."

        type_name = value.GetType().GetName() or ""
        summary = value.GetSummary()
        plain_value = value.GetValue()

        if "basic_string" in type_name or type_name in {"string", "std::string"}:
            return summary.strip('"') if summary else ""

        if value.TypeIsPointerType():
            if value.GetNumChildren() >= 1:
                return extract_value(value.GetChildAtIndex(0), depth + 1, max_depth)
            return plain_value or ""

        children = [
            value.GetChildAtIndex(index)
            for index in range(value.GetNumChildren())
        ]
        if _is_vector(type_name, value):
            vector_items = _vector_items(value, depth, max_depth)
            if vector_items is not None:
                return vector_items

        if children and all((child.GetName() or "").startswith("[") for child in children):
            return [extract_value(child, depth + 1, max_depth) for child in children]

        if children:
            aggregate = {}
            for child in children:
                name = child.GetName() or ""
                if not name or (name.startswith("__") and name not in {"__size_", "__begin_"}):
                    continue
                aggregate[name] = extract_value(child, depth + 1, max_depth)
            if summary:
                aggregate["_summary"] = summary.strip('"')
            return aggregate

        return _coerce_leaf(plain_value if plain_value is not None else summary)


    def extract_locals(frame):
        variables = frame.GetVariables(True, True, False, True)
        result = {}
        for index in range(variables.GetSize()):
            variable = variables.GetValueAtIndex(index)
            name = variable.GetName()
            if name:
                result[name] = extract_value(variable)
        return result


    def _is_vector(type_name, value):
        return "vector<" in type_name or "std::vector" in type_name or value.GetType().GetDisplayTypeName().startswith("std::vector")


    def _vector_items(value, depth, max_depth):
        synthetic = value.GetSyntheticValue()
        if synthetic and synthetic.GetNumChildren() > 0:
            return [
                extract_value(synthetic.GetChildAtIndex(index), depth + 1, max_depth)
                for index in range(synthetic.GetNumChildren())
            ]
        children = [
            value.GetChildAtIndex(index)
            for index in range(value.GetNumChildren())
        ]
        if children and all((child.GetName() or "").startswith("[") for child in children):
            return [extract_value(child, depth + 1, max_depth) for child in children]
        return None


    def _coerce_leaf(value):
        if value is None:
            return ""
        text = str(value).strip()
        if text in {"true", "false"}:
            return text == "true"
        try:
            return int(text, 0)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            return text.strip('"')


    def main():
        binary = sys.argv[1]
        debugger = lldb.SBDebugger.Create()
        debugger.SetAsync(False)
        process = None
        try:
            target = debugger.CreateTarget(binary)
            if not target or not target.IsValid():
                raise RuntimeError("could not create lldb target")
            breakpoint = target.BreakpointCreateByName("main")
            if not breakpoint or breakpoint.GetNumLocations() == 0:
                raise RuntimeError("could not set breakpoint at main")

            command_result = lldb.SBCommandReturnObject()
            debugger.GetCommandInterpreter().HandleCommand("run", command_result)
            if not command_result.Succeeded():
                raise RuntimeError(command_result.GetError() or "could not launch process")
            process = target.GetProcess()
            if not process or not process.IsValid():
                raise RuntimeError("could not launch process")
            wait_until_stopped_or_exited(process)

            steps = []
            seen = set()
            while process.GetState() == lldb.eStateStopped:
                thread = process.GetSelectedThread()
                if not thread or not thread.IsValid():
                    break
                frame = thread.GetFrameAtIndex(0)
                line = frame.GetLineEntry().GetLine()
                if line != UINT32_MAX:
                    variables = extract_locals(frame)
                    key = (line, json.dumps(variables, sort_keys=True, default=str))
                    if key not in seen:
                        steps.append({"line": line, "variables": variables})
                        seen.add(key)
                thread.StepOver()
                wait_until_stopped_or_exited(process)
            print(json.dumps(steps))
        finally:
            if process and process.IsValid() and process.GetState() != lldb.eStateExited:
                process.Kill()
            lldb.SBDebugger.Destroy(debugger)


    if __name__ == "__main__":
        main()
    '''
).lstrip()


def _lldb_python() -> str | None:
    candidates = [sys.executable, "/usr/bin/python3"]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        result = subprocess.run(
            [candidate, "-c", "import lldb"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return candidate
    return None


def cpp_toolchain_available() -> bool:
    return shutil.which("clang++") is not None and _lldb_python() is not None
