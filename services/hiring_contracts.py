"""Closed, versioned contracts for the synthetic-only hiring foundation."""

from __future__ import annotations

import hashlib
import re
import unicodedata
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
    DISCARDED = "DISCARDED"
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
    WAITING_FOR_INTERVIEW = "WAITING_FOR_INTERVIEW"
    INTERVIEW_EVIDENCE_READY = "INTERVIEW_EVIDENCE_READY"
    AWAITING_INTERVIEW_DECISION = "AWAITING_INTERVIEW_DECISION"
    AWAITING_REFERENCE_PERMISSION = "AWAITING_REFERENCE_PERMISSION"
    REFERENCES_IN_PROGRESS = "REFERENCES_IN_PROGRESS"
    REFERENCE_EVIDENCE_READY = "REFERENCE_EVIDENCE_READY"
    AWAITING_FINAL_DECISION = "AWAITING_FINAL_DECISION"
    AWAITING_OFFER_APPROVAL = "AWAITING_OFFER_APPROVAL"
    WAITING_FOR_OFFER_RESPONSE = "WAITING_FOR_OFFER_RESPONSE"
    OFFER_ACCEPTED = "OFFER_ACCEPTED"
    OFFER_DECLINED = "OFFER_DECLINED"
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


class PostInterviewDecisionKind(str, Enum):
    ADDITIONAL_INTERVIEW = "ADDITIONAL_INTERVIEW"
    ADVANCE_TO_REFERENCES = "ADVANCE_TO_REFERENCES"
    ADVANCE_TO_OFFER_WITH_REFERENCE_WAIVER = (
        "ADVANCE_TO_OFFER_WITH_REFERENCE_WAIVER"
    )
    PREPARE_CONDITIONAL_OFFER = "PREPARE_CONDITIONAL_OFFER"
    ADVANCE_TO_OFFER = "ADVANCE_TO_OFFER"
    HOLD = "HOLD"
    DECLINE = "DECLINE"


class OfferResponseKind(str, Enum):
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"


class OnboardingState(str, Enum):
    ONBOARDING_INTAKE = "ONBOARDING_INTAKE"
    AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
    PRE_START = "PRE_START"
    WAITING_FOR_START_DATE = "WAITING_FOR_START_DATE"
    FIRST_DAY = "FIRST_DAY"
    FIRST_WEEK = "FIRST_WEEK"
    AWAITING_COMPLETION_REVIEW = "AWAITING_COMPLETION_REVIEW"
    COMPLETE = "COMPLETE"
    CLOSED = "CLOSED"


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


class InterviewCriterionEvidenceInput(ClosedModel):
    criterion_id: OpaqueId
    observed_fact: Annotated[str, Field(max_length=1200)] = ""
    unknowns: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=20)
    contradictions: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=20)
    source_locator: Annotated[str, Field(max_length=240)] = ""

    @model_validator(mode="after")
    def _requires_bounded_evidence(self):
        if not (self.observed_fact.strip() or self.unknowns or self.contradictions):
            raise ValueError("interview evidence cannot be empty")
        return self


class InterviewEvidenceInput(ClosedModel):
    schema_version: Literal[1] = 1
    interview_event_id: Annotated[str, Field(min_length=3, max_length=512)]
    criteria: list[InterviewCriterionEvidenceInput] = Field(
        min_length=1, max_length=30)
    founder_note: Annotated[str, Field(max_length=2000)] = ""
    transcript_consent: bool = False
    transcript_artifact_id: OpaqueId | None = None
    client_request_id: OpaqueId
    expected_application_version: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _transcript_requires_consent(self):
        if self.transcript_artifact_id and not self.transcript_consent:
            raise ValueError("interview transcript requires recorded consent")
        return self


class PostInterviewDecisionInput(ClosedModel):
    schema_version: Literal[1] = 1
    decision: PostInterviewDecisionKind
    reason_codes: list[
        Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
    ] = Field(min_length=1, max_length=20)
    interview_id: OpaqueId
    note: Annotated[str, Field(max_length=1000)] = ""
    client_request_id: OpaqueId
    expected_application_version: Annotated[int, Field(ge=1)]


