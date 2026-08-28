"""Founder-only live pilot for one deterministic artifact-analysis job.

This module is the only live background-work lane. It accepts one immutable,
already-ingested artifact, dispatches an opaque Cloud Task, reads stored chunks,
and commits a content-free evidence inventory. It cannot call a model, browser,
connector, approval service, consequence kernel, or durable memory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from services import background_pilot_metrics
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.background_work import (
    FOUNDATION_TEMPLATE,
    BackgroundInputRef,
    BackgroundJobRequest,
    BackgroundWorkService,
    enabled_foundation_templates,
)
from services.canonical import canonical_hash
from services.command_service import CommandService
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.workflow_contracts import RuntimeStatus, stable_id, utc_now
from services.workflow_runtime import WorkflowRuntime

_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "REJECTED", "CANCELLED"})
_ARTIFACT_ID = re.compile(r"^[a-f0-9]{32}$")
_HASH = re.compile(r"^[a-f0-9]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
_ALLOWED_ARTIFACT_STATUS = frozenset({"READY", "CONFIRMED"})
_CAPTION_BY_EVENT = {
    "RUN_CREATED": ("QUEUED", "I’ve queued the artifact analysis. You can keep chatting."),
    "STEP_STARTED": ("RUNNING", "I’m reviewing the artifact evidence."),
    "STEP_FAILED": ("RUNNING", "I hit a temporary problem and will retry safely."),
    "RUN_FAILED": ("FAILED", "I couldn’t finish the artifact analysis. Nothing was sent."),
    "RUN_CANCELLING": ("CANCELLING", "I’m stopping the artifact analysis safely."),
    "RUN_CANCELLED": ("CANCELLED", "I stopped the artifact analysis. Nothing was sent."),
    "RUN_SUCCEEDED": (
        "SUCCEEDED",
        "I finished the artifact analysis. The evidence inventory is ready."),
}


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "retryable": retryable}


def _bool_env(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {
        "1", "true", "yes", "on"}


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class BackgroundPilotFlags:
    admission_enabled: bool
    execution_enabled: bool
    kill_switch_active: bool
    workspace_allowlist: frozenset[str]

    @classmethod
    def from_env(cls) -> "BackgroundPilotFlags":
        return cls(
            admission_enabled=(
                _bool_env("BACKGROUND_JOB_ADMISSION_ENABLED")
                and _bool_env("BACKGROUND_ARTIFACT_PILOT_ENABLED")),
            execution_enabled=(
                _bool_env("BACKGROUND_SPECIALIST_EXECUTION_ENABLED")
                and _bool_env("BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED")),
            kill_switch_active=_bool_env(
                "BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH", True),
            workspace_allowlist=frozenset(
                item.strip() for item in os.environ.get(
                    "BACKGROUND_ARTIFACT_PILOT_WORKSPACES", "").split(",")
                if item.strip()),
        )

    def admission_gate(self, workspace_id: str) -> dict[str, Any]:
        if self.kill_switch_active:
            return _error(
                "background_pilot_killed",
                "The background artifact pilot is paused by its kill switch.")
        if not self.admission_enabled:
            return _error(
                "background_pilot_disabled",
                "The background artifact pilot is not enabled.")
        if workspace_id not in self.workspace_allowlist:
            return _error(
                "background_pilot_workspace_denied",
                "This workspace is not allowlisted for the pilot.")
        return {"status": "success"}

    def execution_gate(self, workspace_id: str) -> dict[str, Any]:
        if self.kill_switch_active:
            return _error(
                "background_pilot_killed",
                "The pilot is paused; accepted work remains queued.",
                retryable=True)
        if not self.execution_enabled:
            return _error(
                "background_pilot_execution_disabled",
                "Pilot execution is disabled; accepted work remains queued.",
                retryable=True)
        if workspace_id not in self.workspace_allowlist:
            return _error(
                "background_pilot_workspace_denied",
                "This workspace is not allowlisted for the pilot.")
        return {"status": "success"}


class ArtifactInventoryPort(Protocol):
    async def inspect(self, *, workspace_id: str, actor_id: str,
                      session_id: str, artifact_id: str,
                      expected_version: str, expected_hash: str,
                      max_bytes: int, max_chunks: int,
                      max_output_bytes: int) -> dict[str, Any]: ...


class StoredArtifactInventoryPort:
    """Read only the app's durable artifact row and current indexed chunks."""

    def __init__(
            self, store: DurableStore | None = None, *,
            chunk_loader: Callable[[str, int],
                                   Awaitable[list[dict[str, Any]]]] | None = None):
        self.store = store or production_store()
        self.chunk_loader = chunk_loader

    async def inspect(self, *, workspace_id: str, actor_id: str,
                      session_id: str, artifact_id: str,
                      expected_version: str, expected_hash: str,
                      max_bytes: int, max_chunks: int,
                      max_output_bytes: int) -> dict[str, Any]:
        del actor_id
        artifact = await self.store.get("artifacts", artifact_id)
        if (not artifact or artifact.get("workspace_id", artifact.get("founder_id"))
                != workspace_id or artifact.get("session_id") != session_id):
            return _error("background_artifact_not_found", "Artifact does not exist.")
        if (artifact.get("status") not in _ALLOWED_ARTIFACT_STATUS
                or str(artifact.get("index_generation") or "") != expected_version
                or f"sha256:{artifact.get('sha256', '')}" != expected_hash):
            return _error(
                "input_stale",
                "The artifact changed or is no longer ready for analysis.")
        size_bytes = int(artifact.get("size_bytes") or 0)
        if size_bytes < 1 or size_bytes > max_bytes:
            return _error(
                "budget_exhausted",
                "The artifact exceeds the pilot byte budget.")
        declared_chunks = int(artifact.get("chunk_count") or 0)
        if declared_chunks > max_chunks:
            return _error(
                "budget_exhausted",
                "The artifact exceeds the pilot evidence-chunk budget.")
        if self.chunk_loader:
            chunks = await self.chunk_loader(artifact_id, max_chunks + 1)
        else:
            from services import firestore

            chunks = await firestore.list_artifact_chunks(
                artifact_id, limit=max_chunks + 1)
        if not chunks:
            return _error(
                "validation_failed",
                "The artifact has no current indexed evidence.")
        if len(chunks) > max_chunks:
            return _error(
                "budget_exhausted",
                "The artifact exceeds the pilot evidence-chunk budget.")
        if declared_chunks and declared_chunks != len(chunks):
            return _error("input_stale", "Artifact evidence count changed.")
        characters = 0
        words = 0
        locator_counts: dict[str, int] = {}
        citations: list[dict[str, Any]] = []
        digest_material: list[dict[str, Any]] = []
        for chunk in chunks:
            if str(chunk.get("generation") or "") != expected_version:
                return _error("input_stale", "Artifact evidence generation changed.")
            content = str(chunk.get("content") or "")
            content_hash = str(chunk.get("content_sha256") or "")
            if not content_hash:
                content_hash = hashlib.sha256(content.encode()).hexdigest()
            if not _HASH.fullmatch(content_hash):
                return _error("validation_failed", "Artifact evidence hash is invalid.")
            locator = chunk.get("locator") if isinstance(
                chunk.get("locator"), dict) else {}
            characters += len(content)
            words += len(re.findall(r"\b\w+\b", content))
            for key in ("page", "slide", "paragraph", "section", "sheet", "cell_range"):
                if locator.get(key) not in (None, ""):
                    locator_counts[key] = locator_counts.get(key, 0) + 1
            citation = {
                "artifact_id": artifact_id,
                "chunk_id": str(chunk.get("id") or ""),
                "locator": locator,
                "content_sha256": content_hash,
            }
            citations.append(citation)
            digest_material.append(citation)
        inventory = {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "artifact_version": expected_version,
            "artifact_sha256": expected_hash,
            "chunk_count": len(chunks),
            "character_count": characters,
            "word_count": words,
            "locator_counts": dict(sorted(locator_counts.items())),
            "citations": citations,
            "analysis_kind": "DETERMINISTIC_EVIDENCE_INVENTORY",
            "model_calls": 0,
            "provider_calls": 0,
            "external_reads": 0,
            "tokens": 0,
        }
        inventory["content_hash"] = canonical_hash(
            {"inventory": inventory, "digest_material": digest_material},
            domain="background-artifact-inventory")
        if len(json.dumps(
                inventory, sort_keys=True, separators=(",", ":")).encode()) \
                > max_output_bytes:
            return _error(
                "budget_exhausted",
                "The artifact inventory exceeds the pilot output budget.")
        return {"status": "success", "inventory": inventory}


