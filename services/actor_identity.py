"""Server-derived human identity and hiring authorization."""

from __future__ import annotations

import os
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
    FOUNDER = "FOUNDER"


@dataclass(frozen=True)
class ActorPrincipal:
    actor_id: str
    workspace_id: str
    role: WorkspaceRole
    session_auth_time: int
    membership_version: int
    # Legacy assignment-shaped fields remain empty compatibility metadata.
    # They are not product roles or a second authority system: every active
    # authenticated application user is the Founder.
    role_grants: frozenset[str] = frozenset()
    candidate_assignments: frozenset[str] = frozenset()
    interview_assignments: frozenset[str] = frozenset()
    principal_kind: str = "INTERACTIVE"
    membership_id: str = ""

    def audit_fields(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "workspace_id": self.workspace_id,
            "actor_role": self.role.value,
            "membership_version": self.membership_version,
            "membership_id": self.membership_id,
            "principal_kind": self.principal_kind,
        }


def _error(code: str, message: str, http_status: int = 403) -> dict[str, Any]:
    del http_status
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


async def resolve_actor_from_claims(
        claims: dict[str, Any] | None, *, store: DurableStore | None = None,
        workspace_id: str = "") -> ActorPrincipal | dict[str, Any]:
    """Resolve current membership from signed session claims.

    The cookie proves authentication only. The Founder role is always read from
    the current membership record so revocation is immediate.
    A verified subject is required for every interactive principal. Some OIDC
    providers omit ``auth_time`` unless step-up was explicitly requested; that
    session may use ordinary Founder controls, but receives an epoch freshness
    value so every ``require_fresh`` operation still fails closed.
    """
    subject = str((claims or {}).get("sub") or "")
    auth_time = (claims or {}).get("auth_time")
    if not subject:
        return _error("hiring_auth_required",
                      "Sign in with a verified account to use this workspace.", 401)
    durable = store or production_store()
    filters: dict[str, Any] = {"auth_subject": subject, "status": "ACTIVE"}
    if workspace_id:
        filters["workspace_id"] = workspace_id
    matches = await durable.list("workspace_members", filters=filters, limit=2)
    if len(matches) > 1 and not workspace_id:
        return _error(
            "workspace_selection_required",
            "Select an active workspace before continuing.", 409)
    if len(matches) != 1:
        return _error("membership_missing",
                      "No active workspace membership authorizes this request.")
    member = matches[0]
    try:
        role = WorkspaceRole(str(member["role"]))
        return ActorPrincipal(
            actor_id=str(member["actor_id"]),
            workspace_id=str(member["workspace_id"]),
            role=role,
            session_auth_time=(auth_time if isinstance(auth_time, int) else 0),
            membership_version=int(member.get("version", 1)),
            role_grants=frozenset(str(item) for item in member.get("role_grants", [])),
            candidate_assignments=frozenset(
                str(item) for item in member.get("candidate_assignments", [])),
            interview_assignments=frozenset(
                str(item) for item in member.get("interview_assignments", [])),
            principal_kind="INTERACTIVE",
            membership_id=str(member.get("membership_id") or member.get("id") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return _error("invalid_membership", "Workspace membership is invalid.")


async def resolve_seeded_principal(
        seed_id: str, *, workspace_id: str,
        store: DurableStore | None = None) -> ActorPrincipal | dict[str, Any]:
    """Resolve an explicitly local/eval identity through membership records.

    Seeded identities are never accepted in Cloud Run and cannot be silently
    promoted to an interactive principal by supplying a familiar user id.
    """
    if os.environ.get("K_SERVICE"):
        return _error("seeded_identity_forbidden",
                      "Seeded identities are local/evaluation only.", 401)
    if seed_id not in {"user", "eval_founder", "founder", "demo_founder"}:
        return _error("seeded_identity_forbidden",
                      "Seeded identity is not registered.", 401)
    durable = store or production_store()
    matches = await durable.list(
        "workspace_members",
        filters={"auth_subject": f"seeded:{seed_id}",
                 "workspace_id": workspace_id, "status": "ACTIVE",
                 "local_only": True}, limit=2)
    if len(matches) != 1:
        return _error("membership_missing",
                      "Seeded workspace membership is missing.")
    member = matches[0]
    try:
        role = WorkspaceRole(str(member["role"]))
        return ActorPrincipal(
            actor_id=str(member["actor_id"]),
            workspace_id=str(member["workspace_id"]),
            role=role,
            session_auth_time=int(time.time()),
            membership_version=int(member.get("version", 1)),
            role_grants=frozenset(str(item) for item in member.get("role_grants", [])),
            candidate_assignments=frozenset(
                str(item) for item in member.get("candidate_assignments", [])),
            interview_assignments=frozenset(
                str(item) for item in member.get("interview_assignments", [])),
            principal_kind="SEEDED",
            membership_id=str(member.get("membership_id") or member.get("id") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return _error("invalid_membership", "Workspace membership is invalid.")


def authorize(principal: ActorPrincipal, operation: str, *, require_fresh: bool = False,
              now: int | None = None) -> dict[str, Any]:
    """Code-owned founder membership and freshness authorization."""
    if (operation in {"resolve_approval", "human_decision", "membership_change"}
            and principal.principal_kind != "INTERACTIVE"):
        return _error(
            "interactive_human_required",
            "This decision requires a signed-in interactive human.")
    if require_fresh:
        maximum = int(hiring_activation.policy()["auth_freshness"]["max_age_seconds"])
        age = int(now if now is not None else time.time()) - principal.session_auth_time
        # A small negative age is ordinary clock skew between the identity
        # provider and this server; a large one is a forged or broken claim.
        if age < -_MAX_CLOCK_SKEW_SECONDS or age > maximum:
            return _error("step_up_required",
                          "Recent sign-in is required for this operation.", 401)
    if operation == "membership_change":
        return ({"status": "success"} if principal.role is WorkspaceRole.FOUNDER
                else _error("operation_forbidden",
                            "Only the workspace founder may change membership."))
    # FOUNDER is the sole human membership role. Hiring records remain
    # workspace-scoped, but per-user role/candidate grants are not a second
    # sign-in authority system in this small shared application.
    allowed = {
        "read_role", "read_candidate", "prepare_role", "record_publication",
        "human_decision", "resolve_approval", "submit_scorecard",
    }
    if operation not in allowed:
        return _error("operation_forbidden", "Membership role cannot perform this operation.")
    return {"status": "success"}


async def create_membership(*, actor_id: str, workspace_id: str,
                            auth_subject: str, role: WorkspaceRole,
                            created_by: str, store: DurableStore | None = None,
                            synthetic: bool = True,
                            local_only: bool = False,
                            client_request_id: str = "",
                            ) -> dict[str, Any]:
    """Create an immutable-id membership projection for bootstrap/admin code."""
    durable = store or production_store()
    membership_id = stable_id("membership", workspace_id, actor_id)
    row = {
        "schema_version": 2, "membership_id": membership_id,
        "actor_id": actor_id, "workspace_id": workspace_id,
        "auth_subject": auth_subject, "role": role.value, "status": "ACTIVE",
        "role_grants": [], "candidate_assignments": [], "interview_assignments": [],
        "created_by": created_by, "version": 1, "synthetic": synthetic,
        "local_only": bool(local_only),
    }
    if client_request_id:
        row["client_request_id"] = client_request_id
    if not await durable.create("workspace_members", membership_id, row):
        return _error("version_conflict", "Membership already exists.", 409)
    audit_id = stable_id("audit", workspace_id, "membership_create", actor_id)
    await durable.create("audit", audit_id, {
        "schema_version": 2, "audit_id": audit_id,
        "founder_id": workspace_id, "workspace_id": workspace_id,
        "actor": created_by, "actor_id": created_by,
        "action": "workspace_membership.create",
        "target": f"workspace_members/{membership_id}", "result": "success",
        "detail": f"role={role.value} status=ACTIVE",
        "client_request_id": client_request_id or None,
        "created_at": utc_now(), "version": 1,
    })
    return {**row, "membership_status": row["status"], "status": "success"}


async def change_membership(*, principal: ActorPrincipal, actor_id: str,
                            expected_version: int,
                            status: str,
                            client_request_id: str,
                            store: DurableStore | None = None) -> dict[str, Any]:
    """Versioned, fresh-founder-only membership mutation with append-only audit."""
    gate = authorize(principal, "membership_change", require_fresh=True)
    if gate.get("error"):
        return gate
    if principal.role is not WorkspaceRole.FOUNDER or status not in {"ACTIVE", "REVOKED"}:
        return _error("operation_forbidden", "Only a fresh founder may change membership.")
    durable = store or production_store()
    matches = await durable.list(
        "workspace_members",
        filters={"actor_id": actor_id,
                 "workspace_id": principal.workspace_id}, limit=2)
    if len(matches) != 1:
        return _error("membership_missing", "Membership does not exist.", 404)
    member = matches[0]
    membership_id = str(member.get("membership_id") or member.get("id") or "")
    committed = await durable.compare_and_set(
        "workspace_members", membership_id, expected_version, {
            "role": WorkspaceRole.FOUNDER.value,
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
        "detail": (
            f"role={WorkspaceRole.FOUNDER.value} status={status} "
            f"version={committed['version']}"),
        "idempotency_key": client_request_id,
        "created_at": utc_now(), "version": 1,
    })
    return {"status": "success", "actor_id": actor_id,
            "membership_status": status,
            "membership_version": committed["version"]}
