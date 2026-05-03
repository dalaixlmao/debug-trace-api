from __future__ import annotations

import subprocess
import time

import pytest

from debug_service.adapters.go_adapter import GoAdapter, go_toolchain_available
from debug_service.exceptions import CompileError


pytestmark = pytest.mark.skipif(
    not go_toolchain_available(),
    reason="Go toolchain and Delve are required for Go adapter integration tests",
)


GO_PROGRAM = """package main

import "fmt"

type Person struct {
    Name string
    Age int
}

func main() {
    x := 5
    a := []int{1, 2, 3}
    p := Person{Name: "Ada", Age: 36}
    ptrValue := 7
    ptr := &ptrValue
    m := map[string]int{"a": 1, "b": 2}
    fmt.Println(x, a, p, *ptr, m)
}
"""


def test_go_basic_trace_is_non_empty_and_stays_in_main() -> None:
    steps = GoAdapter().debug(GO_PROGRAM)

    assert steps
    assert all(10 <= step.line <= 18 for step in steps)


def test_go_int_variable() -> None:
    steps = GoAdapter().debug(GO_PROGRAM)

    assert any(step.variables.get("x") == 5 for step in steps)


def test_go_slice_variable() -> None:
    steps = GoAdapter().debug(GO_PROGRAM)

    assert any(step.variables.get("a") == [1, 2, 3] for step in steps)


def test_go_struct_variable() -> None:
    steps = GoAdapter().debug(GO_PROGRAM)

    assert any(step.variables.get("p") == {"Name": "Ada", "Age": 36} for step in steps)


def test_go_pointer_variable_is_dereferenced() -> None:
    steps = GoAdapter().debug(GO_PROGRAM)

    assert any(step.variables.get("ptr") == 7 for step in steps)


def test_go_map_variable() -> None:
    steps = GoAdapter().debug(GO_PROGRAM)

    assert any(step.variables.get("m") == {"a": 1, "b": 2} for step in steps)


def test_go_compile_error_raises_compile_error() -> None:
    with pytest.raises(CompileError):
        GoAdapter().debug("package main\nfunc main() { missing_symbol }\n")


def test_go_delve_process_is_cleaned_up() -> None:
    GoAdapter().debug(GO_PROGRAM)
    time.sleep(0.3)

    result = subprocess.run(
        ["pgrep", "-f", "dlv exec"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == ""


def test_go_factory_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    from debug_service import factory as factory_module
    from debug_service.factory import DebugAdapterFactory

    monkeypatch.setitem(factory_module._REGISTRY, "go", GoAdapter)
    assert isinstance(DebugAdapterFactory().get("go"), GoAdapter)
