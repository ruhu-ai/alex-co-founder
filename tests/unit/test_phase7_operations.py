from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.durable_store import InMemoryDurableStore
from services.platform_operations import (
    QUEUE_CAPACITY_POLICIES,
    SERVICE_OBJECTIVES,
    PlatformOperationsService,
    admission_decision,
)

pytestmark = pytest.mark.asyncio


async def test_operator_scan_surfaces_durable_ids_without_user_content():
    store = InMemoryDurableStore()
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    await store.create("workflow_runs", "run-1", {
        "run_id": "run-1", "workspace_id": "workspace-a",
        "runtime_status": "RUNNING", "updated_at": old,
        "secret_user_text": "must never enter ops", "version": 1})
    await store.create("external_actions", "action-1", {
        "action_id": "action-1", "workspace_id": "workspace-a",
        "run_id": "run-1", "status": "UNCERTAIN", "updated_at": old,
        "payload": "private email body", "version": 1})
    await store.create("wake_deliveries", "wake-1", {
        "delivery_id": "wake-1", "workspace_id": "workspace-a",
        "run_id": "run-1", "status": "DEAD_LETTER", "version": 1})

    result = await PlatformOperationsService(store).inspect_workspace("workspace-a")
    assert {issue["kind"] for issue in result["issues"]} == {
        "STUCK_RUN", "ACTION_RECONCILIATION", "DEAD_LETTER"}
    serialized = str(result)
    assert "private email body" not in serialized
    assert "must never enter ops" not in serialized
    assert len(await store.list(
        "founder_inbox", filters={"workspace_id": "workspace-a"})) == 3


async def test_operator_scan_paginates_past_firestore_query_limit():
    store = InMemoryDurableStore()
    for index in range(1_005):
        await store.create("workflow_runs", f"run-{index:04d}", {
            "run_id": f"run-{index:04d}", "workspace_id": "workspace-a",
            "runtime_status": "SUCCEEDED", "updated_at": "2026-01-01T00:00:00+00:00",
            "version": 1})
    result = await PlatformOperationsService(store).inspect_workspace("workspace-a")
    assert result["snapshot"]["scanned_counts"]["runs"] == 1_005


async def test_slo_observations_are_idempotent_and_exhaust_error_budget():
    store = InMemoryDurableStore()
    ops = PlatformOperationsService(store)
    good = await ops.record_slo_observation(
        workspace_id="platform", objective_id="command_ack",
        idempotency_key="good", observed_seconds=0.2,
        dependency_success=True)
    duplicate = await ops.record_slo_observation(
        workspace_id="platform", objective_id="command_ack",
        idempotency_key="good", observed_seconds=0.2,
        dependency_success=True)
    conflict = await ops.record_slo_observation(
        workspace_id="platform", objective_id="command_ack",
        idempotency_key="good", observed_seconds=4,
        dependency_success=True)
    await ops.record_slo_observation(
        workspace_id="platform", objective_id="command_ack",
        idempotency_key="bad", observed_seconds=3,
        dependency_success=True)
    report = await ops.error_budget_report(
        workspace_id="platform", objective_id="command_ack")
    assert good["observation"]["good"] is True
    assert duplicate["duplicate"] is True
    assert conflict["error_code"] == "idempotency_conflict"
    assert report["budget_status"] == "EXHAUSTED"
    assert report["release_blocked"] is True


async def test_queue_capacity_and_workspace_budget_fail_closed():
    store = InMemoryDurableStore()
    ops = PlatformOperationsService(store)
    metrics = {name: {"depth": 0, "oldest_age_seconds": 0}
               for name in QUEUE_CAPACITY_POLICIES}
    assert ops.queue_capacity_report(metrics)["admit_new_work"] is True
    metrics["browser-actions"]["depth"] = 500
    assert ops.queue_capacity_report(metrics)["admit_new_work"] is False

    configured = await ops.configure_workspace_budget(
        workspace_id="workspace-a", window_id="2026-08",
        limits={"model_tokens": 100, "provider_calls": 1},
        actor_id="owner-a", reason="monthly guardrail")
    assert configured["status"] == "success"
    first = await ops.consume_workspace_budget(
        workspace_id="workspace-a", window_id="2026-08",
        deltas={"model_tokens": 40, "provider_calls": 1},
        idempotency_key="attempt-1", attribution={"run_id": "run-1"})
    duplicate = await ops.consume_workspace_budget(
        workspace_id="workspace-a", window_id="2026-08",
        deltas={"model_tokens": 40, "provider_calls": 1},
        idempotency_key="attempt-1", attribution={"run_id": "run-1"})
    conflict = await ops.consume_workspace_budget(
        workspace_id="workspace-a", window_id="2026-08",
        deltas={"model_tokens": 1}, idempotency_key="attempt-1",
        attribution={"run_id": "run-1"})
    exhausted = await ops.consume_workspace_budget(
        workspace_id="workspace-a", window_id="2026-08",
        deltas={"provider_calls": 1}, idempotency_key="attempt-2",
        attribution={"run_id": "run-1"})
    assert first["budget"]["usage"] == {"model_tokens": 40, "provider_calls": 1}
    assert duplicate["duplicate"] is True
    assert conflict["error_code"] == "idempotency_conflict"
    assert exhausted["error_code"] == "workspace_budget_exhausted"


