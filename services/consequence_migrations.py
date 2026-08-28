"""Receipted migration of legacy effect rows onto the platform ledger."""

from __future__ import annotations

from typing import Any

from services import capability_registry
from services.canonical import canonical_hash
from services.durable_store import AtomicMutation, DurableStore
from services.workflow_contracts import stable_id, utc_now

_GRANT_GATE_BINDINGS = {
    "submit_application": ("submit_application", "browser"),
    "create_portal_account": ("create_portal_account", "browser"),
    "send_email": ("send_email", "alex_mail"),
    "book_meeting": ("create_calendar_event", "calendar"),
}


async def migrate_legacy_grant_approval(
        store: DurableStore, approval_id: str) -> dict[str, Any]:
    """Backfill one legacy grant approval without inventing authority.

    A PENDING/GRANTED v1 row never recorded plan/capability/policy bindings, so
    it is expired and the founder must review a fresh v2 request.  Terminal
    rows are enriched only for audit/search and can never authorize work.
    """
    legacy = await store.get("approvals", approval_id)
    if not legacy:
        return {"status": "error", "error": True,
                "error_code": "migration_row_missing",
                "message": "Legacy approval does not exist."}
    if int(legacy.get("schema_version") or 1) >= 2:
        return {"status": "success", "duplicate": True,
                "approval": legacy}
    workspace_id = str(
        legacy.get("workspace_id") or legacy.get("founder_id") or "")
    gate = str(legacy.get("gate") or "")
    binding = _GRANT_GATE_BINDINGS.get(gate)
    if not workspace_id or not binding:
        return {"status": "error", "error": True,
                "error_code": "migration_binding_ambiguous",
                "message": "Legacy approval owner or gate is not recoverable."}
    action_kind, connector_id = binding
    descriptor = capability_registry.require_external_action(
        action_kind, connector_id)
    target = str(legacy.get("application_id") or "")
    subject_hash = str(legacy.get("subject_hash") or "")
    application = await store.get("applications", target) if target else None
    if application and str(application.get("founder_id") or "") != workspace_id:
        return {"status": "error", "error": True,
                "error_code": "migration_owner_ambiguous",
                "message": "Approval target belongs to another workspace."}
    now = utc_now()
    receipt_id = stable_id(
        "consequencereceipt", "legacy-grant-approval-v2", approval_id)
    receipt = await store.get("consequence_migration_receipts", receipt_id)
    if receipt:
        return {"status": "success", "duplicate": True, "receipt": receipt}
    prior_status = str(legacy.get("status") or "")
    open_legacy = prior_status in {"PENDING", "GRANTED", "CLAIMED"}
    updates = {
        "schema_version": 2,
        "approval_domain": "GRANT_APPLICATION",
        "workspace_id": workspace_id,
        "requested_by_actor_id": str(
            legacy.get("requested_by_actor_id") or workspace_id),
        "approving_actor_requirement": "INTERACTIVE_MEMBER",
        "run_id": (application or {}).get("workflow_run_id"),
        "plan_hash": (application or {}).get("workflow_plan_hash"),
        "step_id": gate,
        "capability_id": descriptor.capability_id,
        "capability_version": descriptor.semantic_version,
        "action_kind": action_kind,
        "target_hash": canonical_hash(
            {"target": target}, domain="approval-target"),
        "normalized_payload_hash": canonical_hash(
            {"subject_hash": subject_hash}, domain="approval-payload"),
        "policy_id": descriptor.approval_policy_id,
        "policy_version": "1",
        "domain_ref": target,
        "domain_version": int((application or {}).get("version") or 1),
        "connector_id": connector_id,
        "connector_binding_version": "connection-v1",
        "migration_disposition": (
            "EXPIRED_REAPPROVAL_REQUIRED" if open_legacy
            else "TERMINAL_AUDIT_BACKFILL"),
        "migrated_at": now,
        "updated_at": now,
    }
    if open_legacy:
        updates.update(status="EXPIRED", token=None,
                       expiry_reason="legacy_binding_incomplete")
    migration_receipt = {
        "schema_version": 1, "receipt_id": receipt_id,
        "workspace_id": workspace_id, "approval_id": approval_id,
        "source_collection": "approvals", "target_collection": "approvals",
        "source_version": int(legacy.get("version") or 0),
        "source_status": prior_status,
        "migration": "legacy-grant-approval-v2",
        "disposition": updates["migration_disposition"],
        "migrated_at": now, "version": 1,
    }
    committed = await store.atomic_compare_and_set((
        AtomicMutation("approvals", approval_id,
                       int(legacy.get("version") or 0), updates=updates),
        AtomicMutation("consequence_migration_receipts", receipt_id, None,
                       record=migration_receipt),
    ))
    if not committed:
        return {"status": "error", "error": True,
                "error_code": "concurrency_conflict",
                "message": "Legacy approval changed during migration."}
    return {"status": "success", "duplicate": False,
            "approval": committed[("approvals", approval_id)],
            "receipt": committed[("consequence_migration_receipts", receipt_id)]}


