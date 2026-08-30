"""Durable connection status and lifecycle for the closed connector set.

Status rendering is Firestore-only.  Provider calls happen at consent time or
during a real operation, and their safe outcome is projected here afterwards.
"""

from __future__ import annotations

import asyncio
from typing import Any

from services import data_source_contracts as dsc
from services import firestore, google_oauth

GOOGLE_PERMISSIONS_URL = "https://myaccount.google.com/permissions"
_ACCOUNT_REF = {"founder": "default", "alex": "alex-role-mailbox"}


def _scope_contract_current(connector_id: str, row: dict[str, Any]) -> bool:
    """True when durable scope evidence still satisfies the code contract.

    Alex's role-account grants are connector-isolated and therefore exact.
    Founder grants can contain the union of several founder connectors, so
    those remain a required-subset check.
    """
    required = set(google_oauth.SCOPE_MAP.get(connector_id, ()))
    granted = set(row.get("granted_scopes") or ())
    # Pre-registry rows have no durable scope evidence. Preserve the explicit
    # legacy migration path; once a consent has recorded scopes, drift is
    # enforced fail-closed and can never fall back to this compatibility case.
    if not granted:
        return True
    if account_for_connector(connector_id) == "alex":
        return granted == required
    return required.issubset(granted)


def account_for_connector(connector_id: str) -> str:
    """Return the fixed Google account slot for a closed connector."""
    dsc.require_closed(connector_id, dsc.ConnectorId)
    return google_oauth.CONNECTOR_ACCOUNT.get(connector_id, "founder")


def connection_id_for(founder_id: str, connector_id: str) -> str:
    account = account_for_connector(connector_id)
    return dsc.data_connection_id(
        founder_id, connector_id, _ACCOUNT_REF[account])


