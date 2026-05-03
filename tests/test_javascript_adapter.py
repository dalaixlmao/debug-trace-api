from __future__ import annotations

import subprocess
import time

import pytest

from debug_service.adapters.javascript_adapter import (
    JavaScriptAdapter,
    node_toolchain_available,
)


pytestmark = pytest.mark.skipif(
    not node_toolchain_available(),
    reason="Node is required for JavaScript adapter integration tests",
)


def test_js_basic_trace_is_non_empty() -> None:
    steps = JavaScriptAdapter().debug("let x = 5;\nlet y = 7;\nconsole.log(x + y);")

    assert steps
    assert any(step.variables.get("x") == 5 for step in steps)
    assert any(step.variables.get("y") == 7 for step in steps)


def test_js_array_variable() -> None:
    steps = JavaScriptAdapter().debug("let arr = [1, 2, 3];\nconsole.log(arr);")

    assert any(step.variables.get("arr") == [1, 2, 3] for step in steps)


def test_js_object_variable() -> None:
    steps = JavaScriptAdapter().debug("let obj = { a: 1, b: 2 };\nconsole.log(obj);")

    assert any(step.variables.get("obj") == {"a": 1, "b": 2} for step in steps)


def test_js_map_variable() -> None:
    steps = JavaScriptAdapter().debug(
        'let m = new Map();\nm.set("k", 1);\nconsole.log(m);'
    )

    assert any(step.variables.get("m") == {"k": 1} for step in steps)


def test_js_set_variable() -> None:
    steps = JavaScriptAdapter().debug("let s = new Set([1, 2, 3]);\nconsole.log(s);")

    assert any(step.variables.get("s") == [1, 2, 3] for step in steps)


def test_js_uninitialized_let_variable() -> None:
    steps = JavaScriptAdapter().debug("let z;\nz = 5;\nconsole.log(z);")

    assert any(step.variables.get("z") == "<uninitialized>" for step in steps)


def test_js_node_internals_are_filtered() -> None:
    steps = JavaScriptAdapter().debug("let x = 1;\nconsole.log(x);")

    for step in steps:
        for name in ("exports", "require", "module", "__filename", "__dirname"):
            assert name not in step.variables


def test_js_no_hang_on_close() -> None:
    start = time.monotonic()

    JavaScriptAdapter().debug("let x = 1;")

    assert time.monotonic() - start < 10


def test_js_processes_are_cleaned_up() -> None:
    JavaScriptAdapter().debug("let x = 1;\nconsole.log(x);")
    time.sleep(0.3)

    result = subprocess.run(
        ["pgrep", "-f", "node --inspect"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == ""


def test_js_factory_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    from debug_service import factory as factory_module
    from debug_service.factory import DebugAdapterFactory

    monkeypatch.setitem(factory_module._REGISTRY, "javascript", JavaScriptAdapter)
    assert isinstance(DebugAdapterFactory().get("javascript"), JavaScriptAdapter)
