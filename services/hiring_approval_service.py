"""Hiring approvals bound across sessions to actor/run/policy/action truth."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from services.actor_identity import ActorPrincipal, authorize
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import canonical_hash, stable_id, utc_now


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    del http_status
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


def _requires_fresh(approval: dict[str, Any], requested: bool) -> bool:
    """Step-up is decided by the action, not by the client.

    An approval that authorizes an outbound Hiring effect always needs a recent
    sign-in. The caller's flag can only tighten this, never relax it: the same
    actor both requests and resolves an approval in a single-founder workspace,
    so proof of a live human at the keyboard is the control that remains.
    """
    return bool(requested) or str(approval.get("action_kind", "")) in {
        "H4S_SEND_EMAIL", "H4S_CREATE_CALENDAR_EVENT",
        "H4S_UPDATE_CALENDAR_EVENT", "H4S_CANCEL_CALENDAR_EVENT",
        "HIRING_SEND_EMAIL", "HIRING_CREATE_INTERVIEW",
        "HIRING_UPDATE_INTERVIEW", "HIRING_CANCEL_INTERVIEW",
        "HIRING_COORDINATE_INTERVIEW", "HIRING_SEND_REFERENCE_REQUEST",
        "HIRING_SEND_OFFER",
        "HIRING_APPROVE_ONBOARDING_PLAN"}


async def request_approval(*, principal: ActorPrincipal, run_id: str,
                           role_id: str, policy_version_id: str,
                           action_kind: str, exact_action: dict[str, Any],
                           client_request_id: str,
                           store: DurableStore | None = None,
                           ttl_minutes: int = 30) -> dict[str, Any]:
    gate = authorize(principal, "resolve_approval")
    if gate.get("error"):
        return gate
    if not exact_action or not 1 <= ttl_minutes <= 1440:
        return _error("invalid_contract", "Approval subject is invalid.", 400)
    durable = store or production_store()
    run = await durable.get("workflow_runs", run_id)
    if (not run or run.get("workspace_id") != principal.workspace_id
            or run.get("domain_ref") not in {role_id, exact_action.get("candidate_application_id")}):
        return _error("owner_mismatch", "Workflow run is not authorized.", 404)
    subject_hash = canonical_hash({
        "schema_version": 1, "workspace_id": principal.workspace_id,
        "run_id": run_id, "role_id": role_id,
        "policy_version_id": policy_version_id,
        "action_kind": action_kind, "exact_action": exact_action,
    })
    approval_id = stable_id("happroval", principal.workspace_id, client_request_id)
    capability_id = None
    capability_version = None
    connector_id = None
    connector_by_action = {
        "H4S_SEND_EMAIL": "h4s_google",
        "H4S_CREATE_CALENDAR_EVENT": "h4s_google",
        "HIRING_SEND_EMAIL": "alex_mail",
        "HIRING_SEND_REFERENCE_REQUEST": "alex_mail",
        "HIRING_CREATE_INTERVIEW": "calendar",
        "HIRING_UPDATE_INTERVIEW": "calendar",
        "HIRING_CANCEL_INTERVIEW": "calendar",
    }
    if action_kind in connector_by_action:
        from services.capability_registry import require_controlled_action

        connector_id = connector_by_action[action_kind]
        capability = require_controlled_action(action_kind, connector_id)
        capability_id = capability.capability_id
        capability_version = capability.semantic_version
    now = datetime.now(timezone.utc)
    row = {
        "schema_version": 2, "approval_id": approval_id,
        "approval_domain": "HIRING", "workspace_id": principal.workspace_id,
        "founder_id": principal.workspace_id,
        "run_id": run_id, "role_id": role_id,
        "plan_hash": run.get("plan_hash"),
        "step_id": exact_action.get("candidate_run_id") or run_id,
        "capability_id": capability_id,
        "capability_version": capability_version,
        "connector_id": connector_id,
        "connector_binding_version": exact_action.get(
            "connector_binding_id"),
        "policy_version_id": policy_version_id, "action_kind": action_kind,
        "policy_id": "hiring_policy", "policy_version": policy_version_id,
        "domain_ref": role_id, "domain_version": int(run.get("version") or 1),
        "target_hash": canonical_hash({
            "destinations": exact_action.get("destination_ids") or
            exact_action.get("normalized_destinations") or []}),
        "normalized_payload_hash": canonical_hash(exact_action),
        "subject_hash": subject_hash, "exact_action": exact_action,
        "requested_by_actor_id": principal.actor_id,
        "approving_actor_requirement": "INTERACTIVE_MEMBER",
        "resolved_by_actor_id": None, "decided_by_actor_id": None,
        "status": "PENDING",
        "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
        "consumed_at": None, "created_at": now.isoformat(),
        "claim_id": None, "claimed_action_id": None, "claimed_at": None,
        "voided_at": None, "void_reason": None,
        "updated_at": now.isoformat(), "client_request_id": client_request_id,
        "provenance": run.get("provenance") or {}, "version": 1,
    }
    created = await durable.create("approvals", approval_id, row)
    existing = row if created else await durable.get("approvals", approval_id)
    if not existing or existing.get("subject_hash") != subject_hash:
        return _error("idempotency_conflict", "Request id names another approval.")
    return {"status": "success", "duplicate": not created,
            "approval_id": approval_id, "subject_hash": subject_hash,
            "approval_status": existing["status"]}


async def resolve_approval(*, principal: ActorPrincipal, approval_id: str,
                           decision: str, store: DurableStore | None = None,
                           require_fresh: bool = False) -> dict[str, Any]:
    durable = store or production_store()
    approval = await durable.get("approvals", approval_id)
    if not approval or approval.get("approval_domain") != "HIRING":
        return _error("approval_not_found", "Approval does not exist.", 404)
    if approval.get("workspace_id") != principal.workspace_id:
        return _error("approval_not_found", "Approval does not exist.", 404)
    gate = authorize(principal, "resolve_approval",
                     require_fresh=_requires_fresh(approval, require_fresh))
    if gate.get("error"):
        return gate
    if decision not in {"GRANT", "DENY"}:
        return _error("invalid_contract", "Decision must be GRANT or DENY.", 400)
    desired_status = "GRANTED" if decision == "GRANT" else "DENIED"
    if approval.get("status") != "PENDING":
        if (approval.get("status") == desired_status
                and approval.get("resolved_by_actor_id") == principal.actor_id):
            return {"status": "success", "duplicate": True,
                    "approval_id": approval_id,
                    "approval_status": approval["status"],
                    "subject_hash": approval["subject_hash"]}
        return _error("approval_terminal", "Approval is already resolved.")
    if str(approval.get("expires_at", "")) <= utc_now():
        return _error("approval_expired", "Approval has expired.")
    committed = await durable.compare_and_set(
        "approvals", approval_id, int(approval["version"]), {
            "status": desired_status,
            "resolved_by_actor_id": principal.actor_id,
            "decided_by_actor_id": principal.actor_id,
            "resolved_at": utc_now(), "updated_at": utc_now(),
            "resolution_membership_version": principal.membership_version,
        })
    if not committed:
        return _error("concurrency_conflict", "Approval changed concurrently.")
    return {"status": "success", "duplicate": False,
            "approval_id": approval_id,
            "approval_status": committed["status"],
            "subject_hash": committed["subject_hash"]}


async def claim_approval(*, principal: ActorPrincipal, approval_id: str,
                         run_id: str, policy_version_id: str,
                         action_kind: str, exact_action: dict[str, Any],
                         store: DurableStore | None = None,
                         require_fresh: bool = False) -> dict[str, Any]:
    durable = store or production_store()
    validated = await validate_approval_claim(
        principal=principal, approval_id=approval_id, run_id=run_id,
        policy_version_id=policy_version_id, action_kind=action_kind,
        exact_action=exact_action, store=durable, require_fresh=require_fresh)
    if validated.get("error"):
        return validated
    approval = validated["approval"]
    expected = validated["subject_hash"]
    committed = await durable.compare_and_set(
        "approvals", approval_id, int(approval["version"]), {
            "status": "CONSUMED", "consumed_by_actor_id": principal.actor_id,
            "consumed_at": utc_now(), "updated_at": utc_now(),
        })
    if not committed:
        return _error("concurrency_conflict", "Approval changed concurrently.")
    return {"status": "success", "approval_id": approval_id,
            "subject_hash": expected}


async def validate_approval_claim(
        *, principal: ActorPrincipal, approval_id: str, run_id: str,
        policy_version_id: str, action_kind: str, exact_action: dict[str, Any],
        store: DurableStore | None = None,
        require_fresh: bool = False) -> dict[str, Any]:
    """Validate an exact claim without mutating it.

    Consequence kernels use the returned version as a precondition in the
    same multi-document transaction that creates the PREPARED action.  Keeping
    validation here prevents each connector from inventing its own binding,
    role, freshness, and expiry rules.
    """
    durable = store or production_store()
    approval = await durable.get("approvals", approval_id)
    if not approval or approval.get("workspace_id") != principal.workspace_id:
        return _error("approval_not_found", "Approval does not exist.", 404)
    gate = authorize(principal, "resolve_approval",
                     require_fresh=_requires_fresh(approval, require_fresh))
    if gate.get("error"):
        return gate
    expected = canonical_hash({
        "schema_version": 1, "workspace_id": principal.workspace_id,
        "run_id": run_id, "role_id": approval["role_id"],
        "policy_version_id": policy_version_id,
        "action_kind": action_kind, "exact_action": exact_action,
    })
    if (approval.get("status") != "GRANTED" or approval.get("run_id") != run_id
            or approval.get("policy_version_id") != policy_version_id
            or approval.get("action_kind") != action_kind
            or approval.get("subject_hash") != expected):
        return _error("approval_binding_mismatch",
                      "Approval does not cover the current action.")
    # The TTL bounds consumption, not only resolution. A grant that has aged
    # out is no longer the decision the human made.
    if str(approval.get("expires_at", "")) <= utc_now():
        return _error("approval_expired", "Approval has expired.")
    return {"status": "success", "approval_id": approval_id,
            "subject_hash": expected, "approval": approval}
