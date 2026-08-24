"""Drafting tools (docs/05). Errors as data. G1 enforced in code."""

import uuid

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run


def save_draft_section(section_key: str, content: str, word_count: int, notes: str,
                       tool_context: ToolContext) -> dict:
    """Save one drafted section (guard G1: only while current_step=DRAFTING).

    Args:
        section_key: The section being drafted, e.g. "describe_traction".
        content: The draft text.
        word_count: The draft's word count (checked against program limits).
        notes: Voice-rule citations shaping this draft, e.g. "avoided
            'revolutionary' per vr_3" — rendered in the UI as the adaptation proof.

    Returns:
        dict with status and section_id. Upserts the section (version+1),
        updates checklist_status, sets current_section.
    """
    state = tool_context.state
    # DRAFTING is the only step the state machine drafts in — a reject/edit in
    # AWAITING_REVIEW returns the application to DRAFTING before re-drafting.
    # Allowing any non-INTERVIEWING step let drafts be written in SUBMITTED/IDLE
    # against a stale active_application_id.
    if state.get(ss.K_CURRENT_STEP) != ss.ApplicationStep.DRAFTING:
        return {"status": "error", "error": True,
                "message": ("Gate G1: sections can only be drafted in DRAFTING "
                            f"(current: {state.get(ss.K_CURRENT_STEP)}).")}

    from services import firestore

    app_id = state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    section_id = f"sec-{uuid.uuid4().hex[:8]}"

    async def _go():
        app = await firestore.get_application(app_id)
        if not app:
            return {"status": "error", "error": True, "message": "no active application"}
        sections = app.get("draft_sections", [])
        existing = next((s for s in sections if s.get("section_key") == section_key), None)
        if existing:
            version = existing.get("version", 0) + 1
            existing.update({"content": content, "word_count": word_count, "notes": notes,
                             "status": ss.SectionStatus.DRAFTED, "version": version})
            sid = existing["section_id"]
        else:
            version = 1
            sections.append({"section_id": section_id, "section_key": section_key,
                             "content": content, "word_count": word_count, "notes": notes,
                             "status": ss.SectionStatus.DRAFTED, "version": version})
            sid = section_id
        await firestore.update_application(app_id, draft_sections=sections)
        await firestore.audit("agent:drafter", "save_draft_section",
                              f"applications/{app_id}/sections/{sid}", "success",
                              f"{section_key} v{version}")
        return {"status": "success", "section_id": sid, "word_count": word_count}

    result = run(_go())
    if result.get("status") == "success":
        state[ss.K_CURRENT_SECTION] = section_key
        checklist = dict(state.get(ss.K_CHECKLIST_STATUS, {}))
        checklist["draft_sections"] = "IN_PROGRESS"
        state[ss.K_CHECKLIST_STATUS] = checklist
    return result


def complete_drafting(tool_context: ToolContext) -> dict:
    """Move a fully drafted application into founder review.

    Returns:
        dict with status and current_step. Refuses unless the application is in
        DRAFTING and has at least one section, with every section saved in a
        reviewable state.
    """
    from services import firestore, pipeline_service

    state = tool_context.state
    app_id = state.get(ss.K_ACTIVE_APPLICATION_ID, "")

    async def _go():
        app = await firestore.get_application(app_id) if app_id else None
        if not app:
            return {"status": "error", "error": True, "message": "no active application"}
        if app.get("state") != ss.ApplicationStep.DRAFTING:
            return {
                "status": "error",
                "error": True,
                "message": f"cannot complete drafting from {app.get('state')}",
            }
        sections = app.get("draft_sections", [])
        reviewable = {
            ss.SectionStatus.DRAFTED,
            ss.SectionStatus.IN_REVIEW,
            ss.SectionStatus.CHANGES_REQUESTED,
            ss.SectionStatus.APPROVED,
        }
        if not sections or any(section.get("status") not in reviewable for section in sections):
            return {
                "status": "error",
                "error": True,
                "message": "drafting is incomplete: every required section must be saved first",
            }
        return await pipeline_service.advance_application(
            app_id, ss.ApplicationStep.AWAITING_REVIEW, actor="agent:drafter"
        )

    result = run(_go())
    if result.get("status") == "success":
        state[ss.K_CURRENT_STEP] = ss.ApplicationStep.AWAITING_REVIEW
        state[ss.K_PENDING_SIGNALS] = ["founder_feedback"]
        checklist = dict(state.get(ss.K_CHECKLIST_STATUS, {}))
        checklist["draft_sections"] = ss.ChecklistStatus.DONE
        checklist["review"] = ss.ChecklistStatus.IN_PROGRESS
        state[ss.K_CHECKLIST_STATUS] = checklist
    return result


def get_section_feedback(section_id: str, tool_context: ToolContext) -> dict:
    """Prior feedback on this section key across ALL applications — how the
    drafter sees "last time you rejected...".

    Args:
        section_id: The section (or section key) to look up feedback for.

    Returns:
        dict with status and the feedback history, newest first.
    """
    from services import firestore

    async def _go():
        client = firestore.get_client()
        rows = []
        try:
            query = client.collection("feedback").where("section_id", "==", section_id)
            async for doc in query.stream():
                rows.append(doc.to_dict())
            rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)  # no composite index
            rows = rows[:10]
        except Exception:
            # store offline — degrade, never break drafting
            return {"status": "success", "feedback": [],
                    "note": "feedback history unavailable"}
        return {"status": "success", "feedback": rows}

    return run(_go())



def get_form_questions(tool_context: ToolContext) -> dict:
    """Every question the program's application form actually asks.

    Recorded from the live form by the recon/open-portal pass, so this is the
    form's own wording — not a guess at what it wants. A document or draft is
    only complete when it answers all of these.

    Returns:
        dict with status, count, and questions: each has name, label, type and
        required. Empty until the form-filler has opened the portal.
    """
    from services import firestore

    async def _go():
        app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
        if not app_id:
            return {"status": "error", "error": True,
                    "message": "no active application — choose one first"}
        app = await firestore.get_application(app_id) or {}
        questions = app.get("form_questions", []) or []
        if not questions:
            return {"status": "success", "count": 0, "questions": [],
                    "message": "the form has not been opened yet — the "
                               "form-filler records the questions when it does"}
        return {"status": "success", "count": len(questions), "questions": questions}

    return run(_go())
