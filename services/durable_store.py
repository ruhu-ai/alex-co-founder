"""Small production Firestore boundary for durable workflow/domain records.

The hiring runtime deliberately depends on this narrow interface instead of
chat/session state.  The in-memory implementation is explicit test support;
production always resolves the Firestore implementation and never silently
falls back to process memory.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol

from services import firestore


def _normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    """Preserve the explicit synthetic marker contract on every fixture row.

    ``synthetic``/``fixture_id`` remain the internal names used by the H0-H3
    services.  The aliases make the reviewed fixture provenance contract
    explicit at the persistence boundary, so a newly added record cannot
    silently omit it.
    """
    normalized = deepcopy(record)
    if normalized.get("synthetic") is True:
        normalized.setdefault("is_synthetic", True)
        fixture_id = normalized.get("fixture_id")
        if fixture_id:
            normalized.setdefault("fixture_set_id", fixture_id)
    return normalized


def _sort_key(value: Any) -> tuple[int, float, str]:
    """Order like Firestore's value ordering, not like ``str()``.

    Sorting every field as text put ``sequence`` 10 before 2, so an in-memory
    run timeline came back in a different order than the same query against
    Firestore. Numbers sort numerically and ahead of strings; missing values
    sort first.
    """
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, bool):
        return (1, float(value), "")
    if isinstance(value, (int, float)):
        return (1, float(value), "")
    return (2, 0.0, str(value))


def _collection_ref(name: str):
    """Resolve only the reviewed durable collections; no caller-selected path."""
    client = firestore.get_client()
    refs = {
        "approvals": client.collection("approvals"),
        "audit": client.collection("audit"),
        "external_actions": client.collection("external_actions"),
        "external_events": client.collection("external_events"),
        "founder_inbox": client.collection("founder_inbox"),
        "workspace_members": client.collection("workspace_members"),
        "workflow_runs": client.collection("workflow_runs"),
        "workflow_steps": client.collection("workflow_steps"),
        "step_attempts": client.collection("step_attempts"),
        "waits": client.collection("waits"),
        "run_events": client.collection("run_events"),
        "connector_credential_grants": client.collection("connector_credential_grants"),
        "hiring_roles": client.collection("hiring_roles"),
        "hiring_policy_versions": client.collection("hiring_policy_versions"),
        "hiring_policy_impacts": client.collection("hiring_policy_impacts"),
        "candidate_identities": client.collection("candidate_identities"),
        "candidate_applications": client.collection("candidate_applications"),
        "hiring_candidate_artifacts": client.collection("hiring_candidate_artifacts"),
        "candidate_evidence": client.collection("candidate_evidence"),
        "candidate_assessments": client.collection("candidate_assessments"),
        "hiring_decisions": client.collection("hiring_decisions"),
        "hiring_candidate_requests": client.collection("hiring_candidate_requests"),
        "candidate_data_rights_receipts": client.collection("candidate_data_rights_receipts"),
        "hiring_mailbox_state": client.collection("hiring_mailbox_state"),
        "hiring_mailbox_probe_receipts": client.collection("hiring_mailbox_probe_receipts"),
        "hiring_fixture_messages": client.collection("hiring_fixture_messages"),
        "mailbox_fetch_batches": client.collection("mailbox_fetch_batches"),
        "mailbox_fetch_batch_entries": client.collection("mailbox_fetch_batch_entries"),
        "hiring_cursor_receipts": client.collection("hiring_cursor_receipts"),
        "hiring_sandbox_runs": client.collection("hiring_sandbox_runs"),
        "hiring_sandbox_destinations": client.collection("hiring_sandbox_destinations"),
        "hiring_sandbox_connector_bindings": client.collection("hiring_sandbox_connector_bindings"),
        "hiring_conversation_turns": client.collection("hiring_conversation_turns"),
        "hiring_process_retrospectives": client.collection("hiring_process_retrospectives"),
        "hiring_reply_correlations": client.collection("hiring_reply_correlations"),
        "internal_demo_runs": client.collection("internal_demo_runs"),
        "internal_demo_approvals": client.collection("internal_demo_approvals"),
        "internal_demo_actions": client.collection("internal_demo_actions"),
        "internal_demo_applications": client.collection("internal_demo_applications"),
    }
    if name not in refs:
        raise ValueError("unregistered durable collection")
    return refs[name]


class DurableStore(Protocol):
    async def get(self, collection: str, document_id: str) -> dict[str, Any] | None: ...
    async def create(self, collection: str, document_id: str,
                     record: dict[str, Any]) -> bool: ...
    async def put(self, collection: str, document_id: str,
                  record: dict[str, Any]) -> None: ...
    async def delete(self, collection: str, document_id: str) -> None: ...
    async def compare_and_set(self, collection: str, document_id: str,
                              expected_version: int,
                              updates: dict[str, Any]) -> dict[str, Any] | None: ...
    async def list(self, collection: str, *, filters: dict[str, Any],
                   order_by: str = "", descending: bool = False,
                   limit: int = 200) -> list[dict[str, Any]]: ...


class FirestoreDurableStore:
    """Firestore implementation with create-only and optimistic CAS writes."""

    async def get(self, collection: str, document_id: str) -> dict[str, Any] | None:
        snap = await _collection_ref(collection).document(document_id).get()
        return ({**snap.to_dict(), "id": snap.id} if snap.exists else None)

    async def create(self, collection: str, document_id: str,
                     record: dict[str, Any]) -> bool:
        from google.api_core.exceptions import AlreadyExists

        ref = _collection_ref(collection).document(document_id)
        try:
            await ref.create(_normalized_record(record))
            return True
        except AlreadyExists:
            return False

    async def put(self, collection: str, document_id: str,
                  record: dict[str, Any]) -> None:
        await _collection_ref(collection).document(document_id).set(
            _normalized_record(record))

    async def delete(self, collection: str, document_id: str) -> None:
        await _collection_ref(collection).document(document_id).delete()

    async def compare_and_set(self, collection: str, document_id: str,
                              expected_version: int,
                              updates: dict[str, Any]) -> dict[str, Any] | None:
        from google.cloud import firestore as gc_firestore

        ref = _collection_ref(collection).document(document_id)
        transaction = firestore.get_client().transaction()

        @gc_firestore.async_transactional
        async def _cas(txn):
            snap = await ref.get(transaction=txn)
            if not snap.exists:
                return None
            row = snap.to_dict()
            if int(row.get("version", 0)) != expected_version:
                return None
            committed = _normalized_record(
                {**row, **deepcopy(updates), "version": expected_version + 1})
            txn.set(ref, committed)
            return {**committed, "id": document_id}

        return await _cas(transaction)

    async def list(self, collection: str, *, filters: dict[str, Any],
                   order_by: str = "", descending: bool = False,
                   limit: int = 200) -> list[dict[str, Any]]:
        query = _collection_ref(collection)
        for field_name, value in filters.items():
            query = query.where(field_name, "==", value)
        if order_by:
            from google.cloud.firestore_v1 import Query
            direction = Query.DESCENDING if descending else Query.ASCENDING
            query = query.order_by(order_by, direction=direction)
        query = query.limit(max(1, min(limit, 1000)))
        return [{**snap.to_dict(), "id": snap.id} async for snap in query.stream()]


@dataclass
class InMemoryDurableStore:
    """Concurrency-safe deterministic store for tests and synthetic evals only."""

    records: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def get(self, collection: str, document_id: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self.records.get(collection, {}).get(document_id)
            return ({**deepcopy(row), "id": document_id} if row is not None else None)

    async def create(self, collection: str, document_id: str,
                     record: dict[str, Any]) -> bool:
        async with self._lock:
            bucket = self.records.setdefault(collection, {})
            if document_id in bucket:
                return False
            bucket[document_id] = _normalized_record(record)
            return True

    async def put(self, collection: str, document_id: str,
                  record: dict[str, Any]) -> None:
        async with self._lock:
            self.records.setdefault(collection, {})[document_id] = _normalized_record(record)

    async def delete(self, collection: str, document_id: str) -> None:
        async with self._lock:
            self.records.setdefault(collection, {}).pop(document_id, None)

    async def compare_and_set(self, collection: str, document_id: str,
                              expected_version: int,
                              updates: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            bucket = self.records.setdefault(collection, {})
            row = bucket.get(document_id)
            if row is None or int(row.get("version", 0)) != expected_version:
                return None
            committed = _normalized_record(
                {**row, **deepcopy(updates), "version": expected_version + 1})
            bucket[document_id] = committed
            return {**deepcopy(committed), "id": document_id}

    async def list(self, collection: str, *, filters: dict[str, Any],
                   order_by: str = "", descending: bool = False,
                   limit: int = 200) -> list[dict[str, Any]]:
        async with self._lock:
            rows = [
                {**deepcopy(row), "id": document_id}
                for document_id, row in self.records.get(collection, {}).items()
                if all(row.get(key) == value for key, value in filters.items())
            ]
        if order_by:
            rows.sort(key=lambda row: _sort_key(row.get(order_by)),
                      reverse=descending)
        return rows[:max(1, min(limit, 1000))]


def production_store() -> DurableStore:
    """Return the production store; intentionally no memory fallback."""
    return FirestoreDurableStore()