async def migrate_internal_demo_approval(
        store: DurableStore, approval_id: str) -> dict[str, Any]:
    """Copy one legacy decision into the shared approval collection."""
    legacy = await store.get("internal_demo_approvals", approval_id)
    if not legacy:
        return {"status": "error", "error": True,
                "error_code": "migration_row_missing",
                "message": "Legacy approval does not exist."}
    workspace_id = str(legacy.get("workspace_id") or "")
    if not workspace_id:
        return {"status": "error", "error": True,
                "error_code": "migration_owner_ambiguous",
                "message": "Legacy approval has no workspace owner."}
    receipt_id = stable_id(
        "consequencereceipt", "internal-demo-approval-v2", approval_id)
    receipt = await store.get("consequence_migration_receipts", receipt_id)
    if receipt:
        return {"status": "success", "duplicate": True, "receipt": receipt}
    if await store.get("approvals", approval_id):
        return {"status": "error", "error": True,
                "error_code": "idempotency_conflict",
                "message": "Shared approvals already has this identity."}
    now = utc_now()
    migrated = {
        **{key: value for key, value in legacy.items() if key != "id"},
        "schema_version": max(2, int(legacy.get("schema_version") or 1)),
        "approval_id": approval_id, "founder_id": workspace_id,
        "workspace_id": workspace_id,
        "approval_domain": "INTERNAL_CONTROLLED_DEMO",
        "migrated_from": "internal_demo_approvals",
        "migrated_at": now, "version": 1,
    }
    migration_receipt = {
        "schema_version": 1, "receipt_id": receipt_id,
        "workspace_id": workspace_id, "approval_id": approval_id,
        "source_collection": "internal_demo_approvals",
        "target_collection": "approvals",
        "source_version": int(legacy["version"]),
        "migration": "internal-demo-approval-v2",
        "migrated_at": now, "version": 1,
    }
    committed = await store.atomic_compare_and_set((
        AtomicMutation("internal_demo_approvals", approval_id,
                       int(legacy["version"]),
                       updates={"migrated_to": "approvals",
                                "migration_receipt_id": receipt_id,
                                "updated_at": now}),
        AtomicMutation("approvals", approval_id, None, record=migrated),
        AtomicMutation("consequence_migration_receipts", receipt_id, None,
                       record=migration_receipt),
    ))
    if not committed:
        return {"status": "error", "error": True,
                "error_code": "concurrency_conflict",
                "message": "Legacy approval changed during migration."}
    return {"status": "success", "duplicate": False,
            "approval": committed[("approvals", approval_id)],
            "receipt": committed[("consequence_migration_receipts", receipt_id)]}


async def migrate_internal_demo_action(
        store: DurableStore, action_id: str) -> dict[str, Any]:
    """Copy one legacy demo receipt without changing its effect identity."""
    legacy = await store.get("internal_demo_actions", action_id)
    if not legacy:
        return {"status": "error", "error": True,
                "error_code": "migration_row_missing",
                "message": "Legacy action does not exist."}
    workspace_id = str(legacy.get("workspace_id") or "")
    if not workspace_id:
        return {"status": "error", "error": True,
                "error_code": "migration_owner_ambiguous",
                "message": "Legacy action has no workspace owner."}
    receipt_id = stable_id(
        "consequencereceipt", "internal-demo-v2", action_id)
    receipt = await store.get("consequence_migration_receipts", receipt_id)
    if receipt:
        return {"status": "success", "duplicate": True,
                "receipt": receipt}
    existing = await store.get("external_actions", action_id)
    if existing:
        return {"status": "error", "error": True,
                "error_code": "idempotency_conflict",
                "message": "Platform ledger already has this action identity."}
    now = utc_now()
    migrated = {
        **{key: value for key, value in legacy.items() if key != "id"},
        "schema_version": max(2, int(legacy.get("schema_version") or 1)),
        "action_id": action_id, "founder_id": workspace_id,
        "workspace_id": workspace_id,
        "action_domain": "INTERNAL_CONTROLLED_DEMO",
        "connection_id": "internal_demo_google",
        "migrated_from": "internal_demo_actions",
        "migrated_at": now, "version": 1,
    }
    migration_receipt = {
        "schema_version": 1, "receipt_id": receipt_id,
        "workspace_id": workspace_id, "action_id": action_id,
        "source_collection": "internal_demo_actions",
        "target_collection": "external_actions",
        "source_version": int(legacy["version"]),
        "migration": "internal-demo-consequence-v2",
        "migrated_at": now, "version": 1,
    }
    committed = await store.atomic_compare_and_set((
        AtomicMutation("internal_demo_actions", action_id,
                       int(legacy["version"]),
                       updates={"migrated_to": "external_actions",
                                "migration_receipt_id": receipt_id,
                                "updated_at": now}),
        AtomicMutation("external_actions", action_id, None, record=migrated),
        AtomicMutation("consequence_migration_receipts", receipt_id, None,
                       record=migration_receipt),
    ))
    if not committed:
        return {"status": "error", "error": True,
                "error_code": "concurrency_conflict",
                "message": "Legacy action changed during migration."}
    return {"status": "success", "duplicate": False,
            "action": committed[("external_actions", action_id)],
            "receipt": committed[("consequence_migration_receipts", receipt_id)]}
