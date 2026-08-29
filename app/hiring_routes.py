"""Founder, applicant, and workload HTTP surfaces for hiring operations."""

from __future__ import annotations

import asyncio
import hashlib
import html
import hmac
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app import auth
from services import (
    document_ingestion,
    hiring_activation,
    hiring_application_intake,
    hiring_policy_service,
    storage,
    task_queue,
    workload_identity,
)
from services.actor_identity import (
    ActorPrincipal,
    WorkspaceRole,
    authorize,
    resolve_actor_from_claims,
)
from services.durable_store import production_store
from services.hiring_approval_service import request_approval, resolve_approval
from services.hiring_contracts import (
    HumanDecisionInput,
    Criterion,
    RoleContract,
    SyntheticFixtureMessage,
    canonical_hash,
    stable_id,
    utc_now,
)
from services.hiring_data_rights import HiringDataRightsService
from services.hiring_h4s_effects import H4SEffectService
from services.hiring_h4s_google import H4SGoogleEffectAdapter
from services.hiring_h4s_reply import H4SReplyService
from services.hiring_identity_vault import (
    CandidateIdentityVault,
    fixture_key_wrapper,
    kms_key_wrapper,
)
from services.hiring_mailbox import HiringMailboxService
from services.hiring_run_answer import HiringRunAnswerService
from services.hiring_sandbox import HiringSandboxService
from services.hiring_service import HiringService
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


class PublishInternalRoleRequest(ClosedRequest):
    expected_role_version: int = Field(ge=1)
    client_request_id: str = Field(min_length=8, max_length=128)


class PublicationRequest(ClosedRequest):
    destination: str
    public_url: str
    attestation: str
    expected_role_version: int = Field(ge=1)
    client_request_id: str


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


class IdentityRevealRequest(ClosedRequest):
    client_request_id: str


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
                "message": "This synthetic fixture is not enabled by deployment policy.",
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


def _inbox_row_visible(principal: ActorPrincipal, row: dict[str, Any]) -> bool:
    """Defer inbox visibility to the same gate that guards the records.

    A row with no role_id (an unrouted mailbox quarantine) is workspace-level
    triage and stays owner-only.
    """
    role_id = str(row.get("role_id") or "")
    candidate_id = str(row.get("candidate_application_id") or "")
    if not role_id:
        return principal.role is WorkspaceRole.OWNER
    operation = "read_candidate" if candidate_id else "read_role"
    return not authorize(principal, operation, role_id=role_id,
                         candidate_application_id=candidate_id).get("error")


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
    kms_key_name = os.environ.get("HIRING_IDENTITY_KMS_KEY_NAME", "")
    dedup_secret = os.environ.get("HIRING_IDENTITY_DEDUP_KEY", "")
    if kms_key_name:
        if len(dedup_secret) < 32:
            return None
        wrap, unwrap = kms_key_wrapper(kms_key_name)
        key = hashlib.sha256(dedup_secret.encode()).digest()
        allow_live = True
    else:
        allow_live = not bool(os.environ.get("K_SERVICE"))
        if os.environ.get("K_SERVICE"):
            return None
        raw = os.environ.get("HIRING_SYNTHETIC_ENCRYPTION_KEY", "")
        if not raw:
            raw = os.environ.get("APP_SESSION_SECRET", "local-hiring-fixture-only")
        key = hashlib.sha256(raw.encode()).digest()
        wrap, unwrap = fixture_key_wrapper(key)
    store = production_store()
    vault = CandidateIdentityVault(wrap_key=wrap, unwrap_key=unwrap,
                                   dedup_key=key, store=store,
                                   allow_live=allow_live)
    hiring = HiringService(store=store, identity_vault=vault)
    return hiring, HiringMailboxService(hiring, store=store)


_PUBLIC_SLUG = re.compile(r"^[0-9a-f]{32}$")
_PUBLIC_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_EMAIL = re.compile(r"^[^\s@]{1,160}@[^\s@]{1,190}\.[^\s@]{2,63}$")
_MAX_APPLICATION_BYTES = 10 * 1024 * 1024


