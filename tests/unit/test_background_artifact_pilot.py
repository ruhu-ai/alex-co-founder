from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.background_pilot import (
    BackgroundArtifactPilot,
    BackgroundPilotDispatcher,
    BackgroundPilotExecutor,
    BackgroundPilotFlags,
)
from services.command_dispatcher import CommandDispatcher
from services.durable_store import InMemoryDurableStore
from services.workflow_runtime import WorkflowRuntime

pytestmark = pytest.mark.asyncio

REPO = Path(__file__).resolve().parents[2]


def _principal(*, actor: str = "actor-founder",
               workspace: str = "workspace-pilot",
               role: WorkspaceRole = WorkspaceRole.FOUNDER,
               kind: str = "INTERACTIVE") -> ActorPrincipal:
    return ActorPrincipal(
        actor_id=actor, workspace_id=workspace, role=role,
        role_grants=frozenset(), candidate_assignments=frozenset(),
        interview_assignments=frozenset(), session_auth_time=1,
        membership_version=1, principal_kind=kind)


def _flags(*, admission: bool = True, execution: bool = True,
           killed: bool = False,
           workspaces: frozenset[str] = frozenset({"workspace-pilot"}),
           ) -> BackgroundPilotFlags:
    return BackgroundPilotFlags(
        admission_enabled=admission, execution_enabled=execution,
        kill_switch_active=killed, workspace_allowlist=workspaces)


async def _artifact(store: InMemoryDurableStore, *,
                    artifact_id: str = "a" * 32,
                    workspace: str = "workspace-pilot",
                    session: str = "session-pilot-001",
                    generation: str = "generation-001") -> dict:
    row = {
        "artifact_id": artifact_id, "workspace_id": workspace,
        "founder_id": workspace, "session_id": session,
        "status": "READY", "index_generation": generation,
        "sha256": "b" * 64, "size_bytes": 4096,
        "source_ref": "private-source.pdf", "version": 1,
    }
    assert await store.create("artifacts", artifact_id, row)
    return row


def _fake_enqueue(calls: list[dict]):
    def enqueue(path, payload, dedupe_key, **kwargs):
        calls.append({
            "path": path, "payload": payload, "dedupe_key": dedupe_key,
            **kwargs,
        })
        return {"status": "success"}

    return enqueue


def _pilot(store: InMemoryDurableStore, calls: list[dict], *,
           flags: BackgroundPilotFlags | None = None) -> BackgroundArtifactPilot:
    selected = flags or _flags()
    dispatcher = BackgroundPilotDispatcher(
        store, flags=selected, enqueue_fn=_fake_enqueue(calls))
    return BackgroundArtifactPilot(
        store, flags=selected, dispatcher=dispatcher)


async def _start(store: InMemoryDurableStore, calls: list[dict], *,
                 client: str = "background-client-0001",
                 artifact_id: str = "a" * 32,
                 pilot: BackgroundArtifactPilot | None = None) -> dict:
    selected = pilot or _pilot(store, calls)
    return await selected.start(
        principal=_principal(), session_id="session-pilot-001",
        artifact_id=artifact_id, client_request_id=client)


def _inventory(artifact_id: str = "a" * 32,
               generation: str = "generation-001") -> dict:
    return {
        "schema_version": 1, "artifact_id": artifact_id,
        "artifact_version": generation,
        "artifact_sha256": "sha256:" + "b" * 64,
        "chunk_count": 2, "character_count": 120, "word_count": 21,
        "locator_counts": {"page": 2},
        "citations": [
            {"artifact_id": artifact_id, "chunk_id": "chunk-001",
             "locator": {"page": 1}, "content_sha256": "c" * 64},
            {"artifact_id": artifact_id, "chunk_id": "chunk-002",
             "locator": {"page": 2}, "content_sha256": "d" * 64},
        ],
        "analysis_kind": "DETERMINISTIC_EVIDENCE_INVENTORY",
        "model_calls": 0, "provider_calls": 0, "external_reads": 0,
        "tokens": 0, "content_hash": "sha256:" + "e" * 64,
    }


class InventoryPort:
    def __init__(self, results: list[dict] | None = None):
        self.results = list(results or [
            {"status": "success", "inventory": _inventory()}])
        self.calls: list[dict] = []

    async def inspect(self, **kwargs):
        self.calls.append(kwargs)
        return self.results.pop(0)


