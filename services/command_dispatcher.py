"""Recoverable, closed dispatch for accepted public commands.

The receipt/outbox and any new authority commit together. Queue publication is
necessarily outside Firestore, so this dispatcher uses a deterministic Cloud
Tasks name and marks the outbox delivered only after enqueue succeeds. A crash
on either side is therefore recoverable without exactly-once delivery.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from services.command_service import CommandService
from services.durable_store import DurableStore, production_store
from services.investor_outreach_service import InvestorOutreachService
from services.workflow_contracts import utc_now

EnqueueFn = Callable[..., dict[str, Any]]


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "retryable": retryable}


class CommandDispatcher:
    """Dispatch only explicitly registered command types."""

    def __init__(self, store: DurableStore | None = None, *,
                 enqueue_fn: EnqueueFn | None = None):
        self.store = store or production_store()
        if enqueue_fn is None:
            from services import task_queue

            enqueue_fn = task_queue.enqueue
        self.enqueue_fn = enqueue_fn

    async def dispatch(self, outbox_id: str) -> dict[str, Any]:
        outbox = await self.store.get("command_outbox", outbox_id)
        if not outbox:
            return _error("command_outbox_not_found", "Dispatch intent does not exist.")
        if outbox.get("status") == "DELIVERED":
            return {"status": "success", "duplicate": True, "outbox": outbox}
        if outbox.get("status") != "PENDING":
            return _error("command_outbox_state_invalid",
                          "Dispatch intent is not pending.")
        command_id = str(outbox.get("command_id") or "")
        workspace_id = str(outbox.get("workspace_id") or "")
        receipt = await self.store.get("command_receipts", command_id)
        if (not receipt or receipt.get("workspace_id") != workspace_id
                or receipt.get("status") != "ACCEPTED"):
            return _error("command_receipt_not_dispatchable",
                          "Command receipt is not dispatchable.")
        command_type = str(outbox.get("command_type") or "")
        if command_type not in {
                "investor_outreach.start", "opportunity_discovery.start"}:
            return _error("command_dispatch_unregistered",
                          "Command type has no recovery dispatcher.")
        dispatch_ref = str(outbox.get("dispatch_ref") or "")
        if command_type == "investor_outreach.start":
            recovered = await InvestorOutreachService(self.store).recover_start(
                workspace_id=workspace_id, outreach_id=dispatch_ref)
            if recovered.get("error"):
                return recovered
            path = "/tasks/investor_outreach_prepare"
            payload = {"workspace_id": workspace_id,
                       "outreach_id": dispatch_ref, "command_id": command_id}
            dedupe_key = f"investor-prepare:{dispatch_ref}"
        else:
            request_row = await self.store.get(
                "discovery_requests", dispatch_ref)
            if (not request_row
                    or request_row.get("workspace_id") != workspace_id):
                return _error("discovery_request_not_found",
                              "Discovery request does not exist.")
            path = "/tasks/discover"
            payload = {"discovery_request_id": dispatch_ref,
                       "founder_id": workspace_id}
            dedupe_key = (f"discover:{workspace_id}:"
                          f"{request_row.get('request_id')}")
        queued = await asyncio.to_thread(
            self.enqueue_fn, path, payload, dedupe_key,
            queue_name="co-founder-discovery-ingestion")
        if queued.get("error"):
            latest = await self.store.get("command_outbox", outbox_id)
            if latest and latest.get("status") == "PENDING":
                await self.store.compare_and_set(
                    "command_outbox", outbox_id, int(latest["version"]), {
                        "attempt": int(latest.get("attempt") or 0) + 1,
                        "last_error_code": str(
                            queued.get("error_code") or "queue_unavailable")[:120],
                        "updated_at": utc_now(),
                    })
            return _error("command_dispatch_failed",
                          "Command dispatch remains pending.", retryable=True)
        transitioned = await CommandService(self.store).transition(
            workspace_id=workspace_id, command_id=command_id,
            expected_version=int(receipt["version"]), status="DISPATCHED",
            run_id=str(receipt.get("run_id") or ""))
        if transitioned.get("error"):
            latest = await self.store.get("command_receipts", command_id)
            if latest and latest.get("status") in {
                    "DISPATCHED", "COMPLETED", "FAILED", "REJECTED", "CANCELLED"}:
                return {"status": "success", "duplicate": True,
                        "command": latest, "queue": queued}
            return transitioned
        return {"status": "success", "duplicate": False,
                "command": transitioned, "queue": queued}

    async def dispatch_pending(self, *, limit: int = 100) -> dict[str, Any]:
        """Bounded scheduler recovery sweep; never executes provider effects."""
        rows = []
        per_type = max(1, min(limit, 100))
        for command_type in (
                "investor_outreach.start", "opportunity_discovery.start"):
            rows.extend(await self.store.list(
                "command_outbox", filters={
                    "status": "PENDING", "command_type": command_type},
                order_by="created_at", limit=per_type))
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        rows = rows[:max(1, min(limit, 100))]
        results = []
        for row in rows:
            results.append(await self.dispatch(
                str(row.get("outbox_id") or row.get("id") or "")))
        return {"status": "success", "scanned": len(rows),
                "dispatched": sum(not item.get("error") for item in results),
                "retryable_failures": sum(
                    bool(item.get("retryable")) for item in results)}
