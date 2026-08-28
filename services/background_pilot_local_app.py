"""Off-cloud synthetic host for the single Spec-40 artifact pilot.

Run only on loopback with the flags documented in
``docs/background-work-founder-artifact-pilot-evidence.md``. The process owns
one ephemeral in-memory workspace, founder, session, artifact, and chunk index;
stopping it deletes all synthetic state.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from app import background_pilot_routes
from services.actor_identity import ActorPrincipal, WorkspaceRole, create_membership
from services.background_pilot import (
    BackgroundPilotExecutor,
    StoredArtifactInventoryPort,
)
from services.durable_store import DurableStore, InMemoryDurableStore

WORKSPACE_ID = "local_spec40_workspace"
ACTOR_ID = "local_spec40_founder"
SESSION_ID = "local_spec40_session"
ARTIFACT_ID = "40" * 16
GENERATION = "local-generation-001"
FOUNDER_KEY_ENV = "BACKGROUND_PILOT_LOCAL_FOUNDER_KEY"
FOUNDER_COOKIE = "local_spec40_founder"
STATIC_ROOT = Path(__file__).resolve().parents[1] / "app" / "static"

_SOURCE_CHUNKS = (
    "Synthetic local Spec 40 evidence. It contains no real user or external data.",
    "The pilot may count and hash this harmless text but cannot send or act on it.",
)
_SOURCE_BYTES = "\n".join(_SOURCE_CHUNKS).encode()
STORE = InMemoryDurableStore()
RETRY_CONTROL = {"failures_remaining": 0, "injected_failures": 0}


def _required_configuration() -> dict[str, bool]:
    return {
        "admission_enabled": (
            os.environ.get("BACKGROUND_JOB_ADMISSION_ENABLED") == "true"
            and os.environ.get("BACKGROUND_ARTIFACT_PILOT_ENABLED") == "true"),
        "execution_enabled": (
            os.environ.get("BACKGROUND_SPECIALIST_EXECUTION_ENABLED") == "true"
            and os.environ.get("BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED")
            == "true"),
        "kill_switch_clear": (
            os.environ.get("BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH") == "false"),
        "conversation_delivery_off": (
            os.environ.get("BACKGROUND_CONVERSATION_DELIVERY_ENABLED", "false")
            == "false"),
        "single_workspace": (
            os.environ.get("BACKGROUND_ARTIFACT_PILOT_WORKSPACES")
            == WORKSPACE_ID),
        "loopback_dispatch": (
            os.environ.get("BACKGROUND_PILOT_ALLOW_TEST_DISPATCH") == "1"),
    }


async def _seed() -> None:
    if os.environ.get("K_SERVICE"):
        raise RuntimeError("synthetic local pilot cannot run in Cloud Run")
    if not all(_required_configuration().values()):
        raise RuntimeError("synthetic local pilot flags are not exact")
    if len(os.environ.get(FOUNDER_KEY_ENV, "")) < 32:
        raise RuntimeError("synthetic founder key must contain at least 32 characters")
    STORE.records.clear()
    RETRY_CONTROL.update(failures_remaining=0, injected_failures=0)
    membership = await create_membership(
        actor_id=ACTOR_ID, workspace_id=WORKSPACE_ID,
        auth_subject="local-spec40:founder", role=WorkspaceRole.FOUNDER,
        created_by="local-spec40-bootstrap", store=STORE,
        synthetic=True, local_only=True)
    if membership.get("error"):
        raise RuntimeError("synthetic founder membership could not be created")
    await STORE.create("local_pilot_sessions", SESSION_ID, {
        "schema_version": 1, "session_id": SESSION_ID,
        "workspace_id": WORKSPACE_ID, "actor_id": ACTOR_ID,
        "synthetic": True, "status": "ACTIVE", "version": 1,
    })
    await STORE.create("artifacts", ARTIFACT_ID, {
        "schema_version": 1, "artifact_id": ARTIFACT_ID,
        "artifact_kind": "SYNTHETIC_LOCAL_TEXT",
        "workspace_id": WORKSPACE_ID, "founder_id": WORKSPACE_ID,
        "actor_id": ACTOR_ID, "session_id": SESSION_ID,
        "status": "READY", "index_generation": GENERATION,
        "sha256": hashlib.sha256(_SOURCE_BYTES).hexdigest(),
        "size_bytes": len(_SOURCE_BYTES), "chunk_count": len(_SOURCE_CHUNKS),
        "synthetic": True, "source_ref": "local://spec40/harmless-fixture",
        "version": 1,
    })
    for ordinal, content in enumerate(_SOURCE_CHUNKS, start=1):
        chunk_id = f"local_chunk_{ordinal:02d}"
        await STORE.create("local_artifact_chunks", chunk_id, {
            "schema_version": 1, "id": chunk_id,
            "artifact_id": ARTIFACT_ID, "generation": GENERATION,
            "ordinal": ordinal, "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "locator": {"section": f"synthetic-{ordinal}"},
            "synthetic": True, "version": 1,
        })


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await _seed()
    yield
    STORE.records.clear()


app = FastAPI(title="Spec 40 local artifact pilot", lifespan=_lifespan)


async def _principal(request: Request) -> ActorPrincipal | dict[str, Any]:
    expected = os.environ.get(FOUNDER_KEY_ENV, "")
    presented = (request.headers.get("X-Local-Founder-Key", "")
                 or request.cookies.get(FOUNDER_COOKIE, ""))
    if not expected or not hmac.compare_digest(expected, presented):
        return {"status": "error", "error": True,
                "error_code": "interactive_founder_required",
                "message": "Synthetic local founder authorization is required."}
    members = await STORE.list("workspace_members", filters={
        "workspace_id": WORKSPACE_ID, "actor_id": ACTOR_ID,
        "role": "FOUNDER", "status": "ACTIVE", "local_only": True}, limit=2)
    if len(members) != 1:
        return {"status": "error", "error": True,
                "error_code": "membership_missing",
                "message": "Synthetic local founder membership is missing."}
    member = members[0]
    return ActorPrincipal(
        actor_id=ACTOR_ID, workspace_id=WORKSPACE_ID,
        role=WorkspaceRole.FOUNDER, role_grants=frozenset(),
        candidate_assignments=frozenset(), interview_assignments=frozenset(),
        session_auth_time=1, membership_version=int(member["version"]),
        principal_kind="INTERACTIVE", membership_id=str(member["membership_id"]))


async def _session(workspace_id: str, session_id: str) -> bool:
    row = await STORE.get("local_pilot_sessions", session_id)
    return bool(row and row.get("workspace_id") == workspace_id
                and row.get("status") == "ACTIVE")


async def _chunks(artifact_id: str, limit: int) -> list[dict[str, Any]]:
    try:
        delay = int(os.environ.get(
            "BACKGROUND_PILOT_LOCAL_INSPECTION_DELAY_MS", "600")) / 1000
    except ValueError:
        delay = 0.6
    await asyncio.sleep(max(0.1, min(3.0, delay)))
    return await STORE.list(
        "local_artifact_chunks", filters={"artifact_id": artifact_id},
        order_by="ordinal", limit=limit)


class _LocalRetryInventoryPort(StoredArtifactInventoryPort):
    """Inject one bounded transient failure only when the local test arms it."""

    async def inspect(self, **kwargs: Any) -> dict[str, Any]:
        if RETRY_CONTROL["failures_remaining"]:
            RETRY_CONTROL["failures_remaining"] -= 1
            RETRY_CONTROL["injected_failures"] += 1
            raise RuntimeError("synthetic bounded transient inspection failure")
        return await super().inspect(**kwargs)


def _executor(store: DurableStore) -> BackgroundPilotExecutor:
    return BackgroundPilotExecutor(
        store, inventory_port=_LocalRetryInventoryPort(
            store, chunk_loader=_chunks))


background_pilot_routes.register(
    app, principal_resolver=_principal, session_resolver=_session,
    store_factory=lambda: STORE, executor_factory=_executor)


async def _founder_or_401(request: Request) -> JSONResponse | None:
    principal = await _principal(request)
    if isinstance(principal, dict):
        return JSONResponse(principal, status_code=401)
    return None


@app.get("/")
async def product_shell(request: Request, key: str = ""):
    """Serve the normal product shell after one local founder-cookie bootstrap."""
    expected = os.environ.get(FOUNDER_KEY_ENV, "")
    if key and expected and hmac.compare_digest(expected, key):
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            FOUNDER_COOKIE, key, httponly=True, samesite="strict", path="/")
        return response
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/favicon.svg")
async def favicon():
    return FileResponse(STATIC_ROOT / "favicon.svg")


@app.get("/auth/me")
async def auth_me(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return {
        "mode": "session", "name": "Synthetic Local Founder",
        "email": "founder@local.invalid", "role": "FOUNDER",
        "csrf_token": "local-spec40-synthetic-csrf",
    }


@app.get("/api/config")
async def config(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return {"persona_name": "Alex", "workflow_id": "spec40-local-pilot"}


@app.post("/api/v1/sessions")
async def create_local_session(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return {"status": "success", "session_id": SESSION_ID, "synthetic": True}


@app.get("/api/v1/sessions/{session_id}/messages")
async def local_messages(session_id: str, request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    if session_id != SESSION_ID:
        return JSONResponse({"detail": "session not found"}, status_code=404)
    return {"messages": []}


@app.get("/api/v1/sessions/{session_id}/resources")
async def local_resources(session_id: str, request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    if session_id != SESSION_ID:
        return JSONResponse({"detail": "session not found"}, status_code=404)
    return {
        "resources": [{
            "result_type": "artifact",
            "canonical_ref": {"id": ARTIFACT_ID},
            "status": "READY",
            "title": "Harmless synthetic Spec 40 artifact",
            "latest_occurrence": {
                "relationship": "uploaded",
                "occurred_at": "2026-08-28T00:00:00Z",
            },
            "focus": {"kind": "artifact", "id": ARTIFACT_ID},
        }],
        "truncated": False,
    }


@app.get("/api/v1/pipeline")
async def local_pipeline(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return {"opportunities": {}, "applications": [], "session_id": SESSION_ID}


@app.get("/api/v1/investor-outreach")
async def local_investor_outreach(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return {"outreaches": []}


@app.get("/api/v1/documents")
async def local_documents(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return {"documents": []}


@app.get("/api/v1/waits")
async def local_waits(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return {"waits": [], "changed": []}


@app.get("/api/v1/founder-inbox")
async def local_founder_inbox(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return {"items": []}


@app.get("/api/v1/approvals")
async def local_approvals(request: Request):
    """Return an empty disabled surface; this pilot can never create approval work."""
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return {"approvals": []}


@app.get("/api/v1/browser/events")
@app.get("/api/v1/events/stream")
async def disabled_local_streams(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return Response(status_code=204)


@app.post("/local-test/retry-next")
async def retry_next(request: Request):
    """Arm exactly one synthetic transient failure for recovery verification."""
    denied = await _founder_or_401(request)
    if denied:
        return denied
    if RETRY_CONTROL["failures_remaining"]:
        return JSONResponse(
            {"detail": "one synthetic retry is already armed"}, status_code=409)
    RETRY_CONTROL["failures_remaining"] = 1
    return {"status": "success", "failures_armed": 1}


@app.get("/health")
async def health():
    return {"status": "ok", "synthetic": True,
            "flags": _required_configuration()}


@app.get("/local-test/state")
async def local_state(request: Request):
    principal = await _principal(request)
    if isinstance(principal, dict):
        return JSONResponse(principal, status_code=401)
    outputs = [row for row in await STORE.list("artifacts", filters={})
               if row.get("artifact_kind") == "BACKGROUND_ARTIFACT_INVENTORY"]
    attempts = await STORE.list("step_attempts", filters={})
    runs = await STORE.list("workflow_runs", filters={})
    forbidden = {}
    for collection in (
            "approvals", "external_actions", "conversation_deliveries",
            "wake_deliveries", "memory_items", "memory_write_receipts"):
        forbidden[collection] = len(await STORE.list(collection, filters={}))
    content_free = all(
        not ({"content", "text", "raw_text", "source_text"} & set(output))
        and all(not ({"content", "text", "raw_text"} & set(citation))
                for citation in output.get("citations") or [])
        for output in outputs)
    return {
        "status": "success", "workspace_id": WORKSPACE_ID,
        "actor_id": ACTOR_ID, "role": "FOUNDER", "session_id": SESSION_ID,
        "artifact_id": ARTIFACT_ID, "artifact_status": "READY",
        "source_kind": "SYNTHETIC_LOCAL_TEXT", "source_bytes": len(_SOURCE_BYTES),
        "source_chunks": len(_SOURCE_CHUNKS), "output_count": len(outputs),
        "output_content_free": content_free,
        "output_keys": sorted(outputs[0]) if outputs else [],
        "run_statuses": sorted(str(run.get("runtime_status")) for run in runs),
        "attempt_count": len(attempts),
        "failed_attempt_count": sum(
            1 for attempt in attempts if attempt.get("status") == "FAILED"),
        "injected_retry_failures": RETRY_CONTROL["injected_failures"],
        "retry_failure_armed": bool(RETRY_CONTROL["failures_remaining"]),
        "forbidden_collection_counts": forbidden,
        "flags": _required_configuration(),
    }
