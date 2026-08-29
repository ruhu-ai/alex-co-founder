"""Founder-safe Hiring entry from Alex and the dedicated workspace."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.co_founder import state_schema as ss
from agents.co_founder.tools import hiring as hiring_tools
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import InMemoryDurableStore
from services.hiring_identity_vault import CandidateIdentityVault, fixture_key_wrapper
from services.hiring_public_intake import (
    HiringPublicIntakeService,
    build_public_intake_projection,
)
from services import hiring_policy_service
from services.hiring_approval_service import request_approval, resolve_approval
from services.hiring_role_draft import build_contract
from services.hiring_run_answer import HiringCandidateConversationService
from services.hiring_service import HiringService
from services.hiring_workflow_adapter import HiringWorkflowAdapter
from services.workflow_runtime import WorkflowRuntime


def _package() -> dict:
    return build_contract(
        company_name="Example Co", role_title="Deployment Engineer",
        role_summary="Own reliable customer deployments.", headcount_target=1,
        target_date="2099-01-30", location="Nigeria",
        work_arrangement="Remote", employment_type="Full-time employee",
        compensation_envelope="Founder-reviewed band",
        required_criteria=["Customer deployment delivery"],
        preferred_criteria=["Experience improving deployment playbooks"],
        relevant_experience=["Owned a customer-facing production deployment"],
        responsibilities=["Lead deployments from discovery through launch"],
        success_outcomes=["Accountable production launches"],
        benefits=["Learning and development support"],
        hiring_process=["Structured role interview", "Founder decision"],
        application_instructions="Apply through the published role page.",
        accessibility_statement=(
            "Candidates may request an adjustment for the interview process."),
        public_job_description=(
            "Join Example Co to lead customer deployments from discovery "
            "through accountable production outcomes."),
    )


def _service(store: InMemoryDurableStore) -> HiringService:
    wrap, unwrap = fixture_key_wrapper(bytes(range(32)))
    vault = CandidateIdentityVault(
        wrap_key=wrap, unwrap_key=unwrap, dedup_key=bytes(range(32)),
        store=store)
    return HiringService(
        store=store, identity_vault=vault,
        runtime=WorkflowRuntime(store, domain_adapter=HiringWorkflowAdapter()))


def _founder() -> ActorPrincipal:
    return ActorPrincipal(
        actor_id="founder_actor", workspace_id="workspace_test",
        role=WorkspaceRole.FOUNDER, session_auth_time=0,
        membership_version=1)


def test_builder_requires_readable_job_description_and_rejects_proxies():
    package = _package()
    assert package["status"] == "success"
    description = package["role_description"]
    assert description["purpose"] == "Own reliable customer deployments."
    assert description["responsibilities"]
    assert description["required_qualifications"]
    assert description["employment_type"] == "Full-time employee"
    post = description["candidate_facing_job_post"]
    for heading in (
            "## Role overview", "## What success looks like",
            "## Responsibilities", "## Must-have qualifications",
            "## Preferred qualifications", "## Relevant experience",
            "## Working model", "## Compensation and benefits",
            "## Hiring process", "## Fair and accessible process",
            "## How to apply"):
        assert heading in post
    assert "accountable production" in post
    assert "Apply through the published role page." in post

    unsafe = build_contract(
        company_name="Example Co", role_title="Engineer",
        role_summary="Find culture fit.", headcount_target=1,
        target_date="2099-01-30", location="Nigeria",
        work_arrangement="Remote", employment_type="Full-time",
        compensation_envelope="Reviewed", required_criteria=["Good engineer"],
        responsibilities=[], success_outcomes=[],
        relevant_experience=[],
        public_job_description="Build the product.")
    assert unsafe["error_code"] == "prohibited_hiring_criterion"


def test_builder_blocks_material_gaps_and_does_not_invent_optional_terms():
    missing = build_contract(
        company_name="Example Co", role_title="Engineer",
        role_summary="Build reliable systems.", headcount_target=1,
        target_date="2099-01-30", location="Nigeria",
        work_arrangement="Remote", employment_type="Full-time",
        compensation_envelope="", required_criteria=["Production delivery"],
        responsibilities=[], success_outcomes=[], relevant_experience=[],
        public_job_description="")
    assert missing["status"] == "needs_information"
    assert set(missing["missing_fields"]) >= {
        "candidate-facing responsibilities", "role success outcomes",
        "relevant experience expectations"}

    safe = build_contract(
        company_name="Example Co", role_title="Engineer",
        role_summary="Build reliable systems.", headcount_target=1,
        target_date="2099-01-30", location="Nigeria",
        work_arrangement="Remote", employment_type="Full-time",
        compensation_envelope="", required_criteria=["Production delivery"],
        responsibilities=["Own bounded production work"],
        success_outcomes=["Reliable releases"],
        preferred_criteria=["Experience improving release workflows"],
        relevant_experience=["Shipped one production system"],
        application_instructions="Apply through the published role page.",
        public_job_description="")
    assert safe["status"] == "success"
    description = safe["role_description"]
    assert "Compensation" in description["honest_unknowns"]
    assert "Equal-opportunity wording" in description["honest_unknowns"]
    post = description["candidate_facing_job_post"]
    assert "## Compensation and benefits" not in post
    assert "equal opportunity employer" not in post.lower()
    assert "Apply through the published role page." in post


@pytest.mark.asyncio
async def test_founder_draft_is_non_synthetic_and_cannot_publish():
    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    created = await service.create_founder_draft_role(
        principal=_founder(), contract=package["contract"],
        role_description=package["role_description"],
        client_request_id="founder_draft_test")

    role = created["role"]
    assert role["role_state"] == "DRAFT"
    assert role["synthetic"] is False
    assert role["fixture_id"] is None
    assert role["creation_mode"] == "FOUNDER_DRAFT"
    assert role["publication_allowed"] is False
    assert role["candidate_processing_allowed"] is False
    assert role["draft_contract"]["public_job_description"] == (
        package["contract"].public_job_description)
    assert role["role_description"]["candidate_facing_job_post"]

    refused = await service.record_publication(
        principal=_founder(), role_id=role["role_id"],
        destination="MANUAL_BROWSER", public_url="https://example.com/job",
        expected_version=role["version"], client_request_id="publish_test",
        attestation="Founder manually published this post.")
    assert refused["error_code"] == "publication_disabled"


@pytest.mark.asyncio
async def test_exact_complete_draft_is_promoted_but_not_published_by_approval():
    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    created = await service.create_founder_draft_role(
        principal=_founder(), contract=package["contract"],
        role_description=package["role_description"],
        client_request_id="founder_reviewable_draft")
    role = created["role"]
    proposed = await hiring_policy_service.propose_policy(
        principal=_founder(), role_id=role["role_id"],
        contract=package["contract"],
        role_description=package["role_description"],
        change_reason="Founder completed the candidate-facing draft.",
        client_request_id="founder_reviewable_policy", store=store)
    preview = await service.get_role_draft_preview(
        principal=_founder(), role_id=role["role_id"],
        policy_version_id=proposed["policy_version_id"])
    assert preview["preview"] is True and preview["live"] is False
    assert preview["open_role"]["application_instructions"] == (
        package["role_description"]["application_instructions"])
    assert preview["open_role"]["intake"]["form_available"] is False
    exact_action = {
        "policy_version_id": proposed["policy_version_id"],
        "policy_hash": proposed["canonical_hash"],
        "role_description_hash": proposed["role_description_hash"],
    }
    approval = await request_approval(
        principal=_founder(), run_id=role["run_id"], role_id=role["role_id"],
        policy_version_id=proposed["policy_version_id"],
        action_kind="ACTIVATE_ROLE_POLICY", exact_action=exact_action,
        client_request_id="founder_reviewable_approval", store=store)
    await resolve_approval(
        principal=_founder(), approval_id=approval["approval_id"],
        decision="GRANT", store=store)
    activated = await hiring_policy_service.approve_policy(
        principal=_founder(), role_id=role["role_id"],
        policy_version_id=proposed["policy_version_id"],
        expected_role_version=role["version"],
        approval_id=approval["approval_id"], store=store)

    assert activated["status"] == "success"
    committed = await store.get("hiring_roles", role["role_id"])
    assert committed["role_state"] == "APPROVED"
    assert committed["publication_allowed"] is True
    assert committed["role_description"] == package["role_description"]
    assert (await service.get_public_role(role["role_id"]))["error_code"] == (
        "open_role_not_live")
    tampered_description = dict(committed["role_description"])
    tampered_description["preferred_qualifications"] = []
    tampered = await store.compare_and_set(
        "hiring_roles", role["role_id"], committed["version"],
        {"role_description": tampered_description})
    refused_publication = await service.record_publication(
        principal=_founder(), role_id=role["role_id"],
        destination="MANUAL_BROWSER",
        public_url="https://example.test/jobs/deployment-engineer",
        expected_version=tampered["version"],
        client_request_id="tampered_publication",
        attestation="Founder manually published the reviewed page.")
    assert refused_publication["error_code"] == "role_description_incomplete"


def test_prepare_tool_presents_exact_package_without_creating_role():
    context = SimpleNamespace(
        state={ss.K_USER_PROFILE_ID: "workspace_test",
               ss.K_ACTOR_ID: "founder_actor"},
        session=SimpleNamespace(id="session_test", user_id="workspace_test"),
        user_id="workspace_test")
    result = hiring_tools.prepare_hiring_role_brief(
        company_name="Example Co", role_title="Deployment Engineer",
        role_summary="Own reliable customer deployments.", headcount_target=1,
        target_date="2099-01-30", location="Nigeria",
        work_arrangement="Remote", employment_type="Full-time employee",
        compensation_envelope="Founder-reviewed band",
        required_criteria=["Customer deployment delivery"],
        responsibilities=["Lead deployments"],
        success_outcomes=["Accountable launches"],
        preferred_criteria=["Experience improving deployment playbooks"],
        relevant_experience=["Owned a production deployment"],
        benefits=[], hiring_process=[],
        application_instructions="Apply through the published role page.",
        equal_opportunity_statement="", accessibility_statement="",
        public_job_description="Full candidate-facing copy.",
        tool_context=context)

    assert result["status"] == "success"
    assert result["created"] is False
    assert "Full candidate-facing copy." in (
        result["role_description"]["candidate_facing_job_post"])
    assert "## Responsibilities" in (
        result["role_description"]["candidate_facing_job_post"])
    assert context.state[ss.K_HIRING_ROLE_PROPOSAL]["status"] == "PRESENTED"
    assert result["confirmation_prompt"].endswith(
        "approve, publish, email, source, rank, or decide anything.")


@pytest.mark.asyncio
async def test_create_tool_revalidates_founder_and_exact_confirmation(monkeypatch):
    store = InMemoryDurableStore()
    await store.create("workspace_members", "membership_test", {
        "membership_id": "membership_test", "actor_id": "founder_actor",
        "workspace_id": "workspace_test", "role": "FOUNDER",
        "status": "ACTIVE", "version": 1,
    })
    context = SimpleNamespace(
        state={ss.K_USER_PROFILE_ID: "workspace_test",
               ss.K_ACTOR_ID: "founder_actor"},
        session=SimpleNamespace(id="session_test", user_id="workspace_test"),
        user_id="workspace_test")
    prepared = hiring_tools.prepare_hiring_role_brief(
        company_name="Example Co", role_title="Deployment Engineer",
        role_summary="Own reliable customer deployments.", headcount_target=1,
        target_date="2099-01-30", location="Nigeria",
        work_arrangement="Remote", employment_type="Full-time employee",
        compensation_envelope="Founder-reviewed band",
        required_criteria=["Customer deployment delivery"],
        responsibilities=["Lead deployments"],
        success_outcomes=["Accountable launches"],
        preferred_criteria=["Experience improving deployment playbooks"],
        relevant_experience=["Owned a production deployment"],
        benefits=[], hiring_process=[],
        application_instructions="Apply through the published role page.",
        equal_opportunity_statement="", accessibility_statement="",
        public_job_description="Full candidate-facing copy.",
        tool_context=context)
    monkeypatch.setattr(hiring_tools, "production_store", lambda: store)

    from app import hiring_routes
    from services import hiring_policy_service

    monkeypatch.setattr(hiring_routes, "_services", lambda: (_service(store), object()))

    async def _propose_policy(**kwargs):
        return {"status": "success", "policy_status": "PROPOSED",
                "policy_version_id": "policy_test"}

    monkeypatch.setattr(hiring_policy_service, "propose_policy", _propose_policy)

    refused = await hiring_tools.create_hiring_draft(
        prepared["proposal_id"], prepared["contract_hash"], False, context)
    assert refused["error_code"] == "founder_confirmation_required"

    result = await hiring_tools.create_hiring_draft(
        prepared["proposal_id"], prepared["contract_hash"], True, context)
    assert result["status"] == "success"
    assert result["role_state"] == "DRAFT"
    assert result["external_actions"] == []

    await store.compare_and_set(
        "workspace_members", "membership_test", 1, {"status": "REVOKED"})
    context.state[ss.K_HIRING_ROLE_PROPOSAL]["status"] = "PRESENTED"
    denied = await hiring_tools.create_hiring_draft(
        prepared["proposal_id"], prepared["contract_hash"], True, context)
    assert denied["error_code"] == "interactive_founder_required"


@pytest.mark.asyncio
async def test_public_role_requires_exact_manual_receipt_and_exposes_only_candidate_fields():
    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    contract = package["contract"]
    policy_id = "policy_public_test"
    policy_hash = hiring_tools.canonical_hash(contract)
    role_id = "role_public_test"
    await store.create("hiring_policy_versions", policy_id, {
        "policy_version_id": policy_id, "role_id": role_id,
        "workspace_id": "workspace_test", "status": "APPROVED",
        "canonical_hash": policy_hash,
        "role_description_hash": hiring_tools.canonical_hash(
            package["role_description"]),
        "contract": contract.model_dump(mode="json"), "version": 1,
    })
    role = {
        "role_id": role_id, "workspace_id": "workspace_test",
        "company_name": contract.company_name, "role_title": contract.role_title,
        "role_state": "PUBLISHED", "current_policy_version_id": policy_id,
        "current_policy_hash": policy_hash,
        "role_description": package["role_description"],
        "publication_package": {"application_address": "apply@example.test"},
        "publication_receipts": [], "publication_allowed": True,
        "synthetic": False, "version": 1,
    }
    await store.create("hiring_roles", role_id, role)
    hidden = await service.get_public_role(role_id)
    assert hidden["error_code"] == "open_role_not_live"

    receipt = {
        "receipt_id": "receipt_public_test", "policy_version_id": policy_id,
        "policy_hash": policy_hash, "automated_publication": False,
        "public_url": "https://example.test/jobs/deployment-engineer",
        "recorded_at": "2099-01-01T00:00:00+00:00",
    }
    await store.compare_and_set(
        "hiring_roles", role_id, 1, {"publication_receipts": [receipt]})
    public = await service.get_public_role(role_id)
    assert public["status"] == "success" and public["live"] is True
    open_role = public["open_role"]
    assert open_role["title"] == "Deployment Engineer"
    assert open_role["responsibilities"]
    assert open_role["application_instructions"]
    assert not ({"policy_hash", "policy_version_id", "candidates", "approvals"}
                & set(open_role))


def _resume_pdf() -> bytes:
    from pypdf import PdfWriter

    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(output)
    return output.getvalue()


@pytest.mark.asyncio
async def test_local_public_form_encrypts_and_queues_without_automatic_processing(
        monkeypatch):
    store = InMemoryDurableStore()
    package = _package()
    contract = package["contract"]
    policy_id = "policy_intake_test"
    policy_hash = hiring_tools.canonical_hash(contract)
    role_id = "role_intake_test"
    receipt = {
        "receipt_id": "receipt_intake_test", "policy_version_id": policy_id,
        "policy_hash": policy_hash, "automated_publication": False,
        "public_url": "https://example.test/jobs/deployment-engineer",
        "recorded_at": "2099-01-01T00:00:00+00:00",
    }
    await store.create("hiring_policy_versions", policy_id, {
        "policy_version_id": policy_id, "role_id": role_id,
        "workspace_id": "workspace_test", "status": "APPROVED",
        "canonical_hash": policy_hash,
        "role_description_hash": hiring_tools.canonical_hash(
            package["role_description"]),
        "contract": contract.model_dump(mode="json"), "version": 1,
    })
    role = {
        "role_id": role_id, "workspace_id": "workspace_test",
        "journey_id": "journey_intake_test", "role_state": "PUBLISHED",
        "current_policy_version_id": policy_id, "current_policy_hash": policy_hash,
        "role_description": package["role_description"],
        "publication_allowed": True, "candidate_processing_allowed": True,
        "public_application_form_enabled": True,
        "publication_receipts": [receipt],
        "publication_package": {"application_address": "role@example.test"},
        "mailbox_binding": {"status": "ACTIVE", "provider_route_id": "route-1",
                            "provider_label_id": "label-1"},
        "synthetic": False, "version": 1,
    }
    await store.create("hiring_roles", role_id, role)
    key_hex = "23" * 32
    monkeypatch.setenv("HIRING_PUBLIC_INTAKE_LOCAL_ENABLED", "1")
    monkeypatch.setenv("HIRING_PUBLIC_INTAKE_LOCAL_KEY", key_hex)
    saved: dict[str, bytes] = {}
    monkeypatch.setattr(
        "services.hiring_public_intake.storage.save_bytes",
        lambda name, data: saved.setdefault(name, data) and name)
    monkeypatch.setattr(
        "services.hiring_public_intake.storage.delete_artifact",
        lambda name: bool(saved.pop(name, None)))
    projection = build_public_intake_projection(role)
    assert projection["form_available"] is True
    assert projection["email_available"] is True
    assert projection["role_email"] == "role@example.test"

    service = HiringPublicIntakeService(
        store=store, local_key=bytes.fromhex(key_hex), local_enabled=True)
    resume = _resume_pdf()
    no_consent = await service.submit(
        role_id=role_id, intake_token=projection["intake_token"],
        client_request_id="application_request_no_consent",
        applicant_name="", email="candidate@example.test", cover_note="",
        consent_accepted=False, filename="candidate-resume.pdf",
        content_type="application/pdf", resume_bytes=resume)
    assert no_consent["error_code"] == "privacy_consent_required"
    submitted = await service.submit(
        role_id=role_id, intake_token=projection["intake_token"],
        client_request_id="application_request_001",
        applicant_name="", email="candidate@example.test", cover_note="",
        consent_accepted=True, filename="candidate-resume.pdf",
        content_type="application/pdf", resume_bytes=resume)
    assert submitted["status"] == "success"
    application = (await store.list(
        "candidate_applications", filters={"role_id": role_id}))[0]
    assert application["candidate_state"] == "RECEIVED"
    assert application["automatic_assessment_allowed"] is False
    assert application["external_actions"] == [] and application["run_id"] is None
    identity = (await store.list(
        "candidate_identities", filters={"role_id": role_id}))[0]
    artifact = (await store.list(
        "hiring_candidate_artifacts", filters={"role_id": role_id}))[0]
    assert identity["ciphertext"] and artifact["scope"] == "HIRING_RESTRICTED"
    assert next(iter(saved.values())) != resume
    serialized = repr(store.records)
    assert "candidate@example.test" not in serialized

    duplicate = await service.submit(
        role_id=role_id, intake_token=projection["intake_token"],
        client_request_id="application_request_001",
        applicant_name="", email="candidate@example.test", cover_note="",
        consent_accepted=True, filename="candidate-resume.pdf",
        content_type="application/pdf", resume_bytes=resume)
    assert duplicate["duplicate"] is True
    conflict = await service.submit(
        role_id=role_id, intake_token=projection["intake_token"],
        client_request_id="application_request_001",
        applicant_name="", email="other@example.test", cover_note="",
        consent_accepted=True, filename="candidate-resume.pdf",
        content_type="application/pdf", resume_bytes=resume)
    assert conflict["error_code"] == "intake_idempotency_conflict"


@pytest.mark.asyncio
async def test_public_form_refuses_synthetic_roles(monkeypatch):
    store = InMemoryDurableStore()
    service = HiringPublicIntakeService(
        store=store, local_key=bytes.fromhex("42" * 32), local_enabled=True)
    synthetic = {"role_id": "role_fixture", "synthetic": True,
                 "role_state": "PUBLISHED", "publication_allowed": True,
                 "candidate_processing_allowed": True,
                 "public_application_form_enabled": True,
                 "publication_receipts": []}
    assert build_public_intake_projection(synthetic)["form_available"] is False
    refused = await service.submit(
        role_id="role_fixture", intake_token="invalid",
        client_request_id="application_request_002",
        applicant_name="Candidate", email="candidate@example.test",
        cover_note="Cover note", consent_accepted=False,
        filename="resume.txt", content_type="text/plain", resume_bytes=b"resume")
    assert refused["error_code"] == "public_intake_not_open"


@pytest.mark.asyncio
async def test_role_conversation_context_is_workspace_scoped_and_candidate_free():
    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    await store.create("hiring_roles", "role_context_test", {
        "role_id": "role_context_test", "workspace_id": "workspace_test",
        "company_name": "Example Co", "role_title": "Deployment Engineer",
        "role_state": "DRAFT", "role_description": package["role_description"],
        "draft_contract": package["contract"].model_dump(mode="json"),
        "candidate_applications": ["must-not-project"], "version": 1,
    })
    result = await service.get_role_conversation_context(
        principal=_founder(), role_id="role_context_test")
    assert result["status"] == "success"
    context = result["role_context"]
    assert context["role_title"] == "Deployment Engineer"
    assert "candidate_applications" not in context
    assert "evidence" not in context and "approvals" not in context

    other = ActorPrincipal(
        actor_id="other", workspace_id="workspace_other",
        role=WorkspaceRole.FOUNDER, session_auth_time=0,
        membership_version=1)
    denied = await service.get_role_conversation_context(
        principal=other, role_id="role_context_test")
    assert denied["error_code"] == "role_not_found"


@pytest.mark.asyncio
async def test_candidate_workspace_reports_coverage_and_alex_never_makes_judgment(
        monkeypatch):
    store = InMemoryDurableStore()
    service = _service(store)
    monkeypatch.setenv("APP_SESSION_SECRET", "candidate-conversation-test-secret")
    contract = _package()["contract"]
    await store.create("hiring_roles", "role_candidate_test", {
        "role_id": "role_candidate_test", "workspace_id": "workspace_test",
        "current_policy_version_id": "policy_candidate_test",
        "current_policy_hash": hiring_tools.canonical_hash(contract),
        "role_state": "PUBLISHED", "version": 1,
    })
    await store.create("hiring_policy_versions", "policy_candidate_test", {
        "policy_version_id": "policy_candidate_test", "role_id": "role_candidate_test",
        "workspace_id": "workspace_test", "status": "APPROVED",
        "canonical_hash": hiring_tools.canonical_hash(contract),
        "contract": contract.model_dump(mode="json"), "version": 1,
    })
    await store.create("candidate_applications", "candidateapp_test", {
        "candidate_application_id": "candidateapp_test",
        "workspace_id": "workspace_test", "role_id": "role_candidate_test",
        "candidate_code": "C-00000001", "candidate_state": "AWAITING_HUMAN_DECISION",
        "current_policy_version_id": "policy_candidate_test",
        "current_assessment_id": "assessment_candidate_test",
        "run_id": None, "synthetic": True, "version": 1,
    })
    await store.create("candidate_assessments", "assessment_candidate_test", {
        "assessment_id": "assessment_candidate_test",
        "workspace_id": "workspace_test", "role_id": "role_candidate_test",
        "candidate_application_id": "candidateapp_test",
        "policy_version_id": "policy_candidate_test",
        "policy_hash": hiring_tools.canonical_hash(contract),
        "criteria": [{
            "criterion_id": contract.criteria[0].criterion_id,
            "status": "PARTIAL", "summary": "Some candidate-provided evidence.",
            "citations": [], "unknowns": ["Production scale is unclear."],
            "contradictions": [],
        }], "version": 1,
    })
    await store.create("hiring_candidate_artifacts", "artifact_candidate_test", {
        "artifact_id": "artifact_candidate_test", "workspace_id": "workspace_test",
        "role_id": "role_candidate_test",
        "candidate_application_id": "candidateapp_test",
        "scope": "HIRING_RESTRICTED", "sensitivity": "HIRING_RESTRICTED",
        "content_type": "application/pdf", "source_kind": "RESUME", "version": 1,
    })
    detail = await service.candidate_detail(
        principal=_founder(), application_id="candidateapp_test")
    assert detail["evidence_coverage"][0]["coverage"] == "UNCLEAR"
    assert detail["artifacts"][0]["scope"] == "HIRING_RESTRICTED"
    assert "storage_name" not in detail["artifacts"][0]

    conversation = HiringCandidateConversationService(store)
    started = await conversation.begin(
        principal=_founder(), candidate_application_id="candidateapp_test")
    coverage = await conversation.answer(
        principal=_founder(), conversation_token=started["conversation_token"],
        question="What evidence is present, missing, or unclear?",
        client_turn_id="candidate_turn_coverage")
    assert coverage["intent"] == "EVIDENCE_COVERAGE"
    assert "1 unclear" in coverage["answer"]
    refused = await conversation.answer(
        principal=_founder(), conversation_token=started["conversation_token"],
        question="Is this the best candidate and should we hire them?",
        client_turn_id="candidate_turn_judgment")
    assert refused["intent"] == "JUDGMENT_REFUSED"
    application = await store.get("candidate_applications", "candidateapp_test")
    assert application["candidate_state"] == "AWAITING_HUMAN_DECISION"
    assert len(await store.list(
        "hiring_conversation_turns",
        filters={"candidate_application_id": "candidateapp_test"})) == 2


def test_hiring_routes_expose_scoped_context_and_non_live_public_page(monkeypatch):
    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    import asyncio
    asyncio.run(store.create("hiring_roles", "role_route_test", {
        "role_id": "role_route_test", "workspace_id": "workspace_test",
        "company_name": "Example Co", "role_title": "Deployment Engineer",
        "role_state": "DRAFT", "role_description": package["role_description"],
        "draft_contract": package["contract"].model_dump(mode="json"),
        "publication_receipts": [], "version": 1,
    }))
    from app import hiring_routes

    async def actor(_request):
        return _founder()

    monkeypatch.setattr(hiring_routes, "_actor", actor)
    monkeypatch.setattr(hiring_routes, "_services", lambda: (service, object()))
    app = FastAPI()
    hiring_routes.register(app)
    client = TestClient(app)
    scoped = client.get(
        "/api/hiring/roles/role_route_test/conversation-context")
    assert scoped.status_code == 200
    assert scoped.json()["role_context"]["role_title"] == "Deployment Engineer"
    public = client.get("/api/public/hiring/roles/role_route_test")
    assert public.status_code == 404
    assert public.json()["error_code"] == "open_role_not_live"


def test_editable_job_description_saves_proposed_version_and_previews_exact_copy(
        monkeypatch):
    import asyncio

    from app import hiring_routes

    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    created = asyncio.run(service.create_founder_draft_role(
        principal=_founder(), contract=package["contract"],
        role_description=package["role_description"],
        client_request_id="editable_route_role"))
    role = created["role"]

    async def actor(_request):
        return _founder()

    monkeypatch.setattr(hiring_routes, "_actor", actor)
    monkeypatch.setattr(hiring_routes, "_mutation_allowed", lambda _request: {})
    monkeypatch.setattr(hiring_routes, "production_store", lambda: store)
    monkeypatch.setattr(
        hiring_policy_service, "production_store", lambda: store)
    monkeypatch.setattr(hiring_routes, "_services", lambda: (service, object()))
    app = FastAPI()
    hiring_routes.register(app)
    client = TestClient(app)
    payload = {
        "client_request_id": "editable_description_v2",
        "expected_role_version": role["version"],
        "change_reason": "Founder clarified the candidate-facing role.",
        "purpose": "Own secure, reliable customer deployments.",
        "responsibilities": ["Lead deployments from discovery through launch"],
        "success_outcomes": ["Accountable production launches"],
        "required_qualifications": ["Customer deployment delivery"],
        "preferred_qualifications": ["Deployment playbook experience"],
        "relevant_experience": ["Owned a production deployment"],
        "location": "Nigeria",
        "work_arrangement": "Remote",
        "employment_type": "Full-time employee",
        "compensation": "",
        "benefits": [],
        "hiring_process": ["Structured interview", "Founder decision"],
        "application_instructions": "Apply through the published role page.",
        "equal_opportunity_statement": "",
        "accessibility_statement": "Adjustments are available on request.",
    }
    saved = client.post(
        f"/api/hiring/roles/{role['role_id']}/job-description-drafts",
        json=payload)
    assert saved.status_code == 200
    policy = saved.json()
    assert policy["policy_status"] == "PROPOSED"
    assert policy["role_description"]["purpose"] == payload["purpose"]

    preview = client.get(
        f"/api/hiring/roles/{role['role_id']}/job-description-preview/"
        f"{policy['policy_version_id']}")
    assert preview.status_code == 200
    projection = preview.json()
    assert projection["preview"] is True and projection["live"] is False
    assert projection["open_role"]["overview"] == payload["purpose"]
    assert projection["open_role"]["responsibilities"] == payload[
        "responsibilities"]


def test_public_application_route_converges_on_restricted_candidate_queue(monkeypatch):
    import asyncio

    from app import hiring_routes

    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    contract = package["contract"]
    policy_id = "policy_route_intake"
    policy_hash = hiring_tools.canonical_hash(contract)
    role_id = "role_route_intake"
    receipt = {
        "receipt_id": "receipt_route_intake", "policy_version_id": policy_id,
        "policy_hash": policy_hash, "automated_publication": False,
        "public_url": "https://example.test/jobs/route-intake",
        "recorded_at": "2099-01-01T00:00:00+00:00",
    }
    asyncio.run(store.create("hiring_policy_versions", policy_id, {
        "policy_version_id": policy_id, "role_id": role_id,
        "workspace_id": "workspace_test", "status": "APPROVED",
        "canonical_hash": policy_hash,
        "role_description_hash": hiring_tools.canonical_hash(
            package["role_description"]),
        "contract": contract.model_dump(mode="json"), "version": 1,
    }))
    asyncio.run(store.create("hiring_roles", role_id, {
        "role_id": role_id, "workspace_id": "workspace_test",
        "journey_id": "journey_route_intake", "role_state": "PUBLISHED",
        "current_policy_version_id": policy_id, "current_policy_hash": policy_hash,
        "role_description": package["role_description"],
        "publication_allowed": True, "candidate_processing_allowed": True,
        "public_application_form_enabled": True,
        "publication_receipts": [receipt], "mailbox_binding": None,
        "publication_package": {"application_address": None},
        "synthetic": False, "version": 1,
    }))
    monkeypatch.setenv("HIRING_PUBLIC_INTAKE_LOCAL_ENABLED", "1")
    monkeypatch.setenv("HIRING_PUBLIC_INTAKE_LOCAL_KEY", "53" * 32)
    saved: dict[str, bytes] = {}
    monkeypatch.setattr(
        "services.hiring_public_intake.storage.save_bytes",
        lambda name, data: saved.setdefault(name, data) and name)
    monkeypatch.setattr(
        "services.hiring_public_intake.storage.delete_artifact",
        lambda name: bool(saved.pop(name, None)))
    monkeypatch.setattr(hiring_routes, "production_store", lambda: store)
    monkeypatch.setattr(hiring_routes, "_services", lambda: (service, object()))
    app = FastAPI()
    hiring_routes.register(app)
    client = TestClient(app)
    projection = client.get(
        f"/api/public/hiring/roles/{role_id}").json()["open_role"]["intake"]
    response = client.post(
        f"/api/public/hiring/roles/{role_id}/applications",
        data={
            "email": "applicant@example.test",
            "privacy_consent": "accepted",
            "intake_token": projection["intake_token"],
            "client_request_id": "application_route_request_001",
        },
        files={"resume": ("resume.pdf", _resume_pdf(), "application/pdf")})
    assert response.status_code == 200
    assert response.json()["application_status"] == "RECEIVED"
    rows = asyncio.run(store.list(
        "candidate_applications", filters={"role_id": role_id}))
    assert len(rows) == 1 and rows[0]["processing_status"] == "FOUNDER_REVIEW_REQUIRED"
