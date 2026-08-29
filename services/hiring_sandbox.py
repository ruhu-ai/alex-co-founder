"""Closed H4S sandbox policy and durable provisioning boundary.

This module deliberately contains no Gmail, Calendar, browser, or generic-agent
tool invocation.  It creates only the server-owned records that later H4S
effect adapters must verify again at execution time.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from services import hiring_activation
from services.actor_identity import ActorPrincipal, authorize
from services.durable_store import DurableStore
from services.hiring_approval_service import request_approval
from services.hiring_contracts import canonical_hash, stable_id, utc_now
from services.hiring_sandbox_config import configured_test_connector, configured_test_destination

_POLICY = Path(__file__).resolve().parents[1] / "policies" / "hiring" / "h4s-sandbox-policy.json"
_DESTINATION_KINDS = {"TEST_CANDIDATE", "TEST_FOUNDER", "TEST_CALENDAR_ATTENDEE"}
_SANDBOX_STATES = {"SANDBOX_PROVISIONED", "SANDBOX_CLOSED"}


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    del http_status
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


@lru_cache(maxsize=1)
def policy() -> dict[str, Any]:
    """Load the independent, closed H4S policy; never mutate the H0 policy."""
    loaded = json.loads(_POLICY.read_text(encoding="utf-8"))
    if (loaded.get("schema_version") != 1
            or loaded.get("status") != "SANDBOX_ONLY"
            or loaded.get("activation", {}).get("real_candidate_processing") is not False
            or loaded.get("activation", {}).get("live_connector_selection") is not False):
        raise RuntimeError("unreviewed H4S sandbox policy")
    return loaded


def require_sandbox(record: dict[str, Any], *, require_enabled: bool = False) -> dict[str, Any]:
    """Fail closed unless RECORD is a valid active synthetic H4S sandbox row."""
    h0 = hiring_activation.require_synthetic(record)
    if h0.get("error"):
        return h0
    if record.get("state") not in _SANDBOX_STATES:
        return _error("sandbox_state_invalid", "Sandbox state is invalid.")
    if record.get("state") != "SANDBOX_PROVISIONED":
        return _error("sandbox_not_active", "Sandbox is not active.")
    if not record.get("sandbox_run_id") or not record.get("workspace_id"):
        return _error("sandbox_contract_invalid", "Sandbox identity is incomplete.")
    if require_enabled and os.environ.get("HIRING_ENABLE_H4_SANDBOX") != "1":
        return _error("sandbox_not_authorized", "H4S effects are disabled by deployment.")
    try:
        loaded = policy()
    except RuntimeError:
        return _error("sandbox_policy_invalid", "H4S policy is unavailable.", 503)
    if loaded["status"] != "SANDBOX_ONLY":
        return _error("sandbox_not_authorized", "H4S policy does not authorize sandbox use.")
    return {"status": "success"}


def _normalize_test_address(address: str) -> str:
    normalized = address.strip().casefold()
    if (not normalized or len(normalized) > 320 or normalized.count("@") != 1
            or any(char.isspace() for char in normalized)):
        return ""
    local, domain = normalized.split("@", 1)
    return normalized if local and "." in domain else ""


class HiringSandboxService:
    """Server-side creation, lookup, and closure for H4S-only records."""

    def __init__(self, store: DurableStore):
        self.store = store

    @staticmethod
    def _provisioner(principal: ActorPrincipal) -> dict[str, Any]:
        """Sandbox creation remains founder-authored and separately gated."""
        del principal
        return {"status": "success"}

    async def create(
            self, *, principal: ActorPrincipal, role_id: str, fixture_id: str,
            synthetic_namespace: str, connector_binding_ids: list[str],
            client_request_id: str) -> dict[str, Any]:
        gate = self._provisioner(principal)
        if gate.get("error"):
            return gate
        gate = authorize(principal, "read_role")
        if gate.get("error"):
            return gate
        guard = {"synthetic": True, "fixture_id": fixture_id,
                 "synthetic_namespace": synthetic_namespace}
        h0 = hiring_activation.require_synthetic(guard)
        if h0.get("error"):
            return h0
        if not connector_binding_ids or len(set(connector_binding_ids)) != len(connector_binding_ids):
            return _error("sandbox_contract_invalid", "Sandbox connector bindings are invalid.", 400)
        sandbox_run_id = stable_id("hsr", principal.workspace_id, client_request_id)
        row = {
            "schema_version": 1, "sandbox_run_id": sandbox_run_id,
            "workspace_id": principal.workspace_id, "role_id": role_id,
            "owner_actor_id": principal.actor_id,
            "connector_binding_ids": sorted(connector_binding_ids),
            "state": "SANDBOX_PROVISIONED",
            "policy_id": policy()["policy_id"],
            "synthetic": True, "fixture_id": fixture_id,
            "synthetic_namespace": synthetic_namespace,
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create("hiring_sandbox_runs", sandbox_run_id, row)
        existing = row if created else await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        if not existing or existing.get("role_id") != role_id or existing.get("fixture_id") != fixture_id:
            return _error("idempotency_conflict", "Request id names another sandbox.")
        return {"status": "success", "duplicate": not created,
                "sandbox_run_id": sandbox_run_id, "sandbox": existing}

    async def add_destination(
            self, *, principal: ActorPrincipal, sandbox_run_id: str,
            destination_kind: str, normalized_address: str,
            verification_receipt_id: str, client_request_id: str) -> dict[str, Any]:
        sandbox = await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        if not sandbox or sandbox.get("workspace_id") != principal.workspace_id:
            return _error("sandbox_not_found", "Sandbox does not exist.", 404)
        gate = self._provisioner(principal)
        if gate.get("error"):
            return gate
        gate = authorize(principal, "read_role")
        if gate.get("error"):
            return gate
        active = require_sandbox(sandbox)
        if active.get("error"):
            return active
        address = _normalize_test_address(normalized_address)
        if destination_kind not in _DESTINATION_KINDS or not address or not verification_receipt_id:
            return _error("sandbox_destination_invalid", "Test destination is invalid.", 400)
        destination_id = stable_id("hsd", sandbox_run_id, client_request_id)
        row = {
            "schema_version": 1, "destination_id": destination_id,
            "sandbox_run_id": sandbox_run_id, "workspace_id": principal.workspace_id,
            "destination_kind": destination_kind, "normalized_address": address,
            "verification_receipt_id": verification_receipt_id, "state": "ACTIVE",
            "synthetic": True, "fixture_id": sandbox["fixture_id"],
            "synthetic_namespace": sandbox["synthetic_namespace"],
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create("hiring_sandbox_destinations", destination_id, row)
        existing = row if created else await self.store.get("hiring_sandbox_destinations", destination_id)
        if (not existing or existing.get("sandbox_run_id") != sandbox_run_id
                or existing.get("normalized_address") != address):
            return _error("idempotency_conflict", "Request id names another destination.")
        return {"status": "success", "duplicate": not created,
                "destination_id": destination_id, "destination": existing}

    async def add_connector_binding(
            self, *, principal: ActorPrincipal, sandbox_run_id: str,
            connector_grant_id: str, provider_account_subject_hash: str,
            provider_kind: str, client_request_id: str) -> dict[str, Any]:
        """Register only a server-provisioned test-account grant for this run."""
        sandbox = await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        if not sandbox or sandbox.get("workspace_id") != principal.workspace_id:
            return _error("sandbox_not_found", "Sandbox does not exist.", 404)
        gate = self._provisioner(principal)
        if gate.get("error"):
            return gate
        active = require_sandbox(sandbox)
        if active.get("error"):
            return active
        if (provider_kind not in {"GMAIL_TEST", "CALENDAR_TEST"}
                or not connector_grant_id
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", provider_account_subject_hash)):
            return _error("sandbox_connector_invalid", "Test connector binding is invalid.", 400)
        binding_id = stable_id("hscb", sandbox_run_id, client_request_id)
        row = {
            "schema_version": 1, "binding_id": binding_id,
            "sandbox_run_id": sandbox_run_id, "workspace_id": principal.workspace_id,
            "connector_grant_id": connector_grant_id, "provider_kind": provider_kind,
            "provider_account_subject_hash": provider_account_subject_hash,
            "state": "ACTIVE", "synthetic": True,
            "fixture_id": sandbox["fixture_id"],
            "synthetic_namespace": sandbox["synthetic_namespace"],
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create("hiring_sandbox_connector_bindings", binding_id, row)
        existing = row if created else await self.store.get("hiring_sandbox_connector_bindings", binding_id)
        if (not existing or existing.get("sandbox_run_id") != sandbox_run_id
                or existing.get("connector_grant_id") != connector_grant_id):
            return _error("idempotency_conflict", "Request id names another connector binding.")
        return {"status": "success", "duplicate": not created,
                "binding_id": binding_id, "binding": existing}

    async def provision_configured_connector_binding(
            self, *, principal: ActorPrincipal, sandbox_run_id: str,
            provider_kind: str, client_request_id: str) -> dict[str, Any]:
        """Bind only the deployment-owned H4S test account for PROVIDER_KIND.

        The browser is never allowed to choose a grant reference or an account
        subject. This makes a normal founder or Alex OAuth connection incapable
        of becoming an H4S effect credential merely by supplying its id.
        """
        configured = configured_test_connector(provider_kind)
        if not configured:
            return _error("sandbox_connector_not_configured",
                          "The required H4S test connector is not configured.", 503)
        return await self.add_connector_binding(
            principal=principal, sandbox_run_id=sandbox_run_id,
            connector_grant_id=configured.connector_grant_id,
            provider_account_subject_hash=configured.provider_account_subject_hash,
            provider_kind=provider_kind, client_request_id=client_request_id)

    async def provision_configured_destination(
            self, *, principal: ActorPrincipal, sandbox_run_id: str,
            destination_kind: str, client_request_id: str) -> dict[str, Any]:
        """Add only a deployment-owned H4S test mailbox/calendar attendee."""
        address = configured_test_destination(destination_kind)
        if not address:
            return _error("sandbox_destination_not_configured",
                          "The required H4S test destination is not configured.", 503)
        receipt = stable_id("h4sdestprobe", destination_kind, address)
        return await self.add_destination(
            principal=principal, sandbox_run_id=sandbox_run_id,
            destination_kind=destination_kind, normalized_address=address,
            verification_receipt_id=receipt, client_request_id=client_request_id)

    async def resolve_connector_binding(
            self, *, sandbox_run_id: str, binding_id: str, provider_kind: str) -> dict[str, Any]:
        """Fail closed: no generic connector ID or legacy migration path exists."""
        sandbox = await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        active = require_sandbox(sandbox or {}, require_enabled=True)
        if active.get("error"):
            return active
        binding = await self.store.get("hiring_sandbox_connector_bindings", binding_id)
        if (not binding or binding.get("sandbox_run_id") != sandbox_run_id
                or binding.get("provider_kind") != provider_kind
                or binding.get("state") != "ACTIVE"):
            return _error("sandbox_connector_mismatch",
                          "Sandbox test connector is not authorized.", 403)
        return {"status": "success", "binding": binding}

    async def build_exact_action(
            self, *, sandbox_run_id: str, binding_id: str,
            candidate_application_id: str, destination_ids: list[str],
            action_kind: str, rendered_payload: dict[str, Any]) -> dict[str, Any]:
        """Build the only H4S approval subject accepted by future executors."""
        expected_provider = {
            "H4S_SEND_EMAIL": "GMAIL_TEST",
            "H4S_CREATE_CALENDAR_EVENT": "CALENDAR_TEST",
            "H4S_UPDATE_CALENDAR_EVENT": "CALENDAR_TEST",
            "H4S_CANCEL_CALENDAR_EVENT": "CALENDAR_TEST",
        }.get(action_kind)
        if (not expected_provider or action_kind not in policy().get("effect_kinds", [])
                or not destination_ids or len(set(destination_ids)) != len(destination_ids)):
            return _error("sandbox_action_invalid", "H4S action contract is invalid.", 400)
        sandbox = await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        if not sandbox:
            return _error("sandbox_not_found", "Sandbox does not exist.", 404)
        binding = await self.resolve_connector_binding(
            sandbox_run_id=sandbox_run_id, binding_id=binding_id,
            provider_kind=expected_provider)
        if binding.get("error"):
            return binding
        application = await self.store.get("candidate_applications", candidate_application_id)
        if (not application or application.get("workspace_id") != sandbox.get("workspace_id")
                or application.get("role_id") != sandbox.get("role_id")
                or application.get("synthetic") is not True
                or application.get("fixture_id") != sandbox.get("fixture_id")
                or application.get("synthetic_namespace")
                != sandbox.get("synthetic_namespace")):
            return _error("sandbox_candidate_mismatch",
                          "The action candidate is outside this sandbox.", 403)
        candidate_run = await self.store.get("workflow_runs", str(application.get("run_id") or ""))
        if (not candidate_run or candidate_run.get("workspace_id") != sandbox.get("workspace_id")
                or candidate_run.get("domain_ref") != candidate_application_id
                or candidate_run.get("parent_run_id") != (await self.store.get(
                    "hiring_roles", sandbox["role_id"]) or {}).get("run_id")):
            return _error("sandbox_candidate_run_missing",
                          "The candidate workflow is not valid for this sandbox.", 409)
        allowed = ({"TEST_CANDIDATE", "TEST_FOUNDER"} if action_kind == "H4S_SEND_EMAIL"
                   else {"TEST_CANDIDATE", "TEST_FOUNDER", "TEST_CALENDAR_ATTENDEE"})
        destinations = []
        for destination_id in destination_ids:
            resolved = await self.resolve_destination(
                sandbox_run_id=sandbox_run_id, destination_id=destination_id,
                allowed_kinds=allowed)
            if resolved.get("error"):
                return resolved
            destinations.append(resolved)
        if action_kind == "H4S_SEND_EMAIL":
            if set(rendered_payload) != {"subject", "body"}:
                return _error("sandbox_action_invalid", "Email payload is invalid.", 400)
            payload = {"subject": str(rendered_payload["subject"])[:500],
                       "body_hash": canonical_hash({"body": str(rendered_payload["body"])})}
        elif action_kind == "H4S_CREATE_CALENDAR_EVENT":
            required = {"summary", "description", "start", "end", "timezone", "conference"}
            if set(rendered_payload) != required:
                return _error("sandbox_action_invalid", "Calendar payload is invalid.", 400)
            payload = {key: str(rendered_payload[key])[:2000] for key in sorted(required)}
        else:
            target_id = str(rendered_payload.get("target_action_id") or "")
            target = await self.store.get("external_actions", target_id)
            target_exact = (target or {}).get("exact_action") or {}
            target_context = (target or {}).get("sandbox_context") or {}
            if (not target or target.get("status") != "SUCCEEDED"
                    or target.get("action_kind") not in {
                        "H4S_CREATE_CALENDAR_EVENT", "H4S_UPDATE_CALENDAR_EVENT"}
                    or target_context.get("sandbox_run_id") != sandbox_run_id
                    or target_context.get("connector_binding_id") != binding_id
                    or target_exact.get("candidate_application_id") != candidate_application_id
                    or target_exact.get("destination_ids") != destination_ids
                    or not target.get("provider_effect_id")):
                return _error("sandbox_calendar_target_mismatch",
                              "Calendar change target is not this exact sandbox event.", 403)
            if action_kind == "H4S_UPDATE_CALENDAR_EVENT":
                required = {"target_action_id", "summary", "description", "start", "end", "timezone", "conference"}
                if set(rendered_payload) != required:
                    return _error("sandbox_action_invalid", "Calendar update payload is invalid.", 400)
                payload = {key: str(rendered_payload[key])[:2000] for key in sorted(required)}
                payload["target_event_id"] = str(target["provider_effect_id"])
            else:
                if set(rendered_payload) != {"target_action_id"}:
                    return _error("sandbox_action_invalid", "Calendar cancellation payload is invalid.", 400)
                payload = {"target_action_id": target_id,
                           "target_event_id": str(target["provider_effect_id"])}
        exact_action = {
            "schema_version": 1, "action_kind": action_kind,
            "sandbox_run_id": sandbox_run_id, "connector_binding_id": binding_id,
            "candidate_application_id": candidate_application_id,
            "candidate_run_id": candidate_run["run_id"],
            "destination_ids": destination_ids,
            "normalized_destinations": [item["normalized_address"] for item in destinations],
            "payload": payload, "policy_id": policy()["policy_id"],
        }
        return {"status": "success", "exact_action": exact_action,
                "subject_hash": canonical_hash(exact_action),
                "sandbox_context": {
                    "sandbox_run_id": sandbox_run_id, "connector_binding_id": binding_id,
                    "destination_ids": destination_ids,
                    "candidate_application_id": candidate_application_id,
                    "candidate_run_id": candidate_run["run_id"],
                    "fixture_id": binding["binding"]["fixture_id"]}}

    async def request_effect_approval(
            self, *, principal: ActorPrincipal, sandbox_run_id: str,
            binding_id: str, candidate_application_id: str,
            destination_ids: list[str], action_kind: str,
            rendered_payload: dict[str, Any], client_request_id: str) -> dict[str, Any]:
        """Create only a hiring-domain, run-bound approval for an H4S effect."""
        sandbox = await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        if not sandbox or sandbox.get("workspace_id") != principal.workspace_id:
            return _error("sandbox_not_found", "Sandbox does not exist.", 404)
        action = await self.build_exact_action(
            sandbox_run_id=sandbox_run_id, binding_id=binding_id,
            candidate_application_id=candidate_application_id,
            destination_ids=destination_ids, action_kind=action_kind,
            rendered_payload=rendered_payload)
        if action.get("error"):
            return action
        runs = await self.store.list(
            "workflow_runs",
            filters={"workspace_id": principal.workspace_id,
                     "domain_ref": sandbox["role_id"], "run_kind": "ROLE"},
            limit=2)
        if len(runs) != 1:
            return _error("sandbox_role_run_missing",
                          "Sandbox requires exactly one durable role run.", 409)
        role = await self.store.get("hiring_roles", sandbox["role_id"])
        if not role:
            return _error("role_not_found", "Sandbox role does not exist.", 404)
        approval = await request_approval(
            principal=principal, run_id=runs[0]["run_id"], role_id=sandbox["role_id"],
            policy_version_id=str(role.get("current_policy_version_id") or policy()["policy_id"]),
            action_kind=action_kind, exact_action=action["exact_action"],
            client_request_id=client_request_id, store=self.store)
        return {**approval, "sandbox_context": action.get("sandbox_context"),
                "exact_action": action.get("exact_action")}

    async def claim_effect_approval(
            self, *, principal: ActorPrincipal, approval_id: str,
            sandbox_run_id: str, binding_id: str, candidate_application_id: str,
            destination_ids: list[str],
            action_kind: str, rendered_payload: dict[str, Any]) -> dict[str, Any]:
        """Refuse the removed claim-without-action compatibility endpoint.

        Claiming authority without preparing an action is not recoverable. The
        H4S effect executor now performs both operations atomically at T1.
        Parameters remain temporarily for an explicit, fail-closed API bridge.
        """
        del (principal, approval_id, sandbox_run_id, binding_id,
             candidate_application_id, destination_ids, action_kind,
             rendered_payload)
        return _error(
            "claim_requires_action_prepare",
            "Approval claims are created only by the effect execution boundary.")

    async def resolve_destination(
            self, *, sandbox_run_id: str, destination_id: str,
            allowed_kinds: set[str]) -> dict[str, Any]:
        """Return only a server-owned active test destination; no free-text fallback."""
        sandbox = await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        active = require_sandbox(sandbox or {})
        if active.get("error"):
            return active
        destination = await self.store.get("hiring_sandbox_destinations", destination_id)
        if (not destination or destination.get("sandbox_run_id") != sandbox_run_id
                or destination.get("state") != "ACTIVE"
                or destination.get("destination_kind") not in allowed_kinds):
            return _error("sandbox_destination_not_allowed",
                          "Destination is not allowlisted for this sandbox.", 403)
        return {"status": "success", "destination_id": destination_id,
                "normalized_address": destination["normalized_address"]}

    async def close(self, *, principal: ActorPrincipal, sandbox_run_id: str) -> dict[str, Any]:
        sandbox = await self.store.get("hiring_sandbox_runs", sandbox_run_id)
        if not sandbox or sandbox.get("workspace_id") != principal.workspace_id:
            return _error("sandbox_not_found", "Sandbox does not exist.", 404)
        gate = self._provisioner(principal)
        if gate.get("error"):
            return gate
        gate = authorize(principal, "read_role")
        if gate.get("error"):
            return gate
        if sandbox.get("state") == "SANDBOX_CLOSED":
            return {"status": "success", "duplicate": True, "sandbox": sandbox}
        committed = await self.store.compare_and_set(
            "hiring_sandbox_runs", sandbox_run_id, int(sandbox["version"]),
            {"state": "SANDBOX_CLOSED", "closed_at": utc_now(),
             "updated_at": utc_now(), "teardown_receipt_id": stable_id("hstr", sandbox_run_id)})
        if not committed:
            return _error("concurrency_conflict", "Sandbox changed concurrently.")
        # Teardown revokes every server-owned child record, not just
        # destinations: a binding left ACTIVE makes the teardown receipt
        # overstate what was actually withdrawn.
        revoked = 0
        for collection, id_field in (
                ("hiring_sandbox_destinations", "destination_id"),
                ("hiring_sandbox_connector_bindings", "binding_id")):
            for row in await self.store.list(
                    collection, filters={"sandbox_run_id": sandbox_run_id}, limit=1000):
                if row.get("state") != "ACTIVE":
                    continue
                if await self.store.compare_and_set(
                        collection, row[id_field], int(row["version"]),
                        {"state": "REVOKED", "revoked_at": utc_now(),
                         "updated_at": utc_now()}):
                    revoked += 1
        return {"status": "success", "duplicate": False, "sandbox": committed,
                "revoked_child_records": revoked}
