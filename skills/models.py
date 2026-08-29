"""Closed, immutable contracts for the repository-owned skills catalog."""

from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # Python 3.10 in the pinned Cloud Run base image.
    from enum import Enum

    class StrEnum(str, Enum):
        """Minimal compatibility base for string-valued closed enums."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Lifecycle(StrEnum):
    DRAFT = "DRAFT"
    QUALIFIED = "QUALIFIED"
    CANARY = "CANARY"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    DISABLED = "DISABLED"
    RETIRED = "RETIRED"


class EffectClass(StrEnum):
    NO_EFFECT = "NO_EFFECT"
    READ_ONLY = "READ_ONLY"
    INTERNAL_REVERSIBLE = "INTERNAL_REVERSIBLE"
    EXTERNAL_REVERSIBLE = "EXTERNAL_REVERSIBLE"
    EXTERNAL_CONSEQUENTIAL = "EXTERNAL_CONSEQUENTIAL"
    FINANCIAL_OR_LEGAL = "FINANCIAL_OR_LEGAL"
    PROHIBITED = "PROHIBITED"


class GoalContract(ClosedModel):
    supported_intents: tuple[str, ...] = Field(min_length=1)
    invocation_modes: tuple[Literal["CONVERSATION", "WORKFLOW_STEP"], ...] = Field(min_length=1)
    non_goals: tuple[str, ...] = ()


class Assignments(ClosedModel):
    agent_roles: tuple[str, ...] = Field(min_length=1)
    workflow_steps: tuple[str, ...] = Field(min_length=1)


class ContractRefs(ClosedModel):
    input_schema_id: str
    input_schema_path: str
    output_schema_id: str
    output_schema_path: str
    error_schema_id: str
    completion_contract_id: str
    context_contract_id: str
    evidence_contract_id: str
    citation_policy_id: str


class CapabilityPin(ClosedModel):
    capability_id: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


class EffectPolicy(ClosedModel):
    ceiling: EffectClass
    authority_impacts: tuple[str, ...] = Field(min_length=1)


class Capabilities(ClosedModel):
    allow: tuple[CapabilityPin, ...] = Field(min_length=1)
    effect_policy: EffectPolicy
    required_permissions: tuple[str, ...] = ()


class SkillPolicy(ClosedModel):
    precondition_ids: tuple[str, ...] = Field(min_length=1)
    approval_policy_id: str
    accepted_data_classes: tuple[str, ...] = Field(min_length=1)
    produced_data_classes: tuple[str, ...] = Field(min_length=1)
    tenant_scope: Literal["CURRENT_WORKSPACE"]
    required_feature_flags: tuple[str, ...] = ()


class ExecutionLimits(ClosedModel):
    max_tool_calls: int = Field(ge=0, le=100)
    max_resource_reads: int = Field(ge=0, le=20)
    max_resource_bytes: int = Field(ge=0, le=1_048_576)
    timeout_seconds: int = Field(ge=1, le=900)
    retry_policy_id: str
    idempotency_contract_id: str
    context_token_budget: int = Field(ge=1, le=200_000)
    output_size_bytes: int = Field(ge=1, le=1_048_576)


class Provenance(ClosedModel):
    playbook_path: str
    decision_ref: str


class ResourceDecl(ClosedModel):
    resource_id: str
    path: str
    media_type: Literal["text/markdown", "application/json", "text/plain"]
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    purpose: str
    model_visibility: Literal["ON_DEMAND"]
    max_bytes: int = Field(ge=1, le=1_048_576)


class Evaluation(ClosedModel):
    suite_ids: tuple[str, ...] = Field(min_length=1)
    release_gate_id: str
    qualification_path: str


class LifecyclePolicy(ClosedModel):
    rollout_policy_id: str
    rollback_to: str | None = None
    replaces: str | None = None
    deprecated_after: str | None = None


class SkillManifest(ClosedModel):
    schema_version: Literal["cofounder.skill.v1"]
    skill_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    status: Lifecycle
    owner: str = Field(min_length=3)
    engineering_owner: str = Field(min_length=3)
    evaluation_owner: str = Field(min_length=3)
    summary: str = Field(min_length=10, max_length=240)
    goal_contract: GoalContract
    assignments: Assignments
    contracts: ContractRefs
    capabilities: Capabilities
    policy: SkillPolicy
    execution: ExecutionLimits
    provenance: Provenance
    resources: tuple[ResourceDecl, ...] = ()
    evaluation: Evaluation
    lifecycle: LifecyclePolicy

    @model_validator(mode="after")
    def unique_references(self) -> "SkillManifest":
        pins = [(item.capability_id, item.version) for item in self.capabilities.allow]
        paths = [item.path for item in self.resources]
        ids = [item.resource_id for item in self.resources]
        if len(pins) != len(set(pins)):
            raise ValueError("duplicate capability pin")
        if len(paths) != len(set(paths)) or len(ids) != len(set(ids)):
            raise ValueError("duplicate resource path or id")
        return self


class QualificationEvidence(ClosedModel):
    schema_version: Literal["cofounder.skill-qualification.v1"]
    evidence_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    status: Literal["NOT_RUN", "PASSED", "FAILED"]
    skill_id: str
    skill_version: str
    model_policy_version: str
    suite_result_refs: tuple[str, ...]
    capability_set_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CompiledSkill(ClosedModel):
    manifest: SkillManifest
    definition_hash: str
    playbook_hash: str
    schema_hashes: dict[str, str]
    resource_hashes: dict[str, str]
    qualification: QualificationEvidence
    qualification_hash: str
    package_path: str

    @property
    def identity(self) -> str:
        return f"{self.manifest.skill_id}@{self.manifest.version}"


class SkillCard(ClosedModel):
    skill_id: str
    version: str
    definition_hash: str
    status: Lifecycle
    summary: str
    supported_intents: tuple[str, ...]
    invocation_modes: tuple[str, ...]
    agent_roles: tuple[str, ...]
    workflow_steps: tuple[str, ...]


class CompiledCatalog(ClosedModel):
    schema_version: Literal["cofounder.skill-catalog.v1"] = "cofounder.skill-catalog.v1"
    catalog_hash: str
    skills: tuple[CompiledSkill, ...]

    def by_identity(self) -> dict[str, CompiledSkill]:
        return {skill.identity: skill for skill in self.skills}

    def cards(self) -> tuple[SkillCard, ...]:
        return tuple(SkillCard(
            skill_id=s.manifest.skill_id,
            version=s.manifest.version,
            definition_hash=s.definition_hash,
            status=s.manifest.status,
            summary=s.manifest.summary,
            supported_intents=s.manifest.goal_contract.supported_intents,
            invocation_modes=s.manifest.goal_contract.invocation_modes,
            agent_roles=s.manifest.assignments.agent_roles,
            workflow_steps=s.manifest.assignments.workflow_steps,
        ) for s in self.skills)


class SkillError(ClosedModel):
    status: Literal["error"] = "error"
    error: Literal[True] = True
    error_code: str
    message: str
    retryable: bool = False
    correlation_id: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
