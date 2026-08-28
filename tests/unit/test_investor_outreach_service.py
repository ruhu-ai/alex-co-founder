from __future__ import annotations

import time

import pytest

from services.actor_identity import ActorPrincipal, WorkspaceRole, create_membership
from services.durable_store import InMemoryDurableStore
from services.investor_outreach_service import InvestorOutreachService
from services.platform_approval_service import PlatformApprovalService
from services.workflow_runtime import WorkflowRuntime

pytestmark = pytest.mark.asyncio


async def _principal(store: InMemoryDurableStore, workspace_id: str = "workspace_a"):
    membership = await create_membership(
        actor_id="actor_owner", workspace_id=workspace_id,
        auth_subject="subject_owner", role=WorkspaceRole.OWNER,
        created_by="test", store=store)
    return ActorPrincipal(
        actor_id="actor_owner", workspace_id=workspace_id,
        role=WorkspaceRole.OWNER, role_grants=frozenset(),
        candidate_assignments=frozenset(), interview_assignments=frozenset(),
        session_auth_time=int(time.time()),
        membership_version=membership["version"],
        membership_id=membership["membership_id"])


async def _search(_query: str, _limit: int):
    return {"status": "success", "results": [
        {"title": "Africa AI Seed Fund", "url": "https://fund.example/thesis",
         "snippet": "Seed investor in African artificial intelligence infrastructure.",
         "email": "partner@fund.example"},
        {"title": "General Capital", "url": "https://general.example/portfolio",
         "snippet": "General technology investments.",
         "email": "hello@general.example"},
    ]}


async def _send(**kwargs):
    assert kwargs["provider_request_id"].startswith("providerrequest_")
    return {"status": "success", "provider_effect_id": "gmail-message-1",
            "provider_thread_id": "gmail-thread-1",
            "rfc822_message_id": "<prepared-1@ruhu.ai>"}


async def _drafted_vertical(store: InMemoryDurableStore):
    principal = await _principal(store)
    service = InvestorOutreachService(store, search_fn=_search, send_fn=_send)
    started = await service.start(
        principal=principal,
        objective="Find seed investors for African AI infrastructure",
        origin_session_id="session-investors",
        client_request_id="investor-request-0001")
    outreach_id = started["outreach"]["outreach_id"]
    await service.research(workspace_id="workspace_a", outreach_id=outreach_id)
    ranked = await service.rank(
        workspace_id="workspace_a", outreach_id=outreach_id)
    drafted = await service.draft(
        workspace_id="workspace_a", outreach_id=outreach_id, limit=1)
    return principal, service, started, ranked, drafted


async def test_second_vertical_survives_exact_approval_reply_and_cold_restart():
    store = InMemoryDurableStore()
    principal, service, started, ranked, drafted = await _drafted_vertical(store)
    assert ranked["ranked"][0]["display_name"] == "Africa AI Seed Fund"
    draft = drafted["drafts"][0]
    assert draft["status"] == "DRAFTED"
    assert draft["evidence_refs"]

    requested = await service.request_send_approval(
        principal=principal, draft_id=draft["draft_id"],
        client_request_id="investor-approval-0001")
    approval_id = requested["approval"]["approval_id"]
    decided = await PlatformApprovalService(store).decide(
        principal=principal, approval_id=approval_id, decision="GRANT")
    assert decided["approval"]["status"] == "GRANTED"

    # A new service object models a cold container; durable state carries the run.
    cold = InvestorOutreachService(store, search_fn=_search, send_fn=_send)
    sent = await cold.send_approved(
        workspace_id="workspace_a", draft_id=draft["draft_id"])
    assert sent["draft"]["status"] == "SENT"
    assert sent["action"]["status"] == "SUCCEEDED"
    assert sent["reply_wait"]["wait_status"] == "OPEN"

    reply = await cold.record_reply(
        workspace_id="workspace_a", provider_thread_id="gmail-thread-1",
        provider_message_id="gmail-reply-1", sender="partner@fund.example",
        subject="Re: Introduction: Ruhu AI",
        excerpt="This sounds relevant. Let us schedule a call.")
    assert reply["outreach"]["domain_state"] == "MEETING_PREP"
    brief = await cold.create_meeting_brief(
        workspace_id="workspace_a",
        outreach_id=started["outreach"]["outreach_id"])
    assert brief["brief"]["claims_are_evidence_bounded"] is True
    assert len(brief["brief"]["evidence_refs"]) == 3
    assert brief["outreach"]["domain_state"] == "CLOSED"
    assert brief["run"]["runtime_status"] == "SUCCEEDED"
    verified = await WorkflowRuntime(store).verify_projection(
        started["run"]["run_id"])
    assert verified["status"] == "success"
    steps = await store.list(
        "workflow_steps", filters={"run_id": started["run"]["run_id"]},
        limit=20)
    assert {step["step_key"] for step in steps} == {
        "research", "rank", "draft", "human_approval", "send",
        "wait_reply", "meeting_brief"}
    assert all(step["status"] == "COMPLETE" for step in steps)


