"""Exact HTTP and workload boundaries for the Gate F canary route."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import background_pilot_routes
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.background_skill_runtime import (
    GateFSkillExecutor,
    GateFSkillFlags,
    SelectedArtifactContext,
    SelectedEvidenceChunk,
)
from services.durable_store import InMemoryDurableStore

pytestmark = pytest.mark.asyncio
ARTIFACT = "a" * 32
CONTENT = "Synthetic pitch artifact records three completed internal trials."
CONTENT_HASH = hashlib.sha256(CONTENT.encode()).hexdigest()
SECRET = "gate-f-local-route-secret-value-0000000001"
REPO = Path(__file__).resolve().parents[2]


class EvidencePort:
    async def read(self, **kwargs):
        del kwargs
        return {"status": "success", "context": SelectedArtifactContext(
            artifact_id=ARTIFACT, artifact_version="generation-001",
            artifact_sha256="sha256:" + "b" * 64,
            chunks=(SelectedEvidenceChunk(
                chunk_id="chunk-001", content_sha256=CONTENT_HASH,
                locator={"page": 1}, content=CONTENT,
            ),),
        )}


class ModelPort:
    async def generate(self, **kwargs):
        context = kwargs["context"]
        chunk = context.chunks[0]
        return {
            "draft_status": "DRAFT",
            "source_artifact_id": context.artifact_id,
            "source_artifact_version": context.artifact_version,
            "title": "Synthetic pitch evidence brief",
            "sections": [{"heading": "Evidence summary", "claims": [{
                "text": "The synthetic artifact records three internal trials.",
                "citations": [{
                    "artifact_id": context.artifact_id,
                    "artifact_version": context.artifact_version,
                    "chunk_id": chunk.chunk_id,
                    "content_sha256": chunk.content_sha256,
                    "locator": chunk.locator,
                }],
            }]}],
            "unknowns": ["External validation is unknown."],
            "conflicts": [],
        }


def _principal(role: WorkspaceRole = WorkspaceRole.FOUNDER, *,
               actor: str = "actor-founder") -> ActorPrincipal:
    return ActorPrincipal(
        actor_id=actor, workspace_id="workspace-pilot", role=role,
        role_grants=frozenset(), candidate_assignments=frozenset(),
        interview_assignments=frozenset(), session_auth_time=1,
        membership_version=1, principal_kind="INTERACTIVE",
    )


def _worker_headers(*, account: str = "local-background-skill-worker",
                    audience: str = "https://testserver/tasks/background-artifact-grounded-brief"):
    claims = {
        "principal_kind": "CLOUD_TASKS", "service_account": account,
        "issuer": "local-test-dispatcher", "audience": audience,
        "delivery_id": "synthetic-delivery-001", "exp": int(time.time()) + 120,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(
        SECRET.encode(), encoded.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "X-Background-Pilot-Test-Principal": encoded,
        "X-Background-Pilot-Test-Signature": signature,
    }


async def _client(monkeypatch, *, role: WorkspaceRole = WorkspaceRole.FOUNDER):
    store = InMemoryDurableStore()
    await store.create("artifacts", ARTIFACT, {
        "artifact_id": ARTIFACT, "workspace_id": "workspace-pilot",
        "founder_id": "workspace-pilot", "session_id": "session-pilot-001",
        "status": "READY", "index_generation": "generation-001",
        "sha256": "b" * 64, "size_bytes": 2048, "chunk_count": 1,
        "version": 1,
    })
    for key, value in {
        "BACKGROUND_SKILLS_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PREPARATION_ENABLED": "true",
        "BACKGROUND_JOB_ADMISSION_ENABLED": "true",
        "BACKGROUND_SPECIALIST_EXECUTION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PREPARATION_EXECUTION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PREPARATION_KILL_SWITCH": "false",
        "BACKGROUND_ARTIFACT_PREPARATION_WORKSPACES": "workspace-pilot",
        "BACKGROUND_ARTIFACT_PREPARATION_QUEUE":
            "co-founder-background-skill-gate-f",
        "AGENT_BASE_URL": "https://pilot.example.test",
        "BACKGROUND_PILOT_ALLOW_TEST_DISPATCH": "1",
        "BACKGROUND_PILOT_TEST_DISPATCH_SECRET": SECRET,
    }.items():
        monkeypatch.setenv(key, value)
    from services import task_queue

    queued: list[dict] = []

    def enqueue(path, payload, dedupe_key, **kwargs):
        queued.append({"path": path, "payload": payload,
                       "dedupe_key": dedupe_key, **kwargs})
        return {"status": "success"}

    monkeypatch.setattr(task_queue, "enqueue", enqueue)

    async def principal_resolver(request):
        return _principal(role, actor=request.headers.get(
            "X-Test-Actor", "actor-founder"))

    async def session_resolver(workspace_id, session_id):
        return workspace_id == "workspace-pilot" and session_id == "session-pilot-001"

    flags = GateFSkillFlags(
        admission_enabled=True, execution_enabled=True,
        kill_switch_active=False,
        workspace_allowlist=frozenset({"workspace-pilot"}),
    )

    def executor_factory(selected_store):
        return GateFSkillExecutor(
            selected_store, flags=flags, evidence_port=EvidencePort(),
            model_port=ModelPort(),
        )

    app = FastAPI()
    background_pilot_routes.register(
        app, principal_resolver=principal_resolver,
        session_resolver=session_resolver, store_factory=lambda: store,
        skill_executor_factory=executor_factory,
    )
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"
    ), store, queued


async def test_exact_founder_route_is_bounded_idempotent_and_actor_private(monkeypatch):
    client, store, queued = await _client(monkeypatch)
    body = {
        "session_id": "session-pilot-001", "artifact_id": ARTIFACT,
        "client_request_id": "gate-f-route-0001",
    }
    async with client:
        missing_key = await client.post(
            "/api/v1/background-pilot/artifact-grounded-brief", json=body)
        malicious = await client.post(
            "/api/v1/background-pilot/artifact-grounded-brief",
            headers={"Idempotency-Key": "gate-f-route-0002"},
            json={**body, "client_request_id": "gate-f-route-0002",
                  "url": "https://attacker.invalid", "tool_name": "email.send"})
        first = await client.post(
            "/api/v1/background-pilot/artifact-grounded-brief",
            headers={"Idempotency-Key": body["client_request_id"]}, json=body)
        duplicate = await client.post(
            "/api/v1/background-pilot/artifact-grounded-brief",
            headers={"Idempotency-Key": body["client_request_id"]}, json=body)
    assert missing_key.status_code == 400
    assert malicious.status_code == 422
    assert first.status_code == duplicate.status_code == 202
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["dispatch_status"] == "DISPATCHED"
    assert duplicate.json()["dispatch_error_code"] is None
    assert len(queued) == 1
    assert queued[0]["path"] == "/tasks/background-artifact-grounded-brief"
    assert set(queued[0]["payload"]) == {"workspace_id", "run_id", "step_id"}
    run = await store.get("workflow_runs", first.json()["run_id"])
    assert run["visibility_scope"] == "ACTOR_PRIVATE"
    assert run["approval_authority"] == run["effect_authority"] == "NONE"


async def test_flags_founder_session_and_actor_boundaries_fail_closed(monkeypatch):
    client, store, _ = await _client(monkeypatch)
    body = {
        "session_id": "session-foreign-001", "artifact_id": ARTIFACT,
        "client_request_id": "gate-f-route-0003",
    }
    async with client:
        foreign = await client.post(
            "/api/v1/background-pilot/artifact-grounded-brief",
            headers={"Idempotency-Key": body["client_request_id"]}, json=body)
    assert foreign.status_code == 404
    assert await store.list("workflow_runs", filters={}) == []

    observer, observer_store, _ = await _client(
        monkeypatch, role=WorkspaceRole.OBSERVER)
    body["session_id"] = "session-pilot-001"
    body["client_request_id"] = "gate-f-route-0004"
    async with observer:
        denied = await observer.post(
            "/api/v1/background-pilot/artifact-grounded-brief",
            headers={"Idempotency-Key": body["client_request_id"]}, json=body)
    assert denied.status_code == 403
    assert await observer_store.list("workflow_runs", filters={}) == []

    client, disabled_store, _ = await _client(monkeypatch)
    monkeypatch.setenv("BACKGROUND_ARTIFACT_PREPARATION_KILL_SWITCH", "true")
    body["client_request_id"] = "gate-f-route-0005"
    async with client:
        killed = await client.post(
            "/api/v1/background-pilot/artifact-grounded-brief",
            headers={"Idempotency-Key": body["client_request_id"]}, json=body)
    assert killed.status_code == 403
    assert killed.json()["error_code"] == "background_skill_killed"
    assert await disabled_store.list("workflow_runs", filters={}) == []


async def test_private_worker_identity_completion_visibility_and_cancel(monkeypatch):
    client, store, _ = await _client(monkeypatch)
    body = {
        "session_id": "session-pilot-001", "artifact_id": ARTIFACT,
        "client_request_id": "gate-f-route-0006",
    }
    async with client:
        started = await client.post(
            "/api/v1/background-pilot/artifact-grounded-brief",
            headers={"Idempotency-Key": body["client_request_id"]}, json=body)
        run_id = started.json()["run_id"]
        step = (await store.list(
            "workflow_steps", filters={"run_id": run_id}, limit=2))[0]
        forged = await client.post(
            "/tasks/background-artifact-grounded-brief",
            headers=_worker_headers(account="local-background-pilot-worker"),
            json={"workspace_id": "workspace-pilot", "run_id": run_id,
                  "step_id": step["step_id"]})
        completed = await client.post(
            "/tasks/background-artifact-grounded-brief",
            headers=_worker_headers(),
            json={"workspace_id": "workspace-pilot", "run_id": run_id,
                  "step_id": step["step_id"]})
        listing = await client.get(
            "/api/v1/background-pilot/jobs?session_id=session-pilot-001")
        timeline = await client.get(
            f"/api/v1/background-pilot/jobs/{run_id}/timeline")
    assert forged.status_code == 401
    assert completed.status_code == 200
    assert completed.json()["runtime_status"] == "SUCCEEDED"
    assert listing.json()["grounded_brief_enabled"] is True
    job = next(item for item in listing.json()["jobs"] if item["run_id"] == run_id)
    assert job["output"]["artifact_kind"] == "BACKGROUND_GROUNDED_EVIDENCE_DRAFT"
    assert job["output"]["draft_status"] == "DRAFT"
    assert [item["sequence"] for item in timeline.json()["messages"]] == [1, 2, 3]
    assert "Synthetic pitch artifact" not in repr(listing.json())


async def test_cancel_route_fences_gate_f_worker_and_is_idempotent(monkeypatch):
    client, store, _ = await _client(monkeypatch)
    body = {
        "session_id": "session-pilot-001", "artifact_id": ARTIFACT,
        "client_request_id": "gate-f-route-0007",
    }
    async with client:
        started = await client.post(
            "/api/v1/background-pilot/artifact-grounded-brief",
            headers={"Idempotency-Key": body["client_request_id"]}, json=body)
        run_id = started.json()["run_id"]
        visible = await client.get(f"/api/v1/background-pilot/jobs/{run_id}")
        cancel_body = {"client_request_id": "gate-f-cancel-0001",
                       "reason": "Synthetic cancellation check"}
        cancelled = await client.post(
            f"/api/v1/background-pilot/jobs/{run_id}:cancel",
            headers={"Idempotency-Key": cancel_body["client_request_id"],
                     "If-Match": str(visible.json()["job"]["version"])},
            json=cancel_body)
        replay = await client.post(
            f"/api/v1/background-pilot/jobs/{run_id}:cancel",
            headers={"Idempotency-Key": cancel_body["client_request_id"],
                     "If-Match": str(visible.json()["job"]["version"])},
            json=cancel_body)
        step = (await store.list(
            "workflow_steps", filters={"run_id": run_id}, limit=2))[0]
        worker = await client.post(
            "/tasks/background-artifact-grounded-brief",
            headers=_worker_headers(),
            json={"workspace_id": "workspace-pilot", "run_id": run_id,
                  "step_id": step["step_id"]})
    assert cancelled.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert worker.status_code == 200
    assert worker.json()["duplicate"] is True
    assert worker.json()["runtime_status"] == "CANCELLED"
    assert await store.list("artifacts", filters={
        "artifact_kind": "BACKGROUND_GROUNDED_EVIDENCE_DRAFT"}) == []


def test_activity_ui_is_contextual_truthful_and_has_no_generic_dashboard():
    html = (REPO / "app/static/index.html").read_text()
    assert "Prepare private grounded brief" in html
    assert "One bounded model call; no web, connector, approval, action, or message" in html
    assert "/api/v1/background-pilot/artifact-grounded-brief" in html
    assert "BACKGROUND_ARTIFACT_PREPARATION_KILL_SWITCH" not in html
    assert "Generic Runs" not in html


def test_cloud_canary_evidence_pins_exact_implementation_and_cleanup():
    evidence = json.loads((
        REPO / "skills/evidence/spec40-gate-f-cloud-canary-20260829.json"
    ).read_text())
    paths = {
        "offline_qualification_evidence_sha256":
            "skills/evidence/spec40-gate-f-offline-qualification-20260829.json",
        "synthetic_runtime_evidence_sha256":
            "skills/evidence/spec40-gate-f-synthetic-runtime-20260829.json",
        "compiled_catalog_file_sha256": "skills/catalog.v1.json",
        "background_skill_runtime_sha256": "services/background_skill_runtime.py",
        "background_routes_sha256": "app/background_pilot_routes.py",
        "activity_ui_sha256": "app/static/index.html",
        "canary_host_sha256": "services/background_pilot_canary_app.py",
        "task_queue_sha256": "services/task_queue.py",
        "workload_identity_sha256": "services/workload_identity.py",
        "skill_models_sha256": "skills/models.py",
    }

    assert evidence["status"] == "PASSED_CONTROLLED_SYNTHETIC_CANARY"
    assert evidence["content_free"] is True
    assert evidence["cleanup"]["synthetic_workspace_remaining_records"] == 0
    assert all(
        value == 0 for key, value in evidence["cleanup"].items()
        if key.endswith("_count")
    )
    assert evidence["default_state"]["repository_admission_enabled"] is False
    assert evidence["default_state"]["repository_execution_enabled"] is False
    assert evidence["default_state"]["repository_kill_switch_active"] is True
    for key, relative in paths.items():
        actual = "sha256:" + hashlib.sha256((REPO / relative).read_bytes()).hexdigest()
        assert evidence["pinned_inputs"][key] == actual
