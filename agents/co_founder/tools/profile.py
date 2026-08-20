"""Profile tools (docs/05, 06). Errors as data. Thin wrappers over
services.profile_service."""

from google.adk.tools import ToolContext

from .. import state_schema as ss
from ._common import run


def _founder_id(tool_context: ToolContext) -> str:
    return tool_context.state.get(ss.K_USER_PROFILE_ID, "founder")


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
    from services import firestore, profile_service

    founder = _founder_id(tool_context)
    result = run(profile_service.record_answer(founder, question_key, question, answer))

    app_id = tool_context.state.get(ss.K_ACTIVE_APPLICATION_ID, "")
    if app_id:
        async def _append():
            app = await firestore.get_application(app_id)
            if app:
                qa = app.get("interview_qa", [])
                qa.append({"question_key": question_key, "question": question, "answer": answer})
                await firestore.update_application(app_id, interview_qa=qa)
        run(_append())
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
        ref: Uploaded filename/artifact ref, or a founder-selected Drive file id.

    Returns:
        dict with status, ingestion_id, proposed_count, summary. Never writes
        the profile directly — auto_apply_profile_updates applies the
        confident, non-conflicting ones.
    """
    from services import profile_service

    artifact = ref
    if source_type == "google_drive":
        from services import drive_adapter

        fetched = drive_adapter.fetch_file(ref)
        if fetched.get("status") != "success":
            return fetched
        artifact = fetched["artifact"]
    return run(profile_service.ingest_document(
        _founder_id(tool_context), source_type, ref, artifact))


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

    return run(profile_service.propose_profile_updates(ingestion_id))


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
