"""Immutable Role Contract versions, impact manifests and activation guards."""

from __future__ import annotations

from typing import Any

from services.actor_identity import ActorPrincipal, WorkspaceRole, authorize
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import RoleContract, canonical_hash, stable_id, utc_now


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    del http_status
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


async def propose_policy(*, principal: ActorPrincipal, role_id: str,
                         contract: RoleContract, change_reason: str,
                         client_request_id: str,
                         store: DurableStore | None = None,
                         crash_point: str = "") -> dict[str, Any]:
    gate = authorize(principal, "prepare_role", role_id=role_id)
    if gate.get("error"):
        return gate
    durable = store or production_store()
    role = await durable.get("hiring_roles", role_id)
    if not role or role.get("workspace_id") != principal.workspace_id:
        return _error("role_not_found", "Role does not exist.", 404)
    existing_versions = await durable.list(
        "hiring_policy_versions", filters={"workspace_id": principal.workspace_id,
                                            "role_id": role_id}, limit=1000)
    sequence = max([int(item.get("sequence", 0)) for item in existing_versions] or [0]) + 1
    policy_id = stable_id("policy", role_id, client_request_id)
    payload = contract.model_dump(mode="json")
    policy_hash = canonical_hash(payload)
    parent_id = str(role.get("current_policy_version_id") or "") or None
    parent = await durable.get("hiring_policy_versions", parent_id) if parent_id else None
    diff = _diff_contract((parent or {}).get("contract"), payload)
    affected = await durable.list(
        "candidate_applications", filters={"workspace_id": principal.workspace_id,
                                            "role_id": role_id}, limit=1000)
    active = [item["candidate_application_id"] for item in affected
              if item.get("candidate_state") not in {"DECLINED", "WITHDRAWN", "CLOSED"}]
    completed = [item["candidate_application_id"] for item in affected
                 if item.get("candidate_state") in {"DECLINED", "WITHDRAWN", "CLOSED"}]
    now = utc_now()
    row = {
        "schema_version": 1, "policy_version_id": policy_id,
        "workspace_id": principal.workspace_id, "role_id": role_id,
        "sequence": sequence, "parent_version_id": parent_id,
        "canonical_hash": policy_hash, "contract": payload,
        "change_reason": change_reason[:1000], "materiality": diff["materiality"],
        "diff": diff["changes"], "status": "PREPARING_IMPACT",
        "approval_id": None, "approved_actor_id": None, "approved_at": None,
        "created_by_actor_id": principal.actor_id, "created_at": now,
        "updated_at": now, "version": 1,
        "synthetic": role.get("synthetic") is True,
        "synthetic_namespace": role.get("synthetic_namespace"),
        "fixture_id": role.get("fixture_id"),
    }
    impact_id = stable_id("impact", policy_id, policy_hash)
    impact = {
        "schema_version": 1, "impact_id": impact_id,
        "workspace_id": principal.workspace_id, "role_id": role_id,
        "policy_version_id": policy_id, "policy_hash": policy_hash,
        "enumeration_status": "COMPLETE", "active_candidate_ids": sorted(active),
        "completed_candidate_ids": sorted(completed),
        "stale_assessment_ids": sorted(str(item.get("current_assessment_id"))
                                       for item in affected
                                       if item.get("current_assessment_id")),
        "invalidated_approval_ids": [], "contacts_triggered": 0,
        "decisions_triggered": 0, "created_at": now,
        "synthetic": row["synthetic"],
        "synthetic_namespace": row["synthetic_namespace"],
        "fixture_id": row["fixture_id"], "version": 1,
    }
    row["impact_manifest_id"] = impact_id
    # Reserve the idempotency key with a non-activatable policy first. This
    # prevents a concurrent different payload from leaving an extra impact that
    # could poison exact activation, while retry can always finish enumeration.
    created = await durable.create("hiring_policy_versions", policy_id, row)
    if not created:
        existing = await durable.get("hiring_policy_versions", policy_id)
        if (not existing or existing.get("canonical_hash") != policy_hash
                or existing.get("role_id") != role_id
                or existing.get("created_by_actor_id") != principal.actor_id):
            return _error("idempotency_conflict", "Request id names another policy.")
    if crash_point == "AFTER_POLICY_RESERVATION":
        return _error("injected_crash",
                      "Synthetic crash injected before policy impact.", 503)
    existing_impact = await durable.get("hiring_policy_impacts", impact_id)
    if existing_impact and existing_impact.get("policy_hash") != policy_hash:
        return _error("impact_conflict", "Policy impact binding is invalid.")
    if not existing_impact:
        await durable.create("hiring_policy_impacts", impact_id, impact)
    current = await durable.get("hiring_policy_versions", policy_id)
    if current and current.get("status") == "PREPARING_IMPACT":
        advanced = await durable.compare_and_set(
            "hiring_policy_versions", policy_id, int(current["version"]), {
                "status": "PROPOSED", "updated_at": utc_now()})
        if advanced:
            current = advanced
    current = current or row
    if current.get("status") == "PREPARING_IMPACT":
        return _error("concurrency_conflict",
                      "Policy impact finalization remains recoverable.", 503)
    return {**current, "policy_status": current["status"],
            "status": "success", "duplicate": not created,
            "impact_manifest_id": impact_id}


