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


def add_pending_signal(tool_context, signal: str) -> None:
    """Append a pending signal without clobbering others already waiting.

    The pending-signals list is a set of wake conditions the resume path is
    watching (e.g. a founder_approval and a portal_verification can be pending
    at once). Overwriting it wholesale drops signals; every writer must be
    additive to match the removal paths, which already filter in place.
    """
    from .. import state_schema as ss

    signals = list(tool_context.state.get(ss.K_PENDING_SIGNALS, []))
    if signal not in signals:
        signals.append(signal)
    tool_context.state[ss.K_PENDING_SIGNALS] = signals


def workspace_id(tool_context) -> str:
    """Resolve the invocation workspace without trusting model arguments.

    ADK state normally carries the reconciled profile id. During first-turn
    initialization or projection repair it may not be populated yet, while
    the invocation/session identity is already server-derived. Durable reads
    remain scoped by falling back to those identities, never to an empty query.
    """
    from .. import state_schema as ss

    session = getattr(tool_context, "session", None)
    return str(
        tool_context.state.get(ss.K_USER_PROFILE_ID)
        or getattr(tool_context, "user_id", "")
        or getattr(session, "user_id", "")
        or "")


def actor_id(tool_context) -> str:
    """Return the server-projected interactive actor identity.

    There is deliberately no founder/workspace fallback: the initiating human
    and workspace are different security dimensions even when one person owns
    the only workspace. Missing actor authority must fail closed in production.
    """
    from .. import state_schema as ss

    return str(tool_context.state.get(ss.K_ACTOR_ID) or "")