async def project_verified_consent(
        founder_id: str, requested_connector: str, account: str, *,
        account_hint: str, granted_scopes: list[str],
        provider_account_hash: str = "") -> dict[str, Any]:
    """Persist every connector proven present in one verified Google grant."""
    try:
        requested = dsc.require_closed(requested_connector, dsc.ConnectorId)
    except ValueError:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "unknown connector"}
    if requested.value not in google_oauth.SCOPE_MAP \
            or account != account_for_connector(requested.value):
        return {"status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "connector account mismatch"}
    granted = set(granted_scopes)
    projected: list[dict[str, Any]] = []
    for connector_id, scopes in google_oauth.SCOPE_MAP.items():
        if account_for_connector(connector_id) != account:
            continue
        required = set(google_oauth.STATUS_SCOPES.get(connector_id, scopes))
        if not required.issubset(granted):
            continue
        row = await firestore.upsert_data_connection(
            founder_id, connector_id, account_ref=_ACCOUNT_REF[account],
            account_hint=account_hint, auth_kind="google_oauth",
            credential_ref=google_oauth.credential_ref(account, founder_id),
            granted_scopes=sorted(granted),
            provider_account_hash=provider_account_hash, status="CONNECTED")
        if row.get("error"):
            return row
        row = await firestore.transition_data_connection(
            founder_id, row["connection_id"], expected_version=row["version"],
            status="CONNECTED", verified=True,
            successful_operation="oauth_consent")
        if row.get("error"):
            return row
        projected.append(row)
    if not any(row.get("connector_id") == requested.value for row in projected):
        return {"status": "error", "error": True,
                "error_code": "scope_missing",
                "message": "required connector scope was not granted"}
    return {"status": "success", "connections": projected}


async def authorize_connector_operation(founder_id: str,
                                        connector_id: str) -> dict[str, Any]:
    """Refuse a known disabled connection while permitting legacy migration.

    A missing row is not reported healthy, but is temporarily allowed so the
    first successful real operation can create the projection during M1/M2.
    """
    try:
        connection_id = connection_id_for(founder_id, connector_id)
    except ValueError:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract", "message": "unknown connector"}
    row = await firestore.get_data_connection(founder_id, connection_id)
    if not row:
        return {"status": "success", "legacy_unprojected": True,
                "connection_id": connection_id}
    if row.get("status") in {"DISCONNECTING", "DISCONNECTED",
                             "REAUTH_REQUIRED"}:
        return {"status": "error", "error": True,
                "error_code": "auth_required",
                "message": "connector is disconnected; reconnect it first"}
    if not _scope_contract_current(connector_id, row):
        return {"status": "error", "error": True,
                "error_code": "scope_missing",
                "message": "connector permissions changed; reconnect it first"}
    return {"status": "success", "connection": row,
            "connection_id": connection_id}


async def record_connector_success(founder_id: str, connector_id: str,
                                   operation: str) -> dict[str, Any]:
    """Project a successful real operation without a provider status probe."""
    gate = await authorize_connector_operation(founder_id, connector_id)
    if gate.get("error"):
        return gate
    row = gate.get("connection")
    if not row:
        account = account_for_connector(connector_id)
        row = await firestore.upsert_data_connection(
            founder_id, connector_id, account_ref=_ACCOUNT_REF[account],
            auth_kind="google_oauth",
            credential_ref=google_oauth.credential_ref(account, founder_id),
            status="CONNECTED")
        if row.get("error"):
            return row
    return await firestore.transition_data_connection(
        founder_id, row["connection_id"], expected_version=row["version"],
        successful_operation=operation)


async def record_connector_failure(founder_id: str, connector_id: str,
                                   error_code: str) -> dict[str, Any]:
    """Project a safe provider failure; optimistic version defeats stale writes."""
    try:
        safe_error = dsc.require_closed(error_code, dsc.SafeErrorCode)
        connection_id = connection_id_for(founder_id, connector_id)
    except ValueError:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract", "message": "invalid failure"}
    row = await firestore.get_data_connection(founder_id, connection_id)
    if not row:
        return {"status": "success", "projected": False}
    if safe_error == dsc.SafeErrorCode.AUTH_REQUIRED:
        next_status = "REAUTH_REQUIRED"
    elif safe_error in {dsc.SafeErrorCode.PERMISSION_DENIED,
                        dsc.SafeErrorCode.SCOPE_MISSING}:
        next_status = "DEGRADED"
    else:
        next_status = row.get("status") if row.get("status") == "CONNECTED" \
            else "DEGRADED"
    return await firestore.transition_data_connection(
        founder_id, connection_id, expected_version=row["version"],
        status=next_status, error_code=safe_error.value)


async def record_operation_result(founder_id: str, connector_id: str,
                                  operation: str,
                                  result: dict[str, Any]) -> dict[str, Any]:
    """Project one adapter result using only a closed safe error category."""
    if result.get("status") == "success":
        return await record_connector_success(founder_id, connector_id, operation)
    supplied = result.get("error_code")
    if supplied in dsc.values(dsc.SafeErrorCode):
        code = str(supplied)
    else:
        message = str(result.get("message") or "").lower()
        if any(token in message for token in ("oauth", "credential", "401",
                                               "unauthorized", "invalid_grant")):
            code = dsc.SafeErrorCode.AUTH_REQUIRED.value
        elif any(token in message for token in ("scope", "permission", "403")):
            code = dsc.SafeErrorCode.SCOPE_MISSING.value
        elif "timeout" in message:
            code = dsc.SafeErrorCode.PROVIDER_TIMEOUT.value
        else:
            code = dsc.SafeErrorCode.PROVIDER_UNAVAILABLE.value
    return await record_connector_failure(founder_id, connector_id, code)


# Spec-facing aliases retain the compact §10 names.
async def record_connection_success(connection_id: str, operation: str,
                                    founder_id: str = "user") -> dict[str, Any]:
    row = await firestore.get_data_connection(founder_id, connection_id)
    if not row:
        return {"status": "error", "error": True,
                "error_code": "owner_mismatch", "message": "connection not found"}
    return await record_connector_success(founder_id, row["connector_id"], operation)


async def record_connection_failure(connection_id: str, error_code: str,
                                    founder_id: str = "user") -> dict[str, Any]:
    row = await firestore.get_data_connection(founder_id, connection_id)
    if not row:
        return {"status": "error", "error": True,
                "error_code": "owner_mismatch", "message": "connection not found"}
    return await record_connector_failure(founder_id, row["connector_id"], error_code)


def _status_line(row: dict[str, Any]) -> str:
    status = row.get("status", "DISCONNECTED")
    hint = row.get("account_hint") or "Google account"
    if status == "CONNECTED":
        return f"Connected · {hint}"
    if status == "DEGRADED":
        return "Connected · needs attention"
    if status == "REAUTH_REQUIRED":
        return "Reconnect required"
    if status == "DISCONNECTING":
        return "Disconnecting"
    if row.get("disconnect_outcome") == "LOCAL_ONLY_SHARED_GRANT":
        return "Disconnected locally · Google grant still active"
    if row.get("disconnect_outcome") == "REMOTE_UNCERTAIN":
        return "Disconnected locally · check Google permissions"
    return "Disconnected"


async def list_connection_status(founder_id: str) -> dict[str, Any]:
    """Return the provider-free connector panel projection."""
    rows = await firestore.list_data_connections(founder_id)
    grants = await firestore.list_source_grants(founder_id)
    active_counts: dict[str, int] = {}
    for grant in grants:
        if grant.get("status") == "ACTIVE":
            cid = str(grant.get("connection_id") or "")
            active_counts[cid] = active_counts.get(cid, 0) + 1
    by_connector: dict[str, dict[str, Any]] = {}
    for row in rows:
        connector_id = row.get("connector_id", "")
        if connector_id not in dsc.values(dsc.ConnectorId):
            continue
        scope_current = _scope_contract_current(connector_id, row)
        effective_status = row.get("status")
        if effective_status in {"CONNECTED", "DEGRADED"} and not scope_current:
            effective_status = "REAUTH_REQUIRED"
        account = account_for_connector(connector_id)
        enabled_others = sorted(
            other.get("connector_id", "") for other in rows
            if other.get("connection_id") != row.get("connection_id")
            and other.get("status") not in {"DISCONNECTED"}
            and other.get("auth_kind") == "google_oauth"
            and account_for_connector(other.get("connector_id", "")) == account)
        by_connector[connector_id] = {
            "connection_id": row.get("connection_id"),
            "connector_id": connector_id,
            "roles": list(row.get("roles") or []),
            "status": effective_status,
            "status_line": _status_line({**row, "status": effective_status}),
            "account_hint": row.get("account_hint") or "",
            "granted_scope_count": len(row.get("granted_scopes") or []),
            "selected_source_count": active_counts.get(row.get("connection_id"), 0),
            "last_success_at": row.get("last_success_at"),
            "last_error_code": ("scope_missing" if not scope_current
                                else row.get("last_error_code")),
            "version": row.get("version"),
            "can_connect": effective_status in {"DISCONNECTED", "REAUTH_REQUIRED"},
            "can_reconnect": effective_status in {"CONNECTED", "DEGRADED",
                                                    "REAUTH_REQUIRED"},
            "can_disconnect": row.get("status") not in {"DISCONNECTED",
                                                          "DISCONNECTING"},
            "disconnect_mode": ("local_only" if enabled_others else "account_wide"),
            "disconnect_impacts": enabled_others,
            "permissions_url": GOOGLE_PERMISSIONS_URL,
        }
    return {"status": "success", "connections": by_connector}


async def disconnect_connection(founder_id: str, connection_id: str, *,
                                expected_version: int) -> dict[str, Any]:
    """Disable one connector and honestly handle shared Google revocation."""
    row = await firestore.get_data_connection(founder_id, connection_id)
    if not row:
        return {"status": "error", "error": True,
                "error_code": "owner_mismatch", "message": "connection not found"}
    if int(row.get("version") or 0) != expected_version:
        return {"status": "error", "error": True,
                "error_code": "version_conflict",
                "message": "connection changed concurrently"}
    if row.get("auth_kind") != "google_oauth":
        return {"status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "built-in connectors cannot be disconnected"}
    account = account_for_connector(row["connector_id"])
    rows = await firestore.list_data_connections(founder_id)
    other_enabled = sorted(
        candidate.get("connector_id", "") for candidate in rows
        if candidate.get("connection_id") != connection_id
        and candidate.get("status") not in {"DISCONNECTED"}
        and candidate.get("auth_kind") == "google_oauth"
        and account_for_connector(candidate.get("connector_id", "")) == account)

    if other_enabled:
        transitioned = await firestore.transition_data_connection(
            founder_id, connection_id, expected_version=expected_version,
            status="DISCONNECTED", disconnect_outcome="LOCAL_ONLY_SHARED_GRANT")
        if transitioned.get("error"):
            return transitioned
        grants = await firestore.revoke_connection_source_grants(
            founder_id, connection_id)
        await firestore.audit(
            f"founder:{founder_id}", "connection.disconnect_local",
            connection_id, "success",
            f"source_grants={grants.get('revoked_count', 0)}")
        return {
            "status": "success", "outcome": "LOCAL_ONLY",
            "connection": transitioned, "local_access_removed": True,
            "provider_revoked": False, "other_connectors": other_enabled,
            "action_required": False,
            "message": ("This connector is disabled in Co-Founder. The shared "
                        "Google grant remains active for the other connectors."),
            "permissions_url": GOOGLE_PERMISSIONS_URL,
        }

    disconnecting = await firestore.transition_data_connection(
        founder_id, connection_id, expected_version=expected_version,
        status="DISCONNECTING")
    if disconnecting.get("error"):
        return disconnecting
    remote = await asyncio.to_thread(
        google_oauth.revoke_account_grant, account, 10, founder_id)
    local = await asyncio.to_thread(
        google_oauth.delete_account_credential, account, founder_id)
    grants = await firestore.revoke_connection_source_grants(founder_id, connection_id)
    certain = remote.get("status") == "success" and local.get("status") == "success"
    final = await firestore.transition_data_connection(
        founder_id, connection_id,
        expected_version=int(disconnecting.get("version") or 0),
        status="DISCONNECTED",
        error_code=None if certain else "remote_revocation_uncertain",
        disconnect_outcome="REMOTE_REVOKED" if certain else "REMOTE_UNCERTAIN")
    await firestore.audit(
        f"founder:{founder_id}", "connection.disconnect_account",
        connection_id, "success" if certain else "uncertain",
        f"source_grants={grants.get('revoked_count', 0)}")
    return {
        "status": "success", "outcome": "SUCCEEDED" if certain else "UNCERTAIN",
        "connection": final, "local_access_removed": local.get("status") == "success",
        "provider_revoked": remote.get("status") == "success",
        "other_connectors": [], "action_required": not certain,
        "message": ("Google access was revoked for this account."
                    if certain else
                    "Co-Founder disabled this connection locally, but could not "
                    "confirm Google removed the grant. Check Google permissions."),
        "permissions_url": GOOGLE_PERMISSIONS_URL,
    }