async def _audit(store: DurableStore, *, workspace_id: str, actor_id: str,
                 action: str, target: str, result: str,
                 code: str = "") -> None:
    audit_id = stable_id(
        "audit", workspace_id, action, target, result, code or "none")
    await store.create("audit", audit_id, {
        "schema_version": 2, "audit_id": audit_id,
        "founder_id": workspace_id, "workspace_id": workspace_id,
        "actor": actor_id, "actor_id": actor_id, "action": action,
        "target": target, "result": result, "error_code": code or None,
        "detail": "background_artifact_pilot", "created_at": utc_now(),
        "version": 1,
    })


class BackgroundArtifactPilot:
    """Admission, actor-private reads, cancellation, and UI projections."""

    def __init__(self, store: DurableStore | None = None, *,
                 flags: BackgroundPilotFlags | None = None,
                 dispatcher: "BackgroundPilotDispatcher | None" = None):
        self.store = store or production_store()
        self.flags = flags or BackgroundPilotFlags.from_env()
        self.dispatcher = dispatcher or BackgroundPilotDispatcher(
            self.store, flags=self.flags)

    async def _deny(self, principal: ActorPrincipal, code: str,
                    message: str) -> dict[str, Any]:
        background_pilot_metrics.record(
            "background_pilot_admission_denied", status="denied",
            error_code=code, template_id=FOUNDATION_TEMPLATE.template_id,
            kill_switch_active=self.flags.kill_switch_active)
        await _audit(
            self.store, workspace_id=principal.workspace_id,
            actor_id=principal.actor_id, action="background_pilot.admission",
            target="pilot.artifact_evidence_inventory", result="denied",
            code=code)
        return _error(code, message)

    async def start(self, *, principal: ActorPrincipal, session_id: str,
                    artifact_id: str, client_request_id: str) -> dict[str, Any]:
        started_at = time.perf_counter()
        gate = self.flags.admission_gate(principal.workspace_id)
        if gate.get("error"):
            return await self._deny(
                principal, str(gate["error_code"]), str(gate["message"]))
        if principal.principal_kind != "INTERACTIVE":
            return await self._deny(
                principal, "interactive_founder_required",
                "A signed-in interactive founder is required.")
        if principal.role is not WorkspaceRole.FOUNDER:
            return await self._deny(
                principal, "interactive_founder_required",
                "A signed-in interactive founder is required.")
        if not _ARTIFACT_ID.fullmatch(artifact_id):
            return await self._deny(
                principal, "background_artifact_invalid",
                "A valid immutable artifact id is required.")
        artifact = await self.store.get("artifacts", artifact_id)
        if (not artifact
                or artifact.get("workspace_id", artifact.get("founder_id"))
                != principal.workspace_id
                or artifact.get("session_id") != session_id):
            return await self._deny(
                principal, "background_artifact_not_found",
                "Artifact does not exist.")
        sha = str(artifact.get("sha256") or "")
        version = str(artifact.get("index_generation") or "")
        if (artifact.get("status") not in _ALLOWED_ARTIFACT_STATUS
                or not _HASH.fullmatch(sha)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,159}", version)
                or int(artifact.get("size_bytes") or 0) < 1
                or int(artifact.get("size_bytes") or 0)
                > int(FOUNDATION_TEMPLATE.budgets["max_artifact_bytes"])
                or int(artifact.get("chunk_count") or 0)
                > int(FOUNDATION_TEMPLATE.budgets["max_artifact_chunks"])):
            return await self._deny(
                principal, "background_artifact_not_ready",
                "Artifact is not ready within the pilot limits.")
        request = BackgroundJobRequest(
            client_request_id=client_request_id,
            template_id=FOUNDATION_TEMPLATE.template_id,
            template_version=FOUNDATION_TEMPLATE.version,
            objective_kind=FOUNDATION_TEMPLATE.objective_kind,
            objective_summary="Create a deterministic evidence inventory for one authorized artifact.",
            origin_message_id=f"pilot:{client_request_id}",
            origin_session_id=session_id,
            input_refs=(BackgroundInputRef(
                kind="WORKSPACE_ARTIFACT", ref_id=artifact_id,
                version=version, content_hash=f"sha256:{sha}"),),
            confirmed_negative_constraints=("CITATIONS_REQUIRED",),
        )
        accepted = await BackgroundWorkService(
            self.store, templates=enabled_foundation_templates(),
            admission_enabled=True).accept(
                principal=principal, request=request)
        if accepted.get("error"):
            return await self._deny(
                principal, str(accepted.get("error_code") or "admission_failed"),
                str(accepted.get("message") or "Pilot admission failed."))
        outbox_id = stable_id(
            "cmdoutbox", str(accepted["command_id"]), "dispatch")
        dispatched = await self.dispatcher.dispatch(outbox_id)
        await _audit(
            self.store, workspace_id=principal.workspace_id,
            actor_id=principal.actor_id, action="background_pilot.accept",
            target=str(accepted["run_id"]),
            result=("accepted" if not dispatched.get("error")
                    else "accepted_dispatch_pending"),
            code=str(dispatched.get("error_code") or ""))
        latency = max(0.0, time.perf_counter() - started_at)
        background_pilot_metrics.record(
            "background_pilot_admission",
            status=("dispatched" if not dispatched.get("error")
                    else "dispatch_pending"),
            error_code=str(dispatched.get("error_code") or ""),
            template_id=FOUNDATION_TEMPLATE.template_id,
            latency_ms=int(latency * 1000),
            duplicate=bool(accepted.get("duplicate")))
        from services.platform_operations import PlatformOperationsService

        await PlatformOperationsService(self.store).record_slo_observation(
            workspace_id=principal.workspace_id,
            objective_id="background_pilot_start",
            idempotency_key=str(accepted["run_id"]),
            observed_seconds=latency,
            dependency_success=not dispatched.get("error"))
        return {
            **accepted,
            "dispatch_status": (
                "DISPATCHED" if not dispatched.get("error") else "PENDING"),
            "dispatch_error_code": dispatched.get("error_code"),
            "location": f"/api/v1/background-pilot/jobs/{accepted['run_id']}",
        }

    async def list_jobs(self, *, principal: ActorPrincipal,
                        session_id: str = "") -> dict[str, Any]:
        if (principal.principal_kind != "INTERACTIVE"
                or principal.role is not WorkspaceRole.FOUNDER):
            return _error(
                "interactive_founder_required",
                "A signed-in interactive founder is required.")
        if session_id and not _OPAQUE_ID.fullmatch(session_id):
            return _error("background_request_invalid", "Session id is invalid.")
        gate = self.flags.admission_gate(principal.workspace_id)
        result = await BackgroundWorkService(self.store).list_jobs(
            principal=principal, limit=50)
        jobs = []
        for run in result.get("jobs", []):
            if session_id and run.get("origin_session_id") != session_id:
                continue
            jobs.append(await self._project(run))
        return {"status": "success", "enabled": not gate.get("error"),
                "kill_switch_active": self.flags.kill_switch_active,
                "jobs": jobs}

    async def get_job(self, *, principal: ActorPrincipal,
                      run_id: str) -> dict[str, Any]:
        if (principal.principal_kind != "INTERACTIVE"
                or principal.role is not WorkspaceRole.FOUNDER):
            return _error(
                "interactive_founder_required",
                "A signed-in interactive founder is required.")
        if not _OPAQUE_ID.fullmatch(run_id):
            return _error("background_job_not_found", "Background job not found.")
        visible = await BackgroundWorkService(self.store).get_job(
            principal=principal, run_id=run_id)
        if visible.get("error"):
            return visible
        return {"status": "success", "job": await self._project(visible["job"])}

    async def timeline(self, *, principal: ActorPrincipal,
                       run_id: str) -> dict[str, Any]:
        if (principal.principal_kind != "INTERACTIVE"
                or principal.role is not WorkspaceRole.FOUNDER):
            return _error(
                "interactive_founder_required",
                "A signed-in interactive founder is required.")
        if not _OPAQUE_ID.fullmatch(run_id):
            return _error("background_job_not_found", "Background job not found.")
        visible = await BackgroundWorkService(self.store).get_job(
            principal=principal, run_id=run_id)
        if visible.get("error"):
            return visible
        events = await self.store.list(
            "run_events", filters={"run_id": run_id},
            order_by="sequence", limit=100)
        messages = []
        seen: set[int] = set()
        for event in events:
            sequence = int(event.get("sequence") or 0)
            contract = _CAPTION_BY_EVENT.get(str(event.get("event_kind") or ""))
            if not contract or sequence < 1 or sequence in seen:
                continue
            seen.add(sequence)
            messages.append({
                "sequence": sequence,
                "event_id": event.get("event_id"),
                "runtime_status": contract[0],
                "caption": contract[1],
                "occurred_at": event.get("occurred_at"),
            })
        messages.sort(key=lambda item: item["sequence"])
        return {"status": "success", "run_id": run_id,
                "session_id": visible["job"].get("origin_session_id"),
                "messages": messages}

    async def cancel(self, *, principal: ActorPrincipal, run_id: str,
                     reason: str) -> dict[str, Any]:
        if (principal.principal_kind != "INTERACTIVE"
                or principal.role is not WorkspaceRole.FOUNDER):
            return _error(
                "interactive_founder_required",
                "A signed-in interactive founder is required.")
        if not _OPAQUE_ID.fullmatch(run_id):
            return _error("background_job_not_found", "Background job not found.")
        visible = await BackgroundWorkService(self.store).get_job(
            principal=principal, run_id=run_id)
        if visible.get("error"):
            return visible
        result = await WorkflowRuntime(self.store).cancel_run(
            run_id, actor_id=principal.actor_id,
            reason=(" ".join(reason.split()) or "Founder cancelled")[:200])
        if not result.get("error"):
            await self._release_capacity(run_id)
        await _audit(
            self.store, workspace_id=principal.workspace_id,
            actor_id=principal.actor_id, action="background_pilot.cancel",
            target=run_id, result=(
                "cancelled" if not result.get("error") else "denied"),
            code=str(result.get("error_code") or ""))
        return result

    async def _release_capacity(self, run_id: str) -> None:
        run = await self.store.get("workflow_runs", run_id)
        if not run:
            return
        capacity_id = stable_id(
            "bgcapacity", str(run["workspace_id"]),
            str(run.get("originating_actor_id") or ""),
            FOUNDATION_TEMPLATE.template_id)
        capacity = await self.store.get("background_pilot_capacity", capacity_id)
        if capacity and capacity.get("active_run_id") == run_id:
            await self.store.compare_and_set(
                "background_pilot_capacity", capacity_id,
                int(capacity["version"]), {
                    "status": "RELEASED", "active_run_id": None,
                    "released_at": utc_now(), "updated_at": utc_now(),
                })

    async def _project(self, run: dict[str, Any]) -> dict[str, Any]:
        status = str(run.get("runtime_status") or "QUEUED")
        fallback_event_kind = {
            "QUEUED": "RUN_CREATED", "RUNNING": "STEP_STARTED",
            "SUCCEEDED": "RUN_SUCCEEDED", "FAILED": "RUN_FAILED",
            "CANCELLING": "RUN_CANCELLING", "CANCELLED": "RUN_CANCELLED",
        }.get(status, "RUN_CREATED")
        event_kind = fallback_event_kind
        caption_sequence = 0
        events = await self.store.list(
            "run_events", filters={"run_id": run["run_id"]},
            order_by="sequence", descending=True, limit=10)
        for event in events:
            candidate = str(event.get("event_kind") or "")
            if candidate in _CAPTION_BY_EVENT:
                event_kind = candidate
                caption_sequence = int(event.get("sequence") or 0)
                break
        caption = _CAPTION_BY_EVENT[event_kind][1]
        output = None
        output_ref = str(run.get("output_manifest_ref") or "")
        if output_ref.startswith("artifacts/"):
            row = await self.store.get("artifacts", output_ref.split("/", 1)[1])
            if row and row.get("subject_id") == run.get("subject_id"):
                output = {
                    "output_id": row.get("artifact_id"),
                    "chunk_count": row.get("chunk_count"),
                    "word_count": row.get("word_count"),
                    "citation_count": row.get("citation_count"),
                    "content_hash": row.get("content_hash"),
                }
        return {
            "job_id": run["run_id"], "run_id": run["run_id"],
            "session_id": run.get("origin_session_id"),
            "template_id": run.get("job_template_id"),
            "runtime_status": status, "caption": caption,
            "caption_sequence": caption_sequence,
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "completed_at": run.get("completed_at"),
            "safe_error_code": run.get("last_safe_error_code"),
            "output": output,
            "version": run.get("version"),
        }


