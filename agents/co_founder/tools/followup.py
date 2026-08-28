"""Follow-up tools (docs/05). Errors as data."""

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run, workspace_id


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
    founder_id = workspace_id(tool_context)

    async def _go():
        # Transactional append: the previous read-modify-write of the whole
        # array lost a follow-up whenever this raced an inbound-mail scan.
        return await firestore.append_application_followup(
            app_id, {"kind": kind, "due_at": due_at,
                     "status": "PENDING", "note": note})

    result = run(_go())
    if result.get("status") == "success":
        transition = run(pipeline_service.advance_application(
            app_id, ss.ApplicationStep.FOLLOW_UP, actor="agent:orchestrator",
            founder_id=founder_id))
        if transition.get("status") == "success":
            tool_context.state[ss.K_CURRENT_STEP] = ss.ApplicationStep.FOLLOW_UP
    return result


def record_status(status_note: str, tool_context: ToolContext) -> dict:
    """Record a founder-facing status line on the active application.

    Args:
        status_note: The status to record, e.g. "confirmation received MP-1042".

    Returns:
        dict with status. Append-only — the application is closed by the portal
        result_posted webhook (FOLLOW_UP -> CLOSED), not by this note.
    """
    from services import firestore

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")

    async def _go():
        result = await firestore.append_application_followup(
            app_id, {"kind": "status", "due_at": None,
                     "status": "DONE", "note": status_note})
        return ({"status": "success"} if result.get("status") == "success"
                else result)

    return run(_go())
