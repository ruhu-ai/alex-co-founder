"""Feedback service (docs/05, 06) — verbatim capture + synchronous distillation
+ section state + the AWAITING_REVIEW → APPROVED transition that mints the
submit idempotency key.
"""

from __future__ import annotations

from typing import Any

from agents.co_founder.state_schema import ApplicationStep as Step
from agents.co_founder.state_schema import SectionStatus
from services import distill_service, firestore, pipeline_service


async def record_feedback(founder_id: str, application_id: str, section_id: str,
                          feedback_type: str, reason: str = "",
                          edited_text: str = "", session_service=None) -> dict[str, Any]:
    if feedback_type not in ("approve", "edit", "reject"):
        return {"status": "error", "error": True, "message": "feedback_type must be approve|edit|reject"}
    if feedback_type in ("edit", "reject") and not reason.strip():
        return {"status": "error", "error": True,
                "message": "a reason is required for edit/reject — it is the distiller's evidence"}

    app = await firestore.get_application(application_id)
    if not app:
        return {"status": "error", "error": True, "message": f"application {application_id} not found"}

    sections = app.get("draft_sections", [])
    section = next((s for s in sections if s.get("section_id") == section_id), None)
    original = section.get("content", "") if section else ""

    feedback_id = await firestore.create_feedback(
        founder_id=founder_id, application_id=application_id, section_id=section_id,
        feedback_type=feedback_type, original=original, reason=reason, edited_text=edited_text,
    )
    await firestore.audit(f"founder:{founder_id}", "feedback",
                          f"applications/{application_id}/sections/{section_id}", "success",
                          f"{feedback_type}: {reason[:160]}")

    # Section state
    if section is not None:
        section["status"] = (SectionStatus.APPROVED if feedback_type == "approve"
                             else SectionStatus.CHANGES_REQUESTED)
        if feedback_type == "approve":
            # An approve-with-edit ships the EDITED text: update the section's own
            # content too, not just the canonical answer, or the submitted form
            # would carry the pre-edit draft.
            if edited_text:
                section["content"] = edited_text
            # Approved text becomes the canonical answer for its question key (docs/06)
            await firestore.apply_profile_update(
                founder_id, "canonical_answer_update",
                {"question_key": section.get("section_key", section_id),
                 "text": edited_text or original, "tags": [section.get("section_key", section_id)]},
                evidence="founder approved this section",
            )
        await firestore.update_application(application_id, draft_sections=sections)

    # Synchronous distillation — the rule exists before the next draft
    distill_result = await distill_service.run_distillation(feedback_id, session_service)

    # A requested change returns the application to DRAFTING. All sections
    # approved moves AWAITING_REVIEW → APPROVED (and mints the submit key).
    transitioned = None
    if section is not None and feedback_type in ("edit", "reject"):
        if app["state"] == Step.AWAITING_REVIEW:
            result = await pipeline_service.advance_application(
                application_id, Step.DRAFTING, actor="agent:orchestrator")
            transitioned = result.get("current_step")
    elif section is not None and all(s.get("status") == SectionStatus.APPROVED for s in sections):
        if app["state"] == Step.AWAITING_REVIEW:
            result = await pipeline_service.advance_application(
                application_id, Step.APPROVED, actor="agent:orchestrator")
            transitioned = result.get("current_step")

    return {
        "status": "success",
        "feedback_id": feedback_id,
        "distillation": distill_result,
        "application_step": transitioned or app["state"],
    }
