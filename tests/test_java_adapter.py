from __future__ import annotations

import subprocess
import time

import pytest

from debug_service.adapters.java_adapter import JavaAdapter, java_toolchain_available
from debug_service.exceptions import CompileError


pytestmark = pytest.mark.skipif(
    not java_toolchain_available(),
    reason="Java toolchain is required for Java adapter integration tests",
)


JAVA_PROGRAM = """
import java.util.*;

public class Main {
    public static void main(String[] args) {
        int x = 5;
        ArrayList<Integer> list = new ArrayList<>();
        list.add(1); list.add(2); list.add(3);
        HashMap<String,Integer> m = new HashMap<>();
        m.put("a", 1); m.put("b", 2);
        Stack<Integer> stack = new Stack<>();
        stack.push(10); stack.push(20); stack.push(30);
        System.out.println(x + list.size() + m.size() + stack.size());
    }
}
"""


def test_java_basic_trace_is_non_empty_and_stays_in_user_class() -> None:
    steps = JavaAdapter().debug(JAVA_PROGRAM)

    assert steps
    assert all(1 <= step.line < 100 for step in steps)


def test_java_class_name_uses_public_class_regex() -> None:
    code = """
public class MyClassRoom {
    public static void main(String[] args) {
        int x = 5;
    }
}
"""
    steps = JavaAdapter().debug(code)

    assert steps
    assert any(step.variables.get("x") == 5 for step in steps)


def test_java_int_variable() -> None:
    steps = JavaAdapter().debug(JAVA_PROGRAM)

    assert any(step.variables.get("x") == 5 for step in steps)


def test_java_arraylist_variable() -> None:
    steps = JavaAdapter().debug(JAVA_PROGRAM)

    assert any(step.variables.get("list") == [1, 2, 3] for step in steps)


def test_java_hashmap_variable() -> None:
    steps = JavaAdapter().debug(JAVA_PROGRAM)

    assert any(step.variables.get("m") == {"a": 1, "b": 2} for step in steps)


def test_java_stack_variable_uses_superclass_fields() -> None:
    steps = JavaAdapter().debug(JAVA_PROGRAM)

    assert any(step.variables.get("stack") == [10, 20, 30] for step in steps)


def test_java_back_to_back_debug_calls_succeed() -> None:
    first = JavaAdapter().debug(JAVA_PROGRAM)
    second = JavaAdapter().debug(JAVA_PROGRAM)

    assert first
    assert second


def test_java_compile_error_raises_compile_error() -> None:
    with pytest.raises(CompileError):
        JavaAdapter().debug("public class Bad { public static void main(String[] args) { nope } }")


def test_java_processes_are_cleaned_up() -> None:
    JavaAdapter().debug(JAVA_PROGRAM)
    time.sleep(0.3)

    for pattern in ("java.*DebugClient", "java.*agentlib:jdwp"):
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip() == ""


def test_java_factory_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    from debug_service import factory as factory_module
    from debug_service.factory import DebugAdapterFactory

    monkeypatch.setitem(factory_module._REGISTRY, "java", JavaAdapter)
    assert isinstance(DebugAdapterFactory().get("java"), JavaAdapter)
