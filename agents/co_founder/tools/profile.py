"""Profile tools (docs/05, 06). Errors as data. Thin wrappers over
services.profile_service."""

import re

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run


def _founder_id(tool_context: ToolContext) -> str:
    return tool_context.state.get(ss.K_USER_PROFILE_ID, "founder")


def _founder_turn_text(tool_context: ToolContext) -> str:
    """The current founder turn, which is the only valid interview evidence."""
    content = getattr(tool_context, "user_content", None)
    return "\n".join(
        part.text for part in (getattr(content, "parts", None) or [])
        if getattr(part, "text", None)
    ).strip()


def get_profile(tool_context: ToolContext) -> dict:
    """Founder Profile minus long canonical answers: facts, voice_rules,
    decision_patterns, rejection summary.

    Returns:
        dict with status and the profile sections.
    """
    from services import profile_service

    async def _go():
        profile = await profile_service.get_profile(_founder_id(tool_context))
        return {"status": "success", "version": profile.get("version", 0),
                "facts": profile.get("facts", {}),
                "fact_provenance": profile.get("fact_provenance", {}),
                "voice_rules": profile.get("voice_rules", []),
                "decision_patterns": profile.get("decision_patterns", []),
                "rejection_history": profile.get("rejection_history", [])[-5:]}

    return run(_go())


def record_answer(question_key: str, question: str, answer: str, tool_context: ToolContext) -> dict:
    """Record one interview answer durably.

    Args:
        question_key: Stable key for the question, e.g. "traction_summary".
        question: The question as asked.
        answer: The founder's answer, verbatim.

    Returns:
        dict with status. Appends to the application's interview_qa and upserts
        profile facts; updates checklist_status.
    """
    from services import firestore

    state = tool_context.state
    if state.get(ss.K_CURRENT_STEP) != ss.ApplicationStep.INTERVIEWING:
        return {
            "status": "error", "error": True,
            "message": ("record_answer requires an active INTERVIEWING application "
                        f"(current: {state.get(ss.K_CURRENT_STEP)})."),
        }
    app_id = state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    if not app_id:
        return {"status": "error", "error": True,
                "message": "no active application"}

    verbatim = _founder_turn_text(tool_context)
    if not verbatim:
        return {"status": "error", "error": True,
                "message": "no founder text is available to record verbatim"}
    normalize = lambda value: re.sub(r"\s+", " ", value).strip()  # noqa: E731
    if normalize(answer) != normalize(verbatim):
        return {
            "status": "error", "error": True,
            "error_code": "answer_not_verbatim",
            "message": ("The answer must be the founder's current message verbatim. "
                        "Do not summarize or add claims from an attachment."),
        }
    if re.search(r"\b(?:attached|uploaded)\b", verbatim, re.I):
        return {
            "status": "error", "error": True,
            "error_code": "attachment_notice_not_answer",
            "message": ("An attachment notice is not an interview fact. Use the "
                        "registered attachment metadata and ingestion result instead."),
        }
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,79}", question_key):
        return {"status": "error", "error": True,
                "message": "question_key must be a stable lowercase identifier"}

    result = run(firestore.record_interview_answer(
        _founder_id(tool_context), app_id, question_key,
        question[:1000], verbatim[:10000]))
    if result.get("status") == "success":
        checklist = dict(state.get(ss.K_CHECKLIST_STATUS) or {})
        checklist["interview"] = ss.ChecklistStatus.IN_PROGRESS
        state[ss.K_CHECKLIST_STATUS] = checklist
    return result


def get_relevant_answers(section_key: str, tool_context: ToolContext) -> dict:
    """Retrieve canonical answers for a draft section (docs/06 §retrieval).

    Args:
        section_key: The section being drafted, e.g. "describe_traction".

    Returns:
        dict with status and up to 5 canonical answers (exact key match first,
        then tag overlap, then recency).
    """
    from services import profile_service

    async def _go():
        return {"status": "success",
                "answers": await profile_service.get_relevant_answers(
                    _founder_id(tool_context), section_key)}

    return run(_go())


def get_voice_rules(tool_context: ToolContext) -> dict:
    """All active voice rules, newest first.

    Returns:
        dict with status and the voice rules with ids and evidence.
    """
    from services import profile_service

    async def _go():
        return {"status": "success",
                "voice_rules": await profile_service.get_voice_rules(_founder_id(tool_context))}

    return run(_go())


