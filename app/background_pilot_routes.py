"""HTTP boundary for the single founder artifact-analysis background pilot."""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.background_pilot import (
    BackgroundArtifactPilot,
    BackgroundPilotExecutor,
)
from services.command_service import CommandService, transport_status
from services.durable_store import DurableStore, production_store
from services.workload_identity import verify_request

PrincipalResolver = Callable[[Request], Awaitable[ActorPrincipal | dict]]
SessionResolver = Callable[[str, str], Awaitable[bool]]
ArtifactAuthorizer = Callable[[ActorPrincipal, str, str], Awaitable[bool]]
StoreFactory = Callable[[], DurableStore]
ExecutorFactory = Callable[[DurableStore], BackgroundPilotExecutor]


class StartArtifactPilotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str = Field(
        min_length=3, max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    client_request_id: str = Field(
        min_length=8, max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class CancelArtifactPilotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    client_request_id: str = Field(
        min_length=8, max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    reason: str = Field(default="Founder cancelled", max_length=200)


class ExecuteArtifactPilotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workspace_id: str = Field(
        min_length=3, max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
    run_id: str = Field(
        min_length=3, max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
    step_id: str = Field(
        min_length=3, max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")


def _status(result: dict, *, accepted: bool = False) -> int:
    code = str(result.get("error_code") or "")
    if accepted:
        return 202
    if code in {"background_job_not_found", "background_artifact_not_found"}:
        return 404
    if code in {"idempotency_conflict", "background_concurrency_exhausted",
                "background_rate_limited", "version_conflict", "terminal_run"}:
        return 409
    if code in {"background_dispatch_failed", "retryable_dependency",
                "transient_store_error", "concurrency_conflict"}:
        return 503
    if result.get("error"):
        return 403 if code in {
            "interactive_founder_required", "background_pilot_workspace_denied",
            "background_pilot_disabled", "background_pilot_killed",
            "background_pilot_execution_disabled"} else 400
    return 200


def _founder_gate(principal: ActorPrincipal) -> dict | None:
    if (principal.principal_kind != "INTERACTIVE"
            or principal.role is not WorkspaceRole.FOUNDER):
        return {
            "status": "error", "error": True,
            "error_code": "interactive_founder_required",
            "message": "A signed-in interactive founder is required.",
        }
    return None


def register(app, *, principal_resolver: PrincipalResolver,
             session_resolver: SessionResolver,
             store_factory: StoreFactory | None = None,
             executor_factory: ExecutorFactory | None = None,
             artifact_authorizer: ArtifactAuthorizer | None = None) -> None:
    """Register one closed public command family and one private worker."""

    def selected_store() -> DurableStore:
        return store_factory() if store_factory else production_store()

    @app.post("/api/v1/background-pilot/artifact-analysis")
    async def start_artifact_analysis(
            request: Request, payload: StartArtifactPilotV1):
        principal = await principal_resolver(request)
        if isinstance(principal, dict):
            return JSONResponse(principal, status_code=401)
        if denied := _founder_gate(principal):
            return JSONResponse(denied, status_code=403)
        if request.headers.get("Idempotency-Key", "") != payload.client_request_id:
            return JSONResponse({
                "status": "error", "error": True,
                "error_code": "idempotency_key_required",
                "message": "Idempotency-Key must match client_request_id.",
            }, status_code=400)
        if not await session_resolver(
                principal.workspace_id, payload.session_id):
            return JSONResponse({"error": "not found"}, status_code=404)
        if artifact_authorizer and not await artifact_authorizer(
                principal, payload.session_id, payload.artifact_id):
            return JSONResponse({"error": "not found"}, status_code=404)
        result = await BackgroundArtifactPilot(selected_store()).start(
            principal=principal, session_id=payload.session_id,
            artifact_id=payload.artifact_id,
            client_request_id=payload.client_request_id)
        return JSONResponse(
            result, status_code=_status(
                result, accepted=not result.get("error")))

    @app.get("/api/v1/background-pilot/jobs")
    async def list_artifact_jobs(
            request: Request,
            session_id: str = Query(
                default="", max_length=160,
                pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")):
        principal = await principal_resolver(request)
        if isinstance(principal, dict):
            return JSONResponse(principal, status_code=401)
        if denied := _founder_gate(principal):
            return JSONResponse(denied, status_code=403)
        if session_id and not await session_resolver(
                principal.workspace_id, session_id):
            return JSONResponse({"error": "not found"}, status_code=404)
        result = await BackgroundArtifactPilot(selected_store()).list_jobs(
            principal=principal, session_id=session_id)
        return JSONResponse(result, status_code=_status(result))

    @app.get("/api/v1/background-pilot/jobs/{run_id}")
    async def get_artifact_job(request: Request, run_id: str):
        principal = await principal_resolver(request)
        if isinstance(principal, dict):
            return JSONResponse(principal, status_code=401)
        if denied := _founder_gate(principal):
            return JSONResponse(denied, status_code=403)
        result = await BackgroundArtifactPilot(selected_store()).get_job(
            principal=principal, run_id=run_id)
        return JSONResponse(result, status_code=_status(result))

    @app.get("/api/v1/background-pilot/jobs/{run_id}/timeline")
    async def get_artifact_job_timeline(request: Request, run_id: str):
        principal = await principal_resolver(request)
        if isinstance(principal, dict):
            return JSONResponse(principal, status_code=401)
        if denied := _founder_gate(principal):
            return JSONResponse(denied, status_code=403)
        result = await BackgroundArtifactPilot(selected_store()).timeline(
            principal=principal, run_id=run_id)
        return JSONResponse(result, status_code=_status(result))

    @app.post("/api/v1/background-pilot/jobs/{run_id}:cancel")
    async def cancel_artifact_job(
            request: Request, run_id: str, payload: CancelArtifactPilotV1):
        principal = await principal_resolver(request)
        if isinstance(principal, dict):
            return JSONResponse(principal, status_code=401)
        if denied := _founder_gate(principal):
            return JSONResponse(denied, status_code=403)
        if request.headers.get("Idempotency-Key", "") != payload.client_request_id:
            return JSONResponse({
                "status": "error", "error": True,
                "error_code": "idempotency_key_required",
                "message": "Idempotency-Key must match client_request_id.",
            }, status_code=400)
        store = selected_store()
        pilot = BackgroundArtifactPilot(store)
        visible = await pilot.get_job(principal=principal, run_id=run_id)
        if visible.get("error"):
            return JSONResponse(visible, status_code=_status(visible))
        expected = request.headers.get("If-Match", "").strip('"')
        if expected != str(visible["job"].get("version") or ""):
            return JSONResponse({
                "status": "error", "error": True,
                "error_code": "version_conflict",
                "message": "Job changed; reload before cancelling.",
            }, status_code=409)
        commands = CommandService(store)
        command = await commands.accept(
            principal=principal,
            client_request_id=payload.client_request_id,
            command_type="background_job.cancel",
            request={"run_id": run_id, "expected_version": int(expected),
                     "reason": payload.reason},
            run_id=run_id, visibility_scope="ACTOR_PRIVATE",
            subject_id=principal.actor_id)
        if command.get("error") or command.get("duplicate"):
            return JSONResponse(
                command, status_code=transport_status(command))
        result = await pilot.cancel(
            principal=principal, run_id=run_id, reason=payload.reason)
        terminal = await commands.transition(
            workspace_id=principal.workspace_id,
            command_id=command["command_id"],
            expected_version=int(command["version"]),
            status="COMPLETED" if not result.get("error") else "REJECTED",
            run_id=run_id,
            result_ref=({"run_id": run_id,
                         "runtime_status": result.get("runtime_status")}
                        if not result.get("error") else None),
            error_code=str(result.get("error_code") or ""))
        if terminal.get("error"):
            return JSONResponse(terminal, status_code=_status(terminal))
        return JSONResponse(
            terminal, status_code=200 if not result.get("error")
            else _status(result))

    @app.post("/tasks/background-artifact-pilot")
    async def execute_artifact_analysis(
            request: Request, payload: ExecuteArtifactPilotV1):
        route = "/tasks/background-artifact-pilot"
        workload = await verify_request(request, route)
        if isinstance(workload, dict):
            return JSONResponse(workload, status_code=401)
        store = selected_store()
        executor = (executor_factory(store) if executor_factory
                    else BackgroundPilotExecutor(store))
        result = await executor.execute(
                workspace_id=payload.workspace_id,
                run_id=payload.run_id, step_id=payload.step_id,
                workload=workload.audit_fields())
        # Cancellation/terminal duplicates acknowledge and stop redelivery.
        status = (503 if result.get("retryable") else
                  409 if result.get("error_code") == "lease_conflict" else 200)
        return JSONResponse(result, status_code=status)
