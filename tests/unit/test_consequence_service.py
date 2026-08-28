from __future__ import annotations

import time

import pytest

from services.actor_identity import (
    ActorPrincipal,
    WorkspaceRole,
    create_membership,
)
from services.consequence_service import ConsequenceService
from services.durable_store import InMemoryDurableStore
from services.platform_approval_service import PlatformApprovalService
from services.workflow_contracts import RunKind
from services.workflow_runtime import WorkflowRuntime

pytestmark = pytest.mark.asyncio


async def _authority(store: InMemoryDurableStore, suffix: str = "a"):
    member = await create_membership(
        actor_id=f"actor_{suffix}", workspace_id="workspace_a",
        auth_subject=f"subject_{suffix}", role=WorkspaceRole.OWNER,
        created_by="test", store=store)
    principal = ActorPrincipal(
        actor_id=f"actor_{suffix}", workspace_id="workspace_a",
        role=WorkspaceRole.OWNER, role_grants=frozenset(),
        candidate_assignments=frozenset(), interview_assignments=frozenset(),
        session_auth_time=int(time.time()),
        membership_version=member["version"],
        membership_id=member["membership_id"])
    runtime = WorkflowRuntime(store)
    run = await runtime.create_run(
        workspace_id="workspace_a", journey_id=f"journey_{suffix}",
        run_kind=RunKind.GRANT_APPLICATION,
        workflow_kind="grant_application:v1",
        idempotency_key=f"run_{suffix}", domain_ref=f"application_{suffix}",
        originating_actor_id=principal.actor_id)
    step = await runtime.create_step(
        run["run_id"], step_key="submit", idempotency_key=f"submit_{suffix}")
    target = {"portal": "portal.example", "application_id": f"application_{suffix}"}
    payload = {"answers_hash": f"answers_{suffix}"}
    requested = await PlatformApprovalService(store).request(
        workspace_id="workspace_a", requested_by_actor_id=principal.actor_id,
        run_id=run["run_id"], plan_hash=run["plan_hash"],
        step_id=step["step_id"], capability_id="external.submit_application",
        capability_version="1.0.0", action_kind="submit_application",
        target=target, payload=payload, policy_id="exact_human_approval.v1",
        policy_version="1", domain_ref=f"application_{suffix}",
        domain_version=1, connector_id="browser",
        connector_binding_version="browser-v1",
        client_request_id=f"approval_{suffix}")
    approval_id = requested["approval"]["approval_id"]
    decided = await PlatformApprovalService(store).decide(
        principal=principal, approval_id=approval_id, decision="GRANT")
    assert decided["approval"]["status"] == "GRANTED"
    return principal, runtime, run, step, approval_id, target, payload


async def test_t1_t2_t3_are_distinct_and_duplicate_prepare_returns_receipt():
    store = InMemoryDurableStore()
    _, runtime, run, step, approval_id, target, payload = await _authority(store)
    service = ConsequenceService(store)
    prepared = await service.prepare(
        workspace_id="workspace_a", approval_id=approval_id,
        run_id=run["run_id"], step_id=step["step_id"], connector_id="browser",
        action_kind="submit_application", idempotency_key="submit_a",
        target=target, payload=payload)
    duplicate = await service.prepare(
        workspace_id="workspace_a", approval_id=approval_id,
        run_id=run["run_id"], step_id=step["step_id"], connector_id="browser",
        action_kind="submit_application", idempotency_key="submit_a",
        target=target, payload=payload)

    assert prepared["receipt_status"] == "PREPARED"
    assert duplicate["duplicate"] is True
    approval = await store.get("approvals", approval_id)
    assert approval["status"] == "CLAIMED"

    started = await service.start(
        workspace_id="workspace_a", action_id=prepared["action"]["action_id"],
        lease_owner=prepared["lease_owner"], workload_principal="worker:browser")
    assert started["receipt_status"] == "EXECUTING"
    assert started["provider_request_id"]
    assert (await store.get("approvals", approval_id))["status"] == "CONSUMED"

    settled = await service.settle(
        workspace_id="workspace_a", action_id=prepared["action"]["action_id"],
        lease_owner=prepared["lease_owner"], status="SUCCEEDED",
        provider_effect_id="provider_receipt_a",
        result_ref={"confirmation_id": "confirmation_a"})
    assert settled["receipt_status"] == "SUCCEEDED"
    assert (await runtime.verify_projection(run["run_id"]))["status"] == "success"


