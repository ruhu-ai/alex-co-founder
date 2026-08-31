"""Founder-safe Hiring entry from Alex and the dedicated workspace."""

from __future__ import annotations

import io
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.co_founder import state_schema as ss
from agents.co_founder.tools import hiring as hiring_tools
from services import hiring_policy_service, hiring_public_intake
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import InMemoryDurableStore
from services.hiring_approval_service import request_approval, resolve_approval
from services.hiring_identity_vault import CandidateIdentityVault, fixture_key_wrapper
from services.hiring_public_intake import (
    HiringPublicIntakeService,
    build_public_intake_projection,
)
from services.hiring_role_draft import build_contract, founder_description_package
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


def test_local_public_intake_auto_key_is_private_and_persistent(
        monkeypatch, tmp_path):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("HIRING_PUBLIC_INTAKE_ENABLED", raising=False)
    monkeypatch.delenv("HIRING_PUBLIC_INTAKE_KEY", raising=False)
    monkeypatch.delenv("HIRING_PUBLIC_INTAKE_LOCAL_ENABLED", raising=False)
    monkeypatch.delenv("HIRING_PUBLIC_INTAKE_LOCAL_KEY", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    first = hiring_public_intake._configured_intake_key()
    second = hiring_public_intake._configured_intake_key()
    path = tmp_path / "cofounder" / "hiring-public-intake.key"

    assert first is not None and len(first) == 32
    assert second == first
    assert path.read_text(encoding="ascii") == first.hex()
    assert path.stat().st_mode & 0o777 == 0o600


def test_local_public_intake_explicit_off_and_cloud_never_auto_generate(
        monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HIRING_PUBLIC_INTAKE_LOCAL_ENABLED", "0")
    assert hiring_public_intake.public_intake_configured() is False
    assert not (tmp_path / "cofounder").exists()

    monkeypatch.delenv("HIRING_PUBLIC_INTAKE_LOCAL_ENABLED", raising=False)
    monkeypatch.setenv("K_SERVICE", "co-founder")
    monkeypatch.delenv("HIRING_PUBLIC_INTAKE_ENABLED", raising=False)
    monkeypatch.delenv("HIRING_PUBLIC_INTAKE_KEY", raising=False)
    assert hiring_public_intake.public_intake_configured() is False
    assert not (tmp_path / "cofounder").exists()


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


def test_explicit_local_fallback_compiles_real_role_and_not_demo_fixture():
    package = founder_description_package(
        ("Founding Full-stack AI Engineer, with 4 or more years of experience, "
         "strong in Python or TypeScript full-stack web development, and "
         "building AI or LLM powered products. They should work directly with "
         "founders, own delivery from idea to production and make practical "
         "technical decisions in a fast moving startup."),
        company_name="Ruhu", location="Nigeria", work_arrangement="Remote",
        employment_type="Full-time employee")
    assert package["status"] == "success"
    assert package["contract"].role_title == "Founding Full-Stack AI Engineer"
    assert package["contract"].role_title != "Forward Deployment Engineer"
    description = package["role_description"]
    assert any("4 or more years" in item for item in
               description["required_qualifications"])
    assert any("Python or TypeScript" in item for item in
               description["required_qualifications"])
    assert description["location"] == "Nigeria"
    assert description["work_arrangement"] == "Remote"
    assert description["preferred_qualifications"] == []


def test_role_title_ignores_natural_create_role_preamble():
    package = founder_description_package(
        ("Create a new role for Ruhu: Founding Full-stack AI Engineer, based "
         "in Nigeria, remote, full-time. We need someone with 4 or more years "
         "of experience in Python or TypeScript full-stack development."),
        company_name="Ruhu", location="Nigeria", work_arrangement="Remote",
        employment_type="Full-time employee")

    assert package["status"] == "success"
    assert package["contract"].role_title == "Founding Full-Stack AI Engineer"


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
        "jurisdiction_binding_sha256": proposed[
            "jurisdiction_binding_sha256"],
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
    assert committed["operating_jurisdiction"] == "Nigeria"
    assert committed["jurisdiction_binding"]["source"] == (
        "APPROVED_ROLE_PACKAGE")
    assert committed["jurisdiction_binding"]["legal_advice"] is False
    assert committed["jurisdiction_binding"]["legal_review_claimed"] is False
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


@pytest.mark.asyncio
async def test_location_correction_requires_a_new_matching_role_package():
    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    created = await service.create_founder_draft_role(
        principal=_founder(), contract=package["contract"],
        role_description=package["role_description"],
        client_request_id="jurisdiction_draft_v1")
    corrected_description = {
        **package["role_description"], "location": "Ghana"}
    refused = await hiring_policy_service.propose_policy(
        principal=_founder(), role_id=created["role"]["role_id"],
        contract=package["contract"],
        role_description=corrected_description,
        change_reason="Founder corrected the advertised location.",
        client_request_id="jurisdiction_policy_stale", store=store)
    assert refused["error_code"] == "jurisdiction_binding_mismatch"

    corrected_package = build_contract(
        company_name="Example Co", role_title="Deployment Engineer",
        role_summary="Own reliable customer deployments.", headcount_target=1,
        target_date="2099-01-30", location="Ghana",
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
    proposed = await hiring_policy_service.propose_policy(
        principal=_founder(), role_id=created["role"]["role_id"],
        contract=corrected_package["contract"],
        role_description=corrected_package["role_description"],
        change_reason="Founder corrected the advertised location.",
        client_request_id="jurisdiction_policy_v2", store=store)
    assert proposed["status"] == "success"
    assert proposed["operating_jurisdiction"] == "Ghana"
    assert proposed["jurisdiction_binding"]["policy_version_id"] == (
        proposed["policy_version_id"])


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
        "destination": "COFOUNDER_PUBLIC_ROLE_PAGE",
        "verification_status": "VERIFIED_APP_OWNED",
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


@pytest.mark.asyncio
async def test_founder_publish_click_opens_only_app_owned_role_intake():
    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    created = await service.create_founder_draft_role(
        principal=_founder(), contract=package["contract"],
        role_description=package["role_description"],
        client_request_id="publish_app_owned_role")
    role = created["role"]
    policy_id = "policy_app_owned_publish"
    policy_hash = hiring_tools.canonical_hash(package["contract"])
    await store.create("hiring_policy_versions", policy_id, {
        "policy_version_id": policy_id, "role_id": role["role_id"],
        "workspace_id": "workspace_test", "status": "APPROVED",
        "canonical_hash": policy_hash,
        "role_description_hash": hiring_tools.canonical_hash(
            package["role_description"]),
        "contract": package["contract"].model_dump(mode="json"), "version": 1,
    })
    approved = await store.compare_and_set(
        "hiring_roles", role["role_id"], role["version"], {
            "role_state": "APPROVED", "current_policy_version_id": policy_id,
            "current_policy_hash": policy_hash, "publication_allowed": True,
        })
    published = await service.record_publication(
        principal=_founder(), role_id=role["role_id"],
        destination="COFOUNDER_PUBLIC_ROLE_PAGE",
        public_url=("https://cofounder.example/hiring-notice.html?role_id="
                    + role["role_id"]),
        expected_version=approved["version"],
        client_request_id="publish_app_owned_role_click",
        attestation="Founder clicked Publish approved role.")
    assert published["verification_status"] == "VERIFIED_APP_OWNED"
    current = await store.get("hiring_roles", role["role_id"])
    assert current["role_state"] == "PUBLISHED"
    assert current["candidate_processing_allowed"] is True
    assert current["public_application_form_enabled"] is True


@pytest.mark.asyncio
async def test_external_publication_receipt_never_opens_app_intake():
    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    created = await service.create_founder_draft_role(
        principal=_founder(), contract=package["contract"],
        role_description=package["role_description"],
        client_request_id="external_receipt_role")
    role = created["role"]
    policy_id = "policy_external_receipt"
    policy_hash = hiring_tools.canonical_hash(package["contract"])
    await store.create("hiring_policy_versions", policy_id, {
        "policy_version_id": policy_id, "role_id": role["role_id"],
        "workspace_id": "workspace_test", "status": "APPROVED",
        "canonical_hash": policy_hash,
        "role_description_hash": hiring_tools.canonical_hash(
            package["role_description"]),
        "contract": package["contract"].model_dump(mode="json"), "version": 1,
    })
    approved = await store.compare_and_set(
        "hiring_roles", role["role_id"], role["version"], {
            "role_state": "APPROVED", "current_policy_version_id": policy_id,
            "current_policy_hash": policy_hash, "publication_allowed": True,
        })
    recorded = await service.record_publication(
        principal=_founder(), role_id=role["role_id"], destination="LINKEDIN",
        public_url="https://example.test/jobs/external-receipt",
        expected_version=approved["version"],
        client_request_id="external_receipt_only",
        attestation="Founder recorded an external publication receipt.")
    assert recorded["verification_status"] == "DISABLED_PENDING_TERMS_REVIEW"
    current = await store.get("hiring_roles", role["role_id"])
    assert current["candidate_processing_allowed"] is False
    assert current["public_application_form_enabled"] is False
    assert build_public_intake_projection(current)["form_available"] is False
    assert (await service.get_public_role(role["role_id"]))[
        "error_code"] == "open_role_not_live"


def _resume_pdf() -> bytes:
    from pypdf import PdfWriter

    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(output)
    return output.getvalue()


@pytest.mark.asyncio
async def test_local_public_form_encrypts_and_queues_alex_evidence_job(
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
        "destination": "COFOUNDER_PUBLIC_ROLE_PAGE",
        "verification_status": "VERIFIED_APP_OWNED",
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
    await store.create("workflow_runs", "run_role_intake_test", {
        "run_id": "run_role_intake_test", "workspace_id": "workspace_test",
        "journey_id": "journey_intake_test", "run_kind": "ROLE", "version": 1,
    })
    role = {
        "role_id": role_id, "workspace_id": "workspace_test",
        "journey_id": "journey_intake_test",
        "run_id": "run_role_intake_test", "role_state": "PUBLISHED",
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
    monkeypatch.setattr(
        "services.hiring_public_intake.storage.read_bytes",
        lambda name: saved[name])
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
        applicant_name="Synthetic Applicant", email="candidate@example.test",
        cover_note="",
        consent_accepted=False, filename="candidate-resume.pdf",
        content_type="application/pdf", resume_bytes=resume)
    assert no_consent["error_code"] == "privacy_consent_required"
    missing_name = await service.submit(
        role_id=role_id, intake_token=projection["intake_token"],
        client_request_id="application_request_missing_name",
        applicant_name="", email="candidate@example.test", cover_note="",
        consent_accepted=True, filename="candidate-resume.pdf",
        content_type="application/pdf", resume_bytes=resume)
    assert missing_name["error_code"] == "applicant_name_invalid"
    submitted = await service.submit(
        role_id=role_id, intake_token=projection["intake_token"],
        client_request_id="application_request_001",
        applicant_name="  Synthetic   Applicant  ",
        email="candidate@example.test", cover_note="",
        consent_accepted=True, filename="candidate-resume.pdf",
        content_type="application/pdf", resume_bytes=resume)
    assert submitted["status"] == "success"
    application = (await store.list(
        "candidate_applications", filters={"role_id": role_id}))[0]
    assert application["candidate_state"] == "RECEIVED"
    assert application["automatic_assessment_allowed"] is False
    assert application["automatic_evidence_preparation_allowed"] is True
    assert application["processing_status"] == "EVIDENCE_QUEUED"
    assert application["external_actions"] == [] and application["run_id"]
    identity = (await store.list(
        "candidate_identities", filters={"role_id": role_id}))[0]
    artifact = (await store.list(
        "hiring_candidate_artifacts", filters={"role_id": role_id}))[0]
    assert identity["ciphertext"] and artifact["scope"] == "HIRING_RESTRICTED"
    assert next(iter(saved.values())) != resume
    serialized = repr(store.records)
    assert "candidate@example.test" not in serialized
    assert "Synthetic Applicant" not in serialized
    fresh_founder = ActorPrincipal(
        actor_id="founder_actor", workspace_id="workspace_test",
        role=WorkspaceRole.FOUNDER, session_auth_time=int(time.time()),
        membership_version=1)
    revealed = await service.reveal_restricted_identity(
        application=application, principal=fresh_founder)
    assert revealed == {"status": "success", "identity": {
        "name": "Synthetic Applicant", "email": "candidate@example.test",
    }}
    founder_projection = await service.reveal_restricted_identity(
        application=application, principal=_founder(), require_fresh=False)
    assert founder_projection["identity"]["email"] == "candidate@example.test"
    opened = await service.read_restricted_resume(application=application)
    assert opened["status"] == "success"
    assert opened["resume_bytes"] == resume

    duplicate = await service.submit(
        role_id=role_id, intake_token=projection["intake_token"],
        client_request_id="application_request_001",
        applicant_name="Synthetic Applicant", email="candidate@example.test",
        cover_note="",
        consent_accepted=True, filename="candidate-resume.pdf",
        content_type="application/pdf", resume_bytes=resume)
    assert duplicate["duplicate"] is True
    conflict = await service.submit(
        role_id=role_id, intake_token=projection["intake_token"],
        client_request_id="application_request_001",
        applicant_name="Synthetic Applicant", email="other@example.test",
        cover_note="",
        consent_accepted=True, filename="candidate-resume.pdf",
        content_type="application/pdf", resume_bytes=resume)
    assert conflict["error_code"] == "intake_idempotency_conflict"


@pytest.mark.asyncio
async def test_alex_worker_maps_public_resume_without_ranking_or_deciding(
        monkeypatch):
    store = InMemoryDurableStore()
    service = _service(store)
    package = _package()
    created = await service.create_founder_draft_role(
        principal=_founder(), contract=package["contract"],
        role_description=package["role_description"],
        client_request_id="public_assessment_role")
    role = created["role"]
    policy_id = "policy_public_assessment"
    policy_hash = hiring_tools.canonical_hash(package["contract"])
    await store.create("hiring_policy_versions", policy_id, {
        "policy_version_id": policy_id, "role_id": role["role_id"],
        "workspace_id": "workspace_test", "status": "APPROVED",
        "canonical_hash": policy_hash,
        "role_description_hash": hiring_tools.canonical_hash(
            package["role_description"]),
        "contract": package["contract"].model_dump(mode="json"), "version": 1,
    })
    receipt = {
        "receipt_id": "receipt_public_assessment",
        "policy_version_id": policy_id, "policy_hash": policy_hash,
        "automated_publication": False,
        "destination": "COFOUNDER_PUBLIC_ROLE_PAGE",
        "verification_status": "VERIFIED_APP_OWNED",
        "public_url": "https://example.test/hiring-notice.html",
        "recorded_at": "2099-01-01T00:00:00+00:00",
    }
    role = await store.compare_and_set("hiring_roles", role["role_id"], 1, {
        "role_state": "PUBLISHED", "current_policy_version_id": policy_id,
        "current_policy_hash": policy_hash, "publication_allowed": True,
        "candidate_processing_allowed": True,
        "public_application_form_enabled": True,
        "publication_receipts": [receipt],
    })
    key_hex = "61" * 32
    monkeypatch.setenv("HIRING_PUBLIC_INTAKE_ENABLED", "1")
    monkeypatch.setenv("HIRING_PUBLIC_INTAKE_KEY", key_hex)
    saved: dict[str, bytes] = {}
    monkeypatch.setattr(
        "services.hiring_public_intake.storage.save_bytes",
        lambda name, data: saved.setdefault(name, data) and name)
    monkeypatch.setattr(
        "services.hiring_public_intake.storage.read_bytes",
        lambda name: saved[name])
    projection = build_public_intake_projection(role)
    intake = HiringPublicIntakeService(store=store)
    submitted = await intake.submit(
        role_id=role["role_id"], intake_token=projection["intake_token"],
        client_request_id="application_public_assessment_001",
        applicant_name="Synthetic Applicant",
        email="synthetic-applicant@example.test", cover_note="",
        consent_accepted=True, filename="resume.pdf",
        content_type="application/pdf", resume_bytes=_resume_pdf())
    assert submitted["status"] == "success"
    application = (await store.list(
        "candidate_applications", filters={"role_id": role["role_id"]}))[0]
    original_get = store.get

    async def reject_empty_document_ids(collection, document_id):
        assert document_id, f"empty document id requested for {collection}"
        return await original_get(collection, document_id)

    monkeypatch.setattr(store, "get", reject_empty_document_ids)
    pending = await service.candidate_detail(
        principal=_founder(),
        application_id=application["candidate_application_id"])
    assert pending["status"] == "success"
    assert pending["assessment"] is None
    assert pending["evidence_status"] == "PREPARING"
    assert all(item["summary"] == "Evidence mapping pending."
               for item in pending["evidence_coverage"])
    monkeypatch.setattr(
        "services.document_ingestion.extract_chunks",
        lambda path, suffix: {"status": "success", "chunks": [{
            "id": "resume-block-1", "content": (
                "Owned customer deployment delivery and accountable production launches."),
            "locator": {"page": 1},
        }]})
    mapped = await service.prepare_public_application_evidence(
        application_id=application["candidate_application_id"], workload={
            "workload_kind": "CLOUD_TASKS",
            "workload_service_account": "hiring-worker@example.test",
            "workload_audience": (
                "https://example.test/tasks/hiring/prepare_candidate_evidence"),
            "delivery_id": "delivery_public_assessment_001",
        })
    assert mapped["status"] == "success"
    assert mapped["candidate_state"] == "AWAITING_HUMAN_DECISION"
    assessment = await store.get("candidate_assessments", mapped["assessment_id"])
    serialized = repr(assessment).casefold()
    assert not any(term in serialized for term in (
        "total_score", "ranking", "recommendation", "best_candidate"))
    committed = await store.get(
        "candidate_applications", application["candidate_application_id"])
    assert committed["role_id"] == role["role_id"]
    assert committed["current_decision_id"] is None
    assert committed["external_actions"] == []
    assert assessment["operator_agent"] == "hiring_operator"
    assert assessment["analyst_agent"] == "hiring_evidence_analyst"
    assert assessment["tool_calls"] == 0 and assessment["model_calls"] == 0
    duplicate = await service.prepare_public_application_evidence(
        application_id=application["candidate_application_id"], workload={
            "workload_kind": "CLOUD_TASKS",
            "workload_service_account": "hiring-worker@example.test",
            "workload_audience": (
                "https://example.test/tasks/hiring/prepare_candidate_evidence"),
            "delivery_id": "delivery_public_assessment_001",
        })
    assert duplicate["status"] == "success" and duplicate["duplicate"] is True
    assert duplicate["assessment_id"] == mapped["assessment_id"]
    ready = await service.candidate_detail(
        principal=_founder(),
        application_id=application["candidate_application_id"])
    assert ready["evidence_status"] == "READY"
    assert ready["assessment"]["assessment_id"] == mapped["assessment_id"]


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
    scoped_context = await service.get_candidate_conversation_context(
        principal=_founder(), application_id="candidateapp_test")
    assert scoped_context["status"] == "success"
    projected = scoped_context["candidate_context"]
    assert projected["context_kind"] == "HIRING_CANDIDATE_EVIDENCE"
    assert projected["criterion_coverage"][0]["coverage"] == "UNCLEAR"
    assert "identity" not in projected and "resume" not in repr(projected).lower()

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

    monkeypatch.delenv("APP_SESSION_SECRET", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("APP_AUTH_TOKEN", "documented-local-auth-fallback")
    local_started = await conversation.begin(
        principal=_founder(), candidate_application_id="candidateapp_test")
    assert local_started["status"] == "success"

    monkeypatch.setenv("K_SERVICE", "co-founder")
    unavailable = await conversation.begin(
        principal=_founder(), candidate_application_id="candidateapp_test")
    assert unavailable["error_code"] == "provider_unavailable"
    assert "conversation_token" not in unavailable


@pytest.mark.asyncio
async def test_candidate_detail_resolves_citations_and_retains_committed_decision():
    store = InMemoryDurableStore()
    service = _service(store)
    contract = _package()["contract"]
    policy_hash = hiring_tools.canonical_hash(contract)
    criterion_id = contract.criteria[0].criterion_id
    evidence_hash = "sha256:" + "a" * 64
    await store.create("hiring_roles", "role_citation", {
        "role_id": "role_citation", "workspace_id": "workspace_test",
        "current_policy_version_id": "policy_citation",
        "current_policy_hash": policy_hash, "role_state": "PUBLISHED",
        "version": 1,
    })
    await store.create("hiring_policy_versions", "policy_citation", {
        "policy_version_id": "policy_citation", "role_id": "role_citation",
        "workspace_id": "workspace_test", "status": "APPROVED",
        "canonical_hash": policy_hash,
        "contract": contract.model_dump(mode="json"), "version": 1,
    })
    await store.create("candidate_applications", "candidateapp_citation", {
        "candidate_application_id": "candidateapp_citation",
        "workspace_id": "workspace_test", "role_id": "role_citation",
        "candidate_code": "C-00000002", "candidate_state": "ADVANCED",
        "current_policy_version_id": "policy_citation",
        "current_assessment_id": "assessment_citation",
        "current_decision_id": "decision_citation", "run_id": None,
        "synthetic": True, "version": 2,
    })
    await store.create("hiring_candidate_artifacts", "artifact_citation", {
        "artifact_id": "artifact_citation", "workspace_id": "workspace_test",
        "role_id": "role_citation",
        "candidate_application_id": "candidateapp_citation",
        "scope": "HIRING_RESTRICTED", "sensitivity": "HIRING_RESTRICTED",
        "content_type": "application/pdf", "source_kind": "RESUME",
        "version": 1,
    })
    await store.create("candidate_evidence", "ce_citation", {
        "evidence_id": "ce_citation", "workspace_id": "workspace_test",
        "role_id": "role_citation",
        "candidate_application_id": "candidateapp_citation",
        "source_artifact_id": "artifact_citation", "source_kind": "RESUME",
        "criterion_ids": [criterion_id],
        "locator": {"page": 2, "block": "experience-2"},
        "quote": "Owned a reviewed customer deployment from plan to launch.",
        "normalized_fact": "", "authority": "CANDIDATE_CLAIM",
        "verification": "UNVERIFIED", "content_risk": "CLEAR",
        "evidence_hash": evidence_hash, "version": 1,
    })
    await store.create("candidate_assessments", "assessment_citation", {
        "assessment_id": "assessment_citation", "workspace_id": "workspace_test",
        "role_id": "role_citation",
        "candidate_application_id": "candidateapp_citation",
        "policy_version_id": "policy_citation", "policy_hash": policy_hash,
        "criteria": [{
            "criterion_id": criterion_id, "status": "SUPPORTED",
            "summary": "Candidate-provided experience is cited.",
            "citations": [{"evidence_id": "ce_citation",
                           "evidence_hash": evidence_hash}],
            "unknowns": [], "contradictions": [],
        }], "version": 1,
    })
    await store.create("hiring_decisions", "decision_citation", {
        "decision_id": "decision_citation", "workspace_id": "workspace_test",
        "role_id": "role_citation",
        "candidate_application_id": "candidateapp_citation",
        "decision": "ADVANCE", "candidate_state_after": "ADVANCED",
        "reason_codes": ["CRITERION_EVIDENCE_SUFFICIENT"],
        "human_note": "Proceed to a structured Founder interview.",
        "commit_status": "COMMITTED", "committed_at": "2099-01-01T10:00:00Z",
        "policy_version_id": "policy_citation",
        "evidence_ids_reviewed": ["ce_citation"], "version": 1,
    })

    detail = await service.candidate_detail(
        principal=_founder(), application_id="candidateapp_citation")
    citation = detail["evidence_coverage"][0]["citations"][0]
    assert detail["evidence_coverage"][0]["coverage"] == "PRESENT"
    assert citation == {
        "evidence_id": "ce_citation", "resolved": True,
        "label": "CV · page 2", "source_kind": "RESUME",
        "source_artifact_id": "artifact_citation",
        "content_type": "application/pdf",
        "locator": {"page": 2, "block": "experience-2"},
        "preview": "Owned a reviewed customer deployment from plan to launch.",
        "authority": "CANDIDATE_CLAIM", "verification": "UNVERIFIED",
        "openable": True,
    }
    assert detail["current_decision"]["decision"] == "ADVANCE"
    assert detail["current_decision"]["commit_status"] == "COMMITTED"
    assert detail["current_decision"]["human_note"] == (
        "Proceed to a structured Founder interview.")
    assert detail["current_decision"]["evidence_count"] == 1

    assessment = await store.get("candidate_assessments", "assessment_citation")
    broken_criteria = list(assessment["criteria"])
    broken_criteria[0] = {
        **broken_criteria[0],
        "citations": [{"evidence_id": "ce_citation",
                       "evidence_hash": "sha256:" + "b" * 64}],
    }
    assert await store.compare_and_set(
        "candidate_assessments", "assessment_citation",
        int(assessment["version"]), {"criteria": broken_criteria})
    unresolved = await service.candidate_detail(
        principal=_founder(), application_id="candidateapp_citation")
    assert unresolved["evidence_coverage"][0]["coverage"] == "UNCLEAR"
    assert unresolved["evidence_coverage"][0]["citations"][0]["resolved"] is False
    assert "could not be resolved" in unresolved["evidence_coverage"][0][
        "unknowns"][-1]


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


def test_founder_can_open_exact_restricted_resume_without_public_url(monkeypatch):
    import asyncio

    from app import hiring_routes

    store = InMemoryDurableStore()
    service = _service(store)
    asyncio.run(store.create("candidate_applications", "candidateapp_abcdef0123456789", {
        "candidate_application_id": "candidateapp_abcdef0123456789",
        "candidate_id": "candidate_abcdef0123456789",
        "candidate_code": "C-12345678",
        "workspace_id": "workspace_test", "role_id": "role_resume_test",
        "source_kind": "PUBLIC_FORM", "synthetic": False,
        "artifact_ids": ["artifact_resume_test"], "version": 1,
    }))

    async def actor(_request):
        return ActorPrincipal(
            actor_id="founder_actor", workspace_id="workspace_test",
            role=WorkspaceRole.FOUNDER, session_auth_time=int(time.time()),
            membership_version=1)

    async def opened(_self, *, application):
        assert application["candidate_application_id"] == "candidateapp_abcdef0123456789"
        return {
            "status": "success", "resume_bytes": b"%PDF-safe-test",
            "extension": ".pdf", "artifact": {
                "artifact_id": "artifact_resume_test",
                "content_type": "application/pdf",
            },
        }

    monkeypatch.setattr(hiring_routes, "_actor", actor)
    monkeypatch.setattr(hiring_routes, "_mutation_allowed", lambda _request: {})
    monkeypatch.setattr(hiring_routes, "production_store", lambda: store)
    monkeypatch.setattr(hiring_routes, "_services", lambda: (service, object()))
    monkeypatch.setattr(HiringPublicIntakeService,
                        "read_restricted_resume", opened)
    app = FastAPI()
    hiring_routes.register(app)
    response = TestClient(app).post(
        "/api/hiring/applications/candidateapp_abcdef0123456789/resume",
        json={"client_request_id": "resume_open_test_001"})
    assert response.status_code == 200
    assert response.content == b"%PDF-safe-test"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["x-content-type-options"] == "nosniff"
    audits = asyncio.run(store.list("audit", filters={}))
    assert len(audits) == 1
    assert audits[0]["action"] == "hiring.resume.open"
    assert "candidate@example" not in repr(audits)


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
        "destination": "COFOUNDER_PUBLIC_ROLE_PAGE",
        "verification_status": "VERIFIED_APP_OWNED",
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
    asyncio.run(store.create("workflow_runs", "run_role_route_intake", {
        "run_id": "run_role_route_intake", "workspace_id": "workspace_test",
        "journey_id": "journey_route_intake", "run_kind": "ROLE", "version": 1,
    }))
    asyncio.run(store.create("hiring_roles", role_id, {
        "role_id": role_id, "workspace_id": "workspace_test",
        "journey_id": "journey_route_intake",
        "run_id": "run_role_route_intake", "role_state": "PUBLISHED",
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
    monkeypatch.setattr(
        hiring_routes.task_queue, "enqueue_hiring",
        lambda *_args, **_kwargs: {"status": "success"})

    async def actor(_request):
        return _founder()

    monkeypatch.setattr(hiring_routes, "_actor", actor)
    app = FastAPI()
    hiring_routes.register(app)
    client = TestClient(app)
    projection = client.get(
        f"/api/public/hiring/roles/{role_id}").json()["open_role"]["intake"]
    response = client.post(
        f"/api/public/hiring/roles/{role_id}/applications",
        data={
            "applicant_name": "Route Applicant",
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
    assert len(rows) == 1 and rows[0]["processing_status"] == "EVIDENCE_QUEUED"
    role_response = client.get(f"/api/hiring/roles/{role_id}")
    assert role_response.status_code == 200
    assert role_response.json()["candidates"][0]["evidence_status"] == "PREPARING"
