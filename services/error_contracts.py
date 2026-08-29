"""Closed service-error to HTTP mapping owned by transport boundaries."""

from __future__ import annotations

from typing import Any

_STATUS_BY_CODE = {
    "auth_required": 401,
    "hiring_auth_required": 401,
    "interactive_human_required": 401,
    "step_up_required": 401,
    "workload_unauthorized": 401,
    "operation_forbidden": 403,
    "csrf_failed": 403,
    "content_type_required": 415,
    "request_too_large": 413,
    "owner_mismatch": 404,
    "approval_not_found": 404,
    "command_not_found": 404,
    "run_not_found": 404,
    "step_not_found": 404,
    "wait_not_found": 404,
    "action_not_found": 404,
    "invalid_contract": 400,
    "inventory_hash_required": 400,
    "command_contract_invalid": 400,
    "workflow_kind_invalid": 400,
    "wait_contract_invalid": 400,
    "idempotency_conflict": 409,
    "version_conflict": 409,
    "concurrency_conflict": 409,
    "approval_terminal": 409,
    "approval_expired": 409,
    "approval_binding_mismatch": 409,
    "lease_conflict": 409,
    "run_fenced": 409,
    "reconciliation_required": 409,
    "provider_unavailable": 503,
    "public_intake_not_enabled": 503,
    "provider_timeout": 504,
}


def http_status(result: dict[str, Any], *, default: int = 409) -> int:
    """Translate a model-safe envelope without adding transport fields to it."""
    if not result.get("error"):
        return 200
    return _STATUS_BY_CODE.get(str(result.get("error_code") or ""), default)