async def _public_role(public_slug: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Resolve one published role and its exact frozen publication policy."""
    if not _PUBLIC_SLUG.fullmatch(public_slug):
        return None
    rows = await production_store().list(
        "hiring_roles", filters={"public_slug": public_slug}, limit=2)
    if len(rows) != 1 or rows[0].get("role_state") != "PUBLISHED":
        return None
    role = rows[0]
    policy_id = str(role.get("published_policy_version_id") or "")
    policy = await production_store().get("hiring_policy_versions", policy_id)
    if (not policy or policy.get("workspace_id") != role.get("workspace_id")
            or policy.get("role_id") != role.get("role_id")
            or policy.get("status") != "APPROVED"
            or policy.get("canonical_hash") != role.get("published_policy_hash")):
        return None
    return role, policy


def _public_application_token(role: dict[str, Any], policy: dict[str, Any]) -> str:
    secret = os.environ.get("APP_SESSION_SECRET", "local-public-hiring-token")
    material = "|".join([
        str(role["public_slug"]), str(role["role_id"]),
        str(policy["canonical_hash"]), "public-application-v1",
    ]).encode()
    return hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()


def _privacy_contact() -> str:
    configured = os.environ.get("HIRING_PRIVACY_CONTACT", "").strip()[:254]
    return configured if _EMAIL.fullmatch(configured) else ""


def _public_applications_ready(role: dict[str, Any]) -> bool:
    return bool(
        role.get("synthetic") is not True
        and hiring_activation.live_applications_enabled()
        and _privacy_contact()
        and _services() is not None)


def _safe_public_contract(role: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    contract = policy["contract"]
    privacy_contact = _privacy_contact()
    applications_enabled = _public_applications_ready(role)
    return {
        "status": "success",
        "role": {
            "public_slug": role["public_slug"],
            "role_code": role["role_code"],
            "company_name": contract["company_name"],
            "role_title": contract["role_title"],
            "role_summary": contract["role_summary"],
            "location_envelope": contract["location_envelope"],
            "compensation_envelope": contract["compensation_envelope"],
            "public_job_description": contract["public_job_description"],
            "criteria": [{"criterion_id": item["criterion_id"],
                          "label": item["label"],
                          "description": item["description"]}
                         for item in contract["criteria"]],
            "target_date": contract["target_date"],
            "notice_path": f"/jobs/{role['public_slug']}/notice",
            "privacy_contact": privacy_contact,
            "published_at": role.get("published_at"),
        },
        "application_token": (_public_application_token(role, policy)
                              if applications_enabled else ""),
        "applications_enabled": applications_enabled,
    }


def register(app: FastAPI) -> None:
    @app.get("/jobs/{public_slug}", include_in_schema=False)
    async def public_job_page(public_slug: str):
        if not _PUBLIC_SLUG.fullmatch(public_slug):
            return JSONResponse({"error": "not found"}, status_code=404)
        path = Path(__file__).resolve().parent / "static" / "hiring-apply.html"
        return FileResponse(path, headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; form-action 'self'; "
                "frame-ancestors 'none'; base-uri 'none'"),
            "X-Content-Type-Options": "nosniff",
        })

    @app.get("/jobs/{public_slug}/notice", include_in_schema=False)
    async def public_job_notice(public_slug: str):
        resolved = await _public_role(public_slug)
        if not resolved:
            return HTMLResponse("Not found", status_code=404)
        role, policy = resolved
        contract = policy["contract"]
        contact = _privacy_contact()
        if not contact:
            return HTMLResponse("Privacy contact is not configured.", status_code=503)
        company = html.escape(str(contract["company_name"]))
        title = html.escape(str(contract["role_title"]))
        safe_contact = html.escape(contact)
        retention = html.escape(str(contract["retention_policy_id"]))
        body = f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Candidate privacy notice · {company}</title><style>body{{max-width:70ch;margin:48px auto;
padding:0 20px;font:16px/1.65 system-ui,sans-serif}}h1,h2{{line-height:1.25}}</style></head>
<body><main><h1>Candidate privacy notice</h1><p><strong>{company}</strong> is receiving
applications for <strong>{title}</strong> through Co-Founder.</p><h2>What is processed</h2>
<p>Your contact details, resume, optional note, role-scoped application receipt, and exact
job-related evidence cited from your submission. Candidate identity is encrypted separately.
Applications are not added to general founder memory or search.</p><h2>How it is used</h2>
<p>Alex organizes evidence against the published role criteria. Alex does not rank candidates,
recommend a hiring outcome, or make an employment decision. An authorized human reviews the
evidence and makes every advance, hold, evidence-request, or decline decision.</p>
<h2>Retention and your choices</h2><p>This role uses retention policy <code>{retention}</code>.
You may ask for access, correction, export, withdrawal, accommodation, human contact, or
deletion, subject to applicable law and a documented legal hold. Contact
<a href=\"mailto:{safe_contact}\">{safe_contact}</a>. External job boards, if used separately,
have their own notices and retention.</p><p><a href=\"/jobs/{html.escape(public_slug)}\">Return to the role</a></p>
</main></body></html>"""
        return HTMLResponse(body, headers={"Cache-Control": "no-store",
                                           "X-Content-Type-Options": "nosniff"})

    @app.get("/api/public/hiring/roles/{public_slug}")
    async def public_role(public_slug: str):
        resolved = await _public_role(public_slug)
        if not resolved:
            return JSONResponse({"error": "not found"}, status_code=404)
        role, policy = resolved
        return JSONResponse(
            _safe_public_contract(role, policy),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/api/public/hiring/roles/{public_slug}/applications")
    async def public_application(
            public_slug: str,
            request: Request,
            name: str = Form(..., min_length=1, max_length=160),
            email: str = Form(..., min_length=3, max_length=254),
            phone: str = Form("", max_length=80),
            cover_note: str = Form("", max_length=4000),
            application_token: str = Form(..., min_length=64, max_length=64),
            client_request_id: str = Form(..., min_length=8, max_length=128),
            notice_accepted: bool = Form(...),
            website: str = Form("", max_length=200),
            resume: UploadFile = File(...)):
        try:
            content_length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > _MAX_APPLICATION_BYTES + 65_536:
            return JSONResponse(
                {"status": "error", "error": True,
                 "error_code": "request_too_large",
                 "message": "Resume files must be 10 MB or smaller."},
                status_code=413)
        resolved = await _public_role(public_slug)
        if not resolved:
            return JSONResponse({"error": "not found"}, status_code=404)
        role, policy = resolved
        if not _public_applications_ready(role):
            return JSONResponse(
                {"status": "error", "error": True,
                 "error_code": "public_applications_disabled",
                 "message": "This role is not accepting applications."},
                status_code=403)
        if website:
            # Honeypot responses disclose no detection signal.
            return {"status": "success", "application_received": True}
        if (not notice_accepted or not _EMAIL.fullmatch(email.strip())
                or not _PUBLIC_REQUEST_ID.fullmatch(client_request_id)
                or not hmac.compare_digest(
                    application_token, _public_application_token(role, policy))):
            return JSONResponse(
                {"status": "error", "error": True,
                 "error_code": "invalid_application",
                 "message": "Check the application fields and try again."},
                status_code=400)
        data = await resume.read(_MAX_APPLICATION_BYTES + 1)
        await resume.close()
        if len(data) > _MAX_APPLICATION_BYTES:
            return JSONResponse(
                {"status": "error", "error": True,
                 "error_code": "request_too_large",
                 "message": "Resume files must be 10 MB or smaller."},
                status_code=413)
        checked = document_ingestion.validate_upload(
            data, resume.filename or "resume", resume.content_type or "application/octet-stream")
        if checked.get("error"):
            return _response(checked)
        suffix = str(checked["detected_extension"])
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(data)
                temp_path = handle.name
            extracted = await asyncio.to_thread(
                document_ingestion.extract_chunks, temp_path, suffix)
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
        if extracted.get("error"):
            return _response(extracted)
        criteria = [Criterion.model_validate(item) for item in policy["contract"]["criteria"]]
        blocks = hiring_application_intake.map_application_blocks(
            chunks=extracted["chunks"], criteria=criteria, cover_note=cover_note)
        source_sha256 = "sha256:" + hashlib.sha256(data).hexdigest()
        application_payload_hash = canonical_hash({
            "schema_version": 1, "role_id": role["role_id"],
            "name": name.strip(), "email": email.strip().lower(),
            "phone": phone.strip(),
            "cover_note_hash": canonical_hash({"cover_note": cover_note.strip()}),
            "source_sha256": source_sha256, "notice_accepted": True,
        })
        provider_message_id = stable_id(
            "publicapp", role["role_id"], client_request_id)
        event_id = stable_id("hevent", role["role_id"], provider_message_id)
        now = utc_now()
        event = {
            "schema_version": 2, "external_event_id": event_id,
            "event_id": event_id, "workspace_id": role["workspace_id"],
            "founder_id": role["workspace_id"], "role_id": role["role_id"],
            "connection_id": "cofounder_public_application",
            "connector_id": "cofounder_public_application",
            "event_kind": "HIRING_PUBLIC_APPLICATION",
            "provider_event_id": provider_message_id,
            "provider_thread_id": provider_message_id,
            "processing_status": "RECEIVED", "business_disposition": "RECEIVED",
            "payload_hash": application_payload_hash,
            "source_sha256": source_sha256,
            "notice_policy_id": policy["contract"]["notice_policy_id"],
            "notice_accepted": True, "received_at": now, "created_at": now,
            "synthetic": False, "data_mode": "LIVE_INTERNAL", "version": 1,
        }
        created_event = await production_store().create("external_events", event_id, event)
        if not created_event:
            existing_event = await production_store().get("external_events", event_id)
            if (not existing_event
                    or existing_event.get("payload_hash") != application_payload_hash
                    or existing_event.get("role_id") != role["role_id"]):
                return JSONResponse(
                    {"status": "error", "error": True,
                     "error_code": "idempotency_conflict",
                     "message": "That application request ID was already used."},
                    status_code=409)
        services = _services()
        if not services:
            return JSONResponse(
                {"status": "error", "error": True,
                 "error_code": "hiring_encryption_unavailable",
                 "message": "Applications are temporarily unavailable."},
                status_code=503)
        storage_name = (
            f"hiring/{role['workspace_id']}/{role['role_id']}/"
            f"{provider_message_id}/resume{suffix}")
        await asyncio.to_thread(storage.save_bytes, storage_name, data)
        ingested = await services[0].ingest_application(
            role_id=role["role_id"], provider_message_id=provider_message_id,
            provider_thread_id=provider_message_id,
            identity_fields={"name": name.strip(), "email": email.strip().lower(),
                             "phone": phone.strip()},
            blocks=blocks, source_sha256=source_sha256,
            external_event_id=event_id,
            provenance={"synthetic": False, "data_mode": "LIVE_INTERNAL"},
            source_filename=f"resume{suffix}",
            source_content_type=str(checked["detected_content_type"]),
            storage_name=storage_name)
        if ingested.get("error"):
            application_id = stable_id(
                "candidateapp", role["role_id"], provider_message_id)
            if not await production_store().get(
                    "candidate_applications", application_id):
                await asyncio.to_thread(storage.delete_artifact, storage_name)
            return _response(ingested)
        current_event = await production_store().get("external_events", event_id)
        if current_event and current_event.get("processing_status") != "APPLIED":
            await production_store().compare_and_set(
                "external_events", event_id, int(current_event["version"]), {
                    "processing_status": "APPLIED",
                    "business_disposition": "APPLIED", "applied_at": utc_now(),
                    "candidate_application_id": ingested.get("candidate_application_id"),
                })
        return JSONResponse({
            "status": "success", "application_received": True,
            "duplicate": bool(ingested.get("duplicate")),
            "application_reference": ingested.get("candidate_code"),
        }, headers={"Cache-Control": "no-store"})

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
        gate = authorize(principal, "read_role", role_id=role_id)
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
                or authorize(principal, "read_role", role_id=str(sandbox.get("role_id") or "")).get("error")):
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
    # could never be created. Each one is owner-only inside the service and
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
        if principal.role is not WorkspaceRole.OWNER:
            rows = [row for row in rows if row["role_id"] in principal.role_grants]
        return {"status": "success", "roles": rows}

    @app.get("/api/hiring/roles/{role_id}")
    async def get_role(request: Request, role_id: str):
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        role = await production_store().get("hiring_roles", role_id)
        if not role or role.get("workspace_id") != principal.workspace_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        gate = authorize(principal, "read_role", role_id=role_id)
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
        # Candidate visibility is decided by the one code-owned gate, never by
        # a role name special-cased here. An OBSERVER holding a role grant may
        # read the role but no candidate record; an INTERVIEWER sees only
        # assigned candidates.
        candidates = [
            row for row in candidates
            if not authorize(
                principal, "read_candidate", role_id=role_id,
                candidate_application_id=row["candidate_application_id"],
            ).get("error")]
        policies.sort(key=lambda item: int(item.get("sequence", 0)))
        return {"status": "success", "role": role, "candidates": candidates,
                "policy_versions": policies, "policy_impacts": impacts}

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
            change_reason=payload.change_reason,
            client_request_id=payload.client_request_id))

    @app.get("/api/hiring/roles/{role_id}/policy-impact/{policy_id}")
    async def policy_impact(request: Request, role_id: str, policy_id: str):
        principal = await _actor(request)
        if isinstance(principal, dict):
            return _response(principal)
        role = await production_store().get("hiring_roles", role_id)
        if not role or role.get("workspace_id") != principal.workspace_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        gate = authorize(principal, "read_role", role_id=role_id)
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

    @app.post("/api/hiring/roles/{role_id}/publish")
    async def publish_internal_role(request: Request, role_id: str,
                                    payload: PublishInternalRoleRequest):
        denied = _mutation_allowed(request)
        if denied.get("error"):
            return _response(denied)
        principal = await _actor(request)
        services = _services()
        if isinstance(principal, dict):
            return _response(principal)
        if not services:
            return JSONResponse(
                {"status": "error", "error": True,
                 "error_code": "hiring_encryption_unavailable",
                 "message": "Hiring identity encryption is not configured."},
                status_code=503)
        return _response(await services[0].publish_internal_role(
            principal=principal, role_id=role_id,
            expected_version=payload.expected_role_version,
            client_request_id=payload.client_request_id))

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
        gate = authorize(principal, "prepare_role", role_id=role_id)
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
        gate = authorize(principal, "prepare_role", role_id=role_id)
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
        if principal.role is not WorkspaceRole.OWNER or not services:
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
        if principal.role is not WorkspaceRole.OWNER or not services:
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
                                 "message": "Synthetic fixture processing is disabled."},
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
        return _response(await services[0].candidate_detail(
            principal=principal, application_id=application_id))

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
        revealed = await services[0].identity_vault.reveal_identity(
            identity_id=application["candidate_id"],
            workspace_id=application["workspace_id"], role_id=application["role_id"],
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
            "result": "success", "detail": "authorized synthetic identity reveal",
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
            gate = authorize(principal, "read_role", role_id=run["domain_ref"])
        else:
            application = await production_store().get(
                "candidate_applications", run["domain_ref"])
            if not application:
                return JSONResponse({"error": "not found"}, status_code=404)
            gate = authorize(
                principal, "read_candidate", role_id=application["role_id"],
                candidate_application_id=application["candidate_application_id"])
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
