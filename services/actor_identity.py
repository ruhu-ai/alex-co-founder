"""Server-derived human identity and hiring authorization."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from services import hiring_activation
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import stable_id, utc_now

# Tolerated difference between the identity provider's clock and this server's
# when judging session freshness.
_MAX_CLOCK_SKEW_SECONDS = 120


class WorkspaceRole(str, Enum):
    OWNER = "OWNER"
    HIRING_MANAGER = "HIRING_MANAGER"
    INTERVIEWER = "INTERVIEWER"
    OBSERVER = "OBSERVER"


@dataclass(frozen=True)
class ActorPrincipal:
    actor_id: str
    workspace_id: str
    role: WorkspaceRole
    role_grants: frozenset[str]
    candidate_assignments: frozenset[str]
    interview_assignments: frozenset[str]
    session_auth_time: int
    membership_version: int

    def audit_fields(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "workspace_id": self.workspace_id,
            "actor_role": self.role.value,
            "membership_version": self.membership_version,
        }


def _error(code: str, message: str, http_status: int = 403) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}


async def resolve_actor_from_claims(
        claims: dict[str, Any] | None, *, store: DurableStore | None = None,
        workspace_id: str = "") -> ActorPrincipal | dict[str, Any]:
    """Resolve current membership from signed session claims.

    The cookie proves authentication only. Role, grants and assignments are
    always read from the current membership record so revocation is immediate.
    Legacy founder-token/FOUNDER_ID requests have no subject/auth_time and are
    intentionally incompatible with hiring.
    """
    subject = str((claims or {}).get("sub") or "")
    auth_time = (claims or {}).get("auth_time")
    if not subject or not isinstance(auth_time, int):
        return _error("hiring_auth_required",
                      "Sign in with a verified account to use hiring.", 401)
    durable = store or production_store()
    filters: dict[str, Any] = {"auth_subject": subject, "status": "ACTIVE"}
    if workspace_id:
        filters["workspace_id"] = workspace_id
    matches = await durable.list("workspace_members", filters=filters, limit=2)
    if len(matches) != 1:
        return _error("membership_missing",
                      "No unique active workspace membership authorizes this request.")
    member = matches[0]
    try:
        role = WorkspaceRole(str(member["role"]))
        return ActorPrincipal(
            actor_id=str(member["actor_id"]),
            workspace_id=str(member["workspace_id"]),
            role=role,
            role_grants=frozenset(str(item) for item in member.get("role_grants", [])),
            candidate_assignments=frozenset(
                str(item) for item in member.get("candidate_assignments", [])),
            interview_assignments=frozenset(
                str(item) for item in member.get("interview_assignments", [])),
            session_auth_time=auth_time,
            membership_version=int(member.get("version", 1)),
        )
    except (KeyError, TypeError, ValueError):
        return _error("invalid_membership", "Workspace membership is invalid.")


def authorize(principal: ActorPrincipal, operation: str, *, role_id: str = "",
              candidate_application_id: str = "", require_fresh: bool = False,
              now: int | None = None) -> dict[str, Any]:
    """Code-owned role/assignment/freshness authorization."""
    if require_fresh:
        maximum = int(hiring_activation.policy()["auth_freshness"]["max_age_seconds"])
        age = int(now if now is not None else time.time()) - principal.session_auth_time
        # A small negative age is ordinary clock skew between the identity
        # provider and this server; a large one is a forged or broken claim.
        if age < -_MAX_CLOCK_SKEW_SECONDS or age > maximum:
            return _error("step_up_required",
                          "Recent sign-in is required for this operation.", 401)
    if principal.role is WorkspaceRole.OWNER:
        return {"status": "success"}
    if operation in {"read_role", "read_candidate"} and principal.role is WorkspaceRole.OBSERVER:
        if candidate_application_id:
            return _error("assignment_required", "Candidate assignment required.")
        return ({"status": "success"} if role_id in principal.role_grants
                else _error("role_grant_required", "Role grant required."))
    if role_id and role_id not in principal.role_grants:
        return _error("role_grant_required", "Role grant required.")
    if candidate_application_id and principal.role is not WorkspaceRole.HIRING_MANAGER:
        if candidate_application_id not in principal.candidate_assignments:
            return _error("assignment_required", "Candidate assignment required.")
    allowed = {
        WorkspaceRole.HIRING_MANAGER: {
            "read_role", "read_candidate", "prepare_role", "record_publication",
            "human_decision", "resolve_approval",
        },
        WorkspaceRole.INTERVIEWER: {"read_candidate", "submit_scorecard"},
        WorkspaceRole.OBSERVER: {"read_role"},
    }
    if operation not in allowed.get(principal.role, set()):
        return _error("operation_forbidden", "Membership role cannot perform this operation.")
    return {"status": "success"}


async def create_membership(*, actor_id: str, workspace_id: str,
                            auth_subject: str, role: WorkspaceRole,
                            created_by: str, store: DurableStore | None = None,
                            synthetic: bool = True) -> dict[str, Any]:
    """Create an immutable-id membership projection for bootstrap/admin code."""
    durable = store or production_store()
    row = {
        "schema_version": 1, "actor_id": actor_id, "workspace_id": workspace_id,
        "auth_subject": auth_subject, "role": role.value, "status": "ACTIVE",
        "role_grants": [], "candidate_assignments": [], "interview_assignments": [],
        "created_by": created_by, "version": 1, "synthetic": synthetic,
    }
    if not await durable.create("workspace_members", actor_id, row):
        return _error("version_conflict", "Membership already exists.", 409)
    audit_id = stable_id("audit", workspace_id, "membership_create", actor_id)
    await durable.create("audit", audit_id, {
        "schema_version": 2, "audit_id": audit_id,
        "founder_id": workspace_id, "workspace_id": workspace_id,
        "actor": created_by, "actor_id": created_by,
        "action": "workspace_membership.create",
        "target": f"workspace_members/{actor_id}", "result": "success",
        "detail": f"role={role.value} status=ACTIVE",
        "created_at": utc_now(), "version": 1,
    })
    return {**row, "membership_status": row["status"], "status": "success"}


async def change_membership(*, principal: ActorPrincipal, actor_id: str,
                            expected_version: int, role: WorkspaceRole,
                            role_grants: list[str],
                            candidate_assignments: list[str],
                            interview_assignments: list[str], status: str,
                            client_request_id: str,
                            store: DurableStore | None = None) -> dict[str, Any]:
    """Versioned, fresh-owner-only membership mutation with append-only audit."""
    gate = authorize(principal, "membership_change", require_fresh=True)
    if gate.get("error"):
        return gate
    if principal.role is not WorkspaceRole.OWNER or status not in {"ACTIVE", "REVOKED"}:
        return _error("operation_forbidden", "Only a fresh owner may change membership.")
    durable = store or production_store()
    member = await durable.get("workspace_members", actor_id)
    if not member or member.get("workspace_id") != principal.workspace_id:
        return _error("membership_missing", "Membership does not exist.", 404)
    committed = await durable.compare_and_set(
        "workspace_members", actor_id, expected_version, {
            "role": role.value, "role_grants": sorted(set(role_grants)),
            "candidate_assignments": sorted(set(candidate_assignments)),
            "interview_assignments": sorted(set(interview_assignments)),
            "status": status, "updated_by_actor_id": principal.actor_id,
            "updated_at": utc_now(),
        })
    if not committed:
        return _error("version_conflict", "Membership changed; reload it.")
    audit_id = stable_id("audit", principal.workspace_id, "membership_change",
                         actor_id, client_request_id)
    await durable.create("audit", audit_id, {
        "schema_version": 2, "audit_id": audit_id,
        "founder_id": principal.workspace_id,
        "workspace_id": principal.workspace_id, "actor": principal.actor_id,
        "actor_id": principal.actor_id, "action": "workspace_membership.change",
        "target": f"workspace_members/{actor_id}", "result": "success",
        "detail": f"role={role.value} status={status} version={committed['version']}",
        "idempotency_key": client_request_id,
        "created_at": utc_now(), "version": 1,
    })
    return {"status": "success", "actor_id": actor_id,
            "membership_status": status,
            "membership_version": committed["version"]}