class BlockingPort:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def inspect(self, **kwargs):
        del kwargs
        self.started.set()
        await self.release.wait()
        return {"status": "success", "inventory": _inventory()}


async def test_flags_workspace_role_and_malicious_input_fail_closed():
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    killed = await _pilot(store, calls, flags=_flags(killed=True)).start(
        principal=_principal(), session_id="session-pilot-001",
        artifact_id="a" * 32, client_request_id="background-killed-001")
    foreign = await _pilot(
        store, calls, flags=_flags(workspaces=frozenset({"other"}))).start(
            principal=_principal(), session_id="session-pilot-001",
            artifact_id="a" * 32, client_request_id="background-foreign-001")
    observer = await _pilot(store, calls).start(
        principal=_principal(role=WorkspaceRole.OBSERVER),
        session_id="session-pilot-001", artifact_id="a" * 32,
        client_request_id="background-observer-001")
    noninteractive = await _pilot(store, calls).start(
        principal=_principal(kind="CLOUD_TASKS"),
        session_id="session-pilot-001", artifact_id="a" * 32,
        client_request_id="background-worker-001")
    malicious = await _pilot(store, calls).start(
        principal=_principal(), session_id="session-pilot-001",
        artifact_id="https://attacker.invalid", client_request_id="background-url-001")

    assert killed["error_code"] == "background_pilot_killed"
    assert foreign["error_code"] == "background_pilot_workspace_denied"
    assert observer["error_code"] == "interactive_founder_required"
    assert noninteractive["error_code"] == "interactive_founder_required"
    assert malicious["error_code"] == "background_artifact_invalid"
    assert await store.list("workflow_runs", filters={}) == []
    assert calls == []


async def test_accept_dispatch_is_one_template_private_bounded_and_idempotent():
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    pilot = _pilot(store, calls)
    first = await _start(store, calls, pilot=pilot)
    duplicate = await _start(store, calls, pilot=pilot)

    assert first["status"] == "accepted"
    assert first["dispatch_status"] == "DISPATCHED"
    assert duplicate["duplicate"] is True
    assert first["job_id"] == first["run_id"]
    assert len(calls) == 1
    assert calls[0]["path"] == "/tasks/background-artifact-pilot"
    assert calls[0]["queue_name"] == "co-founder-background-pilot"
    assert set(calls[0]["payload"]) == {"workspace_id", "run_id", "step_id"}
    assert "a" * 32 not in repr(calls[0]["payload"])
    run = await store.get("workflow_runs", first["run_id"])
    assert run["job_template_id"] == "pilot.artifact_evidence_inventory"
    assert run["visibility_scope"] == "ACTOR_PRIVATE"
    assert run["subject_id"] == _principal().actor_id
    assert run["approval_authority"] == run["effect_authority"] == "NONE"
    assert run["external_read_authority"] == run["memory_write_authority"] == "NONE"
    assert run["skill_bindings"] == []
    assert run["budgets"] == {
        "max_steps": 1, "max_model_calls": 0, "max_provider_calls": 0,
        "max_tokens": 0, "max_active_seconds": 30,
        "max_wall_seconds": 120, "max_artifact_bytes": 5_242_880,
        "max_artifact_chunks": 100, "max_output_bytes": 65_536,
        "max_retries": 2, "max_concurrent": 1}
    assert len(await store.list("workflow_runs", filters={})) == 1
    assert len(await store.list("workflow_steps", filters={})) == 1


async def test_dispatch_queue_override_is_closed_to_registered_names(
        monkeypatch):
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    monkeypatch.setenv(
        "BACKGROUND_ARTIFACT_PILOT_QUEUE",
        "co-founder-background-pilot-real")
    accepted = await _start(store, calls)
    assert accepted["dispatch_status"] == "DISPATCHED"
    assert calls[0]["queue_name"] == "co-founder-background-pilot-real"

    gate_e = InMemoryDurableStore()
    await _artifact(gate_e)
    gate_e_calls: list[dict] = []
    monkeypatch.setenv(
        "BACKGROUND_ARTIFACT_PILOT_QUEUE",
        "co-founder-background-pilot-gate-e")
    gate_e_accepted = await _start(gate_e, gate_e_calls)
    assert gate_e_accepted["dispatch_status"] == "DISPATCHED"
    assert gate_e_calls[0]["queue_name"] == (
        "co-founder-background-pilot-gate-e")
    assert gate_e_calls[0]["path"] == "/tasks/background-artifact-pilot"

    another = InMemoryDurableStore()
    await _artifact(another)
    refused_calls: list[dict] = []
    monkeypatch.setenv(
        "BACKGROUND_ARTIFACT_PILOT_QUEUE", "attacker-selected-queue")
    refused = await _start(another, refused_calls)
    assert refused["dispatch_status"] == "PENDING"
    assert refused["dispatch_error_code"] == "background_dispatch_queue_invalid"
    assert refused_calls == []


