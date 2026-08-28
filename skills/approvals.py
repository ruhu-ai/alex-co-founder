"""Hash-pinned Founder release approval with non-bypassable technical gates."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApprovedSkillPin(_ClosedModel):
    identity: str
    definition_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class FounderStageApproval(_ClosedModel):
    schema_version: Literal["cofounder.founder-stage-approval.v1"]
    approval_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{7,127}$")
    approval_status: Literal["APPROVED"]
    principal_role: Literal["FOUNDER"]
    approval_kind: Literal["RELEASE_STAGE_ONLY"]
    gate: Literal["GATE_F_OFFLINE_QUALIFICATION"]
    approved_at: datetime
    expires_at: datetime
    skills: tuple[ApprovedSkillPin, ...] = Field(min_length=1, max_length=8)
    catalog_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    qualification_plan_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    fixture_bundle_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_policy_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    allowed_operations: tuple[
        Literal[
            "RECORD_INDEPENDENT_REVIEW",
            "RUN_SYNTHETIC_OFFLINE_MODEL_QUALIFICATION",
            "RECORD_QUALIFICATION_EVIDENCE",
        ], ...
    ]
    max_model_calls: int = Field(ge=1, le=40)
    synthetic_only: Literal[True]
    live_selection_authorized: Literal[False]
    runtime_admission_authorized: Literal[False]
    cloud_resource_mutation_authorized: Literal[False]
    deployment_authorized: Literal[False]
    canary_authorized: Literal[False]
    action_or_effect_authority: Literal[False]

    @model_validator(mode="after")
    def validate_scope(self) -> "FounderStageApproval":
        if self.expires_at <= self.approved_at:
            raise ValueError("Founder stage approval must expire after approval")
        identities = [pin.identity for pin in self.skills]
        if len(identities) != len(set(identities)):
            raise ValueError("Founder stage approval contains a duplicate skill")
        if set(self.allowed_operations) != {
            "RECORD_INDEPENDENT_REVIEW",
            "RUN_SYNTHETIC_OFFLINE_MODEL_QUALIFICATION",
            "RECORD_QUALIFICATION_EVIDENCE",
        }:
            raise ValueError("Founder stage approval operation set is incomplete")
        return self


class TechnicalGateState(_ClosedModel):
    schema_version: Literal["cofounder.gate-f-technical-state.v1"]
    canonical_specs_pinned: bool
    catalog_reproducible: bool
    contract_catalog_closed: bool
    fixtures_frozen: bool
    synthetic_only: bool
    secret_scan_passed: bool
    model_policy_pinned: bool
    provider_auth_verified: bool
    model_availability_verified: bool
    cost_budget_pinned: bool
    cost_preflight_passed: bool
    data_governance_pinned: bool
    live_surfaces_disabled: bool
    independent_review_complete: bool
    evidence_refs: dict[str, str]

    def blockers(self) -> tuple[str, ...]:
        checks = {
            name: value
            for name, value in self.model_dump().items()
            if name not in {"schema_version", "evidence_refs"}
        }
        return tuple(sorted(name for name, passed in checks.items() if not passed))


class ApprovalDecision(_ClosedModel):
    authorized: bool
    blockers: tuple[str, ...]


def evaluate_offline_qualification(
    approval: FounderStageApproval,
    technical: TechnicalGateState,
    *,
    now: datetime,
    skill_identity: str,
    definition_hash: str,
    catalog_hash: str,
    qualification_plan_sha256: str,
    fixture_bundle_sha256: str,
    model_policy_sha256: str,
) -> ApprovalDecision:
    """Authorize only when the one Founder approval and every technical gate pass."""

    blockers = list(technical.blockers())
    if now < approval.approved_at:
        blockers.append("approval_not_yet_valid")
    if now >= approval.expires_at:
        blockers.append("approval_expired")
    pins = {pin.identity: pin.definition_hash for pin in approval.skills}
    if pins.get(skill_identity) != definition_hash:
        blockers.append("skill_pin_mismatch")
    expected_hashes = {
        "catalog_hash_mismatch": (approval.catalog_hash, catalog_hash),
        "qualification_plan_mismatch": (
            approval.qualification_plan_sha256, qualification_plan_sha256,
        ),
        "fixture_bundle_mismatch": (
            approval.fixture_bundle_sha256, fixture_bundle_sha256,
        ),
        "model_policy_mismatch": (
            approval.model_policy_sha256, model_policy_sha256,
        ),
    }
    blockers.extend(
        code for code, (expected, actual) in expected_hashes.items()
        if expected != actual
    )
    ordered = tuple(sorted(set(blockers)))
    return ApprovalDecision(authorized=not ordered, blockers=ordered)