def apply_profile_update(kind: str, payload: dict, evidence: str, tool_context: ToolContext) -> dict:
    """The ONLY profile write path (used by the distiller).

    Args:
        kind: One of voice_rule | canonical_answer_update | fact_update | decision_pattern.
        payload: The mutation content, shape per kind (docs/06 §profile schema).
        evidence: The founder's verbatim reason/quote justifying this mutation.

    Returns:
        dict with status and the new profile version.
    """
    from services import profile_service

    founder = tool_context.state.get("founder_id") or _founder_id(tool_context)
    return run(profile_service.apply_update(founder, kind, payload, evidence))


def ingest_document(source_type: str, ref: str, tool_context: ToolContext) -> dict:
    """Ingest a company document into PROPOSED profile updates (docs/06 §bootstrap).

    Args:
        source_type: "upload" or "google_drive".
        ref: Registered upload attachment ref, or an ACTIVE Drive source_grant_id.

    Returns:
        dict with status, ingestion_id, proposed_count, summary. Never writes
        the profile directly — auto_apply_profile_updates applies the
        confident, non-conflicting ones.
    """
    if source_type == "upload":
        from services import firestore

        attachments = list(tool_context.state.get(ss.K_ACTIVE_ATTACHMENTS) or [])
        match = next((item for item in attachments
                      if ref == item.get("attachment_ref")), None)
        if not match:
            return {
                "status": "error", "error": True,
                "error_code": "unregistered_attachment",
                "message": ("That upload is not registered in this session. Use an "
                            "attachment_ref from active_attachments; never guess a filename."),
            }
        ingestion = run(firestore.get_ingestion(match["attachment_ref"]))
        if not isinstance(ingestion, dict) or not ingestion.get("id"):
            return {"status": "error", "error": True,
                    "message": "registered attachment ingestion was not found"}
        return {
            "status": "success",
            "ingestion_id": ingestion["id"],
            "already_ingested": True,
            "ingestion_status": ingestion.get("status", "QUEUED"),
            "auto_applied": int(ingestion.get("auto_applied") or 0),
            "needs_founder": int(ingestion.get("needs_founder_count") or 0),
            "summary": f"registered attachment {ingestion.get('source_ref', 'document')}",
        }

    if source_type == "google_drive":
        from services import source_ingestion

        session_id = (getattr(getattr(tool_context, "session", None), "id", "")
                      or getattr(tool_context, "session_id", ""))
        invocation_id = (getattr(tool_context, "function_call_id", "")
                         or getattr(tool_context, "invocation_id", ""))
        if not session_id or not invocation_id:
            return {"status": "error", "error": True,
                    "error_code": "invalid_contract",
                    "message": "Drive ingestion needs its originating session."}
        result = run(source_ingestion.register_source_ingestion(
            founder_id=_founder_id(tool_context), session_id=session_id,
            source_type="google_drive", source_grant_id=ref,
            source_ref=ref, display_name="Drive document", data=None,
            declared_content_type="application/octet-stream", scope="profile",
            occurrence_key=f"tool-drive:{invocation_id}", session_verified=True))
        result.pop("http_status", None)
        return result
    return {"status": "error", "error": True,
            "error_code": "invalid_contract",
            "message": "source_type must be upload or google_drive"}


def auto_apply_profile_updates(ingestion_id: str, tool_context: ToolContext) -> dict:
    """Autonomously apply an ingestion's confident, non-conflicting proposals.

    Args:
        ingestion_id: The ingestion to auto-apply.

    Returns:
        dict with status, auto_applied count, and needs_founder: the conflict
        and low-confidence items (with reasons) to ask the founder about.
        Applied writes are versioned, evidenced, and audited.
    """
    from services import profile_service

    return run(profile_service.auto_apply_profile_updates(
        _founder_id(tool_context), ingestion_id))


def propose_profile_updates(ingestion_id: str, tool_context: ToolContext) -> dict:
    """Return the next batch of unconfirmed proposals from an ingestion.

    Args:
        ingestion_id: The ingestion whose proposals to review.

    Returns:
        dict with status and the next batch of proposals.
    """
    from services import profile_service

    return run(profile_service.propose_profile_updates(
        _founder_id(tool_context), ingestion_id))


def confirm_profile_updates(ingestion_id: str, approved: list[str], rejected: list[str],
                            rejection_reasons: list[str], tool_context: ToolContext) -> dict:
    """Apply founder decisions on proposed profile updates.

    Args:
        ingestion_id: The ingestion being confirmed.
        approved: Proposal ids to write into the profile (evidence = doc citation).
        rejected: Proposal ids to turn into feedback rows for the distiller.
        rejection_reasons: Verbatim founder reasons, parallel to rejected.

    Returns:
        dict with status and counts of applied/rejected updates.
    """
    from services import profile_service

    return run(profile_service.confirm_profile_updates(
        _founder_id(tool_context), ingestion_id, approved, rejected, rejection_reasons))
