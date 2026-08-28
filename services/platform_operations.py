"""Content-free operational controls, SLO evidence, and recovery drills."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.durable_store import DurableStore, production_store
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
}

RPO_SECONDS = 15 * 60
RTO_SECONDS = 60 * 60


def _age_seconds(value: str, now: datetime) -> float:
    try:
        return max(0.0, (now - datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


class PlatformOperationsService:
    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    async def inspect_workspace(self, workspace_id: str) -> dict[str, Any]:
        """Find actionable durable IDs without reading user content fields."""
        now = datetime.now(timezone.utc)
        issues: list[dict[str, str]] = []
        runs = await self.store.list(
            "workflow_runs", filters={"workspace_id": workspace_id}, limit=1000)
        waits = await self.store.list(
            "waits", filters={"workspace_id": workspace_id}, limit=1000)
        steps = await self.store.list(
            "workflow_steps", filters={"workspace_id": workspace_id}, limit=1000)
        actions = await self.store.list(
            "external_actions", filters={"workspace_id": workspace_id}, limit=1000)
        approvals = await self.store.list(
            "approvals", filters={"workspace_id": workspace_id}, limit=1000)
        deliveries = await self.store.list(
            "wake_deliveries", filters={"workspace_id": workspace_id}, limit=1000)
        open_wait_runs = {row["run_id"] for row in waits if row.get("status") == "OPEN"}
        active_lease_runs = {row["run_id"] for row in steps
                             if row.get("status") == "RUNNING"
                             and str(row.get("lease_expires_at") or "") > utc_now()}
        for run in runs:
            if (run.get("runtime_status") == "RUNNING"
                    and run["run_id"] not in open_wait_runs
                    and run["run_id"] not in active_lease_runs
                    and _age_seconds(run.get("updated_at"), now) > 300):
                issues.append({"kind": "STUCK_RUN", "record_id": run["run_id"],
                               "run_id": run["run_id"], "runbook": "fence_or_replay_outbox"})
        for wait in waits:
            due = str(wait.get("wake_after") or wait.get("expires_at") or "")
            if wait.get("status") == "OPEN" and due and due <= utc_now():
                issues.append({"kind": "OVERDUE_WAIT", "record_id": wait["wait_id"],
                               "run_id": wait["run_id"], "runbook": "deliver_timer_checkpoint"})
        for action in actions:
            status = str(action.get("status") or "")
            age = _age_seconds(action.get("updated_at"), now)
            if ((status == "PREPARED" and str(action.get("lease_expires_at") or "") <= utc_now())
                    or status in {"EXECUTING", "UNCERTAIN"} and age > 900):
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
            "schema_version": 1, "snapshot_id": snapshot_id,
            "workspace_id": workspace_id,
            "counts": {kind: sum(1 for issue in issues if issue["kind"] == kind)
                       for kind in sorted({issue["kind"] for issue in issues})},
            "issue_ids": [issue["record_id"] for issue in issues],
            "slo_version": "phase7-v1", "created_at": utc_now(), "version": 1,
        }
        await self.store.create("operational_snapshots", snapshot_id, snapshot)
        return {"status": "success", "issues": issues, "snapshot": snapshot}

    async def record_recovery_drill(
            self, *, workspace_id: str, drill_idempotency_key: str,
            backup_age_seconds: int, restore_duration_seconds: int,
            deletion_tombstones_replayed: bool,
            provider_actions_reconciled: bool,
            authority_checksum_match: bool) -> dict[str, Any]:
        drill_id = stable_id(
            "recoverydrill", workspace_id, drill_idempotency_key)
        passed = (0 <= backup_age_seconds <= RPO_SECONDS
                  and 0 <= restore_duration_seconds <= RTO_SECONDS
                  and deletion_tombstones_replayed
                  and provider_actions_reconciled
                  and authority_checksum_match)
        row = {
            "schema_version": 1, "recovery_drill_id": drill_id,
            "workspace_id": workspace_id, "rpo_objective_seconds": RPO_SECONDS,
            "rto_objective_seconds": RTO_SECONDS,
            "observed_backup_age_seconds": backup_age_seconds,
            "observed_restore_duration_seconds": restore_duration_seconds,
            "deletion_tombstones_replayed": deletion_tombstones_replayed,
            "provider_actions_reconciled": provider_actions_reconciled,
            "authority_checksum_match": authority_checksum_match,
            "status": "PASSED" if passed else "FAILED",
            "created_at": utc_now(), "version": 1,
        }
        created = await self.store.create("recovery_drills", drill_id, row)
        return {"status": "success" if passed else "error",
                "error": not passed, "duplicate": not created,
                "drill": row if created else await self.store.get(
                    "recovery_drills", drill_id)}

    async def governance_report(self, workspace_id: str) -> dict[str, Any]:
        runs = await self.store.list(
            "workflow_runs", filters={"workspace_id": workspace_id}, limit=1000)
        actions = await self.store.list(
            "external_actions", filters={"workspace_id": workspace_id}, limit=1000)
        memories = await self.store.list(
            "memory_items", filters={"workspace_id": workspace_id}, limit=1000)
        report_id = stable_id("governance", workspace_id, utc_now())
        row = {
            "schema_version": 1, "governance_report_id": report_id,
            "workspace_id": workspace_id,
            "policy_versions": sorted({str(row.get("policy_version") or "")
                                       for row in runs if row.get("policy_version")}),
            "plan_hashes": sorted({str(row.get("plan_hash") or "")
                                   for row in runs if row.get("plan_hash")}),
            "capability_versions": sorted({
                f"{row.get('capability_id')}@{row.get('capability_version')}"
                for row in actions if row.get("capability_id")}),
            "memory_policy_versions": sorted({
                str(row.get("writer_policy_version")) for row in memories
                if row.get("writer_policy_version")}),
            "run_count": len(runs), "action_count": len(actions),
            "memory_count": len(memories), "created_at": utc_now(), "version": 1,
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