async def test_draft_payload_drift_cannot_spend_exact_approval():
    store = InMemoryDurableStore()
    principal, service, _started, _ranked, drafted = await _drafted_vertical(store)
    draft = drafted["drafts"][0]
    requested = await service.request_send_approval(
        principal=principal, draft_id=draft["draft_id"],
        client_request_id="investor-approval-drift")
    await PlatformApprovalService(store).decide(
        principal=principal, approval_id=requested["approval"]["approval_id"],
        decision="GRANT")
    current = await store.get("outreach_drafts", draft["draft_id"])
    await store.compare_and_set(
        "outreach_drafts", draft["draft_id"], current["version"],
        {"body": current["body"] + " Send bank credentials too."})

    refused = await service.send_approved(
        workspace_id="workspace_a", draft_id=draft["draft_id"])

    assert refused["error_code"] == "approval_binding_mismatch"
    approval = await store.get("approvals", requested["approval"]["approval_id"])
    assert approval["status"] == "GRANTED"
    assert await store.list("external_actions", filters={}) == []


async def test_cross_workspace_and_unmatched_reply_fail_closed():
    store = InMemoryDurableStore()
    _principal_a, service, started, _ranked, drafted = await _drafted_vertical(store)

    assert (await service.research(
        workspace_id="workspace_b",
        outreach_id=started["outreach"]["outreach_id"]))["error_code"] \
        == "outreach_not_found"
    unmatched = await service.record_reply(
        workspace_id="workspace_a", provider_thread_id="foreign-thread",
        provider_message_id="foreign-message", sender="x@example.com",
        subject="Hello", excerpt="Unrelated")
    assert unmatched["error_code"] == "reply_unmatched"
    foreign = await service.request_send_approval(
        principal=await _principal(store, "workspace_b"),
        draft_id=drafted["drafts"][0]["draft_id"],
        client_request_id="foreign-approval-0001")
    assert foreign["error_code"] == "draft_not_sendable"


async def test_disabling_investor_vertical_does_not_disable_grant_runtime(
        monkeypatch):
    store = InMemoryDurableStore()
    principal = await _principal(store)
    monkeypatch.setenv("INVESTOR_OUTREACH_ENABLED", "0")
    service = InvestorOutreachService(store, search_fn=_search, send_fn=_send)

    blocked = await service.start(
        principal=principal, objective="Find investors",
        origin_session_id="session-a", client_request_id="investor-disabled")
    grant = await WorkflowRuntime(store).create_run(
        workspace_id="workspace_a", journey_id="grant-journey",
        run_kind="GRANT_APPLICATION", workflow_kind="grant_application:v1",
        idempotency_key="grant-still-works", domain_ref="application-1",
        originating_actor_id=principal.actor_id)

    assert blocked["error_code"] == "workflow_disabled"
    assert grant["status"] == "success"


async def test_declined_exact_draft_can_request_a_new_single_use_approval():
    store = InMemoryDurableStore()
    principal, service, _started, _ranked, drafted = await _drafted_vertical(store)
    draft = drafted["drafts"][0]
    first = await service.request_send_approval(
        principal=principal, draft_id=draft["draft_id"],
        client_request_id="investor-approval-declined")
    denied = await PlatformApprovalService(store).decide(
        principal=principal, approval_id=first["approval"]["approval_id"],
        decision="DENY")
    assert denied["wait_resolution"]["wait_status"] == "RESOLVED"

    second = await service.request_send_approval(
        principal=principal, draft_id=draft["draft_id"],
        client_request_id="investor-approval-reconsidered")

    assert second["approval"]["approval_id"] != first["approval"]["approval_id"]
    assert second["approval"]["status"] == "PENDING"
