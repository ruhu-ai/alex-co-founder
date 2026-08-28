"""Content-free Phase 7 operations, governance, and recovery evidence.

This module never deploys revisions, calls providers, or mutates workflow
authority. It owns durable control/evidence records used to decide admission,
promotion, rollback, and recovery. Lists paginate by document ID so an
operational claim cannot silently stop at Firestore's query limit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from services.durable_store import AtomicMutation, DurableStore, production_store
from services.workflow_contracts import stable_id, utc_now

SERVICE_OBJECTIVES: dict[str, dict[str, Any]] = {
    "command_ack": {"target_percent": 99.9, "threshold_seconds": 2,
                    "window_days": 28, "degrade": "reject_new_commands"},
    "verified_event_ack": {"target_percent": 99.9, "threshold_seconds": 2,
                           "window_days": 28, "degrade": "receipt_only"},
    "interactive_step_start": {"target_percent": 99.0, "threshold_seconds": 10,
                               "window_days": 28, "degrade": "queue_and_notify"},
    "wake_dispatch": {"target_percent": 99.0, "threshold_seconds": 30,
                      "window_days": 28, "degrade": "snapshot_inbox"},
    "stale_lease_repair": {"target_percent": 99.0, "threshold_seconds": 300,
                           "window_days": 28, "degrade": "pause_admission"},
    "action_reconciliation": {"target_percent": 99.0,
                              "threshold_seconds": 900, "window_days": 28,
                              "degrade": "disable_capability"},
    "projection_freshness": {"target_percent": 99.0, "threshold_seconds": 5,
                             "window_days": 28, "degrade": "snapshot_reads"},
    "background_pilot_start": {
        "target_percent": 99.0, "threshold_seconds": 30,
        "window_days": 28, "degrade": "disable_background_pilot_admission"},
    "background_pilot_completion": {
        "target_percent": 99.0, "threshold_seconds": 120,
        "window_days": 28, "degrade": "activate_background_pilot_kill_switch"},
}

QUEUE_CAPACITY_POLICIES: dict[str, dict[str, int]] = {
    "interactive-steps": {"max_depth": 2_000, "max_oldest_age_seconds": 30},
    "provider-events": {"max_depth": 10_000, "max_oldest_age_seconds": 60},
    "discovery-ingestion": {"max_depth": 5_000, "max_oldest_age_seconds": 900},
    "browser-actions": {"max_depth": 500, "max_oldest_age_seconds": 300},
    "timers-expiry": {"max_depth": 10_000, "max_oldest_age_seconds": 60},
    "reconciliation": {"max_depth": 2_000, "max_oldest_age_seconds": 900},
    "background-pilot": {"max_depth": 100, "max_oldest_age_seconds": 120},
}

BUDGET_DIMENSIONS = frozenset({
    "model_tokens", "model_cost_micros", "provider_calls",
    "artifact_bytes", "browser_seconds",
})
CHANGE_VERSION_KEYS = frozenset({
    "application", "policy", "capability_registry", "model_routing",
})
CHAOS_SCENARIOS = frozenset({
    "QUEUE_DUPLICATION", "DATASTORE_CONTENTION", "PROVIDER_OUTAGE",
    "WORKER_DEATH", "PARTIAL_REGIONAL_FAILURE",
})

RPO_SECONDS = 15 * 60
RTO_SECONDS = 60 * 60


def _age_seconds(value: str | None, now: datetime) -> float:
    try:
        return max(0.0, (now - datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True,
            "error_code": code, "message": message}


def _bounded_counts(values: dict[str, Any], allowed: frozenset[str]
                    ) -> dict[str, int] | None:
    if not values or not set(values).issubset(allowed):
        return None
    normalized: dict[str, int] = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        normalized[key] = value
    return normalized


class PlatformOperationsService:
    """Workspace-scoped operational evidence and deterministic controls."""

    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    async def _list_all(self, collection: str, *, filters: dict[str, Any],
                        page_size: int = 500) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: tuple[str, Any] | None = None
        while True:
            page = await self.store.list(
                collection, filters=filters, order_by="id",
                limit=max(1, min(page_size, 1000)), start_after=cursor)
            rows.extend(page)
            if len(page) < page_size:
                return rows
            next_id = str(page[-1].get("id") or "")
            if not next_id or (cursor and next_id <= str(cursor[1])):
                raise RuntimeError("durable pagination did not advance")
            cursor = ("id", next_id)

    async def inspect_workspace(self, workspace_id: str) -> dict[str, Any]:
        """Find every actionable durable ID without reading user content."""
        if not workspace_id:
            return _error("workspace_required", "Workspace is required.")
        now = datetime.now(timezone.utc)
        issues: list[dict[str, str]] = []
        scope = {"workspace_id": workspace_id}
        runs = await self._list_all("workflow_runs", filters=scope)
        waits = await self._list_all("waits", filters=scope)
        steps = await self._list_all("workflow_steps", filters=scope)
        actions = await self._list_all("external_actions", filters=scope)
        approvals = await self._list_all("approvals", filters=scope)
        deliveries = await self._list_all("wake_deliveries", filters=scope)
        open_wait_runs = {row["run_id"] for row in waits
                          if row.get("status") == "OPEN"}
        active_lease_runs = {
            row["run_id"] for row in steps
            if row.get("status") == "RUNNING"
            and str(row.get("lease_expires_at") or "") > utc_now()
        }
        for run in runs:
            if (run.get("runtime_status") == "RUNNING"
                    and run["run_id"] not in open_wait_runs
                    and run["run_id"] not in active_lease_runs
                    and _age_seconds(run.get("updated_at"), now) > 300):
                issues.append({"kind": "STUCK_RUN", "record_id": run["run_id"],
                               "run_id": run["run_id"],
                               "runbook": "fence_or_replay_outbox"})
        for wait in waits:
            due = str(wait.get("wake_after") or wait.get("expires_at") or "")
            if wait.get("status") == "OPEN" and due and due <= utc_now():
                issues.append({"kind": "OVERDUE_WAIT", "record_id": wait["wait_id"],
                               "run_id": wait["run_id"],
                               "runbook": "deliver_timer_checkpoint"})
        for action in actions:
            status = str(action.get("status") or "")
            age = _age_seconds(action.get("updated_at"), now)
            if ((status == "PREPARED"
                 and str(action.get("lease_expires_at") or "") <= utc_now())
                    or (status in {"EXECUTING", "UNCERTAIN"} and age > 900)):
                issues.append({"kind": "ACTION_RECONCILIATION",
                               "record_id": action["action_id"],
                               "run_id": str(action.get("run_id") or ""),
                               "runbook": "provider_read_only_reconcile"})
        for approval in approvals:
            if (approval.get("status") == "CLAIMED"
                    and str(approval.get("claim_lease_expires_at") or "") <= utc_now()):
                issues.append({"kind": "STRANDED_APPROVAL",
                               "record_id": approval["approval_id"],
                               "run_id": str(approval.get("run_id") or ""),
                               "runbook": "inspect_claimed_action"})
        for delivery in deliveries:
            if delivery.get("status") == "DEAD_LETTER":
                issues.append({"kind": "DEAD_LETTER",
                               "record_id": delivery["delivery_id"],
                               "run_id": str(delivery.get("run_id") or ""),
                               "runbook": "requeue_delivery"})
        for issue in issues:
            inbox_id = stable_id(
                "inbox", workspace_id, issue["kind"], issue["record_id"])
            await self.store.create("founder_inbox", inbox_id, {
                "schema_version": 1, "inbox_item_id": inbox_id,
                "workspace_id": workspace_id, "founder_id": workspace_id,
                "item_kind": "OPERATOR_ATTENTION", "title": issue["kind"],
                "summary": f"Record {issue['record_id']} requires {issue['runbook']}.",
                "run_id": issue["run_id"], "status": "UNREAD",
                "created_at": utc_now(), "updated_at": utc_now(), "version": 1})
        snapshot_id = stable_id("opssnapshot", workspace_id, utc_now())
        snapshot = {
            "schema_version": 2, "snapshot_id": snapshot_id,
            "workspace_id": workspace_id,
            "counts": {kind: sum(1 for issue in issues if issue["kind"] == kind)
                       for kind in sorted({issue["kind"] for issue in issues})},
            "scanned_counts": {"runs": len(runs), "waits": len(waits),
                               "steps": len(steps), "actions": len(actions),
                               "approvals": len(approvals),
                               "deliveries": len(deliveries)},
            "issue_ids": [issue["record_id"] for issue in issues],
            "slo_version": "phase7-v2", "created_at": utc_now(), "version": 1,
        }
        await self.store.create("operational_snapshots", snapshot_id, snapshot)
        return {"status": "success", "issues": issues, "snapshot": snapshot}

    async def record_slo_observation(
            self, *, workspace_id: str, objective_id: str,
            idempotency_key: str, observed_seconds: float,
            dependency_success: bool, occurred_at: str | None = None
            ) -> dict[str, Any]:
        objective = SERVICE_OBJECTIVES.get(objective_id)
        if (not workspace_id or not idempotency_key or objective is None
                or isinstance(observed_seconds, bool)
                or not isinstance(observed_seconds, (int, float))
                or observed_seconds < 0):
            return _error("slo_observation_invalid", "SLO observation is invalid.")
        observation_id = stable_id(
            "slo", workspace_id, objective_id, idempotency_key)
        good = bool(dependency_success
                    and observed_seconds <= objective["threshold_seconds"])
        row = {
            "schema_version": 1, "observation_id": observation_id,
            "workspace_id": workspace_id, "objective_id": objective_id,
            "observed_milliseconds": int(observed_seconds * 1000),
            "good": good, "occurred_at": occurred_at or utc_now(),
            "created_at": utc_now(), "version": 1,
        }
        created = await self.store.create("slo_observations", observation_id, row)
        stored = row if created else await self.store.get(
            "slo_observations", observation_id)
        if (not created and any(stored.get(key) != row.get(key) for key in (
                "workspace_id", "objective_id", "observed_milliseconds", "good"))):
            return _error("idempotency_conflict",
                          "SLO key names another observation.")
        return {"status": "success", "duplicate": not created,
                "observation": stored}

    async def error_budget_report(self, *, workspace_id: str,
                                  objective_id: str) -> dict[str, Any]:
        objective = SERVICE_OBJECTIVES.get(objective_id)
        if not workspace_id or objective is None:
            return _error("slo_objective_invalid", "SLO objective is unknown.")
        rows = await self._list_all(
            "slo_observations",
            filters={"workspace_id": workspace_id,
                     "objective_id": objective_id})
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=int(objective["window_days"]))
        rows = [row for row in rows
                if _age_seconds(str(row.get("occurred_at") or ""), cutoff) == 0.0]
        total = len(rows)
        bad = sum(1 for row in rows if not row.get("good"))
        budget_fraction = 1.0 - float(objective["target_percent"]) / 100.0
        consumed_fraction = (bad / total) if total else 0.0
        status = ("NO_DATA" if total == 0 else
                  "EXHAUSTED" if consumed_fraction > budget_fraction else
                  "HEALTHY")
        return {"status": "success", "objective_id": objective_id,
                "measurement_count": total, "bad_count": bad,
                "target_percent": objective["target_percent"],
                "observed_percent": (100.0 if total == 0
                                     else 100.0 * (total - bad) / total),
                "remaining_error_budget_fraction": max(
                    0.0, budget_fraction - consumed_fraction),
                "budget_status": status,
                "release_blocked": status != "HEALTHY",
                "degrade": objective["degrade"]}

    def queue_capacity_report(self, metrics: dict[str, dict[str, Any]]
                              ) -> dict[str, Any]:
        if set(metrics) != set(QUEUE_CAPACITY_POLICIES):
            return _error("queue_policy_incomplete",
                          "Every registered queue requires a metric.")
        queues: dict[str, dict[str, Any]] = {}
        for name, policy in QUEUE_CAPACITY_POLICIES.items():
            values = metrics[name]
            depth = values.get("depth")
            oldest = values.get("oldest_age_seconds")
            if (isinstance(depth, bool) or not isinstance(depth, int) or depth < 0
                    or isinstance(oldest, bool)
                    or not isinstance(oldest, (int, float)) or oldest < 0):
                return _error("queue_metric_invalid", "Queue metric is invalid.")
            over = (depth >= policy["max_depth"]
                    or oldest >= policy["max_oldest_age_seconds"])
            queues[name] = {**policy, "depth": depth,
                            "oldest_age_seconds": oldest,
                            "status": "SHED_NEW_WORK" if over else "HEALTHY"}
        return {"status": "success", "queues": queues,
                "admit_new_work": all(
                    row["status"] == "HEALTHY" for row in queues.values())}

    async def configure_workspace_budget(
            self, *, workspace_id: str, window_id: str,
            limits: dict[str, Any], actor_id: str, reason: str
            ) -> dict[str, Any]:
        normalized = _bounded_counts(limits, BUDGET_DIMENSIONS)
        if (not workspace_id or not window_id or not actor_id or not reason
                or normalized is None):
            return _error("budget_contract_invalid", "Budget policy is invalid.")
        budget_id = stable_id("workspacebudget", workspace_id, window_id)
        existing = await self.store.get("workspace_budgets", budget_id)
        now = utc_now()
        if existing:
            usage = dict(existing.get("usage") or {})
            if any(int(usage.get(key) or 0) > limit
                   for key, limit in normalized.items()):
                return _error("budget_below_committed_usage",
                              "Budget cannot be lower than committed usage.")
            committed = await self.store.compare_and_set(
                "workspace_budgets", budget_id, int(existing["version"]), {
                    "limits": normalized, "changed_by_actor_id": actor_id,
                    "change_reason": reason[:240], "updated_at": now})
            if not committed:
                return _error("concurrency_conflict", "Budget changed concurrently.")
            return {"status": "success", "budget": committed}
        row = {
            "schema_version": 1, "budget_id": budget_id,
            "workspace_id": workspace_id, "window_id": window_id,
            "limits": normalized, "usage": {key: 0 for key in normalized},
            "changed_by_actor_id": actor_id, "change_reason": reason[:240],
            "created_at": now, "updated_at": now, "version": 1,
        }
        created = await self.store.create("workspace_budgets", budget_id, row)
        if not created:
            return _error("concurrency_conflict", "Budget was created concurrently.")
        return {"status": "success", "budget": row}

    async def consume_workspace_budget(
            self, *, workspace_id: str, window_id: str,
            deltas: dict[str, Any], idempotency_key: str,
            attribution: dict[str, str]) -> dict[str, Any]:
        normalized = _bounded_counts(deltas, BUDGET_DIMENSIONS)
        allowed_attribution = {"run_id", "model_id", "provider_id",
                               "capability_id"}
        if (not workspace_id or not window_id or not idempotency_key
                or normalized is None or not attribution
                or not set(attribution).issubset(
                    allowed_attribution)
                or any(not isinstance(value, str) for value in attribution.values())):
            return _error("budget_consumption_invalid", "Budget usage is invalid.")
        budget_id = stable_id("workspacebudget", workspace_id, window_id)
        receipt_id = stable_id(
            "budgetreceipt", workspace_id, window_id, idempotency_key)
        existing_receipt = await self.store.get(
            "budget_consumption_receipts", receipt_id)
        if existing_receipt:
            if (existing_receipt.get("deltas") != normalized
                    or existing_receipt.get("attribution") != attribution):
                return _error("idempotency_conflict",
                              "Budget key names another consumption.")
            return {"status": "success", "duplicate": True,
                    "receipt": existing_receipt}
        for _ in range(5):
            budget = await self.store.get("workspace_budgets", budget_id)
            if not budget or budget.get("workspace_id") != workspace_id:
                return _error("budget_not_configured", "Workspace budget is absent.")
            limits = dict(budget.get("limits") or {})
            usage = dict(budget.get("usage") or {})
            if not set(normalized).issubset(limits):
                return _error("budget_dimension_unconfigured",
                              "Budget dimension is not configured.")
            next_usage = {**usage}
            exhausted = []
            for key, delta in normalized.items():
                next_usage[key] = int(usage.get(key) or 0) + delta
                if next_usage[key] > int(limits[key]):
                    exhausted.append(key)
            if exhausted:
                return {**_error("workspace_budget_exhausted",
                                 "Workspace budget is exhausted."),
                        "exhausted_dimensions": sorted(exhausted)}
            now = utc_now()
            receipt = {
                "schema_version": 1, "budget_receipt_id": receipt_id,
                "workspace_id": workspace_id, "budget_id": budget_id,
                "window_id": window_id, "deltas": normalized,
                "attribution": attribution, "created_at": now, "version": 1,
            }
            committed = await self.store.atomic_compare_and_set((
                AtomicMutation(
                    "workspace_budgets", budget_id, int(budget["version"]),
                    updates={"usage": next_usage, "updated_at": now}),
                AtomicMutation(
                    "budget_consumption_receipts", receipt_id, None,
                    record=receipt),
            ))
            if committed:
                return {"status": "success", "duplicate": False,
                        "receipt": committed[
                            ("budget_consumption_receipts", receipt_id)],
                        "budget": committed[("workspace_budgets", budget_id)]}
            existing_receipt = await self.store.get(
                "budget_consumption_receipts", receipt_id)
            if existing_receipt:
                if (existing_receipt.get("deltas") != normalized
                        or existing_receipt.get("attribution") != attribution):
                    return _error("idempotency_conflict",
                                  "Budget key names another consumption.")
                return {"status": "success", "duplicate": True,
                        "receipt": existing_receipt}
        return _error("concurrency_conflict", "Budget contention exceeded retries.")

    async def start_change(
            self, *, change_idempotency_key: str,
            current_versions: dict[str, str], target_versions: dict[str, str],
            canary_workspace_ids: list[str], actor_id: str,
            decision_ref: str) -> dict[str, Any]:
        values = (*current_versions.values(), *target_versions.values())
        if (not change_idempotency_key or not actor_id or not decision_ref
                or not current_versions or not target_versions
                or not set(current_versions).issubset(CHANGE_VERSION_KEYS)
                or set(current_versions) != set(target_versions)
                or current_versions == target_versions
                or any(not value for value in values)
                or not canary_workspace_ids
                or len(set(canary_workspace_ids)) != len(canary_workspace_ids)):
            return _error("change_contract_invalid", "Change contract is invalid.")
        change_id = stable_id("change", change_idempotency_key)
        existing = await self.store.get("change_rollouts", change_id)
        if existing:
            same = all(existing.get(key) == value for key, value in {
                "current_versions": current_versions,
                "target_versions": target_versions,
                "canary_workspace_ids": sorted(canary_workspace_ids),
                "actor_id": actor_id, "decision_ref": decision_ref,
            }.items())
            return ({"status": "success", "duplicate": True, "change": existing}
                    if same else _error(
                        "idempotency_conflict", "Change key names another rollout."))
        now = utc_now()
        row = {
            "schema_version": 1, "change_id": change_id,
            "workspace_id": "platform", "current_versions": current_versions,
            "target_versions": target_versions,
            "canary_workspace_ids": sorted(canary_workspace_ids),
            "status": "CANARY", "actor_id": actor_id,
            "decision_ref": decision_ref, "observation": None,
            "created_at": now, "updated_at": now, "version": 1,
        }
        created = await self.store.create("change_rollouts", change_id, row)
        return {"status": "success", "duplicate": not created,
                "change": row if created else await self.store.get(
                    "change_rollouts", change_id)}

    async def evaluate_change(
            self, *, change_id: str, sample_count: int, error_count: int,
            projection_mismatches: int, security_control_failures: int,
            min_sample_count: int = 100, max_error_rate: float = 0.01
            ) -> dict[str, Any]:
        change = await self.store.get("change_rollouts", change_id)
        values = (sample_count, error_count, projection_mismatches,
                  security_control_failures, min_sample_count)
        if (not change or change.get("status") not in {"CANARY", "OBSERVING"}
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or value < 0 for value in values)
                or error_count > sample_count or not 0 <= max_error_rate < 1):
            return _error("change_evidence_invalid", "Canary evidence is invalid.")
        error_rate = error_count / max(1, sample_count)
        unsafe = (projection_mismatches > 0 or security_control_failures > 0
                  or error_rate > max_error_rate)
        next_status = ("ROLLBACK_REQUIRED" if unsafe else
                       "PROMOTED" if sample_count >= min_sample_count else
                       "OBSERVING")
        observation = {
            "sample_count": sample_count, "error_count": error_count,
            "error_rate": error_rate,
            "projection_mismatches": projection_mismatches,
            "security_control_failures": security_control_failures,
            "min_sample_count": min_sample_count,
            "max_error_rate": max_error_rate,
        }
        committed = await self.store.compare_and_set(
            "change_rollouts", change_id, int(change["version"]), {
                "status": next_status, "observation": observation,
                "rollback_versions": (change["current_versions"]
                                      if next_status == "ROLLBACK_REQUIRED"
                                      else None),
                "updated_at": utc_now()})
        if not committed:
            return _error("concurrency_conflict", "Canary changed concurrently.")
        return {"status": "success", "decision": next_status,
                "change": committed}

    async def record_chaos_drill(
            self, *, workspace_id: str, scenario: str,
            drill_idempotency_key: str, evidence_ref: str,
            duplicate_effects: int, orphaned_waits: int,
            orphaned_actions: int, lost_receipts: int,
            projection_mismatches: int) -> dict[str, Any]:
        counts = (duplicate_effects, orphaned_waits, orphaned_actions,
                  lost_receipts, projection_mismatches)
        if (not workspace_id or scenario not in CHAOS_SCENARIOS
                or not drill_idempotency_key or not evidence_ref
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or value < 0 for value in counts)):
            return _error("chaos_evidence_invalid", "Chaos evidence is invalid.")
        passed = all(value == 0 for value in counts)
        drill_id = stable_id(
            "chaosdrill", workspace_id, scenario, drill_idempotency_key)
        row = {
            "schema_version": 1, "chaos_drill_id": drill_id,
            "workspace_id": workspace_id, "scenario": scenario,
            "evidence_ref": evidence_ref,
            "duplicate_effects": duplicate_effects,
            "orphaned_waits": orphaned_waits,
            "orphaned_actions": orphaned_actions,
            "lost_receipts": lost_receipts,
            "projection_mismatches": projection_mismatches,
            "status": "PASSED" if passed else "FAILED",
            "created_at": utc_now(), "version": 1,
        }
        created = await self.store.create("chaos_drills", drill_id, row)
        stored = row if created else await self.store.get("chaos_drills", drill_id)
        bindings = ("workspace_id", "scenario", "evidence_ref",
                    "duplicate_effects", "orphaned_waits", "orphaned_actions",
                    "lost_receipts", "projection_mismatches")
        if not created and any(stored.get(key) != row.get(key) for key in bindings):
            return _error("idempotency_conflict",
                          "Chaos drill key names different evidence.")
        return {"status": "success" if stored.get("status") == "PASSED" else "error",
                "error": stored.get("status") != "PASSED",
                "duplicate": not created, "drill": stored}

    async def record_migration_drill(
            self, *, workspace_id: str, migration_id: str,
            drill_idempotency_key: str, direction: str, evidence_ref: str,
            authority_checksum_match: bool, orphaned_waits: int,
            orphaned_actions: int) -> dict[str, Any]:
        if (not workspace_id or not migration_id or not drill_idempotency_key
                or direction not in {"ROLL_FORWARD", "ROLLBACK"}
                or not evidence_ref
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or value < 0 for value in (orphaned_waits, orphaned_actions))):
            return _error("migration_evidence_invalid",
                          "Migration drill evidence is invalid.")
        passed = (authority_checksum_match and orphaned_waits == 0
                  and orphaned_actions == 0)
        drill_id = stable_id(
            "migrationdrill", workspace_id, migration_id, direction,
            drill_idempotency_key)
        row = {
            "schema_version": 1, "migration_drill_id": drill_id,
            "workspace_id": workspace_id, "migration_id": migration_id,
            "direction": direction, "evidence_ref": evidence_ref,
            "authority_checksum_match": authority_checksum_match,
            "orphaned_waits": orphaned_waits,
            "orphaned_actions": orphaned_actions,
            "status": "PASSED" if passed else "FAILED",
            "created_at": utc_now(), "version": 1,
        }
        created = await self.store.create("migration_drills", drill_id, row)
        stored = row if created else await self.store.get(
            "migration_drills", drill_id)
        bindings = ("workspace_id", "migration_id", "direction", "evidence_ref",
                    "authority_checksum_match", "orphaned_waits",
                    "orphaned_actions")
        if not created and any(stored.get(key) != row.get(key) for key in bindings):
            return _error("idempotency_conflict",
                          "Migration drill key names different evidence.")
        return {"status": "success" if stored.get("status") == "PASSED" else "error",
                "error": stored.get("status") != "PASSED",
                "duplicate": not created, "drill": stored}

    async def record_recovery_drill(
            self, *, workspace_id: str, drill_idempotency_key: str,
            drill_kind: str, source_region: str, recovery_region: str,
            backup_id: str, evidence_ref: str,
            backup_age_seconds: int, restore_duration_seconds: int,
            deletion_tombstones_replayed: bool,
            provider_actions_reconciled: bool,
            authority_checksum_match: bool,
            open_waits_preserved: bool,
            nonterminal_actions_preserved: bool) -> dict[str, Any]:
        ints = (backup_age_seconds, restore_duration_seconds)
        if (not workspace_id or not drill_idempotency_key
                or drill_kind not in {"BACKUP_RESTORE", "REGIONAL_FAILOVER"}
                or not source_region or not recovery_region
                or not backup_id or not evidence_ref
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or value < 0 for value in ints)):
            return _error("recovery_evidence_invalid", "Recovery evidence is invalid.")
        passed = (backup_age_seconds <= RPO_SECONDS
                  and restore_duration_seconds <= RTO_SECONDS
                  and deletion_tombstones_replayed
                  and provider_actions_reconciled
                  and authority_checksum_match
                  and open_waits_preserved
                  and nonterminal_actions_preserved
                  and (drill_kind != "REGIONAL_FAILOVER"
                       or source_region != recovery_region))
        drill_id = stable_id(
            "recoverydrill", workspace_id, drill_idempotency_key)
        row = {
            "schema_version": 2, "recovery_drill_id": drill_id,
            "workspace_id": workspace_id, "drill_kind": drill_kind,
            "source_region": source_region, "recovery_region": recovery_region,
            "backup_id": backup_id, "evidence_ref": evidence_ref,
            "rpo_objective_seconds": RPO_SECONDS,
            "rto_objective_seconds": RTO_SECONDS,
            "observed_backup_age_seconds": backup_age_seconds,
            "observed_restore_duration_seconds": restore_duration_seconds,
            "deletion_tombstones_replayed": deletion_tombstones_replayed,
            "provider_actions_reconciled": provider_actions_reconciled,
            "authority_checksum_match": authority_checksum_match,
            "open_waits_preserved": open_waits_preserved,
            "nonterminal_actions_preserved": nonterminal_actions_preserved,
            "status": "PASSED" if passed else "FAILED",
            "created_at": utc_now(), "version": 1,
        }
        created = await self.store.create("recovery_drills", drill_id, row)
        stored = row if created else await self.store.get("recovery_drills", drill_id)
        bindings = (
            "workspace_id", "drill_kind", "source_region", "recovery_region",
            "backup_id", "evidence_ref", "observed_backup_age_seconds",
            "observed_restore_duration_seconds", "deletion_tombstones_replayed",
            "provider_actions_reconciled", "authority_checksum_match",
            "open_waits_preserved", "nonterminal_actions_preserved")
        if not created and any(stored.get(key) != row.get(key) for key in bindings):
            return _error("idempotency_conflict",
                          "Recovery drill key names different evidence.")
        return {"status": "success" if stored.get("status") == "PASSED" else "error",
                "error": stored.get("status") != "PASSED",
                "duplicate": not created, "drill": stored}

    async def governance_report(self, workspace_id: str) -> dict[str, Any]:
        if not workspace_id:
            return _error("workspace_required", "Workspace is required.")
        scope = {"workspace_id": workspace_id}
        runs = await self._list_all("workflow_runs", filters=scope)
        actions = await self._list_all("external_actions", filters=scope)
        memories = await self._list_all("memory_items", filters=scope)
        attempts = await self._list_all("step_attempts", filters=scope)
        budgets = await self._list_all("workspace_budgets", filters=scope)
        rollouts = await self._list_all("change_rollouts", filters={})
        report_id = stable_id("governance", workspace_id, utc_now())
        row = {
            "schema_version": 2, "governance_report_id": report_id,
            "workspace_id": workspace_id,
            "policy_versions": sorted({str(item.get("policy_version"))
                                       for item in runs
                                       if item.get("policy_version")}),
            "plan_hashes": sorted({str(item.get("plan_hash")) for item in runs
                                   if item.get("plan_hash")}),
            "capability_versions": sorted({
                f"{item.get('capability_id')}@{item.get('capability_version')}"
                for item in actions if item.get("capability_id")}),
            "model_versions": sorted({str(item.get("model_version")
                                          or item.get("model_id"))
                                      for item in attempts
                                      if item.get("model_version")
                                      or item.get("model_id")}),
            "memory_policy_versions": sorted({
                str(item.get("writer_policy_version")) for item in memories
                if item.get("writer_policy_version")}),
            "change_versions": [
                {"change_id": item.get("change_id"),
                 "status": item.get("status"),
                 "target_versions": item.get("target_versions")}
                for item in rollouts if item.get("status") in {
                    "CANARY", "OBSERVING", "PROMOTED", "ROLLBACK_REQUIRED"}],
            "budget_windows": [
                {"window_id": item.get("window_id"),
                 "limits": item.get("limits"), "usage": item.get("usage")}
                for item in budgets],
            "run_count": len(runs), "action_count": len(actions),
            "memory_count": len(memories), "attempt_count": len(attempts),
            "created_at": utc_now(), "version": 1,
        }
        await self.store.create("governance_reports", report_id, row)
        return {"status": "success", "report": row}


def admission_decision(*, priority: str, queue_depth: int,
                       workspace_budget_remaining: int) -> dict[str, Any]:
    """Deterministic load shedding preserves existing waits/actions first."""
    if workspace_budget_remaining <= 0:
        return {"admit": False, "reason": "workspace_budget_exhausted"}
    thresholds = {"HIGH": 10_000, "NORMAL": 2_000, "LOW": 500}
    threshold = thresholds.get(priority)
    if threshold is None:
        return {"admit": False, "reason": "priority_invalid"}
    return {"admit": queue_depth < threshold,
            "reason": "admitted" if queue_depth < threshold else "load_shed"}
