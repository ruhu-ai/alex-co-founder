"""Pipeline tools (docs/05). Errors as data. Thin wrappers over
services.pipeline_service + services.approval_service."""

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run


def get_pipeline(tool_context: ToolContext) -> dict:
    """Board snapshot: opportunities grouped by state plus in-flight applications.

    Returns:
        dict with status, opportunities grouped by state (urgent first,
        summaries only, max 40), and in-flight applications.
    """
    from services import pipeline_service

    return run(pipeline_service.board(tool_context.state.get(ss.K_USER_PROFILE_ID, "founder")))


def get_unscored_opportunities(tool_context: ToolContext) -> dict:
    """List opportunities still awaiting fit scoring (state=DISCOVERED).

    Returns:
        dict with status and opportunities (oldest first, max 10).
    """
    from services import firestore

    async def _go():
        return {"status": "success",
                "opportunities": await firestore.list_unscored_opportunities(limit=10)}

    return run(_go())


def shortlist(opportunity_id: str, rationale: str, urgency_note: str, tool_context: ToolContext) -> dict:
    """Mark an opportunity SHORTLISTED (guard: current state must be DISCOVERED).

    Args:
        opportunity_id: The opportunity to shortlist.
        rationale: Founder-facing fit rationale, max 280 chars.
        urgency_note: Deadline-urgency note, e.g. "closes in 9 days, needs 2 essays".

    Returns:
        dict with status and the updated state.
    """
    from services import pipeline_service

    return run(pipeline_service.shortlist(opportunity_id, rationale, urgency_note,
                                          fit_score=tool_context.state.get("temp:fit_score", 75)))


def archive_with_reason(opportunity_id: str, reason: str, fit_score: int, tool_context: ToolContext) -> dict:
    """Mark an opportunity ARCHIVED. The reason is mandatory and must be specific.

    Args:
        opportunity_id: The opportunity to archive.
        reason: Specific non-fit reason, e.g. "requires $50k+ ARR; profile is pre-revenue".
        fit_score: The 0-100 fit score that justified archiving.

    Returns:
        dict with status and the updated state.
    """
    from services import pipeline_service

    return run(pipeline_service.archive(opportunity_id, reason, fit_score))


def choose_opportunity(opportunity_id: str, tool_context: ToolContext) -> dict:
    """Start an application for a SHORTLISTED opportunity.

    Args:
        opportunity_id: The SHORTLISTED opportunity the founder chose.

    Returns:
        dict with status and the new application_id. Creates the application
        doc + checklist, sets current_step=INTERVIEWING and the active ids.
    """
    from services import pipeline_service

    founder_id = tool_context.state.get(ss.K_USER_PROFILE_ID, "founder")
    result = run(pipeline_service.choose_opportunity(founder_id, opportunity_id))
    if result.get("status") == "success":
        state = tool_context.state
        state[ss.K_CURRENT_STEP] = ss.ApplicationStep.INTERVIEWING
        state[ss.K_ACTIVE_APPLICATION_ID] = result["application_id"]
        state[ss.K_ACTIVE_OPPORTUNITY_ID] = opportunity_id
        state[ss.K_ACTIVE_PROGRAM_REQUIREMENTS] = result["active_program_requirements"]
        state[ss.K_CHECKLIST_STATUS] = result["checklist_status"]
    return result


def get_opportunity(opportunity_id: str, tool_context: ToolContext) -> dict:
    """Fetch the full opportunity record (drafter context).

    Args:
        opportunity_id: The opportunity to fetch.

    Returns:
        dict with status and the full record.
    """
    from services import firestore

    async def _go():
        opp = await firestore.get_opportunity(opportunity_id)
        if not opp:
            return {"status": "error", "error": True, "message": f"opportunity {opportunity_id} not found"}
        return {"status": "success", "opportunity": opp}

    return run(_go())


def get_checklist(tool_context: ToolContext) -> dict:
    """Current application's checklist with progress count.

    Returns:
        dict with status, checklist items, and progress ("3 of 6 done").
        Reads session state first (the agent's ground truth — docs/README
        principle 1); Firestore is the fallback for out-of-session contexts.
    """
    from services import firestore

    state = tool_context.state
    state_status = dict(state.get(ss.K_CHECKLIST_STATUS) or {})
    if state_status:
        done = sum(1 for s in state_status.values() if s == "DONE")
        return {"status": "success", "checklist_status": state_status,
                "progress": f"{done} of {len(state_status)} done",
                "current_section": state.get(ss.K_CURRENT_SECTION, ""),
                "source": "session_state"}

    async def _go():
        app_id = state.get(ss.K_ACTIVE_APPLICATION_ID, "")
        app = await firestore.get_application(app_id) if app_id else None
        if not app:
            return {"status": "error", "error": True, "message": "no active application"}
        items = app.get("checklist", [])
        done = sum(1 for i in items if i["status"] == "DONE")
        return {"status": "success", "checklist": items,
                "progress": f"{done} of {len(items)} done", "source": "firestore"}

    return run(_go())


def complete_interview(tool_context: ToolContext) -> dict:
    """Close the interview: guard = no unresolved required gaps. Flushes
    interview answers into profile facts and transitions to DRAFTING.

    Returns:
        dict with status and the new current_step.
    """
    from services import pipeline_service

    state = tool_context.state
    app_id = state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    result = run(pipeline_service.advance_application(app_id, ss.ApplicationStep.DRAFTING,
                                                      actor="agent:interviewer"))
    if result.get("status") == "success":
        state[ss.K_CURRENT_STEP] = ss.ApplicationStep.DRAFTING
    return result


def request_approval(gate: str, tool_context: ToolContext) -> dict:
    """Create a PENDING approval for a gated action (founder resolves it in the UI).

    Args:
        gate: The gate name; v1 supports "submit_application" only.

    Returns:
        dict with status and approval_id. NEVER returns a token — tokens are
        minted server-side only when the founder grants (docs/12).
    """
    from services import approval_service

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    result = run(approval_service.request_approval(app_id, gate))
    if result.get("status") == "success":
        tool_context.state[ss.K_PENDING_SIGNALS] = ["founder_approval"]
    return result
