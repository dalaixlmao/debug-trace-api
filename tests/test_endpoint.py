from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from debug_service.adapters.base import DebugAdapter
from debug_service.decorators import with_timeout, with_validation
from debug_service.exceptions import AdapterFailureError, CompileError, UnsupportedLanguageError
from debug_service.models import DebugStep
from debug_service.observers import EventBus
from debug_service.service import DebugService


class FakeAdapter(DebugAdapter):
    def __init__(
        self,
        steps: list[DebugStep] | None = None,
        raises: Exception | None = None,
        sleep: float = 0,
    ) -> None:
        self._steps = steps or [DebugStep(line=1, variables={"x": 1})]
        self._raises = raises
        self._sleep = sleep

    def debug(self, code: str) -> list[DebugStep]:
        if self._sleep:
            time.sleep(self._sleep)
        if self._raises:
            raise self._raises
        return self._steps


class FakeFactory:
    def __init__(
        self,
        adapters: dict[str, DebugAdapter],
        timeout_seconds: float = 0.1,
    ) -> None:
        self._adapters = adapters
        self._timeout_seconds = timeout_seconds

    def get(self, language: str) -> DebugAdapter:
        try:
            adapter = self._adapters[language]
        except KeyError:
            raise UnsupportedLanguageError(language) from None
        adapter.debug = with_validation(with_timeout(self._timeout_seconds)(adapter.debug))  # type: ignore[method-assign]
        return adapter


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from debug_service import main

    factory = FakeFactory(
        {language: FakeAdapter() for language in ("python", "go", "cpp", "java", "javascript")}
    )
    monkeypatch.setattr(
        main,
        "service",
        DebugService(factory=factory, event_bus=EventBus()),
    )
    return TestClient(main.app)


@pytest.mark.parametrize("language", ["python", "go", "cpp", "java", "javascript"])
def test_language_happy_paths(client: TestClient, language: str) -> None:
    response = client.post("/debug", json={"language": language, "code": "x=1"})

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    assert body[0] == {"line": 1, "variables": {"x": 1}}


def test_unsupported_language_returns_400(client: TestClient) -> None:
    response = client.post(
        "/debug",
        json={"language": "rust", "code": "fn main(){}"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "unsupported_language"


def test_empty_code_returns_400(client: TestClient) -> None:
    response = client.post("/debug", json={"language": "python", "code": ""})

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "empty_code"


def test_missing_code_returns_422(client: TestClient) -> None:
    response = client.post("/debug", json={"language": "python"})

    assert response.status_code == 422


def test_compile_error_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    from debug_service import main

    factory = FakeFactory(
        {"go": FakeAdapter(raises=CompileError("/tmp/debugtrace/main.go: undefined"))}
    )
    monkeypatch.setattr(
        main,
        "service",
        DebugService(factory=factory, event_bus=EventBus()),
    )
    response = TestClient(main.app).post(
        "/debug",
        json={"language": "go", "code": "package main\nfunc main() { undefined_symbol }"},
    )

    assert response.status_code == 422
    body = response.json()["detail"]
    assert body["error"] == "compile_error"
    assert "undefined" in body["detail"]
    assert "/tmp/" not in body["detail"]


def test_timeout_returns_408_and_follow_up_request_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from debug_service import main

    adapters = {"python": FakeAdapter(sleep=0.2)}
    factory = FakeFactory(adapters, timeout_seconds=0.01)
    monkeypatch.setattr(
        main,
        "service",
        DebugService(factory=factory, event_bus=EventBus()),
    )
    client = TestClient(main.app)

    start = time.monotonic()
    timeout_response = client.post("/debug", json={"language": "python", "code": "x=1"})
    elapsed = time.monotonic() - start

    assert timeout_response.status_code == 408
    assert timeout_response.json()["detail"]["error"] == "timeout"
    assert elapsed < 0.1

    adapters["python"] = FakeAdapter()
    healthy_response = client.post("/debug", json={"language": "python", "code": "x=1"})
    assert healthy_response.status_code == 200


def test_adapter_crash_returns_500(monkeypatch: pytest.MonkeyPatch) -> None:
    from debug_service import main

    factory = FakeFactory({"python": FakeAdapter(raises=RuntimeError("boom"))})
    monkeypatch.setattr(
        main,
        "service",
        DebugService(factory=factory, event_bus=EventBus()),
    )

    response = TestClient(main.app).post(
        "/debug",
        json={"language": "python", "code": "x=1"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "error": "internal_error",
        "detail": "debug session failed",
    }