async def approve_policy(*, principal: ActorPrincipal, role_id: str,
                         policy_version_id: str, expected_role_version: int,
                         approval_id: str, store: DurableStore | None = None,
                         crash_point: str = "") -> dict[str, Any]:
    if principal.role is not WorkspaceRole.OWNER:
        return _error("operation_forbidden", "Only the workspace owner can activate policy.", 403)
    durable = store or production_store()
    role = await durable.get("hiring_roles", role_id)
    policy = await durable.get("hiring_policy_versions", policy_version_id)
    if (not role or not policy or role.get("workspace_id") != principal.workspace_id
            or policy.get("workspace_id") != principal.workspace_id
            or policy.get("role_id") != role_id):
        return _error("role_not_found", "Role or policy does not exist.", 404)
    if policy.get("status") not in {"PROPOSED", "APPROVED"}:
        return _error("policy_not_ready",
                      "Policy impact must be finalized before approval.")
    impact = (await durable.list(
        "hiring_policy_impacts", filters={"policy_version_id": policy_version_id},
        limit=2))
    if len(impact) != 1 or impact[0].get("enumeration_status") != "COMPLETE":
        return _error("impact_incomplete", "Policy impact enumeration is incomplete.")
    approval = await durable.get("approvals", approval_id)
    pointer_matches = (
        role.get("current_policy_version_id") == policy_version_id
        and role.get("current_policy_hash") == policy["canonical_hash"])
    if (not approval or approval.get("status") not in {"GRANTED", "CONSUMED"}
            or approval.get("workspace_id") != principal.workspace_id
            or approval.get("role_id") != role_id
            or approval.get("policy_version_id") != policy_version_id
            or approval.get("action_kind") != "ACTIVATE_ROLE_POLICY"
            or approval.get("exact_action") != {
                "policy_version_id": policy_version_id,
                "policy_hash": policy["canonical_hash"],
            }
            or (approval.get("status") == "CONSUMED" and not pointer_matches)):
        return _error("approval_binding_mismatch", "Exact policy approval is required.")
    if pointer_matches:
        committed_role = role
    else:
        if int(role.get("version", 0)) != expected_role_version:
            return _error("version_conflict", "Role changed; reload policy impact.")
        committed_role = await durable.compare_and_set(
            "hiring_roles", role_id, expected_role_version, {
                "current_policy_version_id": policy_version_id,
                "current_policy_hash": policy["canonical_hash"],
                "updated_at": utc_now(),
            })
        if not committed_role:
            return _error("version_conflict", "Role changed; reload policy impact.")
        if crash_point == "AFTER_ROLE_POINTER":
            return _error("injected_crash",
                          "Synthetic crash injected after policy pointer.", 503)
    current_policy = await durable.get("hiring_policy_versions", policy_version_id)
    if current_policy and current_policy.get("status") != "APPROVED":
        await durable.compare_and_set(
            "hiring_policy_versions", policy_version_id, int(current_policy["version"]), {
                "status": "APPROVED", "approval_id": approval_id,
                "approved_actor_id": principal.actor_id, "approved_at": utc_now(),
                "updated_at": utc_now(),
            })
    current_approval = await durable.get("approvals", approval_id)
    if current_approval and current_approval.get("status") == "GRANTED":
        await durable.compare_and_set(
            "approvals", approval_id, int(current_approval["version"]), {
                "status": "CONSUMED", "consumed_by_actor_id": principal.actor_id,
                "consumed_at": utc_now(), "updated_at": utc_now(),
            })
    return {"status": "success", "duplicate": pointer_matches,
            "role_id": role_id,
            "policy_version_id": policy_version_id,
            "policy_hash": policy["canonical_hash"],
            "role_version": committed_role["version"]}


def assessment_is_current(role: dict[str, Any], assessment: dict[str, Any]) -> bool:
    return bool(role.get("current_policy_version_id")) and (
        assessment.get("policy_version_id") == role.get("current_policy_version_id")
        and assessment.get("policy_hash") == role.get("current_policy_hash"))


def _diff_contract(parent: dict[str, Any] | None,
                   current: dict[str, Any]) -> dict[str, Any]:
    if parent is None:
        return {"materiality": "INITIAL", "changes": [{"path": "$", "kind": "ADDED"}]}
    changes = []
    for key in sorted(set(parent) | set(current)):
        if parent.get(key) != current.get(key):
            changes.append({"path": key, "kind": "CHANGED",
                            "old_hash": canonical_hash({"value": parent.get(key)}),
                            "new_hash": canonical_hash({"value": current.get(key)})})
    material = "MATERIAL" if any(change["path"] in {
        "criteria", "interview_plan", "approved_reason_codes", "prohibited_criteria",
        "compensation_envelope", "location_envelope",
    } for change in changes) else "NON_MATERIAL"
    return {"materiality": material, "changes": changes}
