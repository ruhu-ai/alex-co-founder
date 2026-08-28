"""Unified T1/T2/T3 approval and provider-consequence protocol."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from services import capability_registry
from services.canonical import canonical_hash
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.workflow_contracts import RuntimeStatus, normalize_runtime_status, stable_id, utc_now


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True,
            "error_code": code, "message": message}


def _terminal(status: str) -> bool:
    return status in {"SUCCEEDED", "FAILED"}


class ConsequenceService:
    """The provider SDK is deliberately absent from this class.

    A caller receives an EXECUTING receipt from ``start`` and only then calls
    its registered adapter. It must settle or mark uncertainty; it can never
    use this service to blind-retry the provider endpoint.
    """

    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    async def prepare(self, *, workspace_id: str, approval_id: str,
                      run_id: str, step_id: str, connector_id: str,
                      action_kind: str, idempotency_key: str,
                      target: dict[str, Any], payload: dict[str, Any],
                      lease_seconds: int = 120) -> dict[str, Any]:
        """T1: atomically claim exact approval and create PREPARED + outbox."""
        try:
            capability = capability_registry.require_external_action(
                action_kind, connector_id)
        except ValueError:
            return _error("capability_disabled", "Consequence is not registered.")
        approval = await self.store.get("approvals", approval_id)
        run = await self.store.get("workflow_runs", run_id)
        step = await self.store.get("workflow_steps", step_id)
        if (not approval or not run or not step
                or any(row.get("workspace_id") != workspace_id
                       for row in (approval, run, step))):
            return _error("authority_not_found", "Consequence authority is missing.")
        target_hash = canonical_hash(target, domain="approval-target")
        payload_hash = canonical_hash(payload, domain="approval-payload")
        expected = {
            "run_id": run_id, "plan_hash": run.get("plan_hash"),
            "step_id": step_id, "capability_id": capability.capability_id,
            "capability_version": capability.semantic_version,
            "action_kind": action_kind, "target_hash": target_hash,
            "normalized_payload_hash": payload_hash,
            "connector_id": connector_id,
        }
        action_id = stable_id(
            "action", workspace_id, action_kind, idempotency_key)
        request_material = {
            **expected, "workspace_id": workspace_id,
            "approval_id": approval_id,
            "policy_id": approval.get("policy_id"),
            "policy_version": approval.get("policy_version"),
            "approval_domain": approval.get("approval_domain"),
            "domain_ref": approval.get("domain_ref"),
            "domain_version": approval.get("domain_version"),
            "connector_binding_version": approval.get(
                "connector_binding_version"),
        }
        request_hash = canonical_hash(
            request_material, domain="external-action-request-v2")
        existing = await self.store.get("external_actions", action_id)
        if existing:
            if (existing.get("request_hash") != request_hash
                    or existing.get("approval_id") != approval_id):
                return _error("idempotency_conflict",
                              "Action key names another consequence.")
            if existing.get("status") == "UNCERTAIN":
                return _error("reconciliation_required",
                              "Action outcome requires reconciliation.")
            return {"status": "success", "duplicate": True,
                    "receipt_status": existing.get("status"),
                    "action": existing}
        if (approval.get("status") != "GRANTED"
                or str(approval.get("expires_at") or "") <= utc_now()
                or any(approval.get(key) != value for key, value in expected.items())):
            return _error("approval_binding_mismatch",
                          "Approval does not cover this exact consequence.")
        if normalize_runtime_status(str(run.get("runtime_status") or "")) in {
                RuntimeStatus.CANCELLING.value, RuntimeStatus.CANCELLED.value,
                RuntimeStatus.SUCCEEDED.value, RuntimeStatus.FAILED.value,
                RuntimeStatus.REJECTED.value}:
            return _error("run_fenced", "Run no longer authorizes consequences.")
        now = datetime.now(timezone.utc)
        lease_owner = uuid.uuid4().hex
        claim_id = stable_id("claim", approval_id, action_id)
        outbox_id = stable_id("actionoutbox", action_id, "execute")
        action = {
            "schema_version": 2, "action_id": action_id,
            "workspace_id": workspace_id, "action_domain": "CONNECTOR_ACTION",
            "run_id": run_id, "step_id": step_id,
            "step_attempt_id": step.get("active_attempt_id"),
            "action_kind": action_kind, "connector_id": connector_id,
            "connector_binding_version": approval.get(
                "connector_binding_version"),
            "request_hash": request_hash, "idempotency_key": idempotency_key,
            "approval_id": approval_id, "subject_hash": approval.get("subject_hash"),
            "approval_domain": approval.get("approval_domain"),
            "target_hash": target_hash,
            "normalized_payload_hash": payload_hash,
            "plan_hash": run.get("plan_hash"),
            "capability_id": capability.capability_id,
            "capability_version": capability.semantic_version,
            "policy_id": approval.get("policy_id"),
            "policy_version": approval.get("policy_version"),
            "domain_ref": approval.get("domain_ref"),
            "domain_version": approval.get("domain_version"),
            "status": "PREPARED", "lease_owner": lease_owner,
            "lease_generation": 1,
            "lease_expires_at": (now + timedelta(
                seconds=max(1, min(lease_seconds, 900)))).isoformat(),
            "execution_attempt": 0,
            "observed_cancellation_generation": int(
                run.get("cancellation_generation") or 0),
            "provider_idempotency_key": stable_id(
                "providerkey", workspace_id, action_id),
            "provider_request_id": None,
            "consequence_start_committed_at": None,
            "provider_call_attempted_at": None,
            "provider_effect_id": None, "result_ref": None,
            "uncertainty_reason": None, "error_code": None,
            "prepared_at": now.isoformat(), "terminal_at": None,
            "reconciled_at": None, "updated_at": now.isoformat(),
            "version": 1,
        }
        outbox = {
            "schema_version": 1, "outbox_id": outbox_id,
            "workspace_id": workspace_id, "action_id": action_id,
            "status": "PENDING", "attempt": 0,
            "created_at": now.isoformat(), "updated_at": now.isoformat(),
            "version": 1,
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation(
                "workflow_runs", run_id, int(run["version"]), check_only=True),
            AtomicMutation(
                "workflow_steps", step_id, int(step["version"]), check_only=True),
            AtomicMutation(
                "approvals", approval_id, int(approval["version"]),
                updates={"status": "CLAIMED", "claim_id": claim_id,
                         "claimed_action_id": action_id,
                         "claimed_at": now.isoformat(),
                         "claim_lease_generation": 1,
                         "claim_lease_expires_at": action["lease_expires_at"],
                         "updated_at": now.isoformat()}),
            AtomicMutation("external_actions", action_id, None, record=action),
            AtomicMutation(
                "action_execution_outbox", outbox_id, None, record=outbox),
        ))
        if not committed:
            return _error("concurrency_conflict",
                          "Approval or run changed during preparation.")
        return {"status": "success", "duplicate": False,
                "receipt_status": "PREPARED", "lease_owner": lease_owner,
                "action": committed[("external_actions", action_id)]}

    async def start(self, *, workspace_id: str, action_id: str,
                    lease_owner: str, workload_principal: str) -> dict[str, Any]:
        """T2 linearization: consume authority and enter EXECUTING atomically."""
        if not workload_principal:
            return _error("workload_unauthorized",
                          "A verified consequence worker is required.")
        action = await self.store.get("external_actions", action_id)
        if (not action or action.get("workspace_id") != workspace_id
                or int(action.get("schema_version") or 0) != 2):
            return _error("action_not_found", "Action does not exist.")
        if _terminal(str(action.get("status"))):
            return {"status": "success", "duplicate": True,
                    "receipt_status": action["status"], "action": action}
        if action.get("status") == "UNCERTAIN":
            return _error("reconciliation_required",
                          "Action outcome requires reconciliation.")
        if (action.get("status") != "PREPARED"
                or action.get("lease_owner") != lease_owner):
            return _error("lease_conflict", "Action preparation lease changed.")
        approval = await self.store.get("approvals", action["approval_id"])
        run = await self.store.get("workflow_runs", action["run_id"])
        step = await self.store.get("workflow_steps", action["step_id"])
        plans = await self.store.list(
            "workflow_plans",
            filters={"run_id": action["run_id"], "status": "ACTIVE"}, limit=2)
        if not approval or not run or not step or len(plans) != 1:
            return _error("authority_not_found", "Action authority is missing.")
        plan = plans[0]
        run_status = normalize_runtime_status(str(run.get("runtime_status") or ""))
        fenced = (
            run_status in {RuntimeStatus.CANCELLING.value,
                           RuntimeStatus.CANCELLED.value,
                           RuntimeStatus.SUCCEEDED.value,
                           RuntimeStatus.FAILED.value,
                           RuntimeStatus.REJECTED.value}
            or int(run.get("cancellation_generation") or 0)
            != int(action.get("observed_cancellation_generation") or 0))
        now = utc_now()
        if (approval.get("status") != "CLAIMED"
                or approval.get("claimed_action_id") != action_id):
            return _error("approval_binding_mismatch",
                          "Claim no longer belongs to this action.")
        try:
            current_capability = capability_registry.require_external_action(
                str(action.get("action_kind") or ""),
                str(action.get("connector_id") or ""))
            capability_valid = (
                current_capability.capability_id == action.get("capability_id")
                and current_capability.semantic_version
                == action.get("capability_version"))
        except ValueError:
            capability_valid = False
        decided_actor_id = str(approval.get("decided_by_actor_id") or "")
        memberships = (await self.store.list(
            "workspace_members",
            filters={"workspace_id": workspace_id,
                     "actor_id": decided_actor_id,
                     "status": "ACTIVE"}, limit=2)
            if decided_actor_id else [])
        binding_fields = (
            "workspace_id", "run_id", "plan_hash", "step_id",
            "capability_id", "capability_version", "action_kind",
            "target_hash", "normalized_payload_hash", "policy_id",
            "policy_version", "domain_ref", "domain_version",
            "connector_id", "connector_binding_version",
        )
        approval_binding_valid = all(
            approval.get(field) == action.get(field)
            for field in binding_fields)
        budget_usage = dict(run.get("budget_usage") or {})
        budget_limits = dict(run.get("budgets") or {})
        provider_budget_exhausted = int(
            budget_usage.get("provider_calls") or 0) >= int(
                budget_limits.get("max_provider_calls", 20))
        authority_invalid = (
            str(action.get("lease_expires_at") or "") <= now
            or str(approval.get("expires_at") or "") <= now
            or step.get("run_id") != run.get("run_id")
            or plan.get("plan_hash") != run.get("plan_hash")
            or action.get("plan_hash") != run.get("plan_hash")
            or not capability_valid
            or not approval_binding_valid
            or len(memberships) != 1
            or provider_budget_exhausted)
        if fenced or authority_invalid:
            reason = ("run_fenced" if fenced else
                      "budget_exhausted" if provider_budget_exhausted else
                      "stale_consequence_guard")
            committed = await self.store.atomic_compare_and_set((
                AtomicMutation(
                    "approvals", approval["approval_id"], int(approval["version"]),
                    updates={"status": "VOIDED", "voided_at": now,
                             "void_reason": reason,
                             "terminal_action_id": action_id, "updated_at": now}),
                AtomicMutation(
                    "external_actions", action_id, int(action["version"]),
                    updates={"status": "FAILED", "error_code": reason,
                             "lease_owner": None, "terminal_at": now,
                             "updated_at": now}),
            ))
            return (_error(reason, "Consequence authority changed before execution.")
                    if committed else _error(
                        "concurrency_conflict", "Action guard changed concurrently."))
        provider_request_id = stable_id("providerrequest", action_id, "1")
        outboxes = await self.store.list(
            "action_execution_outbox", filters={"action_id": action_id}, limit=2)
        if len(outboxes) != 1:
            return _error("action_outbox_missing", "Execution intent is missing.")
        outbox = outboxes[0]
        next_budget_usage = {
            **budget_usage,
            "provider_calls": int(budget_usage.get("provider_calls") or 0) + 1,
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation(
                "workflow_runs", run["run_id"], int(run["version"]),
                updates={"budget_usage": next_budget_usage,
                         "updated_at": now}),
            AtomicMutation(
                "workflow_plans", str(plan.get("plan_id") or plan["id"]),
                int(plan["version"]), check_only=True),
            AtomicMutation(
                "workflow_steps", step["step_id"], int(step["version"]),
                check_only=True),
            AtomicMutation(
                "workspace_members",
                str(memberships[0].get("membership_id") or memberships[0]["id"]),
                int(memberships[0]["version"]), check_only=True),
            AtomicMutation(
                "approvals", approval["approval_id"], int(approval["version"]),
                updates={"status": "CONSUMED", "consumed_at": now,
                         "terminal_action_id": action_id, "updated_at": now}),
            AtomicMutation(
                "external_actions", action_id, int(action["version"]),
                updates={"status": "EXECUTING",
                         "provider_request_id": provider_request_id,
                         "consequence_start_committed_at": now,
                         "executing_workload_principal": workload_principal,
                         "execution_attempt": int(
                             action.get("execution_attempt") or 0) + 1,
                         "updated_at": now}),
            AtomicMutation(
                "action_execution_outbox",
                str(outbox.get("outbox_id") or outbox["id"]),
                int(outbox["version"]),
                updates={"status": "DELIVERED", "delivered_at": now,
                         "updated_at": now}),
        ))
        if not committed:
            return _error("concurrency_conflict", "Consequence start raced.")
        started = committed[("external_actions", action_id)]
        return {"status": "success", "duplicate": False,
                "receipt_status": "EXECUTING",
                "provider_request_id": provider_request_id,
                "provider_idempotency_key": started["provider_idempotency_key"],
                "action": started}

    async def reclaim_prepared(self, *, workspace_id: str, action_id: str,
                               lease_seconds: int = 120) -> dict[str, Any]:
        """Recover death after T1 without minting another action or claim."""
        action = await self.store.get("external_actions", action_id)
        if (not action or action.get("workspace_id") != workspace_id):
            return _error("action_not_found", "Action does not exist.")
        if action.get("status") != "PREPARED":
            return _error("action_state_invalid", "Action is not prepared.")
        if str(action.get("lease_expires_at") or "") > utc_now():
            return _error("lease_active", "Preparation lease is still active.")
        approval = await self.store.get("approvals", action["approval_id"])
        if (not approval or approval.get("status") != "CLAIMED"
                or approval.get("claimed_action_id") != action_id):
            return _error("approval_binding_mismatch", "Action claim is not recoverable.")
        now = datetime.now(timezone.utc)
        generation = int(action.get("lease_generation") or 0) + 1
        owner = uuid.uuid4().hex
        expiry = (now + timedelta(
            seconds=max(1, min(lease_seconds, 900)))).isoformat()
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation(
                "approvals", approval["approval_id"], int(approval["version"]),
                updates={"claim_lease_generation": generation,
                         "claim_lease_expires_at": expiry,
                         "updated_at": now.isoformat()}),
            AtomicMutation(
                "external_actions", action_id, int(action["version"]),
                updates={"lease_owner": owner, "lease_generation": generation,
                         "lease_expires_at": expiry,
                         "updated_at": now.isoformat()}),
        ))
        if not committed:
            return _error("concurrency_conflict", "Preparation recovery raced.")
        return {"status": "success", "duplicate": False,
                "receipt_status": "PREPARED", "lease_owner": owner,
                "action": committed[("external_actions", action_id)]}

    async def mark_provider_attempted(self, *, workspace_id: str,
                                      action_id: str,
                                      lease_owner: str) -> dict[str, Any]:
        """Best-effort evidence written immediately before the SDK call."""
        action = await self.store.get("external_actions", action_id)
        if (not action or action.get("workspace_id") != workspace_id
                or action.get("status") != "EXECUTING"
                or action.get("lease_owner") != lease_owner):
            return _error("lease_conflict", "Provider-attempt lease changed.")
        if action.get("provider_call_attempted_at"):
            return {"status": "success", "duplicate": True, "action": action}
        committed = await self.store.compare_and_set(
            "external_actions", action_id, int(action["version"]), {
                "provider_call_attempted_at": utc_now(), "updated_at": utc_now()})
        if not committed:
            return _error("concurrency_conflict", "Provider-attempt record raced.")
        return {"status": "success", "duplicate": False, "action": committed}

    async def settle(self, *, workspace_id: str, action_id: str,
                     lease_owner: str, status: str,
                     provider_effect_id: str = "",
                     result_ref: dict[str, Any] | None = None,
                     error_code: str = "", uncertainty_reason: str = ""
                     ) -> dict[str, Any]:
        """T3: commit one evidenced outcome plus ordered run event."""
        if status not in {"SUCCEEDED", "FAILED", "UNCERTAIN"}:
            return _error("action_status_invalid", "Settlement status is invalid.")
        action = await self.store.get("external_actions", action_id)
        if (not action or action.get("workspace_id") != workspace_id
                or action.get("lease_owner") != lease_owner):
            return _error("lease_conflict", "Action settlement lease changed.")
        if _terminal(str(action.get("status"))):
            return {"status": "success", "duplicate": True,
                    "receipt_status": action["status"], "action": action}
        if action.get("status") != "EXECUTING":
            return _error("action_state_invalid", "Only executing actions settle.")
        if status == "FAILED" and not error_code:
            return _error("failure_evidence_missing", "Failure needs closed evidence.")
        if status == "UNCERTAIN" and not uncertainty_reason:
            return _error("uncertainty_reason_missing", "Uncertainty must be visible.")
        run = await self.store.get("workflow_runs", action["run_id"])
        if not run:
            return _error("run_not_found", "Action run is missing.")
        sequence = int(run.get("next_event_sequence") or 1)
        event_key = f"action-settle:{action_id}:{status}"
        event_id = stable_id("evt", run["run_id"], event_key)
        now = utc_now()
        safe_result = {str(key)[:64]: str(value)[:280]
                       for key, value in (result_ref or {}).items()}
        event = {
            "schema_version": 2, "event_id": event_id,
            "run_id": run["run_id"], "workspace_id": workspace_id,
            "journey_id": run["journey_id"], "sequence": sequence,
            "event_kind": "ACTION_SETTLED", "event_schema_version": 1,
            "reducer_version": 1, "idempotency_key": event_key,
            "safe_payload": {"action_id": action_id, "status": status},
            "actor_id": None, "workload_principal": action.get(
                "executing_workload_principal"),
            "causation_event_id": None, "occurred_at": now,
            "provenance": run.get("provenance") or {}, "version": 1,
        }
        updates = {
            "status": status, "provider_effect_id": provider_effect_id or None,
            "result_ref": safe_result or None, "error_code": error_code or None,
            "uncertainty_reason": uncertainty_reason or None,
            "lease_owner": None, "lease_expires_at": None,
            "terminal_at": now if status != "UNCERTAIN" else None,
            "updated_at": now,
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation(
                "workflow_runs", run["run_id"], int(run["version"]),
                updates={"next_event_sequence": sequence + 1,
                         "updated_at": now}),
            AtomicMutation(
                "external_actions", action_id, int(action["version"]),
                updates=updates),
            AtomicMutation("run_events", event_id, None, record=event),
        ))
        if not committed:
            current = await self.store.get("external_actions", action_id)
            if current and current.get("status") == status:
                return {"status": "success", "duplicate": True,
                        "receipt_status": status, "action": current}
            return _error("concurrency_conflict", "Action settlement raced.")
        return {"status": "success", "duplicate": False,
                "receipt_status": status,
                "action": committed[("external_actions", action_id)]}

    async def mark_expired_executing_uncertain(
            self, *, workspace_id: str, action_id: str) -> dict[str, Any]:
        """Sweeper transition for worker death after T2."""
        action = await self.store.get("external_actions", action_id)
        if (not action or action.get("workspace_id") != workspace_id):
            return _error("action_not_found", "Action does not exist.")
        if action.get("status") == "UNCERTAIN":
            return {"status": "success", "duplicate": True, "action": action}
        if action.get("status") != "EXECUTING":
            return _error("action_state_invalid", "Action is not executing.")
        expiry = str(action.get("lease_expires_at") or "")
        if not expiry or expiry > utc_now():
            return _error("lease_active", "Action execution lease is still active.")
        committed = await self.store.compare_and_set(
            "external_actions", action_id, int(action["version"]), {
                "status": "UNCERTAIN", "lease_owner": None,
                "uncertainty_reason": "execution_lease_expired",
                "error_code": "reconciliation_required",
                "updated_at": utc_now(),
            })
        if not committed:
            return _error("concurrency_conflict", "Action changed concurrently.")
        return {"status": "success", "duplicate": False,
                "receipt_status": "UNCERTAIN", "action": committed}

    async def reconcile(self, *, workspace_id: str, action_id: str,
                        status: str, evidence_id: str,
                        provider_effect_id: str = "",
                        result_ref: dict[str, Any] | None = None,
                        error_code: str = "") -> dict[str, Any]:
        """Settle UNCERTAIN from registered provider/operator evidence only."""
        if status not in {"SUCCEEDED", "FAILED"} or not evidence_id:
            return _error("reconciliation_evidence_invalid",
                          "Definitive reconciliation evidence is required.")
        action = await self.store.get("external_actions", action_id)
        if (not action or action.get("workspace_id") != workspace_id):
            return _error("action_not_found", "Action does not exist.")
        if _terminal(str(action.get("status"))):
            return {"status": "success", "duplicate": True,
                    "receipt_status": action["status"], "action": action}
        if action.get("status") != "UNCERTAIN":
            return _error("action_state_invalid", "Action is not uncertain.")
        run = await self.store.get("workflow_runs", action["run_id"])
        if not run:
            return _error("run_not_found", "Action run is missing.")
        sequence = int(run.get("next_event_sequence") or 1)
        event_key = f"action-reconcile:{action_id}:{status}:{evidence_id}"
        event_id = stable_id("evt", run["run_id"], event_key)
        now = utc_now()
        event = {
            "schema_version": 2, "event_id": event_id,
            "run_id": run["run_id"], "workspace_id": workspace_id,
            "journey_id": run["journey_id"], "sequence": sequence,
            "event_kind": "ACTION_SETTLED", "event_schema_version": 1,
            "reducer_version": 1, "idempotency_key": event_key,
            "safe_payload": {"action_id": action_id, "status": status,
                             "reconciled": True, "evidence_id": evidence_id[:160]},
            "actor_id": None, "workload_principal": "reconciler",
            "causation_event_id": None, "occurred_at": now,
            "provenance": run.get("provenance") or {}, "version": 1,
        }
        safe_result = {str(key)[:64]: str(value)[:280]
                       for key, value in (result_ref or {}).items()}
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation(
                "workflow_runs", run["run_id"], int(run["version"]),
                updates={"next_event_sequence": sequence + 1,
                         "updated_at": now}),
            AtomicMutation(
                "external_actions", action_id, int(action["version"]),
                updates={"status": status,
                         "provider_effect_id": provider_effect_id or None,
                         "result_ref": safe_result or None,
                         "error_code": error_code or None,
                         "uncertainty_reason": None,
                         "reconciliation_evidence_id": evidence_id,
                         "reconciled_at": now, "terminal_at": now,
                         "updated_at": now}),
            AtomicMutation("run_events", event_id, None, record=event),
        ))
        if not committed:
            return _error("concurrency_conflict", "Reconciliation raced.")
        return {"status": "success", "duplicate": False,
                "receipt_status": status,
                "action": committed[("external_actions", action_id)]}
