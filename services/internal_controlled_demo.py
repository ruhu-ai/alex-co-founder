"""Closed authority for the two-account Ruhu internal demonstration lane.

This is intentionally not a hiring executor and contains no generic mail or
Calendar adapter. It owns only policy/configuration/template validation so a
future reviewed provider adapter has one narrow, auditable input boundary.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.actor_identity import ActorPrincipal, authorize
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import canonical_hash, stable_id, utc_now

_POLICY = Path(__file__).resolve().parents[1] / "policies" / "hiring" / "internal-controlled-demo-policy.json"
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_KINDS = frozenset({
    "INTERNAL_DEMO_SEND_RECAP", "INTERNAL_DEMO_CREATE_CALENDAR_EVENT",
    "INTERNAL_DEMO_UPDATE_CALENDAR_EVENT", "INTERNAL_DEMO_CANCEL_CALENDAR_EVENT",
})


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    del http_status
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


@lru_cache(maxsize=1)
def policy() -> dict[str, Any]:
    """Load the independent, closed internal-demo policy."""
    loaded = json.loads(_POLICY.read_text(encoding="utf-8"))
    if (loaded.get("schema_version") != 2
            or loaded.get("status") != "INTERNAL_DEMO_ONLY"
            or loaded.get("activation", {}).get("real_candidate_processing") is not False
            or set(loaded.get("effect_kinds") or []) != _KINDS
            or not isinstance(loaded.get("fixture"), dict)):
        raise RuntimeError("unreviewed internal controlled-demo policy")
    return loaded


def configured_accounts() -> dict[str, str] | None:
    """Return only deployment-pinned ordinary accounts and opaque subjects."""
    try:
        accounts = policy()["accounts"]
    except (RuntimeError, KeyError):
        return None
    alex_subject = os.environ.get("HIRING_INTERNAL_DEMO_ALEX_SUBJECT_SHA256", "").strip()
    founder_subject = os.environ.get("HIRING_INTERNAL_DEMO_FOUNDER_SUBJECT_SHA256", "").strip()
    if not _HASH.fullmatch(alex_subject) or not _HASH.fullmatch(founder_subject):
        return None
    return {"alex_address": str(accounts["alex_sender_address"]),
            "founder_address": str(accounts["founder_recipient_address"]),
            "alex_subject_hash": alex_subject,
            "founder_subject_hash": founder_subject}


def require_enabled() -> dict[str, Any]:
    """Fail closed until the separate internal-demo deployment flag is set."""
    if os.environ.get("HIRING_ENABLE_INTERNAL_CONTROLLED_DEMO") != "1":
        return _error("internal_demo_disabled", "Internal controlled-demo effects are disabled.", 403)
    if not configured_accounts():
        return _error("internal_demo_not_configured",
                      "Internal controlled-demo account pins are not configured.", 503)
    return {"status": "success"}


def exact_template(action_kind: str) -> dict[str, Any] | dict[str, str]:
    """Return the immutable product-demo content; no caller text is accepted."""
    if action_kind not in _KINDS:
        return _error("internal_demo_action_invalid", "Internal demo action is not allowlisted.", 400)
    templates = {
        "INTERNAL_DEMO_SEND_RECAP": {
            "template_version": "internal-demo-recap-v1",
            "subject": "[LIVE INTERNAL DEMO] Ruhu hiring workflow recap",
            "body": ("This is a bounded internal product demonstration between Ruhu-controlled "
                     "accounts. No candidate data, hiring decision, or external recipient is involved."),
        },
        "INTERNAL_DEMO_CREATE_CALENDAR_EVENT": {
            "template_version": "internal-demo-calendar-v1",
            "summary": "[LIVE INTERNAL DEMO] Ruhu hiring workflow",
            "description": ("Bounded product demonstration between Ruhu-controlled accounts. "
                            "No candidate participation or hiring decision."),
            "duration_minutes": "30", "timezone": "Africa/Lagos",
        },
    }
    if action_kind in templates:
        return templates[action_kind]
    return {"template_version": "internal-demo-calendar-v1"}


def _calendar_event_id(demo_run_id: str) -> str:
    """A Google-calendar-safe, deterministic reconciliation handle."""
    return "idemo" + canonical_hash({"run": demo_run_id})[7:31]


def build_exact_action(action_kind: str, *, demo_run_id: str,
                       calendar_start_at: str = "") -> dict[str, Any]:
    """Build a non-customizable action subject for the reviewed executor."""
    gate = require_enabled()
    if gate.get("error"):
        return gate
    template = exact_template(action_kind)
    if "error" in template:
        return template
    accounts = configured_accounts()
    assert accounts is not None  # established by require_enabled above
    action = {"schema_version": 1, "action_kind": action_kind,
              "demo_run_id": demo_run_id, "policy_id": policy()["policy_id"],
              "template": template, "alex_address": accounts["alex_address"],
              "founder_address": accounts["founder_address"],
              "alex_subject_hash": accounts["alex_subject_hash"],
              "founder_subject_hash": accounts["founder_subject_hash"]}
    if action_kind in {"INTERNAL_DEMO_CREATE_CALENDAR_EVENT",
                       "INTERNAL_DEMO_UPDATE_CALENDAR_EVENT",
                       "INTERNAL_DEMO_CANCEL_CALENDAR_EVENT"}:
        if not calendar_start_at:
            return _error("internal_demo_schedule_missing",
                          "The fixed internal-demo meeting time is unavailable.", 503)
        try:
            start = datetime.fromisoformat(calendar_start_at)
            if start.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError:
            return _error("internal_demo_schedule_invalid",
                          "The fixed internal-demo meeting time is invalid.", 503)
        end = start + timedelta(minutes=30)
        action["calendar_event_id"] = _calendar_event_id(demo_run_id)
        action["calendar"] = {"start_at": start.isoformat(), "end_at": end.isoformat(),
                              "timezone": "Africa/Lagos"}
    return {"status": "success", "exact_action": action,
            "subject_hash": canonical_hash(action),
            "action_id": stable_id("intdemo", demo_run_id, canonical_hash(action))}


class InternalControlledDemoService:
    """Own the closed run and exact-approval truth for the live internal demo.

    This service intentionally has no Gmail or Calendar client. Provider code
    can receive only a durable, approved exact action produced here.
    """

    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    @staticmethod
    def _founder(principal: ActorPrincipal, *, fresh: bool = False) -> dict[str, Any]:
        return authorize(principal, "resolve_approval", require_fresh=fresh)

    async def create_run(self, *, principal: ActorPrincipal, client_request_id: str,
                         role_id: str, ttl_minutes: int = 120) -> dict[str, Any]:
        gate = self._founder(principal, fresh=True)
        if gate.get("error"):
            return gate
        enabled = require_enabled()
        if enabled.get("error"):
            return enabled
        role = await self.store.get("hiring_roles", role_id)
        if (not role or role.get("workspace_id") != principal.workspace_id
                or role.get("synthetic") is not True
                or role.get("fixture_id") != "fixture_ruhu_fde_walkthrough"
                or not role.get("current_policy_version_id")
                or role.get("role_state") != "PUBLISHED"):
            return _error("internal_demo_role_not_ready",
                          "Use a published synthetic Ruhu FDE role for the internal demo.", 404)
        role_gate = authorize(principal, "read_role")
        if role_gate.get("error"):
            return role_gate
        if not 5 <= ttl_minutes <= 240:
            return _error("invalid_contract", "Demo expiry must be between 5 minutes and 4 hours.", 400)
        fixture = policy()["fixture"]
        demo_run_id = stable_id("idemo", principal.workspace_id, client_request_id)
        now = datetime.now(timezone.utc)
        # The only calendar time in this lane is generated by the server.  It
        # is never supplied by a caller or model, and is intentionally well in
        # the future so an approval is meaningful at execution time.
        calendar_start = (now + timedelta(hours=2)).replace(
            minute=0, second=0, microsecond=0)
        row = {"schema_version": 1, "demo_run_id": demo_run_id,
               "workspace_id": principal.workspace_id, "owner_actor_id": principal.actor_id,
               "state": "ACTIVE", "internal_demo": True,
               "role_id": role_id,
               "demo_namespace": "internal_demo_ruhu", "fixture_id": fixture["fixture_id"],
               "policy_id": policy()["policy_id"], "created_at": now.isoformat(),
               "calendar_start_at": calendar_start.isoformat(),
               "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
               "updated_at": now.isoformat(), "version": 1}
        created = await self.store.create("internal_demo_runs", demo_run_id, row)
        existing = row if created else await self.store.get("internal_demo_runs", demo_run_id)
        if not existing or existing.get("workspace_id") != principal.workspace_id:
            return _error("idempotency_conflict", "Request id names another demo run.")
        return {"status": "success", "duplicate": not created, "demo_run_id": demo_run_id,
                "demo_run": existing}

    async def request_approval(self, *, principal: ActorPrincipal, demo_run_id: str,
                               action_kind: str, client_request_id: str) -> dict[str, Any]:
        gate = self._founder(principal, fresh=True)
        if gate.get("error"):
            return gate
        run = await self._active_run(principal, demo_run_id)
        if run.get("error"):
            return run
        built = build_exact_action(
            action_kind, demo_run_id=demo_run_id,
            calendar_start_at=str(run["demo_run"].get("calendar_start_at") or ""))
        if built.get("error"):
            return built
        from services.capability_registry import require_controlled_action

        try:
            capability = require_controlled_action(
                action_kind, "internal_demo_google")
        except ValueError:
            return _error("capability_disabled",
                          "This internal-demo action is not reviewed.", 403)
        approval_id = stable_id("idemoapproval", principal.workspace_id, client_request_id)
        now = datetime.now(timezone.utc)
        row = {"schema_version": 2, "approval_id": approval_id,
               "approval_domain": "INTERNAL_CONTROLLED_DEMO", "workspace_id": principal.workspace_id,
               "founder_id": principal.workspace_id,
               "demo_run_id": demo_run_id, "action_kind": action_kind,
               "run_id": demo_run_id,
               "plan_hash": canonical_hash({
                   "policy_id": policy()["policy_id"],
                   "demo_run_id": demo_run_id}),
               "step_id": action_kind,
               "capability_id": capability.capability_id,
               "capability_version": capability.semantic_version,
               "connector_id": "internal_demo_google",
               "connector_binding_version": "pinned-two-account-v1",
               "policy_id": policy()["policy_id"], "policy_version": "1",
               "domain_ref": demo_run_id, "domain_version": 1,
               "target_hash": canonical_hash({
                   "alex": built["exact_action"].get("alex_subject_hash"),
                   "founder": built["exact_action"].get(
                       "founder_subject_hash")}),
               "normalized_payload_hash": canonical_hash(
                   built["exact_action"]),
               "exact_action": built["exact_action"], "subject_hash": built["subject_hash"],
               "status": "PENDING", "requested_by_actor_id": principal.actor_id,
               "approving_actor_requirement": "INTERACTIVE_MEMBER",
               "decided_by_actor_id": None,
               "claim_id": None, "claimed_action_id": None,
               "claimed_at": None, "consumed_at": None,
               "voided_at": None, "void_reason": None,
               "expires_at": min(str(run["demo_run"]["expires_at"]),
                                 (now + timedelta(minutes=30)).isoformat()),
               "created_at": now.isoformat(), "updated_at": now.isoformat(), "version": 1,
               "internal_demo": True, "fixture_id": run["demo_run"]["fixture_id"]}
        created = await self.store.create("approvals", approval_id, row)
        existing = row if created else await self.store.get("approvals", approval_id)
        if not existing or existing.get("subject_hash") != built["subject_hash"]:
            return _error("idempotency_conflict", "Request id names another approval.")
        return {"status": "success", "duplicate": not created, "approval_id": approval_id,
                "approval_status": existing["status"], "exact_action": existing["exact_action"]}

    async def resolve_approval(self, *, principal: ActorPrincipal, approval_id: str,
                               decision: str) -> dict[str, Any]:
        gate = self._founder(principal, fresh=True)
        if gate.get("error"):
            return gate
        approval = await self.store.get("approvals", approval_id)
        if (not approval or approval.get("workspace_id") != principal.workspace_id
                or approval.get("approval_domain") != "INTERNAL_CONTROLLED_DEMO"):
            return _error("approval_not_found", "Internal-demo approval does not exist.", 404)
        if decision not in {"GRANT", "DENY"}:
            return _error("invalid_contract", "Decision must be GRANT or DENY.", 400)
        if approval.get("status") != "PENDING":
            return _error("approval_terminal", "Approval is already resolved.")
        if str(approval.get("expires_at") or "") <= utc_now():
            return _error("approval_expired", "Approval has expired.")
        run = await self._active_run(principal, str(approval["demo_run_id"]))
        if run.get("error"):
            return run
        committed = await self.store.compare_and_set(
            "approvals", approval_id, int(approval["version"]),
            {"status": "GRANTED" if decision == "GRANT" else "DENIED",
             "resolved_by_actor_id": principal.actor_id,
             "decided_by_actor_id": principal.actor_id,
             "resolved_at": utc_now(),
             "updated_at": utc_now()})
        if not committed:
            return _error("concurrency_conflict", "Approval changed concurrently.")
        return {"status": "success", "approval_id": approval_id,
                "approval_status": committed["status"]}

    async def _active_run(self, principal: ActorPrincipal, demo_run_id: str) -> dict[str, Any]:
        enabled = require_enabled()
        if enabled.get("error"):
            return enabled
        run = await self.store.get("internal_demo_runs", demo_run_id)
        if (not run or run.get("workspace_id") != principal.workspace_id
                or run.get("state") != "ACTIVE" or run.get("internal_demo") is not True
                or run.get("policy_id") != policy()["policy_id"]):
            return _error("demo_run_not_active", "Internal demo run is not active.", 404)
        if str(run.get("expires_at") or "") <= utc_now():
            return _error("demo_run_expired", "Internal demo run has expired.")
        return {"status": "success", "demo_run": run}

    async def reset(self, *, principal: ActorPrincipal, demo_run_id: str) -> dict[str, Any]:
        """Revoke a demo run without deleting provider mail or unrelated data."""
        gate = self._founder(principal, fresh=True)
        if gate.get("error"):
            return gate
        run = await self.store.get("internal_demo_runs", demo_run_id)
        if (not run or run.get("workspace_id") != principal.workspace_id
                or run.get("internal_demo") is not True):
            return _error("demo_run_not_found", "Internal demo run does not exist.", 404)
        if run.get("state") == "RESET":
            return {"status": "success", "duplicate": True, "demo_run_id": demo_run_id,
                    "reset_note": "Provider mail is retained; no mail was deleted."}
        if run.get("state") != "ACTIVE":
            return _error("demo_run_not_active", "Internal demo run cannot be reset.")
        actions = await self.store.list(
            "external_actions",
            filters={"demo_run_id": demo_run_id,
                     "action_domain": "INTERNAL_CONTROLLED_DEMO"}, limit=100)
        created_invite = any(
            row.get("action_kind") == "INTERNAL_DEMO_CREATE_CALENDAR_EVENT"
            and row.get("status") == "SUCCEEDED" for row in actions)
        cancelled_invite = any(
            row.get("action_kind") == "INTERNAL_DEMO_CANCEL_CALENDAR_EVENT"
            and row.get("status") == "SUCCEEDED" for row in actions)
        if created_invite and not cancelled_invite:
            return _error("calendar_cancellation_required",
                          "Approve and execute the exact calendar cancellation before reset.")
        committed = await self.store.compare_and_set(
            "internal_demo_runs", demo_run_id, int(run["version"]),
            {"state": "RESET", "reset_at": utc_now(), "reset_by_actor_id": principal.actor_id,
             "updated_at": utc_now()})
        if not committed:
            return _error("concurrency_conflict", "Demo run changed concurrently.")
        return {"status": "success", "duplicate": False, "demo_run_id": demo_run_id,
                "reset_note": "Provider mail is retained; the internal calendar invite is cancelled."}
