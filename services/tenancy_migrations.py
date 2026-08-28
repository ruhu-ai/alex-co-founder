"""Receipted Phase 2A workspace/discriminator backfill."""

from __future__ import annotations

from typing import Any

from services.durable_store import AtomicMutation, DurableStore
from services.workflow_contracts import stable_id, utc_now

DOMAIN_FIELDS: dict[str, tuple[str, str]] = {
    "opportunities": ("opportunity_domain", "OPPORTUNITY"),
    "applications": ("application_domain", "GRANT_APPLICATION"),
    "ingestions": ("ingestion_domain", "FOUNDER_SOURCE"),
    "artifacts": ("artifact_domain", "FOUNDER_EVIDENCE"),
    "feedback": ("feedback_domain", "FOUNDER_DRAFT"),
    "evidence_checks": ("evidence_domain", "APPLICATION_EVIDENCE"),
    "discovery_requests": ("request_domain", "OPPORTUNITY_DISCOVERY"),
    "approvals": ("approval_domain", "GRANT_APPLICATION"),
    "external_actions": ("action_domain", "CONNECTOR_ACTION"),
    "external_events": ("event_domain", "CONNECTOR_EVENT"),
    "founder_inbox": ("inbox_domain", "EXTERNAL_EVENT"),
    "data_connections": ("connection_domain", "CONNECTOR"),
    "source_grants": ("grant_domain", "CONNECTOR_SOURCE"),
    "wake_deliveries": ("delivery_domain", "FOUNDER_WAKE"),
    "portal_event_receipts": ("event_domain", "PORTAL_EVENT"),
}


async def migrate_tenant_row(store: DurableStore, *, collection: str,
                             document_id: str,
                             owner_override: str = "") -> dict[str, Any]:
    if collection not in DOMAIN_FIELDS:
        return {"status": "error", "error": True,
                "error_code": "migration_collection_invalid",
                "message": "Collection is not in the tenancy manifest."}
    row = await store.get(collection, document_id)
    if not row:
        return {"status": "error", "error": True,
                "error_code": "migration_row_missing",
                "message": "Migration row does not exist."}
    recorded_owner = str(row.get("workspace_id") or row.get("founder_id") or "")
    if recorded_owner and owner_override and recorded_owner != owner_override:
        return {"status": "error", "error": True,
                "error_code": "migration_owner_conflict",
                "message": "Explicit owner conflicts with the durable owner."}
    workspace_id = recorded_owner or str(owner_override or "")
    if not workspace_id:
        return {"status": "error", "error": True,
                "error_code": "migration_owner_ambiguous",
                "message": "No deterministic workspace owner exists."}
    domain_field, default_domain = DOMAIN_FIELDS[collection]
    receipt_id = stable_id(
        "tenancyreceipt", collection, document_id, "workspace-v1")
    existing_receipt = await store.get("tenancy_migration_receipts", receipt_id)
    if existing_receipt:
        return {"status": "success", "duplicate": True,
                "receipt": existing_receipt}
    now = utc_now()
    updates = {
        "workspace_id": workspace_id,
        domain_field: str(row.get(domain_field) or default_domain),
        "tenancy_schema_version": 1,
        "updated_at": row.get("updated_at") or now,
    }
    receipt = {
        "schema_version": 1, "receipt_id": receipt_id,
        "workspace_id": workspace_id, "collection": collection,
        "document_id": document_id,
        "source_version": int(row.get("version") or 0),
        "owner_assignment": ("DURABLE_RECORD" if recorded_owner
                             else "EXPLICIT_OPERATOR_OVERRIDE"),
        "migration": "workspace-discriminator-v1",
        "migrated_at": now, "version": 1,
    }
    committed = await store.atomic_compare_and_set((
        AtomicMutation(collection, document_id, int(row.get("version") or 0),
                       updates=updates),
        AtomicMutation("tenancy_migration_receipts", receipt_id, None,
                       record=receipt),
    ))
    if not committed:
        return {"status": "error", "error": True,
                "error_code": "concurrency_conflict",
                "message": "Tenant row changed during migration; retry."}
    return {"status": "success", "duplicate": False,
            "row": committed[(collection, document_id)],
            "receipt": committed[("tenancy_migration_receipts", receipt_id)]}
