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
from typing import Any, Protocol, Sequence

from services import firestore


def _normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    """Copy a record without injecting domain-specific persistence fields."""
    return deepcopy(record)


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
        "opportunities": client.collection("opportunities"),
        "applications": client.collection("applications"),
        "ingestions": client.collection("ingestions"),
        "artifacts": client.collection("artifacts"),
        "feedback": client.collection("feedback"),
        "evidence_checks": client.collection("evidence_checks"),
        "discovery_requests": client.collection("discovery_requests"),
        "approvals": client.collection("approvals"),
        "audit": client.collection("audit"),
        "external_actions": client.collection("external_actions"),
        "external_events": client.collection("external_events"),
        "data_connections": client.collection("data_connections"),
        "source_grants": client.collection("source_grants"),
        "wake_deliveries": client.collection("wake_deliveries"),
        "conversation_deliveries": client.collection("conversation_deliveries"),
        "background_pilot_capacity": client.collection("background_pilot_capacity"),
        "portal_event_receipts": client.collection("portal_event_receipts"),
        "command_receipts": client.collection("command_receipts"),
        "command_outbox": client.collection("command_outbox"),
        "projection_streams": client.collection("projection_streams"),
        "projection_events": client.collection("projection_events"),
        "tenancy_migration_receipts": client.collection(
            "tenancy_migration_receipts"),
        "connector_credential_migration_receipts": client.collection(
            "connector_credential_migration_receipts"),
        "consequence_migration_receipts": client.collection(
            "consequence_migration_receipts"),
        "workflow_migration_receipts": client.collection(
            "workflow_migration_receipts"),
        "action_execution_outbox": client.collection(
            "action_execution_outbox"),
        "founder_inbox": client.collection("founder_inbox"),
        "workspace_members": client.collection("workspace_members"),
        "workflow_runs": client.collection("workflow_runs"),
        "workflow_plans": client.collection("workflow_plans"),
        "workflow_steps": client.collection("workflow_steps"),
        "step_attempts": client.collection("step_attempts"),
        "waits": client.collection("waits"),
        "run_events": client.collection("run_events"),
        "investor_outreach": client.collection("investor_outreach"),
        "investor_candidates": client.collection("investor_candidates"),
        "outreach_drafts": client.collection("outreach_drafts"),
        "investor_replies": client.collection("investor_replies"),
        "meeting_briefs": client.collection("meeting_briefs"),
        "workspace_profiles": client.collection("workspace_profiles"),
        "actor_preference_profiles": client.collection("actor_preference_profiles"),
        "profile_fact_pointers": client.collection("profile_fact_pointers"),
        "profile_facts": client.collection("profile_facts"),
        "profile_fact_receipts": client.collection("profile_fact_receipts"),
        "memory_items": client.collection("memory_items"),
        "memory_write_receipts": client.collection("memory_write_receipts"),
        "memory_search_receipts": client.collection("memory_search_receipts"),
        "deletion_jobs": client.collection("deletion_jobs"),
        "deletion_work_items": client.collection("deletion_work_items"),
        "deletion_receipts": client.collection("deletion_receipts"),
        "capability_states": client.collection("capability_states"),
        "operational_snapshots": client.collection("operational_snapshots"),
        "slo_observations": client.collection("slo_observations"),
        "workspace_budgets": client.collection("workspace_budgets"),
        "budget_consumption_receipts": client.collection(
            "budget_consumption_receipts"),
        "change_rollouts": client.collection("change_rollouts"),
        "chaos_drills": client.collection("chaos_drills"),
        "migration_drills": client.collection("migration_drills"),
        "recovery_drills": client.collection("recovery_drills"),
        "governance_reports": client.collection("governance_reports"),
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


