from __future__ import annotations

from debug_service.adapters.base import DebugAdapter
from debug_service.adapters.cpp_adapter import CppAdapter
from debug_service.adapters.go_adapter import GoAdapter
from debug_service.adapters.java_adapter import JavaAdapter
from debug_service.adapters.javascript_adapter import JavaScriptAdapter
from debug_service.adapters.python_adapter import PythonAdapter
from debug_service.exceptions import UnsupportedLanguageError


_REGISTRY: dict[str, type[DebugAdapter]] = {
    "python": PythonAdapter,
    "go": GoAdapter,
    "cpp": CppAdapter,
    "java": JavaAdapter,
    "javascript": JavaScriptAdapter,
    # Add new languages here, and only here.
}


class DebugAdapterFactory:
    """Resolve language identifiers to concrete debug adapters.

    The module-level registry is the system's Open/Closed extension point:
    adding a language means adding one registry entry and a new adapter file.
    """

    def get(self, language: str) -> DebugAdapter:
        adapter_type = _REGISTRY.get(language)
        if adapter_type is None:
            raise UnsupportedLanguageError(language)
        return adapter_type()

    @staticmethod
    def supported() -> list[str]:
        return sorted(_REGISTRY)
