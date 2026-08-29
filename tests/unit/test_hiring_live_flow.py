"""End-to-end checks for description-driven roles and public applications."""

from __future__ import annotations

import hashlib
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import hiring_routes
from services import hiring_policy_service, hiring_role_intake, storage
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import InMemoryDurableStore
from services.hiring_approval_service import request_approval, resolve_approval
from services.hiring_contracts import RoleContract
from services.hiring_data_rights import HiringDataRightsService
from services.hiring_identity_vault import CandidateIdentityVault, fixture_key_wrapper
from services.hiring_service import HiringService
from services.hiring_workflow_adapter import HiringWorkflowAdapter
from services.workflow_runtime import WorkflowRuntime


def _owner(workspace_id: str = "workspace_live") -> ActorPrincipal:
    return ActorPrincipal(
        actor_id="member_owner", workspace_id=workspace_id,
        role=WorkspaceRole.OWNER, role_grants=frozenset(),
        candidate_assignments=frozenset(), interview_assignments=frozenset(),
        session_auth_time=int(time.time()), membership_version=1)


def _contract(*, title: str = "Senior Platform Engineer") -> RoleContract:
    return RoleContract.model_validate({
        "schema_version": 1,
        "company_name": "Acme Labs",
        "role_title": title,
        "role_summary": "Own reliable developer infrastructure and production delivery.",
        "headcount_target": 1,
        "target_date": "2027-01-30",
        "location_envelope": ["Remote in Europe"],
        "compensation_envelope": "EUR 90,000-110,000",
        "criteria": [{
            "criterion_id": "platform_delivery",
            "label": "Platform delivery",
            "description": "Delivered reliable developer infrastructure into production.",
            "evidence_examples": ["Production platform delivery with measured reliability"],
            "approved_question_ids": ["platform_delivery_question"],
        }],
        "interview_plan": [{
            "question_id": "platform_delivery_question",
            "criterion_id": "platform_delivery",
            "text": "Describe a production platform you directly delivered.",
            "rubric": ["Direct scope", "Measured reliability"],
        }],
        "public_job_description": (
            "Acme Labs is hiring a Senior Platform Engineer to deliver reliable "
            "developer infrastructure into production. Remote in Europe."),
        "approved_reason_codes": ["JOB_EVIDENCE_SUFFICIENT", "MORE_EVIDENCE_REQUIRED"],
        "prohibited_criteria": ["Protected characteristics", "Culture fit", "Prestige"],
        "notice_policy_id": "notice_live_v1",
        "retention_policy_id": "retention_live_v1",
        "jurisdiction_policy_id": "jurisdiction_live_v1",
    })


def _service(store: InMemoryDurableStore) -> HiringService:
    key = bytes(range(32))
    wrap, unwrap = fixture_key_wrapper(key)
    vault = CandidateIdentityVault(
        wrap_key=wrap, unwrap_key=unwrap, dedup_key=key,
        store=store, allow_live=True)
    runtime = WorkflowRuntime(store, domain_adapter=HiringWorkflowAdapter())
    return HiringService(store=store, identity_vault=vault, runtime=runtime)


async def _published_role(store: InMemoryDurableStore, hiring: HiringService):
    owner = _owner()
    contract = _contract()
    created = await hiring.create_role(
        principal=owner, contract=contract, client_request_id="live_role_request_1",
        provenance={"synthetic": False, "data_mode": "LIVE_INTERNAL"},
        source_description_hash="sha256:" + "1" * 64)
    role = created["role"]
    proposed = await hiring_policy_service.propose_policy(
        principal=owner, role_id=role["role_id"], contract=contract,
        change_reason="Founder role description",
        client_request_id="live_policy_request_1", store=store)
    exact_action = {
        "policy_version_id": proposed["policy_version_id"],
        "policy_hash": proposed["canonical_hash"],
    }
    approval = await request_approval(
        principal=owner, run_id=role["run_id"], role_id=role["role_id"],
        policy_version_id=proposed["policy_version_id"],
        action_kind="ACTIVATE_ROLE_POLICY", exact_action=exact_action,
        client_request_id="live_policy_approval_1", store=store)
    await resolve_approval(
        principal=owner, approval_id=approval["approval_id"],
        decision="GRANT", store=store)
    activated = await hiring_policy_service.approve_policy(
        principal=owner, role_id=role["role_id"],
        policy_version_id=proposed["policy_version_id"],
        expected_role_version=role["version"], approval_id=approval["approval_id"],
        store=store)
    published = await hiring.publish_internal_role(
        principal=owner, role_id=role["role_id"],
        expected_version=activated["role_version"],
        client_request_id="live_role_publish_1")
    return owner, contract, await store.get("hiring_roles", role["role_id"]), published


