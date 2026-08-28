"""Phase 1 acceptance tests for the domain-neutral durable runtime."""

from __future__ import annotations

import asyncio

import pytest

from services.durable_store import InMemoryDurableStore
from services.workflow_contracts import RunKind
from services.workflow_migrations import link_grant_application_run, migrate_run
from services.workflow_runtime import WorkflowRuntime

pytestmark = pytest.mark.asyncio


async def _grant_run(runtime: WorkflowRuntime, suffix: str = "one") -> dict:
    return await runtime.create_run(
        workspace_id="workspace_a", journey_id=f"journey_{suffix}",
        run_kind=RunKind.GRANT_APPLICATION,
        workflow_kind="grant_application:v1",
        idempotency_key=f"grant_{suffix}", domain_ref=f"application_{suffix}")


async def test_generic_run_needs_no_hiring_fixture_and_replays_from_events():
    store = InMemoryDurableStore()
    runtime = WorkflowRuntime(store)
    run = await _grant_run(runtime)
    assert run["runtime_status"] == "QUEUED"
    assert run["plan_hash"].startswith("sha256:")
    plans = await store.list(
        "workflow_plans", filters={"run_id": run["run_id"]}, limit=10)
    assert len(plans) == 1 and plans[0]["plan_hash"] == run["plan_hash"]
    assert run["provenance"] == {"provenance_class": "PRODUCTION"}
    assert "synthetic" not in run and "fixture_id" not in run
    wait = await runtime.create_wait(
        run["run_id"], wait_kind="FOUNDER_APPROVAL",
        correlation_key="approval_1")
    resolved = await runtime.resolve_wait(
        wait["wait_id"], event_id="approval_granted_1")
    delivery = await store.get(
        "wake_deliveries", resolved["wake_delivery_id"])
    assert delivery["run_id"] == run["run_id"]
    assert delivery["wait_id"] == wait["wait_id"]
    assert delivery["event_id"] == "approval_granted_1"
    assert delivery["status"] == "PENDING"
    verified = await runtime.verify_projection(run["run_id"])
    assert verified["status"] == "success"
    assert verified["rebuilt"]["next_event_sequence"] == 4


async def test_concurrent_event_appends_are_gap_free_and_commit_atomically():
    store = InMemoryDurableStore()
    runtime = WorkflowRuntime(store)
    run = await _grant_run(runtime, "events")
    results = await asyncio.gather(*(
        runtime.append_event(
            run["run_id"], event_kind="STEP_COMPLETED",
            idempotency_key=f"manual_{index}",
            safe_payload={"step_id": f"step_{index}"})
        for index in range(20)))
    assert all(result["status"] == "success" for result in results)
    events = await store.list(
        "run_events", filters={"run_id": run["run_id"]}, order_by="sequence",
        limit=1000)
    assert [row["sequence"] for row in events] == list(range(1, 22))
    current = await store.get("workflow_runs", run["run_id"])
    assert current["next_event_sequence"] == 22
    assert current.get("pending_event") is None


async def test_cancellation_generation_fences_an_inflight_step_and_wait():
    store = InMemoryDurableStore()
    runtime = WorkflowRuntime(store)
    run = await _grant_run(runtime, "cancel")
    step = await runtime.create_step(
        run["run_id"], step_key="draft", idempotency_key="draft_1")
    claimed = await runtime.claim_step(step["step_id"], lease_owner="worker_1")
    wait = await runtime.create_wait(
        run["run_id"], wait_kind="PROVIDER_EVENT", correlation_key="provider_1")
    cancelled = await runtime.cancel_run(
        run["run_id"], actor_id="founder", reason="Stop this work")
    assert cancelled["runtime_status"] == "CANCELLED"
    assert cancelled["cancellation_cleanup_complete"] is True
    completion = await runtime.complete_step(
        step["step_id"], lease_owner="worker_1",
        generation=claimed["attempt_generation"])
    assert completion["error_code"] == "run_fenced"
    follow_on = await runtime.create_step(
        run["run_id"], step_key="after_cancel", idempotency_key="blocked")
    assert follow_on["error_code"] == "run_fenced"
    appended = await runtime.append_event(
        run["run_id"], event_kind="STEP_COMPLETED",
        idempotency_key="after_cancel", safe_payload={"step_id": "blocked"})
    assert appended["error_code"] == "run_fenced"
    late = await runtime.resolve_wait(wait["wait_id"], event_id="late_event")
    assert late["error_code"] in {"wait_not_open", "run_fenced"}
    assert (await runtime.verify_projection(run["run_id"]))["status"] == "success"


async def test_unregistered_wait_kind_fails_closed():
    runtime = WorkflowRuntime(InMemoryDurableStore())
    run = await _grant_run(runtime, "bad_wait")
    result = await runtime.create_wait(
        run["run_id"], wait_kind="MODEL_INVENTED_WAIT",
        correlation_key="x")
    assert result["error_code"] == "wait_contract_invalid"


