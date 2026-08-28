"""One versioned human-decision record for platform consequences."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from services import capability_registry
from services.actor_identity import ActorPrincipal, authorize
from services.canonical import canonical_hash
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.workflow_contracts import stable_id, utc_now


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True,
            "error_code": code, "message": message}


class PlatformApprovalService:
    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    async def request(self, *, workspace_id: str, requested_by_actor_id: str,
                      run_id: str, plan_hash: str, step_id: str,
                      capability_id: str, capability_version: str,
                      action_kind: str, target: dict[str, Any],
                      payload: dict[str, Any], policy_id: str,
                      policy_version: str, domain_ref: str,
                      domain_version: int, connector_id: str,
                      connector_binding_version: str,
                      client_request_id: str, ttl_minutes: int = 30,
                      distinct_approver_required: bool = False,
                      origin_session_id: str = "",
                      legacy_target: str = "",
                      legacy_gate: str = "",
                      presentation_details: dict[str, Any] | None = None,
                      approval_domain: str = "PLATFORM_CONSEQUENCE",
                      ) -> dict[str, Any]:
        if not 1 <= ttl_minutes <= 240:
            return _error("approval_contract_invalid", "Approval expiry is invalid.")
        try:
            descriptor = capability_registry.require_external_action(
                action_kind, connector_id)
        except ValueError:
            return _error("capability_disabled",
                          "The consequence is not in the reviewed manifest.")
        if (capability_id != descriptor.capability_id
                or capability_version != descriptor.semantic_version
                or policy_id != descriptor.approval_policy_id):
            return _error("approval_contract_invalid",
                          "Capability or approval policy binding is invalid.")
        run = await self.store.get("workflow_runs", run_id)
        step = await self.store.get("workflow_steps", step_id)
        memberships = await self.store.list(
            "workspace_members",
            filters={"workspace_id": workspace_id,
                     "actor_id": requested_by_actor_id,
                     "status": "ACTIVE"}, limit=2)
        if (not run or not step or len(memberships) != 1
                or run.get("workspace_id") != workspace_id
                or step.get("workspace_id") != workspace_id
                or step.get("run_id") != run_id
                or run.get("plan_hash") != plan_hash):
            return _error("approval_authority_missing",
                          "Run, step, plan, or initiating membership is invalid.")
        target_hash = canonical_hash(target, domain="approval-target")
        payload_hash = canonical_hash(payload, domain="approval-payload")
        subject = {
            "workspace_id": workspace_id, "run_id": run_id,
            "plan_hash": plan_hash, "step_id": step_id,
            "capability_id": capability_id,
            "capability_version": capability_version,
            "action_kind": action_kind, "target_hash": target_hash,
            "normalized_payload_hash": payload_hash,
            "policy_id": policy_id, "policy_version": policy_version,
            "domain_ref": domain_ref, "domain_version": domain_version,
            "connector_id": connector_id,
            "connector_binding_version": connector_binding_version,
        }
        approval_subject_hash = canonical_hash(
            subject, domain="approval-subject-v2")
        # Existing product connectors use `subject_hash` for the exact
        # founder-visible form/message payload. Keep that compatibility digest
        # while retaining the complete platform binding under its explicit
        # name. New generic callers without that legacy digest receive the full
        # platform subject hash in both fields.
        action_subject_hash = str(
            payload.get("subject_hash") or approval_subject_hash)
        approval_id = stable_id(
            "approval", workspace_id, client_request_id)
        now = datetime.now(timezone.utc)
        row = {
            "schema_version": 2, "approval_id": approval_id,
            **subject, "subject_hash": action_subject_hash,
            "approval_subject_hash": approval_subject_hash,
            # Compatibility presentation fields keep the current approval UI
            # and connector facade working during convergence. They never
            # replace the exact v2 subject bindings above.
            "approval_domain": approval_domain,
            "founder_id": workspace_id,
            "application_id": legacy_target or domain_ref,
            "gate": legacy_gate or action_kind,
            "session_id": origin_session_id,
            "details": dict(presentation_details or {}),
            "requested_by_actor_id": requested_by_actor_id,
            "approving_actor_requirement": (
                "DISTINCT_INTERACTIVE" if distinct_approver_required
                else "INTERACTIVE_MEMBER"),
            "status": "PENDING", "decided_by_actor_id": None,
            "decision_at": None, "decision_reason": None,
            "claim_id": None, "claimed_action_id": None,
            "claimed_at": None, "claim_lease_generation": 0,
            "claim_lease_expires_at": None, "consumed_at": None,
            "voided_at": None, "void_reason": None,
            "terminal_action_id": None,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
            "updated_at": now.isoformat(), "version": 1,
        }
        created = await self.store.create("approvals", approval_id, row)
        existing = row if created else await self.store.get("approvals", approval_id)
        if (not existing
                or (existing.get("approval_subject_hash")
                    or existing.get("subject_hash")) != approval_subject_hash):
            return _error("idempotency_conflict",
                          "Approval request id names another subject.")
        from services.projection_stream import publish_best_effort

        await publish_best_effort(
            store=self.store, workspace_id=workspace_id,
            projection_type="approval", aggregate_id=approval_id,
            aggregate_version=int(existing["version"]), run_id=run_id,
            safe_payload={"status": existing["status"],
                          "gate": existing["gate"],
                          "action_kind": action_kind,
                          "expires_at": existing["expires_at"]},
            idempotency_key=f"request:{approval_id}:{existing['version']}")
        return {"status": "success", "duplicate": not created,
                "approval": existing}

    async def decide(self, *, principal: ActorPrincipal, approval_id: str,
                     decision: str, reason: str = "") -> dict[str, Any]:
        gate = authorize(principal, "resolve_approval", require_fresh=True)
        if gate.get("error"):
            return gate
        if decision not in {"GRANT", "DENY"}:
            return _error("approval_decision_invalid", "Decision is invalid.")
        approval = await self.store.get("approvals", approval_id)
        if (not approval or approval.get("workspace_id") != principal.workspace_id
                or int(approval.get("schema_version") or 0) != 2):
            return _error("approval_not_found", "Approval does not exist.")
        if approval.get("status") != "PENDING":
            return _error("approval_terminal", "Approval is already decided.")
        if str(approval.get("expires_at") or "") <= utc_now():
            return _error("approval_expired", "Approval has expired.")
        if (approval.get("approving_actor_requirement") == "DISTINCT_INTERACTIVE"
                and approval.get("requested_by_actor_id") == principal.actor_id):
            return _error("distinct_approver_required",
                          "This policy requires another signed-in approver.")
        if not principal.membership_id:
            return _error("membership_missing", "Current membership is required.")
        membership = await self.store.get(
            "workspace_members", principal.membership_id)
        if (not membership or membership.get("workspace_id") != principal.workspace_id
                or membership.get("status") != "ACTIVE"):
            return _error("membership_missing", "Current membership is inactive.")
        now = utc_now()
        audit_id = stable_id("audit", approval_id, decision, principal.actor_id)
        mutations = [
            AtomicMutation(
                "workspace_members", principal.membership_id,
                int(membership["version"]), check_only=True),
            AtomicMutation(
                "approvals", approval_id, int(approval["version"]),
                updates={"status": "GRANTED" if decision == "GRANT" else "DENIED",
                         "decided_by_actor_id": principal.actor_id,
                         "decision_at": now, "decision_reason": reason[:240],
                         "updated_at": now}),
            AtomicMutation("audit", audit_id, None, record={
                "schema_version": 2, "audit_id": audit_id,
                "workspace_id": principal.workspace_id,
                "actor_id": principal.actor_id,
                "action": "approval.decide", "target": approval_id,
                "result": decision, "created_at": now, "version": 1,
            }),
        ]
        wake_id = ""
        if approval.get("session_id"):
            wake_id = stable_id(
                "wake", approval_id, str(approval["session_id"]))
            mutations.append(AtomicMutation(
                "wake_deliveries", wake_id, None, record={
                    "schema_version": 1, "delivery_id": wake_id,
                    "workspace_id": principal.workspace_id,
                    "delivery_domain": "FOUNDER_WAKE",
                    "founder_id": principal.workspace_id,
                    "session_id": str(approval["session_id"]),
                    "source_kind": "approval", "source_id": approval_id,
                    "notice": ("Resume: founder "
                               f"{'approved' if decision == 'GRANT' else 'declined'} "
                               f"{approval.get('gate', 'action')} at the approval gate."),
                    "state_delta": {"pending_signals": []},
                    "status": "PENDING", "attempt": 0,
                    "lease_owner": None, "lease_started_at": None,
                    "lease_seconds": 120, "last_error_code": None,
                    "created_at": now, "updated_at": now,
                    "delivered_at": None, "version": 1,
                }))
        committed = await self.store.atomic_compare_and_set(tuple(mutations))
        if not committed:
            return _error("concurrency_conflict", "Approval changed concurrently.")
        decided = committed[("approvals", approval_id)]
        from services.projection_stream import publish_best_effort

        await publish_best_effort(
            store=self.store, workspace_id=principal.workspace_id,
            projection_type="approval", aggregate_id=approval_id,
            aggregate_version=int(decided["version"]),
            run_id=str(decided.get("run_id") or ""),
            safe_payload={"status": decided["status"],
                          "gate": decided.get("gate"),
                          "decision": decision},
            idempotency_key=f"decide:{approval_id}:{decided['version']}")
        wait_resolution: dict[str, Any] | None = None
        waits = await self.store.list(
            "waits", filters={"run_id": approval["run_id"],
                              "correlation_key": approval_id}, limit=2)
        if len(waits) == 1 and waits[0].get("status") == "OPEN":
            from services.workflow_runtime import WorkflowRuntime

            wait_resolution = await WorkflowRuntime(self.store).resolve_wait(
                waits[0]["wait_id"],
                event_id=f"approval-decision:{approval_id}:{decision.lower()}",
                expected_generation=int(waits[0].get("generation") or 1))
        return {"status": "success", "duplicate": False,
                "approval": decided,
                "approval_id": approval_id,
                "decision": decision.lower(),
                "gate": approval.get("gate", ""),
                "application_id": approval.get("application_id", ""),
                "session_id": approval.get("session_id", ""),
                "decided_by_actor_id": principal.actor_id,
                "wake_delivery_id": wake_id or None,
                "wait_resolution": wait_resolution}
