"""Founder and workload HTTP surfaces for Hiring H0–H4.

H4 live provider actions remain server-bound to a real advanced application and
one fresh, bounded Founder coordination consent. Every later provider effect is
durably receipt-backed and constrained to that candidate, thread and confirmed
availability. The separate H4S routes retain their synthetic-only contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any, Literal
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app import auth
from services import hiring_policy_service, task_queue, workload_identity
from services.actor_identity import (
    ActorPrincipal,
    authorize,
    resolve_actor_from_claims,
)
from services.durable_store import production_store
from services.hiring_approval_service import request_approval, resolve_approval
from services.hiring_contracts import (
    HumanDecisionInput,
    RoleContract,
    SyntheticFixtureMessage,
    stable_id,
    utc_now,
)
from services.hiring_coordination import HiringCoordinationService
from services.hiring_data_rights import HiringDataRightsService
from services.hiring_h4s_effects import H4SEffectService
from services.hiring_h4s_google import H4SGoogleEffectAdapter
from services.hiring_h4s_reply import H4SReplyService
from services.hiring_identity_vault import CandidateIdentityVault, fixture_key_wrapper
from services.hiring_mailbox import HiringMailboxService
from services.hiring_public_intake import (
    MAX_RESUME_BYTES,
    HiringPublicIntakeService,
    public_intake_configured,
)
from services.hiring_role_draft import build_contract
from services.hiring_run_answer import (
    HiringCandidateConversationService,
    HiringRunAnswerService,
)
from services.hiring_sandbox import HiringSandboxService
from services.hiring_service import HiringService, candidate_evidence_status
from services.internal_controlled_demo import InternalControlledDemoService
from services.internal_controlled_demo_effects import InternalDemoEffectService
from services.internal_controlled_demo_intake import InternalDemoInboxImportService


class ClosedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SyntheticGuardRequest(ClosedRequest):
    synthetic: Literal[True]
    synthetic_namespace: str = Field(pattern=r"^synthetic_hiring_[a-z0-9_-]{1,80}$")
    fixture_id: str = Field(pattern=r"^[a-z][a-z0-9_:-]{2,127}$")


class CreateRoleRequest(SyntheticGuardRequest):
    client_request_id: str
    contract: RoleContract


class PolicyRequest(ClosedRequest):
    client_request_id: str
    change_reason: str = Field(max_length=1000)
    contract: RoleContract
    role_description: dict[str, Any] | None = None


class RoleDescriptionDraftRequest(ClosedRequest):
    client_request_id: str = Field(min_length=3, max_length=200)
    expected_role_version: int = Field(ge=1)
    change_reason: str = Field(min_length=1, max_length=1000)
    purpose: str = Field(min_length=1, max_length=3000)
    responsibilities: list[str] = Field(min_length=1, max_length=12)
    success_outcomes: list[str] = Field(min_length=1, max_length=12)
    required_qualifications: list[str] = Field(min_length=1, max_length=12)
    preferred_qualifications: list[str] = Field(default_factory=list, max_length=12)
    relevant_experience: list[str] = Field(min_length=1, max_length=12)
    location: str = Field(min_length=1, max_length=120)
    work_arrangement: str = Field(min_length=1, max_length=120)
    employment_type: str = Field(min_length=1, max_length=120)
    compensation: str = Field(default="", max_length=500)
    benefits: list[str] = Field(default_factory=list, max_length=12)
    hiring_process: list[str] = Field(min_length=1, max_length=12)
    application_instructions: str = Field(min_length=1, max_length=2000)
    equal_opportunity_statement: str = Field(default="", max_length=2000)
    accessibility_statement: str = Field(default="", max_length=2000)


class ApprovalRequest(ClosedRequest):
    client_request_id: str
    run_id: str
    policy_version_id: str
    action_kind: str
    exact_action: dict[str, Any]


class ResolveApprovalRequest(ClosedRequest):
    decision: Literal["GRANT", "DENY"]
    require_fresh: bool = False


class ActivatePolicyRequest(ClosedRequest):
    approval_id: str
    expected_role_version: int = Field(ge=1)


class PublicationRequest(ClosedRequest):
    destination: str
    public_url: str
    attestation: str
    expected_role_version: int = Field(ge=1)
    client_request_id: str


class PublishRoleRequest(ClosedRequest):
    expected_role_version: int = Field(ge=1)
    client_request_id: str = Field(min_length=3, max_length=200)


class BindingRequest(SyntheticGuardRequest):
    connection_id: str
    provider_route_id: str
    provider_label_id: str
    expected_role_version: int = Field(ge=1)


class ProbeRequest(ClosedRequest):
    fixture_message_id: str
    probe_kind: Literal["POSITIVE", "NEGATIVE_FORGED_HEADER"]
    expected_role_version: int = Field(ge=1)


class FixtureMessageRequest(SyntheticGuardRequest):
    message: SyntheticFixtureMessage


class FetchBatchRequest(SyntheticGuardRequest):
    connection_id: str
    old_cursor: str
    proposed_cursor: str
    message_ids: list[str] = Field(max_length=1000)
    batch_key: str
    mode: Literal["HISTORY", "EXPIRED_CURSOR_RECOVERY"] = "HISTORY"
    fully_paginated: bool = True


class ProcessBatchRequest(ClosedRequest):
    batch_id: str


class PrepareCandidateEvidenceRequest(ClosedRequest):
    application_id: str = Field(pattern=r"^candidateapp_[a-f0-9]{16,64}$")


class IdentityRevealRequest(ClosedRequest):
    client_request_id: str


class ResumeOpenRequest(ClosedRequest):
    client_request_id: str = Field(min_length=3, max_length=128)


class CandidateRequest(ClosedRequest):
    request_kind: Literal["WITHDRAWAL", "ACCOMMODATION", "HUMAN_CONTACT"]
    safe_note: str = Field(max_length=500)
    client_request_id: str
    expected_application_version: int = Field(ge=1)


class CandidateExportRequest(ClosedRequest):
    client_request_id: str = Field(min_length=3, max_length=128)


class CandidateDeletionRequest(ClosedRequest):
    execute: bool = False
    expected_inventory_hash: str = Field(default="", max_length=80)
    client_request_id: str = Field(min_length=3, max_length=128)


class CandidateLegalHoldRequest(ClosedRequest):
    active: bool
    reason_code: Literal["LITIGATION", "REGULATORY", "QUALIFIED_REVIEW"]
    expected_identity_version: int = Field(ge=1)
    client_request_id: str = Field(min_length=3, max_length=128)


class HiringContactRequest(ClosedRequest):
    client_request_id: str = Field(min_length=8, max_length=128)
    reply: bool = False
    copy_founder: bool = False
    duration_minutes: int = Field(default=60, ge=30, le=90)
    subject: str | None = Field(default=None, max_length=240)
    body: str | None = Field(default=None, max_length=12_000)
    availability_hash: str = Field(default="", max_length=80)
    preview_only: bool = False


class HiringInterviewRequest(ClosedRequest):
    client_request_id: str = Field(min_length=8, max_length=128)
    start: str = Field(default="", max_length=80)
    end: str = Field(default="", max_length=80)
    timezone: str = Field(default="Africa/Lagos", min_length=1, max_length=80)
    target_event_id: str = Field(default="", max_length=512)
    cancel: bool = False


class HiringEffectExecutionRequest(ClosedRequest):
    coordination_id: str = Field(min_length=3, max_length=128)
    approval_id: str = Field(default="", max_length=128)


class H4SConversationStartRequest(ClosedRequest):
    sandbox_run_id: str = Field(pattern=r"^hsr_[a-f0-9]{28}$")
    candidate_application_id: str = ""


class H4SConversationAnswerRequest(ClosedRequest):
    conversation_token: str = Field(min_length=20, max_length=4096)
    question: str = Field(min_length=1, max_length=2000)
    client_turn_id: str = Field(default="", max_length=128)


class H4SSandboxCreateRequest(SyntheticGuardRequest):
    role_id: str = Field(min_length=3, max_length=128)
    connector_binding_ids: list[str] = Field(min_length=1, max_length=10)
    client_request_id: str = Field(min_length=3, max_length=128)


class H4SDestinationRequest(ClosedRequest):
    destination_kind: Literal["TEST_CANDIDATE", "TEST_FOUNDER",
                              "TEST_CALENDAR_ATTENDEE"]
    client_request_id: str = Field(min_length=3, max_length=128)


class H4SConnectorBindingRequest(ClosedRequest):
    provider_kind: Literal["GMAIL_TEST", "CALENDAR_TEST"]
    client_request_id: str = Field(min_length=3, max_length=128)


class H4SEffectApprovalRequest(ClosedRequest):
    binding_id: str = Field(min_length=3, max_length=128)
    candidate_application_id: str = Field(min_length=3, max_length=128)
    destination_ids: list[str] = Field(min_length=1, max_length=20)
    action_kind: Literal["H4S_SEND_EMAIL", "H4S_CREATE_CALENDAR_EVENT",
                         "H4S_UPDATE_CALENDAR_EVENT", "H4S_CANCEL_CALENDAR_EVENT"]
    rendered_payload: dict[str, Any]
    client_request_id: str = Field(min_length=3, max_length=128)


class H4SEffectClaimRequest(ClosedRequest):
    approval_id: str = Field(min_length=3, max_length=128)
    binding_id: str = Field(min_length=3, max_length=128)
    candidate_application_id: str = Field(min_length=3, max_length=128)
    destination_ids: list[str] = Field(min_length=1, max_length=20)
    action_kind: Literal["H4S_SEND_EMAIL", "H4S_CREATE_CALENDAR_EVENT",
                         "H4S_UPDATE_CALENDAR_EVENT", "H4S_CANCEL_CALENDAR_EVENT"]
    rendered_payload: dict[str, Any]


class H4SReplyDeliveryRequest(ClosedRequest):
    """Task payload: identifiers only; Gmail metadata is fetched server-side."""

    sandbox_run_id: str = Field(pattern=r"^hsr_[a-f0-9]{28}$")
    binding_id: str = Field(min_length=3, max_length=128)
    provider_message_id: str = Field(min_length=3, max_length=512)


class InternalDemoRunRequest(ClosedRequest):
    client_request_id: str = Field(min_length=3, max_length=128)
    role_id: str = Field(min_length=3, max_length=128)
    ttl_minutes: int = Field(default=120, ge=5, le=240)


class InternalDemoApprovalRequest(ClosedRequest):
    action_kind: Literal["INTERNAL_DEMO_SEND_RECAP", "INTERNAL_DEMO_CREATE_CALENDAR_EVENT",
                         "INTERNAL_DEMO_UPDATE_CALENDAR_EVENT", "INTERNAL_DEMO_CANCEL_CALENDAR_EVENT"]
    client_request_id: str = Field(min_length=3, max_length=128)


class InternalDemoApprovalResolutionRequest(ClosedRequest):
    decision: Literal["GRANT", "DENY"]


class InternalDemoExecutionRequest(ClosedRequest):
    approval_id: str = Field(min_length=3, max_length=128)
    action_kind: Literal["INTERNAL_DEMO_SEND_RECAP", "INTERNAL_DEMO_CREATE_CALENDAR_EVENT",
                         "INTERNAL_DEMO_UPDATE_CALENDAR_EVENT", "INTERNAL_DEMO_CANCEL_CALENDAR_EVENT"]


class InternalDemoReconcileRequest(ClosedRequest):
    action_id: str = Field(min_length=3, max_length=128)


class InternalDemoResetRequest(ClosedRequest):
    acknowledge_provider_mail_retained: Literal[True]


class InternalDemoFixtureImportRequest(ClosedRequest):
    acknowledge_fixture_only: Literal[True]


def _guard(payload: SyntheticGuardRequest) -> dict[str, Any]:
    return {"synthetic": payload.synthetic,
            "synthetic_namespace": payload.synthetic_namespace,
            "fixture_id": payload.fixture_id}


def _synthetic_demo_allowed(payload: SyntheticGuardRequest) -> dict[str, Any]:
    """Fixture authority is deployment-owned, never granted by client markers."""
    if not _fixture_id_allowed(payload.fixture_id):
        return {"status": "error", "error": True,
                "error_code": "synthetic_fixture_not_authorized",
                "message": "This read-only test role is not enabled by deployment policy.",
                "http_status": 403}
    return {"status": "success"}


def _fixture_id_allowed(fixture_id: str) -> bool:
    allowed = {item.strip() for item in os.environ.get(
        "HIRING_SYNTHETIC_FIXTURE_IDS", "").split(",") if item.strip()}
    return (os.environ.get("HIRING_ENABLE_SYNTHETIC_DEMO") == "1"
            and fixture_id in allowed)


def _response(result: dict[str, Any]):
    if result.get("error"):
        from services.error_contracts import http_status

        return JSONResponse(result, status_code=http_status(result))
    return result


async def _actor(request: Request) -> ActorPrincipal | dict[str, Any]:
    return await resolve_actor_from_claims(auth.session_claims(request))


async def _founder_candidate_identity(
        principal: ActorPrincipal,
        application: dict[str, Any]) -> dict[str, Any] | None:
    """Decrypt a real public applicant's display identity for Hiring only."""
    if (application.get("source_kind") != "PUBLIC_FORM"
            or application.get("synthetic") is not False):
        return None
    revealed = await HiringPublicIntakeService(
        store=production_store()).reveal_restricted_identity(
            application=application, principal=principal,
            require_fresh=False)
    return (dict(revealed["identity"])
            if not revealed.get("error") else None)


