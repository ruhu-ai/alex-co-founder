"""Pipeline tools (docs/05). Errors as data. Thin wrappers over
services.pipeline_service + services.approval_service."""

import re

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import actor_id, add_pending_signal, run, workspace_id

# Application steps in which a committed application is mid-flight: starting a
# second application would clobber active_application_id/current_step/checklist
# and strand the first. Retrying the same opportunity is idempotent; replacing
# it is safe only from IDLE/TRIAGE/CLOSED.
_INFLIGHT_STEPS = frozenset({
    ss.ApplicationStep.INTERVIEWING, ss.ApplicationStep.DRAFTING,
    ss.ApplicationStep.AWAITING_REVIEW,
    ss.ApplicationStep.APPROVED, ss.ApplicationStep.FORM_FILLING,
    ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL, ss.ApplicationStep.SUBMITTED,
    ss.ApplicationStep.FOLLOW_UP,
})

# Requirement words too generic to count as coverage on their own.
_GAP_STOPWORDS = frozenset({
    "plan", "letter", "form", "document", "documents", "statement", "statements",
    "copy", "proof", "report", "details", "detail", "information", "your", "the",
})


def _requirement_addressed(requirement: str, answered: str) -> bool:
    """True when a recorded interview answer plausibly covers this required
    material. Heuristic: any distinctive word of the requirement appears in the
    answered corpus. Requirements with no distinctive word can't be checked, so
    they never block."""
    words = [w for w in re.split(r"[^a-z0-9]+", requirement.lower())
             if len(w) >= 4 and w not in _GAP_STOPWORDS]
    if not words:
        return True
    return any(word in answered for word in words)


def get_pipeline(tool_context: ToolContext) -> dict:
    """Board snapshot: opportunities grouped by state plus in-flight applications.

    Returns:
        dict with status, opportunities grouped by state (urgent first,
        summaries only, max 40), and in-flight applications.
    """
    from services import pipeline_service

    return run(pipeline_service.board(workspace_id(tool_context)))


def get_unscored_opportunities(tool_context: ToolContext) -> dict:
    """List opportunities still awaiting fit scoring (state=DISCOVERED).

    Returns:
        dict with status and opportunities (oldest first, max 10).
    """
    from services import firestore

    async def _go():
        return {"status": "success",
                "opportunities": await firestore.list_unscored_opportunities(
                    limit=10, founder_id=workspace_id(tool_context))}

    return run(_go())


def shortlist(opportunity_id: str, rationale: str, urgency_note: str,
              fit_score: int, tool_context: ToolContext) -> dict:
    """Mark an opportunity SHORTLISTED (guard: current state must be DISCOVERED).

    Args:
        opportunity_id: The opportunity to shortlist.
        rationale: Founder-facing fit rationale, max 280 chars.
        urgency_note: Deadline-urgency note, e.g. "closes in 9 days, needs 2 essays".
        fit_score: The evidence-backed 70-100 fit score that justified shortlisting.

    Returns:
        dict with status and the updated state.
    """
    from services import pipeline_service

    return run(pipeline_service.shortlist(
        opportunity_id, rationale, urgency_note, fit_score=fit_score,
        founder_id=workspace_id(tool_context)))


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

    return run(pipeline_service.archive(
        opportunity_id, reason, fit_score,
        founder_id=workspace_id(tool_context)))


def choose_opportunity(opportunity_id: str, tool_context: ToolContext) -> dict:
    """Start an application for a SHORTLISTED opportunity.

    Args:
        opportunity_id: The SHORTLISTED opportunity the founder chose.

    Returns:
        dict with status and the new application_id. Creates the application
        doc + checklist, sets current_step=INTERVIEWING and the active ids.
    """
    from services import pipeline_service

    state = tool_context.state
    # The durable workspace scope comes from the active invocation identity.
    # Session state normally mirrors it, but a fresh/repaired projection may
    # not contain the profile key yet; never turn that projection miss into an
    # unscoped durable read.
    founder_id = str(
        state.get(ss.K_USER_PROFILE_ID)
        or getattr(tool_context, "user_id", "")
        or getattr(getattr(tool_context, "session", None), "user_id", "")
        or "")
    # Escalate when blocked (docs/README): refuse to start a second application
    # while one is mid-flight — clobbering the active ids would strand it.
    active_id = state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    current = state.get(ss.K_CURRENT_STEP, "")
    active_opportunity = state.get(ss.K_ACTIVE_OPPORTUNITY_ID, "")
    if (active_id and current in _INFLIGHT_STEPS
            and active_opportunity != opportunity_id):
        return {"status": "error", "error": True,
                "message": (f"Application {active_id} is already in progress at "
                            f"{current}. Finish or close it before starting a new "
                            "one — I won't drop work in flight.")}

    founder_id = workspace_id(tool_context)
    result = run(pipeline_service.choose_opportunity(founder_id, opportunity_id))
    if result.get("status") == "success":
        # A duplicate selection from another/recreated session rehydrates the
        # durable application's real position; it must never rewind to the
        # beginning merely because this session had stale local state.
        state[ss.K_CURRENT_STEP] = result.get(
            "current_step", ss.ApplicationStep.INTERVIEWING)
        state[ss.K_ACTIVE_APPLICATION_ID] = result["application_id"]
        state[ss.K_ACTIVE_OPPORTUNITY_ID] = opportunity_id
        state[ss.K_ACTIVE_PROGRAM_REQUIREMENTS] = result["active_program_requirements"]
        state[ss.K_OPPORTUNITY_READINESS] = result.get("readiness", {})
        state[ss.K_CHECKLIST_STATUS] = result["checklist_status"]
        _register_selection(tool_context, founder_id, opportunity_id, result)
    return result


