"""Private, synthetic-only Cloud Run host for the Spec 40 canary.

This service is deliberately separate from the product app.  It exposes only
one enabled pilot at a time, fixed synthetic canary controls, and a health
check. The Gate F profile permits one closed Vertex call but has no agent
runner, web, browser, connector, approval, effect, memory,
conversation-delivery, or user-selected routing surface.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import background_pilot_routes
from services import firestore
from services.actor_identity import ActorPrincipal, WorkspaceRole, create_membership
from services.background_pilot import BackgroundPilotExecutor, StoredArtifactInventoryPort
from services.background_skill_runtime import (
    GateFSkillExecutor,
    GateFSkillFlags,
    StoredSelectedArtifactPort,
    VertexGroundedDraftModelPort,
)
from services.durable_store import DurableStore, production_store

WORKSPACE_ID = "spec40_canary_workspace_20260828"
ACTOR_ID = "spec40_canary_founder_20260828"
SESSION_ID = "spec40_canary_session_20260828"
ARTIFACT_ID = "41" * 16
GENERATION = "spec40-canary-generation-001"
CONTROL_ID = "spec40_canary_control_20260828"
FOUNDER_KEY_ENV = "SPEC40_CANARY_FOUNDER_KEY"
FOUNDER_HEADER = "X-Spec40-Canary-Key"

_SOURCE_CHUNKS = (
    "Synthetic company records three internal usability trials with twelve fictional participants.",
    "Synthetic records provide no revenue or independent validation figures.",
)
_SOURCE_BYTES = "\n".join(_SOURCE_CHUNKS).encode()
_FORBIDDEN_COLLECTIONS = (
    "approvals", "external_actions", "conversation_deliveries",
    "wake_deliveries", "memory_items", "memory_write_receipts",
)
_CANARY_COLLECTIONS = (
    "action_execution_outbox", "approvals", "audit",
    "background_pilot_capacity", "budget_consumption_receipts",
    "command_outbox", "command_receipts", "conversation_deliveries",
    "external_actions", "founder_inbox", "memory_items",
    "memory_write_receipts", "operational_snapshots", "projection_events",
    "projection_streams", "run_events", "slo_observations", "step_attempts",
    "waits", "wake_deliveries", "workflow_plans", "workflow_runs",
    "workflow_steps", "workspace_budgets", "workspace_members",
)


def _exact_configuration() -> dict[str, bool]:
    """Report the fail-closed canary envelope without exposing secrets."""
    return {
        "cloud_run": bool(os.environ.get("K_SERVICE")),
        "gate_f_selected": (
            os.environ.get("SPEC40_CANARY_TEMPLATE")
            == "pilot.artifact_grounded_brief@1"),
        "admission_enabled": (
            os.environ.get("BACKGROUND_JOB_ADMISSION_ENABLED") == "true"
            and os.environ.get("BACKGROUND_SKILLS_ENABLED") == "true"
            and os.environ.get("BACKGROUND_ARTIFACT_PREPARATION_ENABLED")
            == "true"),
        "execution_enabled": (
            os.environ.get("BACKGROUND_SPECIALIST_EXECUTION_ENABLED") == "true"
            and os.environ.get(
                "BACKGROUND_ARTIFACT_PREPARATION_EXECUTION_ENABLED")
            == "true"),
        "kill_switch_clear": (
            os.environ.get("BACKGROUND_ARTIFACT_PREPARATION_KILL_SWITCH")
            == "false"),
        "single_workspace": (
            os.environ.get("BACKGROUND_ARTIFACT_PREPARATION_WORKSPACES")
            == WORKSPACE_ID),
        "exact_queue": (
            os.environ.get("BACKGROUND_ARTIFACT_PREPARATION_QUEUE")
            == "co-founder-background-skill-gate-f"),
        "inventory_pilot_disabled": (
            os.environ.get("BACKGROUND_ARTIFACT_PILOT_ENABLED", "false")
            != "true"
            and os.environ.get(
                "BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED", "false")
            != "true"
            and os.environ.get("BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH", "true")
            != "false"),
        "conversation_delivery_off": (
            os.environ.get("BACKGROUND_CONVERSATION_DELIVERY_ENABLED", "false")
            == "false"),
        "local_dispatch_off": (
            os.environ.get("BACKGROUND_PILOT_ALLOW_TEST_DISPATCH", "0") != "1"),
    }


def _key_valid(request: Request) -> bool:
    expected = os.environ.get(FOUNDER_KEY_ENV, "").strip()
    presented = request.headers.get(FOUNDER_HEADER, "").strip()
    return (len(expected) >= 32 and len(presented) >= 32
            and hmac.compare_digest(expected, presented))


async def _principal(request: Request) -> ActorPrincipal | dict[str, Any]:
    if not _key_valid(request):
        return {"status": "error", "error": True,
                "error_code": "interactive_founder_required",
                "message": "Synthetic canary founder authorization is required."}
    store = production_store()
    members = await store.list("workspace_members", filters={
        "workspace_id": WORKSPACE_ID, "actor_id": ACTOR_ID,
        "role": "FOUNDER", "status": "ACTIVE", "synthetic": True,
    }, limit=2)
    if len(members) != 1:
        return {"status": "error", "error": True,
                "error_code": "membership_missing",
                "message": "Synthetic canary Founder membership is missing."}
    member = members[0]
    return ActorPrincipal(
        actor_id=ACTOR_ID, workspace_id=WORKSPACE_ID,
        role=WorkspaceRole.FOUNDER, role_grants=frozenset(),
        candidate_assignments=frozenset(), interview_assignments=frozenset(),
        session_auth_time=1, membership_version=int(member["version"]),
        principal_kind="INTERACTIVE", membership_id=str(member["membership_id"]))


async def _session(workspace_id: str, session_id: str) -> bool:
    row = await production_store().get("operational_snapshots", CONTROL_ID)
    return bool(row and row.get("workspace_id") == workspace_id
                and row.get("session_id") == session_id
                and row.get("status") == "ACTIVE"
                and row.get("synthetic") is True)


async def _consume_control(name: str) -> bool:
    store = production_store()
    for _ in range(4):
        row = await store.get("operational_snapshots", CONTROL_ID)
        if not row or int(row.get(name) or 0) < 1:
            return False
        updated = await store.compare_and_set(
            "operational_snapshots", CONTROL_ID, int(row["version"]),
            {name: int(row[name]) - 1})
        if updated:
            return True
    raise RuntimeError("synthetic canary control changed concurrently")


class _CanaryInventoryPort(StoredArtifactInventoryPort):
    """Inject only fixed, bounded synthetic failure modes for recovery proof."""

    async def inspect(self, **kwargs: Any) -> dict[str, Any]:
        if await _consume_control("failures_remaining"):
            raise RuntimeError("synthetic bounded canary failure")
        if await _consume_control("timeouts_remaining"):
            await asyncio.sleep(3)
        if await _consume_control("delays_remaining"):
            await asyncio.sleep(10)
        return await super().inspect(**kwargs)


class _CanaryEvidencePort(StoredSelectedArtifactPort):
    """Inject fixed pre-model recovery/cancellation conditions."""

    async def read(self, **kwargs: Any) -> dict[str, Any]:
        if await _consume_control("failures_remaining"):
            raise RuntimeError("synthetic bounded canary failure")
        if await _consume_control("timeouts_remaining"):
            raise TimeoutError("synthetic bounded canary timeout")
        if await _consume_control("delays_remaining"):
            await asyncio.sleep(10)
        return await super().read(**kwargs)


def _executor(store: DurableStore) -> BackgroundPilotExecutor:
    return BackgroundPilotExecutor(
        store, inventory_port=_CanaryInventoryPort(store), timeout_seconds=1)


def _skill_executor(store: DurableStore) -> GateFSkillExecutor:
    return GateFSkillExecutor(
        store, flags=GateFSkillFlags.from_env(),
        evidence_port=_CanaryEvidencePort(store),
        model_port=VertexGroundedDraftModelPort(
            project_id=os.environ.get("GOOGLE_CLOUD_PROJECT", "")),
        timeout_seconds=120,
    )


app = FastAPI(
    title="Spec 40 private synthetic canary",
    docs_url=None, redoc_url=None, openapi_url=None,
)
background_pilot_routes.register(
    app, principal_resolver=_principal, session_resolver=_session,
    store_factory=production_store, executor_factory=_executor,
    skill_executor_factory=_skill_executor)


async def _founder_or_401(request: Request) -> JSONResponse | None:
    principal = await _principal(request)
    if isinstance(principal, dict):
        return JSONResponse(principal, status_code=401)
    return None


@app.get("/health")
async def health():
    exact = _exact_configuration()
    return {"status": "ok" if all(exact.values()) else "blocked",
            "synthetic": True,
            "template_id": "pilot.artifact_grounded_brief@1",
            "flags": _exact_configuration()}


@app.post("/canary/seed")
async def seed(request: Request):
    if not _key_valid(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not all(_exact_configuration().values()):
        return JSONResponse({"error": "canary configuration is not exact"},
                            status_code=409)
    store = production_store()
    existing = await store.get("operational_snapshots", CONTROL_ID)
    if existing:
        return {"status": "success", "duplicate": True,
                "workspace_id": WORKSPACE_ID, "session_id": SESSION_ID,
                "artifact_id": ARTIFACT_ID}
    membership = await create_membership(
        actor_id=ACTOR_ID, workspace_id=WORKSPACE_ID,
        auth_subject="spec40-canary:synthetic-founder",
        role=WorkspaceRole.FOUNDER, created_by="spec40-canary-bootstrap",
        store=store, synthetic=True, local_only=False)
    if membership.get("error"):
        return JSONResponse(membership, status_code=409)
    await store.create("operational_snapshots", CONTROL_ID, {
        "schema_version": 1, "snapshot_id": CONTROL_ID,
        "snapshot_kind": "SPEC40_SYNTHETIC_CANARY_CONTROL",
        "workspace_id": WORKSPACE_ID, "actor_id": ACTOR_ID,
        "session_id": SESSION_ID, "status": "ACTIVE", "synthetic": True,
        "failures_remaining": 0, "timeouts_remaining": 0,
        "delays_remaining": 0, "version": 1,
    })
    await store.create("artifacts", ARTIFACT_ID, {
        "schema_version": 1, "artifact_id": ARTIFACT_ID,
        "artifact_kind": "SYNTHETIC_CANARY_TEXT",
        "workspace_id": WORKSPACE_ID, "founder_id": WORKSPACE_ID,
        "actor_id": ACTOR_ID, "session_id": SESSION_ID,
        "status": "READY", "index_generation": GENERATION,
        "sha256": hashlib.sha256(_SOURCE_BYTES).hexdigest(),
        "size_bytes": len(_SOURCE_BYTES), "chunk_count": len(_SOURCE_CHUNKS),
        "synthetic": True, "source_ref": "synthetic://spec40/harmless-fixture",
        "version": 1,
    })
    artifact_ref = firestore.get_client().collection("artifacts").document(ARTIFACT_ID)
    for ordinal, content in enumerate(_SOURCE_CHUNKS, start=1):
        await artifact_ref.collection("chunks").document(
            f"canary_chunk_{ordinal:02d}").create({
                "schema_version": 1, "generation": GENERATION,
                "ordinal": ordinal, "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "locator": {"page": ordinal},
                "synthetic": True,
            })
    return {"status": "success", "duplicate": False,
            "workspace_id": WORKSPACE_ID, "actor_id": ACTOR_ID,
            "role": "FOUNDER", "session_id": SESSION_ID,
            "artifact_id": ARTIFACT_ID, "source_bytes": len(_SOURCE_BYTES),
            "source_chunks": len(_SOURCE_CHUNKS)}


@app.post("/canary/arm/{mode}")
async def arm(mode: str, request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    field = {"failure": "failures_remaining", "timeout": "timeouts_remaining",
             "delay": "delays_remaining"}.get(mode)
    if not field:
        return JSONResponse({"error": "unknown fixed canary mode"}, status_code=404)
    store = production_store()
    for _ in range(4):
        row = await store.get("operational_snapshots", CONTROL_ID)
        if not row:
            return JSONResponse({"error": "canary is not seeded"}, status_code=409)
        if int(row.get(field) or 0):
            return JSONResponse({"error": "mode is already armed"}, status_code=409)
        if await store.compare_and_set(
                "operational_snapshots", CONTROL_ID, int(row["version"]),
                {field: 1}):
            return {"status": "success", "mode": mode, "armed": 1}
    return JSONResponse({"error": "control changed concurrently"}, status_code=409)


@app.post("/canary/failure-probe")
async def failure_probe(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    return JSONResponse({"status": "synthetic_failure_probe"}, status_code=503)


@app.get("/canary/state")
async def state(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    store = production_store()
    runs = await store.list("workflow_runs", filters={"workspace_id": WORKSPACE_ID},
                            limit=20)
    attempts = await store.list("step_attempts", filters={
        "workspace_id": WORKSPACE_ID}, limit=50)
    outputs = [row for row in await store.list(
        "artifacts", filters={"workspace_id": WORKSPACE_ID}, limit=50)
        if row.get("artifact_kind") == "BACKGROUND_GROUNDED_EVIDENCE_DRAFT"]
    forbidden = {name: len(await store.list(
        name, filters={"workspace_id": WORKSPACE_ID}, limit=20))
        for name in _FORBIDDEN_COLLECTIONS}
    return {
        "status": "success", "workspace_id": WORKSPACE_ID,
        "actor_id": ACTOR_ID, "role": "FOUNDER", "session_id": SESSION_ID,
        "artifact_id": ARTIFACT_ID, "source_bytes": len(_SOURCE_BYTES),
        "source_chunks": len(_SOURCE_CHUNKS), "run_count": len(runs),
        "run_statuses": sorted(str(row.get("runtime_status")) for row in runs),
        "attempt_count": len(attempts),
        "failed_attempt_count": sum(
            row.get("status") == "FAILED" for row in attempts),
        "output_count": len(outputs),
        "output_private_draft_only": all(
            output.get("visibility_scope") == "ACTOR_PRIVATE"
            and output.get("draft_status") == "DRAFT"
            for output in outputs),
        "output_citation_counts": sorted(
            int(output.get("citation_count") or 0) for output in outputs),
        "output_content_hashes": sorted(
            str(output.get("content_hash") or "") for output in outputs),
        "output_keys": sorted(outputs[0]) if outputs else [],
        "forbidden_collection_counts": forbidden,
        "flags": _exact_configuration(),
    }


@app.delete("/canary/cleanup")
async def cleanup(request: Request):
    denied = await _founder_or_401(request)
    if denied:
        return denied
    client = firestore.get_client()
    deleted: dict[str, int] = {}
    artifact_refs = [snap.reference async for snap in client.collection(
        "artifacts").where("workspace_id", "==", WORKSPACE_ID).stream()]
    for ref in artifact_refs:
        chunks = [snap.reference async for snap in ref.collection("chunks").stream()]
        for chunk in chunks:
            await chunk.delete()
        await ref.delete()
    deleted["artifacts"] = len(artifact_refs)
    for collection in _CANARY_COLLECTIONS:
        refs = [snap.reference async for snap in client.collection(collection).where(
            "workspace_id", "==", WORKSPACE_ID).stream()]
        for ref in refs:
            await ref.delete()
        deleted[collection] = len(refs)
    remaining = 0
    for collection in (*_CANARY_COLLECTIONS, "artifacts"):
        remaining += len([snap async for snap in client.collection(collection).where(
            "workspace_id", "==", WORKSPACE_ID).limit(1).stream()])
    return {"status": "success" if not remaining else "error",
            "workspace_id": WORKSPACE_ID, "deleted": deleted,
            "remaining_records": remaining}
