"""The deterministic adaptation contract from docs/11.

The model-backed distiller is replaced at its service boundary, then the real
feedback, profile, drafting, state-machine, and audit paths prove that a learned
rule exists before the next draft and is visibly cited by that draft.
"""

import pytest

from agents.co_founder.tools import drafting
from services import feedback_service, firestore, pipeline_service, profile_service

pytestmark = pytest.mark.asyncio


async def _seed_application(store):
    store.opportunities["opp1"] = {
        "id": "opp1", "state": "SHORTLISTED", "name": "Meridian Grant",
        "application_url": "https://portal.example/apply", "required_materials": ["essay"],
        "deadline": None, "dedup_hash": "x", "created_at": "", "updated_at": ""}
    chosen = await pipeline_service.choose_opportunity("founder", "opp1")
    app_id = chosen["application_id"]
    await pipeline_service.advance_application(app_id, "DRAFTING", actor="test")
    app = await firestore.get_application(app_id)
    assert app is not None
    sections = [{"section_id": "sec-1", "section_key": "describe_traction",
                 "content": "Our revolutionary traction is unmatched.",
                 "word_count": 6, "notes": "", "status": "DRAFTED", "version": 1}]
    await firestore.update_application(app_id, draft_sections=sections)
    await pipeline_service.advance_application(app_id, "AWAITING_REVIEW", actor="test")
    return app_id


async def test_adaptation_loop(fake_store, monkeypatch):
    app_id = await _seed_application(fake_store)

    # The synchronous distiller, faked at the service boundary (the agent-level
    # distiller needs model creds; the contract it must satisfy is this write).
    async def fake_distill(feedback_id, session_service=None):
        record = await firestore.get_feedback(feedback_id)
        assert "revolutionary" in record["reason"]  # verbatim evidence reaches the distiller
        await firestore.apply_profile_update(
            record["founder_id"], "voice_rule",
            {"rule": "never use the word 'revolutionary'"}, evidence=record["reason"])
        rules = await profile_service.get_voice_rules(record["founder_id"])
        rule_id = rules[-1]["id"]
        await firestore.mark_distilled(feedback_id, [rule_id])
        return {"status": "success", "rule_ids": [rule_id]}

    monkeypatch.setattr("services.distill_service.run_distillation", fake_distill)

    result = await feedback_service.record_feedback(
        founder_id="founder", application_id=app_id, section_id="sec-1",
        feedback_type="reject", reason="too buzzwordy, drop the word revolutionary")

    assert result["status"] == "success"
    assert result["distillation"]["status"] == "success"  # synchronous, not queued

    # 1. verbatim reason stored
    fb = await firestore.get_feedback(result["feedback_id"])
    assert fb["reason"] == "too buzzwordy, drop the word revolutionary"
    assert fb["distilled"] is True

    # 2. profile mutated with an active rule
    rules = await profile_service.get_voice_rules("founder")
    rule = next(r for r in rules if "revolutionary" in r["rule"])
    assert fb["distilled_rule_ids"] == [rule["id"]]

    # 3. version bumped and the rejected section returned to drafting
    profile = await profile_service.get_profile("founder")
    assert profile["version"] >= 1
    app = await firestore.get_application(app_id)
    assert app is not None
    assert app["state"] == "DRAFTING"
    assert app["draft_sections"][0]["status"] == "CHANGES_REQUESTED"

    # 4. The next real save follows the rule and cites the exact learned rule.
    redraft = drafting.save_draft_section(
        "describe_traction",
        "Our traction comes from a live pilot and a growing waitlist.",
        11,
        f"avoided 'revolutionary' per {rule['id']}",
        _ToolCtx(app_id),
    )
    assert redraft["status"] == "success"
    app = await firestore.get_application(app_id)
    section = app["draft_sections"][0]
    assert "revolutionary" not in section["content"].lower()
    assert rule["id"] in section["notes"]
    assert section["version"] == 2

    # 5. Audit evidence preserves the causal sequence.
    actions = [row["action"] for row in fake_store.audit]
    assert actions.index("feedback") < actions.index("profile_update")
    assert actions.index("profile_update") < actions.index("save_draft_section")


