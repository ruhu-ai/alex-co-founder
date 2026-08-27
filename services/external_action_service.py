"""Durable product receipts for non-browser external side effects.

The provider adapters own provider-specific idempotency and reconciliation.
This module owns the common prepare-before-effect boundary, terminal receipts,
safe audit records, and the migration bridge for legacy OAuth connections.
"""

from __future__ import annotations

from typing import Any

from services import connection_registry, data_source_metrics, firestore
from services import data_source_contracts as dsc


async def _connection(founder_id: str, connector_id: str) -> dict[str, Any]:
    gate = await connection_registry.authorize_connector_operation(
        founder_id, connector_id)
    if gate.get("error"):
        return gate
    if gate.get("connection"):
        return gate["connection"]

    # M1/M2 compatibility: credentials predated durable connection rows.  A
    # real operation may create that projection, but status rendering never
    # calls this bridge and therefore never probes or invents provider health.
    account = connection_registry.account_for_connector(connector_id)
    account_ref = "alex-role-mailbox" if account == "alex" else "default"
    return await firestore.upsert_data_connection(
        founder_id, connector_id, account_ref=account_ref,
        auth_kind="google_oauth", status="CONNECTED")


async def prepare(
        founder_id: str, connector_id: str, action_kind: str,
        idempotency_key: str, request_metadata: dict[str, Any], *,
        session_id: str | None = None, application_id: str | None = None,
        resource_id: str | None = None, subject_hash: str | None = None,
        approval_id: str | None = None,
        sandbox_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist PREPARED before an effect and return any original receipt."""
    try:
        dsc.require_closed(action_kind, dsc.ExternalActionKind)
        request_hash = dsc.canonical_hash(request_metadata)
    except ValueError:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "invalid external action contract"}
    connection = await _connection(founder_id, connector_id)
    if connection.get("error"):
        return connection
    result = await firestore.prepare_external_action(
        founder_id, connection["connection_id"], action_kind,
        idempotency_key, request_hash, session_id=session_id,
        application_id=application_id, resource_id=resource_id,
        subject_hash=subject_hash, approval_id=approval_id,
        sandbox_context=sandbox_context)
    outcome = ("success" if result.get("status") == "success" else
               "refused")
    detail = ("duplicate" if result.get("duplicate") else
              "in_progress" if result.get("in_progress") else
              result.get("error_code") or "prepared")
    await firestore.audit(
        f"agent:{connector_id}", f"external_action.{action_kind}.prepare",
        result.get("action_id") or connection["connection_id"], outcome,
        str(detail)[:160], idempotency_key=idempotency_key)
    data_source_metrics.record(
        "external_action_prepare", action_kind=action_kind,
        status=result.get("status"), error_code=result.get("error_code"))
    return result


async def finish(
        founder_id: str, action_id: str, lease_owner: str, status: str, *,
        action_kind: str, idempotency_key: str,
        provider_effect_id: str | None = None,
        result_ref: dict[str, Any] | None = None,
        uncertainty_reason: str | None = None,
        error_code: str | None = None) -> dict[str, Any]:
    """Commit one terminal receipt and append a content-free audit event."""
    result = await firestore.finish_external_action(
        founder_id, action_id, lease_owner, status,
        provider_effect_id=provider_effect_id, result_ref=result_ref,
        uncertainty_reason=uncertainty_reason, error_code=error_code)
    audit_result = {
        "SUCCEEDED": "success", "FAILED": "error", "UNCERTAIN": "uncertain",
    }.get(status, "error")
    await firestore.audit(
        "agent:external_action", f"external_action.{action_kind}.finish",
        action_id, audit_result,
        f"error={error_code or ''} provider_effect={provider_effect_id or ''}"[:200],
        idempotency_key=idempotency_key)
    data_source_metrics.record(
        "external_action_finish", action_kind=action_kind, status=status,
        error_code=error_code)
    return result


async def reconcile(
        founder_id: str, action_id: str, status: str, *, action_kind: str,
        idempotency_key: str, provider_effect_id: str | None = None,
        result_ref: dict[str, Any] | None = None,
        error_code: str | None = None) -> dict[str, Any]:
    """Resolve UNCERTAIN only from provider evidence, never by blind retry."""
    result = await firestore.reconcile_external_action(
        founder_id, action_id, status, provider_effect_id=provider_effect_id,
        result_ref=result_ref, error_code=error_code)
    await firestore.audit(
        "agent:external_action", f"external_action.{action_kind}.reconcile",
        action_id, "success" if result.get("status") == "success" else "refused",
        f"resolved={status} provider_effect={provider_effect_id or ''}"[:200],
        idempotency_key=idempotency_key)
    data_source_metrics.record(
        "external_action_reconcile", action_kind=action_kind, status=status,
        error_code=error_code)
    return result


def duplicate_result(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded caller-facing view of an already terminal receipt."""
    if receipt.get("status") == "SUCCEEDED":
        kind = receipt.get("action_kind")
        message = {
            "send_email": "This email was already sent; nothing was sent twice.",
            "create_calendar_event": ("This meeting was already booked; no invites "
                                      "were sent twice."),
            "export_drive_file": ("This document was already copied to Drive; no "
                                  "duplicate file was created."),
        }.get(kind, "This external action already completed successfully.")
        return {"status": "success", "duplicate": True,
                "action_id": receipt.get("action_id"),
                "provider_effect_id": receipt.get("provider_effect_id"),
                "message": message, **(receipt.get("result_ref") or {})}
    return {"status": "error", "error": True, "duplicate": True,
            "action_id": receipt.get("action_id"),
            "error_code": receipt.get("error_code") or "provider_rejected",
            "message": "This external action already reached a terminal failure."}
