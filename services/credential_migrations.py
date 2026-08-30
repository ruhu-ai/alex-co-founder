"""Receipted retirement of legacy process-global Google OAuth slots.

The safe production migration does not copy a refresh token between tenant
slots.  A global token has no durable owner proof, so assigning it to a
workspace would create authority.  Instead, affected connection projections
are atomically marked ``REAUTH_REQUIRED`` and point at the workspace-scoped
slot.  The next verified consent populates that slot and re-enables only the
connectors proven by the returned grant.
"""

from __future__ import annotations

from typing import Any

from services import data_source_contracts as dsc
from services import google_oauth
from services.durable_store import AtomicMutation, DurableStore
from services.workflow_contracts import stable_id, utc_now


async def retire_legacy_google_credential(
        store: DurableStore, *, workspace_id: str, account: str
        ) -> dict[str, Any]:
    """Fence legacy global credential references without reading token data."""
    if not workspace_id or account not in google_oauth.ACCOUNT_ENV:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "A workspace and closed Google account are required."}
    receipt_id = stable_id(
        "credentialmigration", workspace_id, account, "google-oauth-workspace-v1")
    existing = await store.get(
        "connector_credential_migration_receipts", receipt_id)
    if existing:
        return {"status": "success", "duplicate": True, "receipt": existing}

    rows = await store.list(
        "data_connections", filters={"workspace_id": workspace_id}, limit=50)
    account_connectors = {
        connector_id for connector_id in google_oauth.SCOPE_MAP
        if google_oauth.CONNECTOR_ACCOUNT.get(connector_id, "founder") == account
    }
    affected = [row for row in rows
                if row.get("connector_id") in account_connectors
                and row.get("auth_kind") == dsc.ConnectionAuthKind.GOOGLE_OAUTH.value]
    if not affected:
        return {"status": "error", "error": True,
                "error_code": "migration_row_missing",
                "message": "No workspace Google connection requires migration."}

    now = utc_now()
    mutations: list[AtomicMutation] = []
    for row in affected:
        if row.get("workspace_id") != workspace_id \
                or row.get("founder_id") != workspace_id:
            return {"status": "error", "error": True,
                    "error_code": "migration_owner_ambiguous",
                    "message": "Connection ownership is not deterministic."}
        document_id = str(row.get("id") or row.get("connection_id") or "")
        if not document_id:
            return {"status": "error", "error": True,
                    "error_code": "migration_row_missing",
                    "message": "Connection identity is missing."}
        mutations.append(AtomicMutation(
            "data_connections", document_id, int(row.get("version") or 0),
            updates={
                "credential_ref": google_oauth.credential_ref(
                    account, workspace_id, str(row.get("connector_id") or "")),
                "status": dsc.ConnectionStatus.REAUTH_REQUIRED.value,
                "credential_migration_status": "RECONNECT_REQUIRED",
                "last_error_code": dsc.SafeErrorCode.AUTH_REQUIRED.value,
                "last_error_at": now,
                "updated_at": now,
            }))
    receipt = {
        "schema_version": 1,
        "receipt_id": receipt_id,
        "workspace_id": workspace_id,
        "account": account,
        "migration": "google-oauth-workspace-v1",
        "strategy": "RECONNECT_REQUIRED",
        "credential_values_read": False,
        "credential_values_copied": False,
        "connection_ids": sorted(str(row.get("connection_id") or row.get("id"))
                                 for row in affected),
        "migrated_at": now,
        "version": 1,
    }
    mutations.append(AtomicMutation(
        "connector_credential_migration_receipts", receipt_id, None,
        record=receipt))
    committed = await store.atomic_compare_and_set(tuple(mutations))
    if not committed:
        return {"status": "error", "error": True,
                "error_code": "concurrency_conflict",
                "message": "Connection changed during migration; retry."}
    return {"status": "success", "duplicate": False,
            "receipt": committed[
                ("connector_credential_migration_receipts", receipt_id)]}
