"""Shared tool helpers."""

import asyncio
import logging

import nest_asyncio


def run(coro):
    """Run an async service-layer call from ADK's tool context (loop may or may
    not already be running depending on the surface)."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    except Exception as exc:  # noqa: BLE001 - this is the model-facing boundary
        logging.getLogger(__name__).exception("tool service call failed")
        return {
            "status": "error",
            "error": True,
            "message": f"service call failed: {exc}"[:300],
        }


def failed(result) -> bool:
    """True when a run() result is an error dict — from the exception boundary
    above or a service's errors-as-data. Callers that wrap coroutines returning
    non-dict values (bool, str, None) MUST check this before truthiness: the
    error dict is truthy, so `if not run(...)` silently treats a failed call
    as success."""
    return isinstance(result, dict) and result.get("error") is True