class BackgroundPilotDispatcher:
    """Queue only the exact pilot step with an opaque deterministic task."""

    def __init__(self, store: DurableStore | None = None, *,
                 flags: BackgroundPilotFlags | None = None,
                 enqueue_fn: Callable[..., dict[str, Any]] | None = None):
        self.store = store or production_store()
        self.flags = flags or BackgroundPilotFlags.from_env()
        if enqueue_fn is None:
            from services import task_queue

            enqueue_fn = task_queue.enqueue
        self.enqueue_fn = enqueue_fn

    async def dispatch(self, outbox_id: str) -> dict[str, Any]:
        outbox = await self.store.get("command_outbox", outbox_id)
        if not outbox:
            return _error("background_outbox_not_found", "Dispatch intent does not exist.")
        if outbox.get("status") == "DELIVERED":
            return {"status": "success", "duplicate": True, "outbox": outbox}
        receipt = await self.store.get(
            "command_receipts", str(outbox.get("command_id") or ""))
        run = await self.store.get(
            "workflow_runs", str(outbox.get("run_id") or ""))
        if (outbox.get("status") != "PENDING"
                or outbox.get("command_type") != "background_job.create"
                or outbox.get("visibility_scope") != "ACTOR_PRIVATE"
                or not receipt or receipt.get("status") != "ACCEPTED"
                or not run or run.get("job_template_id")
                != FOUNDATION_TEMPLATE.template_id
                or run.get("background_gate_ceiling") != "GATE_C_FOUNDER_PILOT"
                or run.get("approval_authority") != "NONE"
                or run.get("effect_authority") != "NONE"
                or run.get("external_read_authority") != "NONE"
                or run.get("memory_write_authority") != "NONE"):
            return _error(
                "background_dispatch_authority_invalid",
                "Pilot dispatch authority is invalid.")
        gate = self.flags.execution_gate(str(run["workspace_id"]))
        if gate.get("error"):
            background_pilot_metrics.record(
                "background_pilot_dispatch_blocked", status="blocked",
                error_code=str(gate.get("error_code") or ""),
                kill_switch_active=self.flags.kill_switch_active)
            return gate
        step = await WorkflowRuntime(self.store).create_step(
            run["run_id"], step_key="analyze_artifact",
            idempotency_key=f"background-artifact:{run['run_id']}")
        if step.get("error"):
            return step
        path = "/tasks/background-artifact-pilot"
        base_url = os.environ.get("AGENT_BASE_URL", "").rstrip("/")
        queue_name = os.environ.get(
            "BACKGROUND_ARTIFACT_PILOT_QUEUE",
            "co-founder-background-pilot")
        if queue_name not in {
                "co-founder-background-pilot",
                "co-founder-background-pilot-real",
                "co-founder-background-pilot-gate-e"}:
            return _error(
                "background_dispatch_queue_invalid",
                "Pilot dispatch queue is not registered.")
        queued = await asyncio.to_thread(
            self.enqueue_fn, path,
            {"workspace_id": run["workspace_id"], "run_id": run["run_id"],
             "step_id": step["step_id"]},
            f"background-artifact:{run['run_id']}:{step['step_id']}",
            queue_name=queue_name,
            audience=f"{base_url}{path}" if base_url else None)
        if queued.get("error"):
            background_pilot_metrics.record(
                "background_pilot_dispatch", status="pending",
                error_code="background_dispatch_failed")
            return _error(
                "background_dispatch_failed",
                "Accepted pilot work remains queued.", retryable=True)
        transitioned = await CommandService(self.store).transition(
            workspace_id=str(run["workspace_id"]),
            command_id=str(receipt["command_id"]),
            expected_version=int(receipt["version"]),
            status="DISPATCHED", run_id=str(run["run_id"]))
        if transitioned.get("error"):
            latest = await self.store.get(
                "command_receipts", str(receipt["command_id"]))
            if latest and latest.get("status") == "DISPATCHED":
                return {"status": "success", "duplicate": True,
                        "command": latest, "step": step}
            return transitioned
        background_pilot_metrics.record(
            "background_pilot_dispatch", status="dispatched",
            duplicate=False, template_id=FOUNDATION_TEMPLATE.template_id)
        return {"status": "success", "duplicate": False,
                "command": transitioned, "step": step}