class ReferencePermissionInput(ClosedModel):
    schema_version: Literal[1] = 1
    permission_granted: Literal[True]
    permission_receipt_ref: Annotated[str, Field(min_length=3, max_length=512)]
    reference_contact_ref: OpaqueId
    reference_label: Annotated[str, Field(min_length=1, max_length=160)]
    approved_questions: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        min_length=1, max_length=20)
    criterion_ids: list[OpaqueId] = Field(min_length=1, max_length=30)
    client_request_id: OpaqueId
    expected_application_version: Annotated[int, Field(ge=1)]


class ReferenceContactInput(ClosedModel):
    """Candidate-provided reference contact encrypted outside general records."""

    schema_version: Literal[1] = 1
    name: Annotated[str, Field(min_length=1, max_length=200)]
    email: Annotated[str, Field(
        min_length=3, max_length=320,
        pattern=r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")]
    label: Annotated[str, Field(min_length=1, max_length=160)]
    client_request_id: OpaqueId


class ReferenceOutreachExecutionInput(ClosedModel):
    """Consume one exact Founder approval for one reference request."""

    schema_version: Literal[1] = 1
    reference_check_id: OpaqueId
    approval_id: OpaqueId
    client_request_id: OpaqueId


class ReferenceEvidenceInput(ClosedModel):
    schema_version: Literal[1] = 1
    reference_check_id: OpaqueId
    response_token: Annotated[str, Field(min_length=20, max_length=512)]
    criterion_id: OpaqueId
    claim: Annotated[str, Field(min_length=1, max_length=1200)]
    source_locator: Annotated[str, Field(min_length=1, max_length=240)]
    contradictions: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=20)
    unknowns: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=20)
    client_request_id: OpaqueId


class OfferDraftInput(ClosedModel):
    schema_version: Literal[1] = 1
    title: Annotated[str, Field(min_length=1, max_length=160)]
    start_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    compensation: Annotated[str, Field(min_length=1, max_length=500)]
    employment_terms: Annotated[str, Field(min_length=1, max_length=4000)]
    reference_condition: Literal[
        "COMPLETED_PRE_OFFER",
        "WAIVED_BY_FOUNDER",
        "REQUIRED_BEFORE_START",
    ] = "COMPLETED_PRE_OFFER"
    document_artifact_id: OpaqueId
    document_sha256: Hash
    client_request_id: OpaqueId
    expected_application_version: Annotated[int, Field(ge=1)]


class OfferApprovalInput(ClosedModel):
    schema_version: Literal[1] = 1
    offer_id: OpaqueId
    approval_id: OpaqueId
    client_request_id: OpaqueId


class OfferSignatureEventInput(ClosedModel):
    schema_version: Literal[1] = 1
    offer_id: OpaqueId
    provider_event_id: OpaqueId
    provider_envelope_sha256: Hash
    offer_sha256: Hash
    response: OfferResponseKind
    occurred_at: Annotated[str, Field(min_length=20, max_length=80)]


class OnboardingItemInput(ClosedModel):
    item_id: OpaqueId
    label: Annotated[str, Field(min_length=1, max_length=240)]
    owner: Literal["FOUNDER", "NEW_HIRE", "ALEX"]
    due_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    action_kind: Literal["CHECKLIST", "MEETING_PREPARATION", "ACCESS_REQUEST"]


class OnboardingPlanInput(ClosedModel):
    schema_version: Literal[1] = 1
    onboarding_run_id: OpaqueId
    start_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    items: list[OnboardingItemInput] = Field(min_length=1, max_length=100)
    client_request_id: OpaqueId
    expected_onboarding_version: Annotated[int, Field(ge=1)]


class OnboardingItemResolutionInput(ClosedModel):
    schema_version: Literal[1] = 1
    onboarding_run_id: OpaqueId
    item_id: OpaqueId
    resolution: Literal["COMPLETE", "WAIVED"]
    note: Annotated[str, Field(max_length=1000)] = ""
    client_request_id: OpaqueId
    expected_item_version: Annotated[int, Field(ge=1)]


