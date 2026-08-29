"""Closed, versioned contracts for live and optional synthetic hiring flows."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.canonical import canonical_hash as _platform_canonical_hash
from services.workflow_contracts import RunKind  # noqa: F401

# ``RunKind`` remains a documented hiring-contract import during the Phase 0
# compatibility window.  The generic definition is authoritative; this
# re-export prevents existing hiring adapters from silently selecting a second
# enum or breaking at import time during the staged migration.

StrictText = Annotated[str, Field(min_length=1, max_length=4000)]
OpaqueId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_:-]{2,127}$")]
Hash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RoleState(str, Enum):
    DRAFT = "DRAFT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class CandidateState(str, Enum):
    RECEIVED = "RECEIVED"
    QUARANTINED = "QUARANTINED"
    ASSESSING = "ASSESSING"
    AWAITING_HUMAN_DECISION = "AWAITING_HUMAN_DECISION"
    HELD = "HELD"
    EVIDENCE_REQUESTED = "EVIDENCE_REQUESTED"
    ADVANCED = "ADVANCED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"
    CLOSED = "CLOSED"


class DecisionKind(str, Enum):
    ADVANCE = "ADVANCE"
    HOLD = "HOLD"
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    DECLINE = "DECLINE"
    REOPEN = "REOPEN"
    CORRECT = "CORRECT"


class EvidenceStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    CONTRADICTED = "CONTRADICTED"


class ContentRisk(str, Enum):
    CLEAR = "CLEAR"
    INJECTION_SUSPECTED = "INJECTION_SUSPECTED"
    WITHHELD = "WITHHELD"


class EvidenceAuthority(str, Enum):
    CANDIDATE_CLAIM = "CANDIDATE_CLAIM"
    INTERVIEW_OBSERVATION = "INTERVIEW_OBSERVATION"
    REFERENCE_CLAIM = "REFERENCE_CLAIM"
    FOUNDER_OBSERVATION = "FOUNDER_OBSERVATION"


class Verification(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    CORROBORATED = "CORROBORATED"
    CONTRADICTED = "CONTRADICTED"


class Criterion(ClosedModel):
    criterion_id: OpaqueId
    label: Annotated[str, Field(min_length=1, max_length=160)]
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    evidence_examples: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list, max_length=20)
    approved_question_ids: list[OpaqueId] = Field(default_factory=list, max_length=30)


class InterviewQuestion(ClosedModel):
    question_id: OpaqueId
    criterion_id: OpaqueId
    text: Annotated[str, Field(min_length=1, max_length=1000)]
    rubric: list[Annotated[str, Field(max_length=400)]] = Field(
        default_factory=list, max_length=12)


class RoleContract(ClosedModel):
    schema_version: Literal[1] = 1
    company_name: Annotated[str, Field(min_length=1, max_length=160)]
    role_title: Annotated[str, Field(min_length=1, max_length=160)]
    role_summary: Annotated[str, Field(min_length=1, max_length=3000)]
    headcount_target: Annotated[int, Field(ge=1, le=50)]
    target_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    location_envelope: list[Annotated[str, Field(max_length=120)]] = Field(
        min_length=1, max_length=20)
    compensation_envelope: Annotated[str, Field(min_length=1, max_length=500)]
    criteria: list[Criterion] = Field(min_length=1, max_length=30)
    interview_plan: list[InterviewQuestion] = Field(min_length=1, max_length=80)
    public_job_description: Annotated[str, Field(min_length=1, max_length=12000)]
    approved_reason_codes: list[
        Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
    ]
    prohibited_criteria: list[Annotated[str, Field(max_length=160)]] = Field(
        default_factory=list, max_length=100)
    notice_policy_id: OpaqueId
    retention_policy_id: OpaqueId
    jurisdiction_policy_id: OpaqueId

    @model_validator(mode="after")
    def _references_are_closed(self):
        criterion_ids = [item.criterion_id for item in self.criteria]
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("criterion ids must be unique")
        question_ids = [item.question_id for item in self.interview_plan]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("question ids must be unique")
        if any(question.criterion_id not in criterion_ids
               for question in self.interview_plan):
            raise ValueError("every interview question must reference a criterion")
        if len(set(self.approved_reason_codes)) != len(self.approved_reason_codes):
            raise ValueError("reason codes must be unique")
        return self


class EvidenceLocator(ClosedModel):
    page: Annotated[int, Field(ge=1, le=10000)] | None = None
    block: Annotated[str, Field(min_length=1, max_length=160)]


class EvidenceItem(ClosedModel):
    schema_version: Literal[1] = 1
    evidence_id: OpaqueId
    workspace_id: OpaqueId
    role_id: OpaqueId
    candidate_application_id: OpaqueId
    source_artifact_id: OpaqueId
    source_kind: Literal[
        "APPLICATION", "RESUME", "PORTFOLIO", "INTERVIEW", "REFERENCE", "FOUNDER_NOTE"
    ]
    criterion_ids: list[OpaqueId] = Field(min_length=1, max_length=20)
    locator: EvidenceLocator
    quote: Annotated[str, Field(max_length=800)] = ""
    normalized_fact: Annotated[str, Field(max_length=800)] = ""
    authority: EvidenceAuthority
    verification: Verification
    content_risk: ContentRisk
    source_sha256: Hash
    redaction_policy_version: Annotated[int, Field(ge=1)]
    created_at: datetime

    @model_validator(mode="after")
    def _bounded_claim(self):
        if self.content_risk is ContentRisk.CLEAR and not (self.quote or self.normalized_fact):
            raise ValueError("clear evidence requires a quote or normalized fact")
        if self.content_risk is not ContentRisk.CLEAR and (self.quote or self.normalized_fact):
            raise ValueError("withheld/risky evidence must not retain content")
        return self


class AnalystEvidenceRef(ClosedModel):
    evidence_id: OpaqueId
    evidence_hash: Hash


class AnalystInput(ClosedModel):
    schema_version: Literal[1] = 1
    invocation_id: OpaqueId
    workspace_id: OpaqueId
    role_id: OpaqueId
    candidate_application_id: OpaqueId
    candidate_code: Annotated[str, Field(pattern=r"^C-[0-9]{3,8}$")]
    policy_version_id: OpaqueId
    policy_hash: Hash
    criteria: list[Criterion] = Field(min_length=1, max_length=30)
    evidence: list[EvidenceItem] = Field(max_length=500)

    @model_validator(mode="after")
    def _identity_free_and_scoped(self):
        # Scan the envelope's FIELD NAMES, not its text. Scanning the serialized
        # document rejected ordinary job-related prose ("BA, Gender Studies",
        # "disability services coordinator") that redaction had already cleared,
        # which discarded the whole Evidence Passport. Protected content inside
        # a block is the redaction layer's job (hiring_evidence.redact_block).
        forbidden = {"candidate_name", "email_address", "phone_number",
                     "date_of_birth", "gender", "race", "ethnicity", "religion",
                     "disability", "email", "phone", "name", "address"}
        if _field_names(self.model_dump(mode="json")) & forbidden:
            raise ValueError("analyst input contains a forbidden identity/protected field")
        for item in self.evidence:
            if (item.workspace_id, item.role_id, item.candidate_application_id) != (
                    self.workspace_id, self.role_id, self.candidate_application_id):
                raise ValueError("cross-scope evidence is forbidden")
        return self


class CriterionAssessment(ClosedModel):
    criterion_id: OpaqueId
    status: EvidenceStatus
    citations: list[AnalystEvidenceRef] = Field(default_factory=list, max_length=50)
    contradictions: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=20)
    unknowns: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=20)
    summary: Annotated[str, Field(max_length=1000)]

    @model_validator(mode="after")
    def _unknown_is_not_scored(self):
        if self.status is EvidenceStatus.UNKNOWN and self.citations:
            raise ValueError("unknown criterion cannot carry supporting citations")
        return self


class AnalystOutput(ClosedModel):
    schema_version: Literal[1] = 1
    invocation_id: OpaqueId
    workspace_id: OpaqueId
    role_id: OpaqueId
    candidate_application_id: OpaqueId
    policy_version_id: OpaqueId
    policy_hash: Hash
    criteria: list[CriterionAssessment] = Field(min_length=1, max_length=30)
    withheld_evidence_ids: list[OpaqueId] = Field(default_factory=list, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def _reject_disguised_ranking(cls, value):
        if isinstance(value, dict):
            prohibited = {
                "score", "total_score", "rank", "ranking", "recommendation",
                "hire_recommendation", "reject_recommendation", "culture_fit",
                "personality", "best_candidate", "percentile",
            }
            if _field_names(value) & prohibited:
                raise ValueError("scoring, ranking, recommendation and proxy fields are forbidden")
        return value


class HumanDecisionInput(ClosedModel):
    schema_version: Literal[1] = 1
    decision: DecisionKind
    reason_codes: list[
        Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
    ]
    evidence_ids_reviewed: list[OpaqueId] = Field(default_factory=list, max_length=500)
    assessment_id: OpaqueId
    expected_application_version: Annotated[int, Field(ge=1)]
    client_request_id: OpaqueId
    note: Annotated[str, Field(max_length=1000)] = ""
    supersedes_decision_id: OpaqueId | None = None

    @field_validator("decision", mode="before")
    @classmethod
    def _wire_decision(cls, value):
        # FastAPI presents a parsed JSON string to Pydantic's Python path;
        # convert only the exact closed enum, never arbitrary coercions.
        return DecisionKind(value) if isinstance(value, str) else value


class SyntheticEnvelope(ClosedModel):
    synthetic: Literal[True]
    synthetic_namespace: Annotated[str, Field(pattern=r"^synthetic_hiring_[a-z0-9_-]{1,80}$")]
    fixture_id: OpaqueId
    human_reviewed: bool


class SyntheticIdentity(ClosedModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    email: Annotated[str, Field(min_length=3, max_length=320)]
    phone: Annotated[str, Field(max_length=80)] = ""
    provider_candidate_id: Annotated[str, Field(max_length=200)] = ""


class SyntheticEvidenceBlock(ClosedModel):
    block_id: OpaqueId
    source_kind: Literal[
        "APPLICATION", "RESUME", "PORTFOLIO", "INTERVIEW", "REFERENCE", "FOUNDER_NOTE"
    ]
    text: Annotated[str, Field(max_length=4000)]
    criterion_ids: list[OpaqueId] = Field(min_length=1, max_length=20)
    classification_confidence: Annotated[float, Field(ge=0, le=1)] = 1.0
    classifier_disagreed: bool = False


class SyntheticFixtureMessage(ClosedModel):
    schema_version: Literal[1] = 1
    message_id: OpaqueId
    thread_id: OpaqueId
    connection_id: OpaqueId
    provider_internal_date: Annotated[
        str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T[^\s]{5,40}$")]
    provider_label_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        max_length=100)
    provider_route_id: Annotated[str, Field(min_length=1, max_length=200)]
    delivery_authentic: bool
    raw_recipient_header: Annotated[str, Field(max_length=500)]
    identity: SyntheticIdentity
    blocks: list[SyntheticEvidenceBlock] = Field(max_length=200)
    source_sha256: Hash


def _field_names(value: object) -> set[str]:
    """Collect every lowercased key name in a nested JSON-safe structure."""
    names: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            names.add(str(key).lower())
            names |= _field_names(nested)
    elif isinstance(value, list):
        for nested in value:
            names |= _field_names(nested)
    return names


def canonical_hash(value: BaseModel | dict) -> str:
    return _platform_canonical_hash(value, domain="hiring-contract")


def stable_id(prefix: str, *parts: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,20}", prefix):
        raise ValueError("invalid id prefix")
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:28]
    return f"{prefix}_{digest}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
