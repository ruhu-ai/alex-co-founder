"""Regression coverage for the detailed-review findings fixed together."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.co_founder.state_schema import ApplicationStep, SectionStatus
from agents.co_founder.sub_agents.form_filler import verify_before_action
from agents.co_founder.tools import browser, drafting
from agents.co_founder.tools._common import run
from services import (
    approval_service,
    browser_service,
    feedback_service,
    pipeline_service,
    portal_accounts,
)

pytestmark = pytest.mark.asyncio


def _context(state, session_id="session-1"):
    return SimpleNamespace(
        state=state,
        user_id="founder",
        session=SimpleNamespace(
            app_name="co_founder", user_id="founder", id=session_id),
    )


async def test_staleness_fence_blocks_changed_form(monkeypatch):
    app_id = "app-stale"
    context = _context({"active_application_id": app_id,
                        "current_step": "FORM_FILLING",
                        "temp:portal_signature": "sha256:old"})
    live_session = {"page": object(), "signature": "sha256:old"}
    monkeypatch.setattr(
        browser_service, "fill_session_for_application",
        lambda candidate: live_session if candidate == app_id else None,
    )

    async def changed(_page):
        return {"status": "success", "signature": "sha256:new", "fields": []}

    monkeypatch.setattr(browser_service, "inspect", changed)
    result = await verify_before_action(
        SimpleNamespace(name="submit_form"), {}, context)
    assert result["stale"] is True
    assert result["current_signature"] == "sha256:new"


async def test_g3_gate_blocks_portal_tools_before_approved():
    """sign_in/fill_fields previously had NO step gate: a live portal page
    could be opened and typed into during DRAFTING. The fence now enforces G3
    for every portal-touching tool."""
    context = _context({"active_application_id": "app-1",
                        "current_step": "DRAFTING"})
    for tool_name in ("sign_in", "open_portal", "fill_fields", "submit_form"):
        result = await verify_before_action(
            SimpleNamespace(name=tool_name), {}, context)
        assert result is not None and result["error"] is True, tool_name
        assert "Gate G3" in result["message"], tool_name


async def test_g3_gate_admits_portal_tools_after_approved(monkeypatch):
    """In APPROVED the gate opens (the staleness fence still applies to
    fill/submit — expect its message, not G3's)."""
    context = _context({"active_application_id": "app-2",
                        "current_step": "APPROVED"})
    result = await verify_before_action(
        SimpleNamespace(name="sign_in"), {}, context)
    assert result is None  # tool may run
    result = await verify_before_action(
        SimpleNamespace(name="fill_fields"), {}, context)
    assert result["stale"] is True  # staleness fence, not G3
    assert "Gate G3" not in result["message"]


async def test_register_account_is_not_step_gated():
    """Account creation has its own founder approval (create_portal_account
    gate) and its verification resume can arrive at any step — the G3 step
    gate must not block it."""
    context = _context({"active_application_id": "app-3",
                        "current_step": "DRAFTING"})
    result = await verify_before_action(
        SimpleNamespace(name="register_account"), {}, context)
    assert result is None


async def test_complete_drafting_advances_firestore_and_session(fake_store):
    app_id = await fake_store_create_application(fake_store, state=ApplicationStep.DRAFTING)
    fake_store.applications[app_id]["draft_sections"] = [{
        "section_id": "s1", "status": SectionStatus.DRAFTED, "content": "Ready",
    }]
    context = _context({"active_application_id": app_id,
                        "current_step": ApplicationStep.DRAFTING})
    result = drafting.complete_drafting(context)
    assert result["status"] == "success"
    assert fake_store.applications[app_id]["state"] == ApplicationStep.AWAITING_REVIEW
    assert context.state["current_step"] == ApplicationStep.AWAITING_REVIEW


async def test_feedback_rejection_returns_application_to_drafting(
        fake_store, monkeypatch):
    app_id = await fake_store_create_application(
        fake_store, state=ApplicationStep.AWAITING_REVIEW)
    fake_store.applications[app_id]["draft_sections"] = [{
        "section_id": "s1", "section_key": "traction", "status": SectionStatus.DRAFTED,
        "content": "Draft",
    }]

    async def distilled(*_args, **_kwargs):
        return {"status": "success"}

    monkeypatch.setattr("services.distill_service.run_distillation", distilled)
    result = await feedback_service.record_feedback(
        "founder", app_id, "s1", "reject", reason="Needs exact numbers")
    assert result["application_step"] == ApplicationStep.DRAFTING
    assert fake_store.applications[app_id]["state"] == ApplicationStep.DRAFTING


async def test_approval_is_session_bound_and_single_use(fake_store):
    from tests.conftest import bind_fill_report

    app_id = await fake_store_create_application(
        fake_store, state=ApplicationStep.AWAITING_SUBMIT_APPROVAL)
    await bind_fill_report(fake_store, app_id)
    requested = await approval_service.request_approval(
        app_id, founder_id="founder", session_id="session-1")
    wrong = await approval_service.resolve(
        requested["approval_id"], "grant", "founder", "session-2")
    assert wrong["status"] == "error"
    await approval_service.resolve(
        requested["approval_id"], "grant", "founder", "session-1")
    # The claim validates without consuming (post-2026-08 contract): the gate
    # answers "may I act?"; consume() runs after the side effect succeeds, so
    # a failed action never burns the founder's approval. Single-use is still
    # enforced — after consume, the gate refuses.
    first, second = await asyncio.gather(
        approval_service.resolve_for_submit(
            app_id, founder_id="founder", session_id="session-1"),
        approval_service.resolve_for_submit(
            app_id, founder_id="founder", session_id="session-1"),
    )
    assert first["status"] == "success" and second["status"] == "success"
    await approval_service.consume(first["approval_id"])
    after = await approval_service.resolve_for_submit(
        app_id, founder_id="founder", session_id="session-1")
    assert after["status"] == "error"  # CONSUMED is final


async def test_expired_grant_no_longer_satisfies_the_gate(fake_store):
    """TTL is a real fence: a GRANTED approval that has passed its expiry must
    not resolve at the submit gate. Only single-use and session-binding were
    covered before — the expires_at path had no test."""
    from datetime import datetime, timedelta, timezone

    from tests.conftest import bind_fill_report

    app_id = await fake_store_create_application(
        fake_store, state=ApplicationStep.AWAITING_SUBMIT_APPROVAL)
    # Fully bound, so expiry — not a missing binding — is what refuses.
    await bind_fill_report(fake_store, app_id)
    requested = await approval_service.request_approval(
        app_id, founder_id="founder", session_id="session-1")
    aid = requested["approval_id"]
    await approval_service.resolve(aid, "grant", "founder", "session-1")
    assert (await approval_service.resolve_for_submit(
        app_id, founder_id="founder", session_id="session-1")
    )["status"] == "success"
    # fast-forward past the TTL
    fake_store.approvals[aid]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    refused = await approval_service.resolve_for_submit(
        app_id, founder_id="founder", session_id="session-1")
    assert refused["status"] == "error"  # expired grant is not valid


async def test_resolve_refuses_expired_pending_request(fake_store):
    """Granting a request that already expired is refused with a clear reason."""
    from datetime import datetime, timedelta, timezone

    from tests.conftest import bind_fill_report

    app_id = await fake_store_create_application(
        fake_store, state=ApplicationStep.AWAITING_SUBMIT_APPROVAL)
    await bind_fill_report(fake_store, app_id)
    requested = await approval_service.request_approval(
        app_id, founder_id="founder", session_id="session-1")
    aid = requested["approval_id"]
    fake_store.approvals[aid]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    result = await approval_service.resolve(aid, "grant", "founder", "session-1")
    assert result["status"] == "error"
    assert "expired" in result["message"]


async def test_registration_requests_approval_before_browser_side_effect(
        fake_store, monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_SECRETS_FILE", str(tmp_path / "portal.json"))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    portal_accounts._CACHE.clear()
    called = False

    async def must_not_register(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"status": "success"}

    monkeypatch.setattr(browser_service, "register", must_not_register)
    result = browser.register_account(
        "https://portal.example", "alex@ruhu.ai",
        _context({"active_application_id": "app-register"}))
    assert result["status"] == "needs_approval"
    assert called is False


async def test_registration_parks_without_polling_and_persists_wake_route(
        fake_store, monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_SECRETS_FILE", str(tmp_path / "portal.json"))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    portal_accounts._CACHE.clear()
    portal_target = "portal:portal.example"
    portal_details = {"portal_url": "https://portal.example",
                      "email": "alex@ruhu.ai"}
    requested = await approval_service.request_approval(
        portal_target, gate="create_portal_account", details=portal_details,
        founder_id="founder", session_id="session-1",
        subject_hash=approval_service.action_subject_hash(
            "create_portal_account", portal_target, portal_details))
    await approval_service.resolve(
        requested["approval_id"], "grant", "founder", "session-1")

    class RegistrationContext:
        async def close(self):
            return None

    async def registered(*_args, **_kwargs):
        return {"status": "success", "body": "Check your email to verify",
                "context": RegistrationContext(), "page": object(),
                "run_id": "fill-run"}

    async def no_mail(**_kwargs):
        return {"status": "error", "error": True, "message": "not here yet"}

    monkeypatch.setattr(browser_service, "register", registered)
    monkeypatch.setattr("services.alex_mailbox.wait_for_email", no_mail)
    monkeypatch.setattr(
        browser_service, "close_run",
        lambda *_a, **_k: __import__("asyncio").sleep(
            0, result={"status": "success"}
        ),
    )
    context = _context({"active_application_id": "app-register"})
    result = browser.register_account(
        "https://portal.example", "alex@ruhu.ai", context)
    assert result["status"] == "waiting"
    assert context.state["pending_signals"] == ["portal_verification"]
    assert fake_store.portal_registrations["portal.example"]["session_id"] == "session-1"


async def test_discovery_public_url_guard_blocks_private_dns(monkeypatch):
    async def private_dns(_host, _port):
        return ["10.0.0.8"]

    browser_service.set_resolver(private_dns)
    try:
        refusal = await browser_service.validate_public_url("https://program.example/apply")
    finally:
        browser_service.set_resolver(None)
    assert "private or reserved" in refusal


async def test_fit_score_thresholds_are_enforced(fake_store):
    fake_store.opportunities["opp-1"] = {
        "id": "opp-1", "state": "DISCOVERED", "deadline": None,
        "required_materials": [],
    }
    bad_shortlist = await pipeline_service.shortlist(
        "opp-1", "fit", "", fit_score=69)
    bad_archive = await pipeline_service.archive(
        "opp-1", "not a fit", fit_score=70)
    assert bad_shortlist["status"] == "error"
    assert bad_archive["status"] == "error"
    assert fake_store.opportunities["opp-1"]["state"] == "DISCOVERED"


async def test_tool_boundary_converts_service_exception_to_data():
    async def explode():
        raise RuntimeError("backend unavailable")

    result = run(explode())
    assert result["status"] == "error"
    assert "backend unavailable" in result["message"]


async def test_external_links_never_launch_an_os_browser():
    root = Path(__file__).parents[2]
    ui = (root / "app/static/index.html").read_text(encoding="utf-8")
    oauth_setup = (root / "scripts/oauth_setup.py").read_text(encoding="utf-8")
    assert 'target="_blank"' not in ui
    assert "window.open" not in ui
    assert "data-browser-url" in ui and "browseTo(url)" in ui
    assert "open_browser=False" in oauth_setup


async def fake_store_create_application(fake_store, state):
    """Seed only the fields exercised by pipeline transitions."""
    app_id = f"app-{len(fake_store.applications) + 1}"
    fake_store.applications[app_id] = {
        "id": app_id,
        "founder_id": "founder",
        "opportunity_id": "opp-1",
        "state": state,
        "checklist": [],
        "draft_sections": [],
        "created_at": fake_store._now(),
        "updated_at": fake_store._now(),
    }
    return app_id
