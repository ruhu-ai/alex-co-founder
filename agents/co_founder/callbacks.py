"""before_agent_callback: initialize session state keys (docs/03 §init).

Every key the instruction templates reference must exist before first render.
"""

from google.adk.agents.callback_context import CallbackContext

from . import state_schema as ss

_DEFAULTS = {
    ss.K_CURRENT_STEP: ss.ApplicationStep.IDLE,
    ss.K_ACTIVE_APPLICATION_ID: "",
    ss.K_ACTIVE_OPPORTUNITY_ID: "",
    ss.K_ACTIVE_PROGRAM_REQUIREMENTS: [],
    ss.K_CHECKLIST_STATUS: {},
    ss.K_PENDING_SIGNALS: [],
    ss.K_CURRENT_SECTION: "",
    ss.K_BROWSER_STATUS: {
        "active": False, "kind": None, "run_id": None, "url": None,
        "goal": None, "last_action": None,
    },
    ss.K_USER_PREFS: {},
}


async def initialize_session_state(callback_context: CallbackContext) -> None:
    """Ensures all state machine keys are initialized to prevent errors."""
    state = callback_context.state
    for key, default in _DEFAULTS.items():
        if key not in state:
            state[key] = default
    # The agent's clock: refreshed every turn so "what's today's date" and
    # deadline math are always grounded.
    from datetime import date

    state[ss.K_TODAY] = date.today().isoformat()
    # user:profile_id is resolved from the request's founder identity (Day 3:
    # profile store). System sessions (task runs, webhook wakes) act on behalf
    # of the founder — they must resolve to the founder's profile, never to a
    # "system" profile that would read empty.
    if ss.K_USER_PROFILE_ID not in state:
        import os

        uid = getattr(callback_context, "user_id", "") or ""
        if uid and uid != "system":
            state[ss.K_USER_PROFILE_ID] = uid
        else:
            state[ss.K_USER_PROFILE_ID] = os.environ.get("FOUNDER_ID", "founder")
    if ss.K_APP_WORKFLOW_ID not in state:
        from .workflow import get_workflow  # local import: avoid cycles at import time

        state[ss.K_APP_WORKFLOW_ID] = get_workflow().workflow_id

    # BrowserRun is durable truth; rewrite the advisory projection before the
    # instruction template renders on every invocation (docs/18).
    try:
        from services import browser_service

        session = getattr(callback_context, "session", None)
        session_key = {
            "app_name": getattr(session, "app_name", "") or "co_founder",
            "user_id": getattr(callback_context, "user_id", "")
            or getattr(session, "user_id", ""),
            "session_id": getattr(session, "id", "")
            or getattr(session, "session_id", ""),
        }
        if session_key["user_id"] and session_key["session_id"]:
            state[ss.K_BROWSER_STATUS] = await browser_service.reconcile_session(session_key)
    except Exception:
        # A Firestore outage must not prevent the founder from using chat; the
        # initialized inactive projection is safer than rendering stale state.
        state[ss.K_BROWSER_STATUS] = _DEFAULTS[ss.K_BROWSER_STATUS].copy()
