"""Durable, generation-fenced timer checkpoints for workflow waits.

Cloud Tasks is delivery infrastructure, not timer authority.  The wait row
contains the due time and checkpoint generation; a lost or duplicate task can
therefore be recreated without changing the workflow outcome.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from services import task_queue
from services.durable_store import DurableStore, production_store
from services.workflow_contracts import stable_id, utc_now

CHECKPOINT_HORIZON_DAYS = 28
TIMER_QUEUE = "co-founder-timers"


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc))


def _checkpoint_at(due_at: str) -> str:
    due = _parse(due_at)
    horizon = datetime.now(timezone.utc) + timedelta(
        days=CHECKPOINT_HORIZON_DAYS)
    return min(due, horizon).isoformat()


async def schedule_wait_timer(
        wait_id: str, *, expected_generation: int,
        store: DurableStore | None = None,
        force_local: bool = False) -> dict[str, Any]:
    """Schedule the next bounded checkpoint and durably record its receipt."""
    store = store or production_store()
    wait = await store.get("waits", wait_id)
    if not wait:
        return _error("wait_not_found", "Workflow wait does not exist.")
    if (wait.get("status") != "OPEN"
            or int(wait.get("generation") or 0) != expected_generation):
        return {"status": "success", "duplicate": True,
                "timer_status": "NOT_REQUIRED"}
    due_at = str(wait.get("wake_after") or "")
    if not due_at:
        return _error("timer_not_configured", "Workflow wait has no timer.")
    checkpoint_generation = int(
        wait.get("timer_checkpoint_generation") or 1)
    checkpoint_at = _checkpoint_at(due_at)
    payload = {
        "workspace_id": wait["workspace_id"], "wait_id": wait_id,
        "expected_generation": expected_generation,
        "checkpoint_generation": checkpoint_generation,
    }
    if not (os.environ.get("K_SERVICE") or force_local):
        return {"status": "success", "deferred": True,
                "timer_status": wait.get("timer_status") or "PENDING",
                "checkpoint_at": checkpoint_at}
    result = await asyncio.to_thread(
        task_queue.enqueue, "/tasks/workflow_timer_checkpoint", payload,
        stable_id("timer_task", wait_id, str(expected_generation),
                  str(checkpoint_generation)),
        queue_name=TIMER_QUEUE, schedule_at=checkpoint_at)
    latest = await store.get("waits", wait_id)
    if (not latest or latest.get("status") != "OPEN"
            or int(latest.get("generation") or 0) != expected_generation):
        return {"status": "success", "duplicate": True,
                "timer_status": "NOT_REQUIRED"}
    updates = {
        "timer_status": "ENQUEUED" if not result.get("error") else "FAILED",
        "timer_checkpoint_at": checkpoint_at,
        "timer_last_error": (None if not result.get("error")
                             else str(result.get("message") or "enqueue failed")[:200]),
        "updated_at": utc_now(),
    }
    committed = await store.compare_and_set(
        "waits", wait_id, int(latest["version"]), updates)
    if not committed:
        return _error("concurrency_conflict", "Timer state changed concurrently.")
    return {"status": "success" if not result.get("error") else "error",
            "error": bool(result.get("error")),
            "error_code": "timer_enqueue_failed" if result.get("error") else None,
            "timer_status": updates["timer_status"],
            "checkpoint_at": checkpoint_at}


async def deliver_timer_checkpoint(
        *, workspace_id: str, wait_id: str, expected_generation: int,
        checkpoint_generation: int,
        store: DurableStore | None = None) -> dict[str, Any]:
    """Resolve a due wait once, or roll a far-future wait to another checkpoint."""
    store = store or production_store()
    wait = await store.get("waits", wait_id)
    if not wait or wait.get("workspace_id") != workspace_id:
        return _error("wait_not_found", "Workflow wait does not exist.")
    if (wait.get("status") != "OPEN"
            or int(wait.get("generation") or 0) != expected_generation):
        return {"status": "success", "duplicate": True,
                "timer_status": "NOT_REQUIRED"}
    if int(wait.get("timer_checkpoint_generation") or 1) != checkpoint_generation:
        return {"status": "success", "duplicate": True,
                "timer_status": "STALE_CHECKPOINT"}
    due_at = str(wait.get("wake_after") or "")
    if not due_at:
        return _error("timer_not_configured", "Workflow wait has no timer.")
    if datetime.now(timezone.utc) < _parse(due_at):
        advanced = await store.compare_and_set(
            "waits", wait_id, int(wait["version"]), {
                "timer_status": "PENDING",
                "timer_checkpoint_generation": checkpoint_generation + 1,
                "updated_at": utc_now(),
            })
        if not advanced:
            return _error("concurrency_conflict", "Timer changed concurrently.")
        return await schedule_wait_timer(
            wait_id, expected_generation=expected_generation, store=store,
            force_local=True)
    # Import lazily so the runtime can call the scheduler without a cycle.
    from services.workflow_runtime import WorkflowRuntime

    event_id = stable_id(
        "timer_event", wait_id, str(expected_generation), due_at)
    resolved = await WorkflowRuntime(store).resolve_wait(
        wait_id, event_id=event_id,
        expected_generation=expected_generation)
    if resolved.get("error"):
        return resolved
    latest = await store.get("waits", wait_id)
    if latest:
        await store.compare_and_set(
            "waits", wait_id, int(latest["version"]), {
                "timer_status": "DELIVERED", "timer_delivered_at": utc_now(),
                "updated_at": utc_now(),
            })
    return {**resolved, "timer_status": "DELIVERED"}


async def recover_timer(
        *, workspace_id: str, wait_id: str,
        store: DurableStore | None = None) -> dict[str, Any]:
    """Operator/sweeper repair for a visible PENDING/FAILED timer receipt."""
    store = store or production_store()
    wait = await store.get("waits", wait_id)
    if not wait or wait.get("workspace_id") != workspace_id:
        return _error("wait_not_found", "Workflow wait does not exist.")
    return await schedule_wait_timer(
        wait_id, expected_generation=int(wait.get("generation") or 0),
        store=store, force_local=bool(os.environ.get("K_SERVICE")))
