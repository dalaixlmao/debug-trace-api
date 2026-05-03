from __future__ import annotations

from debug_service.adapters.base import DebugAdapter
from debug_service.adapters.cpp_adapter import CppAdapter
from debug_service.adapters.go_adapter import GoAdapter
from debug_service.adapters.python_adapter import PythonAdapter
from debug_service.adapters.stub import StubAdapter
from debug_service.exceptions import UnsupportedLanguageError
from debug_service.models import Language


_REGISTRY: dict[str, type[DebugAdapter] | DebugAdapter] = {
    language.value: StubAdapter(language.value) for language in Language
}
_REGISTRY[Language.PYTHON.value] = PythonAdapter
_REGISTRY[Language.GO.value] = GoAdapter
_REGISTRY[Language.CPP.value] = CppAdapter


class DebugAdapterFactory:
    def get(self, language: str) -> DebugAdapter:
        adapter_or_type = _REGISTRY.get(language)
        if adapter_or_type is None:
            raise UnsupportedLanguageError(language)
        if isinstance(adapter_or_type, type):
            return adapter_or_type()
        return adapter_or_type

    def register(self, language: str, adapter: type[DebugAdapter] | DebugAdapter) -> None:
        _REGISTRY[language] = adapter
