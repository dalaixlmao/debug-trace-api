from __future__ import annotations

from typing import Any


MAX_DEPTH = 4


def serialize(value: Any, depth: int = 0) -> Any:
    """Convert runtime values into bounded, JSON-safe structures."""
    if depth > MAX_DEPTH:
        return "..."
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes):
        return repr(value)
    if isinstance(value, list | tuple):
        return [serialize(item, depth + 1) for item in value]
    if isinstance(value, set | frozenset):
        return [serialize(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {
            str(key): serialize(item, depth + 1)
            for key, item in value.items()
        }
    if hasattr(value, "__dict__"):
        return {
            key: serialize(item, depth + 1)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return repr(value)
