"""Generic durable, event-driven workflow runtime (docs/21 phases 1A-1C).

Runtime state is deliberately independent from hiring domain state. No method
polls or sleeps: an event, timer delivery, or explicit human command performs a
bounded transition and persists the next wait.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from services.canonical import canonical_hash
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.workflow_contracts import (
    BACKGROUND_FOUNDATION_NEGATIVE_CONSTRAINTS,
    PLAN_TEMPLATES,
    WAIT_CONTRACTS,
    DefaultWorkflowPolicy,
    PlatformDomainAdapter,
    RunKind,
    RuntimeStatus,
    normalize_runtime_status,
    resolve_workflow_definition,
    stable_id,
    utc_now,
)


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    del http_status
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


class WorkflowRuntime:
    def __init__(self, store: DurableStore | None = None, *,
                 definitions=None, domain_adapter=None, policy=None):
        self.store = store or production_store()
        self.definitions = definitions
        self.domain_adapter = domain_adapter or PlatformDomainAdapter()
        self.policy = policy or DefaultWorkflowPolicy()

    async def _publish_run(self, run: dict[str, Any], *, event_kind: str,
                           event_sequence: int) -> None:
        # The current SSE stream is workspace-wide. Gate A/B background jobs
        # are actor-private, so publishing them here would leak existence to a
        # second actor. An actor-scoped projection lane is a later gate.
        if (run.get("visibility_scope") == "ACTOR_PRIVATE"
                or run.get("execution_mode") == "BACKGROUND"):
            return
        from services.projection_stream import publish_best_effort

        await publish_best_effort(
            store=self.store, workspace_id=str(run["workspace_id"]),
            projection_type="run", aggregate_id=str(run["run_id"]),
            aggregate_version=int(run["version"]), run_id=str(run["run_id"]),
            safe_payload={
                "runtime_status": normalize_runtime_status(
                    str(run.get("runtime_status") or "")),
                "event_kind": event_kind,
                "event_sequence": int(event_sequence),
            },
            idempotency_key=(f"run:{run['run_id']}:{event_kind}:"
                             f"{event_sequence}:{run['version']}"))

    async def publish_created_run(self, run: dict[str, Any]) -> None:
        """Publish the rebuildable initial projection after a T8 commit."""
        await self._publish_run(
            run, event_kind="RUN_CREATED", event_sequence=1)

    async def prepare_run_creation(
            self, *, workspace_id: str, journey_id: str, run_kind: RunKind,
            idempotency_key: str, domain_ref: str,
            provenance: dict[str, Any] | None = None,
            parent_run_id: str | None = None,
            originating_actor_id: str = "", workflow_kind: str = "",
            origin_session_id: str = "",
            priority: str = "NORMAL",
            budgets: dict[str, int] | None = None,
            background_profile: dict[str, Any] | None = None) -> dict[str, Any]:
        if priority not in {"LOW", "NORMAL", "HIGH"}:
            return _error("run_contract_invalid", "Run priority is invalid.", 400)
        budget_limits = {
            "max_steps": 100, "max_model_calls": 30,
            "max_provider_calls": 20, "max_tokens": 1_000_000,
            "max_active_seconds": 900, "max_wall_seconds": 86_400,
            "max_artifact_bytes": 100_000_000,
            "max_artifact_chunks": 10_000,
            "max_output_bytes": 10_000_000, "max_retries": 10,
            "max_concurrent": 100,
        }
        for key, value in dict(budgets or {}).items():
            if key not in budget_limits or not isinstance(value, int) or value < 0:
                return _error("run_contract_invalid", "Run budget is invalid.", 400)
            budget_limits[key] = value
        try:
            definition = resolve_workflow_definition(
                run_kind, workflow_kind, registry=self.definitions)
        except ValueError:
            return _error("workflow_kind_invalid",
                          "Workflow kind is not registered.", 400)
        run_kind = definition.run_kind
        profile = dict(background_profile or {})
        if run_kind is RunKind.BACKGROUND:
            profile_hash = str(profile.pop("background_profile_hash", ""))
            constraints = profile.get("negative_constraints")
            profile_fields = {
                "execution_mode", "job_id", "job_template_id",
                "job_template_version", "eligibility_policy_id",
                "eligibility_policy_version", "eligibility_decision_hash",
                "origin_actor_id", "origin_message_id", "delivery_session_id",
                "subject_kind", "subject_id", "visibility_scope",
                "visibility_policy_id", "visibility_policy_version",
                "objective_summary", "negative_constraints",
                "input_manifest_ref", "input_manifest_hash",
                "completion_contract_id", "milestone_policy_id",
                "skill_bindings", "output_manifest_ref",
                "background_gate_ceiling", "approval_authority",
                "effect_authority", "memory_write_authority",
                "external_read_authority", "specialist_execution_enabled",
            }
            required = {
                "execution_mode": "BACKGROUND",
                "background_gate_ceiling": "GATE_C_FOUNDER_PILOT",
                "job_template_id": "pilot.artifact_evidence_inventory",
                "job_template_version": "1",
                "eligibility_policy_id": "BackgroundEligibilityPolicy",
                "eligibility_policy_version":
                    "background-artifact-pilot-eligibility-v1",
                "origin_actor_id": originating_actor_id,
                "delivery_session_id": origin_session_id,
                "subject_kind": "ACTOR",
                "subject_id": originating_actor_id,
                "visibility_scope": "ACTOR_PRIVATE",
                "visibility_policy_id": "actor-private-default",
                "visibility_policy_version": "actor-private-background-pilot-v1",
                "completion_contract_id": "background.artifact_inventory.v1",
                "milestone_policy_id": "background.closed_milestones.v1",
                "skill_bindings": [],
                "output_manifest_ref": None,
                "approval_authority": "NONE",
                "effect_authority": "NONE",
                "memory_write_authority": "NONE",
                "external_read_authority": "NONE",
                "specialist_execution_enabled": True,
            }
            if (not originating_actor_id
                    or set(profile) != profile_fields
                    or any(profile.get(key) != value
                           for key, value in required.items())
                    or not isinstance(constraints, list) or not constraints
                    or len(constraints) > 32
                    or any(not isinstance(item, str) or not item or len(item) > 80
                           for item in constraints)
                    or len(set(constraints)) != len(constraints)
                    or not set(BACKGROUND_FOUNDATION_NEGATIVE_CONSTRAINTS)
                    <= set(constraints)
                    or budget_limits != {
                        "max_steps": 1, "max_model_calls": 0,
                        "max_provider_calls": 0, "max_tokens": 0,
                        "max_active_seconds": 30, "max_wall_seconds": 120,
                        "max_artifact_bytes": 5_242_880,
                        "max_artifact_chunks": 100,
                        "max_output_bytes": 65_536, "max_retries": 2,
                        "max_concurrent": 1}
                    or not str(profile.get("input_manifest_ref") or "").endswith(
                        f"/{domain_ref}")
                    or not str(profile.get("input_manifest_hash") or "").startswith(
                        "sha256:")
                    or not str(profile.get("eligibility_decision_hash") or "").startswith(
                        "sha256:")
                    or not str(profile.get("origin_message_id") or "")
                    or not str(profile.get("objective_summary") or "")
                    or canonical_hash(
                        profile, domain="background-run-profile") != profile_hash):
                return _error(
                    "background_profile_invalid",
                    "Background runs require an immutable Gate A/B profile.", 400)
            profile["background_profile_hash"] = profile_hash
        elif profile:
            return _error(
                "background_profile_invalid",
                "Only background runs accept a background profile.", 400)
        provenance = dict(provenance or {"provenance_class": "PRODUCTION"})
        domain_gate = self.domain_adapter.validate_run(
            definition=definition, workspace_id=workspace_id,
            domain_ref=domain_ref, provenance=provenance)
        if domain_gate.get("error"):
            return domain_gate
        policy_gate = self.policy.validate_run(
            definition=definition, workspace_id=workspace_id,
            originating_actor_id=originating_actor_id)
        if policy_gate.get("error"):
            return policy_gate
        run_id = stable_id("run", workspace_id, journey_id, run_kind.value,
                           idempotency_key)
        if run_kind is RunKind.BACKGROUND and profile.get("job_id") != run_id:
            return _error(
                "background_profile_invalid",
                "Background job identity does not match its workflow run.", 400)
        if parent_run_id:
            parent = await self.store.get("workflow_runs", parent_run_id)
            if not parent or parent.get("workspace_id") != workspace_id:
                return _error("parent_run_missing", "Parent run does not exist.", 404)
            if parent.get("journey_id") != journey_id:
                return _error("journey_mismatch", "Parent run is in another journey.")
            if parent.get("run_kind") not in {
                    kind.value for kind in definition.allowed_parent_kinds}:
                return _error("invalid_run_hierarchy", "Invalid parent/child run kinds.")
        elif definition.allowed_parent_kinds:
            return _error("parent_run_required", "Child runs require a parent run.")
        now = utc_now()
        plan_version = "initial-v1"
        plan_id = stable_id("plan", run_id, plan_version)
        plan_material = {
            "schema_version": definition.plan_schema_version,
            "workflow_kind": definition.workflow_kind,
            "workflow_definition_version": definition.version,
            "steps": list(PLAN_TEMPLATES[definition.workflow_kind]),
        }
        from services.workflow_plan_validator import validate_plan

        plan_gate = validate_plan(plan_material, budgets=budget_limits)
        if plan_gate.get("error"):
            return plan_gate
        from services import capability_registry
        from services.capability_governance import state_id

        for capability_id in set(plan_gate["capabilities"].values()):
            descriptor = capability_registry.require_capability(capability_id)
            state = await self.store.get(
                "capability_states", state_id(
                    capability_id, descriptor.semantic_version))
            if state and state.get("lifecycle") == "DISABLED":
                return _error("plan_capability_disabled",
                              "A required capability is disabled.")
        plan_hash = canonical_hash(plan_material, domain="workflow-plan")
        plan_row = {
            **plan_material, "plan_id": plan_id, "run_id": run_id,
            "workspace_id": workspace_id, "plan_version": plan_version,
            "plan_hash": plan_hash, "status": "ACTIVE",
            "created_at": now, "version": 1,
        }
        row = {
            "schema_version": 2, "run_id": run_id, "workspace_id": workspace_id,
            "journey_id": journey_id, "run_kind": run_kind.value,
            "workflow_kind": definition.workflow_kind,
            "workflow_definition_version": definition.version,
            "parent_run_id": parent_run_id, "domain_ref": domain_ref,
            "runtime_status": RuntimeStatus.QUEUED.value,
            "runtime_status_schema_version": 2,
            "next_event_sequence": 2,
            "cancel_reason": None, "originating_actor_id": originating_actor_id,
            "origin_session_id": origin_session_id or None,
            "cancellation_generation": 0,
            "plan_schema_version": definition.plan_schema_version,
            "plan_version": plan_version, "plan_hash": plan_hash,
            "policy_version": definition.policy_version,
            "capability_registry_version": definition.capability_registry_version,
            "priority": priority, "budgets": budget_limits,
            "budget_usage": {"steps_started": 0, "model_calls": 0,
                             "provider_calls": 0, "tokens": 0,
                             "retries": 0},
            "provenance": provenance,
            "created_at": now, "updated_at": now, "version": 1,
        }
        row.update(profile)
        event_id = stable_id("evt", run_id, f"create:{run_id}")
        event = {
            "schema_version": 2, "event_id": event_id, "run_id": run_id,
            "workspace_id": workspace_id, "journey_id": journey_id,
            "sequence": 1, "event_kind": "RUN_CREATED",
            "event_schema_version": 1, "reducer_version": 1,
            "idempotency_key": f"create:{run_id}",
            "safe_payload": {"run_kind": run_kind.value,
                             "workflow_kind": definition.workflow_kind,
                             "domain_ref": domain_ref,
                             "plan_version": plan_version,
                             "plan_hash": plan_hash},
            "actor_id": originating_actor_id or None,
            "workload_principal": None, "causation_event_id": None,
            "occurred_at": now, "provenance": provenance, "version": 1,
        }
        return {
            "status": "success", "run_id": run_id,
            "run_record": row, "plan_record": plan_row,
            "event_record": event,
            "mutations": (
                AtomicMutation("workflow_runs", run_id, None, record=row),
                AtomicMutation("workflow_plans", plan_id, None, record=plan_row),
                AtomicMutation("run_events", event_id, None, record=event),
            ),
        }

    async def create_run(
            self, *, workspace_id: str, journey_id: str, run_kind: RunKind,
            idempotency_key: str, domain_ref: str,
            provenance: dict[str, Any] | None = None,
            parent_run_id: str | None = None,
            originating_actor_id: str = "", workflow_kind: str = "",
            origin_session_id: str = "",
            priority: str = "NORMAL",
            budgets: dict[str, int] | None = None,
            background_profile: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create the immutable plan, initial event, and run atomically.

        Public command handlers that must include command acceptance in this
        boundary call :meth:`prepare_run_creation` and add its reviewed
        mutations to ``CommandService.accept``. Normal internal callers use
        this convenience method.
        """
        prepared = await self.prepare_run_creation(
            workspace_id=workspace_id, journey_id=journey_id,
            run_kind=run_kind, idempotency_key=idempotency_key,
            domain_ref=domain_ref, provenance=provenance,
            parent_run_id=parent_run_id,
            originating_actor_id=originating_actor_id,
            workflow_kind=workflow_kind,
            origin_session_id=origin_session_id,
            priority=priority, budgets=budgets,
            background_profile=background_profile)
        if prepared.get("error"):
            return prepared
        run_id = str(prepared["run_id"])
        row = dict(prepared["run_record"])
        committed = await self.store.atomic_compare_and_set(
            prepared["mutations"])
        if not committed:
            existing = await self.store.get("workflow_runs", run_id)
            if existing and all(existing.get(field) == row[field] for field in (
                    "workspace_id", "journey_id", "workflow_kind", "run_kind",
                    "parent_run_id", "domain_ref", "provenance")):
                return {"status": "success", "duplicate": True, **existing}
            return _error("idempotency_conflict", "Run key already names different work.")
        committed_run = committed[("workflow_runs", run_id)]
        await self.publish_created_run(committed_run)
        return {"status": "success", "duplicate": False, **committed_run}

    async def append_event(self, run_id: str, *, event_kind: str,
                           idempotency_key: str, safe_payload: dict[str, Any],
                           actor_id: str = "",
                           workload: dict[str, Any] | None = None,
                           causation_event_id: str = "") -> dict[str, Any]:
        """Append once while advancing the run sequence in one transaction."""
        event_id = stable_id("evt", run_id, idempotency_key)
        existing = await self.store.get("run_events", event_id)
        if existing:
            return {"status": "success", "duplicate": True, **existing}
        for _ in range(8):
            run = await self.store.get("workflow_runs", run_id)
            if not run:
                return _error("run_not_found", "Workflow run does not exist.", 404)
            if normalize_runtime_status(str(run.get("runtime_status") or "")) in {
                    RuntimeStatus.CANCELLING.value,
                    RuntimeStatus.CANCELLED.value,
                    RuntimeStatus.SUCCEEDED.value,
                    RuntimeStatus.FAILED.value,
                    RuntimeStatus.REJECTED.value}:
                return _error("run_fenced",
                              "Terminal or cancelling runs reject new events.")
            sequence = int(run.get("next_event_sequence", 1))
            event = {
                "schema_version": 2, "event_id": event_id, "run_id": run_id,
                "workspace_id": run["workspace_id"], "journey_id": run["journey_id"],
                "sequence": sequence, "event_kind": event_kind,
                "event_schema_version": 1, "reducer_version": 1,
                "idempotency_key": idempotency_key,
                "safe_payload": {str(key)[:80]: value for key, value in safe_payload.items()},
                "actor_id": actor_id or None,
                "workload_principal": dict(workload or {}) or None,
                "causation_event_id": causation_event_id or None,
                "occurred_at": utc_now(), "provenance": run.get("provenance") or {},
                "version": 1,
            }
            committed = await self.store.atomic_compare_and_set((
                AtomicMutation(
                    "workflow_runs", run_id, int(run["version"]),
                    updates={"next_event_sequence": sequence + 1,
                             "updated_at": utc_now()}),
                AtomicMutation("run_events", event_id, None, record=event),
            ))
            if not committed:
                existing = await self.store.get("run_events", event_id)
                if existing:
                    return {"status": "success", "duplicate": True, **existing}
                continue
            committed_event = committed[("run_events", event_id)]
            await self._publish_run(
                committed[("workflow_runs", run_id)], event_kind=event_kind,
                event_sequence=sequence)
            return {"status": "success", "duplicate": False,
                    **committed_event}
        return _error("concurrency_conflict", "Could not append run event.")

    async def recover_run(self, run_id: str) -> dict[str, Any]:
        run = await self.store.get("workflow_runs", run_id)
        if not run:
            return _error("run_not_found", "Workflow run does not exist.", 404)
        return {"status": "success", "run_id": run_id,
                "runtime_status": normalize_runtime_status(
                    str(run.get("runtime_status") or ""))}

    async def rebuild_projection(self, run_id: str) -> dict[str, Any]:
        """Deterministically reduce registered immutable events for one run."""
        run = await self.store.get("workflow_runs", run_id)
        if not run:
            return _error("run_not_found", "Workflow run does not exist.", 404)
        events = await self.store.list(
            "run_events", filters={"run_id": run_id}, order_by="sequence",
            limit=1000)
        status = RuntimeStatus.RECEIVED.value
        plan_version = ""
        plan_hash = ""
        open_waits: set[str] = set()
        expected = 1
        for event in events:
            if (int(event.get("sequence") or 0) != expected
                    or int(event.get("event_schema_version") or 1) != 1
                    or int(event.get("reducer_version") or 1) != 1):
                return _error("event_log_invalid",
                              "Run event sequence or reducer version is invalid.")
            expected += 1
            kind = str(event.get("event_kind") or "")
            payload = event.get("safe_payload") or {}
            if kind == "RUN_CREATED":
                status = RuntimeStatus.QUEUED.value
                plan_version = str(payload.get("plan_version") or "")
                plan_hash = str(payload.get("plan_hash") or "")
            elif kind == "WAIT_OPENED":
                open_waits.add(str(payload.get("wait_id") or ""))
                status = RuntimeStatus.WAITING.value
            elif kind == "WAIT_RESOLVED":
                open_waits.discard(str(payload.get("wait_id") or ""))
                status = RuntimeStatus.QUEUED.value
            elif kind == "RUN_PAUSED":
                status = RuntimeStatus.PAUSED.value
            elif kind == "RUN_RESUMED":
                status = (RuntimeStatus.WAITING.value if open_waits
                          else RuntimeStatus.QUEUED.value)
            elif kind == "RUN_CANCELLING":
                status = RuntimeStatus.CANCELLING.value
            elif kind == "RUN_CANCELLED":
                status = RuntimeStatus.CANCELLED.value
                open_waits.clear()
            elif kind == "RUN_SUCCEEDED":
                status = RuntimeStatus.SUCCEEDED.value
                open_waits.clear()
            elif kind not in {"STEP_COMPLETED", "ACTION_SETTLED"}:
                return _error("event_kind_unregistered",
                              "Run event kind has no registered reducer.")
        return {"status": "success", "run_id": run_id,
                "runtime_status": status,
                "plan_version": plan_version,
                "plan_hash": plan_hash,
                "open_wait_ids": sorted(item for item in open_waits if item),
                "next_event_sequence": expected,
                "event_count": len(events)}

    async def verify_projection(self, run_id: str) -> dict[str, Any]:
        """Compare the durable control projection with a from-zero replay."""
        run = await self.store.get("workflow_runs", run_id)
        rebuilt = await self.rebuild_projection(run_id)
        if not run or rebuilt.get("error"):
            return rebuilt
        waits = await self.store.list(
            "waits", filters={"run_id": run_id, "status": "OPEN"}, limit=1000)
        actual = {
            "runtime_status": normalize_runtime_status(
                str(run.get("runtime_status") or "")),
            "open_wait_ids": sorted(str(row.get("wait_id") or "") for row in waits),
            "next_event_sequence": int(run.get("next_event_sequence") or 1),
            "plan_version": str(run.get("plan_version") or ""),
            "plan_hash": str(run.get("plan_hash") or ""),
        }
        expected = {key: rebuilt[key] for key in actual}
        return {"status": "success" if actual == expected else "error",
                "error": actual != expected,
                "error_code": None if actual == expected else "projection_mismatch",
                "run_id": run_id, "actual": actual, "rebuilt": expected}

    async def succeed_run(self, run_id: str, *, result_ref: str,
                          actor_id: str = "") -> dict[str, Any]:
        """Commit terminal success and its replayable event in one boundary."""
        run = await self.store.get("workflow_runs", run_id)
        if not run:
            return _error("run_not_found", "Workflow run does not exist.", 404)
        status = normalize_runtime_status(str(run.get("runtime_status") or ""))
        if status == RuntimeStatus.SUCCEEDED.value:
            return {**run, "status": "success", "duplicate": True}
        if status in {RuntimeStatus.CANCELLING.value,
                      RuntimeStatus.CANCELLED.value,
                      RuntimeStatus.FAILED.value,
                      RuntimeStatus.REJECTED.value}:
            return _error("run_fenced", "Workflow run cannot succeed.")
        open_waits = await self.store.list(
            "waits", filters={"run_id": run_id, "status": "OPEN"}, limit=2)
        if open_waits:
            return _error("open_waits", "Workflow run still has an open wait.")
        event_key = f"run-succeeded:{result_ref}"
        event_id = stable_id("evt", run_id, event_key)
        sequence = int(run.get("next_event_sequence") or 1)
        now = utc_now()
        event = {
            "schema_version": 2, "event_id": event_id, "run_id": run_id,
            "workspace_id": run["workspace_id"], "journey_id": run["journey_id"],
            "sequence": sequence, "event_kind": "RUN_SUCCEEDED",
            "event_schema_version": 1, "reducer_version": 1,
            "idempotency_key": event_key,
            "safe_payload": {"result_ref": result_ref},
            "actor_id": actor_id or None, "workload_principal": None,
            "causation_event_id": None, "occurred_at": now,
            "provenance": run.get("provenance") or {}, "version": 1,
        }
        committed = await self.store.atomic_compare_and_set((
            AtomicMutation(
                "workflow_runs", run_id, int(run["version"]),
                updates={"runtime_status": RuntimeStatus.SUCCEEDED.value,
                         "result_ref": result_ref,
                         "next_event_sequence": sequence + 1,
                         "updated_at": now, "completed_at": now}),
            AtomicMutation("run_events", event_id, None, record=event),
        ))
        if not committed:
            return _error("concurrency_conflict", "Run changed concurrently.")
        current = committed[("workflow_runs", run_id)]
        await self._publish_run(
            current, event_kind="RUN_SUCCEEDED", event_sequence=sequence)
        return {**current, "status": "success", "duplicate": False}

    async def create_step(self, run_id: str, *, step_key: str,
                          idempotency_key: str) -> dict[str, Any]:
        run = await self.store.get("workflow_runs", run_id)
        if not run:
            return _error("run_not_found", "Workflow run does not exist.", 404)
        if normalize_runtime_status(str(run.get("runtime_status") or "")) in {
                RuntimeStatus.CANCELLING.value,
                RuntimeStatus.CANCELLED.value,
                RuntimeStatus.SUCCEEDED.value,
                RuntimeStatus.FAILED.value,
                RuntimeStatus.REJECTED.value}:
            return _error("run_fenced",
                          "Terminal or cancelling runs reject follow-on steps.")
        step_id = stable_id("step", run_id, step_key)
        from services.workflow_plan_validator import capability_for_step

        capability_id = capability_for_step(
            str(run.get("workflow_kind") or ""), step_key)
        if not capability_id:
            return _error("step_capability_missing",
                          "Workflow step is not registered.")
        row = {
            "schema_version": 1, "step_id": step_id, "run_id": run_id,
            "workspace_id": run["workspace_id"], "step_key": step_key,
            "originating_actor_id": run.get("originating_actor_id"),
            "subject_kind": run.get("subject_kind"),
            "subject_id": run.get("subject_id"),
            "visibility_scope": run.get("visibility_scope", "WORKSPACE"),
            "capability_id": capability_id,
            "status": "READY", "attempt_generation": 0,
            "lease_owner": None, "lease_expires_at": None,
            "idempotency_key": idempotency_key,
            "provenance": run.get("provenance") or {},
            "observed_cancellation_generation": int(
                run.get("cancellation_generation") or 0),
            "created_at": utc_now(),
            "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create("workflow_steps", step_id, row)
        existing = row if created else await self.store.get("workflow_steps", step_id)
        if not existing or existing.get("idempotency_key") != idempotency_key:
            return _error("idempotency_conflict", "Step key names different work.")
        return {**existing, "step_status": existing.get("status"),
                "status": "success", "duplicate": not created}

    async def claim_step(self, step_id: str, *, lease_owner: str,
                         lease_seconds: int = 120,
                         workload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not lease_owner or not 10 <= lease_seconds <= 900:
            return _error("invalid_contract", "Invalid step lease.", 400)
        for _ in range(5):
            step = await self.store.get("workflow_steps", step_id)
            if not step:
                return _error("step_not_found", "Workflow step does not exist.", 404)
            if step.get("status") == "COMPLETE":
                return {**step, "step_status": step.get("status"),
                        "status": "success", "duplicate": True}
            expiry = _parse_time(step.get("lease_expires_at"))
            if step.get("status") == "RUNNING" and expiry and expiry > datetime.now(timezone.utc):
                return _error("lease_conflict", "Workflow step is already leased.")
            generation = int(step.get("attempt_generation", 0)) + 1
            run = await self.store.get("workflow_runs", step["run_id"])
            if not run:
                return _error("run_not_found", "Workflow run does not exist.", 404)
            run_status = normalize_runtime_status(
                str(run.get("runtime_status") or ""))
            if run_status in {RuntimeStatus.CANCELLING.value,
                              RuntimeStatus.CANCELLED.value,
                              RuntimeStatus.SUCCEEDED.value,
                              RuntimeStatus.FAILED.value,
                              RuntimeStatus.REJECTED.value}:
                return _error("run_fenced", "Workflow run no longer accepts work.")
            cancel_generation = int(run.get("cancellation_generation") or 0)
            usage = dict(run.get("budget_usage") or {})
            limits = dict(run.get("budgets") or {})
            max_steps = int(limits.get("max_steps", 100))
            max_retries = int(limits.get("max_retries", 10))
            first_claim = generation == 1
            if (first_claim and int(usage.get("steps_started") or 0) >= max_steps):
                return _error("budget_exhausted",
                              "Workflow step budget is exhausted.", 429)
            if (not first_claim
                    and int(usage.get("retries") or 0) >= max_retries):
                return _error("budget_exhausted",
                              "Workflow retry budget is exhausted.", 429)
            next_usage = {
                **usage,
                "steps_started": int(usage.get("steps_started") or 0)
                + int(first_claim),
                "retries": int(usage.get("retries") or 0)
                + int(not first_claim),
            }
            lease_expires = (datetime.now(timezone.utc)
                             + timedelta(seconds=lease_seconds)).isoformat()
            attempt_id = stable_id("attempt", step_id, str(generation))
            attempt = {
                "schema_version": 1, "attempt_id": attempt_id, "step_id": step_id,
                "run_id": step["run_id"], "workspace_id": step["workspace_id"],
                "generation": generation,
                "lease_owner": lease_owner, "status": "RUNNING",
                "workload_principal": dict(workload or {}) or None,
                "started_at": utc_now(), "completed_at": None,
                "observed_cancellation_generation": cancel_generation,
                "provenance": step.get("provenance") or {}, "version": 1,
            }
            batch = await self.store.atomic_compare_and_set((
                AtomicMutation(
                    "workflow_runs", run["run_id"], int(run["version"]),
                    updates={"budget_usage": next_usage,
                             "runtime_status": RuntimeStatus.RUNNING.value,
                             "updated_at": utc_now()}),
                AtomicMutation(
                    "workflow_steps", step_id, int(step["version"]),
                    updates={"status": "RUNNING",
                             "attempt_generation": generation,
                             "lease_owner": lease_owner,
                             "lease_expires_at": lease_expires,
                             "observed_cancellation_generation": cancel_generation,
                             "workload_principal": dict(workload or {}) or None,
                             "updated_at": utc_now()}),
                AtomicMutation("step_attempts", attempt_id, None,
                               record=attempt),
            ))
            if not batch:
                continue
            claimed = batch[("workflow_steps", step_id)]
            return {**claimed, "step_status": claimed.get("status"),
                    "status": "success", "duplicate": False,
                    "attempt_id": attempt_id}
        return _error("concurrency_conflict", "Could not claim workflow step.")

    async def complete_step(self, step_id: str, *, lease_owner: str,
                            generation: int, result_ref: str = "") -> dict[str, Any]:
        step = await self.store.get("workflow_steps", step_id)
        if not step:
            return _error("step_not_found", "Workflow step does not exist.", 404)
        if step.get("status") == "COMPLETE":
            return {**step, "step_status": step.get("status"),
                    "status": "success", "duplicate": True}
        lease_expiry = _parse_time(step.get("lease_expires_at"))
        if (step.get("status") != "RUNNING" or step.get("lease_owner") != lease_owner
                or int(step.get("attempt_generation", 0)) != generation
                or not lease_expiry
                or lease_expiry <= datetime.now(timezone.utc)):
            return _error("lease_lost", "Step lease is no longer authoritative.")
        attempt_id = stable_id("attempt", step_id, str(generation))
        attempt = await self.store.get("step_attempts", attempt_id)
        run = await self.store.get("workflow_runs", step["run_id"])
        if not run or not attempt:
            return _error("attempt_not_found", "Step attempt is incomplete.", 409)
        observed = int(step.get("observed_cancellation_generation") or 0)
        if (int(run.get("cancellation_generation") or 0) != observed
                or normalize_runtime_status(str(run.get("runtime_status") or ""))
                in {RuntimeStatus.CANCELLING.value,
                    RuntimeStatus.CANCELLED.value}):
            return _error("run_fenced",
                          "Run cancellation fenced this step outcome.")
        event_key = f"step-complete:{step_id}:{generation}"
        event_id = stable_id("evt", step["run_id"], event_key)
        sequence = int(run.get("next_event_sequence") or 1)
        now = utc_now()
        event = {
            "schema_version": 2, "event_id": event_id,
            "run_id": step["run_id"], "workspace_id": step["workspace_id"],
            "journey_id": run["journey_id"], "sequence": sequence,
            "event_kind": "STEP_COMPLETED", "event_schema_version": 1,
            "reducer_version": 1, "idempotency_key": event_key,
            "safe_payload": {"step_id": step_id, "result_ref": result_ref},
            "actor_id": None, "workload_principal": None,
            "causation_event_id": None, "occurred_at": now,
            "provenance": run.get("provenance") or {}, "version": 1,
        }
        batch = await self.store.atomic_compare_and_set((
            AtomicMutation(
                "workflow_runs", run["run_id"], int(run["version"]),
                updates={"next_event_sequence": sequence + 1,
                         "updated_at": now}),
            AtomicMutation(
                "workflow_steps", step_id, int(step["version"]),
                updates={"status": "COMPLETE", "result_ref": result_ref or None,
                         "lease_owner": None, "lease_expires_at": None,
                         "updated_at": now, "completed_at": now}),
            AtomicMutation(
                "step_attempts", attempt_id, int(attempt["version"]),
                updates={"status": "COMPLETE", "result_ref": result_ref or None,
                         "completed_at": now}),
            AtomicMutation("run_events", event_id, None, record=event),
        ))
        if not batch:
            existing_event = await self.store.get("run_events", event_id)
            existing_step = await self.store.get("workflow_steps", step_id)
            if existing_event and existing_step and existing_step.get("status") == "COMPLETE":
                return {**existing_step, "step_status": "COMPLETE",
                        "status": "success", "duplicate": True}
            return _error("concurrency_conflict", "Step changed concurrently.")
        committed = batch[("workflow_steps", step_id)]
        await self._publish_run(
            batch[("workflow_runs", run["run_id"])],
            event_kind="STEP_COMPLETED", event_sequence=sequence)
        return {**committed, "step_status": committed.get("status"),
                "status": "success", "duplicate": False}

    async def create_wait(self, run_id: str, *, wait_kind: str,
                          correlation_key: str, wake_after: str | None = None,
                          origin_session_id: str = "") -> dict[str, Any]:
        if wait_kind not in WAIT_CONTRACTS or not correlation_key:
            return _error("wait_contract_invalid",
                          "Wait kind or correlation contract is not registered.", 400)
        run = await self.store.get("workflow_runs", run_id)
        if not run:
            return _error("run_not_found", "Workflow run does not exist.", 404)
        if normalize_runtime_status(str(run.get("runtime_status") or "")) in {
                RuntimeStatus.CANCELLING.value, RuntimeStatus.CANCELLED.value,
                RuntimeStatus.SUCCEEDED.value, RuntimeStatus.FAILED.value,
                RuntimeStatus.REJECTED.value}:
            return _error("run_fenced", "Workflow run no longer accepts waits.")
        if wake_after:
            try:
                due = datetime.fromisoformat(wake_after.replace("Z", "+00:00"))
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                wake_after = due.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                return _error("wait_contract_invalid",
                              "Timer due time is invalid.", 400)
        wait_id = stable_id("wait", run_id, wait_kind, correlation_key)
        row = {
            "schema_version": 1, "wait_id": wait_id, "run_id": run_id,
            "workspace_id": run["workspace_id"], "wait_kind": wait_kind,
            "correlation_key": correlation_key, "status": "OPEN",
            "origin_session_id": origin_session_id or None,
            "wake_after": wake_after, "resolved_by_event_id": None,
            "timer_status": "PENDING" if wake_after else "NOT_REQUIRED",
            "timer_checkpoint_generation": 1 if wake_after else 0,
            "timer_checkpoint_at": None, "timer_last_error": None,
            "generation": 1, "provenance": run.get("provenance") or {},
            "observed_cancellation_generation": int(
                run.get("cancellation_generation") or 0),
            "created_at": utc_now(),
            "updated_at": utc_now(), "version": 1,
        }
        event_key = f"wait-open:{wait_id}"
        event_id = stable_id("evt", run_id, event_key)
        sequence = int(run.get("next_event_sequence") or 1)
        now = utc_now()
        next_status = (RuntimeStatus.PAUSED.value
                       if normalize_runtime_status(str(run.get("runtime_status")))
                       == RuntimeStatus.PAUSED.value else RuntimeStatus.WAITING.value)
        event = {
            "schema_version": 2, "event_id": event_id, "run_id": run_id,
            "workspace_id": run["workspace_id"], "journey_id": run["journey_id"],
            "sequence": sequence, "event_kind": "WAIT_OPENED",
            "event_schema_version": 1, "reducer_version": 1,
            "idempotency_key": event_key,
            "safe_payload": {"wait_id": wait_id, "wait_kind": wait_kind},
            "actor_id": None, "workload_principal": None,
            "causation_event_id": None, "occurred_at": now,
            "provenance": run.get("provenance") or {}, "version": 1,
        }
        batch = await self.store.atomic_compare_and_set((
            AtomicMutation(
                "workflow_runs", run_id, int(run["version"]),
                updates={"runtime_status": next_status,
                         "next_event_sequence": sequence + 1,
                         "updated_at": now}),
            AtomicMutation("waits", wait_id, None, record=row),
            AtomicMutation("run_events", event_id, None, record=event),
        ))
        if not batch:
            existing = await self.store.get("waits", wait_id) or {}
            if existing:
                return {**existing, "wait_status": existing.get("status"),
                        "status": "success", "duplicate": True}
            return _error("concurrency_conflict", "Could not open workflow wait.")
        committed = batch[("waits", wait_id)]
        await self._publish_run(
            batch[("workflow_runs", run_id)],
            event_kind="WAIT_OPENED", event_sequence=sequence)
        timer_result = None
        if wake_after and os.environ.get("K_SERVICE"):
            from services.workflow_timer_service import schedule_wait_timer

            timer_result = await schedule_wait_timer(
                wait_id, expected_generation=1, store=self.store)
            committed = await self.store.get("waits", wait_id) or committed
        return {**committed, "wait_status": committed.get("status"),
                "status": "success", "duplicate": False,
                "timer_schedule": timer_result}

    async def resolve_wait(self, wait_id: str, *, event_id: str,
                           expected_generation: int = 1) -> dict[str, Any]:
        wait = await self.store.get("waits", wait_id)
        if not wait:
            return _error("wait_not_found", "Workflow wait does not exist.", 404)
        if wait.get("status") == "RESOLVED":
            if wait.get("resolved_by_event_id") == event_id:
                delivery_id = stable_id(
                    "wake", wait["run_id"], wait_id, event_id,
                    str(expected_generation))
                return {**wait, "wait_status": wait.get("status"),
                        "status": "success", "duplicate": True,
                        "wake_delivery_id": delivery_id}
            return _error("wait_already_resolved", "Wait was resolved by another event.")
        # Only an OPEN wait is resolvable. A late event must never revive a wait
        # that cancel_run already closed, or the cancellation record is erased
        # and a WAIT_RESOLVED event lands on a cancelled run.
        if wait.get("status") != "OPEN":
            return _error("wait_not_open",
                          "Wait is no longer open for resolution.")
        if int(wait.get("generation", 0)) != expected_generation:
            return _error("generation_mismatch", "Wait generation is stale.")
        run = await self.store.get("workflow_runs", wait["run_id"])
        if not run:
            return _error("run_not_found", "Workflow run does not exist.", 404)
        if (int(run.get("cancellation_generation") or 0)
                != int(wait.get("observed_cancellation_generation") or 0)
                or normalize_runtime_status(str(run.get("runtime_status") or ""))
                in {RuntimeStatus.CANCELLING.value,
                    RuntimeStatus.CANCELLED.value}):
            return _error("run_fenced", "Run cancellation fenced this wait.")
        event_key = f"wait-resolve:{wait_id}:{event_id}"
        run_event_id = stable_id("evt", wait["run_id"], event_key)
        sequence = int(run.get("next_event_sequence") or 1)
        now = utc_now()
        next_status = (RuntimeStatus.PAUSED.value
                       if normalize_runtime_status(str(run.get("runtime_status")))
                       == RuntimeStatus.PAUSED.value else RuntimeStatus.QUEUED.value)
        event = {
            "schema_version": 2, "event_id": run_event_id,
            "run_id": wait["run_id"], "workspace_id": wait["workspace_id"],
            "journey_id": run["journey_id"], "sequence": sequence,
            "event_kind": "WAIT_RESOLVED", "event_schema_version": 1,
            "reducer_version": 1, "idempotency_key": event_key,
            "safe_payload": {"wait_id": wait_id, "external_event_id": event_id},
            "actor_id": None, "workload_principal": None,
            "causation_event_id": event_id, "occurred_at": now,
            "provenance": run.get("provenance") or {}, "version": 1,
        }
        delivery_id = stable_id(
            "wake", wait["run_id"], wait_id, event_id,
            str(expected_generation))
        delivery = {
            "schema_version": 2, "delivery_id": delivery_id,
            "workspace_id": wait["workspace_id"],
            "delivery_domain": "WORKFLOW_WAKE",
            "founder_id": wait["workspace_id"],
            "run_id": wait["run_id"], "wait_id": wait_id,
            "event_id": event_id, "generation": expected_generation,
            "destination_kind": "RUN_WAKE",
            "session_id": wait.get("origin_session_id"),
            "source_kind": "workflow_wait", "source_id": event_id,
            "notice": f"Workflow wait {wait_id} resolved.",
            "state_delta": {}, "status": "PENDING", "attempt": 0,
            "max_attempts": 8, "next_attempt_at": None,
            "lease_owner": None, "lease_generation": 0,
            "lease_started_at": None, "lease_expires_at": None,
            "lease_seconds": 120, "last_error_code": None,
            "created_at": now, "updated_at": now,
            "delivered_at": None, "version": 1,
        }
        batch = await self.store.atomic_compare_and_set((
            AtomicMutation(
                "workflow_runs", run["run_id"], int(run["version"]),
                updates={"runtime_status": next_status,
                         "next_event_sequence": sequence + 1,
                         "updated_at": now}),
            AtomicMutation(
                "waits", wait_id, int(wait["version"]),
                updates={"status": "RESOLVED",
                         "resolved_by_event_id": event_id,
                         "resolved_at": now, "updated_at": now}),
            AtomicMutation("run_events", run_event_id, None, record=event),
            AtomicMutation(
                "wake_deliveries", delivery_id, None, record=delivery),
        ))
        if not batch:
            current = await self.store.get("waits", wait_id)
            if (current and current.get("status") == "RESOLVED"
                    and current.get("resolved_by_event_id") == event_id):
                return {**current, "wait_status": "RESOLVED",
                        "status": "success", "duplicate": True}
            return _error("concurrency_conflict", "Wait changed concurrently.")
        committed = batch[("waits", wait_id)]
        await self._publish_run(
            batch[("workflow_runs", run["run_id"])],
            event_kind="WAIT_RESOLVED", event_sequence=sequence)
        return {**committed, "wait_status": committed.get("status"),
                "status": "success", "duplicate": False,
                "wake_delivery_id": delivery_id}

    async def pause_run(self, run_id: str, *, actor_id: str,
                        reason: str) -> dict[str, Any]:
        """Pause bounded execution without resolving or discarding open waits."""
        for _ in range(5):
            run = await self.store.get("workflow_runs", run_id)
            if not run:
                return _error("run_not_found", "Workflow run does not exist.", 404)
            status = normalize_runtime_status(str(run.get("runtime_status") or ""))
            if status == RuntimeStatus.PAUSED.value:
                return {"status": "success", "duplicate": True, **run}
            if status in {RuntimeStatus.CANCELLING.value,
                          RuntimeStatus.CANCELLED.value,
                          RuntimeStatus.SUCCEEDED.value,
                          RuntimeStatus.FAILED.value,
                          RuntimeStatus.REJECTED.value}:
                return _error("terminal_run", "Terminal run cannot be paused.")
            sequence = int(run.get("next_event_sequence") or 1)
            event_key = f"pause:{run_id}:{int(run['version']) + 1}"
            event_id = stable_id("evt", run_id, event_key)
            now = utc_now()
            event = self._event_row(
                run, event_id=event_id, sequence=sequence,
                event_kind="RUN_PAUSED", idempotency_key=event_key,
                safe_payload={"reason": reason[:200]}, actor_id=actor_id,
                occurred_at=now)
            batch = await self.store.atomic_compare_and_set((
                AtomicMutation(
                    "workflow_runs", run_id, int(run["version"]),
                    updates={"runtime_status": RuntimeStatus.PAUSED.value,
                             "pause_reason": reason[:500],
                             "paused_by_actor_id": actor_id, "paused_at": now,
                             "next_event_sequence": sequence + 1,
                             "updated_at": now}),
                AtomicMutation("run_events", event_id, None, record=event),
            ))
            if batch:
                committed = batch[("workflow_runs", run_id)]
                await self._publish_run(
                    committed, event_kind="RUN_PAUSED", event_sequence=sequence)
                return {"status": "success", "duplicate": False, **committed}
        return _error("concurrency_conflict", "Run changed concurrently.")

    async def resume_run(self, run_id: str, *, actor_id: str) -> dict[str, Any]:
        """Resume to DORMANT when unresolved waits remain, otherwise ACTIVE."""
        for _ in range(5):
            run = await self.store.get("workflow_runs", run_id)
            if not run:
                return _error("run_not_found", "Workflow run does not exist.", 404)
            if run.get("runtime_status") != RuntimeStatus.PAUSED.value:
                if run.get("runtime_status") in {
                        RuntimeStatus.QUEUED.value, RuntimeStatus.WAITING.value}:
                    return {"status": "success", "duplicate": True, **run}
                return _error("terminal_run", "Only a paused run can resume.")
            open_waits = await self.store.list(
                "waits", filters={"run_id": run_id, "status": "OPEN"}, limit=1000)
            next_status = (RuntimeStatus.WAITING.value if open_waits
                           else RuntimeStatus.QUEUED.value)
            sequence = int(run.get("next_event_sequence") or 1)
            event_key = f"resume:{run_id}:{int(run['version']) + 1}"
            event_id = stable_id("evt", run_id, event_key)
            now = utc_now()
            event = self._event_row(
                run, event_id=event_id, sequence=sequence,
                event_kind="RUN_RESUMED", idempotency_key=event_key,
                safe_payload={"runtime_status": next_status}, actor_id=actor_id,
                occurred_at=now)
            batch = await self.store.atomic_compare_and_set((
                AtomicMutation(
                    "workflow_runs", run_id, int(run["version"]),
                    updates={"runtime_status": next_status,
                             "resumed_by_actor_id": actor_id, "resumed_at": now,
                             "next_event_sequence": sequence + 1,
                             "updated_at": now}),
                AtomicMutation("run_events", event_id, None, record=event),
            ))
            if batch:
                committed = batch[("workflow_runs", run_id)]
                await self._publish_run(
                    committed, event_kind="RUN_RESUMED", event_sequence=sequence)
                return {"status": "success", "duplicate": False, **committed}
        return _error("concurrency_conflict", "Run changed concurrently.")

    async def cancel_run(self, run_id: str, *, actor_id: str,
                         reason: str) -> dict[str, Any]:
        for _ in range(8):
            run = await self.store.get("workflow_runs", run_id)
            if not run:
                return _error("run_not_found", "Workflow run does not exist.", 404)
            status = normalize_runtime_status(str(run.get("runtime_status") or ""))
            if status == RuntimeStatus.CANCELLED.value:
                await self._cancel_children(run, actor_id=actor_id, reason=reason)
                return {"status": "success", "duplicate": True, **run}
            if status == RuntimeStatus.SUCCEEDED.value:
                return _error("terminal_run", "Completed run cannot be cancelled.")
            if status != RuntimeStatus.CANCELLING.value:
                generation = int(run.get("cancellation_generation") or 0) + 1
                event_key = f"cancel-start:{run_id}:{generation}"
                event_id = stable_id("evt", run_id, event_key)
                sequence = int(run.get("next_event_sequence") or 1)
                now = utc_now()
                event = self._event_row(
                    run, event_id=event_id, sequence=sequence,
                    event_kind="RUN_CANCELLING", idempotency_key=event_key,
                    safe_payload={"reason": reason[:200],
                                  "cancellation_generation": generation},
                    actor_id=actor_id, occurred_at=now)
                started = await self.store.atomic_compare_and_set((
                    AtomicMutation(
                        "workflow_runs", run_id, int(run["version"]),
                        updates={"runtime_status": RuntimeStatus.CANCELLING.value,
                                 "cancellation_generation": generation,
                                 "cancel_reason": reason[:500],
                                 "cancelled_by_actor_id": actor_id,
                                 "cancellation_started_at": now,
                                 "cancellation_cleanup_complete": False,
                                 "next_event_sequence": sequence + 1,
                                 "updated_at": now}),
                    AtomicMutation("run_events", event_id, None, record=event),
                ))
                if not started:
                    continue
                run = started[("workflow_runs", run_id)]

            # Exhaust the query rather than treating the store's 1,000 row cap
            # as completion. Every successful CAS removes a row from the next
            # page; contention is retried by the outer pass.
            while True:
                waits = await self.store.list(
                    "waits", filters={"run_id": run_id, "status": "OPEN"},
                    limit=1000)
                if not waits:
                    break
                progressed = False
                for wait in waits:
                    closed = await self.store.compare_and_set(
                        "waits", wait["wait_id"], int(wait["version"]),
                        {"status": "CANCELLED", "cancelled_at": utc_now(),
                         "updated_at": utc_now()})
                    progressed = progressed or bool(closed)
                if not progressed:
                    return _error("concurrency_conflict",
                                  "Could not exhaust open waits during cancellation.")

            current = await self.store.get("workflow_runs", run_id)
            if not current:
                return _error("run_not_found", "Workflow run does not exist.", 404)
            if normalize_runtime_status(str(current.get("runtime_status"))) \
                    != RuntimeStatus.CANCELLING.value:
                continue
            generation = int(current.get("cancellation_generation") or 0)
            event_key = f"cancel-complete:{run_id}:{generation}"
            event_id = stable_id("evt", run_id, event_key)
            sequence = int(current.get("next_event_sequence") or 1)
            now = utc_now()
            event = self._event_row(
                current, event_id=event_id, sequence=sequence,
                event_kind="RUN_CANCELLED", idempotency_key=event_key,
                safe_payload={"reason": reason[:200],
                              "cancellation_generation": generation},
                actor_id=actor_id, occurred_at=now)
            finished = await self.store.atomic_compare_and_set((
                AtomicMutation(
                    "workflow_runs", run_id, int(current["version"]),
                    updates={"runtime_status": RuntimeStatus.CANCELLED.value,
                             "cancellation_cleanup_complete": True,
                             "cancelled_at": now,
                             "next_event_sequence": sequence + 1,
                             "updated_at": now}),
                AtomicMutation("run_events", event_id, None, record=event),
            ))
            if not finished:
                continue
            committed = finished[("workflow_runs", run_id)]
            await self._publish_run(
                committed, event_kind="RUN_CANCELLED", event_sequence=sequence)
            await self._cancel_children(committed, actor_id=actor_id, reason=reason)
            return {"status": "success", "duplicate": False, **committed}
        return _error("concurrency_conflict", "Run changed concurrently.")

    @staticmethod
    def _event_row(run: dict[str, Any], *, event_id: str, sequence: int,
                   event_kind: str, idempotency_key: str,
                   safe_payload: dict[str, Any], actor_id: str = "",
                   occurred_at: str | None = None) -> dict[str, Any]:
        return {
            "schema_version": 2, "event_id": event_id,
            "run_id": run["run_id"], "workspace_id": run["workspace_id"],
            "journey_id": run["journey_id"], "sequence": sequence,
            "event_kind": event_kind, "event_schema_version": 1,
            "reducer_version": 1, "idempotency_key": idempotency_key,
            "safe_payload": safe_payload, "actor_id": actor_id or None,
            "workload_principal": None, "causation_event_id": None,
            "occurred_at": occurred_at or utc_now(),
            "provenance": run.get("provenance") or {}, "version": 1,
        }

    async def _cancel_children(self, run: dict[str, Any], *, actor_id: str,
                               reason: str) -> None:
        """Recursively converge descendants; terminal completed children remain proof."""
        children = await self.store.list(
            "workflow_runs", filters={"parent_run_id": run["run_id"]}, limit=1000)
        for child in children:
            if normalize_runtime_status(str(child.get("runtime_status"))) == \
                    RuntimeStatus.SUCCEEDED.value:
                continue
            await self.cancel_run(child["run_id"], actor_id=actor_id,
                                  reason=f"Parent cancelled: {reason}"[:500])


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
