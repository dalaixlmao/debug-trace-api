from __future__ import annotations

import functools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import TypeVar

from debug_service.exceptions import (
    AdapterFailureError,
    DebugTimeoutError,
    DebugTraceError,
    EmptyCodeError,
)


T = TypeVar("T")


def with_validation(fn: Callable[..., T]) -> Callable[..., T]:
    @functools.wraps(fn)
    def wrapper(code: str, *args: object, **kwargs: object) -> T:
        if not isinstance(code, str) or not code.strip():
            raise EmptyCodeError()
        return fn(code, *args, **kwargs)

    return wrapper


def with_timeout(seconds: float = 30.0) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(code: str, *args: object, **kwargs: object) -> T:
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(fn, code, *args, **kwargs)
            try:
                return future.result(timeout=seconds)
            except FutureTimeoutError as exc:
                raise DebugTimeoutError(f"exceeded {seconds:g}s") from exc
            except DebugTraceError:
                raise
            except Exception as exc:
                raise AdapterFailureError(str(exc)) from exc
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        return wrapper

    return decorator
