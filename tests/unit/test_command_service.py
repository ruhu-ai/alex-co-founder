from __future__ import annotations

import pytest

from services import firestore
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.command_dispatcher import CommandDispatcher
from services.command_service import CommandService, transport_status
from services.durable_store import AtomicMutation, InMemoryDurableStore
from services.investor_outreach_service import InvestorOutreachService
from services.workflow_contracts import RunKind, stable_id
from services.workflow_runtime import WorkflowRuntime

pytestmark = pytest.mark.asyncio


def _principal(workspace: str = "workspace_a") -> ActorPrincipal:
    return ActorPrincipal(
        actor_id="actor_a", workspace_id=workspace, role=WorkspaceRole.FOUNDER, session_auth_time=1,
        membership_version=1)


async def test_accept_is_atomic_and_duplicate_inflight_returns_202():
    store = InMemoryDurableStore()
    service = CommandService(store)
    first = await service.accept(
        principal=_principal(), client_request_id="request_1",
        command_type="discovery.start", request={"context": "fintech"},
        origin_session_id="session_a")
    duplicate = await service.accept(
        principal=_principal(), client_request_id="request_1",
        command_type="discovery.start", request={"context": "fintech"},
        origin_session_id="session_a")

    assert transport_status(first) == transport_status(duplicate) == 202
    assert "http_status" not in first and "http_status" not in duplicate
    assert duplicate["duplicate"] is True
    outbox = await store.list(
        "command_outbox", filters={"command_id": first["command_id"]}, limit=10)
    assert len(outbox) == 1


async def test_changed_duplicate_conflicts_and_workspace_point_read_is_hidden():
    service = CommandService(InMemoryDurableStore())
    first = await service.accept(
        principal=_principal(), client_request_id="request_2",
        command_type="run.cancel", request={"run_id": "run_a"})
    conflict = await service.accept(
        principal=_principal(), client_request_id="request_2",
        command_type="run.cancel", request={"run_id": "run_b"})
    hidden = await service.get(
        workspace_id="workspace_b", command_id=first["command_id"])
    assert conflict["error_code"] == "idempotency_conflict"
    assert hidden["error_code"] == "command_not_found"


async def test_terminal_command_requires_evidence_and_replays_result():
    store = InMemoryDurableStore()
    service = CommandService(store)
    accepted = await service.accept(
        principal=_principal(), client_request_id="request_3",
        command_type="run.pause", request={"run_id": "run_a"})
    missing = await service.transition(
        workspace_id="workspace_a", command_id=accepted["command_id"],
        expected_version=accepted["version"], status="COMPLETED")
    assert missing["error_code"] == "command_result_missing"
    completed = await service.transition(
        workspace_id="workspace_a", command_id=accepted["command_id"],
        expected_version=accepted["version"], status="COMPLETED",
        result_ref={"run_id": "run_a", "runtime_status": "PAUSED"})
    duplicate = await service.accept(
        principal=_principal(), client_request_id="request_3",
        command_type="run.pause", request={"run_id": "run_a"})
    assert completed["status"] == "COMPLETED"
    assert duplicate["result_ref"]["runtime_status"] == "PAUSED"


async def test_command_run_domain_acceptance_and_dispatch_are_recoverable():
    store = InMemoryDurableStore()
    principal = _principal()
    outreach = InvestorOutreachService(store)
    prepared = await outreach.prepare_start_creation(
        principal=principal, objective="Find seed investors",
        origin_session_id="session_a", client_request_id="request_atomic_1")
    commands = CommandService(store)
    accepted = await commands.accept(
        principal=principal, client_request_id="request_atomic_1",
        command_type="investor_outreach.start",
        request={"objective": "Find seed investors", "session_id": "session_a"},
        origin_session_id="session_a",
        run_id=prepared["run"]["run_id"],
        dispatch_ref=prepared["outreach"]["outreach_id"],
        authority_mutations=prepared["mutations"])

    # Simulate death immediately after the Firestore commit: every authority
    # root exists but no route-local scaffolding or queue call has happened.
    assert await store.get("workflow_runs", accepted["run_id"])
    assert await store.get("investor_outreach", accepted["dispatch_ref"])
    assert await store.list(
        "workflow_steps", filters={"run_id": accepted["run_id"]}) == []

    enqueued: list[tuple] = []

    def enqueue(*args, **kwargs):
        enqueued.append((args, kwargs))
        return {"status": "success", "task_name": "task-1"}

    outbox_id = stable_id("cmdoutbox", accepted["command_id"], "dispatch")
    dispatched = await CommandDispatcher(
        store, enqueue_fn=enqueue).dispatch(outbox_id)
    assert dispatched["command"]["status"] == "DISPATCHED"
    assert len(enqueued) == 1
    assert len(await store.list(
        "workflow_steps", filters={"run_id": accepted["run_id"]},
        limit=20)) == 7
    replay = await CommandDispatcher(store, enqueue_fn=enqueue).dispatch(outbox_id)
    assert replay["duplicate"] is True
    assert len(enqueued) == 1


async def test_authority_conflict_rolls_back_command_and_outbox():
    store = InMemoryDurableStore()
    await store.put("investor_outreach", "occupied", {
        "workspace_id": "workspace_a", "version": 1})
    service = CommandService(store)
    result = await service.accept(
        principal=_principal(), client_request_id="request_atomic_2",
        command_type="investor_outreach.start", request={"objective": "x"},
        authority_mutations=(AtomicMutation(
            "investor_outreach", "occupied", None,
            record={"workspace_id": "workspace_a", "version": 1}),))
    assert result["error_code"] == "concurrency_conflict"
    assert await store.list("command_receipts", filters={}, limit=10) == []
    assert await store.list("command_outbox", filters={}, limit=10) == []


async def test_discovery_command_commit_recovers_queue_handoff():
    store = InMemoryDurableStore()
    principal = _principal()
    discovery_id = firestore.discovery_receipt_id(
        principal.workspace_id, "request_discovery_1")
    journey_id = stable_id(
        "journey", principal.workspace_id, "discovery", discovery_id)
    run = await WorkflowRuntime(store).prepare_run_creation(
        workspace_id=principal.workspace_id, journey_id=journey_id,
        run_kind=RunKind.OPPORTUNITY_DISCOVERY,
        workflow_kind="opportunity_discovery:v1",
        idempotency_key=discovery_id, domain_ref=discovery_id,
        originating_actor_id=principal.actor_id,
        origin_session_id="session_a")
    domain = firestore.discovery_receipt_record(
        "request_discovery_1", principal.workspace_id, "hash_a",
        origin_session_id="session_a", display_query="funding",
        workflow_run_id=run["run_id"],
        workflow_plan_hash=run["run_record"]["plan_hash"],
        workflow_plan_version=run["run_record"]["plan_version"])
    accepted = await CommandService(store).accept(
        principal=principal, client_request_id="request_discovery_1",
        command_type="opportunity_discovery.start",
        request={"session_id": "session_a", "context": "funding"},
        run_id=run["run_id"], dispatch_ref=discovery_id,
        authority_mutations=(*run["mutations"], AtomicMutation(
            "discovery_requests", discovery_id, None, record=domain)))
    enqueued = []

    def enqueue(*args, **kwargs):
        enqueued.append((args, kwargs))
        return {"status": "success"}

    result = await CommandDispatcher(store, enqueue_fn=enqueue).dispatch(
        stable_id("cmdoutbox", accepted["command_id"], "dispatch"))
    assert result["command"]["status"] == "DISPATCHED"
    assert enqueued[0][0][0] == "/tasks/discover"
    assert enqueued[0][0][1]["discovery_request_id"] == discovery_id
