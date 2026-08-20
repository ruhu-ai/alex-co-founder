"""Follow-up tools (docs/05). Errors as data."""

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run


def schedule_followup(kind: str, due_at: str, note: str, tool_context: ToolContext) -> dict:
    """Add a follow-up obligation to the active application and move it to FOLLOW_UP.

    Args:
        kind: Follow-up kind, e.g. "confirmation_email", "interview_invite", "result".
        due_at: ISO-8601 UTC timestamp when this should be checked.
        note: Founder-facing note about what is expected.

    Returns:
        dict with status.
    """
    from services import firestore, pipeline_service

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")

    async def _go():
        app = await firestore.get_application(app_id)
        if not app:
            return {"status": "error", "error": True, "message": "no active application"}
        followups = app.get("followups", [])
        followups.append({"kind": kind, "due_at": due_at, "status": "PENDING", "note": note})
        await firestore.update_application(app_id, followups=followups)
        return {"status": "success", "followups": len(followups)}

    result = run(_go())
    if result.get("status") == "success":
        transition = run(pipeline_service.advance_application(
            app_id, ss.ApplicationStep.FOLLOW_UP, actor="agent:orchestrator"))
        if transition.get("status") == "success":
            tool_context.state[ss.K_CURRENT_STEP] = ss.ApplicationStep.FOLLOW_UP
    return result


def record_status(status_note: str, tool_context: ToolContext) -> dict:
    """Record a founder-facing status line on the active application.

    Args:
        status_note: The status to record, e.g. "confirmation received MP-1042".

    Returns:
        dict with status. Closes the application when the note records a result.
    """
    from services import firestore

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")

    async def _go():
        app = await firestore.get_application(app_id)
        if not app:
            return {"status": "error", "error": True, "message": "no active application"}
        followups = app.get("followups", [])
        followups.append({"kind": "status", "due_at": None, "status": "DONE", "note": status_note})
        await firestore.update_application(app_id, followups=followups)
        return {"status": "success"}

    return run(_go())