def _register_selection(tool_context: ToolContext, founder_id: str,
                        opportunity_id: str, result: dict) -> None:
    """Record the founder's selection occurrence (docs/23 §5.2 triggers).

    `selected` on the opportunity and `created`/`continued` on the application,
    both keyed so that one application is at most one occurrence per
    conversation: a re-selection in the same session replays, while continuing
    the same application in a NEW session is its own durable occurrence.
    """
    session_id = getattr(getattr(tool_context, "session", None), "id", "") or ""
    if not session_id:
        return
    from services import session_resources as sr

    application_id = result.get("application_id", "")
    relationship = (sr.Relationship.CONTINUED if result.get("already_exists")
                    else sr.Relationship.CREATED)
    opportunity_resource = sr.resource_id_for(
        founder_id, sr.ResourceType.OPPORTUNITY, "opportunities",
        opportunity_id)

    async def _register():
        from services import firestore

        opp = await firestore.get_opportunity(opportunity_id, founder_id) or {}
        await sr.register_session_resource(
            founder_id=founder_id, session_id=session_id,
            resource_type=sr.ResourceType.OPPORTUNITY,
            canonical_id=opportunity_id,
            relationship=sr.Relationship.SELECTED,
            occurrence_key=f"select:{application_id}",
            producer_kind="tool", producer_id="choose_opportunity",
            producer_output_key="selection",
            title=str(opp.get("name") or "")[:200],
            summary=str(opp.get("description") or "")[:500],
            status=str(opp.get("state") or ""), session_verified=True)
        await sr.register_session_resource(
            founder_id=founder_id, session_id=session_id,
            resource_type=sr.ResourceType.APPLICATION,
            canonical_id=application_id, relationship=relationship,
            occurrence_key=f"{relationship}:{application_id}",
            producer_kind="tool", producer_id="choose_opportunity",
            producer_output_key="application",
            title=str(opp.get("name") or "Application")[:200],
            summary="Application", status=result.get("current_step", ""),
            parent_resource_id=opportunity_resource, session_verified=True)

    run(_register())


def get_opportunity(opportunity_id: str, tool_context: ToolContext) -> dict:
    """Fetch the full opportunity record (drafter context).

    Args:
        opportunity_id: The opportunity to fetch.

    Returns:
        dict with status and the full record.
    """
    from services import firestore

    async def _go():
        opp = await firestore.get_opportunity(
            opportunity_id, workspace_id(tool_context))
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
    founder_id = str(
        state.get(ss.K_USER_PROFILE_ID)
        or getattr(tool_context, "user_id", "")
        or getattr(getattr(tool_context, "session", None), "user_id", "")
        or "")
    state_status = dict(state.get(ss.K_CHECKLIST_STATUS) or {})
    if state_status:
        done = sum(1 for s in state_status.values() if s == "DONE")
        return {"status": "success", "checklist_status": state_status,
                "progress": f"{done} of {len(state_status)} done",
                "current_section": state.get(ss.K_CURRENT_SECTION, ""),
                "source": "session_state"}

    async def _go():
        app_id = state.get(ss.K_ACTIVE_APPLICATION_ID, "")
        app = (await firestore.get_application(app_id, founder_id)
               if app_id else None)
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
        dict with status and the new current_step. Refuses (error-as-data) while
        any required program material has no recorded interview answer.
    """
    from services import firestore, pipeline_service

    state = tool_context.state
    app_id = state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    founder_id = str(
        state.get(ss.K_USER_PROFILE_ID)
        or getattr(tool_context, "user_id", "")
        or getattr(getattr(tool_context, "session", None), "user_id", "")
        or "")
    requirements = [str(r) for r in (state.get(ss.K_ACTIVE_PROGRAM_REQUIREMENTS) or [])]

    async def _go():
        app = (await firestore.get_application(app_id, founder_id)
               if app_id else None)
        if not app:
            return {"status": "error", "error": True, "message": "no active application"}
        # The interviewer's ground truth for what has been answered.
        answered = " ".join(
            f"{qa.get('question_key', '')} {qa.get('question', '')} {qa.get('answer', '')}"
            for qa in app.get("interview_qa", [])
        ).lower()
        unresolved = [req for req in requirements
                      if not _requirement_addressed(req, answered)]
        if unresolved:
            return {
                "status": "error", "error": True, "unresolved_gaps": unresolved,
                "message": ("Interview incomplete — no recorded answer yet addresses "
                            f"required item(s): {', '.join(unresolved[:6])}. Ask about "
                            "these and record_answer before completing the interview."),
            }
        return await pipeline_service.advance_application(
            app_id, ss.ApplicationStep.DRAFTING, actor="agent:interviewer",
            founder_id=founder_id)

    result = run(_go())
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
    session = getattr(tool_context, "session", None)
    session_id = (getattr(session, "id", "")
                  or getattr(session, "session_id", ""))
    founder_id = workspace_id(tool_context)
    result = run(approval_service.request_approval(
        app_id, gate, founder_id=founder_id, session_id=session_id,
        requested_by_actor_id=actor_id(tool_context)))
    if result.get("status") == "success":
        add_pending_signal(tool_context, "founder_approval")
    return result
