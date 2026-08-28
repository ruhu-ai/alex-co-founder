from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.durable_store import InMemoryDurableStore
from services.platform_operations import (
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


async def test_recovery_drill_must_prove_tombstones_actions_checksum_and_rpo_rto():
    store = InMemoryDurableStore()
    ops = PlatformOperationsService(store)
    failed = await ops.record_recovery_drill(
        workspace_id="workspace-a", drill_idempotency_key="drill-fail",
        backup_age_seconds=60, restore_duration_seconds=120,
        deletion_tombstones_replayed=False,
        provider_actions_reconciled=True, authority_checksum_match=True)
    assert failed["drill"]["status"] == "FAILED"
    passed = await ops.record_recovery_drill(
        workspace_id="workspace-a", drill_idempotency_key="drill-pass",
        backup_age_seconds=60, restore_duration_seconds=120,
        deletion_tombstones_replayed=True,
        provider_actions_reconciled=True, authority_checksum_match=True)
    assert passed["drill"]["status"] == "PASSED"


async def test_governance_report_and_load_shedding_are_bounded():
    store = InMemoryDurableStore()
    await store.create("workflow_runs", "run-1", {
        "run_id": "run-1", "workspace_id": "workspace-a",
        "policy_version": "policy-v1", "plan_hash": "hash-1", "version": 1})
    report = await PlatformOperationsService(store).governance_report("workspace-a")
    assert report["report"]["policy_versions"] == ["policy-v1"]
    assert report["report"]["plan_hashes"] == ["hash-1"]
    assert admission_decision(
        priority="LOW", queue_depth=500,
        workspace_budget_remaining=10)["admit"] is False
    assert admission_decision(
        priority="HIGH", queue_depth=500,
        workspace_budget_remaining=10)["admit"] is True
    assert all("target_percent" in objective and "degrade" in objective
               for objective in SERVICE_OBJECTIVES.values())
