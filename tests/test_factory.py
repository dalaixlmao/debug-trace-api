from __future__ import annotations

import subprocess

import pytest

from debug_service.adapters.base import DebugAdapter
from debug_service.adapters.cpp_adapter import CppAdapter
from debug_service.adapters.go_adapter import GoAdapter
from debug_service.adapters.java_adapter import JavaAdapter
from debug_service.adapters.javascript_adapter import JavaScriptAdapter
from debug_service.adapters.python_adapter import PythonAdapter
from debug_service.exceptions import UnsupportedLanguageError
from debug_service.factory import DebugAdapterFactory
from debug_service.models import DebugStep


def test_returns_each_supported_adapter() -> None:
    factory = DebugAdapterFactory()

    assert isinstance(factory.get("python"), PythonAdapter)
    assert isinstance(factory.get("go"), GoAdapter)
    assert isinstance(factory.get("cpp"), CppAdapter)
    assert isinstance(factory.get("java"), JavaAdapter)
    assert isinstance(factory.get("javascript"), JavaScriptAdapter)


def test_unsupported_language_raises() -> None:
    with pytest.raises(UnsupportedLanguageError):
        DebugAdapterFactory().get("rust")


def test_factory_uses_module_level_singleton_registry() -> None:
    assert DebugAdapterFactory().supported() == DebugAdapterFactory().supported()
    assert DebugAdapterFactory().supported() == [
        "cpp",
        "go",
        "java",
        "javascript",
        "python",
    ]


def test_factory_picks_fake_language(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAdapter(DebugAdapter):
        def debug(self, code: str) -> list[DebugStep]:
            return [DebugStep(line=1, variables={"code": code})]

    from debug_service import factory as factory_module

    monkeypatch.setitem(factory_module._REGISTRY, "fake", FakeAdapter)

    adapter = DebugAdapterFactory().get("fake")

    assert isinstance(adapter, FakeAdapter)
    assert adapter.debug("anything") == [
        DebugStep(line=1, variables={"code": "anything"})
    ]


def test_factory_is_only_concrete_adapter_import_boundary() -> None:
    result = subprocess.run(
        [
            "grep",
            "-rE",
            r"from debug_service\.adapters\.\w+_adapter import",
            "debug_service/",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    leaks = [
        line for line in result.stdout.splitlines()
        if "factory.py" not in line
    ]

    assert leaks == []
