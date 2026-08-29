from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import background_pilot_routes
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import InMemoryDurableStore

pytestmark = pytest.mark.asyncio


def _principal(actor: str = "actor-founder",
               role: WorkspaceRole | str = WorkspaceRole.FOUNDER) -> ActorPrincipal:
    return ActorPrincipal(
        actor_id=actor, workspace_id="workspace-pilot", role=role,
        session_auth_time=1,
        membership_version=1, principal_kind="INTERACTIVE")


async def _client(monkeypatch, *, role: WorkspaceRole | str = WorkspaceRole.FOUNDER):
    store = InMemoryDurableStore()
    await store.create("artifacts", "a" * 32, {
        "artifact_id": "a" * 32, "workspace_id": "workspace-pilot",
        "founder_id": "workspace-pilot", "session_id": "session-pilot-001",
        "status": "READY", "index_generation": "generation-001",
        "sha256": "b" * 64, "size_bytes": 4096, "version": 1,
    })
    for key, value in {
        "BACKGROUND_JOB_ADMISSION_ENABLED": "true",
        "BACKGROUND_SPECIALIST_EXECUTION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PILOT_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED": "true",
        "BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH": "false",
        "BACKGROUND_ARTIFACT_PILOT_WORKSPACES": "workspace-pilot",
        "AGENT_BASE_URL": "https://pilot.example.test",
    }.items():
        monkeypatch.setenv(key, value)
    from services import task_queue

    monkeypatch.setattr(
        task_queue, "enqueue",
        lambda path, payload, dedupe_key, **kwargs: {"status": "success"})

    async def principal_resolver(request):
        actor = request.headers.get("X-Test-Actor", "actor-founder")
        return _principal(actor=actor, role=role)

    async def session_resolver(workspace_id, session_id):
        return (workspace_id == "workspace-pilot"
                and session_id == "session-pilot-001")

    app = FastAPI()
    background_pilot_routes.register(
        app, principal_resolver=principal_resolver,
        session_resolver=session_resolver, store_factory=lambda: store)
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver"), store


async def test_route_requires_idempotency_session_and_founder_role(monkeypatch):
    client, store = await _client(monkeypatch)
    body = {
        "session_id": "session-pilot-001", "artifact_id": "a" * 32,
        "client_request_id": "background-route-0001",
    }
    async with client:
        missing_key = await client.post(
            "/api/v1/background-pilot/artifact-analysis", json=body)
        wrong_session = await client.post(
            "/api/v1/background-pilot/artifact-analysis",
            headers={"Idempotency-Key": "background-route-0002"},
            json={**body, "client_request_id": "background-route-0002",
                  "session_id": "session-foreign-001"})
        accepted = await client.post(
            "/api/v1/background-pilot/artifact-analysis",
            headers={"Idempotency-Key": body["client_request_id"]}, json=body)
    assert missing_key.status_code == 400
    assert wrong_session.status_code == 404
    assert accepted.status_code == 202
    assert accepted.json()["dispatch_status"] == "DISPATCHED"
    assert len(await store.list("workflow_runs", filters={})) == 1

    observer_client, observer_store = await _client(
        monkeypatch, role="OBSERVER")
    async with observer_client:
        denied = await observer_client.post(
            "/api/v1/background-pilot/artifact-analysis",
            headers={"Idempotency-Key": "background-observer-0001"},
            json={**body, "client_request_id": "background-observer-0001"})
        hidden_session = await observer_client.post(
            "/api/v1/background-pilot/artifact-analysis",
            headers={"Idempotency-Key": "background-observer-0002"},
            json={**body, "client_request_id": "background-observer-0002",
                  "session_id": "session-foreign-001"})
    assert denied.status_code == 403
    assert await observer_store.list("workflow_runs", filters={}) == []
    assert hidden_session.status_code == 403


async def test_route_collapses_actor_private_reads_and_fences_cancel_version(
        monkeypatch):
    client, _ = await _client(monkeypatch)
    body = {
        "session_id": "session-pilot-001", "artifact_id": "a" * 32,
        "client_request_id": "background-route-0003",
    }
    async with client:
        started = await client.post(
            "/api/v1/background-pilot/artifact-analysis",
            headers={"Idempotency-Key": body["client_request_id"]}, json=body)
        run_id = started.json()["run_id"]
        visible = await client.get(
            f"/api/v1/background-pilot/jobs/{run_id}")
        hidden = await client.get(
            f"/api/v1/background-pilot/jobs/{run_id}",
            headers={"X-Test-Actor": "actor-other"})
        stale = await client.post(
            f"/api/v1/background-pilot/jobs/{run_id}:cancel",
            headers={"Idempotency-Key": "background-cancel-0001",
                     "If-Match": "999"},
            json={"client_request_id": "background-cancel-0001",
                  "reason": "stop"})
        cancelled = await client.post(
            f"/api/v1/background-pilot/jobs/{run_id}:cancel",
            headers={"Idempotency-Key": "background-cancel-0002",
                     "If-Match": str(visible.json()["job"]["version"])},
            json={"client_request_id": "background-cancel-0002",
                  "reason": "stop"})
        worker = await client.post(
            "/tasks/background-artifact-pilot",
            json={"workspace_id": "workspace-pilot", "run_id": run_id,
                  "step_id": "step-forged"})
    assert visible.status_code == 200
    assert hidden.status_code == 404
    assert stale.status_code == 409
    assert cancelled.status_code == 200
    assert worker.status_code == 401


async def test_route_rejects_extra_and_tampered_fields(monkeypatch):
    client, store = await _client(monkeypatch)
    async with client:
        extra = await client.post(
            "/api/v1/background-pilot/artifact-analysis",
            headers={"Idempotency-Key": "background-extra-0001"},
            json={"session_id": "session-pilot-001", "artifact_id": "a" * 32,
                  "client_request_id": "background-extra-0001",
                  "url": "https://attacker.invalid",
                  "tool_name": "email.send"})
        bad_id = await client.post(
            "/api/v1/background-pilot/artifact-analysis",
            headers={"Idempotency-Key": "background-badid-0001"},
            json={"session_id": "session-pilot-001",
                  "artifact_id": "../foreign",
                  "client_request_id": "background-badid-0001"})
    assert extra.status_code == 422
    assert bad_id.status_code == 422
    assert await store.list("workflow_runs", filters={}) == []