async def _candidate_evidence_summary(
        application: dict[str, Any]) -> dict[str, int]:
    assessment_id = str(application.get("current_assessment_id") or "")
    assessment = (await production_store().get(
        "candidate_assessments", assessment_id) if assessment_id else None)
    counts = {"present": 0, "missing": 0, "unclear": 0,
              "contradicted": 0}
    for item in list((assessment or {}).get("criteria") or []):
        status = str(item.get("status") or "")
        if status == "SUPPORTED":
            counts["present"] += 1
        elif status == "UNKNOWN":
            counts["missing"] += 1
        elif status == "CONTRADICTED":
            counts["contradicted"] += 1
        else:
            counts["unclear"] += 1
    return counts


def _inbox_row_visible(principal: ActorPrincipal, row: dict[str, Any]) -> bool:
    """Defer inbox visibility to the same gate that guards the records.

    A row with no role_id is workspace-level founder triage.
    """
    role_id = str(row.get("role_id") or "")
    candidate_id = str(row.get("candidate_application_id") or "")
    if not role_id:
        return True
    operation = "read_candidate" if candidate_id else "read_role"
    return not authorize(principal, operation).get("error")


def _mutation_allowed(request: Request) -> dict[str, Any]:
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        return {"status": "error", "error": True,
                "error_code": "content_type_required",
                "message": "Hiring mutations require application/json.",
                "http_status": 415}
    if not auth.csrf_is_valid(request):
        return {"status": "error", "error": True, "error_code": "csrf_failed",
                "message": "Refresh the hiring surface and try again.",
                "http_status": 403}
    try:
        content_length = int(request.headers.get("content-length", "0") or 0)
    except ValueError:
        content_length = -1
    if content_length < 0 or content_length > 1_048_576:
        return {"status": "error", "error": True,
                "error_code": "request_too_large",
                "message": "Hiring request exceeds the 1 MB envelope limit.",
                "http_status": 413}
    return {"status": "success"}


