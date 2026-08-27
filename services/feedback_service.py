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

    section_status = (SectionStatus.APPROVED if feedback_type == "approve"
                      else SectionStatus.CHANGES_REQUESTED)
    update = await firestore.update_draft_section(
        application_id, founder_id, section_id, section_status,
        edited_text if feedback_type == "approve" else "")
    if update.get("status") != "success":
        return update
    section = update["section"]
    sections = update["sections"]
    original = update["original"]
    app_state = update["state"]

    feedback_id = await firestore.create_feedback(
        founder_id=founder_id, application_id=application_id, section_id=section_id,
        feedback_type=feedback_type, original=original, reason=reason, edited_text=edited_text,
        section_key=section.get("section_key", ""),
    )
    await firestore.audit(f"founder:{founder_id}", "feedback",
                          f"applications/{application_id}/sections/{section_id}", "success",
                          f"{feedback_type}: {reason[:160]}")

    if feedback_type == "approve":
        # Approved text becomes the canonical answer for its question key (docs/06)
        await firestore.apply_profile_update(
            founder_id, "canonical_answer_update",
            {"question_key": section.get("section_key", section_id),
             "text": edited_text or original, "tags": [section.get("section_key", section_id)]},
            evidence="founder approved this section",
        )

    # Synchronous distillation — the rule exists before the next draft
    distill_result = await distill_service.run_distillation(feedback_id, session_service)

    # A requested change returns the application to DRAFTING. All sections
    # approved moves AWAITING_REVIEW → APPROVED (and mints the submit key).
    transitioned = None
    if feedback_type in ("edit", "reject"):
        if app_state == Step.AWAITING_REVIEW:
            result = await pipeline_service.advance_application(
                application_id, Step.DRAFTING, actor="agent:orchestrator")
            transitioned = result.get("current_step")
    elif all(s.get("status") == SectionStatus.APPROVED for s in sections):
        if app_state == Step.AWAITING_REVIEW:
            result = await pipeline_service.advance_application(
                application_id, Step.APPROVED, actor="agent:orchestrator")
            transitioned = result.get("current_step")

    return {
        "status": "success",
        "feedback_id": feedback_id,
        "distillation": distill_result,
        # The UI must use this explicit boolean rather than treating a returned
        # error envelope as proof that a durable learning rule exists.
        "distillation_succeeded": distill_result.get("status") == "success",
        "application_step": transitioned or app_state,
    }
