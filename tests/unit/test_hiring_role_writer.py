"""Grounded job-spec writer quality, repair, and provider-boundary tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services import gemini_backends, hiring_role_writer
from services.hiring_role_draft import build_contract

DESCRIPTION = (
    "Founding Full-stack AI Engineer, with 4 or more years of experience, "
    "strong in Python or TypeScript full-stack web development, and building "
    "AI or LLM powered products. They should work directly with founders, own "
    "delivery from idea to production and make practical technical decisions "
    "in a fast moving startup."
)


def _good_output(title: str = "Founding Full-Stack AI Engineer") -> dict:
    return {
        "role_title": title,
        "purpose": (
            "Ruhu is seeking a Founding Full-Stack AI Engineer to own the "
            "delivery of AI-powered product capabilities from early problem "
            "definition through dependable production operation. The engineer "
            "will work directly with the Founders, turn ambiguous product needs "
            "into practical technical plans, and make proportionate decisions "
            "about speed, reliability, cost, security, and maintainability."
        ),
        "responsibilities": [
            "Own full-stack product delivery from problem definition through production operation.",
            "Build and maintain reliable Python or TypeScript services and web interfaces.",
            "Design grounded AI and LLM capabilities with appropriate evaluation and safeguards.",
            "Translate ambiguous Founder priorities into bounded technical plans and milestones.",
            "Establish practical testing, deployment, monitoring, and incident-response practices.",
            "Communicate architecture trade-offs, delivery risks, and operational decisions clearly.",
        ],
        "success_outcomes": [
            "A meaningful customer-facing capability is shipped and supported reliably in production.",
            "The product team has a repeatable path from a reviewed idea to a monitored release.",
            "Technical trade-offs are documented clearly enough for the Founders to review decisions.",
            "Recurring operational failures produce visible fixes and improvements to delivery practice.",
        ],
        "required_qualifications": [
            "4 or more years of experience building and operating production software.",
            "Strong practical experience with Python or TypeScript in full-stack web development.",
            "Experience delivering AI or LLM powered product capabilities to production users.",
            "Evidence of independently owning delivery from an ambiguous idea through release.",
            "Ability to make pragmatic architecture decisions in a fast-moving startup environment.",
        ],
        "preferred_qualifications": [
            "Experience establishing engineering practices in an early-stage product team.",
            "Experience evaluating reliability and quality for model-backed product behavior.",
        ],
        "relevant_experience": [
            "Owned a full-stack production feature across planning, implementation, and operation.",
            "Integrated an AI or LLM capability with a user-facing application and measured its quality.",
            "Worked directly with company or product leadership on delivery and technical trade-offs.",
        ],
        "hiring_process": [
            "Founder conversation about role scope, motivation, and examples of end-to-end ownership.",
            "Structured technical and product discussion using the reviewed job-related scorecard.",
            "Practical delivery case study followed by a final human decision from the Founder.",
        ],
        "application_instructions": (
            "Apply through this role page with a current PDF or DOCX resume and "
            "examples of relevant production work."
        ),
        "compensation": "",
        "benefits": [],
        "equal_opportunity_statement": "",
        "accessibility_statement": "",
    }


@pytest.fixture(autouse=True)
def _reset_writer(monkeypatch):
    monkeypatch.setattr(hiring_role_writer, "_writer_fn", None)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv(
        "HIRING_ROLE_WRITER_ALLOW_DEGRADED_LOCAL", raising=False)


async def _write(**overrides):
    values = {
        "description": DESCRIPTION,
        "company_name": "Ruhu",
        "location": "Nigeria",
        "work_arrangement": "Remote",
        "employment_type": "Full-time employee",
        "company_context": {"product": "An AI co-founder product"},
    }
    values.update(overrides)
    return await hiring_role_writer.write_founder_role_package(**values)


@pytest.mark.asyncio
async def test_grounded_writer_returns_detailed_existing_package_fields(monkeypatch):
    seen = []

    async def writer(payload):
        seen.append(payload)
        return _good_output()

    monkeypatch.setattr(hiring_role_writer, "_writer_fn", writer)
    package = await _write(company_context={
        "product": "An AI co-founder product",
        "oauth_refresh_token": "must-not-reach-model",
        "private_notes": "must-not-reach-model",
    })

    assert package["status"] == "success"
    assert package["drafting_mode"] == "GEMINI_GROUNDED"
    assert package["contract"].role_title == "Founding Full-Stack AI Engineer"
    assert package["contract"].role_title != "Forward Deployment Engineer"
    assert package["contract"].role_summary == _good_output()["purpose"]
    assert len(package["role_description"]["responsibilities"]) == 6
    assert len(package["role_description"]["success_outcomes"]) == 4
    assert len(package["role_description"]["required_qualifications"]) == 5
    assert "## Responsibilities" in package["contract"].public_job_description
    assert "Forward Deployment Engineer" not in package["contract"].public_job_description
    assert len(seen) == 1 and seen[0]["mode"] == "WRITE"
    assert seen[0]["company_context"] == {
        "product": "An AI co-founder product"}
    assert "target_date" not in seen[0]
    assert "headcount_target" not in seen[0]


@pytest.mark.asyncio
async def test_normal_alex_structured_brief_uses_same_writer_and_preserves_facts(
        monkeypatch):
    async def writer(_payload):
        return _good_output("Deployment Engineer")

    monkeypatch.setattr(hiring_role_writer, "_writer_fn", writer)
    package = await hiring_role_writer.write_structured_founder_role_package(
        company_name="Example Co", role_title="Deployment Engineer",
        role_summary="Own reliable customer deployments.", headcount_target=2,
        target_date="2099-01-30", location="Nigeria",
        work_arrangement="Remote", employment_type="Full-time employee",
        compensation_envelope="Founder supplied band",
        required_criteria=["Customer deployment delivery"],
        responsibilities=["Lead deployments"],
        success_outcomes=["Accountable launches"],
        preferred_criteria=[],
        relevant_experience=["Owned a production deployment"],
        benefits=["Founder supplied learning budget"], hiring_process=[],
        application_instructions="Apply through the published role page.",
        equal_opportunity_statement="", accessibility_statement="",
        public_job_description="Customer-facing deployment ownership.")

    assert package["status"] == "success"
    assert package["source_entry"] == "NORMAL_ALEX_CONVERSATION"
    assert len(package["contract"].criteria) == 5
    assert len(package["role_description"]["responsibilities"]) == 6
    assert package["contract"].target_date == "2099-01-30"
    assert package["contract"].headcount_target == 2
    assert package["role_description"]["compensation"] == "Founder supplied band"
    assert package["role_description"]["benefits"] == [
        "Founder supplied learning budget"]


@pytest.mark.asyncio
async def test_writer_repairs_once_then_commits_valid_package(monkeypatch):
    calls = []

    async def writer(payload):
        calls.append(payload)
        if payload["mode"] == "WRITE":
            return {**_good_output(), "role_title": "Forward Deployment Engineer"}
        return _good_output()

    monkeypatch.setattr(hiring_role_writer, "_writer_fn", writer)
    package = await _write()

    assert package["status"] == "success"
    assert [item["mode"] for item in calls] == ["WRITE", "REPAIR"]
    assert "role_title must remain exactly" in " ".join(
        calls[1]["validation_errors"])


@pytest.mark.asyncio
async def test_second_invalid_output_fails_closed_without_draft(monkeypatch):
    async def writer(_payload):
        return {**_good_output(), "responsibilities": ["Too short"] * 6}

    monkeypatch.setattr(hiring_role_writer, "_writer_fn", writer)
    result = await _write()

    assert result["error_code"] == "role_draft_unsafe"
    assert "contract" not in result
    assert result["validation_errors"]


@pytest.mark.asyncio
async def test_unwired_writer_is_distinct_from_unsafe_output():
    result = await _write()
    assert result["error_code"] == "role_writer_unavailable"


@pytest.mark.asyncio
async def test_explicit_local_degraded_mode_is_visible(monkeypatch):
    monkeypatch.setenv("HIRING_ROLE_WRITER_ALLOW_DEGRADED_LOCAL", "1")
    result = await _write()
    assert result["status"] == "success"
    assert result["drafting_mode"] == "DEGRADED_LOCAL"


@pytest.mark.asyncio
async def test_model_cannot_invent_compensation_or_benefits(monkeypatch):
    async def writer(_payload):
        return {**_good_output(), "compensation": "Competitive",
                "benefits": ["Invented health plan"]}

    monkeypatch.setattr(hiring_role_writer, "_writer_fn", writer)
    result = await _write()
    assert result["error_code"] == "role_draft_unsafe"
    assert any("compensation" in item for item in result["validation_errors"])


def test_realistic_copy_does_not_trigger_selection_guard():
    safe = build_contract(
        company_name="Example", role_title="Product Engineer",
        role_summary="Build reliable, accessible product systems.",
        headcount_target=1, target_date="2099-01-30", location="Nigeria",
        work_arrangement="Remote", employment_type="Full-time",
        compensation_envelope="", required_criteria=[
            "Experience delivering reliable production web applications"],
        preferred_criteria=[], relevant_experience=[
            "Owned a customer-facing production release"],
        responsibilities=[
            "Own the age-gating and identity-verification flow safely",
            "Design a race-condition-free background job scheduler",
            "Build accessible interfaces for users with a disability",
            "Support a global, multi-nationality customer base responsibly",
        ], success_outcomes=["Production behavior remains reliable under load"],
        application_instructions="Apply through the published role page.",
        public_job_description="Build reliable product systems.")
    assert safe["status"] == "success"

    blocked = build_contract(
        company_name="Example", role_title="Product Engineer",
        role_summary="Build reliable product systems.", headcount_target=1,
        target_date="2099-01-30", location="Nigeria",
        work_arrangement="Remote", employment_type="Full-time",
        compensation_envelope="", required_criteria=[
            "Candidates must be under age 30"], preferred_criteria=[],
        relevant_experience=["Owned a production release"],
        responsibilities=["Own production product delivery"],
        success_outcomes=["Production releases are dependable"],
        application_instructions="Apply through the published role page.",
        public_job_description="Build reliable product systems.")
    assert blocked["error_code"] == "prohibited_hiring_criterion"


@pytest.mark.asyncio
async def test_gemini_backend_uses_structured_toolless_call(monkeypatch):
    captured = {}

    class Models:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text=json.dumps(_good_output()))

    monkeypatch.setattr(
        gemini_backends, "get_client",
        lambda: SimpleNamespace(models=Models()))
    result = await gemini_backends.hiring_role_writer_fn({
        "mode": "WRITE", "founder_description": DESCRIPTION,
        "expected_role_title": "Founding Full-Stack AI Engineer",
        "company_name": "Ruhu", "company_context": {},
        "working_assumptions": {
            "location": "Nigeria", "work_arrangement": "Remote",
            "employment_type": "Full-time employee"},
    })

    assert result["role_title"] == "Founding Full-Stack AI Engineer"
    assert captured["model"] == gemini_backends.MODEL_ID
    config = captured["config"]
    assert config.response_mime_type == "application/json"
    assert config.tools is None
    assert config.temperature == 0.2
    schema = config.response_schema
    assert "additionalProperties" not in schema
    assert "title" not in schema
    assert schema["properties"]["responsibilities"] == {
        "type": "array", "items": {"type": "string"}}