async def test_cancellation_between_t1_and_t2_voids_without_provider_authority():
    store = InMemoryDurableStore()
    _, runtime, run, step, approval_id, target, payload = await _authority(
        store, "cancel")
    service = ConsequenceService(store)
    prepared = await service.prepare(
        workspace_id="workspace_a", approval_id=approval_id,
        run_id=run["run_id"], step_id=step["step_id"], connector_id="browser",
        action_kind="submit_application", idempotency_key="submit_cancel",
        target=target, payload=payload)
    await runtime.cancel_run(
        run["run_id"], actor_id="actor_cancel", reason="Founder cancelled")

    started = await service.start(
        workspace_id="workspace_a", action_id=prepared["action"]["action_id"],
        lease_owner=prepared["lease_owner"], workload_principal="worker:browser")

    assert started["error_code"] == "run_fenced"
    assert (await store.get("approvals", approval_id))["status"] == "VOIDED"
    assert (await store.get(
        "external_actions", prepared["action"]["action_id"]))["status"] == "FAILED"


async def test_worker_death_after_t2_becomes_visible_uncertain():
    store = InMemoryDurableStore()
    _, _, run, step, approval_id, target, payload = await _authority(store, "death")
    service = ConsequenceService(store)
    prepared = await service.prepare(
        workspace_id="workspace_a", approval_id=approval_id,
        run_id=run["run_id"], step_id=step["step_id"], connector_id="browser",
        action_kind="submit_application", idempotency_key="submit_death",
        target=target, payload=payload, lease_seconds=1)
    action_id = prepared["action"]["action_id"]
    await service.start(
        workspace_id="workspace_a", action_id=action_id,
        lease_owner=prepared["lease_owner"], workload_principal="worker:browser")
    action = await store.get("external_actions", action_id)
    await store.compare_and_set(
        "external_actions", action_id, action["version"],
        {"lease_expires_at": "2000-01-01T00:00:00+00:00"})

    uncertain = await service.mark_expired_executing_uncertain(
        workspace_id="workspace_a", action_id=action_id)

    assert uncertain["receipt_status"] == "UNCERTAIN"
    assert uncertain["action"]["error_code"] == "reconciliation_required"
    assert (await store.get("approvals", approval_id))["status"] == "CONSUMED"
    reconciled = await service.reconcile(
        workspace_id="workspace_a", action_id=action_id,
        status="SUCCEEDED", evidence_id="provider-lookup-1",
        provider_effect_id="provider_effect_death")
    assert reconciled["receipt_status"] == "SUCCEEDED"


async def test_crash_after_t1_reclaims_same_claim_and_action():
    store = InMemoryDurableStore()
    _, _, run, step, approval_id, target, payload = await _authority(store, "reclaim")
    service = ConsequenceService(store)
    prepared = await service.prepare(
        workspace_id="workspace_a", approval_id=approval_id,
        run_id=run["run_id"], step_id=step["step_id"], connector_id="browser",
        action_kind="submit_application", idempotency_key="submit_reclaim",
        target=target, payload=payload, lease_seconds=1)
    action_id = prepared["action"]["action_id"]
    action = await store.get("external_actions", action_id)
    await store.compare_and_set(
        "external_actions", action_id, action["version"],
        {"lease_expires_at": "2000-01-01T00:00:00+00:00"})

    reclaimed = await service.reclaim_prepared(
        workspace_id="workspace_a", action_id=action_id)

    assert reclaimed["action"]["action_id"] == action_id
    assert reclaimed["action"]["lease_generation"] == 2
    assert (await store.get("approvals", approval_id))[
        "claimed_action_id"] == action_id


async def test_membership_revocation_between_t1_and_t2_voids_authority():
    store = InMemoryDurableStore()
    principal, _, run, step, approval_id, target, payload = await _authority(
        store, "revoked")
    service = ConsequenceService(store)
    prepared = await service.prepare(
        workspace_id="workspace_a", approval_id=approval_id,
        run_id=run["run_id"], step_id=step["step_id"], connector_id="browser",
        action_kind="submit_application", idempotency_key="submit_revoked",
        target=target, payload=payload)
    membership = await store.get("workspace_members", principal.membership_id)
    await store.compare_and_set(
        "workspace_members", principal.membership_id, membership["version"],
        {"status": "REVOKED"})

    started = await service.start(
        workspace_id="workspace_a", action_id=prepared["action"]["action_id"],
        lease_owner=prepared["lease_owner"], workload_principal="worker:browser")

    assert started["error_code"] == "stale_consequence_guard"
    assert (await store.get("approvals", approval_id))["status"] == "VOIDED"


async def test_plan_drift_between_t1_and_t2_voids_authority():
    store = InMemoryDurableStore()
    _, _, run, step, approval_id, target, payload = await _authority(
        store, "plan_drift")
    service = ConsequenceService(store)
    prepared = await service.prepare(
        workspace_id="workspace_a", approval_id=approval_id,
        run_id=run["run_id"], step_id=step["step_id"], connector_id="browser",
        action_kind="submit_application", idempotency_key="submit_plan_drift",
        target=target, payload=payload)
    current_run = await store.get("workflow_runs", run["run_id"])
    await store.compare_and_set(
        "workflow_runs", run["run_id"], current_run["version"],
        {"plan_hash": "sha256:changed-plan"})

    started = await service.start(
        workspace_id="workspace_a", action_id=prepared["action"]["action_id"],
        lease_owner=prepared["lease_owner"], workload_principal="worker:browser")

    assert started["error_code"] == "stale_consequence_guard"
    assert (await store.get("approvals", approval_id))["status"] == "VOIDED"


