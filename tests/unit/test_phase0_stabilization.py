"""Phase 0 safety gates: decisions, consequences, and durable wakes."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from agents.co_founder import state_schema as ss
from agents.co_founder.callbacks import initialize_session_state
from services import (
    approval_service,
    capability_registry,
    distill_service,
    external_action_service,
    firestore,
    wake_delivery_service,
)
from tests.conftest import bind_fill_report
from tests.unit.test_issue_regressions import fake_store_create_application

pytestmark = pytest.mark.asyncio


async def _pending_submit(fake_store):
    app_id = await fake_store_create_application(
        fake_store, state="AWAITING_SUBMIT_APPROVAL")
    bound = await bind_fill_report(fake_store, app_id)
    requested = await approval_service.request_approval(
        app_id, founder_id="founder", session_id="session-1")
    return app_id, bound, requested["approval_id"]


async def test_competing_approval_decisions_have_one_winner_and_one_wake(
        fake_store):
    _app_id, _bound, approval_id = await _pending_submit(fake_store)
    grant, deny = await asyncio.gather(
        approval_service.resolve(
            approval_id, "grant", "founder", "session-1"),
        approval_service.resolve(
            approval_id, "deny", "founder", "session-1"),
    )
    assert sum(result["status"] == "success" for result in (grant, deny)) == 1
    loser = next(result for result in (grant, deny)
                 if result["status"] == "error")
    assert loser["error_code"] == "approval_terminal"
    expected_wakes = 1 if grant["status"] == "success" else 0
    assert len(fake_store.wake_deliveries) == expected_wakes


async def test_approval_spend_and_action_prepare_share_one_boundary(fake_store):
    app_id, bound, approval_id = await _pending_submit(fake_store)
    await approval_service.resolve(
        approval_id, "grant", "founder", "session-1")
    result = await external_action_service.prepare(
        "founder", "browser", "submit_application", "submit-key-1",
        {"application_id": app_id, "subject_hash": bound["subject_hash"]},
        session_id="session-1", application_id=app_id, resource_id=app_id,
        subject_hash=bound["subject_hash"], approval_id=approval_id,
        consume_approval=True, approval_gate="submit_application")
    assert result["claimed"] is True
    assert fake_store.approvals[approval_id]["status"] == "CONSUMED"
    assert fake_store.approvals[approval_id]["terminal_action_id"] == \
        result["action_id"]
    assert fake_store.external_actions[result["action_id"]][
        "approval_consumed"] is True


async def test_binding_mismatch_spends_nothing_and_creates_no_action(fake_store):
    app_id, _bound, approval_id = await _pending_submit(fake_store)
    await approval_service.resolve(
        approval_id, "grant", "founder", "session-1")
    result = await external_action_service.prepare(
        "founder", "browser", "submit_application", "submit-key-drift",
        {"application_id": app_id, "subject_hash": "sha256:changed"},
        session_id="session-1", application_id=app_id, resource_id=app_id,
        subject_hash="sha256:changed", approval_id=approval_id,
        consume_approval=True, approval_gate="submit_application")
    assert result["error_code"] == "approval_binding_mismatch"
    assert fake_store.approvals[approval_id]["status"] == "GRANTED"
    assert fake_store.external_actions == {}


async def test_v2_approval_rejects_capability_binding_drift(fake_store):
    app_id, bound, approval_id = await _pending_submit(fake_store)
    approval = fake_store.approvals[approval_id]
    assert approval["schema_version"] == 2
    assert approval["capability_id"] == "external.submit_application"
    # Model a registry/approval row whose capability binding no longer names
    # the reviewed execution path.  Subject matching alone must not spend it.
    approval["capability_id"] = "external.send_email"
    await approval_service.resolve(
        approval_id, "grant", "founder", "session-1")

    result = await external_action_service.prepare(
        "founder", "browser", "submit_application", "submit-key-capability-drift",
        {"application_id": app_id, "subject_hash": bound["subject_hash"]},
        session_id="session-1", application_id=app_id, resource_id=app_id,
        subject_hash=bound["subject_hash"], approval_id=approval_id,
        consume_approval=True, approval_gate="submit_application")

    assert result["error_code"] == "approval_binding_mismatch"
    assert fake_store.approvals[approval_id]["status"] == "GRANTED"
    assert fake_store.external_actions == {}


async def test_submit_receipt_and_domain_state_commit_together(fake_store):
    app_id, bound, approval_id = await _pending_submit(fake_store)
    await approval_service.resolve(
        approval_id, "grant", "founder", "session-1")
    prepared = await external_action_service.prepare(
        "founder", "browser", "submit_application", "submit-key-t3",
        {"application_id": app_id, "subject_hash": bound["subject_hash"]},
        session_id="session-1", application_id=app_id, resource_id=app_id,
        subject_hash=bound["subject_hash"], approval_id=approval_id,
        consume_approval=True, approval_gate="submit_application")

    receipt = await external_action_service.finish(
        "founder", prepared["action_id"], prepared["lease_owner"],
        "SUCCEEDED", action_kind="submit_application",
        idempotency_key="submit-key-t3", provider_effect_id="confirmation-1",
        result_ref={"confirmation_id": "confirmation-1",
                    "portal_url": "https://portal.example/form"})

    assert receipt["status"] == "SUCCEEDED"
    assert fake_store.external_actions[prepared["action_id"]]["status"] == \
        "SUCCEEDED"
    assert fake_store.applications[app_id]["state"] == "SUBMITTED"
    assert fake_store.applications[app_id]["submission"][
        "external_action_id"] == prepared["action_id"]


async def test_failed_wake_is_visible_and_reclaimable(fake_store):
    created = await firestore.create_wake_delivery(
        "founder", "session-1", "feedback", "feedback-1",
        "Resume: founder reviewed a section.", {"pending_signals": []})

    async def fail(*_args):
        raise RuntimeError("container stopped")

    first = await wake_delivery_service.deliver(
        "founder", created["delivery_id"], fail)
    assert first["error_code"] == "dispatch_failed"
    assert fake_store.wake_deliveries[created["delivery_id"]]["status"] == \
        "FAILED"

    calls = 0

    async def succeed(*_args):
        nonlocal calls
        calls += 1

    second = await wake_delivery_service.deliver(
        "founder", created["delivery_id"], succeed)
    assert second["delivery_status"] == "DELIVERED"
    row = fake_store.wake_deliveries[created["delivery_id"]]
    assert calls == 1 and row["attempt"] == 2


async def test_exhausted_wake_becomes_visible_and_is_explicitly_drainable(
        fake_store):
    created = await firestore.create_wake_delivery(
        "founder", "session-1", "system_notice", "notice-dead-letter",
        "Resume durable work.", {})
    fake_store.wake_deliveries[created["delivery_id"]]["max_attempts"] = 1

    async def fail(*_args):
        raise RuntimeError("closed session")

    failed = await wake_delivery_service.deliver(
        "founder", created["delivery_id"], fail)

    assert failed["error_code"] == "delivery_dead_letter"
    assert fake_store.wake_deliveries[created["delivery_id"]]["status"] == \
        "DEAD_LETTER"
    assert any(item.get("delivery_id") == created["delivery_id"]
               for item in fake_store.founder_inbox.values())
    requeued = await firestore.requeue_wake_delivery(
        "founder", created["delivery_id"])
    assert requeued["delivery_status"] == "FAILED"
    assert fake_store.wake_deliveries[created["delivery_id"]]["attempt"] == 0


async def test_distiller_logs_metadata_not_founder_content(
        fake_store, monkeypatch, caplog):
    secret_feedback = "private founder feedback must never enter logs"
    feedback_id = "feedback-private"
    fake_store.feedback[feedback_id] = {
        "id": feedback_id, "founder_id": "founder", "type": "reject",
        "original": "private draft", "edited_text": "",
        "reason": secret_feedback, "distilled": False,
    }

    class Sessions:
        async def create_session(self, **_kwargs):
            return None

    class Runner:
        async def run_async(self, **_kwargs):
            yield SimpleNamespace(
                author="distiller", invocation_id="inv-1",
                content=SimpleNamespace(parts=[SimpleNamespace(
                    text=secret_feedback)]))

    monkeypatch.setattr(distill_service, "_distill_runner", Runner())
    monkeypatch.setattr(distill_service, "_session_service", Sessions())
    with caplog.at_level(logging.INFO, logger=distill_service.__name__):
        result = await distill_service.run_distillation(feedback_id)
    assert result["status"] == "success"
    assert secret_feedback not in caplog.text
    assert "private draft" not in caplog.text
    assert "inv-1" in caplog.text


async def test_active_session_projection_repairs_from_durable_application(
        fake_store):
    app_id = await fake_store_create_application(
        fake_store, state=ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL)
    context = SimpleNamespace(
        state={ss.K_ACTIVE_APPLICATION_ID: app_id,
               ss.K_CURRENT_STEP: ss.ApplicationStep.DRAFTING,
               ss.K_USER_PROFILE_ID: "founder"},
        user_id="founder", session=SimpleNamespace(
            app_name="co_founder", user_id="founder", id="session-1"))
    stopped = await initialize_session_state(context)
    assert stopped is None
    assert context.state[ss.K_CURRENT_STEP] == \
        ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL


async def test_unreadable_active_authority_stops_before_inference(fake_store):
    context = SimpleNamespace(
        state={ss.K_ACTIVE_APPLICATION_ID: "missing-app",
               ss.K_CURRENT_STEP: ss.ApplicationStep.AWAITING_SUBMIT_APPROVAL,
               ss.K_USER_PROFILE_ID: "founder"},
        user_id="founder", session=SimpleNamespace(
            app_name="co_founder", user_id="founder", id="session-1"))
    stopped = await initialize_session_state(context)
    assert stopped is not None
    assert context.state[ss.K_CURRENT_STEP] == ss.ApplicationStep.IDLE
    assert context.state["temp:effect_failures"][
        "authority_reconciliation"]["error_code"] == "authority_unavailable"


async def test_consequence_manifest_is_closed_and_binding_specific(fake_store):
    capability_registry.validate_manifest()
    descriptor = capability_registry.require_external_action(
        "send_email", "alex_mail")
    assert descriptor.approval_policy_id == "exact_human_approval.v1"
    with pytest.raises(ValueError):
        capability_registry.require_external_action("send_email", "drive")
    with pytest.raises(ValueError):
        capability_registry.require_external_action(
            "model_authored_shell", "browser")
    controlled = capability_registry.require_controlled_action(
        "H4S_SEND_EMAIL", "h4s_google")
    assert controlled.capability_id == "external.H4S_SEND_EMAIL"
    with pytest.raises(ValueError):
        capability_registry.require_controlled_action(
            "H4S_SEND_EMAIL", "founder_gmail")
