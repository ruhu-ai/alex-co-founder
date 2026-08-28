"""Workspace-scoped idempotent command receipts for public mutations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from services.actor_identity import ActorPrincipal
from services.canonical import canonical_hash
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.workflow_contracts import stable_id, utc_now

TERMINAL = frozenset({"COMPLETED", "FAILED", "REJECTED", "CANCELLED"})
STATUSES = TERMINAL | frozenset({"RECEIVED", "ACCEPTED", "DISPATCHED"})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True,
            "error_code": code, "message": message}


def transport_status(result: dict[str, Any]) -> int:
    """Map the model/service envelope to HTTP only at a route boundary."""
    if result.get("error_code") == "idempotency_conflict":
        return 409
    if result.get("error"):
        return 400
    return 202 if result.get("status") in {
        "accepted", "RECEIVED", "ACCEPTED", "DISPATCHED"} else 200


class CommandService:
    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    async def accept(self, *, principal: ActorPrincipal,
                     client_request_id: str, command_type: str,
                     request: dict[str, Any], origin_session_id: str = "",
                     run_id: str = "", dispatch_ref: str = "",
                     authority_mutations: Sequence[AtomicMutation] = ()
                     ) -> dict[str, Any]:
        """Atomically persist command acceptance and its dispatch intent.

        A command that creates authoritative state supplies prevalidated,
        create-only ``authority_mutations``. This makes the receipt, outbox,
        run/plan/event, and domain root one Firestore commit instead of relying
        on an exactly-once route process. Callers cannot use this hook to
        update existing authority or either command collection.
        """
        if (not client_request_id or len(client_request_id) > 160
                or not command_type or len(command_type) > 120):
            return _error("command_contract_invalid", "Command identity is invalid.")
        request_hash = canonical_hash(
            request, domain=f"command:{command_type[:80]}")
        command_id = stable_id(
            "command", principal.workspace_id, client_request_id)
        outbox_id = stable_id("cmdoutbox", command_id, "dispatch")
        existing = await self.store.get("command_receipts", command_id)
        if existing:
            return self._duplicate(existing, request_hash)
        mutations = tuple(authority_mutations)
        if (len(mutations) > 90
                or any(item.expected_version is not None or item.check_only
                       or item.collection in {
                           "command_receipts", "command_outbox"}
                       or item.record.get("workspace_id") != principal.workspace_id
                       for item in mutations)):
            return _error("command_authority_invalid",
                          "Command authority creation is invalid.")
        now = utc_now()
        receipt = {
            "schema_version": 1, "command_id": command_id,
            "client_request_id": client_request_id,
            "command_type": command_type,
            "workspace_id": principal.workspace_id,
            "actor_id": principal.actor_id,
            "origin_session_id": origin_session_id or None,
            "normalized_request_hash": request_hash,
            "status": "ACCEPTED", "run_id": run_id or None,
            "dispatch_ref": dispatch_ref or None,
            "result_ref": None, "error_code": None,
            "created_at": now, "accepted_at": now,
            "terminal_at": None, "version": 1,
        }
        outbox = {
            "schema_version": 1, "outbox_id": outbox_id,
            "workspace_id": principal.workspace_id,
            "command_id": command_id, "kind": "COMMAND_DISPATCH",
            "command_type": command_type,
            "run_id": run_id or None, "dispatch_ref": dispatch_ref or None,
            "status": "PENDING", "attempt": 0,
            "created_at": now, "updated_at": now, "version": 1,
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation("command_receipts", command_id, None, record=receipt),
            AtomicMutation("command_outbox", outbox_id, None, record=outbox),
            *mutations,
        ))
        if not committed:
            existing = await self.store.get("command_receipts", command_id)
            return (self._duplicate(existing, request_hash) if existing else
                    _error("concurrency_conflict", "Command acceptance raced; retry."))
        receipt = committed[("command_receipts", command_id)]
        from services.projection_stream import publish_best_effort

        await publish_best_effort(
            store=self.store, workspace_id=principal.workspace_id,
            projection_type="command", aggregate_id=command_id,
            aggregate_version=int(receipt["version"]),
            safe_payload={"status": receipt["status"],
                          "command_type": command_type},
            idempotency_key=f"accept:{command_id}:{receipt['version']}")
        return {**receipt, "duplicate": False}

    @staticmethod
    def _duplicate(receipt: dict[str, Any], request_hash: str) -> dict[str, Any]:
        if receipt.get("normalized_request_hash") != request_hash:
            return _error(
                "idempotency_conflict",
                "Client request id already names a different command.")
        state = str(receipt.get("status") or "")
        if state not in STATUSES:
            return _error("command_state_invalid", "Stored command state is invalid.")
        return {**receipt, "duplicate": True}

    async def transition(self, *, workspace_id: str, command_id: str,
                         expected_version: int, status: str,
                         run_id: str = "", result_ref: dict[str, Any] | None = None,
                         error_code: str = "") -> dict[str, Any]:
        if status not in STATUSES or status in {"RECEIVED", "ACCEPTED"}:
            return _error("command_transition_invalid", "Command transition is invalid.")
        current = await self.store.get("command_receipts", command_id)
        if not current or current.get("workspace_id") != workspace_id:
            return _error("command_not_found", "Command does not exist.")
        if current.get("status") in TERMINAL:
            return {"status": "success", "duplicate": True, **current}
        if status == "COMPLETED" and not result_ref:
            return _error("command_result_missing", "Completed command needs a result.")
        if status in {"FAILED", "REJECTED"} and not error_code:
            return _error("command_error_missing", "Failed command needs an error code.")
        now = utc_now()
        receipt_updates = {
                "status": status, "run_id": run_id or current.get("run_id"),
                "result_ref": dict(result_ref or {}) or None,
                "error_code": error_code or None,
                "terminal_at": now if status in TERMINAL else None,
                "updated_at": now,
            }
        outboxes = await self.store.list(
            "command_outbox", filters={"command_id": command_id}, limit=2)
        if len(outboxes) != 1:
            return _error("command_outbox_missing",
                          "Command dispatch intent is not recoverable.")
        outbox = outboxes[0]
        committed_batch = await self.store.atomic_compare_and_set((
            AtomicMutation(
                "command_receipts", command_id, expected_version,
                updates=receipt_updates),
            AtomicMutation(
                "command_outbox", str(outbox.get("outbox_id") or outbox["id"]),
                int(outbox["version"]),
                updates={"status": "DELIVERED", "delivered_at": now,
                         "updated_at": now}),
        ))
        if not committed_batch:
            return _error("concurrency_conflict", "Command changed concurrently.")
        receipt = committed_batch[("command_receipts", command_id)]
        from services.projection_stream import publish_best_effort

        await publish_best_effort(
            store=self.store, workspace_id=workspace_id,
            projection_type="command", aggregate_id=command_id,
            aggregate_version=int(receipt["version"]),
            run_id=str(receipt.get("run_id") or ""),
            safe_payload={"status": receipt["status"],
                          "error_code": receipt.get("error_code")},
            idempotency_key=f"transition:{command_id}:{receipt['version']}")
        return {**receipt, "duplicate": False}

    async def get(self, *, workspace_id: str,
                  command_id: str) -> dict[str, Any]:
        row = await self.store.get("command_receipts", command_id)
        if not row or row.get("workspace_id") != workspace_id:
            return _error("command_not_found", "Command does not exist.")
        return dict(row)