class BackgroundPilotExecutor:
    """Claim, inspect, and atomically commit one bounded deterministic output."""

    def __init__(self, store: DurableStore | None = None, *,
                 flags: BackgroundPilotFlags | None = None,
                 inventory_port: ArtifactInventoryPort | None = None,
                 timeout_seconds: int = 25):
        self.store = store or production_store()
        self.flags = flags or BackgroundPilotFlags.from_env()
        self.inventory_port = inventory_port or StoredArtifactInventoryPort(
            self.store)
        self.timeout_seconds = max(1, min(timeout_seconds, 25))

    async def execute(self, *, workspace_id: str, run_id: str,
                      step_id: str, workload: dict[str, Any]) -> dict[str, Any]:
        gate = self.flags.execution_gate(workspace_id)
        if gate.get("error"):
            background_pilot_metrics.record(
                "background_pilot_execution_blocked", status="blocked",
                error_code=str(gate.get("error_code") or ""),
                kill_switch_active=self.flags.kill_switch_active)
            await _audit(
                self.store, workspace_id=workspace_id,
                actor_id="workload:background-pilot",
                action="background_pilot.execution", target=run_id,
                result="blocked", code=str(gate.get("error_code") or ""))
            return gate
        run = await self.store.get("workflow_runs", run_id)
        step = await self.store.get("workflow_steps", step_id)
        if (not run or run.get("workspace_id") != workspace_id
                or run.get("job_template_id") != FOUNDATION_TEMPLATE.template_id
                or run.get("visibility_scope") != "ACTOR_PRIVATE"
                or run.get("background_gate_ceiling") != "GATE_C_FOUNDER_PILOT"
                or run.get("approval_authority") != "NONE"
                or run.get("effect_authority") != "NONE"
                or run.get("external_read_authority") != "NONE"
                or run.get("memory_write_authority") != "NONE"
                or not step or step.get("run_id") != run_id
                or step.get("step_key") != "analyze_artifact"
                or step.get("capability_id") != "background.artifact.inspect"):
            return _error(
                "background_execution_authority_invalid",
                "Pilot execution authority is invalid.")
        if str(run.get("runtime_status") or "") in _TERMINAL:
            return {"status": "success", "duplicate": True,
                    "runtime_status": run.get("runtime_status")}
        owner = f"background-pilot:{str(workload.get('delivery_id') or 'delivery')[:80]}"
        claimed = await WorkflowRuntime(self.store).claim_step(
            step_id, lease_owner=owner, lease_seconds=30,
            workload={key: str(workload.get(key) or "")[:240]
                      for key in ("principal_kind", "service_account",
                                  "audience", "delivery_id")})
        if claimed.get("error"):
            return claimed
        generation = int(claimed["attempt_generation"])
        background_pilot_metrics.record(
            "background_pilot_attempt", status="running",
            attempt=generation, runtime_status="RUNNING")
        progress = await WorkflowRuntime(self.store).append_event(
            run_id, event_kind="STEP_STARTED",
            idempotency_key=f"pilot-step-start:{step_id}:{generation}",
            safe_payload={"step_id": step_id, "phase": "ARTIFACT_EVIDENCE_INVENTORY"},
            workload=workload)
        if progress.get("error"):
            return await self._fail(
                run_id=run_id, step_id=step_id, owner=owner,
                generation=generation, error_code="transient_store_error",
                retryable=True)
        manifest = await self.store.get(
            "artifacts", str(run.get("domain_ref") or ""))
        input_manifest = await self.store.get(
            "artifacts", str(run.get("input_manifest_ref") or "").split("/")[-1])
        if (not input_manifest or input_manifest.get("content_hash")
                != run.get("input_manifest_hash")
                or len(input_manifest.get("source_refs") or []) != 1):
            return await self._fail(
                run_id=run_id, step_id=step_id, owner=owner,
                generation=generation, error_code="input_stale",
                retryable=False)
        source = input_manifest["source_refs"][0]
        del manifest
        try:
            inspected = await asyncio.wait_for(
                self.inventory_port.inspect(
                    workspace_id=workspace_id,
                    actor_id=str(run.get("originating_actor_id") or ""),
                    session_id=str(run.get("origin_session_id") or ""),
                    artifact_id=str(source["ref_id"]),
                    expected_version=str(source["version"]),
                    expected_hash=str(source["content_hash"]),
                    max_bytes=int(run["budgets"]["max_artifact_bytes"]),
                    max_chunks=int(run["budgets"]["max_artifact_chunks"]),
                    max_output_bytes=int(
                        run["budgets"]["max_output_bytes"])),
                timeout=self.timeout_seconds)
        except TimeoutError:
            inspected = _error(
                "retryable_dependency", "Artifact inspection timed out.",
                retryable=True)
        except Exception:
            inspected = _error(
                "retryable_dependency", "Artifact inspection failed safely.",
                retryable=True)
        if inspected.get("error"):
            return await self._fail(
                run_id=run_id, step_id=step_id, owner=owner,
                generation=generation,
                error_code=str(inspected.get("error_code") or "validation_failed"),
                retryable=bool(inspected.get("retryable")))
        return await self._commit(
            run_id=run_id, step_id=step_id, owner=owner,
            generation=generation, inventory=inspected["inventory"])

    async def _commit(self, *, run_id: str, step_id: str, owner: str,
                      generation: int,
                      inventory: dict[str, Any]) -> dict[str, Any]:
        run = await self.store.get("workflow_runs", run_id)
        step = await self.store.get("workflow_steps", step_id)
        attempt_id = stable_id("attempt", step_id, str(generation))
        attempt = await self.store.get("step_attempts", attempt_id)
        output_id = stable_id(
            "artifact", run_id, step_id,
            str(run.get("input_manifest_hash") or ""), "inventory")
        existing_output = await self.store.get("artifacts", output_id)
        if run and run.get("runtime_status") == "SUCCEEDED":
            if (existing_output and existing_output.get("content_hash")
                    == inventory.get("content_hash")):
                return {"status": "success", "duplicate": True,
                        "run_id": run_id, "output_id": output_id}
            return _error("idempotency_conflict", "Stored output hash conflicts.")
        expiry = _parse_time((step or {}).get("lease_expires_at"))
        if (not run or not step or not attempt
                or step.get("status") != "RUNNING"
                or step.get("lease_owner") != owner
                or int(step.get("attempt_generation") or 0) != generation
                or not expiry or expiry <= datetime.now(timezone.utc)
                or int(run.get("cancellation_generation") or 0)
                != int(step.get("observed_cancellation_generation") or 0)
                or run.get("runtime_status") in {"CANCELLING", "CANCELLED"}):
            return _error("lease_lost", "Pilot lease or cancellation fence changed.")
        current_source = await self.store.get(
            "artifacts", str(inventory["artifact_id"]))
        if (not current_source
                or current_source.get("workspace_id",
                                      current_source.get("founder_id"))
                != run.get("workspace_id")
                or str(current_source.get("index_generation") or "")
                != inventory.get("artifact_version")
                or f"sha256:{current_source.get('sha256', '')}"
                != inventory.get("artifact_sha256")):
            return await self._fail(
                run_id=run_id, step_id=step_id, owner=owner,
                generation=generation, error_code="input_stale",
                retryable=False)
        now = utc_now()
        sequence = int(run.get("next_event_sequence") or 1)
        event_key = f"pilot-succeeded:{step_id}:{generation}"
        event_id = stable_id("evt", run_id, event_key)
        output = {
            "schema_version": 1, "artifact_id": output_id,
            "artifact_kind": "BACKGROUND_ARTIFACT_INVENTORY",
            "workspace_id": run["workspace_id"],
            "founder_id": run["workspace_id"],
            "actor_id": run.get("originating_actor_id"),
            "subject_kind": "ACTOR", "subject_id": run.get("subject_id"),
            "visibility_scope": "ACTOR_PRIVATE",
            "source_refs": [{
                "kind": "WORKSPACE_ARTIFACT",
                "ref_id": inventory["artifact_id"],
                "version": inventory["artifact_version"],
                "content_hash": inventory["artifact_sha256"],
            }],
            "chunk_count": int(inventory["chunk_count"]),
            "word_count": int(inventory["word_count"]),
            "character_count": int(inventory["character_count"]),
            "citation_count": len(inventory.get("citations") or []),
            "locator_counts": dict(inventory.get("locator_counts") or {}),
            "citations": list(inventory.get("citations") or []),
            "content_hash": inventory["content_hash"],
            "status": "IMMUTABLE", "created_at": now, "version": 1,
        }
        event = {
            "schema_version": 2, "event_id": event_id, "run_id": run_id,
            "workspace_id": run["workspace_id"], "journey_id": run["journey_id"],
            "sequence": sequence, "event_kind": "RUN_SUCCEEDED",
            "event_schema_version": 1, "reducer_version": 1,
            "idempotency_key": event_key,
            "safe_payload": {"output_id": output_id,
                             "completion_contract_id":
                                 "background.artifact_inventory.v1"},
            "actor_id": None, "workload_principal": None,
            "causation_event_id": None, "occurred_at": now,
            "provenance": run.get("provenance") or {}, "version": 1,
        }
        mutations: list[AtomicMutation] = [
            AtomicMutation(
                "workflow_runs", run_id, int(run["version"]),
                updates={"runtime_status": RuntimeStatus.SUCCEEDED.value,
                         "output_manifest_ref": f"artifacts/{output_id}",
                         "result_ref": f"artifacts/{output_id}",
                         "next_event_sequence": sequence + 1,
                         "updated_at": now, "completed_at": now}),
            AtomicMutation(
                "workflow_steps", step_id, int(step["version"]),
                updates={"status": "COMPLETE",
                         "result_ref": f"artifacts/{output_id}",
                         "lease_owner": None, "lease_expires_at": None,
                         "updated_at": now, "completed_at": now}),
            AtomicMutation(
                "step_attempts", attempt_id, int(attempt["version"]),
                updates={"status": "COMPLETE",
                         "result_ref": f"artifacts/{output_id}",
                         "completed_at": now}),
            AtomicMutation("artifacts", output_id, None, record=output),
            AtomicMutation("run_events", event_id, None, record=event),
        ]
        capacity_id = stable_id(
            "bgcapacity", str(run["workspace_id"]),
            str(run.get("originating_actor_id") or ""),
            FOUNDATION_TEMPLATE.template_id)
        capacity = await self.store.get("background_pilot_capacity", capacity_id)
        if capacity and capacity.get("active_run_id") == run_id:
            mutations.append(AtomicMutation(
                "background_pilot_capacity", capacity_id,
                int(capacity["version"]),
                updates={"status": "RELEASED", "active_run_id": None,
                         "released_at": now, "updated_at": now}))
        committed = await self.store.atomic_compare_and_set(tuple(mutations))
        if not committed:
            current = await self.store.get("workflow_runs", run_id)
            current_output = await self.store.get("artifacts", output_id)
            if (current and current.get("runtime_status") == "SUCCEEDED"
                    and current_output
                    and current_output.get("content_hash")
                    == inventory.get("content_hash")):
                return {"status": "success", "duplicate": True,
                        "run_id": run_id, "output_id": output_id}
            return _error(
                "concurrency_conflict",
                "Pilot completion changed concurrently.", retryable=True)
        await _audit(
            self.store, workspace_id=str(run["workspace_id"]),
            actor_id=str(run.get("originating_actor_id") or ""),
            action="background_pilot.complete", target=run_id,
            result="success")
        created = _parse_time(run.get("created_at"))
        elapsed = max(
            0.0, (datetime.now(timezone.utc) - created).total_seconds()
        ) if created else 121.0
        background_pilot_metrics.record(
            "background_pilot_complete", status="success",
            runtime_status="SUCCEEDED", attempt=generation,
            latency_ms=int(elapsed * 1000))
        from services.platform_operations import PlatformOperationsService

        await PlatformOperationsService(self.store).record_slo_observation(
            workspace_id=str(run["workspace_id"]),
            objective_id="background_pilot_completion",
            idempotency_key=run_id, observed_seconds=elapsed,
            dependency_success=True)
        return {"status": "success", "duplicate": False,
                "run_id": run_id, "output_id": output_id,
                "runtime_status": "SUCCEEDED"}

    async def _fail(self, *, run_id: str, step_id: str, owner: str,
                    generation: int, error_code: str,
                    retryable: bool) -> dict[str, Any]:
        run = await self.store.get("workflow_runs", run_id)
        step = await self.store.get("workflow_steps", step_id)
        attempt_id = stable_id("attempt", step_id, str(generation))
        attempt = await self.store.get("step_attempts", attempt_id)
        expiry = _parse_time((step or {}).get("lease_expires_at"))
        if (not run or not step or not attempt
                or step.get("status") != "RUNNING"
                or step.get("lease_owner") != owner
                or int(step.get("attempt_generation") or 0) != generation
                or not expiry or expiry <= datetime.now(timezone.utc)
                or int(run.get("cancellation_generation") or 0)
                != int(step.get("observed_cancellation_generation") or 0)
                or run.get("runtime_status") in {"CANCELLING", "CANCELLED"}):
            return _error("lease_lost", "Pilot attempt is no longer current.")
        max_attempts = int(FOUNDATION_TEMPLATE.max_attempts)
        will_retry = retryable and generation < max_attempts
        now = utc_now()
        sequence = int(run.get("next_event_sequence") or 1)
        event_kind = "STEP_FAILED" if will_retry else "RUN_FAILED"
        event_key = f"pilot-failed:{step_id}:{generation}:{error_code}"
        event_id = stable_id("evt", run_id, event_key)
        event = {
            "schema_version": 2, "event_id": event_id, "run_id": run_id,
            "workspace_id": run["workspace_id"], "journey_id": run["journey_id"],
            "sequence": sequence, "event_kind": event_kind,
            "event_schema_version": 1, "reducer_version": 1,
            "idempotency_key": event_key,
            "safe_payload": {"error_code": error_code,
                             "retryable": will_retry},
            "actor_id": None, "workload_principal": None,
            "causation_event_id": None, "occurred_at": now,
            "provenance": run.get("provenance") or {}, "version": 1,
        }
        mutations: list[AtomicMutation] = [
            AtomicMutation(
                "workflow_runs", run_id, int(run["version"]),
                updates={"runtime_status": (
                    RuntimeStatus.RUNNING.value if will_retry
                    else RuntimeStatus.FAILED.value),
                         "last_safe_error_code": error_code,
                         "next_event_sequence": sequence + 1,
                         "updated_at": now,
                         **({"completed_at": now} if not will_retry else {})}),
            AtomicMutation(
                "workflow_steps", step_id, int(step["version"]),
                updates={"status": "READY" if will_retry else "FAILED",
                         "lease_owner": None, "lease_expires_at": None,
                         "last_safe_error_code": error_code,
                         "updated_at": now}),
            AtomicMutation(
                "step_attempts", attempt_id, int(attempt["version"]),
                updates={"status": "FAILED", "error_code": error_code,
                         "completed_at": now}),
            AtomicMutation("run_events", event_id, None, record=event),
        ]
        if not will_retry:
            capacity_id = stable_id(
                "bgcapacity", str(run["workspace_id"]),
                str(run.get("originating_actor_id") or ""),
                FOUNDATION_TEMPLATE.template_id)
            capacity = await self.store.get(
                "background_pilot_capacity", capacity_id)
            if capacity and capacity.get("active_run_id") == run_id:
                mutations.append(AtomicMutation(
                    "background_pilot_capacity", capacity_id,
                    int(capacity["version"]),
                    updates={"status": "RELEASED", "active_run_id": None,
                             "released_at": now, "updated_at": now}))
        committed = await self.store.atomic_compare_and_set(tuple(mutations))
        if not committed:
            return _error(
                "concurrency_conflict",
                "Pilot failure disposition changed concurrently.",
                retryable=True)
        await _audit(
            self.store, workspace_id=str(run["workspace_id"]),
            actor_id=str(run.get("originating_actor_id") or ""),
            action="background_pilot.attempt", target=run_id,
            result="retry" if will_retry else "failed", code=error_code)
        background_pilot_metrics.record(
            "background_pilot_attempt",
            status="retry" if will_retry else "failed",
            error_code=error_code, attempt=generation,
            runtime_status="RUNNING" if will_retry else "FAILED")
        return _error(
            error_code,
            "Pilot attempt will retry." if will_retry
            else "Pilot stopped safely. Nothing was sent.",
            retryable=will_retry)
