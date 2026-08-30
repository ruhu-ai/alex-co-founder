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
    "opportunities": CollectionLifecycle("opportunities", _FIELD),
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
    "wake_deliveries": CollectionLifecycle("wake_deliveries", _FIELD),
    "conversation_deliveries": CollectionLifecycle(
        "conversation_deliveries", _FIELD, "workspace_id"),
    "background_pilot_capacity": CollectionLifecycle(
        "background_pilot_capacity", _FIELD, "workspace_id"),
    "portal_event_receipts": CollectionLifecycle("portal_event_receipts", _FIELD),
    "command_receipts": CollectionLifecycle(
        "command_receipts", _FIELD, "workspace_id"),
    "command_outbox": CollectionLifecycle(
        "command_outbox", _FIELD, "workspace_id"),
    "media_consent_grants": CollectionLifecycle(
        "media_consent_grants", _FIELD, "workspace_id"),
    "live_media_shares": CollectionLifecycle(
        "live_media_shares", _FIELD, "workspace_id"),
    "projection_streams": CollectionLifecycle(
        "projection_streams", _FIELD, "workspace_id"),
    "projection_events": CollectionLifecycle(
        "projection_events", _FIELD, "workspace_id"),
    "tenancy_migration_receipts": CollectionLifecycle(
        "tenancy_migration_receipts", _FIELD, "workspace_id"),
    "connector_credential_migration_receipts": CollectionLifecycle(
        "connector_credential_migration_receipts", _FIELD, "workspace_id"),
    "consequence_migration_receipts": CollectionLifecycle(
        "consequence_migration_receipts", _FIELD, "workspace_id"),
    "workflow_migration_receipts": CollectionLifecycle(
        "workflow_migration_receipts", _FIELD, "workspace_id"),
    "action_execution_outbox": CollectionLifecycle(
        "action_execution_outbox", _FIELD, "workspace_id"),
    # docs/25 uses workspace ownership rather than the legacy founder id.
    "workspace_members": CollectionLifecycle("workspace_members", _FIELD, "workspace_id"),
    "workflow_runs": CollectionLifecycle("workflow_runs", _FIELD, "workspace_id"),
    "investor_outreach": CollectionLifecycle(
        "investor_outreach", _FIELD, "workspace_id"),
    "investor_candidates": CollectionLifecycle(
        "investor_candidates", _FIELD, "workspace_id"),
    "outreach_drafts": CollectionLifecycle(
        "outreach_drafts", _FIELD, "workspace_id"),
    "investor_replies": CollectionLifecycle(
        "investor_replies", _FIELD, "workspace_id"),
    "meeting_briefs": CollectionLifecycle(
        "meeting_briefs", _FIELD, "workspace_id"),
    "workspace_profiles": CollectionLifecycle(
        "workspace_profiles", _FIELD, "workspace_id"),
    "actor_preference_profiles": CollectionLifecycle(
        "actor_preference_profiles", _FIELD, "workspace_id"),
    "profile_fact_pointers": CollectionLifecycle(
        "profile_fact_pointers", _FIELD, "workspace_id"),
    "profile_facts": CollectionLifecycle(
        "profile_facts", _FIELD, "workspace_id"),
    "profile_fact_receipts": CollectionLifecycle(
        "profile_fact_receipts", _FIELD, "workspace_id"),
    "memory_items": CollectionLifecycle(
        "memory_items", _FIELD, "workspace_id"),
    "memory_source_manifests": CollectionLifecycle(
        "memory_source_manifests", _FIELD, "workspace_id"),
    "memory_settings": CollectionLifecycle(
        "memory_settings", _FIELD, "workspace_id"),
    "memory_control_receipts": CollectionLifecycle(
        "memory_control_receipts", _FIELD, "workspace_id"),
    "memory_deletion_tombstones": CollectionLifecycle(
        "memory_deletion_tombstones", _FIELD, "workspace_id"),
    "memory_deletion_jobs": CollectionLifecycle(
        "memory_deletion_jobs", _FIELD, "workspace_id"),
    "memory_export_jobs": CollectionLifecycle(
        "memory_export_jobs", _FIELD, "workspace_id"),
    # The deny ledger lives in an independently administered, non-restored
    # database. Ordinary workspace export/deletion must never roll it back or
    # erase its non-content safety fence.
    "memory_deletion_ledger": CollectionLifecycle(
        "memory_deletion_ledger", _SHARED),
    "memory_deletion_ledger_heads": CollectionLifecycle(
        "memory_deletion_ledger_heads", _SHARED),
    "memory_write_receipts": CollectionLifecycle(
        "memory_write_receipts", _FIELD, "workspace_id"),
    "memory_search_receipts": CollectionLifecycle(
        "memory_search_receipts", _FIELD, "workspace_id"),
    "deletion_jobs": CollectionLifecycle("deletion_jobs", _SHARED),
    "deletion_work_items": CollectionLifecycle(
        "deletion_work_items", _FIELD, "workspace_id"),
    "deletion_receipts": CollectionLifecycle("deletion_receipts", _SHARED),
    "capability_states": CollectionLifecycle("capability_states", _SHARED),
    "operational_snapshots": CollectionLifecycle("operational_snapshots", _SHARED),
    "slo_observations": CollectionLifecycle(
        "slo_observations", _FIELD, "workspace_id"),
    "workspace_budgets": CollectionLifecycle(
        "workspace_budgets", _FIELD, "workspace_id"),
    "budget_consumption_receipts": CollectionLifecycle(
        "budget_consumption_receipts", _FIELD, "workspace_id"),
    "change_rollouts": CollectionLifecycle("change_rollouts", _SHARED),
    "chaos_drills": CollectionLifecycle("chaos_drills", _SHARED),
    "migration_drills": CollectionLifecycle("migration_drills", _SHARED),
    "recovery_drills": CollectionLifecycle("recovery_drills", _SHARED),
    "governance_reports": CollectionLifecycle("governance_reports", _SHARED),
    "workflow_plans": CollectionLifecycle("workflow_plans", _FIELD, "workspace_id"),
    "workflow_steps": CollectionLifecycle("workflow_steps", _FIELD, "workspace_id"),
    "step_attempts": CollectionLifecycle("step_attempts", _FIELD, "workspace_id"),
    "waits": CollectionLifecycle("waits", _FIELD, "workspace_id"),
    "run_events": CollectionLifecycle("run_events", _FIELD, "workspace_id"),
    "connector_credential_grants": CollectionLifecycle(
        "connector_credential_grants", _FIELD, "workspace_id"),
    # Compiled qualification and lifecycle decisions are shared governance
    # records; selection/invocation receipts belong to the exact workspace.
    "skill_qualification_bundles": CollectionLifecycle(
        "skill_qualification_bundles", _SHARED),
    "skill_lifecycle_states": CollectionLifecycle(
        "skill_lifecycle_states", _SHARED),
    "skill_lifecycle_events": CollectionLifecycle(
        "skill_lifecycle_events", _SHARED),
    "skill_selections": CollectionLifecycle(
        "skill_selections", _FIELD, "workspace_id"),
    "skill_invocations": CollectionLifecycle(
        "skill_invocations", _FIELD, "workspace_id"),
    "skill_receipts": CollectionLifecycle(
        "skill_receipts", _FIELD, "workspace_id"),
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
    "hiring_coordination_items": CollectionLifecycle(
        "hiring_coordination_items", _FIELD, "workspace_id"),
    "internal_demo_runs": CollectionLifecycle(
        "internal_demo_runs", _FIELD, "workspace_id"),
    "internal_demo_approvals": CollectionLifecycle(
        "internal_demo_approvals", _FIELD, "workspace_id"),
    "internal_demo_actions": CollectionLifecycle(
        "internal_demo_actions", _FIELD, "workspace_id"),
    "internal_demo_applications": CollectionLifecycle(
        "internal_demo_applications", _FIELD, "workspace_id"),
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
    "image_observations": CollectionLifecycle(
        "image_observations", _DOC, parent_collection="artifacts"),
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
    blob_deleted = 0
    blob_errors: list[str] = []
    for snapshot in ordered_snapshots:
        collection = snapshot.reference.path.split("/", 1)[0]
        if collection in {"artifacts", "documents", "document_versions"}:
            from services import storage

            row = snapshot.to_dict() or {}
            names = [str(row.get("storage_name") or row.get("artifact_name")
                         or row.get("artifact") or "")]
            if collection == "artifacts":
                names.append(str(row.get("normalized_storage_name") or ""))
            try:
                for name in dict.fromkeys(item for item in names if item):
                    blob_deleted += bool(storage.delete_artifact(name))
            except Exception:
                blob_errors.append(snapshot.reference.path)
                continue
        await snapshot.reference.delete()
        deleted += 1
    return {"status": "error" if blob_errors else "success",
            "error": bool(blob_errors),
            "error_code": "deletion_degraded" if blob_errors else None,
            "dry_run": False,
            "founder_id": founder_id, "inventory_hash": plan_hash,
            "deleted": deleted, "blobs_deleted": blob_deleted,
            "blob_errors": blob_errors}