async def test_approve_advances_and_absorbs_canonical_answer(fake_store, monkeypatch):
    app_id = await _seed_application(fake_store)
    async def fake_distill(feedback_id, session_service=None):
        return {"status": "success"}

    monkeypatch.setattr("services.distill_service.run_distillation", fake_distill)

    result = await feedback_service.record_feedback(
        founder_id="founder", application_id=app_id, section_id="sec-1",
        feedback_type="approve")

    assert result["application_step"] == "APPROVED"
    app = await firestore.get_application(app_id)
    assert app is not None
    # the canonical answer library absorbed the approved text
    profile = await profile_service.get_profile("founder")
    assert any(a["question_key"] == "describe_traction" for a in profile["canonical_answers"])


# ---------------------------------------------------------------------------
# Evidence Checker never strands an application (docs/20, docs/03 transition 5)
# ---------------------------------------------------------------------------

def _fake_evidence_store(monkeypatch, rows=None):
    """In-memory evidence_checks. The collection's own transactional behaviour
    is covered by tests/unit/test_gemma_evidence.py; what this file asserts is
    that the real tool still advances, so it must not need a live Firestore."""
    import time as _time
    rows = {} if rows is None else rows

    async def get_row(report_id, founder_id=None, application_id=None):
        return rows.get(report_id)

    async def claim(report_id, row, lease_seconds):
        current = rows.get(report_id)
        if current and current.get("execution_status") == "COMPLETE":
            return {"claimed": False, "existing": current}
        if current and (_time.time() - float(current.get("lease_started_epoch") or 0)) <= lease_seconds:
            return {"claimed": False, "existing": None}
        rows[report_id] = {**row, "report_id": report_id, "execution_status": "PREPARED",
                           "lease_owner": "owner", "lease_started_epoch": _time.time(),
                           "lease_seconds": lease_seconds}
        return {"claimed": True, "existing": None, "lease_owner": "owner"}

    async def complete(report_id, report, lease_owner, application_id):
        rows[report_id] = {**report, "report_id": report_id, "execution_status": "COMPLETE"}
        await firestore.update_application(application_id,
                                           latest_evidence_check_id=report_id)
        return True

    monkeypatch.setattr("services.firestore.get_evidence_check", get_row)
    monkeypatch.setattr("services.firestore.claim_evidence_check", claim)
    monkeypatch.setattr("services.firestore.complete_evidence_check", complete)
    return rows


class _ToolCtx:
    """Minimal ToolContext stand-in: complete_drafting only touches .state."""

    def __init__(self, app_id):
        self.state = {"active_application_id": app_id,
                      "current_step": "DRAFTING", "checklist_status": {}}


async def _ready_to_complete(fake_store, monkeypatch):
    app_id = await _seed_application(fake_store)
    await pipeline_service.advance_application(app_id, "DRAFTING", actor="test")
    rows = _fake_evidence_store(monkeypatch)
    return app_id, rows


async def test_model_failure_still_reaches_awaiting_review(fake_store, monkeypatch):
    """Advisory means advisory: a dead model must not hold a finished draft.
    This calls the real tool, so a regression in complete_drafting is caught."""
    from agents.co_founder.tools import drafting
    from services import gemma_evidence

    app_id, rows = await _ready_to_complete(fake_store, monkeypatch)

    class DeadBackend:
        model_id = "fake-gemma"

        async def check(self, pack, timeout_s):
            raise RuntimeError("provider down")

    gemma_evidence.set_backend(DeadBackend())
    try:
        ctx = _ToolCtx(app_id)
        result = drafting.complete_drafting(ctx)
    finally:
        gemma_evidence.set_backend(None)

    assert result["status"] == "success", result
    app = await firestore.get_application(app_id)
    assert app["state"] == "AWAITING_REVIEW"
    assert ctx.state["current_step"] == "AWAITING_REVIEW"
    # an honest unavailable report exists rather than nothing at all
    assert any(r["status"] == "UNAVAILABLE" for r in rows.values())


