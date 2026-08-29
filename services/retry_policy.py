"""Shared bounded retry classification for model and task-backed work.

Retries amplify model/provider cost, so only failures that can plausibly heal
without changing the request are eligible. Validation, authorization, parsing,
and ambiguous-effect failures are terminal and remain visible as data.
"""

from __future__ import annotations

import asyncio
from typing import Any

from google.genai import types

TRANSIENT_HTTP_STATUS_CODES = [408, 429, 500, 502, 503, 504]


def gemini_retry_options() -> types.HttpRetryOptions:
    """Two bounded retries for explicit transient HTTP statuses only."""
    return types.HttpRetryOptions(
        attempts=3,
        initial_delay=0.5,
        max_delay=4.0,
        exp_base=2.0,
        jitter=0.25,
        http_status_codes=TRANSIENT_HTTP_STATUS_CODES,
    )


def is_transient_exception(exc: BaseException) -> bool:
    """Return true only for timeout, transport, quota, or 5xx failures."""
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return False
    transient_types: tuple[type[BaseException], ...] = (
        TimeoutError, ConnectionError, OSError, asyncio.TimeoutError,
    )
    try:
        from google.api_core import exceptions as google_exceptions

        transient_types += (
            google_exceptions.TooManyRequests,
            google_exceptions.InternalServerError,
            google_exceptions.BadGateway,
            google_exceptions.ServiceUnavailable,
            google_exceptions.GatewayTimeout,
            google_exceptions.DeadlineExceeded,
        )
    except (ImportError, AttributeError):
        pass
    try:
        import httpx

        transient_types += (httpx.TimeoutException, httpx.TransportError)
    except ImportError:
        pass

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (FileNotFoundError, PermissionError)):
            return False
        if isinstance(current, transient_types):
            return True
        status = getattr(current, "status_code", None)
        if status in TRANSIENT_HTTP_STATUS_CODES:
            return True
        response: Any = getattr(current, "response", None)
        if getattr(response, "status_code", None) in TRANSIENT_HTTP_STATUS_CODES:
            return True
        current = current.__cause__ or current.__context__
    return False
