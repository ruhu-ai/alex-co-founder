"""Idempotent Phase 1 workflow status and provenance migration helpers."""

from __future__ import annotations

from typing import Any

from services.durable_store import AtomicMutation, DurableStore
from services.workflow_contracts import (
    RunKind,
    RuntimeStatus,
    normalize_runtime_status,
    stable_id,
    utc_now,
)
from services.workflow_runtime import WorkflowRuntime


async def migrate_run(store: DurableStore, run_id: str) -> dict[str, Any]:
    """Migrate one legacy run after conservatively expiring old leases.

    Legacy ACTIVE did not distinguish queued from running. We never infer a
    surviving worker after cut-over: open attempts are fenced, open waits win
    WAITING, and every other ACTIVE row becomes safely redispatchable QUEUED.
    """
    run = await store.get("workflow_runs", run_id)
    if not run:
        return {"status": "error", "error": True,
                "error_code": "run_not_found", "message": "Run does not exist."}
    if int(run.get("runtime_status_schema_version") or 0) >= 2:
        return {"status": "success", "duplicate": True, "run": run}

    legacy = str(run.get("runtime_status") or "")
    try:
        target = normalize_runtime_status(legacy)
    except ValueError:
        return {"status": "error", "error": True,
                "error_code": "runtime_status_unknown",
                "message": "Legacy runtime status is not registered."}

    now = utc_now()
    waits = await store.list(
        "waits", filters={"run_id": run_id, "status": "OPEN"}, limit=1000)
    attempts = await store.list(
        "step_attempts", filters={"run_id": run_id}, limit=1000)
    mutations: list[AtomicMutation] = []
    for attempt in attempts:
        if attempt.get("status") in {"LEASED", "RUNNING", "ACTIVE"}:
            mutations.append(AtomicMutation(
                "step_attempts", str(attempt.get("attempt_id") or attempt.get("id")),
                int(attempt["version"]),
                updates={"status": "FENCED", "lease_owner": None,
                         "lease_expires_at": None,
                         "error_code": "pre_cutover_lease_fenced",
                         "updated_at": now}))
    if legacy == "ACTIVE":
        target = (RuntimeStatus.WAITING.value if waits
                  else RuntimeStatus.QUEUED.value)

    provenance = run.get("provenance")
    if not isinstance(provenance, dict) or not provenance.get("provenance_class"):
        provenance = ({
            "provenance_class": "SYNTHETIC",
            "namespace": str(run.get("synthetic_namespace") or ""),
            "fixture_set_id": str(run.get("fixture_id") or ""),
        } if run.get("synthetic") is True else {
            "provenance_class": "PRODUCTION"})
    replacement = {
        key: value for key, value in run.items()
        if key not in {"id", "version", "synthetic", "synthetic_namespace",
                       "fixture_id", "is_synthetic", "fixture_set_id"}
    }
    replacement.update({
        "runtime_status": target,
        "runtime_status_schema_version": 2,
        "provenance": provenance,
        "pre_cutover_leases_fenced_at": now,
        "updated_at": now,
    })
    mutations.append(AtomicMutation(
        "workflow_runs", run_id, int(run["version"]),
        record=replacement, replace=True))
    committed = await store.atomic_compare_and_set(tuple(mutations))
    if not committed:
        return {"status": "error", "error": True,
                "error_code": "concurrency_conflict",
                "message": "Run changed during migration; retry."}
    return {"status": "success", "duplicate": False,
            "run": committed[("workflow_runs", run_id)],
            "fenced_attempt_count": len(mutations) - 1}


async def link_grant_application_run(
        store: DurableStore, application_id: str) -> dict[str, Any]:
    """Create/link the deterministic control run for one legacy application.

    Run creation and the domain link cannot share one helper call because run
    creation also creates its immutable plan and first event. The identities
    are deterministic: a crash can leave only an orphan control run, and the
    retry links that exact run rather than creating another one.
    """
    application = await store.get("applications", application_id)
    if not application:
        return {"status": "error", "error": True,
                "error_code": "application_not_found",
                "message": "Application does not exist."}
    workspace_id = str(application.get("founder_id") or "")
    if not workspace_id:
        return {"status": "error", "error": True,
                "error_code": "migration_owner_ambiguous",
                "message": "Application has no workspace owner."}
    receipt_id = stable_id(
        "workflowreceipt", "grant-application-control-v2", application_id)
    receipt = await store.get("workflow_migration_receipts", receipt_id)
    if receipt:
        return {"status": "success", "duplicate": True, "receipt": receipt}
    runtime = WorkflowRuntime(store)
    journey_id = stable_id(
        "journey", workspace_id, "grant", application_id)
    run = await runtime.create_run(
        workspace_id=workspace_id, journey_id=journey_id,
        run_kind=RunKind.GRANT_APPLICATION,
        workflow_kind="grant_application:v1",
        idempotency_key=application_id, domain_ref=application_id,
        originating_actor_id=workspace_id)
    if run.get("error"):
        return run
    # A pre-existing link must point at the same deterministic run. Anything
    # else is an ownership/history conflict and is never overwritten.
    prior_run_id = str(application.get("workflow_run_id") or "")
    if prior_run_id and prior_run_id != run["run_id"]:
        return {"status": "error", "error": True,
                "error_code": "idempotency_conflict",
                "message": "Application already names another workflow run."}
    now = utc_now()
    migration_receipt = {
        "schema_version": 1, "receipt_id": receipt_id,
        "workspace_id": workspace_id, "application_id": application_id,
        "run_id": run["run_id"], "plan_hash": run["plan_hash"],
        "migration": "grant-application-control-v2",
        "migrated_at": now, "version": 1,
    }
    committed = await store.atomic_compare_and_set((
        AtomicMutation(
            "applications", application_id,
            int(application.get("version") or 0),
            updates={"workflow_run_id": run["run_id"],
                     "workflow_plan_hash": run["plan_hash"],
                     "workflow_plan_version": run["plan_version"],
                     "workflow_shadow_status": "AUTHORITATIVE_CONTROL_LINKED",
                     "updated_at": now}),
        AtomicMutation(
            "workflow_migration_receipts", receipt_id, None,
            record=migration_receipt),
    ))
    if not committed:
        return {"status": "error", "error": True,
                "error_code": "concurrency_conflict",
                "message": "Application changed while its run was linked."}
    return {"status": "success", "duplicate": False,
            "application": committed[("applications", application_id)],
            "run": run,
            "receipt": committed[("workflow_migration_receipts", receipt_id)]}