async def test_a_check_already_running_holds_the_transition(fake_store, monkeypatch):
    """IN_PROGRESS is a concurrency condition, not a verdict: advancing on it
    would enter review with no report for the current draft."""
    from agents.co_founder.tools import drafting
    from services import gemma_evidence

    app_id, rows = await _ready_to_complete(fake_store, monkeypatch)

    async def busy(report_id, row, lease_seconds):
        return {"claimed": False, "existing": None}

    monkeypatch.setattr("services.firestore.claim_evidence_check", busy)

    class Backend:
        model_id = "fake-gemma"

        async def check(self, pack, timeout_s):
            return {"verdict": "CLEAN", "findings": []}

    gemma_evidence.set_backend(Backend())
    try:
        result = drafting.complete_drafting(_ToolCtx(app_id))
    finally:
        gemma_evidence.set_backend(None)

    assert result.get("error") and result.get("retriable")
    app = await firestore.get_application(app_id)
    assert app["state"] == "DRAFTING"


async def test_unreadable_evidence_blocks_review_with_error_data(fake_store, monkeypatch):
    """An unknown evidence snapshot cannot honestly produce a review report."""
    from agents.co_founder.tools import drafting

    app_id, rows = await _ready_to_complete(fake_store, monkeypatch)

    async def boom(*args, **kwargs):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr("services.profile_service.get_profile", boom)
    result = drafting.complete_drafting(_ToolCtx(app_id))

    assert result["status"] == "error"
    assert result["error_code"] == "evidence_read_failed"
    assert result["retriable"] is True
    app = await firestore.get_application(app_id)
    assert app["state"] == "DRAFTING"
    assert not app.get("latest_evidence_check_id")
    assert rows == {}


async def test_disabled_checker_persists_unavailable_before_review(fake_store, monkeypatch):
    from agents.co_founder.tools import drafting
    from services import gemma_evidence

    app_id, rows = await _ready_to_complete(fake_store, monkeypatch)
    monkeypatch.delenv("GEMMA_EVIDENCE_CHECK_ENABLED", raising=False)
    gemma_evidence.set_backend(None)
    result = drafting.complete_drafting(_ToolCtx(app_id))

    assert result["status"] == "success"
    app = await firestore.get_application(app_id)
    assert app["state"] == "AWAITING_REVIEW"
    report_id = app.get("latest_evidence_check_id")
    assert report_id in rows
    assert rows[report_id]["status"] == "UNAVAILABLE"
    assert rows[report_id]["error_code"] == "not_configured"


async def test_report_persistence_failure_blocks_review(fake_store, monkeypatch):
    from agents.co_founder.tools import drafting
    from services import gemma_evidence

    app_id, rows = await _ready_to_complete(fake_store, monkeypatch)

    async def broken_persistence(*args):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr("services.firestore.complete_evidence_check", broken_persistence)

    class Backend:
        model_id = "fake-gemma"

        async def check(self, pack, timeout_s):
            return {"verdict": "CLEAN", "findings": []}

    gemma_evidence.set_backend(Backend())
    try:
        result = drafting.complete_drafting(_ToolCtx(app_id))
    finally:
        gemma_evidence.set_backend(None)

    assert result["status"] == "error"
    assert result["error_code"] == "persistence_error"
    app = await firestore.get_application(app_id)
    assert app["state"] == "DRAFTING"
    assert not app.get("latest_evidence_check_id")
    assert not any(row.get("execution_status") == "COMPLETE" for row in rows.values())
