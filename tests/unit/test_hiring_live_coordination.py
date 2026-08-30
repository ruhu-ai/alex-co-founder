"""Real Hiring H4 communication and interview authority regressions."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import InMemoryDurableStore
from services.hiring_approval_service import resolve_approval
from services.hiring_coordination import HiringCoordinationService


class _Identity:
    async def reveal_restricted_identity(self, *, application, principal):
        assert application["workspace_id"] == principal.workspace_id
        return {"status": "success", "identity": {
            "name": "Ada Candidate", "email": "ada@example.test"}}


class _Provider:
    def __init__(self):
        self.calls = []

    async def preflight(self, *, workspace_id, connector_id):
        return {"status": "success", "connection_id": f"conn_{connector_id}"}

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["action_kind"] == "HIRING_SEND_EMAIL":
            return {"status": "success", "provider_effect_id": "gmail_message_1",
                    "result_ref": {"provider_thread_id": "thread_hiring_1",
                                   "rfc822_message_id": "<hiring@test>"}}
        return {"status": "success", "provider_effect_id": "calendar_event_1",
                "result_ref": {"event_id": "calendar_event_1",
                               "meet_link": "https://meet.invalid/opaque"}}

    async def reconcile(self, **kwargs):
        return {"status": "success", "provider_effect_id": "reconciled"}


class _UncertainProvider(_Provider):
    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "uncertain", "uncertainty_reason": "provider_timeout"}


@pytest.fixture
def founder() -> ActorPrincipal:
    return ActorPrincipal(
        actor_id="founder_actor", workspace_id="founder",
        role=WorkspaceRole.FOUNDER, session_auth_time=int(time.time()),
        membership_version=1)


async def _seed(store: InMemoryDurableStore, *, advanced: bool = True,
                workspace_id: str = "founder") -> str:
    application_id = "candidateapp_" + "a" * 28
    run_id = "run_candidate_live"
    role_id = "role_live"
    await store.create("workflow_runs", run_id, {
        "schema_version": 2, "run_id": run_id, "workspace_id": workspace_id,
        "journey_id": "journey_live", "run_kind": "CANDIDATE",
        "domain_ref": application_id, "runtime_status": "WAITING",
        "next_event_sequence": 1, "provenance": {}, "version": 1,
    })
    await store.create("hiring_roles", role_id, {
        "role_id": role_id, "workspace_id": workspace_id,
        "role_title": "Product Lead", "current_policy_version_id": "hpv_live",
        "current_policy_hash": "sha256:policy", "synthetic": False,
        "version": 1,
    })
    decision_id = "decision_live"
    await store.create("hiring_decisions", decision_id, {
        "decision_id": decision_id, "workspace_id": workspace_id,
        "candidate_application_id": application_id,
        "decision": "ADVANCE" if advanced else "HOLD",
        "commit_status": "COMMITTED", "version": 1,
    })
    await store.create("candidate_applications", application_id, {
        "candidate_application_id": application_id, "workspace_id": workspace_id,
        "role_id": role_id, "run_id": run_id, "candidate_code": "C-00000001",
        "source_kind": "PUBLIC_FORM", "synthetic": False,
        "candidate_state": "ADVANCED" if advanced else "HELD",
        "current_decision_id": decision_id, "version": 1,
    })
    return application_id


def _service(store, provider):
    return HiringCoordinationService(store=store, adapter=provider, intake=_Identity())


@pytest.mark.asyncio
async def test_advance_prepares_exact_real_email_and_executes_once(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    prepared = await _service(store, provider).prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_request_0001")
    assert prepared["status"] == "success"
    assert prepared["item"]["recipients_masked"] == ["a***@example.test"]
    assert "Founder is currently available" in prepared["item"]["body"]
    approved = await resolve_approval(
        principal=founder, approval_id=prepared["approval_id"],
        decision="GRANT", store=store)
    assert approved["approval_status"] == "GRANTED"
    sent = await _service(store, provider).execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert sent["receipt_status"] == "SUCCEEDED"
    duplicate = await _service(store, provider).execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert duplicate["duplicate"] is True
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_non_advanced_synthetic_cross_tenant_and_kill_switch_fail_closed(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store, advanced=False)
    denied = await _service(store, provider).prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_request_0002")
    assert denied["error_code"] == "founder_advance_required"
    other = ActorPrincipal(
        actor_id="other", workspace_id="other", role=WorkspaceRole.FOUNDER,
        session_auth_time=int(time.time()), membership_version=1)
    assert (await _service(store, provider).projection(
        principal=other, application_id=application_id))["error_code"] == "application_not_found"
    app = await store.get("candidate_applications", application_id)
    await store.compare_and_set("candidate_applications", application_id,
                                app["version"], {"candidate_state": "ADVANCED"})
    decision = await store.get("hiring_decisions", "decision_live")
    await store.compare_and_set("hiring_decisions", "decision_live",
                                decision["version"], {"decision": "ADVANCE"})
    monkeypatch.setenv("HIRING_LIVE_OPERATIONS_KILL_SWITCH", "1")
    killed = await _service(store, provider).prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_request_0003")
    assert killed["error_code"] == "hiring_operations_killed"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_reply_requires_exact_thread_and_candidate_sender(founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_request_0004")
    await resolve_approval(principal=founder, approval_id=prepared["approval_id"],
                           decision="GRANT", store=store)
    await service.execute(principal=founder, application_id=application_id,
                          coordination_id=prepared["coordination_id"],
                          approval_id=prepared["approval_id"])
    forged = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_forged", "thread_id": "thread_hiring_1",
            "from": "Mallory <mallory@example.test>", "subject": "Re: interview",
            "excerpt": "available tomorrow", "kind": "update"})
    assert forged["error_code"] == "reply_not_correlatable"
    reply = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_exact", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>", "subject": "Re: interview",
            "excerpt": "Option two works for me.", "kind": "update"})
    assert reply["candidate_application_id"] == application_id
    duplicate = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_exact", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>", "subject": "Re: interview",
            "excerpt": "Option two works for me.", "kind": "update"})
    assert duplicate["duplicate"] is True


@pytest.mark.asyncio
async def test_reply_email_continues_verified_thread_and_reply_stays_correlatable(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    initial = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_thread_initial")
    await resolve_approval(principal=founder, approval_id=initial["approval_id"],
                           decision="GRANT", store=store)
    await service.execute(principal=founder, application_id=application_id,
                          coordination_id=initial["coordination_id"],
                          approval_id=initial["approval_id"])
    reply = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_thread_reply", reply=True)
    item = await store.get("hiring_coordination_items", reply["coordination_id"])
    assert item["exact_action"]["payload"]["provider_thread_id"] == "thread_hiring_1"
    assert item["subject"].startswith("Re:")
    await resolve_approval(principal=founder, approval_id=reply["approval_id"],
                           decision="GRANT", store=store)
    await service.execute(principal=founder, application_id=application_id,
                          coordination_id=reply["coordination_id"],
                          approval_id=reply["approval_id"])
    correlated = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_after_two_sends", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>",
            "subject": "Re: Interview availability", "excerpt": "Option one works.",
            "kind": "update"})
    assert correlated["candidate_application_id"] == application_id


@pytest.mark.asyncio
async def test_uncertain_email_never_retries_without_reconciliation(founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _UncertainProvider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_uncertain")
    await resolve_approval(principal=founder, approval_id=prepared["approval_id"],
                           decision="GRANT", store=store)
    first = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert first["error_code"] == "reconciliation_required"
    second = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert second["error_code"] == "reconciliation_required"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_interview_binds_candidate_and_alex_and_rejects_unowned_change(founder):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    start_dt = datetime.now(timezone.utc) + timedelta(days=7)
    future = start_dt.isoformat()
    end = (start_dt + timedelta(minutes=45)).isoformat()
    prepared = await service.prepare_interview(
        principal=founder, application_id=application_id,
        start=future, end=end, timezone_name="Africa/Lagos",
        client_request_id="interview_request_0001")
    assert prepared["item"]["action_kind"] == "HIRING_CREATE_INTERVIEW"
    exact = (await store.get("hiring_coordination_items",
                             prepared["coordination_id"]))["exact_action"]
    assert exact["recipients"] == ["ada@example.test", "alex@ruhu.ai"]
    denied = await service.prepare_interview(
        principal=founder, application_id=application_id,
        start=future, end=end, timezone_name="Africa/Lagos",
        client_request_id="interview_request_0002",
        target_event_id="not_owned")
    assert denied["error_code"] == "interview_not_found"


@pytest.mark.asyncio
async def test_interview_create_update_and_cancel_each_need_exact_approval(founder):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    start_dt = datetime.now(timezone.utc) + timedelta(days=8)

    async def prepare_execute(*, request_id, start, end, target="", cancel=False):
        prepared = await service.prepare_interview(
            principal=founder, application_id=application_id,
            start=start.isoformat() if start else "",
            end=end.isoformat() if end else "", timezone_name="Africa/Lagos",
            client_request_id=request_id, target_event_id=target, cancel=cancel)
        await resolve_approval(
            principal=founder, approval_id=prepared["approval_id"],
            decision="GRANT", store=store)
        return await service.execute(
            principal=founder, application_id=application_id,
            coordination_id=prepared["coordination_id"],
            approval_id=prepared["approval_id"])

    created = await prepare_execute(
        request_id="interview_create_exact",
        start=start_dt, end=start_dt + timedelta(minutes=45))
    assert created["receipt_status"] == "SUCCEEDED"
    event_id = created["result_ref"]["event_id"]
    updated = await prepare_execute(
        request_id="interview_update_exact",
        start=start_dt + timedelta(hours=2),
        end=start_dt + timedelta(hours=2, minutes=45), target=event_id)
    assert updated["receipt_status"] == "SUCCEEDED"
    cancelled = await prepare_execute(
        request_id="interview_cancel_exact", start=None, end=None,
        target=event_id, cancel=True)
    assert cancelled["receipt_status"] == "SUCCEEDED"
    assert [call["action_kind"] for call in provider.calls] == [
        "HIRING_CREATE_INTERVIEW", "HIRING_UPDATE_INTERVIEW",
        "HIRING_CANCEL_INTERVIEW"]


@pytest.mark.asyncio
async def test_tampered_draft_cannot_consume_approval(founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_request_0005")
    item = await store.get("hiring_coordination_items", prepared["coordination_id"])
    exact = dict(item["exact_action"])
    exact["payload"] = {**exact["payload"], "body": "tampered"}
    await store.compare_and_set("hiring_coordination_items", item["coordination_id"],
                                item["version"], {"exact_action": exact})
    await resolve_approval(principal=founder, approval_id=prepared["approval_id"],
                           decision="GRANT", store=store)
    result = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert result["error_code"] == "approval_binding_mismatch"
    assert provider.calls == []