async def test_provider_budget_is_enforced_before_provider_authority():
    store = InMemoryDurableStore()
    _, _, run, step, approval_id, target, payload = await _authority(
        store, "provider_budget")
    current_run = await store.get("workflow_runs", run["run_id"])
    await store.compare_and_set(
        "workflow_runs", run["run_id"], current_run["version"],
        {"budgets": {**current_run["budgets"], "max_provider_calls": 0}})
    service = ConsequenceService(store)
    prepared = await service.prepare(
        workspace_id="workspace_a", approval_id=approval_id,
        run_id=run["run_id"], step_id=step["step_id"], connector_id="browser",
        action_kind="submit_application", idempotency_key="provider_budget",
        target=target, payload=payload)

    started = await service.start(
        workspace_id="workspace_a", action_id=prepared["action"]["action_id"],
        lease_owner=prepared["lease_owner"], workload_principal="worker:browser")

    assert started["error_code"] == "budget_exhausted"
    assert (await store.get("approvals", approval_id))["status"] == "VOIDED"
    action = await store.get("external_actions", prepared["action"]["action_id"])
    assert action["status"] == "FAILED"
    assert action.get("provider_started_at") is None
    assert action.get("provider_call_attempted_at") is None


async def test_approval_request_cannot_name_arbitrary_capability_or_policy():
    store = InMemoryDurableStore()
    principal, _, run, step, _, target, payload = await _authority(
        store, "closed_capability")
    before = await store.list("approvals", filters={})

    result = await PlatformApprovalService(store).request(
        workspace_id="workspace_a",
        requested_by_actor_id=principal.actor_id,
        run_id=run["run_id"], plan_hash=run["plan_hash"],
        step_id=step["step_id"], capability_id="external.shell",
        capability_version="1.0.0", action_kind="submit_application",
        target=target, payload=payload, policy_id="prompt_says_ok",
        policy_version="1", domain_ref="application_closed_capability",
        domain_version=1, connector_id="browser",
        connector_binding_version="browser-v1",
        client_request_id="approval_arbitrary_capability")

    assert result["error_code"] == "approval_contract_invalid"
    assert await store.list("approvals", filters={}) == before


async def test_same_signed_in_human_may_initiate_and_approve_with_atomic_wake():
    """The role split is an audit fact, not a mandatory two-person policy."""
    store = InMemoryDurableStore()
    member = await create_membership(
        actor_id="actor_owner", workspace_id="workspace_a",
        auth_subject="subject_owner", role=WorkspaceRole.OWNER,
        created_by="test", store=store)
    principal = ActorPrincipal(
        actor_id="actor_owner", workspace_id="workspace_a",
        role=WorkspaceRole.OWNER, role_grants=frozenset(),
        candidate_assignments=frozenset(), interview_assignments=frozenset(),
        session_auth_time=int(time.time()), membership_version=member["version"],
        membership_id=member["membership_id"])
    runtime = WorkflowRuntime(store)
    run = await runtime.create_run(
        workspace_id="workspace_a", journey_id="journey_simple_approval",
        run_kind=RunKind.EXTERNAL_CONSEQUENCE,
        workflow_kind="external_consequence:v1",
        idempotency_key="simple_approval", domain_ref="email:general",
        originating_actor_id=principal.actor_id)
    step = await runtime.create_step(
        run["run_id"], step_key="human_approval:send_email",
        idempotency_key="approval:send_email:one")
    service = PlatformApprovalService(store)
    requested = await service.request(
        workspace_id="workspace_a", requested_by_actor_id=principal.actor_id,
        run_id=run["run_id"], plan_hash=run["plan_hash"],
        step_id=step["step_id"], capability_id="external.send_email",
        capability_version="1.0.0", action_kind="send_email",
        target={"target": "email:general"},
        payload={"subject_hash": "sha256:message"},
        policy_id="exact_human_approval.v1", policy_version="1",
        domain_ref="email:general", domain_version=1,
        connector_id="alex_mail", connector_binding_version="connection-v1",
        client_request_id="simple-approval", origin_session_id="session_a",
        legacy_target="email:general", legacy_gate="send_email",
        presentation_details={"to": "person@example.com"})
    approval_id = requested["approval"]["approval_id"]

    decided = await service.decide(
        principal=principal, approval_id=approval_id, decision="GRANT")

    assert decided["status"] == "success"
    assert decided["approval"]["requested_by_actor_id"] == principal.actor_id
    assert decided["approval"]["decided_by_actor_id"] == principal.actor_id
    wake = await store.get("wake_deliveries", decided["wake_delivery_id"])
    assert wake["source_id"] == approval_id
    assert wake["workspace_id"] == principal.workspace_id
