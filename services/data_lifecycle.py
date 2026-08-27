"""Registry-driven founder export and deletion inventory (docs/24 WI-1).

The registry covers every Firestore collection the application registers.
Deletion is dry-run by default and requires the caller to echo the exact plan
hash before any writes, so a stale/broadened inventory cannot be executed.
Shared product catalog records are inventoried but never founder-deleted.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

from services import firestore


class OwnershipMode(str, Enum):
    FIELD = "field"
    DOCUMENT_ID = "document_id"
    LEGACY_SINGLE_FOUNDER = "legacy_single_founder"
    SHARED = "shared"


@dataclass(frozen=True)
class CollectionLifecycle:
    name: str
    ownership: OwnershipMode
    owner_field: str = "founder_id"
    parent_collection: str | None = None


_FIELD = OwnershipMode.FIELD
_DOC = OwnershipMode.DOCUMENT_ID
_LEGACY = OwnershipMode.LEGACY_SINGLE_FOUNDER
_SHARED = OwnershipMode.SHARED

# Every top-level collection is explicit. Entries marked SHARED are still part
# of inventory/coverage, but are never exported as founder-owned or deleted.
TOP_LEVEL_LIFECYCLE: dict[str, CollectionLifecycle] = {
    "opportunities": CollectionLifecycle("opportunities", _SHARED),
    "applications": CollectionLifecycle("applications", _FIELD),
    "profiles": CollectionLifecycle("profiles", _DOC),
    "ingestions": CollectionLifecycle("ingestions", _FIELD),
    "artifacts": CollectionLifecycle("artifacts", _FIELD),
    "feedback": CollectionLifecycle("feedback", _FIELD),
    "approvals": CollectionLifecycle("approvals", _FIELD),
    "audit": CollectionLifecycle("audit", _SHARED),
    "evidence_checks": CollectionLifecycle("evidence_checks", _FIELD),
    "browser_runs": CollectionLifecycle("browser_runs", _FIELD, "user_id"),
    "discovery_requests": CollectionLifecycle("discovery_requests", _FIELD),
    "oauth_states": CollectionLifecycle("oauth_states", _FIELD),
    "integrations": CollectionLifecycle("integrations", _DOC),
    "gmail_state": CollectionLifecycle("gmail_state", _LEGACY),
    "alex_mail_state": CollectionLifecycle("alex_mail_state", _LEGACY),
    "portal_registrations": CollectionLifecycle("portal_registrations", _FIELD),
    "source_state": CollectionLifecycle("source_state", _SHARED),
    "documents": CollectionLifecycle("documents", _FIELD),
    "document_versions": CollectionLifecycle("document_versions", _FIELD),
    "founder_state": CollectionLifecycle("founder_state", _DOC),
    "resource_index": CollectionLifecycle("resource_index", _FIELD),
    "session_resource_links": CollectionLifecycle("session_resource_links", _FIELD),
    "session_catalog": CollectionLifecycle("session_catalog", _FIELD),
    "data_connections": CollectionLifecycle("data_connections", _FIELD),
    "source_grants": CollectionLifecycle("source_grants", _FIELD),
    "external_events": CollectionLifecycle("external_events", _FIELD),
    "founder_inbox": CollectionLifecycle("founder_inbox", _FIELD),
    "external_actions": CollectionLifecycle("external_actions", _FIELD),
    # docs/25 uses workspace ownership rather than the legacy founder id.
    "workspace_members": CollectionLifecycle("workspace_members", _FIELD, "workspace_id"),
    "workflow_runs": CollectionLifecycle("workflow_runs", _FIELD, "workspace_id"),
    "workflow_steps": CollectionLifecycle("workflow_steps", _FIELD, "workspace_id"),
    "step_attempts": CollectionLifecycle("step_attempts", _FIELD, "workspace_id"),
    "waits": CollectionLifecycle("waits", _FIELD, "workspace_id"),
    "run_events": CollectionLifecycle("run_events", _FIELD, "workspace_id"),
    "connector_credential_grants": CollectionLifecycle(
        "connector_credential_grants", _FIELD, "workspace_id"),
    "hiring_roles": CollectionLifecycle("hiring_roles", _FIELD, "workspace_id"),
    "hiring_policy_versions": CollectionLifecycle(
        "hiring_policy_versions", _FIELD, "workspace_id"),
    "hiring_policy_impacts": CollectionLifecycle(
        "hiring_policy_impacts", _FIELD, "workspace_id"),
    "candidate_identities": CollectionLifecycle(
        "candidate_identities", _FIELD, "workspace_id"),
    "candidate_applications": CollectionLifecycle(
        "candidate_applications", _FIELD, "workspace_id"),
    "hiring_candidate_artifacts": CollectionLifecycle(
        "hiring_candidate_artifacts", _FIELD, "workspace_id"),
    "candidate_evidence": CollectionLifecycle(
        "candidate_evidence", _FIELD, "workspace_id"),
    "candidate_assessments": CollectionLifecycle(
        "candidate_assessments", _FIELD, "workspace_id"),
    "hiring_decisions": CollectionLifecycle(
        "hiring_decisions", _FIELD, "workspace_id"),
    "hiring_candidate_requests": CollectionLifecycle(
        "hiring_candidate_requests", _FIELD, "workspace_id"),
    "candidate_data_rights_receipts": CollectionLifecycle(
        "candidate_data_rights_receipts", _FIELD, "workspace_id"),
    "hiring_mailbox_state": CollectionLifecycle(
        "hiring_mailbox_state", _FIELD, "workspace_id"),
    "hiring_mailbox_probe_receipts": CollectionLifecycle(
        "hiring_mailbox_probe_receipts", _FIELD, "workspace_id"),
    "hiring_fixture_messages": CollectionLifecycle(
        "hiring_fixture_messages", _FIELD, "workspace_id"),
    "mailbox_fetch_batches": CollectionLifecycle(
        "mailbox_fetch_batches", _FIELD, "workspace_id"),
    "mailbox_fetch_batch_entries": CollectionLifecycle(
        "mailbox_fetch_batch_entries", _FIELD, "workspace_id"),
    "hiring_cursor_receipts": CollectionLifecycle(
        "hiring_cursor_receipts", _FIELD, "workspace_id"),
    "hiring_sandbox_runs": CollectionLifecycle(
        "hiring_sandbox_runs", _FIELD, "workspace_id"),
    "hiring_sandbox_destinations": CollectionLifecycle(
        "hiring_sandbox_destinations", _FIELD, "workspace_id"),
    "hiring_sandbox_connector_bindings": CollectionLifecycle(
        "hiring_sandbox_connector_bindings", _FIELD, "workspace_id"),
    "hiring_conversation_turns": CollectionLifecycle(
        "hiring_conversation_turns", _FIELD, "workspace_id"),
    "hiring_process_retrospectives": CollectionLifecycle(
        "hiring_process_retrospectives", _FIELD, "workspace_id"),
    "hiring_reply_correlations": CollectionLifecycle(
        "hiring_reply_correlations", _FIELD, "workspace_id"),
}

SUBCOLLECTION_LIFECYCLE: dict[str, CollectionLifecycle] = {
    "update_receipts": CollectionLifecycle(
        "update_receipts", _DOC, parent_collection="profiles"),
    "frames": CollectionLifecycle(
        "frames", _DOC, parent_collection="browser_runs"),
    "actions": CollectionLifecycle(
        "actions", _DOC, parent_collection="browser_runs"),
    "chunks": CollectionLifecycle(
        "chunks", _DOC, parent_collection="artifacts"),
}


def registry_coverage() -> dict[str, Any]:
    """Return exact registry drift; both missing and unregistered fail tests."""
    top = frozenset(TOP_LEVEL_LIFECYCLE)
    sub = frozenset(SUBCOLLECTION_LIFECYCLE)
    return {
        "status": "success" if (top == firestore.TOP_LEVEL_COLLECTIONS
                                and sub == firestore.SUBCOLLECTIONS) else "error",
        "missing_top": sorted(firestore.TOP_LEVEL_COLLECTIONS - top),
        "extra_top": sorted(top - firestore.TOP_LEVEL_COLLECTIONS),
        "missing_subcollections": sorted(firestore.SUBCOLLECTIONS - sub),
        "extra_subcollections": sorted(sub - firestore.SUBCOLLECTIONS),
    }


def _is_single_founder_owner(founder_id: str) -> bool:
    return founder_id == os.environ.get("FOUNDER_ID", "founder")


async def _top_documents(founder_id: str,
                         spec: CollectionLifecycle) -> list[Any]:
    collection = firestore.get_client().collection(spec.name)
    if spec.ownership == OwnershipMode.SHARED:
        return []
    if spec.ownership == OwnershipMode.DOCUMENT_ID:
        snapshot = await collection.document(founder_id).get()
        return [snapshot] if snapshot.exists else []
    if spec.ownership == OwnershipMode.LEGACY_SINGLE_FOUNDER:
        if not _is_single_founder_owner(founder_id):
            return []
        return [snapshot async for snapshot in collection.stream()]
    query = collection.where(spec.owner_field, "==", founder_id)
    return [snapshot async for snapshot in query.stream()]


async def _inventory(founder_id: str) -> tuple[list[dict[str, Any]], list[Any]]:
    if not founder_id or len(founder_id) > 256:
        raise ValueError("invalid founder id")
    coverage = registry_coverage()
    if coverage["status"] != "success":
        raise RuntimeError(f"collection lifecycle registry drift: {coverage}")

    entries: list[dict[str, Any]] = []
    snapshots_by_collection: dict[str, list[Any]] = {}
    deletion_order: list[Any] = []
    for name in sorted(TOP_LEVEL_LIFECYCLE):
        spec = TOP_LEVEL_LIFECYCLE[name]
        snapshots = await _top_documents(founder_id, spec)
        snapshots_by_collection[name] = snapshots
        entries.append({
            "collection": name, "kind": "top_level",
            "ownership": spec.ownership.value,
            "paths": sorted(snapshot.reference.path for snapshot in snapshots),
            "records": [snapshot.to_dict() | {"id": snapshot.id}
                        for snapshot in snapshots],
            "deletable": spec.ownership != OwnershipMode.SHARED,
        })

    # Inherited ownership is resolved from already owner-qualified parents;
    # collection-group queries are not trusted to infer a parent owner.
    for name in sorted(SUBCOLLECTION_LIFECYCLE):
        spec = SUBCOLLECTION_LIFECYCLE[name]
        snapshots = []
        for parent in snapshots_by_collection[spec.parent_collection or ""]:
            snapshots.extend([
                child async for child in parent.reference.collection(name).stream()
            ])
        entries.append({
            "collection": name, "kind": "subcollection",
            "parent_collection": spec.parent_collection,
            "ownership": "inherited",
            "paths": sorted(snapshot.reference.path for snapshot in snapshots),
            "records": [snapshot.to_dict() | {"id": snapshot.id}
                        for snapshot in snapshots],
            "deletable": True,
        })
        deletion_order.extend(snapshots)

    # Children first, then owned parents. Shared rows never enter this list.
    for name in sorted(TOP_LEVEL_LIFECYCLE):
        spec = TOP_LEVEL_LIFECYCLE[name]
        if spec.ownership != OwnershipMode.SHARED:
            deletion_order.extend(snapshots_by_collection[name])
    return entries, deletion_order


def _inventory_hash(founder_id: str, paths: list[str]) -> str:
    payload = json.dumps({"founder_id": founder_id, "paths": sorted(paths)},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


async def export_founder_data(founder_id: str) -> dict[str, Any]:
    """Export owner-qualified rows while visibly inventorying shared records."""
    entries, _ = await _inventory(founder_id)
    paths = [path for entry in entries for path in entry["paths"]]
    return {
        "status": "success", "founder_id": founder_id,
        "inventory_hash": _inventory_hash(founder_id, paths),
        "record_count": len(paths), "collections": entries,
    }


async def delete_founder_data(founder_id: str, *, execute: bool = False,
                              expected_inventory_hash: str = "") -> dict[str, Any]:
    """Plan founder deletion, or execute exactly the echoed immutable plan.

    The default is always dry-run. Execution refuses without an exact plan hash
    generated from a fresh inventory, and deletes subcollection rows first.
    """
    entries, ordered_snapshots = await _inventory(founder_id)
    paths = [snapshot.reference.path for snapshot in ordered_snapshots]
    plan_hash = _inventory_hash(founder_id, paths)
    if not execute:
        return {
            "status": "success", "dry_run": True, "founder_id": founder_id,
            "inventory_hash": plan_hash, "delete_count": len(paths),
            "paths": paths, "collections": entries,
        }
    if not expected_inventory_hash or expected_inventory_hash != plan_hash:
        return {
            "status": "error", "error": True,
            "error_code": "version_conflict",
            "message": "deletion inventory changed; run a new dry-run",
        }
    deleted = 0
    for snapshot in ordered_snapshots:
        await snapshot.reference.delete()
        deleted += 1
    return {"status": "success", "dry_run": False,
            "founder_id": founder_id, "inventory_hash": plan_hash,
            "deleted": deleted}
