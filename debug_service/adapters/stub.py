from __future__ import annotations

from debug_service.adapters.base import DebugAdapter
from debug_service.exceptions import CompileError
from debug_service.models import DebugStep


class StubAdapter(DebugAdapter):
    """Temporary Story 1 adapter used until concrete language stories land."""

    def __init__(self, language: str):
        self._language = language

    def debug(self, code: str) -> list[DebugStep]:
        if self._language == "go" and "undefined_symbol" in code:
            raise CompileError("undefined: undefined_symbol")
        return [DebugStep(line=1, variables={})]
