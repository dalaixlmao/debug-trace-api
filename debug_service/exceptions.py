from __future__ import annotations

import re


_TEMP_PATH_RE = re.compile(r"(?:/private)?/tmp/\S+/")


def _sanitize_detail(detail: str) -> str:
    return _TEMP_PATH_RE.sub("<tmp>/", detail)


class DebugTraceError(Exception):
    error = "debugtrace_error"

    def payload(self) -> dict[str, str]:
        return {"error": self.error, "detail": str(self)}


class UnsupportedLanguageError(DebugTraceError):
    error = "unsupported_language"

    def __init__(self, language: str):
        super().__init__(f"unsupported language: {language}")


class EmptyCodeError(DebugTraceError):
    error = "empty_code"

    def __init__(self):
        super().__init__("code must not be empty")


class CompileError(DebugTraceError):
    error = "compile_error"

    def payload(self) -> dict[str, str]:
        detail = self.args[0] if self.args else ""
        return {"error": self.error, "detail": _sanitize_detail(str(detail))}


class DebugTimeoutError(DebugTraceError):
    error = "timeout"


class AdapterFailureError(DebugTraceError):
    error = "internal_error"

    def payload(self) -> dict[str, str]:
        return {"error": self.error, "detail": "debug session failed"}


class IllegalStateTransitionError(DebugTraceError):
    error = "illegal_state_transition"