@dataclass(frozen=True)
class AtomicMutation:
    """One version-fenced write in a same-database atomic commit.

    ``expected_version=None`` means the document must not exist and ``record``
    is created at version 1. Otherwise the document must exist at that exact
    version and ``updates`` are merged with version incremented once.
    """

    collection: str
    document_id: str
    expected_version: int | None
    updates: dict[str, Any] = field(default_factory=dict)
    record: dict[str, Any] = field(default_factory=dict)
    check_only: bool = False
    replace: bool = False


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
    async def atomic_compare_and_set(
            self, mutations: Sequence[AtomicMutation]
            ) -> dict[tuple[str, str], dict[str, Any]] | None: ...
    async def list(self, collection: str, *, filters: dict[str, Any],
                   order_by: str = "", descending: bool = False,
                   limit: int = 200,
                   start_after: tuple[str, Any] | None = None
                   ) -> list[dict[str, Any]]: ...


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

    async def atomic_compare_and_set(
            self, mutations: Sequence[AtomicMutation]
            ) -> dict[tuple[str, str], dict[str, Any]] | None:
        """Commit create/update mutations together after reading all fences."""
        from google.cloud import firestore as gc_firestore

        items = tuple(mutations)
        if not items or len(items) > 100:
            raise ValueError("atomic mutation batch must contain 1..100 items")
        keys = [(item.collection, item.document_id) for item in items]
        if len(set(keys)) != len(keys):
            raise ValueError("atomic mutation batch contains duplicate document")
        refs = [_collection_ref(item.collection).document(item.document_id)
                for item in items]
        transaction = firestore.get_client().transaction()

        @gc_firestore.async_transactional
        async def _commit(txn):
            snapshots = [await ref.get(transaction=txn) for ref in refs]
            committed: dict[tuple[str, str], dict[str, Any]] = {}
            for item, snapshot in zip(items, snapshots, strict=True):
                if item.check_only and item.expected_version is None:
                    raise ValueError("check-only mutation requires an existing version")
                if item.expected_version is None:
                    if snapshot.exists:
                        return None
                    row = _normalized_record(
                        {**deepcopy(item.record), "version": 1})
                else:
                    if not snapshot.exists:
                        return None
                    current = snapshot.to_dict()
                    if int(current.get("version", 0)) != item.expected_version:
                        return None
                    row = (_normalized_record(current) if item.check_only else
                           _normalized_record({
                               **({} if item.replace else current),
                               **(deepcopy(item.record) if item.replace
                                  else deepcopy(item.updates)),
                               "version": item.expected_version + 1,
                           }))
                committed[(item.collection, item.document_id)] = {
                    **row, "id": item.document_id}
            for ref, item in zip(refs, items, strict=True):
                if item.check_only:
                    continue
                row = committed[(item.collection, item.document_id)]
                txn.set(ref, {key: value for key, value in row.items()
                              if key != "id"})
            return committed

        return await _commit(transaction)

    async def list(self, collection: str, *, filters: dict[str, Any],
                   order_by: str = "", descending: bool = False,
                   limit: int = 200,
                   start_after: tuple[str, Any] | None = None
                   ) -> list[dict[str, Any]]:
        query = _collection_ref(collection)
        for field_name, value in filters.items():
            query = query.where(field_name, "==", value)
        document_id_order = order_by == "id"
        if start_after:
            field_name, value = start_after
            if field_name == "id":
                from google.cloud.firestore_v1.field_path import FieldPath
                query = query.where(FieldPath.document_id(), ">", value)
                document_id_order = True
            else:
                query = query.where(field_name, ">", value)
            if not order_by:
                order_by = field_name
        if order_by:
            from google.cloud.firestore_v1 import Query
            from google.cloud.firestore_v1.field_path import FieldPath
            direction = Query.DESCENDING if descending else Query.ASCENDING
            query = query.order_by(
                FieldPath.document_id() if document_id_order else order_by,
                direction=direction)
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

    async def atomic_compare_and_set(
            self, mutations: Sequence[AtomicMutation]
            ) -> dict[tuple[str, str], dict[str, Any]] | None:
        items = tuple(mutations)
        if not items or len(items) > 100:
            raise ValueError("atomic mutation batch must contain 1..100 items")
        keys = [(item.collection, item.document_id) for item in items]
        if len(set(keys)) != len(keys):
            raise ValueError("atomic mutation batch contains duplicate document")
        async with self._lock:
            committed: dict[tuple[str, str], dict[str, Any]] = {}
            for item in items:
                if item.check_only and item.expected_version is None:
                    raise ValueError("check-only mutation requires an existing version")
                current = self.records.get(item.collection, {}).get(
                    item.document_id)
                if item.expected_version is None:
                    if current is not None:
                        return None
                    row = _normalized_record(
                        {**deepcopy(item.record), "version": 1})
                else:
                    if (current is None
                            or int(current.get("version", 0))
                            != item.expected_version):
                        return None
                    row = (_normalized_record(current) if item.check_only else
                           _normalized_record({
                               **({} if item.replace else current),
                               **(deepcopy(item.record) if item.replace
                                  else deepcopy(item.updates)),
                               "version": item.expected_version + 1,
                           }))
                committed[(item.collection, item.document_id)] = row
            for item in items:
                if item.check_only:
                    continue
                collection, document_id = item.collection, item.document_id
                row = committed[(collection, document_id)]
                self.records.setdefault(collection, {})[document_id] = row
            return {
                key: {**deepcopy(row), "id": key[1]}
                for key, row in committed.items()
            }

    async def list(self, collection: str, *, filters: dict[str, Any],
                   order_by: str = "", descending: bool = False,
                   limit: int = 200,
                   start_after: tuple[str, Any] | None = None
                   ) -> list[dict[str, Any]]:
        async with self._lock:
            rows = [
                {**deepcopy(row), "id": document_id}
                for document_id, row in self.records.get(collection, {}).items()
                if all(row.get(key) == value for key, value in filters.items())
            ]
        if start_after:
            field_name, value = start_after
            rows = [row for row in rows
                    if row.get(field_name) is not None
                    and row.get(field_name) > value]
        if order_by:
            rows.sort(key=lambda row: _sort_key(row.get(order_by)),
                      reverse=descending)
        return rows[:max(1, min(limit, 1000))]


def production_store() -> DurableStore:
    """Return the production store; intentionally no memory fallback."""
    return FirestoreDurableStore()
