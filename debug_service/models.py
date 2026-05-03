from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Language(str, Enum):
    PYTHON = "python"
    GO = "go"
    CPP = "cpp"
    JAVA = "java"
    JAVASCRIPT = "javascript"


class DebugRequest(BaseModel):
    language: str
    code: str


class DebugStep(BaseModel):
    line: int = Field(ge=1)
    variables: dict[str, Any]