async def test_run_priority_and_step_budget_are_code_enforced():
    store = InMemoryDurableStore()
    runtime = WorkflowRuntime(store)
    run = await runtime.create_run(
        workspace_id="workspace_a", journey_id="journey_budget",
        run_kind=RunKind.GRANT_APPLICATION,
        workflow_kind="grant_application:v1",
        idempotency_key="grant_budget", domain_ref="application_budget",
        priority="HIGH", budgets={"max_steps": 1})
    first = await runtime.create_step(
        run["run_id"], step_key="interview", idempotency_key="first")
    second = await runtime.create_step(
        run["run_id"], step_key="draft", idempotency_key="second")

    claimed = await runtime.claim_step(
        first["step_id"], lease_owner="worker_budget")
    refused = await runtime.claim_step(
        second["step_id"], lease_owner="worker_budget_2")

    assert claimed["status"] == "success"
    assert refused["error_code"] == "budget_exhausted"
    current = await store.get("workflow_runs", run["run_id"])
    assert current["priority"] == "HIGH"
    assert current["budget_usage"]["steps_started"] == 1


async def test_parameterized_approval_step_is_closed_to_reviewed_template():
    runtime = WorkflowRuntime(InMemoryDurableStore())
    run = await runtime.create_run(
        workspace_id="workspace_a", journey_id="journey_consequence",
        run_kind=RunKind.EXTERNAL_CONSEQUENCE,
        workflow_kind="external_consequence:v1",
        idempotency_key="consequence_approval", domain_ref="email:general")

    approval = await runtime.create_step(
        run["run_id"], step_key="human_approval:send_email:message_123",
        idempotency_key="approval:email:message_123")
    invented = await runtime.create_step(
        run["run_id"], step_key="execute:send_email",
        idempotency_key="execute:email:message_123")
    malformed = await runtime.create_step(
        run["run_id"], step_key="human_approval:Send Email",
        idempotency_key="approval:invalid")

    assert approval["status"] == "success"
    assert approval["capability_id"] == "workflow.approval"
    assert invented["error_code"] == "step_capability_missing"
    assert malformed["error_code"] == "step_capability_missing"


@pytest.mark.parametrize("legacy,target", [
    ("DORMANT", "WAITING"), ("PAUSED", "PAUSED"),
    ("CANCELLING", "CANCELLING"), ("CANCELLED", "CANCELLED"),
    ("COMPLETED", "SUCCEEDED"), ("FAILED", "FAILED"),
])
async def test_runtime_status_migration_is_total(legacy: str, target: str):
    store = InMemoryDurableStore()
    await store.create("workflow_runs", f"run_{legacy.lower()}", {
        "run_id": f"run_{legacy.lower()}", "runtime_status": legacy,
        "synthetic": True, "synthetic_namespace": "synthetic_hiring_test",
        "fixture_id": "fixture_1", "version": 1})

    migrated = await migrate_run(store, f"run_{legacy.lower()}")

    assert migrated["run"]["runtime_status"] == target
    assert migrated["run"]["runtime_status_schema_version"] == 2
    assert migrated["run"]["provenance"] == {
        "provenance_class": "SYNTHETIC",
        "namespace": "synthetic_hiring_test",
        "fixture_set_id": "fixture_1",
    }
    assert "synthetic" not in migrated["run"]
    assert (await migrate_run(store, f"run_{legacy.lower()}"))["duplicate"] is True


async def test_active_migration_fences_lease_and_derives_waiting():
    store = InMemoryDurableStore()
    await store.create("workflow_runs", "run_active", {
        "run_id": "run_active", "runtime_status": "ACTIVE", "version": 1})
    await store.create("waits", "wait_active", {
        "wait_id": "wait_active", "run_id": "run_active",
        "status": "OPEN", "version": 1})
    await store.create("step_attempts", "attempt_active", {
        "attempt_id": "attempt_active", "run_id": "run_active",
        "status": "RUNNING", "lease_owner": "old_worker", "version": 1})

    migrated = await migrate_run(store, "run_active")

    assert migrated["run"]["runtime_status"] == "WAITING"
    assert migrated["fenced_attempt_count"] == 1
    attempt = await store.get("step_attempts", "attempt_active")
    assert attempt["status"] == "FENCED"
    assert attempt["lease_owner"] is None


async def test_legacy_application_links_one_deterministic_run_and_plan():
    store = InMemoryDurableStore()
    await store.create("applications", "application_legacy", {
        "founder_id": "workspace_a", "state": "INTERVIEWING",
        "created_at": "2026-01-01T00:00:00+00:00", "version": 1})

    first = await link_grant_application_run(store, "application_legacy")
    duplicate = await link_grant_application_run(store, "application_legacy")

    assert first["application"]["workflow_run_id"] == first["run"]["run_id"]
    assert first["application"]["workflow_plan_hash"] == first["run"]["plan_hash"]
    assert duplicate["duplicate"] is True
    runs = await store.list(
        "workflow_runs", filters={"domain_ref": "application_legacy"})
    assert len(runs) == 1