async def test_pending_outbox_recovery_uses_only_closed_pilot_dispatcher():
    store = InMemoryDurableStore()
    await _artifact(store)
    failed_calls: list[dict] = []

    def unavailable(path, payload, dedupe_key, **kwargs):
        failed_calls.append({"path": path, "payload": payload,
                             "dedupe_key": dedupe_key, **kwargs})
        return {"status": "error", "error": True,
                "error_code": "queue_unavailable"}

    flags = _flags()
    pilot = BackgroundArtifactPilot(
        store, flags=flags,
        dispatcher=BackgroundPilotDispatcher(
            store, flags=flags, enqueue_fn=unavailable))
    accepted = await _start(store, failed_calls, pilot=pilot)
    assert accepted["dispatch_status"] == "PENDING"

    recovered_calls: list[dict] = []
    monkey_flags = {
        "BACKGROUND_JOB_ADMISSION_ENABLED": "true",
        "BACKGROUND_SPECIALIST_EXECUTION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PILOT_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH": "false",
        "BACKGROUND_ARTIFACT_PILOT_WORKSPACES": "workspace-pilot",
    }
    previous = {key: os.environ.get(key) for key in monkey_flags}
    os.environ.update(monkey_flags)
    try:
        recovered = await CommandDispatcher(
            store, enqueue_fn=_fake_enqueue(recovered_calls)
        ).dispatch_pending(limit=10)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert recovered["scanned"] == recovered["dispatched"] == 1
    assert len(recovered_calls) == 1
    assert recovered_calls[0]["path"] == "/tasks/background-artifact-pilot"
    assert recovered_calls[0]["queue_name"] == "co-founder-background-pilot"


async def test_changed_idempotency_and_concurrent_admission_do_not_fork_jobs():
    store = InMemoryDurableStore()
    await _artifact(store)
    await _artifact(store, artifact_id="c" * 32)
    calls: list[dict] = []
    pilot = _pilot(store, calls)
    first = await _start(store, calls, pilot=pilot)
    changed = await _start(
        store, calls, pilot=pilot, artifact_id="c" * 32)
    assert changed["error_code"] == "idempotency_conflict"

    await pilot.cancel(
        principal=_principal(), run_id=first["run_id"], reason="reset")
    raced = await asyncio.gather(
        _start(store, calls, client="background-race-0001", pilot=pilot),
        _start(store, calls, client="background-race-0002", pilot=pilot))
    assert sum(not row.get("error") for row in raced) == 1
    refused = next(row for row in raced if row.get("error"))
    assert refused["error_code"] == "background_concurrency_exhausted"


async def test_executor_commits_once_orders_captions_and_has_no_effect_path():
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    pilot = _pilot(store, calls)
    accepted = await _start(store, calls, pilot=pilot)
    task = calls[0]["payload"]
    port = InventoryPort()
    executor = BackgroundPilotExecutor(
        store, flags=_flags(), inventory_port=port)
    result = await executor.execute(
        **task, workload={"principal_kind": "CLOUD_TASKS",
                          "service_account": "pilot-worker@example.test",
                          "audience": "/tasks/background-artifact-pilot",
                          "delivery_id": "task-001"})
    duplicate = await executor.execute(
        **task, workload={"principal_kind": "CLOUD_TASKS",
                          "service_account": "pilot-worker@example.test",
                          "audience": "/tasks/background-artifact-pilot",
                          "delivery_id": "task-001"})

    assert result["runtime_status"] == "SUCCEEDED"
    assert duplicate["duplicate"] is True
    run = await store.get("workflow_runs", accepted["run_id"])
    assert run["budget_usage"] == {
        "steps_started": 1, "model_calls": 0, "provider_calls": 0,
        "tokens": 0, "retries": 0}
    output = await store.get("artifacts", result["output_id"])
    assert output["visibility_scope"] == "ACTOR_PRIVATE"
    assert output["chunk_count"] == output["citation_count"] == 2
    timeline = await pilot.timeline(
        principal=_principal(), run_id=accepted["run_id"])
    assert [row["sequence"] for row in timeline["messages"]] == [1, 2, 3]
    assert [row["runtime_status"] for row in timeline["messages"]] == [
        "QUEUED", "RUNNING", "SUCCEEDED"]
    assert len({row["event_id"] for row in timeline["messages"]}) == 3
    for collection in (
            "approvals", "external_actions", "wake_deliveries",
            "conversation_deliveries", "memory_items",
            "memory_write_receipts"):
        assert await store.list(collection, filters={}) == []


