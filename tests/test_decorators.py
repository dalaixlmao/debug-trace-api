from __future__ import annotations

import time

import pytest

from debug_service.adapters.base import DebugAdapter
from debug_service.decorators import with_timeout, with_validation
from debug_service.exceptions import (
    AdapterFailureError,
    DebugTimeoutError,
    EmptyCodeError,
)
from debug_service.factory import DebugAdapterFactory
from debug_service.models import DebugStep


def test_validation_rejects_empty_code_before_call() -> None:
    called = False

    @with_validation
    def debug(code: str) -> list[DebugStep]:
        nonlocal called
        called = True
        return []

    with pytest.raises(EmptyCodeError):
        debug("   ")

    assert called is False


def test_timeout_raises_promptly() -> None:
    @with_timeout(0.01)
    def debug(code: str) -> list[DebugStep]:
        time.sleep(0.1)
        return []

    start = time.monotonic()
    with pytest.raises(DebugTimeoutError):
        debug("x=1")

    assert time.monotonic() - start < 0.08


def test_unexpected_adapter_error_is_normalized() -> None:
    @with_timeout(0.1)
    def debug(code: str) -> list[DebugStep]:
        raise FileNotFoundError("node")

    with pytest.raises(AdapterFailureError):
        debug("x=1")


def test_factory_applies_validation_and_timeout_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowAdapter(DebugAdapter):
        def debug(self, code: str) -> list[DebugStep]:
            time.sleep(0.1)
            return [DebugStep(line=1, variables={})]

    from debug_service import factory as factory_module

    monkeypatch.setitem(factory_module._REGISTRY, "slow", SlowAdapter)
    adapter = DebugAdapterFactory(timeout_seconds=0.01).get("slow")

    with pytest.raises(EmptyCodeError):
        adapter.debug("")

    with pytest.raises(DebugTimeoutError):
        adapter.debug("x=1")