@pytest.mark.asyncio
async def test_description_draft_is_real_and_rejects_protected_criteria():
    description = (
        "Acme Labs needs a senior platform engineer, remote in Europe, to own "
        "developer infrastructure and production reliability.")
    hiring_role_intake.set_role_contract_generator(
        lambda supplied, _context: _contract(
            title="Senior Platform Engineer").model_dump(mode="json")
        if supplied == description else {})
    drafted = await hiring_role_intake.draft_role_contract(description=description)
    assert drafted["contract"].company_name == "Acme Labs"
    assert drafted["contract"].role_title == "Senior Platform Engineer"
    assert drafted["description_hash"].startswith("sha256:")

    unsafe = _contract().model_copy(update={
        "criteria": [_contract().criteria[0].model_copy(
            update={"description": "Prefer young candidates with platform delivery."})]})
    hiring_role_intake.set_role_contract_generator(
        lambda _description, _context: unsafe.model_dump(mode="json"))
    refused = await hiring_role_intake.draft_role_contract(description=description)
    assert refused["error_code"] == "prohibited_role_criterion"
    hiring_role_intake.set_role_contract_generator(None)


@pytest.mark.asyncio
async def test_live_role_approval_publication_is_exact_idempotent_and_fenced(monkeypatch):
    monkeypatch.setenv("HIRING_ENABLE_ROLE_INTAKE", "1")
    store = InMemoryDurableStore()
    hiring = _service(store)
    _owner_principal, _role_contract, role, published = await _published_role(store, hiring)
    assert role["synthetic"] is False and role["data_mode"] == "LIVE_INTERNAL"
    assert role["role_state"] == "PUBLISHED"
    assert published["public_path"] == f"/jobs/{role['public_slug']}"
    waits = await store.list("waits", filters={"run_id": role["run_id"]})
    assert any(wait["wait_kind"] == "APPLICATION_PUBLIC" for wait in waits)

    duplicate = await hiring.publish_internal_role(
        principal=_owner(), role_id=role["role_id"],
        expected_version=1, client_request_id="live_role_publish_1")
    assert duplicate["duplicate"] is True
    stale = await hiring.publish_internal_role(
        principal=_owner(), role_id=role["role_id"],
        expected_version=1, client_request_id="live_role_publish_stale")
    assert stale["error_code"] == "version_conflict"


