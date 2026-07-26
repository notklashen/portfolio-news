"""Small bounded retry helper shared by outbound APIs."""

from __future__ import annotations

from collections.abc import Callable
import logging
import time
from typing import Optional, TypeVar


T = TypeVar("T")
TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
TRANSIENT_EXCEPTION_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "ConnectionError",
    "HttpLib2Error",
    "RateLimitError",
    "ReadError",
    "ReadTimeout",
    "RemoteProtocolError",
    "TimeoutException",
}


def status_code_for_error(exc: BaseException) -> Optional[int]:
    for candidate in (exc, getattr(exc, "response", None), getattr(exc, "resp", None)):
        if candidate is None:
            continue
        for name in ("status_code", "status"):
            value = getattr(candidate, name, None)
            if isinstance(value, int):
                return value
    return None


def is_transient_error(exc: BaseException) -> bool:
    status = status_code_for_error(exc)
    if status is not None:
        return status in TRANSIENT_STATUS_CODES
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return any(cls.__name__ in TRANSIENT_EXCEPTION_NAMES for cls in type(exc).__mro__)


def retry_call(
    function: Callable[[], T],
    *,
    operation: str,
    attempts: int,
    base_delay: float = 1.0,
    max_delay: float = 8.0,
    retryable: Callable[[BaseException], bool] = is_transient_error,
    sleep: Callable[[float], None] = time.sleep,
    logger: Optional[logging.Logger] = None,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    log = logger or logging.getLogger(__name__)
    for attempt in range(1, attempts + 1):
        try:
            return function()
        except Exception as exc:
            if attempt >= attempts or not retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            log.warning(
                "transient_api_error",
                extra={
                    "operation": operation,
                    "attempt": attempt,
                    "next_delay_seconds": delay,
                    "error_type": type(exc).__name__,
                    "status_code": status_code_for_error(exc),
                },
            )
            sleep(delay)
    raise AssertionError("unreachable")