async def test_canary_change_promotes_or_requires_exact_rollback_versions():
    store = InMemoryDurableStore()
    ops = PlatformOperationsService(store)
    created = await ops.start_change(
        change_idempotency_key="release-1",
        current_versions={"application": "rev-1", "policy": "p1"},
        target_versions={"application": "rev-2", "policy": "p2"},
        canary_workspace_ids=["workspace-a"], actor_id="operator-a",
        decision_ref="change/1")
    observing = await ops.evaluate_change(
        change_id=created["change"]["change_id"], sample_count=10,
        error_count=0, projection_mismatches=0, security_control_failures=0)
    rollback = await ops.evaluate_change(
        change_id=created["change"]["change_id"], sample_count=100,
        error_count=0, projection_mismatches=1, security_control_failures=0)
    assert observing["decision"] == "OBSERVING"
    assert rollback["decision"] == "ROLLBACK_REQUIRED"
    assert rollback["change"]["rollback_versions"] == {
        "application": "rev-1", "policy": "p1"}


async def test_recovery_chaos_and_migration_evidence_fail_closed():
    store = InMemoryDurableStore()
    ops = PlatformOperationsService(store)
    failed = await ops.record_recovery_drill(
        workspace_id="workspace-a", drill_idempotency_key="drill-fail",
        drill_kind="REGIONAL_FAILOVER", source_region="us-central1",
        recovery_region="us-east1", backup_id="backup-1",
        evidence_ref="gs://evidence/drill-fail.json",
        backup_age_seconds=60, restore_duration_seconds=120,
        deletion_tombstones_replayed=False,
        provider_actions_reconciled=True, authority_checksum_match=True,
        open_waits_preserved=True, nonterminal_actions_preserved=True)
    passed = await ops.record_recovery_drill(
        workspace_id="workspace-a", drill_idempotency_key="drill-pass",
        drill_kind="REGIONAL_FAILOVER", source_region="us-central1",
        recovery_region="us-east1", backup_id="backup-2",
        evidence_ref="gs://evidence/drill-pass.json",
        backup_age_seconds=60, restore_duration_seconds=120,
        deletion_tombstones_replayed=True,
        provider_actions_reconciled=True, authority_checksum_match=True,
        open_waits_preserved=True, nonterminal_actions_preserved=True)
    chaos = await ops.record_chaos_drill(
        workspace_id="workspace-a", scenario="WORKER_DEATH",
        drill_idempotency_key="chaos-1", evidence_ref="test://worker-death",
        duplicate_effects=0, orphaned_waits=0, orphaned_actions=0,
        lost_receipts=0, projection_mismatches=0)
    migration = await ops.record_migration_drill(
        workspace_id="workspace-a", migration_id="runtime-v2",
        drill_idempotency_key="migration-1", direction="ROLLBACK",
        evidence_ref="test://migration-rollback",
        authority_checksum_match=True, orphaned_waits=0, orphaned_actions=0)
    assert failed["drill"]["status"] == "FAILED"
    assert passed["drill"]["status"] == "PASSED"
    assert chaos["drill"]["status"] == "PASSED"
    assert migration["drill"]["status"] == "PASSED"


async def test_governance_report_traces_policy_model_capability_and_budget():
    store = InMemoryDurableStore()
    await store.create("workflow_runs", "run-1", {
        "run_id": "run-1", "workspace_id": "workspace-a",
        "policy_version": "policy-v1", "plan_hash": "hash-1", "version": 1})
    await store.create("step_attempts", "attempt-1", {
        "attempt_id": "attempt-1", "workspace_id": "workspace-a",
        "model_version": "gemini-3.6-flash", "version": 1})
    ops = PlatformOperationsService(store)
    await ops.configure_workspace_budget(
        workspace_id="workspace-a", window_id="2026-08",
        limits={"model_tokens": 100}, actor_id="owner-a", reason="test")
    report = await ops.governance_report("workspace-a")
    assert report["report"]["policy_versions"] == ["policy-v1"]
    assert report["report"]["plan_hashes"] == ["hash-1"]
    assert report["report"]["model_versions"] == ["gemini-3.6-flash"]
    assert report["report"]["budget_windows"][0]["window_id"] == "2026-08"
    assert admission_decision(
        priority="LOW", queue_depth=500,
        workspace_budget_remaining=10)["admit"] is False
    assert admission_decision(
        priority="HIGH", queue_depth=500,
        workspace_budget_remaining=10)["admit"] is True
    assert all("target_percent" in objective and "degrade" in objective
               for objective in SERVICE_OBJECTIVES.values())
