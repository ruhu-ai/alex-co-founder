"""Drafting tools (docs/05). Errors as data. G1 enforced in code."""

import logging
import os
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
    # Session comes from the invocation context, never from model args.
    session_id = getattr(getattr(tool_context, "session", None), "id", "") or ""

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
        # Advisory evidence check (docs/20). It runs before the transition so
        # the founder sees the report beside the draft, but NO model outcome
        # blocks: unavailable and invalid are honest statuses, not gates. Only
        # failing to persist the report stops the transition, because a report
        # the founder cannot see must not be silently dropped.
        gate = await _attach_evidence_check(app, app_id, session_id)
        if not gate.get("ok"):
            return gate          # IN_PROGRESS / persistence failure: stay in DRAFTING

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


async def _attach_evidence_check(app: dict, app_id: str,
                                 session_id: str = "") -> dict:
    """Run the Evidence Checker before founder review.

    Returns {"ok": True} to proceed, or an error dict that must block the
    transition. Two things block, and neither is a model verdict: another
    check already holds the lease for this exact draft (retry, do not advance
    with no report), and failing to persist a report the founder was meant to
    see. Model outages and invalid output become honest terminal statuses;
    unreadable source evidence and persistence failures return error data and
    keep the application in DRAFTING.
    """
    from services import firestore, gemma_evidence, profile_service

    founder = app.get("founder_id") or os.environ.get("FOUNDER_ID", "founder")
    try:
        profile = await profile_service.get_profile(founder) or {}
        opportunity = (await firestore.get_opportunity(app.get("opportunity_id") or "")
                       if app.get("opportunity_id") else None)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "evidence unreadable: %s", type(exc).__name__)
        return {
            "status": "error", "error": True, "retriable": True,
            "error_code": "evidence_read_failed",
            "message": "the saved evidence could not be read; try completing drafting again",
        }

    try:
        report = await gemma_evidence.run_evidence_check(
            founder, {**app, "id": app_id}, profile, opportunity)
    except Exception as exc:  # defensive boundary: tools return errors as data
        logging.getLogger(__name__).warning(
            "evidence check errored: %s", type(exc).__name__)
        return {
            "status": "error", "error": True, "retriable": True,
            "error_code": "evidence_check_failed",
            "message": "the evidence report could not be saved; try again",
        }

    if report.get("status") in ("IN_PROGRESS", "ERROR"):
        code = report.get("error_code") or "evidence_check_failed"
        return {
            "status": "error", "error": True,
            "retriable": bool(report.get("retriable", True)),
            "error_code": code,
            "message": ("an evidence check for this exact draft is already running; "
                        "try again in a moment" if report.get("status") == "IN_PROGRESS"
                        else "the evidence report could not be completed; try again"),
        }
    # Supporting resource under the application (docs/23 §6.1). Occurrence is
    # keyed by the report's deterministic input hash, so re-running drafting
    # over the same draft replays instead of duplicating.
    report_id = report.get("report_id") or ""
    if session_id and report_id:
        try:
            from services import session_resources as sr

            await sr.register_session_resource(
                founder_id=founder, session_id=session_id,
                resource_type=sr.ResourceType.EVIDENCE_REPORT,
                canonical_id=report_id,
                relationship=sr.Relationship.PRODUCED,
                occurrence_key=f"evidence_check:{report_id}",
                producer_kind="service", producer_id="gemma_evidence",
                producer_output_key="report",
                title="Evidence check",
                summary=f"{len(report.get('findings') or [])} findings",
                status=str(report.get("status") or ""),
                visibility=sr.Visibility.SUPPORTING,
                parent_resource_id=sr.resource_id_for(
                    founder, sr.ResourceType.APPLICATION, "applications",
                    app_id),
                session_verified=True)
        except Exception:  # noqa: BLE001 — provenance never blocks review
            logging.getLogger(__name__).warning(
                "evidence resource registration failed")
    return {"ok": True}


def get_section_feedback(section_id: str, tool_context: ToolContext) -> dict:
    """Prior feedback on this section key across ALL applications — how the
    drafter sees "last time you rejected...".

    Args:
        section_id: The stable section key (e.g. "describe_traction") or a
            per-application section id ("sec-1a2b3c4d") — either is resolved to
            the stable key before the lookup.

    Returns:
        dict with status and the feedback history, newest first.
    """
    from services import firestore

    async def _go():
        # History is keyed by the QUESTION ("describe_traction"), never the
        # per-application UUID — querying by section_id alone always returned
        # empty across applications. Map a section id back to its key first.
        section_key = section_id
        app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
        if section_id.startswith("sec-") and app_id:
            app = await firestore.get_application(app_id) or {}
            match = next((s for s in app.get("draft_sections", [])
                          if s.get("section_id") == section_id), None)
            if match and match.get("section_key"):
                section_key = match["section_key"]

        client = firestore.get_client()
        rows = []
        try:
            query = client.collection("feedback").where("section_key", "==", section_key)
            async for doc in query.stream():
                rows.append(doc.to_dict())
            rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)  # no composite index
            rows = rows[:10]
        except Exception:
            # store offline — degrade, never break drafting
            return {"status": "success", "feedback": [],
                    "note": "feedback history unavailable"}
        return {"status": "success", "feedback": rows, "section_key": section_key}

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