class OnboardingProgressInput(ClosedModel):
    """Founder-controlled lifecycle transition for one onboarding child run."""

    schema_version: Literal[1] = 1
    onboarding_run_id: OpaqueId
    transition: Literal[
        "SYNC_START_DATE",
        "COMPLETE_FIRST_DAY",
        "REQUEST_COMPLETION_REVIEW",
        "COMPLETE_ONBOARDING",
    ]
    note: Annotated[str, Field(max_length=1000)] = ""
    client_request_id: OpaqueId
    expected_onboarding_version: Annotated[int, Field(ge=1)]


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


def normalize_operating_jurisdiction(value: str) -> str:
    """Normalize the advertised job location used as an operating label.

    This is a version binding, not legal advice and not evidence that any law
    or regulatory review occurred.  The Founder changes it only by approving
    a new role-package version.
    """
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = re.sub(r"\s+", " ", normalized).strip(" \t\r\n,.;:")
    if not normalized or len(normalized) > 120:
        raise ValueError("an advertised operating jurisdiction is required")
    return normalized


def jurisdiction_policy_id(value: str) -> str:
    """Return an opaque id for an advertised-location jurisdiction label."""
    normalized = normalize_operating_jurisdiction(value)
    return stable_id("jurisdiction", normalized.casefold())


def build_jurisdiction_binding(
        *, role_id: str, policy_version_id: str, policy_hash: str,
        role_description_hash: str, advertised_location: str) -> dict:
    """Bind one approved package version to its normalized operating label."""
    jurisdiction = normalize_operating_jurisdiction(advertised_location)
    payload = {
        "schema_version": 1,
        "source": "APPROVED_ROLE_PACKAGE",
        "operating_jurisdiction": jurisdiction,
        "jurisdiction_policy_id": jurisdiction_policy_id(jurisdiction),
        "role_id": role_id,
        "policy_version_id": policy_version_id,
        "policy_hash": policy_hash,
        "role_description_hash": role_description_hash,
        "legal_advice": False,
        "legal_review_claimed": False,
    }
    return {**payload, "binding_sha256": canonical_hash(payload)}


def verified_jurisdiction_binding(
        *, role: dict, policy: dict, application: dict | None = None) -> dict | None:
    """Return the exact current binding, or ``None`` on any stale projection."""
    if role.get("synthetic") is not False or policy.get("synthetic") is not False:
        return None
    if (policy.get("status") != "APPROVED"
            or role.get("current_policy_version_id") !=
            policy.get("policy_version_id")
            or role.get("current_policy_hash") != policy.get("canonical_hash")):
        return None
    description = dict(policy.get("role_description") or {})
    description_hash = str(policy.get("role_description_hash") or "")
    if not description_hash or canonical_hash(description) != description_hash:
        return None
    try:
        expected = build_jurisdiction_binding(
            role_id=str(role.get("role_id") or ""),
            policy_version_id=str(policy.get("policy_version_id") or ""),
            policy_hash=str(policy.get("canonical_hash") or ""),
            role_description_hash=description_hash,
            advertised_location=str(description.get("location") or ""),
        )
    except ValueError:
        return None
    for record in (policy, role):
        if (record.get("operating_jurisdiction") !=
                expected["operating_jurisdiction"]
                or record.get("jurisdiction_policy_id") !=
                expected["jurisdiction_policy_id"]
                or record.get("jurisdiction_binding_sha256") !=
                expected["binding_sha256"]
                or record.get("jurisdiction_binding") != expected):
            return None
    if application is not None and (
            application.get("synthetic") is not False
            or application.get("current_policy_version_id") !=
            expected["policy_version_id"]
            or application.get("current_policy_hash") != expected["policy_hash"]
            or application.get("operating_jurisdiction") !=
            expected["operating_jurisdiction"]
            or application.get("jurisdiction_binding_sha256") !=
            expected["binding_sha256"]):
        return None
    return expected


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
