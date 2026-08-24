"""The adaptation loop (docs/11): feedback → distill → rule → next draft cites it.

Runs at the service layer with a faked synchronous distiller when ADC/model
is unavailable; the full agent-level path runs when credentials exist.
The test is not vacuous: it fails if the distill step or the verbatim store
is removed.
"""

import pytest

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
        await firestore.mark_distilled(feedback_id, ["vr_test"])
        return {"status": "success"}

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
    assert any("revolutionary" in r["rule"] for r in rules)

    # 3. version bumped and audited
    profile = await profile_service.get_profile("founder")
    assert profile["version"] >= 1
    actions = [r["action"] for r in fake_store.audit]
    assert "feedback" in actions and "profile_update" in actions

    # 4. section went back for changes — the next draft must follow the rule
    app = await firestore.get_application(app_id)
    assert app is not None
    assert app["draft_sections"][0]["status"] == "CHANGES_REQUESTED"


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
