"""Orchestrator-only interactive browsing tools (docs/05, 18).

These wrappers only adapt ToolContext to the async service. Policy, budgets,
auditing, idempotency, and browser lifecycle all remain in browser_service.
"""

# ruff: noqa: BLE001 -- tools must convert every service failure to error data.

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run


def _session_key(tool_context: ToolContext) -> dict[str, str]:
    session = getattr(tool_context, "session", None)
    return {
        "app_name": getattr(session, "app_name", "") or "co_founder",
        "user_id": getattr(tool_context, "user_id", "")
        or getattr(session, "user_id", ""),
        "session_id": getattr(session, "id", "") or getattr(session, "session_id", ""),
    }


def _service_error(exc: Exception) -> dict:
    code = "timeout" if "Timeout" in type(exc).__name__ else "browser_unavailable"
    return {
        "status": "error",
        "error": True,
        "code": code,
        "message": f"browser service failed: {str(exc)[:240]}",
    }


def open_page(url: str, purpose: str, tool_context: ToolContext) -> dict:
    """Open a public page in an isolated, read-only browser run.

    Args:
        url: The public http/https page to open.
        purpose: The immutable research goal for this run, in at most 200 chars.
        tool_context: ADK context supplying the authenticated user and session.

    Returns:
        A dict containing the run id, current URL, title, short excerpt, links,
        and screenshot artifact, or the documented browser error schema.
    """
    from services import browser_service

    try:
        result = run(browser_service.open_run(_session_key(tool_context), url, purpose))
        if result.get("status") == "success":
            tool_context.state[ss.K_BROWSER_STATUS] = run(
                browser_service.browser_status_projection(_session_key(tool_context))
            )
        return result
    except Exception as exc:
        return _service_error(exc)


def read_page(question: str, tool_context: ToolContext) -> dict:
    """Answer a question using only the current page's extracted text.

    Args:
        question: The question to answer from the current page.
        tool_context: ADK context supplying the authenticated user and session.

    Returns:
        A dict with an isolated-reader answer and an artifact character range,
        or the documented browser error schema. This function never acts.
    """
    from services import browser_service

    try:
        active = run(
            browser_service.current_run_for_session(_session_key(tool_context))
        )
        if not active:
            return {
                "status": "error",
                "error": True,
                "code": "no_active_run",
                "message": "there is no active browser run",
            }
        return run(browser_service.read_current(active["run_id"], question))
    except Exception as exc:
        return _service_error(exc)


def browser_action(tool_context: ToolContext) -> dict:
    """Perform one bounded, read-only navigation action toward the run's goal.

    Args:
        tool_context: ADK context supplying session identity and invocation id.

    Returns:
        A dict describing the single action, resulting URL, excerpt, and
        screenshot, or the documented browser error schema.
    """
    from services import browser_service

    try:
        key = _session_key(tool_context)
        active = run(browser_service.current_run_for_session(key))
        if not active:
            return {
                "status": "error",
                "error": True,
                "code": "no_active_run",
                "message": "there is no active browser run",
            }
        invocation_id = getattr(tool_context, "function_call_id", "") or getattr(
            tool_context, "invocation_id", ""
        )
        if not invocation_id:
            return {
                "status": "error",
                "error": True,
                "code": "browser_unavailable",
                "message": "the server could not derive an action identity",
            }
        result = run(browser_service.propose_and_act(active["run_id"], invocation_id))
        tool_context.state[ss.K_BROWSER_STATUS] = run(
            browser_service.browser_status_projection(key)
        )
        return result
    except Exception as exc:
        return _service_error(exc)


def close_browser(tool_context: ToolContext) -> dict:
    """Close the session's active browse run idempotently.

    Args:
        tool_context: ADK context supplying the authenticated user and session.

    Returns:
        A success dict; when already closed, ``already_closed`` is true.
    """
    from services import browser_service

    try:
        key = _session_key(tool_context)
        active = run(browser_service.current_run_for_session(key))
        result = (
            {"status": "success", "already_closed": True}
            if not active
            else run(
                browser_service.close_run(
                    active["run_id"], "agent_close", "agent:co_founder"
                )
            )
        )
        tool_context.state[ss.K_BROWSER_STATUS] = run(
            browser_service.browser_status_projection(key)
        )
        return result
    except Exception as exc:
        return _service_error(exc)