async def test_retry_budget_reuses_step_and_counts_attempts_not_steps():
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    accepted = await _start(store, calls)
    port = InventoryPort([
        {"status": "error", "error": True,
         "error_code": "retryable_dependency", "retryable": True,
         "message": "temporary"},
        {"status": "success", "inventory": _inventory()},
    ])
    executor = BackgroundPilotExecutor(
        store, flags=_flags(), inventory_port=port)
    first = await executor.execute(
        **calls[0]["payload"],
        workload={"delivery_id": "task-retry-001"})
    second = await executor.execute(
        **calls[0]["payload"],
        workload={"delivery_id": "task-retry-002"})

    assert first["retryable"] is True
    assert second["runtime_status"] == "SUCCEEDED"
    run = await store.get("workflow_runs", accepted["run_id"])
    assert run["budget_usage"]["steps_started"] == 1
    assert run["budget_usage"]["retries"] == 1
    attempts = await store.list(
        "step_attempts", filters={"run_id": accepted["run_id"]})
    assert [row["generation"] for row in attempts] == [1, 2]


async def test_cancel_fences_running_worker_and_releases_concurrency():
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    pilot = _pilot(store, calls)
    accepted = await _start(store, calls, pilot=pilot)
    blocking = BlockingPort()
    executor = BackgroundPilotExecutor(
        store, flags=_flags(), inventory_port=blocking)
    executing = asyncio.create_task(executor.execute(
        **calls[0]["payload"], workload={"delivery_id": "task-race-001"}))
    await blocking.started.wait()
    cancelled = await pilot.cancel(
        principal=_principal(), run_id=accepted["run_id"],
        reason="Founder stopped it")
    blocking.release.set()
    late = await executing

    assert cancelled["runtime_status"] == "CANCELLED"
    assert late["error_code"] in {"lease_lost", "run_fenced"}
    assert not any(
        row.get("artifact_kind") == "BACKGROUND_ARTIFACT_INVENTORY"
        for row in await store.list("artifacts", filters={}))
    timeline = await pilot.timeline(
        principal=_principal(), run_id=accepted["run_id"])
    assert [row["sequence"] for row in timeline["messages"]] == [1, 2, 3, 4]
    assert timeline["messages"][-1]["runtime_status"] == "CANCELLED"
    next_job = await _start(
        store, calls, pilot=pilot, client="background-after-cancel-001")
    assert next_job["status"] == "accepted"


async def test_expired_lease_and_tampered_authority_cannot_commit():
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    accepted = await _start(store, calls)
    step_id = calls[0]["payload"]["step_id"]
    claim = await WorkflowRuntime(store).claim_step(
        step_id, lease_owner="stale-worker", lease_seconds=10)
    step = await store.get("workflow_steps", step_id)
    await store.compare_and_set(
        "workflow_steps", step_id, int(step["version"]), {
            "lease_expires_at": "2000-01-01T00:00:00+00:00"})
    expired = await WorkflowRuntime(store).complete_step(
        step_id, lease_owner="stale-worker",
        generation=int(claim["attempt_generation"]))
    run = await store.get("workflow_runs", accepted["run_id"])
    await store.compare_and_set(
        "workflow_runs", accepted["run_id"], int(run["version"]), {
            "effect_authority": "MODEL_SELECTED"})
    tampered = await BackgroundPilotExecutor(
        store, flags=_flags(), inventory_port=InventoryPort()).execute(
            **calls[0]["payload"], workload={"delivery_id": "task-tampered"})

    assert expired["error_code"] == "lease_lost"
    assert tampered["error_code"] == "background_execution_authority_invalid"
    assert not any(
        row.get("artifact_kind") == "BACKGROUND_ARTIFACT_INVENTORY"
        for row in await store.list("artifacts", filters={}))