def _services() -> tuple[HiringService, HiringMailboxService] | None:
    raw = os.environ.get("HIRING_SYNTHETIC_ENCRYPTION_KEY", "")
    if not raw:
        if os.environ.get("K_SERVICE"):
            return None
        raw = os.environ.get("APP_SESSION_SECRET", "local-hiring-fixture-only")
    key = hashlib.sha256(raw.encode()).digest()
    wrap, unwrap = fixture_key_wrapper(key)
    store = production_store()
    vault = CandidateIdentityVault(wrap_key=wrap, unwrap_key=unwrap,
                                   dedup_key=key, store=store)
    hiring = HiringService(store=store, identity_vault=vault)
    return hiring, HiringMailboxService(hiring, store=store)


def register(app: FastAPI) -> None:
    @app.get("/api/public/hiring/roles/{role_id}")
    async def public_open_role(role_id: str):
        """Receipt-backed, candidate-safe projection; no internal policy data."""
        services = _services()
        if not services:
            return JSONResponse(
                {"status": "error", "error": True,
                 "error_code": "open_role_unavailable",
                 "message": "Open roles are temporarily unavailable."},
                status_code=503)
        result = await services[0].get_public_role(role_id)
        if result.get("error"):
            return JSONResponse(result, status_code=404)
        return result

    @app.post("/api/public/hiring/roles/{role_id}/applications")
    async def submit_public_application(
            request: Request, role_id: str,
            email: str = Form(...), cover_note: str = Form(""),
            applicant_name: str = Form(...), privacy_consent: str = Form(...),
            intake_token: str = Form(...), client_request_id: str = Form(...),
            resume: UploadFile = File(...)):
        """Role-scoped candidate intake; never enables a provider connector.

        The service re-resolves the exact live role/policy/receipt and stores a
        restricted encrypted queue item. The public caller cannot select a
        workspace, candidate state, assessment, action, or policy.
        """
        try:
            content_length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_RESUME_BYTES + 256_000:
            return JSONResponse({
                "status": "error", "error": True,
                "error_code": "intake_request_too_large",
                "message": "The application exceeds the 5 MB resume limit."
            }, status_code=413)
        data = bytearray()
        while chunk := await resume.read(1024 * 1024):
            data.extend(chunk)
            if len(data) > MAX_RESUME_BYTES:
                return JSONResponse({
                    "status": "error", "error": True,
                    "error_code": "resume_size_invalid",
                    "message": "Resume files must be 5 MB or smaller."
                }, status_code=413)
        result = await HiringPublicIntakeService(
            store=production_store()).submit(
                role_id=role_id, intake_token=intake_token,
                client_request_id=client_request_id,
                applicant_name=applicant_name, email=email,
                cover_note=cover_note,
                consent_accepted=privacy_consent == "accepted",
                filename=resume.filename or "",
                content_type=resume.content_type or "application/octet-stream",
                resume_bytes=bytes(data))
        if result.get("error"):
            return JSONResponse(result, status_code=int(result.get(
                "http_status") or 400))
        dispatch_spec = dict(result.pop("_dispatch", {}) or {})
        application_id = str(dispatch_spec.get("application_id") or "")
        if application_id:
            dispatch = await asyncio.to_thread(
                task_queue.enqueue_hiring,
                "/tasks/hiring/prepare_candidate_evidence",
                {"application_id": application_id},
                str(dispatch_spec.get("dedupe_key") or
                    f"hiring-evidence:{application_id}"))
            current = await production_store().get(
                "candidate_applications", application_id)
            dispatch_ok = dispatch.get("status") == "success"
            if (not dispatch_ok and current
                    and current.get("current_assessment_id") is None
                    and current.get("processing_status") in {
                        "EVIDENCE_QUEUED", "EVIDENCE_PREPARATION_DELAYED"}):
                await production_store().compare_and_set(
                    "candidate_applications", application_id,
                    int(current["version"]), {
                        "evidence_dispatch_status": "DELAYED",
                        "evidence_dispatch_error_code": str(
                            dispatch.get("error_code") or
                            "delivery_unavailable")[:120],
                        "processing_status": "EVIDENCE_PREPARATION_DELAYED",
                        "updated_at": utc_now(),
                    })
            result["evidence_preparation_status"] = (
                "PREPARING" if dispatch.get("status") == "success"
                else "DELAYED")
        return result

    @app.get("/api/hiring/csrf")
    async def hiring_csrf(request: Request):
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        token = auth.csrf_token(request)
        if not token:
            return JSONResponse({"error": "signed session required"}, status_code=401)
        return {"status": "success", "csrf_token": token,
                "actor_id": principal.actor_id,
                "workspace_id": principal.workspace_id}

    @app.get("/api/hiring/roles/{role_id}/conversation-context")
    async def role_conversation_context(request: Request, role_id: str):
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        services = _services()
        if not services:
            return JSONResponse(
                {"status": "error", "error": True,
                 "error_code": "hiring_unavailable",
                 "message": "Hiring is temporarily unavailable."},
                status_code=503)
        return _response(await services[0].get_role_conversation_context(
            principal=principal, role_id=role_id))

    @app.get("/api/hiring/applications/{application_id}/conversation-context")
    async def candidate_conversation_context(
            request: Request, application_id: str):
        """Return the closed durable evidence context used by canonical Alex."""
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        services = _services()
        if not services:
            return _response({
                "status": "error", "error": True,
                "error_code": "provider_unavailable",
                "message": "Hiring is temporarily unavailable.",
            })
        return _response(await services[0].get_candidate_conversation_context(
            principal=principal, application_id=application_id))

    # -- Internal controlled demo ------------------------------------------
    # This has a separate policy, records and approval domain from H4S. It is
    # intentionally incapable of selecting a normal connector or recipient.

    @app.post("/api/hiring/internal-demo/runs")
    async def create_internal_demo_run(request: Request, payload: InternalDemoRunRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await InternalControlledDemoService(production_store()).create_run(
            principal=principal, client_request_id=payload.client_request_id,
            role_id=payload.role_id,
            ttl_minutes=payload.ttl_minutes))

    @app.post("/api/hiring/internal-demo/runs/{demo_run_id}/approvals")
    async def request_internal_demo_approval(request: Request, demo_run_id: str,
                                             payload: InternalDemoApprovalRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await InternalControlledDemoService(production_store()).request_approval(
            principal=principal, demo_run_id=demo_run_id,
            action_kind=payload.action_kind, client_request_id=payload.client_request_id))

    @app.post("/api/hiring/internal-demo/approvals/{approval_id}/resolve")
    async def resolve_internal_demo_approval(
            request: Request, approval_id: str,
            payload: InternalDemoApprovalResolutionRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await InternalControlledDemoService(production_store()).resolve_approval(
            principal=principal, approval_id=approval_id, decision=payload.decision))

    @app.post("/api/hiring/internal-demo/runs/{demo_run_id}/effect-executions")
    async def execute_internal_demo_effect(request: Request, demo_run_id: str,
                                           payload: InternalDemoExecutionRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await InternalDemoEffectService(store=production_store()).execute(
            principal=principal, demo_run_id=demo_run_id, approval_id=payload.approval_id,
            action_kind=payload.action_kind))

    @app.post("/api/hiring/internal-demo/runs/{demo_run_id}/effect-reconciliations")
    async def reconcile_internal_demo_effect(request: Request, demo_run_id: str,
                                              payload: InternalDemoReconcileRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await InternalDemoEffectService(store=production_store()).reconcile(
            principal=principal, demo_run_id=demo_run_id, action_id=payload.action_id))

    @app.get("/api/hiring/internal-demo/runs/{demo_run_id}/projection")
    async def get_internal_demo_projection(request: Request, demo_run_id: str):
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        store = production_store()
        run = await store.get("internal_demo_runs", demo_run_id)
        if not run or run.get("workspace_id") != principal.workspace_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        approvals = await store.list(
            "approvals",
            filters={"demo_run_id": demo_run_id,
                     "approval_domain": "INTERNAL_CONTROLLED_DEMO"},
            order_by="created_at", descending=False, limit=100)
        actions = await store.list(
            "external_actions",
            filters={"demo_run_id": demo_run_id,
                     "action_domain": "INTERNAL_CONTROLLED_DEMO"},
            order_by="created_at", descending=False, limit=100)
        applications = await store.list(
            "internal_demo_applications", filters={"demo_run_id": demo_run_id},
            order_by="created_at", descending=False, limit=20)
        return {"status": "success", "live_internal_demo": True, "demo_run": run,
                "approvals": approvals, "actions": actions,
                "applications": [{key: row.get(key) for key in (
                    "application_id", "candidate_application_id", "assessment_id", "state",
                    "evidence", "created_at", "updated_at")}
                    for row in applications]}

    @app.post("/api/hiring/internal-demo/runs/{demo_run_id}/fixture-import")
    async def import_internal_demo_fixture(request: Request, demo_run_id: str,
                                           payload: InternalDemoFixtureImportRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        services = _services()
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"}, status_code=503)
        return _response(await InternalDemoInboxImportService(
            production_store(), hiring=services[0]).import_fixture(
                principal=principal, demo_run_id=demo_run_id))

    @app.post("/api/hiring/internal-demo/runs/{demo_run_id}/reset")
    async def reset_internal_demo(request: Request, demo_run_id: str,
                                  payload: InternalDemoResetRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await InternalControlledDemoService(production_store()).reset(
            principal=principal, demo_run_id=demo_run_id))

    @app.get("/api/hiring/h4s/roles/{role_id}")
    async def get_h4s_role(request: Request, role_id: str):
        """Return only an active, authorized sandbox projection for ROLE_ID."""
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        gate = authorize(principal, "read_role")
        if gate.get("error"):
            return _response(gate)
        rows = await production_store().list(
            "hiring_sandbox_runs",
            filters={"workspace_id": principal.workspace_id, "role_id": role_id},
            order_by="updated_at", descending=True, limit=20)
        active = next((row for row in rows if row.get("state") == "SANDBOX_PROVISIONED"), None)
        return {"status": "success", "sandbox": active}

    @app.get("/api/hiring/h4s/sandboxes/{sandbox_run_id}/projection")
    async def get_h4s_projection(request: Request, sandbox_run_id: str):
        """Read-only, receipt-backed H4S view for the existing hiring cockpit."""
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        store = production_store()
        sandbox = await store.get("hiring_sandbox_runs", sandbox_run_id)
        if (not sandbox or sandbox.get("workspace_id") != principal.workspace_id
                or authorize(principal, "read_role").get("error")):
            return JSONResponse({"error": "not found"}, status_code=404)
        actions = [
            row for row in await store.list(
                "external_actions", filters={"workspace_id": principal.workspace_id},
                order_by="updated_at", descending=True, limit=500)
            if (row.get("sandbox_context") or {}).get("sandbox_run_id") == sandbox_run_id
        ]
        correlations = await store.list(
            "hiring_reply_correlations", filters={"sandbox_run_id": sandbox_run_id},
            order_by="updated_at", descending=True, limit=100)
        return {"status": "success", "sandbox": sandbox,
                "actions": [{key: row.get(key) for key in (
                    "action_id", "action_kind", "status", "approval_id",
                    "provider_effect_id", "error_code", "uncertainty_reason",
                    "created_at", "updated_at", "completed_at", "result_ref",
                    "sandbox_context", "exact_action")} for row in actions],
                "reply_correlations": [{key: row.get(key) for key in (
                    "correlation_id", "action_id", "candidate_run_id", "status",
                    "safe_reason", "created_at", "resolved_at", "event_id", "wait_id")}
                    for row in correlations]}

    @app.post("/api/hiring/h4s/conversations")
    async def start_h4s_conversation(request: Request, payload: H4SConversationStartRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringRunAnswerService(production_store()).begin(
            principal=principal, sandbox_run_id=payload.sandbox_run_id,
            candidate_application_id=payload.candidate_application_id))

    @app.post("/api/hiring/h4s/conversations/answer")
    async def answer_h4s_conversation(request: Request, payload: H4SConversationAnswerRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringRunAnswerService(production_store()).answer(
            principal=principal, conversation_token=payload.conversation_token,
            question=payload.question, client_turn_id=payload.client_turn_id))

    # -- H4S sandbox provisioning ------------------------------------------
    # Without these the sandbox records that every other H4S surface reads
    # could never be created. Each one is founder-only inside the service and
    # additionally requires the deployment flag before any effect authority is
    # resolved.

    @app.post("/api/hiring/h4s/sandboxes")
    async def create_h4s_sandbox(request: Request, payload: H4SSandboxCreateRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        fixture_gate = _synthetic_demo_allowed(payload)
        if fixture_gate.get("error"):
            return _response(fixture_gate)
        return _response(await HiringSandboxService(production_store()).create(
            principal=principal, role_id=payload.role_id,
            fixture_id=payload.fixture_id,
            synthetic_namespace=payload.synthetic_namespace,
            connector_binding_ids=payload.connector_binding_ids,
            client_request_id=payload.client_request_id))

    @app.post("/api/hiring/h4s/sandboxes/{sandbox_run_id}/destinations")
    async def add_h4s_destination(request: Request, sandbox_run_id: str,
                                  payload: H4SDestinationRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringSandboxService(production_store()).provision_configured_destination(
            principal=principal, sandbox_run_id=sandbox_run_id,
            destination_kind=payload.destination_kind,
            client_request_id=payload.client_request_id))

    @app.post("/api/hiring/h4s/sandboxes/{sandbox_run_id}/connector-bindings")
    async def add_h4s_connector_binding(request: Request, sandbox_run_id: str,
                                        payload: H4SConnectorBindingRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(
            await HiringSandboxService(production_store()).provision_configured_connector_binding(
                principal=principal, sandbox_run_id=sandbox_run_id,
                provider_kind=payload.provider_kind,
                client_request_id=payload.client_request_id))

    @app.post("/api/hiring/h4s/sandboxes/{sandbox_run_id}/effect-approvals")
    async def request_h4s_effect_approval(request: Request, sandbox_run_id: str,
                                          payload: H4SEffectApprovalRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(
            await HiringSandboxService(production_store()).request_effect_approval(
                principal=principal, sandbox_run_id=sandbox_run_id,
                binding_id=payload.binding_id,
                candidate_application_id=payload.candidate_application_id,
                destination_ids=payload.destination_ids,
                action_kind=payload.action_kind,
                rendered_payload=payload.rendered_payload,
                client_request_id=payload.client_request_id))

    @app.post("/api/hiring/h4s/sandboxes/{sandbox_run_id}/effect-claims")
    async def claim_h4s_effect_approval(request: Request, sandbox_run_id: str,
                                        payload: H4SEffectClaimRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        # Compatibility route is deliberately fail-closed. The execution
        # endpoint owns atomic approval claim + action preparation.
        return _response(
            await HiringSandboxService(production_store()).claim_effect_approval(
                principal=principal, approval_id=payload.approval_id,
                sandbox_run_id=sandbox_run_id, binding_id=payload.binding_id,
                candidate_application_id=payload.candidate_application_id,
                destination_ids=payload.destination_ids,
                action_kind=payload.action_kind,
                rendered_payload=payload.rendered_payload))

    @app.post("/api/hiring/h4s/sandboxes/{sandbox_run_id}/effect-executions")
    async def execute_h4s_effect(request: Request, sandbox_run_id: str,
                                 payload: H4SEffectClaimRequest):
        """Run one exact-approved H4S action through its isolated executor.

        The default adapter is intentionally disabled until a separately
        provisioned test account is installed; this route cannot reach normal
        founder Gmail, Alex's mailbox, or the primary Calendar.
        """
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        store = production_store()
        sandbox = HiringSandboxService(store)
        return _response(await H4SEffectService(sandbox=sandbox, store=store).execute(
            principal=principal, approval_id=payload.approval_id,
            sandbox_run_id=sandbox_run_id, binding_id=payload.binding_id,
            candidate_application_id=payload.candidate_application_id,
            destination_ids=payload.destination_ids, action_kind=payload.action_kind,
            rendered_payload=payload.rendered_payload))

    @app.post("/api/hiring/h4s/sandboxes/{sandbox_run_id}/effect-reconciliations/{action_id}")
    async def reconcile_h4s_effect(request: Request, sandbox_run_id: str,
                                   action_id: str):
        """Reconcile an uncertain sandbox receipt; never retries blindly."""
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        store = production_store()
        sandbox = HiringSandboxService(store)
        return _response(await H4SEffectService(sandbox=sandbox, store=store).reconcile(
            principal=principal, sandbox_run_id=sandbox_run_id, action_id=action_id))

    @app.post("/tasks/hiring/h4s/process_reply")
    async def process_h4s_reply(request: Request):
        """Authenticated worker-only Gmail fetch, causal correlation and wake."""
        workload = await workload_identity.verify_request(
            request, "/tasks/hiring/h4s/process_reply")
        if isinstance(workload, dict):
            return _response(workload)
        try:
            if int(request.headers.get("content-length", "0") or 0) > 16_384:
                raise ValueError("task body too large")
            payload = H4SReplyDeliveryRequest.model_validate(await request.json())
        except Exception:
            return JSONResponse({"status": "error", "error": True,
                                 "error_code": "invalid_contract",
                                 "message": "Invalid internal delivery."}, status_code=400)
        store = production_store()
        sandbox = HiringSandboxService(store)
        resolved = await sandbox.resolve_connector_binding(
            sandbox_run_id=payload.sandbox_run_id, binding_id=payload.binding_id,
            provider_kind="GMAIL_TEST")
        if resolved.get("error"):
            return _response(resolved)
        fetched = await H4SGoogleEffectAdapter().fetch_inbound_reply(
            binding=resolved["binding"], provider_message_id=payload.provider_message_id)
        if fetched.get("status") != "success":
            return _response(fetched)
        destinations = await store.list(
            "hiring_sandbox_destinations",
            filters={"sandbox_run_id": payload.sandbox_run_id}, limit=1000)
        matched = [row for row in destinations if row.get("state") == "ACTIVE"
                   and row.get("destination_kind") == "TEST_CANDIDATE"
                   and row.get("normalized_address") == fetched.get("sender_address")]
        sender_destination_id = matched[0]["destination_id"] if len(matched) == 1 else "unknown"
        result = await H4SReplyService(sandbox=sandbox, store=store).ingest_verified_reply(
            sandbox_run_id=payload.sandbox_run_id, binding_id=payload.binding_id,
            provider_message_id=fetched["provider_message_id"],
            provider_thread_id=fetched["provider_thread_id"],
            in_reply_to_message_id=fetched["in_reply_to_message_id"],
            causal_token=fetched["causal_token"],
            sender_destination_id=sender_destination_id,
            message_kind=fetched["message_kind"], auto_submitted=fetched["auto_submitted"])
        correlation_id = str(result.get("correlation_id") or "")
        correlation = await store.get("hiring_reply_correlations", correlation_id)
        if correlation:
            await store.compare_and_set(
                "hiring_reply_correlations", correlation_id, int(correlation["version"]),
                {"last_workload_principal": workload.audit_fields(), "updated_at": utc_now()})
        return _response(result)

    @app.post("/api/hiring/h4s/sandboxes/{sandbox_run_id}/close")
    async def close_h4s_sandbox(request: Request, sandbox_run_id: str):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringSandboxService(production_store()).close(
            principal=principal, sandbox_run_id=sandbox_run_id))

    @app.post("/api/hiring/roles")
    async def create_role(request: Request, payload: CreateRoleRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        fixture_gate = _synthetic_demo_allowed(payload)
        if fixture_gate.get("error"):
            return _response(fixture_gate)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        return _response(await services[0].create_role(
            principal=principal, contract=payload.contract,
            client_request_id=payload.client_request_id,
            synthetic_guard=_guard(payload)))

    @app.get("/api/hiring/roles")
    async def list_roles(request: Request):
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        rows = await production_store().list(
            "hiring_roles", filters={"workspace_id": principal.workspace_id},
            order_by="updated_at", descending=True, limit=200)
        if request.query_params.get("include_demo") != "true":
            rows = [row for row in rows if row.get("synthetic") is not True]
        return {"status": "success", "roles": rows}

    @app.get("/api/hiring/roles/{role_id}")
    async def get_role(request: Request, role_id: str):
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        role = await production_store().get("hiring_roles", role_id)
        if not role or role.get("workspace_id") != principal.workspace_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        gate = authorize(principal, "read_role")
        if gate.get("error"):
            return _response(gate)
        candidates = await production_store().list(
            "candidate_applications", filters={"workspace_id": principal.workspace_id,
                                                "role_id": role_id}, limit=1000)
        policies = await production_store().list(
            "hiring_policy_versions", filters={"workspace_id": principal.workspace_id,
                                                "role_id": role_id}, limit=1000)
        impacts = await production_store().list(
            "hiring_policy_impacts", filters={"workspace_id": principal.workspace_id,
                                               "role_id": role_id}, limit=1000)
        # Candidate visibility is decided by the one code-owned workspace gate;
        # no second human-role system is reconstructed in this route.
        candidates = [
            row for row in candidates
            if not authorize(
                principal, "read_candidate",
            ).get("error")]
        async def project_candidate(row: dict[str, Any]) -> dict[str, Any]:
            assessment_id = str(row.get("current_assessment_id") or "")
            assessment = (await production_store().get(
                "candidate_assessments", assessment_id)
                if assessment_id else None)
            identity, summary = await asyncio.gather(
                _founder_candidate_identity(principal, row),
                _candidate_evidence_summary(row))
            return {
                **row,
                "identity": identity,
                "identity_status": "VISIBLE" if identity else "UNAVAILABLE",
                "evidence_summary": summary,
                "evidence_status": candidate_evidence_status(
                    row, assessment=assessment,
                    current_policy_version_id=str(
                        role.get("current_policy_version_id") or "")),
            }

        candidates = list(await asyncio.gather(*(
            project_candidate(row) for row in candidates)))
        policies.sort(key=lambda item: int(item.get("sequence", 0)))
        return JSONResponse(
            {"status": "success", "role": role, "candidates": candidates,
             "policy_versions": policies, "policy_impacts": impacts},
            headers={"Cache-Control": "no-store"})

    @app.post("/api/hiring/roles/{role_id}/policy-versions")
    async def propose_policy(request: Request, role_id: str,
                             payload: PolicyRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await hiring_policy_service.propose_policy(
            principal=principal, role_id=role_id, contract=payload.contract,
            role_description=payload.role_description,
            change_reason=payload.change_reason,
            client_request_id=payload.client_request_id))

    @app.post("/api/hiring/roles/{role_id}/job-description-drafts")
    async def save_job_description_draft(
            request: Request, role_id: str,
            payload: RoleDescriptionDraftRequest):
        """Save an immutable proposed role package; never publish or activate it."""
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        role = await production_store().get("hiring_roles", role_id)
        if (not role or role.get("workspace_id") != principal.workspace_id
                or role.get("synthetic") is not False):
            return JSONResponse({"status": "error", "error": True,
                                 "error_code": "role_not_found",
                                 "message": "Role does not exist."}, status_code=404)
        gate = authorize(principal, "prepare_role")
        if gate.get("error"):
            return _response(gate)
        if int(role.get("version", 0)) != payload.expected_role_version:
            return _response({"status": "error", "error": True,
                              "error_code": "version_conflict",
                              "message": "Role changed; reload before saving."})
        current_contract = RoleContract.model_validate(
            role.get("draft_contract") or {})
        package = build_contract(
            company_name=current_contract.company_name,
            role_title=current_contract.role_title,
            role_summary=payload.purpose,
            headcount_target=current_contract.headcount_target,
            target_date=current_contract.target_date,
            location=payload.location,
            work_arrangement=payload.work_arrangement,
            employment_type=payload.employment_type,
            compensation_envelope=payload.compensation,
            required_criteria=payload.required_qualifications,
            responsibilities=payload.responsibilities,
            success_outcomes=payload.success_outcomes,
            preferred_criteria=payload.preferred_qualifications,
            relevant_experience=payload.relevant_experience,
            benefits=payload.benefits,
            hiring_process=payload.hiring_process,
            application_instructions=payload.application_instructions,
            equal_opportunity_statement=payload.equal_opportunity_statement,
            accessibility_statement=payload.accessibility_statement,
            public_job_description=payload.purpose,
        )
        if package.get("status") != "success":
            return _response(package)
        result = await hiring_policy_service.propose_policy(
            principal=principal, role_id=role_id,
            contract=package["contract"],
            role_description=package["role_description"],
            change_reason=payload.change_reason,
            client_request_id=payload.client_request_id)
        return _response(result)

    @app.get("/api/hiring/roles/{role_id}/job-description-preview/{policy_id}")
    async def preview_job_description(
            request: Request, role_id: str, policy_id: str):
        """Founder-only preview of the exact proposed candidate-facing page."""
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        services = _services()
        if not services:
            return JSONResponse({"status": "error", "error": True,
                                 "error_code": "hiring_unavailable",
                                 "message": "Hiring is temporarily unavailable."},
                                status_code=503)
        return _response(await services[0].get_role_draft_preview(
            principal=principal, role_id=role_id,
            policy_version_id=policy_id))

    @app.get("/api/hiring/roles/{role_id}/policy-impact/{policy_id}")
    async def policy_impact(request: Request, role_id: str, policy_id: str):
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        role = await production_store().get("hiring_roles", role_id)
        if not role or role.get("workspace_id") != principal.workspace_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        gate = authorize(principal, "read_role")
        if gate.get("error"):
            return _response(gate)
        impacts = await production_store().list(
            "hiring_policy_impacts",
            filters={"workspace_id": principal.workspace_id,
                     "role_id": role_id, "policy_version_id": policy_id}, limit=2)
        if len(impacts) != 1:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"status": "success", "impact": impacts[0]}

    @app.post("/api/hiring/roles/{role_id}/approvals")
    async def create_approval(request: Request, role_id: str,
                              payload: ApprovalRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await request_approval(
            principal=principal, run_id=payload.run_id, role_id=role_id,
            policy_version_id=payload.policy_version_id,
            action_kind=payload.action_kind, exact_action=payload.exact_action,
            client_request_id=payload.client_request_id))

    @app.post("/api/hiring/approvals/{approval_id}/resolve")
    async def resolve_hiring_approval(request: Request, approval_id: str,
                                      payload: ResolveApprovalRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await resolve_approval(
            principal=principal, approval_id=approval_id,
            decision=payload.decision, require_fresh=payload.require_fresh))

    @app.post("/api/hiring/roles/{role_id}/policy-versions/{policy_id}/approve")
    async def activate_policy(request: Request, role_id: str, policy_id: str,
                              payload: ActivatePolicyRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await hiring_policy_service.approve_policy(
            principal=principal, role_id=role_id, policy_version_id=policy_id,
            expected_role_version=payload.expected_role_version,
            approval_id=payload.approval_id))

    @app.post("/api/hiring/roles/{role_id}/publication-receipts")
    async def record_publication(request: Request, role_id: str,
                                 payload: PublicationRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        return _response(await services[0].record_publication(
            principal=principal, role_id=role_id, destination=payload.destination,
            public_url=payload.public_url, attestation=payload.attestation,
            expected_version=payload.expected_role_version,
            client_request_id=payload.client_request_id))

    @app.post("/api/hiring/roles/{role_id}/publish")
    async def publish_role(request: Request, role_id: str,
                           payload: PublishRoleRequest):
        """Publish the exact approved package on the app-owned public page."""
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return _response({"status": "error", "error": True,
                              "error_code": "hiring_unavailable",
                              "message": "Hiring is temporarily unavailable."})
        if not public_intake_configured():
            return _response({
                "status": "error", "error": True,
                "error_code": "public_intake_not_enabled",
                "message": (
                    "The public application form is not securely configured. "
                    "Nothing was published."),
            })
        configured = os.environ.get("AGENT_BASE_URL", "").rstrip("/")
        base = (configured if os.environ.get("K_SERVICE") else
                str(request.base_url).rstrip("/"))
        public_url = f"{base}/hiring-notice.html?role_id={quote(role_id)}"
        return _response(await services[0].record_publication(
            principal=principal, role_id=role_id,
            destination="COFOUNDER_PUBLIC_ROLE_PAGE", public_url=public_url,
            attestation=("Founder clicked Publish approved role for the exact "
                         "approved app-owned job page."),
            expected_version=payload.expected_role_version,
            client_request_id=payload.client_request_id))

    @app.post("/tasks/hiring/prepare_candidate_evidence")
    async def prepare_candidate_evidence(request: Request):
        """Authenticated Alex worker: prepare evidence, never make a decision."""
        workload = await workload_identity.verify_request(
            request, "/tasks/hiring/prepare_candidate_evidence")
        if isinstance(workload, dict):
            return _response(workload)
        try:
            if int(request.headers.get("content-length", "0") or 0) > 8_192:
                raise ValueError("task body too large")
            payload = PrepareCandidateEvidenceRequest.model_validate(
                await request.json())
        except Exception:
            return JSONResponse({
                "status": "error", "error": True,
                "error_code": "invalid_contract",
                "message": "Invalid internal delivery.",
            }, status_code=400)
        services = _services()
        if not services:
            return JSONResponse({
                "status": "error", "error": True,
                "error_code": "hiring_unavailable",
                "message": "Hiring is temporarily unavailable.",
            }, status_code=503)
        return _response(await services[0].prepare_public_application_evidence(
            application_id=payload.application_id,
            workload=workload.audit_fields()))

    @app.post("/api/hiring/roles/{role_id}/mailbox-binding")
    async def configure_binding(request: Request, role_id: str,
                                payload: BindingRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        fixture_gate = _synthetic_demo_allowed(payload)
        if fixture_gate.get("error"):
            return _response(fixture_gate)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        gate = authorize(principal, "prepare_role")
        if gate.get("error"):
            return _response(gate)
        return _response(await services[1].configure_binding(
            role_id=role_id, connection_id=payload.connection_id,
            provider_route_id=payload.provider_route_id,
            provider_label_id=payload.provider_label_id,
            expected_role_version=payload.expected_role_version,
            synthetic_guard=_guard(payload)))

    @app.post("/api/hiring/roles/{role_id}/mailbox-binding/probe")
    async def probe_binding(request: Request, role_id: str, payload: ProbeRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        gate = authorize(principal, "prepare_role")
        if gate.get("error"):
            return _response(gate)
        return _response(await services[1].record_probe(
            role_id=role_id, fixture_message_id=payload.fixture_message_id,
            probe_kind=payload.probe_kind,
            expected_role_version=payload.expected_role_version))

    @app.post("/api/hiring/synthetic/messages")
    async def seed_message(request: Request, payload: FixtureMessageRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        fixture_gate = _synthetic_demo_allowed(payload)
        if fixture_gate.get("error"):
            return _response(fixture_gate)
        if not services:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return _response(await services[1].seed_fixture_message(
            message=payload.message.model_dump(mode="json", exclude={"schema_version"}),
            synthetic_guard=_guard(payload)))

    @app.post("/api/hiring/synthetic/mailbox-batches")
    async def create_batch(request: Request, payload: FetchBatchRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        fixture_gate = _synthetic_demo_allowed(payload)
        if fixture_gate.get("error"):
            return _response(fixture_gate)
        if not services:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        result = await services[1].create_fetch_batch(
            connection_id=payload.connection_id, old_cursor=payload.old_cursor,
            proposed_cursor=payload.proposed_cursor, message_ids=payload.message_ids,
            batch_key=payload.batch_key, mode=payload.mode,
            fully_paginated=payload.fully_paginated,
            synthetic_guard=_guard(payload))
        if not result.get("error"):
            # Hand the reserved manifest to the durable worker. The batch is
            # already committed, so a dispatch failure is reported as data and
            # heals on the next identical request rather than losing the batch.
            dispatch = await asyncio.to_thread(
                task_queue.enqueue_hiring,
                "/tasks/hiring/process_mailbox_batch",
                {"batch_id": result["batch_id"]},
                f"hiring-batch:{result['batch_id']}")
            result = {**result, "dispatch_status": dispatch.get("status", "error"),
                      "dispatch_message": dispatch.get("message", "")}
        return _response(result)

    @app.post("/tasks/hiring/process_mailbox_batch")
    async def process_mailbox_batch(request: Request):
        principal = await workload_identity.verify_request(
            request, "/tasks/hiring/process_mailbox_batch")
        if isinstance(principal, dict):
            return _response(principal)
        # Parse identifiers only after workload authentication, so an invalid
        # caller cannot use validation responses as an existence oracle.
        try:
            if int(request.headers.get("content-length", "0") or 0) > 65_536:
                raise ValueError("task body too large")
            payload = ProcessBatchRequest.model_validate(await request.json())
        except Exception:
            return JSONResponse({"status": "error", "error": True,
                                 "error_code": "invalid_contract",
                                 "message": "Invalid internal delivery."},
                                status_code=400)
        services = _services()
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        store = production_store()
        batch = await store.get("mailbox_fetch_batches", payload.batch_id)
        if not batch or not _fixture_id_allowed(str(batch.get("fixture_id") or "")):
            return JSONResponse({"status": "error", "error": True,
                                 "error_code": "synthetic_fixture_not_authorized",
                                 "message": "Read-only test processing is disabled."},
                                status_code=403)
        result = await services[1].process_batch(payload.batch_id)
        # Workload provenance is written into the batch without exposing
        # payload existence to unauthenticated callers.
        if not result.get("error"):
            batch = await store.get("mailbox_fetch_batches", payload.batch_id)
            if batch:
                await store.compare_and_set(
                    "mailbox_fetch_batches", payload.batch_id, int(batch["version"]),
                    {"last_workload_principal": principal.audit_fields()})
        return _response(result)

    @app.get("/api/hiring/applications/{application_id}")
    async def candidate_detail(request: Request, application_id: str):
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        detail = await services[0].candidate_detail(
            principal=principal, application_id=application_id)
        if detail.get("error"):
            return _response(detail)
        identity = await _founder_candidate_identity(
            principal, detail["application"])
        detail["identity"] = identity
        detail["identity_status"] = "VISIBLE" if identity else "UNAVAILABLE"
        detail["identity_revealed"] = bool(identity)
        return JSONResponse(detail, headers={"Cache-Control": "no-store"})

    @app.post("/api/hiring/applications/{application_id}/resume")
    async def open_candidate_resume(
            request: Request, application_id: str,
            payload: ResumeOpenRequest):
        """Stream one audited restricted CV to the authenticated Founder.

        The route never returns a durable public URL and never places CV bytes
        in general artifacts, search, memory, logs, or model context.
        """
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        gate = authorize(principal, "read_candidate", require_fresh=True)
        if gate.get("error"):
            return _response(gate)
        store = production_store()
        application = await store.get("candidate_applications", application_id)
        if (not application
                or application.get("workspace_id") != principal.workspace_id):
            return JSONResponse({"status": "error", "error": True,
                                 "error_code": "application_not_found",
                                 "message": "Application does not exist."},
                                status_code=404)
        opened = await HiringPublicIntakeService(
            store=store).read_restricted_resume(application=application)
        if opened.get("error"):
            return _response(opened)
        artifact = dict(opened["artifact"])
        audit_id = stable_id(
            "audit", principal.workspace_id, "resume_open",
            application_id, payload.client_request_id)
        await store.create("audit", audit_id, {
            "schema_version": 2, "audit_id": audit_id,
            "founder_id": principal.workspace_id,
            "workspace_id": principal.workspace_id,
            "actor": principal.actor_id, "actor_id": principal.actor_id,
            "action": "hiring.resume.open",
            "target": f"hiring_candidate_artifacts/{artifact.get('artifact_id')}",
            "result": "success",
            "detail": "authorized restricted resume open",
            "idempotency_key": payload.client_request_id,
            "created_at": utc_now(), "version": 1,
        })
        content_type = str(artifact.get("content_type") or
                           "application/octet-stream")
        disposition = "inline" if content_type == "application/pdf" else "attachment"
        filename = f"candidate-resume{opened['extension']}"
        return Response(
            content=opened["resume_bytes"], media_type=content_type,
            headers={
                "Cache-Control": "no-store, private",
                "Content-Disposition": f'{disposition}; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
                "X-Hiring-Audit-Id": audit_id,
            })

    @app.get("/api/hiring/applications/{application_id}/coordination")
    async def hiring_coordination_projection(request: Request, application_id: str):
        """Receipt-backed live communication/interview state for one candidate."""
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringCoordinationService(
            production_store()).projection(
                principal=principal, application_id=application_id))

    @app.post("/api/hiring/applications/{application_id}/coordination/contact")
    async def prepare_hiring_contact(request: Request, application_id: str,
                                     payload: HiringContactRequest):
        """Prepare, but never send, one candidate-bound Alex email."""
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringCoordinationService(
            production_store()).prepare_contact(
                principal=principal, application_id=application_id,
                client_request_id=payload.client_request_id,
                reply=payload.reply, copy_founder=payload.copy_founder,
                duration_minutes=payload.duration_minutes,
                subject=payload.subject, body=payload.body,
                availability_hash=payload.availability_hash,
                preview_only=payload.preview_only))

    @app.post("/api/hiring/applications/{application_id}/coordination/interview")
    async def prepare_hiring_interview(request: Request, application_id: str,
                                       payload: HiringInterviewRequest):
        """Prepare an exact create/update/cancel for a Hiring-owned interview."""
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringCoordinationService(
            production_store()).prepare_interview(
                principal=principal, application_id=application_id,
                start=payload.start, end=payload.end,
                timezone_name=payload.timezone,
                client_request_id=payload.client_request_id,
                target_event_id=payload.target_event_id,
                cancel=payload.cancel))

    @app.post("/api/hiring/applications/{application_id}/coordination/execute")
    async def execute_hiring_coordination(request: Request, application_id: str,
                                          payload: HiringEffectExecutionRequest):
        """Consume one fresh exact approval and execute its one provider action."""
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringCoordinationService(
            production_store()).execute(
                principal=principal, application_id=application_id,
                coordination_id=payload.coordination_id,
                approval_id=payload.approval_id))

    @app.post("/api/hiring/applications/{application_id}/coordination/actions/{action_id}/reconcile")
    async def reconcile_hiring_coordination(request: Request, application_id: str,
                                            action_id: str):
        """Read provider evidence for one uncertain action; never repeats it."""
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringCoordinationService(
            production_store()).reconcile(
                principal=principal, application_id=application_id,
                action_id=action_id))

    @app.post("/api/hiring/applications/{application_id}/conversations")
    async def start_candidate_conversation(request: Request, application_id: str,
                                           payload: ClosedRequest):
        del payload
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringCandidateConversationService(
            production_store()).begin(
                principal=principal,
                candidate_application_id=application_id))

    @app.post("/api/hiring/candidate-conversations/answer")
    async def answer_candidate_conversation(
            request: Request, payload: H4SConversationAnswerRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        return _response(await HiringCandidateConversationService(
            production_store()).answer(
                principal=principal,
                conversation_token=payload.conversation_token,
                question=payload.question,
                client_turn_id=payload.client_turn_id))

    @app.post("/api/hiring/applications/{application_id}/identity-reveal")
    async def reveal_candidate_identity(request: Request, application_id: str,
                                        payload: IdentityRevealRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        store = production_store()
        application = await store.get("candidate_applications", application_id)
        if not application or application.get("workspace_id") != principal.workspace_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        if (application.get("source_kind") == "PUBLIC_FORM"
                and application.get("synthetic") is False):
            revealed = await HiringPublicIntakeService(
                store=store).reveal_restricted_identity(
                    application=application, principal=principal)
        else:
            revealed = await services[0].identity_vault.reveal_identity(
                identity_id=application["candidate_id"],
                workspace_id=application["workspace_id"],
                role_id=application["role_id"],
                candidate_application_id=application_id, principal=principal)
        if revealed.get("error"):
            return _response(revealed)
        audit_id = stable_id("audit", principal.workspace_id, "identity_reveal",
                             application_id, payload.client_request_id)
        await store.create("audit", audit_id, {
            "schema_version": 2, "audit_id": audit_id,
            "founder_id": principal.workspace_id,
            "workspace_id": principal.workspace_id, "actor": principal.actor_id,
            "actor_id": principal.actor_id, "action": "hiring.identity.reveal",
            "target": f"candidate_applications/{application_id}",
            "result": "success", "detail": "authorized candidate identity reveal",
            "idempotency_key": payload.client_request_id,
            "created_at": utc_now(), "version": 1,
        })
        return JSONResponse({"status": "success", "identity": revealed["identity"],
                             "audit_id": audit_id},
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/hiring/applications/{application_id}/decisions")
    async def human_decision(request: Request, application_id: str,
                             payload: HumanDecisionInput):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        return _response(await services[0].record_human_decision(
            principal=principal, application_id=application_id,
            decision_input=payload))

    @app.post("/api/hiring/applications/{application_id}/candidate-requests")
    async def candidate_request(request: Request, application_id: str,
                                payload: CandidateRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        return _response(await services[0].record_candidate_request(
            principal=principal, application_id=application_id,
            request_kind=payload.request_kind, safe_note=payload.safe_note,
            client_request_id=payload.client_request_id,
            expected_application_version=payload.expected_application_version))

    @app.post("/api/hiring/applications/{application_id}/data-export")
    async def export_candidate_data(request: Request, application_id: str,
                                    payload: CandidateExportRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        result = await HiringDataRightsService(
            identity_vault=services[0].identity_vault,
            store=production_store()).export_candidate(
                principal=principal, application_id=application_id,
                client_request_id=payload.client_request_id)
        from services.error_contracts import http_status
        return JSONResponse(result, status_code=http_status(result, default=400),
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/hiring/applications/{application_id}/data-deletion")
    async def delete_candidate_data(request: Request, application_id: str,
                                    payload: CandidateDeletionRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        rights = HiringDataRightsService(
            identity_vault=services[0].identity_vault, store=production_store())
        if payload.execute:
            if not payload.expected_inventory_hash:
                return _response({"status": "error", "error": True,
                                  "error_code": "inventory_hash_required",
                                  "message": "Execute requires the exact dry-run hash."})
            result = await rights.execute_deletion(
                principal=principal, application_id=application_id,
                expected_inventory_hash=payload.expected_inventory_hash,
                client_request_id=payload.client_request_id)
        else:
            result = await rights.deletion_plan(
                principal=principal, application_id=application_id)
        from services.error_contracts import http_status
        return JSONResponse(result, status_code=http_status(result, default=400),
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/hiring/applications/{application_id}/legal-hold")
    async def change_candidate_legal_hold(request: Request, application_id: str,
                                          payload: CandidateLegalHoldRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return JSONResponse({"error": "synthetic encryption is not configured"},
                                status_code=503)
        result = await HiringDataRightsService(
            identity_vault=services[0].identity_vault,
            store=production_store()).set_legal_hold(
                principal=principal, application_id=application_id,
                active=payload.active, reason_code=payload.reason_code,
                expected_identity_version=payload.expected_identity_version,
                client_request_id=payload.client_request_id)
        return _response(result)

    @app.get("/api/hiring/runs/{run_id}/timeline")
    async def run_timeline(request: Request, run_id: str):
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        run = await production_store().get("workflow_runs", run_id)
        if not run or run.get("workspace_id") != principal.workspace_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        if run.get("run_kind") == "ROLE":
            gate = authorize(principal, "read_role")
        else:
            application = await production_store().get(
                "candidate_applications", run["domain_ref"])
            if not application:
                return JSONResponse({"error": "not found"}, status_code=404)
            gate = authorize(
                principal, "read_candidate")
        if gate.get("error"):
            return _response(gate)
        events = await production_store().list(
            "run_events", filters={"run_id": run_id}, order_by="sequence", limit=1000)
        return {"status": "success", "run": run, "events": events}

    @app.get("/api/hiring/inbox")
    async def hiring_inbox(request: Request):
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        rows = await production_store().list(
            "founder_inbox", filters={"workspace_id": principal.workspace_id,
                                       "status": "OPEN"},
            order_by="created_at", descending=True, limit=200)
        safe = [{key: value for key, value in row.items() if key not in {
            "raw_content", "candidate_identity", "email", "name", "phone"}}
                for row in rows if str(row.get("kind", "")).startswith("HIRING_")
                and _inbox_row_visible(principal, row)]
        return {"status": "success", "items": safe}
