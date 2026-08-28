"""Domain-neutral contracts for durable workflow execution."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol


class RuntimeStatus(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"

    # Source-compatible names during the explicit legacy read window. New
    # writes serialize only the target values on the right.
    ACTIVE = "QUEUED"
    DORMANT = "WAITING"
    COMPLETED = "SUCCEEDED"


class RunKind(str, Enum):
    ROLE = "ROLE"
    CANDIDATE = "CANDIDATE"
    ONBOARDING = "ONBOARDING"
    OPPORTUNITY_DISCOVERY = "OPPORTUNITY_DISCOVERY"
    GRANT_APPLICATION = "GRANT_APPLICATION"
    INVESTOR_OUTREACH = "INVESTOR_OUTREACH"
    EXTERNAL_CONSEQUENCE = "EXTERNAL_CONSEQUENCE"


LEGACY_RUNTIME_STATUS: dict[str, str] = {
    "ACTIVE": RuntimeStatus.QUEUED.value,
    "DORMANT": RuntimeStatus.WAITING.value,
    "PAUSED": RuntimeStatus.PAUSED.value,
    "CANCELLING": RuntimeStatus.CANCELLING.value,
    "CANCELLED": RuntimeStatus.CANCELLED.value,
    "COMPLETED": RuntimeStatus.SUCCEEDED.value,
    "FAILED": RuntimeStatus.FAILED.value,
}


def normalize_runtime_status(value: str) -> str:
    """Total dual-reader for legacy and current runtime status values."""
    text = str(value or "")
    if text in LEGACY_RUNTIME_STATUS:
        return LEGACY_RUNTIME_STATUS[text]
    try:
        return RuntimeStatus(text).value
    except ValueError as exc:
        raise ValueError("unknown runtime status") from exc


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_kind: str
    run_kind: RunKind
    version: str
    allowed_parent_kinds: frozenset[RunKind]
    plan_schema_version: int = 1
    policy_version: str = "platform-default-v1"
    capability_registry_version: str = "phase0-minimum-v1"
    domain: str = "platform"


WORKFLOW_DEFINITIONS: dict[str, WorkflowDefinition] = {
    "hiring_role:v1": WorkflowDefinition(
        "hiring_role:v1", RunKind.ROLE, "1", frozenset(), domain="hiring"),
    "hiring_candidate:v1": WorkflowDefinition(
        "hiring_candidate:v1", RunKind.CANDIDATE, "1",
        frozenset({RunKind.ROLE}), domain="hiring"),
    "hiring_onboarding:v1": WorkflowDefinition(
        "hiring_onboarding:v1", RunKind.ONBOARDING, "1",
        frozenset({RunKind.CANDIDATE}), domain="hiring"),
    "opportunity_discovery:v1": WorkflowDefinition(
        "opportunity_discovery:v1", RunKind.OPPORTUNITY_DISCOVERY, "1",
        frozenset(), domain="opportunity"),
    "grant_application:v1": WorkflowDefinition(
        "grant_application:v1", RunKind.GRANT_APPLICATION, "1",
        frozenset(), domain="grant"),
    "investor_outreach:v1": WorkflowDefinition(
        "investor_outreach:v1", RunKind.INVESTOR_OUTREACH, "1",
        frozenset(), domain="investor"),
    "external_consequence:v1": WorkflowDefinition(
        "external_consequence:v1", RunKind.EXTERNAL_CONSEQUENCE, "1",
        frozenset(), domain="platform"),
}


class WorkflowDomainAdapter(Protocol):
    """Domain-owned admission/provenance checks injected into the runtime."""

    def validate_run(self, *, definition: WorkflowDefinition,
                     workspace_id: str, domain_ref: str,
                     provenance: Mapping[str, Any]) -> dict[str, Any]: ...


class WorkflowPolicy(Protocol):
    """Code-owned policy hook; model output cannot bypass this decision."""

    def validate_run(self, *, definition: WorkflowDefinition,
                     workspace_id: str, originating_actor_id: str) -> dict[str, Any]: ...


class PlatformDomainAdapter:
    """Default adapter for production workflows; hiring has its own gate."""

    def validate_run(self, *, definition: WorkflowDefinition,
                     workspace_id: str, domain_ref: str,
                     provenance: Mapping[str, Any]) -> dict[str, Any]:
        if not workspace_id or not domain_ref:
            return {"status": "error", "error": True,
                    "error_code": "run_contract_invalid",
                    "message": "Workspace and domain reference are required."}
        if definition.domain == "hiring":
            return {"status": "error", "error": True,
                    "error_code": "production_hiring_disabled",
                    "message": "Hiring requires its reviewed domain adapter."}
        if provenance.get("provenance_class") != "PRODUCTION":
            return {"status": "error", "error": True,
                    "error_code": "provenance_invalid",
                    "message": "The platform adapter accepts production provenance only."}
        return {"status": "success"}


class DefaultWorkflowPolicy:
    def validate_run(self, *, definition: WorkflowDefinition,
                     workspace_id: str, originating_actor_id: str) -> dict[str, Any]:
        del definition, originating_actor_id
        if not workspace_id:
            return {"status": "error", "error": True,
                    "error_code": "workspace_required",
                    "message": "A workspace is required."}
        return {"status": "success"}

WAIT_CONTRACTS: frozenset[str] = frozenset({
    "APPLICATION_EMAIL", "EMAIL_REPLY", "LONG_DELAY", "H4S_TEST_REPLY",
    "FOUNDER_APPROVAL", "FOUNDER_FEEDBACK", "PORTAL_CONFIRMATION",
    "PROVIDER_EVENT", "DEADLINE_TICK", "TIMER",
})

# Reviewed static templates only. Model-authored/composed plan IR remains
# disabled; Phase 5 may mature these descriptors after the second vertical.
PLAN_TEMPLATES: dict[str, tuple[str, ...]] = {
    "hiring_role:v1": ("define_role", "publish", "wait_for_applications"),
    "hiring_candidate:v1": ("ingest", "assess", "human_decision"),
    "hiring_onboarding:v1": ("prepare_onboarding", "human_decision"),
    "opportunity_discovery:v1": ("discover", "score", "publish_receipt"),
    "grant_application:v1": (
        "interview", "draft", "fill", "human_approval", "submit", "wait"),
    "investor_outreach:v1": (
        "research", "rank", "draft", "human_approval", "send", "wait_reply",
        "meeting_brief"),
    "external_consequence:v1": (
        "human_approval", "execute", "reconcile"),
}

_DEFAULT_BY_RUN_KIND = {
    definition.run_kind: definition.workflow_kind
    for definition in WORKFLOW_DEFINITIONS.values()
}


def resolve_workflow_definition(
        run_kind: RunKind | str, workflow_kind: str = "", *,
        registry: Mapping[str, WorkflowDefinition] | None = None
        ) -> WorkflowDefinition:
    try:
        closed_kind = run_kind if isinstance(run_kind, RunKind) else RunKind(str(run_kind))
    except ValueError as exc:
        raise ValueError("unregistered run kind") from exc
    key = workflow_kind or _DEFAULT_BY_RUN_KIND.get(closed_kind, "")
    definition = (registry or WORKFLOW_DEFINITIONS).get(key)
    if not definition or definition.run_kind is not closed_kind:
        raise ValueError("unregistered workflow kind")
    return definition


def stable_id(prefix: str, *parts: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,20}", prefix):
        raise ValueError("invalid id prefix")
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:28]
    return f"{prefix}_{digest}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