async def test_actor_and_tenant_boundaries_hide_job_output_and_timeline():
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    pilot = _pilot(store, calls)
    accepted = await _start(store, calls, pilot=pilot)
    foreign_actor = _principal(actor="actor-other")
    foreign_workspace = _principal(
        actor="actor-founder", workspace="workspace-other")

    hidden_actor = await pilot.get_job(
        principal=foreign_actor, run_id=accepted["run_id"])
    hidden_tenant = await pilot.timeline(
        principal=foreign_workspace, run_id=accepted["run_id"])
    actor_list = await pilot.list_jobs(principal=foreign_actor)

    assert hidden_actor["error_code"] == "background_job_not_found"
    assert hidden_tenant["error_code"] == "background_job_not_found"
    assert actor_list["jobs"] == []


async def test_kill_switch_stops_new_claims_without_false_failure():
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    accepted = await _start(store, calls)
    killed = await BackgroundPilotExecutor(
        store, flags=_flags(killed=True),
        inventory_port=InventoryPort()).execute(
            **calls[0]["payload"], workload={"delivery_id": "task-killed"})

    assert killed["error_code"] == "background_pilot_killed"
    assert killed["retryable"] is True
    run = await store.get("workflow_runs", accepted["run_id"])
    step = await store.get("workflow_steps", calls[0]["payload"]["step_id"])
    assert run["runtime_status"] == "QUEUED"
    assert step["status"] == "READY"
    audits = await store.list("audit", filters={
        "action": "background_pilot.execution"})
    assert len(audits) == 1
    assert audits[0]["result"] == "blocked"
    assert audits[0]["error_code"] == "background_pilot_killed"


async def test_rollback_disables_admission_without_hiding_accepted_work():
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    accepted = await _start(store, calls)
    rolled_back = BackgroundArtifactPilot(
        store, flags=_flags(admission=False, execution=False),
        dispatcher=BackgroundPilotDispatcher(
            store, flags=_flags(admission=False, execution=False),
            enqueue_fn=_fake_enqueue(calls)))

    listing = await rolled_back.list_jobs(principal=_principal())
    detail = await rolled_back.get_job(
        principal=_principal(), run_id=accepted["run_id"])

    assert listing["enabled"] is False
    assert [job["run_id"] for job in listing["jobs"]] == [accepted["run_id"]]
    assert detail["job"]["runtime_status"] == "QUEUED"


async def test_artifact_chunk_budget_fails_before_durable_admission():
    store = InMemoryDurableStore()
    artifact = await _artifact(store)
    await store.compare_and_set(
        "artifacts", artifact["artifact_id"], int(artifact["version"]),
        {"chunk_count": 101})
    calls: list[dict] = []

    refused = await _start(store, calls)

    assert refused["error_code"] == "background_artifact_not_ready"
    assert await store.list("workflow_runs", filters={}) == []
    assert calls == []


async def test_rate_limit_is_durable_across_completed_jobs():
    store = InMemoryDurableStore()
    await _artifact(store)
    calls: list[dict] = []
    pilot = _pilot(store, calls)
    for index in range(3):
        accepted = await _start(
            store, calls, pilot=pilot,
            client=f"background-rate-{index:04d}")
        assert accepted["status"] == "accepted"
        cancelled = await pilot.cancel(
            principal=_principal(), run_id=accepted["run_id"],
            reason="rate test")
        assert cancelled["runtime_status"] == "CANCELLED"
    fourth = await _start(
        store, calls, pilot=pilot, client="background-rate-0004")
    assert fourth["error_code"] == "background_rate_limited"


async def test_ui_is_contextual_truthful_and_not_a_generic_runs_dashboard():
    source = (REPO / "app" / "static" / "index.html").read_text()
    routes = (REPO / "app" / "background_pilot_routes.py").read_text()

    assert "Alex background work" in source
    assert "Analyze safely in background" in source
    assert "No external action, provider call, or message can occur." in source
    assert "BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH" not in source
    assert "/api/v1/background-pilot/artifact-analysis" in routes
    assert '"/tasks/background-artifact-pilot"' in routes
    assert "Idempotency-Key" in routes and "If-Match" in routes
    assert "verify_request(request, route)" in routes