@pytest.mark.asyncio
async def test_public_application_attaches_to_role_and_conservative_assessment(
        monkeypatch, tmp_path):
    monkeypatch.setenv("HIRING_ENABLE_ROLE_INTAKE", "1")
    monkeypatch.setenv("HIRING_ENABLE_PUBLIC_APPLICATIONS", "1")
    monkeypatch.setenv("APP_SESSION_SECRET", "test-public-hiring-secret")
    monkeypatch.setenv("HIRING_PRIVACY_CONTACT", "privacy@acme.example")
    monkeypatch.setenv("ARTIFACT_SERVICE_URI", f"file://{tmp_path}")
    storage._ROOT = None
    store = InMemoryDurableStore()
    hiring = _service(store)
    _owner_principal, contract, role, _published = await _published_role(store, hiring)
    monkeypatch.setattr(hiring_routes, "production_store", lambda: store)
    monkeypatch.setattr(hiring_routes, "_services", lambda: (hiring, None))
    app = FastAPI()
    hiring_routes.register(app)

    with TestClient(app) as client:
        page = client.get(f"/jobs/{role['public_slug']}")
        assert page.status_code == 200
        assert "Apply for this role" in page.text
        notice = client.get(f"/jobs/{role['public_slug']}/notice")
        assert notice.status_code == 200
        assert "privacy@acme.example" in notice.text
        public = client.get(f"/api/public/hiring/roles/{role['public_slug']}")
        assert public.status_code == 200
        public_body = public.json()
        assert public_body["role"]["role_title"] == contract.role_title
        assert "workspace_id" not in public_body["role"]
        form = {
            "name": "Ada Candidate", "email": "ada@example.com", "phone": "",
            "cover_note": "I delivered production platform infrastructure with measured reliability.",
            "application_token": public_body["application_token"],
            "client_request_id": "public_application_request_1",
            "notice_accepted": "true", "website": "",
        }
        resume_bytes = (
            b"Led production platform delivery for developer infrastructure "
            b"and improved measured reliability."
        )
        invalid_form = {**form, "application_token": "0" * 64,
                        "client_request_id": "public_application_invalid_1"}
        invalid = client.post(
            f"/api/public/hiring/roles/{role['public_slug']}/applications",
            data=invalid_form,
            files={"resume": ("resume.txt", resume_bytes, "text/plain")})
        assert invalid.status_code == 400
        first = client.post(
            f"/api/public/hiring/roles/{role['public_slug']}/applications",
            data=form, files={"resume": ("resume.txt", resume_bytes, "text/plain")})
        assert first.status_code == 200, first.text
        assert first.json()["application_received"] is True
        duplicate = client.post(
            f"/api/public/hiring/roles/{role['public_slug']}/applications",
            data=form, files={"resume": ("resume.txt", resume_bytes, "text/plain")})
        assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True
        changed = client.post(
            f"/api/public/hiring/roles/{role['public_slug']}/applications",
            data=form, files={"resume": ("resume.txt", b"Different content", "text/plain")})
        assert changed.status_code == 409

    applications = await store.list(
        "candidate_applications", filters={"role_id": role["role_id"]})
    assert len(applications) == 1
    application = applications[0]
    assert application["candidate_state"] == "AWAITING_HUMAN_DECISION"
    assert application["data_mode"] == "LIVE_INTERNAL"
    identity = await store.get("candidate_identities", application["candidate_id"])
    assert identity["notice_receipts"][0]["notice_policy_id"] == "notice_live_v1"
    assert identity["consent_receipts"][0]["source_event_id"] == application["source_event_id"]
    assessment = await store.get(
        "candidate_assessments", application["current_assessment_id"])
    assert assessment["role_id"] == role["role_id"]
    assert assessment["criteria"][0]["status"] == "PARTIAL"
    assert not ({"score", "rank", "recommendation"} & set(assessment))
    event = (await store.list(
        "external_events", filters={"candidate_application_id":
                                     application["candidate_application_id"]}))[0]
    assert event["processing_status"] == "APPLIED"
    assert event["source_sha256"] == (
        "sha256:" + hashlib.sha256(resume_bytes).hexdigest())
    assert event["payload_hash"].startswith("sha256:")
    artifact = (await store.list(
        "hiring_candidate_artifacts",
        filters={"candidate_application_id": application["candidate_application_id"]}))[0]
    assert storage.exists(artifact["storage_name"])
    rights = HiringDataRightsService(
        identity_vault=hiring.identity_vault, store=store)
    plan = await rights.deletion_plan(
        principal=_owner(), application_id=application["candidate_application_id"])
    deleted = await rights.execute_deletion(
        principal=_owner(), application_id=application["candidate_application_id"],
        expected_inventory_hash=plan["inventory_hash"],
        client_request_id="live_candidate_delete_1")
    assert deleted["status"] == "success"
    assert not storage.exists(artifact["storage_name"])
