from __future__ import annotations

import sys

import pytest

from debug_service.adapters.python_adapter import PythonAdapter


def test_basic_trace_line_order() -> None:
    steps = PythonAdapter().debug("x = 1\ny = 2\nprint(x + y)")

    assert [step.line for step in steps] == [1, 2, 3]


def test_variables_reflect_state_before_each_line() -> None:
    steps = PythonAdapter().debug("x = 1\ny = 2\nprint(x + y)")

    assert steps[1].variables == {"x": 1}
    assert steps[2].variables == {"x": 1, "y": 2}


def test_no_state_leak_between_calls() -> None:
    adapter = PythonAdapter()

    adapter.debug("leaked = 99")
    steps = adapter.debug("y = 1\npass")

    assert "leaked" not in steps[1].variables
    assert steps[1].variables == {"y": 1}


def test_filters_dunder_names_and_callables() -> None:
    code = "__secret__ = 1\ndef f():\n    pass\nclass C:\n    pass\nx = 1\npass"

    steps = PythonAdapter().debug(code)

    for step in steps:
        assert "__builtins__" not in step.variables
        assert "__secret__" not in step.variables
        assert "f" not in step.variables
        assert "C" not in step.variables


def test_nested_lists_are_depth_bounded() -> None:
    steps = PythonAdapter().debug("a = [[[[[1]]]]]\npass")

    assert steps[1].variables["a"] == [[[[["..."]]]]]


def test_objects_serialize_as_public_attributes_or_repr() -> None:
    code = """
class P:
    def __init__(self):
        self.x = 7
        self._hidden = 8
p = P()
q = object()
pass
""".strip()

    steps = PythonAdapter().debug(code)

    assert steps[-1].variables["p"] == {"x": 7}
    assert isinstance(steps[-1].variables["q"], str)
    assert steps[-1].variables["q"].startswith("<object object at ")


def test_imported_library_lines_are_excluded() -> None:
    steps = PythonAdapter().debug("import os\ncwd = os.getcwd()\npass")

    assert [step.line for step in steps] == [1, 2, 3]


def test_tracer_is_cleared_after_user_code_error() -> None:
    with pytest.raises(ValueError):
        PythonAdapter().debug("raise ValueError('boom')")

    assert sys.gettrace() is None
