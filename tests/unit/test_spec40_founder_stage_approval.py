"""One Founder release approval never bypasses Spec 40 technical gates."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from skills.approvals import (
    FounderStageApproval,
    TechnicalGateState,
    evaluate_offline_qualification,
)

REPO = Path(__file__).resolve().parents[2]
APPROVAL_PATH = (
    REPO / "skills/approvals/spec40-gate-f-offline-qualification.json"
)
TECHNICAL_PATH = REPO / "skills/approvals/spec40-gate-f-technical-gates.json"
MODEL_POLICY_PATH = REPO / "skills/model_policies/spec40-gate-f-offline-v1.json"
COST_PREFLIGHT_PATH = REPO / "skills/approvals/spec40-gate-f-cost-preflight.json"
PROVIDER_PREFLIGHT_PATH = (
    REPO / "skills/approvals/spec40-gate-f-provider-preflight.json"
)
PLAN_PATH = REPO / "tests/eval/spec40_gate_f_qualification_plan.json"
FIXTURE_PATH = REPO / "tests/eval/spec40_gate_f_cases.json"
CATALOG_PATH = REPO / "skills/catalog.v1.json"


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _approval() -> FounderStageApproval:
    return FounderStageApproval.model_validate_json(APPROVAL_PATH.read_text())


def _technical() -> TechnicalGateState:
    return TechnicalGateState.model_validate_json(TECHNICAL_PATH.read_text())


def _decision(**changes):
    approval = _approval()
    catalog = json.loads(CATALOG_PATH.read_text())
    skill = catalog["skills"][0]
    values = {
        "now": datetime(2026, 8, 29, tzinfo=timezone.utc),
        "skill_identity": (
            f"{skill['manifest']['skill_id']}@{skill['manifest']['version']}"
        ),
        "definition_hash": skill["definition_hash"],
        "catalog_hash": catalog["catalog_hash"],
        "qualification_plan_sha256": _sha(PLAN_PATH),
        "fixture_bundle_sha256": _sha(FIXTURE_PATH),
        "model_policy_sha256": _sha(MODEL_POLICY_PATH),
    }
    values.update(changes)
    return evaluate_offline_qualification(
        approval,
        _technical(),
        **values,
    )


def test_one_approval_pins_exact_skill_catalog_plan_fixtures_and_model_policy():
    approval = _approval()
    catalog = json.loads(CATALOG_PATH.read_text())
    skill = catalog["skills"][0]

    assert approval.principal_role == "FOUNDER"
    assert approval.approval_kind == "RELEASE_STAGE_ONLY"
    assert approval.gate == "GATE_F_OFFLINE_QUALIFICATION"
    assert len(approval.skills) == 1
    assert approval.skills[0].identity == (
        "documents.produce-grounded-artifact@1.0.0"
    )
    assert approval.skills[0].definition_hash == skill["definition_hash"]
    assert approval.catalog_hash == catalog["catalog_hash"]
    assert approval.qualification_plan_sha256 == _sha(PLAN_PATH)
    assert approval.fixture_bundle_sha256 == _sha(FIXTURE_PATH)
    assert approval.model_policy_sha256 == _sha(MODEL_POLICY_PATH)


def test_current_approval_preserves_every_unfinished_technical_preflight():
    decision = _decision()

    assert not decision.authorized
    assert decision.blockers == ("independent_review_complete",)


def test_same_approval_authorizes_after_all_technical_checks_pass():
    approval = _approval()
    technical = _technical().model_copy(update={
        "provider_auth_verified": True,
        "model_availability_verified": True,
        "independent_review_complete": True,
        "evidence_refs": {
            **_technical().evidence_refs,
            "provider_auth": "preflight:synthetic-provider-auth-v1",
            "model_availability": "preflight:gemini-3.6-flash-v1",
            "independent_review": "review:synthetic-independent-review-v1",
        },
    })
    catalog = json.loads(CATALOG_PATH.read_text())
    skill = catalog["skills"][0]
    decision = evaluate_offline_qualification(
        approval,
        technical,
        now=datetime(2026, 8, 29, tzinfo=timezone.utc),
        skill_identity=(
            f"{skill['manifest']['skill_id']}@{skill['manifest']['version']}"
        ),
        definition_hash=skill["definition_hash"],
        catalog_hash=catalog["catalog_hash"],
        qualification_plan_sha256=_sha(PLAN_PATH),
        fixture_bundle_sha256=_sha(FIXTURE_PATH),
        model_policy_sha256=_sha(MODEL_POLICY_PATH),
    )
    assert decision.authorized
    assert decision.blockers == ()


@pytest.mark.parametrize("field,wrong,blocker", [
    ("definition_hash", "sha256:" + "0" * 64, "skill_pin_mismatch"),
    ("catalog_hash", "sha256:" + "0" * 64, "catalog_hash_mismatch"),
    (
        "qualification_plan_sha256",
        "sha256:" + "0" * 64,
        "qualification_plan_mismatch",
    ),
    (
        "fixture_bundle_sha256",
        "sha256:" + "0" * 64,
        "fixture_bundle_mismatch",
    ),
    (
        "model_policy_sha256",
        "sha256:" + "0" * 64,
        "model_policy_mismatch",
    ),
])
def test_any_hash_change_requires_a_new_founder_approval(field, wrong, blocker):
    decision = _decision(**{field: wrong})
    assert not decision.authorized
    assert blocker in decision.blockers


def test_new_skill_gate_or_expiry_cannot_reuse_the_approval():
    unknown = _decision(skill_identity="documents.another-skill@1.0.0")
    expired = _decision(now=datetime(2026, 9, 5, tzinfo=timezone.utc))

    assert "skill_pin_mismatch" in unknown.blockers
    assert "approval_expired" in expired.blockers
    payload = json.loads(APPROVAL_PATH.read_text())
    payload["gate"] = "GATE_G_APPROVAL_WAIT"
    with pytest.raises(ValidationError):
        FounderStageApproval.model_validate(payload)


@pytest.mark.parametrize("field", [
    "live_selection_authorized",
    "runtime_admission_authorized",
    "cloud_resource_mutation_authorized",
    "deployment_authorized",
    "canary_authorized",
    "action_or_effect_authority",
])
def test_release_approval_cannot_enable_runtime_or_effect_authority(field):
    payload = json.loads(APPROVAL_PATH.read_text())
    payload[field] = True
    with pytest.raises(ValidationError):
        FounderStageApproval.model_validate(payload)


def test_model_policy_is_synthetic_toolless_bounded_and_content_private():
    policy = json.loads(MODEL_POLICY_PATH.read_text())

    assert policy["model_id"] == "gemini-3.6-flash"
    assert policy["max_model_calls"] == _approval().max_model_calls == 40
    assert policy["max_estimated_cost_usd"] == 5.0
    assert policy["provider_retries"] == 0
    assert policy["tools"] == []
    assert policy["grounding"] is False
    assert policy["web_access"] is False
    assert policy["connector_access"] is False
    assert policy["external_data"] is False
    assert policy["real_user_data_allowed"] is False
    assert policy["secrets_allowed"] is False
    assert policy["prompt_logging"] is False
    assert policy["response_logging"] is False
    assert policy["runtime_activation_authority"] is False


def test_cost_preflight_is_worst_case_bounded_and_under_approval_cap():
    cost = json.loads(COST_PREFLIGHT_PATH.read_text())
    policy = json.loads(MODEL_POLICY_PATH.read_text())

    input_cost = (
        cost["max_model_calls"]
        * cost["max_input_tokens_per_call"]
        / 1_000_000
        * cost["input_usd_per_million_tokens"]
    )
    output_cost = (
        cost["max_model_calls"]
        * cost["max_output_tokens_per_call"]
        / 1_000_000
        * cost["output_usd_per_million_tokens"]
    )
    assert input_cost == pytest.approx(cost["max_input_cost_usd"])
    assert output_cost == pytest.approx(cost["max_output_cost_usd"])
    assert input_cost + output_cost == pytest.approx(cost["max_total_cost_usd"])
    assert cost["max_total_cost_usd"] * cost["contingency_multiplier"] == (
        pytest.approx(cost["max_cost_with_contingency_usd"])
    )
    assert cost["max_cost_with_contingency_usd"] < policy["max_estimated_cost_usd"]
    assert cost["approval_cost_cap_usd"] == policy["max_estimated_cost_usd"]
    assert cost["passed"] is True


def test_provider_preflight_is_exact_read_only_and_performed_no_inference():
    provider = json.loads(PROVIDER_PREFLIGHT_PATH.read_text())

    assert provider["project_id"] == "co-founder-506001"
    assert provider["project_lifecycle"] == "ACTIVE"
    assert provider["quota_project_id"] == provider["project_id"]
    assert provider["vertex_api"] == "aiplatform.googleapis.com"
    assert provider["vertex_api_enabled"] is True
    assert provider["model_resource"] == (
        "publishers/google/models/gemini-3.6-flash"
    )
    assert provider["model_available"] is True
    assert provider["billing_project_explicit"] is True
    assert provider["model_inference_performed"] is False
    assert provider["cloud_resource_mutation_performed"] is False
    assert provider["secrets_recorded"] is False
    assert provider["passed"] is True


def test_approved_files_contain_no_recognizable_secret_material():
    paths = (
        APPROVAL_PATH,
        TECHNICAL_PATH,
        COST_PREFLIGHT_PATH,
        PROVIDER_PREFLIGHT_PATH,
        MODEL_POLICY_PATH,
        PLAN_PATH,
        FIXTURE_PATH,
    )
    forbidden = (
        re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
        re.compile(r"sk-[0-9A-Za-z_-]{20,}"),
        re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),
        re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    for path in paths:
        text = path.read_text()
        assert not any(pattern.search(text) for pattern in forbidden), path.name


def test_plan_distinguishes_one_stage_approval_from_technical_success():
    plan = json.loads(PLAN_PATH.read_text())
    policy = plan["founder_stage_approval"]

    assert policy == {
        "mode": "ONE_EXACT_HASH_PINNED_APPROVAL_PER_GATE",
        "approval_ref": (
            "skills/approvals/spec40-gate-f-offline-qualification.json"
        ),
        "gate": "GATE_F_OFFLINE_QUALIFICATION",
        "covers_skill_count": 1,
        "technical_gates_remain_mandatory": True,
        "hash_or_scope_change_requires_new_approval": True,
        "runtime_or_effect_authority": False,
    }
    assert "independent_reviewer_decision" in plan["unresolved_dependencies"]
    assert plan["activation_authorized"] is False
    assert plan["model_evaluation_authorized"] is False
