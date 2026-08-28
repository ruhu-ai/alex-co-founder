"""Closed durable background-work contract for the founder artifact pilot.

The only live-eligible template inventories one already-authorized, immutable
artifact. It has no model, provider, connector, browser, approval, effect,
memory, URL, or model-authored routing surface.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping

from services import capability_registry
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.canonical import canonical_hash
from services.command_service import CommandService
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.workflow_contracts import (
    BACKGROUND_FOUNDATION_NEGATIVE_CONSTRAINTS,
    RunKind,
    run_visible_to_actor,
    stable_id,
    utc_now,
)
from services.workflow_runtime import WorkflowRuntime


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "status": "error", "error": True, "error_code": code,
        "message": message, "retryable": retryable,
    }


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class BackgroundWorkClass(str, Enum):
    DETACHED_READ = "DETACHED_READ"


@dataclass(frozen=True)
class BackgroundInputRef:
    """One immutable internal input identity; content never enters task bodies."""

    kind: str
    ref_id: str
    version: str
    content_hash: str


@dataclass(frozen=True)
class BackgroundJobRequest:
    client_request_id: str
    template_id: str
    template_version: str
    objective_kind: str
    objective_summary: str
    origin_message_id: str
    origin_session_id: str
    input_refs: tuple[BackgroundInputRef, ...] = ()
    confirmed_negative_constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackgroundTemplate:
    template_id: str
    version: str
    status: str
    admission_enabled: bool
    workflow_kind: str
    objective_kind: str
    work_class: BackgroundWorkClass
    capability_ids: tuple[str, ...]
    allowed_input_kinds: frozenset[str]
    negative_constraints: tuple[str, ...]
    completion_contract_id: str
    milestone_policy_id: str
    retryable_error_codes: frozenset[str]
    max_attempts: int
    budgets: Mapping[str, int]


POLICY_VERSION = "background-artifact-pilot-eligibility-v1"
VISIBILITY_POLICY_VERSION = "actor-private-background-pilot-v1"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
_INPUT_KINDS = frozenset({"WORKSPACE_ARTIFACT"})
FOUNDER_CONSTRAINT_CODES = frozenset({
    "NO_PERSONAL_DATA", "NO_UNVERIFIED_CLAIMS", "CITATIONS_REQUIRED",
})
FOUNDATION_NEGATIVE_CONSTRAINTS = BACKGROUND_FOUNDATION_NEGATIVE_CONSTRAINTS

FOUNDATION_TEMPLATE = BackgroundTemplate(
    template_id="pilot.artifact_evidence_inventory",
    version="1",
    status="FOUNDER_PILOT",
    admission_enabled=False,
    workflow_kind="alex_background_job:v1",
    objective_kind="ARTIFACT_EVIDENCE_INVENTORY",
    work_class=BackgroundWorkClass.DETACHED_READ,
    capability_ids=("background.artifact.inspect",),
    allowed_input_kinds=_INPUT_KINDS,
    negative_constraints=FOUNDATION_NEGATIVE_CONSTRAINTS,
    completion_contract_id="background.artifact_inventory.v1",
    milestone_policy_id="background.closed_milestones.v1",
    retryable_error_codes=frozenset({
        "lease_lost", "retryable_dependency", "transient_store_error"}),
    max_attempts=3,
    budgets={
        "max_steps": 1, "max_model_calls": 0,
        "max_provider_calls": 0, "max_tokens": 0,
        "max_active_seconds": 30, "max_wall_seconds": 120,
        "max_artifact_bytes": 5_242_880, "max_artifact_chunks": 100,
        "max_output_bytes": 65_536, "max_retries": 2,
        "max_concurrent": 1,
    },
)
FOUNDATION_TEMPLATES: Mapping[tuple[str, str], BackgroundTemplate] = {
    (FOUNDATION_TEMPLATE.template_id, FOUNDATION_TEMPLATE.version):
        FOUNDATION_TEMPLATE,
}


def enabled_foundation_templates() -> Mapping[tuple[str, str], BackgroundTemplate]:
    """Explicit test/evaluation registry; production defaults remain disabled."""
    enabled = replace(FOUNDATION_TEMPLATE, admission_enabled=True)
    return {(enabled.template_id, enabled.version): enabled}


class BackgroundEligibilityPolicy:
    """Deterministic, code-owned admission for the reviewed foundation shape."""

    def __init__(self, templates: Mapping[
            tuple[str, str], BackgroundTemplate] = FOUNDATION_TEMPLATES):
        self.templates = dict(templates)

    def evaluate(self, *, principal: ActorPrincipal,
                 request: BackgroundJobRequest,
                 admission_enabled: bool) -> dict[str, Any]:
        if not admission_enabled:
            return _error(
                "background_admission_disabled",
                "Background job admission is disabled.")
        template = self.templates.get(
            (request.template_id, request.template_version))
        if (template is None or template.status != "FOUNDER_PILOT"
                or not template.admission_enabled):
            return _error(
                "background_template_unavailable",
                "The background template is not enabled for this gate.")
        if (principal.principal_kind != "INTERACTIVE"
                or principal.role is not WorkspaceRole.FOUNDER
                or not principal.actor_id or not principal.workspace_id):
            return _error(
                "interactive_founder_required",
                "A signed-in interactive founder is required.")
        if (not _REQUEST_ID.fullmatch(request.client_request_id)
                or not _OPAQUE_ID.fullmatch(request.origin_message_id)
                or not _OPAQUE_ID.fullmatch(request.origin_session_id)):
            return _error(
                "background_request_invalid",
                "The background request identity is invalid.")
        objective = " ".join(request.objective_summary.split()).strip()
        if (request.objective_kind != template.objective_kind
                or not objective or len(objective) > 1000):
            return _error(
                "background_intent_mismatch",
                "The interpreted objective does not match this template.")
        if (len(request.input_refs) != 1
                or len({(item.kind, item.ref_id, item.version)
                        for item in request.input_refs}) != len(request.input_refs)):
            return _error(
                "background_input_invalid",
                "Background inputs are ambiguous or exceed the bound.")
        for item in request.input_refs:
            if (item.kind not in template.allowed_input_kinds
                    or not _OPAQUE_ID.fullmatch(item.ref_id)
                    or not _OPAQUE_ID.fullmatch(item.version)
                    or not _HASH.fullmatch(item.content_hash)
                    or item.ref_id.startswith(("http:", "https:"))):
                return _error(
                    "background_input_invalid",
                    "Only immutable, registered internal references are allowed.")
        confirmed = set(request.confirmed_negative_constraints)
        if not confirmed <= FOUNDER_CONSTRAINT_CODES:
            return _error(
                "negative_constraint_invalid",
                "A negative constraint is not registered.")
        for capability_id in template.capability_ids:
            try:
                descriptor = capability_registry.require_capability(capability_id)
            except ValueError:
                return _error(
                    "background_capability_unavailable",
                    "A required foundation capability is unavailable.")
            if (descriptor.side_effect_class != "NO_EFFECT"
                    or descriptor.approval_policy_id != "none.v1"
                    or descriptor.required_permissions):
                return _error(
                    "background_authority_forbidden",
                    "Gate A/B background work cannot carry effect authority.")
        negative_constraints = sorted(
            set(template.negative_constraints) | confirmed)
        decision_material = {
            "policy_version": POLICY_VERSION,
            "workspace_id": principal.workspace_id,
            "actor_id": principal.actor_id,
            "template_id": template.template_id,
            "template_version": template.version,
            "objective_kind": request.objective_kind,
            "objective_hash": canonical_hash(
                {"summary": objective}, domain="background-objective"),
            "input_refs": [
                {"kind": item.kind, "ref_id": item.ref_id,
                 "version": item.version, "content_hash": item.content_hash}
                for item in request.input_refs
            ],
            "negative_constraints": negative_constraints,
            "budgets": dict(template.budgets),
            "visibility_scope": "ACTOR_PRIVATE",
        }
        return {
            "status": "success", "eligible": True,
            "template": template,
            "objective_summary": objective,
            "negative_constraints": negative_constraints,
            "decision_material": decision_material,
            "eligibility_decision_hash": canonical_hash(
                decision_material, domain="background-eligibility-decision"),
        }


class BackgroundRetryPolicy:
    """Closed retry classification; it never executes or schedules a retry."""

    @staticmethod
    def classify(template: BackgroundTemplate, *, error_code: str,
                 attempts: int) -> dict[str, Any]:
        retryable = (error_code in template.retryable_error_codes
                     and 0 <= attempts < template.max_attempts)
        return {
            "status": "success", "retryable": retryable,
            "error_code": error_code,
            "attempts": max(0, attempts),
            "max_attempts": template.max_attempts,
            "retry_policy_id": "background-bounded-transient-v1",
        }


_PROJECTION_CONTRACTS: Mapping[str, tuple[str, str, bool]] = {
    "RUN_CREATED": ("QUEUED", "BACKGROUND_QUEUED", False),
    "STEP_STARTED": ("RUNNING", "BACKGROUND_WORKING", False),
    "MILESTONE_REACHED": ("RUNNING", "BACKGROUND_MILESTONE", False),
    "APPROVAL_REQUIRED": ("WAITING", "BACKGROUND_APPROVAL_REQUIRED", True),
    "STEP_FAILED": ("RUNNING", "BACKGROUND_RECOVERABLE_FAILURE", False),
    "RUN_FAILED": ("FAILED", "BACKGROUND_TERMINAL_FAILURE", False),
    "RUN_CANCELLING": ("CANCELLING", "BACKGROUND_CANCELLING", False),
    "RUN_CANCELLED": ("CANCELLED", "BACKGROUND_CANCELLED", False),
    "RUN_SUCCEEDED": ("SUCCEEDED", "BACKGROUND_COMPLETE", False),
}
_PROJECTION_ERROR_CODES = frozenset({
    "budget_exhausted", "cancelled", "lease_lost", "policy_refused",
    "retryable_dependency", "validation_failed",
})


def normalize_background_projection(*, event_kind: str, run_id: str,
                                    event_id: str, event_sequence: int,
                                    error_code: str = "",
                                    approval_id: str = "") -> dict[str, Any]:
    """Return content-free closed display metadata; projections authorize nothing."""
    contract = _PROJECTION_CONTRACTS.get(event_kind)
    if (contract is None or not _OPAQUE_ID.fullmatch(run_id)
            or not _OPAQUE_ID.fullmatch(event_id) or event_sequence < 1
            or (error_code and error_code not in _PROJECTION_ERROR_CODES)
            or (event_kind == "APPROVAL_REQUIRED") != bool(approval_id)
            or (approval_id and not _OPAQUE_ID.fullmatch(approval_id))):
        return _error(
            "background_projection_invalid",
            "Background projection metadata is invalid.")
    runtime_status, caption_code, approval_required = contract
    return {
        "status": "success",
        "projection": {
            "schema_version": 1,
            "projection_kind": "BACKGROUND_JOB_STATUS",
            "run_id": run_id,
            "event_id": event_id,
            "event_sequence": event_sequence,
            "runtime_status": runtime_status,
            "caption_code": caption_code,
            "error_code": error_code or None,
            "approval_required": approval_required,
            "approval_id": approval_id or None,
            "authoritative": False,
        },
    }


class BackgroundWorkService:
    """Atomic actor-private job admission with every later-gate authority removed."""

    def __init__(self, store: DurableStore | None = None, *,
                 templates: Mapping[
                     tuple[str, str], BackgroundTemplate] = FOUNDATION_TEMPLATES,
                 admission_enabled: bool | None = None):
        self.store = store or production_store()
        self.templates = dict(templates)
        self.policy = BackgroundEligibilityPolicy(self.templates)
        self.admission_enabled = (
            os.environ.get("BACKGROUND_JOB_ADMISSION_ENABLED", "false").lower()
            in {"1", "true", "yes", "on"}
            if admission_enabled is None else bool(admission_enabled))

    async def accept(self, *, principal: ActorPrincipal,
                     request: BackgroundJobRequest) -> dict[str, Any]:
        decision = self.policy.evaluate(
            principal=principal, request=request,
            admission_enabled=self.admission_enabled)
        if decision.get("error"):
            return decision
        template: BackgroundTemplate = decision["template"]
        input_manifest_id = stable_id(
            "artifact", principal.workspace_id, principal.actor_id,
            request.client_request_id, "background-input")
        journey_id = stable_id(
            "journey", principal.workspace_id, principal.actor_id,
            request.client_request_id, "background")
        job_id = stable_id(
            "run", principal.workspace_id, journey_id,
            RunKind.BACKGROUND.value, request.client_request_id)
        now = utc_now()
        input_manifest = {
            "schema_version": 1,
            "artifact_id": input_manifest_id,
            "artifact_kind": "BACKGROUND_INPUT_MANIFEST",
            "workspace_id": principal.workspace_id,
            "founder_id": principal.workspace_id,
            "actor_id": principal.actor_id,
            "subject_kind": "ACTOR",
            "subject_id": principal.actor_id,
            "visibility_scope": "ACTOR_PRIVATE",
            "source_refs": list(decision["decision_material"]["input_refs"]),
            "objective_hash": decision["decision_material"]["objective_hash"],
            "negative_constraints": list(decision["negative_constraints"]),
            "content_hash": canonical_hash(
                decision["decision_material"],
                domain="background-input-manifest"),
            "status": "IMMUTABLE",
            "created_at": now,
            "version": 1,
        }
        profile = {
            "execution_mode": "BACKGROUND",
            "job_id": job_id,
            "job_template_id": template.template_id,
            "job_template_version": template.version,
            "eligibility_policy_id": "BackgroundEligibilityPolicy",
            "eligibility_policy_version": POLICY_VERSION,
            "eligibility_decision_hash": decision["eligibility_decision_hash"],
            "origin_actor_id": principal.actor_id,
            "origin_message_id": request.origin_message_id,
            "delivery_session_id": request.origin_session_id,
            "subject_kind": "ACTOR",
            "subject_id": principal.actor_id,
            "visibility_scope": "ACTOR_PRIVATE",
            "visibility_policy_id": "actor-private-default",
            "visibility_policy_version": VISIBILITY_POLICY_VERSION,
            "objective_summary": decision["objective_summary"],
            "negative_constraints": list(decision["negative_constraints"]),
            "input_manifest_ref": f"artifacts/{input_manifest_id}",
            "input_manifest_hash": input_manifest["content_hash"],
            "completion_contract_id": template.completion_contract_id,
            "milestone_policy_id": template.milestone_policy_id,
            "skill_bindings": [],
            "output_manifest_ref": None,
            "background_gate_ceiling": "GATE_C_FOUNDER_PILOT",
            "approval_authority": "NONE",
            "effect_authority": "NONE",
            "memory_write_authority": "NONE",
            "external_read_authority": "NONE",
            "specialist_execution_enabled": True,
        }
        profile["background_profile_hash"] = canonical_hash(
            profile, domain="background-run-profile")
        prepared = await WorkflowRuntime(self.store).prepare_run_creation(
            workspace_id=principal.workspace_id,
            journey_id=journey_id,
            run_kind=RunKind.BACKGROUND,
            workflow_kind=template.workflow_kind,
            idempotency_key=request.client_request_id,
            domain_ref=input_manifest_id,
            originating_actor_id=principal.actor_id,
            origin_session_id=request.origin_session_id,
            priority="LOW",
            budgets=dict(template.budgets),
            background_profile=profile)
        if prepared.get("error"):
            return prepared
        capacity_id = stable_id(
            "bgcapacity", principal.workspace_id, principal.actor_id,
            template.template_id)
        capacity = await self.store.get(
            "background_pilot_capacity", capacity_id)
        now_dt = datetime.now(timezone.utc)
        window_start = _parse_time((capacity or {}).get("window_started_at"))
        admissions = int((capacity or {}).get("window_admissions") or 0)
        if not window_start or window_start <= now_dt - timedelta(hours=1):
            window_start = now_dt
            admissions = 0
        active_run_id = str((capacity or {}).get("active_run_id") or "")
        if active_run_id and active_run_id != prepared["run_id"]:
            active_run = await self.store.get("workflow_runs", active_run_id)
            if active_run and active_run.get("runtime_status") not in {
                    "SUCCEEDED", "FAILED", "REJECTED", "CANCELLED"}:
                return _error(
                    "background_concurrency_exhausted",
                    "The founder pilot already has one active job.")
        if admissions >= 3:
            return _error(
                "background_rate_limited",
                "The founder pilot admits at most three jobs per hour.")
        capacity_values = {
            "schema_version": 1, "capacity_id": capacity_id,
            "workspace_id": principal.workspace_id,
            "actor_id": principal.actor_id,
            "template_id": template.template_id,
            "status": "ACTIVE", "active_run_id": prepared["run_id"],
            "window_started_at": window_start.isoformat(),
            "window_admissions": admissions + 1,
            "updated_at": now,
        }
        capacity_mutation = (
            AtomicMutation(
                "background_pilot_capacity", capacity_id,
                int(capacity["version"]), updates=capacity_values)
            if capacity else AtomicMutation(
                "background_pilot_capacity", capacity_id, None,
                record={**capacity_values, "created_at": now}))
        accepted = await CommandService(self.store).accept(
            principal=principal,
            client_request_id=request.client_request_id,
            command_type="background_job.create",
            request={
                "template_id": template.template_id,
                "template_version": template.version,
                "objective_hash": decision["decision_material"]["objective_hash"],
                "input_manifest_hash": input_manifest["content_hash"],
                "negative_constraints": list(decision["negative_constraints"]),
            },
            origin_session_id=request.origin_session_id,
            run_id=prepared["run_id"],
            dispatch_ref=input_manifest_id,
            authority_mutations=(*prepared["mutations"], AtomicMutation(
                "artifacts", input_manifest_id, None, record=input_manifest),
                capacity_mutation),
            visibility_scope="ACTOR_PRIVATE",
            subject_id=principal.actor_id)
        if accepted.get("error"):
            if accepted.get("error_code") == "concurrency_conflict":
                latest_capacity = await self.store.get(
                    "background_pilot_capacity", capacity_id)
                if (latest_capacity
                        and latest_capacity.get("active_run_id")
                        != prepared["run_id"]):
                    return _error(
                        "background_concurrency_exhausted",
                        "The founder pilot already has one active job.")
            return accepted
        run = await self.store.get("workflow_runs", str(prepared["run_id"]))
        if not run or run.get("job_id") != run.get("run_id"):
            return _error(
                "background_authority_incomplete",
                "The accepted command has no matching durable job authority.")
        return {
            "status": "accepted", "duplicate": bool(accepted.get("duplicate")),
            "command_id": accepted["command_id"],
            "job_id": run["run_id"], "run_id": run["run_id"],
            "runtime_status": run["runtime_status"],
            "visibility_scope": run["visibility_scope"],
            "eligibility_decision_hash": run["eligibility_decision_hash"],
        }

    async def get_job(self, *, principal: ActorPrincipal,
                      run_id: str) -> dict[str, Any]:
        row = await self.store.get("workflow_runs", run_id)
        if (not row or row.get("execution_mode") != "BACKGROUND"
                or not run_visible_to_actor(
                    row, workspace_id=principal.workspace_id,
                    actor_id=principal.actor_id)):
            return _error("background_job_not_found", "Background job does not exist.")
        return {"status": "success", "job": row}

    async def list_jobs(self, *, principal: ActorPrincipal,
                        limit: int = 100) -> dict[str, Any]:
        rows = await self.store.list(
            "workflow_runs", filters={
                "workspace_id": principal.workspace_id,
                "originating_actor_id": principal.actor_id,
                "execution_mode": "BACKGROUND",
                "visibility_scope": "ACTOR_PRIVATE",
            }, order_by="updated_at", descending=True,
            limit=max(1, min(limit, 100)))
        return {"status": "success", "jobs": rows}

    async def cancel(self, *, principal: ActorPrincipal, run_id: str,
                     reason: str) -> dict[str, Any]:
        visible = await self.get_job(principal=principal, run_id=run_id)
        if visible.get("error"):
            return visible
        return await WorkflowRuntime(self.store).cancel_run(
            run_id, actor_id=principal.actor_id,
            reason=(" ".join(reason.split()).strip() or "Founder cancelled")[:500])


class BackgroundCommandDispatcher:
    """Materialize only the pilot's registered deterministic read step."""

    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    async def dispatch(self, outbox_id: str) -> dict[str, Any]:
        outbox = await self.store.get("command_outbox", outbox_id)
        if not outbox:
            return _error(
                "background_outbox_not_found", "Dispatch intent does not exist.")
        if outbox.get("status") == "DELIVERED":
            return {"status": "success", "duplicate": True, "outbox": outbox}
        if (outbox.get("status") != "PENDING"
                or outbox.get("command_type") != "background_job.create"
                or outbox.get("visibility_scope") != "ACTOR_PRIVATE"):
            return _error(
                "background_dispatch_forbidden",
                "Dispatch intent is outside the Gate A/B lane.")
        command_id = str(outbox.get("command_id") or "")
        receipt = await self.store.get("command_receipts", command_id)
        run = await self.store.get("workflow_runs", str(outbox.get("run_id") or ""))
        if (not receipt or receipt.get("status") != "ACCEPTED"
                or receipt.get("command_type") != "background_job.create"
                or not run or run.get("execution_mode") != "BACKGROUND"
                or run.get("background_gate_ceiling") != "GATE_C_FOUNDER_PILOT"
                or run.get("job_template_id") != FOUNDATION_TEMPLATE.template_id
                or run.get("specialist_execution_enabled") is not True
                or run.get("approval_authority") != "NONE"
                or run.get("effect_authority") != "NONE"
                or run.get("memory_write_authority") != "NONE"
                or run.get("external_read_authority") != "NONE"
                or receipt.get("subject_id") != run.get("subject_id")):
            return _error(
                "background_dispatch_authority_invalid",
                "Accepted background authority is incomplete or widened.")
        step = await WorkflowRuntime(self.store).create_step(
            run["run_id"], step_key="analyze_artifact",
            idempotency_key=f"background-artifact:{run['run_id']}")
        if step.get("error"):
            return step
        transitioned = await CommandService(self.store).transition(
            workspace_id=run["workspace_id"], command_id=command_id,
            expected_version=int(receipt["version"]), status="DISPATCHED",
            run_id=run["run_id"])
        if transitioned.get("error"):
            latest = await self.store.get("command_receipts", command_id)
            if latest and latest.get("status") == "DISPATCHED":
                return {"status": "success", "duplicate": True,
                        "command": latest, "step": step}
            return transitioned
        return {"status": "success", "duplicate": False,
                "command": transitioned, "step": step}
