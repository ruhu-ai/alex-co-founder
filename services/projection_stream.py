"""Durable workspace projection events for replayable product SSE.

The stream is an observation projection only. A failed publish never rolls
back authoritative workflow/domain state; reconnecting clients recover through
resource snapshots. Events contain bounded, content-free display metadata.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from services.durable_store import AtomicMutation, DurableStore, production_store
from services.workflow_contracts import stable_id, utc_now

PROJECTION_TYPES = frozenset({
    "command", "run", "wait", "approval", "action", "inbox", "message",
    "artifact", "connection", "browser",
})
MAX_PAYLOAD_BYTES = 8192
MAX_REPLAY_BATCH = 100
RECONCILE_SECONDS = 25.0

# Projection writes and SSE readers usually share a Cloud Run instance. Wake
# those readers directly instead of querying Firestore every two seconds. A
# bounded reconciliation timeout remains necessary because another instance
# may commit the event; it is a recovery path, not the primary delivery loop.
_workspace_waiters: dict[str, set[asyncio.Event]] = {}
_workspace_revisions: dict[str, int] = {}


def notify_workspace(workspace_id: str) -> None:
    """Wake local projection readers after an authoritative commit."""
    _workspace_revisions[workspace_id] = _workspace_revisions.get(workspace_id, 0) + 1
    for waiter in tuple(_workspace_waiters.get(workspace_id, ())):
        waiter.set()


def workspace_revision(workspace_id: str) -> int:
    """Return the process-local publish generation for race-free waiting."""
    return _workspace_revisions.get(workspace_id, 0)


async def wait_for_workspace_event(
        workspace_id: str, *, after_revision: int | None = None,
        timeout: float = RECONCILE_SECONDS) -> bool:
    """Wait for a local publish; timeout permits cross-instance reconciliation."""
    expected = (workspace_revision(workspace_id)
                if after_revision is None else int(after_revision))
    if workspace_revision(workspace_id) != expected:
        return True
    waiter = asyncio.Event()
    waiters = _workspace_waiters.setdefault(workspace_id, set())
    waiters.add(waiter)
    try:
        # Close the replay -> subscribe race: a publish between the first check
        # and registration changed the generation even if no future set occurs.
        if workspace_revision(workspace_id) != expected:
            return True
        await asyncio.wait_for(waiter.wait(), timeout=max(0.01, float(timeout)))
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        waiters.discard(waiter)
        if not waiters:
            _workspace_waiters.pop(workspace_id, None)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True,
            "error_code": code, "message": message}


class ProjectionEventService:
    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    async def publish(self, *, workspace_id: str, projection_type: str,
                      aggregate_id: str, aggregate_version: int,
                      safe_payload: dict[str, Any], run_id: str = "",
                      idempotency_key: str) -> dict[str, Any]:
        if (not workspace_id or projection_type not in PROJECTION_TYPES
                or not aggregate_id or aggregate_version < 0
                or not idempotency_key):
            return _error("projection_contract_invalid",
                          "Projection event identity is invalid.")
        try:
            encoded = json.dumps(
                safe_payload, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            return _error("projection_payload_invalid",
                          "Projection payload is not JSON-safe.")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            return _error("projection_payload_too_large",
                          "Projection payload exceeds the safe bound.")
        event_id = stable_id(
            "projection", workspace_id, projection_type, idempotency_key)
        existing = await self.store.get("projection_events", event_id)
        if existing:
            if (existing.get("aggregate_id") != aggregate_id
                    or existing.get("aggregate_version") != aggregate_version):
                return _error("idempotency_conflict",
                              "Projection key names another update.")
            return {"status": "success", "duplicate": True, **existing}
        stream_id = stable_id("stream", workspace_id)
        for _ in range(8):
            stream = await self.store.get("projection_streams", stream_id)
            current_sequence = int((stream or {}).get("last_sequence") or 0)
            sequence = current_sequence + 1
            now = utc_now()
            row = {
                "schema_version": 1, "event_id": event_id,
                "workspace_id": workspace_id, "sequence": sequence,
                "projection_type": projection_type,
                "aggregate_id": aggregate_id,
                "aggregate_version": aggregate_version,
                "run_id": run_id or None,
                "safe_payload": dict(safe_payload),
                "created_at": now, "version": 1,
            }
            stream_mutation = (
                AtomicMutation(
                    "projection_streams", stream_id, int(stream["version"]),
                    updates={"last_sequence": sequence, "updated_at": now})
                if stream else
                AtomicMutation(
                    "projection_streams", stream_id, None, record={
                        "schema_version": 1, "stream_id": stream_id,
                        "workspace_id": workspace_id,
                        "last_sequence": sequence,
                        "created_at": now, "updated_at": now,
                        "version": 1,
                    }))
            committed = await self.store.atomic_compare_and_set((
                stream_mutation,
                AtomicMutation(
                    "projection_events", event_id, None, record=row),
            ))
            if committed:
                notify_workspace(workspace_id)
                return {"status": "success", "duplicate": False,
                        **committed[("projection_events", event_id)]}
            existing = await self.store.get("projection_events", event_id)
            if existing:
                return {"status": "success", "duplicate": True, **existing}
        return _error("concurrency_conflict",
                      "Projection sequence changed concurrently.")

    async def resolve_cursor(self, *, workspace_id: str,
                             event_id: str) -> dict[str, Any]:
        if not event_id:
            return {"status": "success", "sequence": 0}
        event = await self.store.get("projection_events", event_id)
        if not event:
            return _error("snapshot_required",
                          "Replay cursor is outside the retained window.")
        if event.get("workspace_id") != workspace_id:
            return _error("foreign_cursor", "Replay cursor is not authorized.")
        return {"status": "success", "sequence": int(event["sequence"])}

    async def replay(self, *, workspace_id: str, after_sequence: int,
                     limit: int = MAX_REPLAY_BATCH) -> dict[str, Any]:
        bounded = max(1, min(int(limit), MAX_REPLAY_BATCH))
        rows = await self.store.list(
            "projection_events", filters={"workspace_id": workspace_id},
            order_by="sequence", limit=bounded + 1,
            start_after=("sequence", int(after_sequence)))
        overflow = len(rows) > bounded
        return {"status": "success", "events": rows[:bounded],
                "snapshot_required": overflow,
                "next_sequence": (int(rows[min(len(rows), bounded) - 1]["sequence"])
                                  if rows else int(after_sequence))}


def sse_event(row: dict[str, Any]) -> str:
    payload = {
        "event_id": row["event_id"],
        "workspace_id": row["workspace_id"],
        "run_id": row.get("run_id"),
        "projection_type": row["projection_type"],
        "aggregate_id": row["aggregate_id"],
        "version": int(row["aggregate_version"]),
        "sequence": int(row["sequence"]),
        "payload": row.get("safe_payload") or {},
    }
    return (f"event: projection\n"
            f"id: {row['event_id']}\n"
            f"data: {json.dumps(payload, separators=(',', ':'))}\n\n")


def snapshot_required_event(sequence: int) -> str:
    return ("event: snapshot_required\n"
            f"data: {{\"after_sequence\":{int(sequence)}}}\n\n")


async def publish_best_effort(*, store: DurableStore | None = None,
                              **event: Any) -> None:
    """Publish an observation after authority commits; never undo authority."""
    try:
        result = await ProjectionEventService(store).publish(**event)
        if result.get("error"):
            logging.getLogger(__name__).warning(
                "projection publish refused type=%s aggregate=%s code=%s",
                event.get("projection_type"), event.get("aggregate_id"),
                result.get("error_code"))
    except Exception:  # noqa: BLE001 - projection repair is asynchronous
        logging.getLogger(__name__).exception(
            "projection publish failed type=%s aggregate=%s",
            event.get("projection_type"), event.get("aggregate_id"))
