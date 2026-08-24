"""Feedback tools (docs/05, 06). Errors as data. Thin wrappers over
services.feedback_service."""

import os
import re

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run

# A stored artifact name is a single flat filename — never a path.
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def record_feedback(section_id: str, feedback_type: str, reason: str, edited_text: str,
                    tool_context: ToolContext) -> dict:
    """Record founder feedback on a draft section; runs the distiller
    SYNCHRONOUSLY so the new rule exists before the next draft.

    Args:
        section_id: The section being reviewed.
        feedback_type: One of approve | edit | reject.
        reason: The founder's verbatim reason. Required for edit and reject.
        edited_text: The founder's edited text, when feedback_type is edit.

    Returns:
        dict with status. Writes the feedback row, updates section status,
        awaits distillation, and when all sections are APPROVED transitions the
        application to APPROVED and mints the submit idempotency key.
    """
    from services import feedback_service

    result = run(feedback_service.record_feedback(
        founder_id=tool_context.state.get(ss.K_USER_PROFILE_ID, "founder"),
        application_id=tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, ""),
        section_id=section_id,
        feedback_type=feedback_type,
        reason=reason,
        edited_text=edited_text,
    ))
    step = result.get("application_step")
    if result.get("status") == "success" and step:
        tool_context.state[ss.K_CURRENT_STEP] = step
        tool_context.state[ss.K_PENDING_SIGNALS] = (
            [] if step == ss.ApplicationStep.APPROVED else ["founder_feedback"]
        )
    return result


def get_feedback(feedback_id: str, tool_context: ToolContext) -> dict:
    """Fetch one feedback record (distiller input).

    Args:
        feedback_id: The feedback record to fetch.

    Returns:
        dict with status and the full feedback record.
    """
    from services import firestore

    async def _go():
        record = await firestore.get_feedback(feedback_id)
        if not record:
            return {"status": "error", "error": True, "message": f"feedback {feedback_id} not found"}
        return {"status": "success", "feedback": record}

    return run(_go())


def mark_distilled(feedback_id: str, rule_ids: list[str], tool_context: ToolContext) -> dict:
    """Mark a feedback record as distilled and link the profile rules it produced.

    Args:
        feedback_id: The feedback record to mark.
        rule_ids: Profile mutation ids created from this feedback.

    Returns:
        dict with status.
    """
    from services import firestore

    return run(firestore.mark_distilled(feedback_id, rule_ids))


def submit_voice_note(artifact_name: str, context: str, tool_context: ToolContext) -> dict:
    """Process a founder voice note: transcribe + extract intent via Gemini
    audio understanding, store the transcript verbatim, route the intent into
    record_answer or record_feedback.

    Args:
        artifact_name: The stored voice-note artifact to transcribe, as returned
            by the voice-note upload (e.g. "voicenote_founder_ab12cd34.webm").
        context: What the note is about, e.g. "feedback on section traction".

    Returns:
        dict with status, transcript, and extracted intent. The raw audio is
        kept as an artifact and never discarded.
    """
    from services import storage, voice_service

    # Model-supplied name lands in a filesystem path — reduce it to a single
    # safe artifact component (no path separators, no traversal) before use.
    name = os.path.basename((artifact_name or "").strip())
    if not name or not _ARTIFACT_NAME.match(name) or set(name) == {"."}:
        return {"status": "error", "error": True,
                "message": "a valid voice-note artifact name is required"}
    try:
        # In prod the artifact may live only in the bucket (another instance
        # wrote it) — pull it into the local cache before transcription.
        storage.download_if_missing(name)
    except Exception:  # noqa: BLE001 — a cache miss is not fatal; transcribe reports it
        pass
    return run(voice_service.transcribe(storage.artifact_path(name), context))
