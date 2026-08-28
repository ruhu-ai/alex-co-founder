from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services import task_queue
from services.durable_store import InMemoryDurableStore
from services.workflow_contracts import RunKind
from services.workflow_runtime import WorkflowRuntime
from services.workflow_timer_service import (
    deliver_timer_checkpoint,
    schedule_wait_timer,
)

pytestmark = pytest.mark.asyncio


async def _timer_wait(store: InMemoryDurableStore, due_at: str):
    runtime = WorkflowRuntime(store)
    run = await runtime.create_run(
        workspace_id="workspace_a", journey_id="journey_timer",
        run_kind=RunKind.GRANT_APPLICATION,
        workflow_kind="grant_application:v1", idempotency_key=due_at,
        domain_ref="application_timer")
    return await runtime.create_wait(
        run["run_id"], wait_kind="TIMER",
        correlation_key=f"timer:{due_at}", wake_after=due_at,
        origin_session_id="session_a")


async def test_due_timer_resolves_wait_and_creates_one_wake(monkeypatch):
    store = InMemoryDurableStore()
    due_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    wait = await _timer_wait(store, due_at)
    calls = []

    def enqueue(path, payload, dedupe_key, **kwargs):
        calls.append((path, payload, dedupe_key, kwargs))
        return {"status": "success"}

    monkeypatch.setattr(task_queue, "enqueue", enqueue)
    scheduled = await schedule_wait_timer(
        wait["wait_id"], expected_generation=1, store=store,
        force_local=True)
    delivered = await deliver_timer_checkpoint(
        workspace_id="workspace_a", wait_id=wait["wait_id"],
        expected_generation=1, checkpoint_generation=1, store=store)
    duplicate = await deliver_timer_checkpoint(
        workspace_id="workspace_a", wait_id=wait["wait_id"],
        expected_generation=1, checkpoint_generation=1, store=store)

    assert scheduled["timer_status"] == "ENQUEUED"
    assert len(calls) == 1 and calls[0][0] == "/tasks/workflow_timer_checkpoint"
    assert calls[0][3]["queue_name"] == "co-founder-timers"
    assert delivered["timer_status"] == "DELIVERED"
    assert duplicate["duplicate"] is True
    current = await store.get("waits", wait["wait_id"])
    assert current["status"] == "RESOLVED"
    wakes = await store.list(
        "wake_deliveries", filters={"wait_id": wait["wait_id"]})
    assert len(wakes) == 1


async def test_far_future_timer_rolls_generation_without_resolving(monkeypatch):
    store = InMemoryDurableStore()
    due_at = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    wait = await _timer_wait(store, due_at)
    calls = []

    def enqueue(path, payload, dedupe_key, **kwargs):
        calls.append((payload, dedupe_key, kwargs))
        return {"status": "success"}

    monkeypatch.setattr(task_queue, "enqueue", enqueue)
    await schedule_wait_timer(
        wait["wait_id"], expected_generation=1, store=store,
        force_local=True)
    rolled = await deliver_timer_checkpoint(
        workspace_id="workspace_a", wait_id=wait["wait_id"],
        expected_generation=1, checkpoint_generation=1, store=store)

    current = await store.get("waits", wait["wait_id"])
    assert rolled["timer_status"] == "ENQUEUED"
    assert current["status"] == "OPEN"
    assert current["timer_checkpoint_generation"] == 2
    assert len(calls) == 2
    assert calls[1][0]["checkpoint_generation"] == 2


async def test_invalid_timer_due_time_is_rejected_before_wait_creation():
    store = InMemoryDurableStore()
    runtime = WorkflowRuntime(store)
    run = await runtime.create_run(
        workspace_id="workspace_a", journey_id="journey_bad_timer",
        run_kind=RunKind.GRANT_APPLICATION,
        workflow_kind="grant_application:v1", idempotency_key="bad_timer",
        domain_ref="application_bad_timer")

    result = await runtime.create_wait(
        run["run_id"], wait_kind="TIMER", correlation_key="bad",
        wake_after="tomorrow afternoon")

    assert result["error_code"] == "wait_contract_invalid"
    assert await store.list("waits", filters={"run_id": run["run_id"]}) == []
