from __future__ import annotations

import sys
from types import FrameType
from typing import Any

from debug_service.adapters.base import DebugAdapter
from debug_service.adapters.serialization import serialize
from debug_service.models import DebugStep


_FILENAME = "<debugtrace-python>"


class PythonAdapter(DebugAdapter):
    """Strategy implementation that traces Python code with sys.settrace."""

    def debug(self, code: str) -> list[DebugStep]:
        steps: list[DebugStep] = []
        compiled = compile(code, _FILENAME, "exec")
        user_globals: dict[str, Any] = {}

        def tracer(frame: FrameType, event: str, arg: Any) -> Any:
            if event == "line" and frame.f_code.co_filename == _FILENAME:
                steps.append(
                    DebugStep(
                        line=frame.f_lineno,
                        variables=_snapshot_variables(frame.f_locals),
                    )
                )
            return tracer

        sys.settrace(tracer)
        try:
            exec(compiled, user_globals)
        finally:
            sys.settrace(None)
        return steps


def _snapshot_variables(locals_: dict[str, Any]) -> dict[str, Any]:
    return {
        name: serialize(value)
        for name, value in dict(locals_).items()
        if not name.startswith("__") and not callable(value)
    }
