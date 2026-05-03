from __future__ import annotations

import subprocess
import time

import pytest

from debug_service.adapters.cpp_adapter import CppAdapter, cpp_toolchain_available
from debug_service.exceptions import CompileError


pytestmark = pytest.mark.skipif(
    not cpp_toolchain_available(),
    reason="clang++ and LLDB Python bindings are required for C++ adapter tests",
)


CPP_PROGRAM = """#include <iostream>
#include <string>
#include <vector>

int main() {
    int x = 5;
    std::string s = "hi";
    std::vector<int> v{1, 2, 3};
    int* p = &x;
    std::cout << x << s << v.size() << *p;
    return 0;
}
"""


def test_cpp_basic_trace_is_non_empty() -> None:
    steps = CppAdapter().debug(CPP_PROGRAM)

    assert steps


def test_cpp_int_variable() -> None:
    steps = CppAdapter().debug(CPP_PROGRAM)

    assert any(step.variables.get("x") == 5 for step in steps)


def test_cpp_string_variable() -> None:
    steps = CppAdapter().debug(CPP_PROGRAM)

    assert any(step.variables.get("s") == "hi" for step in steps)


def test_cpp_vector_variable() -> None:
    steps = CppAdapter().debug(CPP_PROGRAM)

    assert any(step.variables.get("v") == [1, 2, 3] for step in steps)


def test_cpp_pointer_variable_is_dereferenced() -> None:
    steps = CppAdapter().debug(CPP_PROGRAM)

    assert any(step.variables.get("p") == 5 for step in steps)


def test_cpp_trace_filters_lldb_sentinel_line() -> None:
    steps = CppAdapter().debug(CPP_PROGRAM)

    assert all(step.line != 4294967295 for step in steps)


def test_cpp_compile_error_raises_compile_error() -> None:
    with pytest.raises(CompileError):
        CppAdapter().debug("int main() { undefined_symbol; }\n")


def test_cpp_binary_process_is_cleaned_up() -> None:
    CppAdapter().debug(CPP_PROGRAM)
    time.sleep(0.3)

    result = subprocess.run(
        ["pgrep", "-f", r"/tmp/.*/prog"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == ""


def test_cpp_factory_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    from debug_service import factory as factory_module
    from debug_service.factory import DebugAdapterFactory

    monkeypatch.setitem(factory_module._REGISTRY, "cpp", CppAdapter)
    assert isinstance(DebugAdapterFactory().get("cpp"), CppAdapter)
