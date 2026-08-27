"""Generic durable, event-driven workflow runtime (docs/21 phases 1A-1C).

Runtime state is deliberately independent from hiring domain state. No method
polls or sleeps: an event, timer delivery, or explicit human command performs a
bounded transition and persists the next wait.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from services import hiring_activation
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import RunKind, RuntimeStatus, stable_id, utc_now


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}


class WorkflowRuntime:
    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    async def create_run(
            self, *, workspace_id: str, journey_id: str, run_kind: RunKind,
            idempotency_key: str, domain_ref: str, synthetic_guard: dict[str, Any],
            parent_run_id: str | None = None,
            originating_actor_id: str = "") -> dict[str, Any]:
        gate = hiring_activation.require_synthetic(synthetic_guard)
        if gate.get("error"):
            return gate
        run_id = stable_id("run", workspace_id, journey_id, run_kind.value,
                           idempotency_key)
        if parent_run_id:
            parent = await self.store.get("workflow_runs", parent_run_id)
            if not parent or parent.get("workspace_id") != workspace_id:
                return _error("parent_run_missing", "Parent run does not exist.", 404)
            if parent.get("journey_id") != journey_id:
                return _error("journey_mismatch", "Parent run is in another journey.")
            allowed_parent = {
                RunKind.CANDIDATE.value: RunKind.ROLE.value,
                RunKind.ONBOARDING.value: RunKind.CANDIDATE.value,
            }.get(run_kind.value)
            if parent.get("run_kind") != allowed_parent:
                return _error("invalid_run_hierarchy", "Invalid parent/child run kinds.")
        elif run_kind is not RunKind.ROLE:
            return _error("parent_run_required", "Child runs require a parent run.")
        now = utc_now()
        row = {
            "schema_version": 1, "run_id": run_id, "workspace_id": workspace_id,
            "journey_id": journey_id, "run_kind": run_kind.value,
            "parent_run_id": parent_run_id, "domain_ref": domain_ref,
            "runtime_status": RuntimeStatus.ACTIVE.value,
            "next_event_sequence": 1, "pending_event": None,
            "cancel_reason": None, "originating_actor_id": originating_actor_id,
            "synthetic": True,
            "synthetic_namespace": synthetic_guard["synthetic_namespace"],
            "fixture_id": synthetic_guard["fixture_id"],
            "created_at": now, "updated_at": now, "version": 1,
        }
        created = await self.store.create("workflow_runs", run_id, row)
        if not created:
            existing = await self.store.get("workflow_runs", run_id)
            if existing and all(existing.get(field) == row[field] for field in (
                    "workspace_id", "journey_id", "run_kind", "parent_run_id",
                    "domain_ref", "synthetic_namespace", "fixture_id")):
                return {"status": "success", "duplicate": True, **existing}
            return _error("idempotency_conflict", "Run key already names different work.")
        event = await self.append_event(
            run_id, event_kind="RUN_CREATED", idempotency_key=f"create:{run_id}",
            safe_payload={"run_kind": run_kind.value, "domain_ref": domain_ref},
            actor_id=originating_actor_id)
        if event.get("error"):
            return event
        return {"status": "success", "duplicate": False, **row}

    async def append_event(self, run_id: str, *, event_kind: str,
                           idempotency_key: str, safe_payload: dict[str, Any],
                           actor_id: str = "",
                           workload: dict[str, Any] | None = None,
                           causation_event_id: str = "") -> dict[str, Any]:
        """Append once with a recoverable sequence reservation."""
        event_id = stable_id("evt", run_id, idempotency_key)
        existing = await self.store.get("run_events", event_id)
        if existing:
            return {"status": "success", "duplicate": True, **existing}
        for _ in range(5):
            run = await self.store.get("workflow_runs", run_id)
            if not run:
                return _error("run_not_found", "Workflow run does not exist.", 404)
            pending = run.get("pending_event")
            if pending:
                recovered = await self._materialize_pending(run)
                if recovered.get("error"):
                    return recovered
                if pending.get("event_id") == event_id:
                    return {"status": "success", "duplicate": True,
                            **(await self.store.get("run_events", event_id) or {})}
                continue
            sequence = int(run.get("next_event_sequence", 1))
            event = {
                "schema_version": 1, "event_id": event_id, "run_id": run_id,
                "workspace_id": run["workspace_id"], "journey_id": run["journey_id"],
                "sequence": sequence, "event_kind": event_kind,
                "idempotency_key": idempotency_key,
                "safe_payload": {str(key)[:80]: value for key, value in safe_payload.items()},
                "actor_id": actor_id or None,
                "workload_principal": dict(workload or {}) or None,
                "causation_event_id": causation_event_id or None,
                "occurred_at": utc_now(), "synthetic": run.get("synthetic") is True,
                "synthetic_namespace": run.get("synthetic_namespace"),
                "fixture_id": run.get("fixture_id"), "version": 1,
            }
            reserved = await self.store.compare_and_set(
                "workflow_runs", run_id, int(run["version"]), {
                    "pending_event": event, "next_event_sequence": sequence + 1,
                    "updated_at": utc_now(),
                })
            if not reserved:
                continue
            return await self._materialize_pending(reserved)
        return _error("concurrency_conflict", "Could not reserve run event.")

    async def _materialize_pending(self, run: dict[str, Any]) -> dict[str, Any]:
        event = run.get("pending_event")
        if not isinstance(event, dict) or not event.get("event_id"):
            return {"status": "success"}
        await self.store.create("run_events", event["event_id"], event)
        current = await self.store.get("workflow_runs", run["run_id"])
        if current and (current.get("pending_event") or {}).get("event_id") == event["event_id"]:
            await self.store.compare_and_set(
                "workflow_runs", run["run_id"], int(current["version"]),
                {"pending_event": None, "updated_at": utc_now()})
        return {"status": "success", "duplicate": False, **event}

    async def recover_run(self, run_id: str) -> dict[str, Any]:
        run = await self.store.get("workflow_runs", run_id)
        if not run:
            return _error("run_not_found", "Workflow run does not exist.", 404)
        result = await self._materialize_pending(run)
        if result.get("error"):
            return result
        return {"status": "success", "run_id": run_id}

    async def create_step(self, run_id: str, *, step_key: str,
                          idempotency_key: str) -> dict[str, Any]:
        run = await self.store.get("workflow_runs", run_id)
        if not run:
            return _error("run_not_found", "Workflow run does not exist.", 404)
        step_id = stable_id("step", run_id, step_key)
        row = {
            "schema_version": 1, "step_id": step_id, "run_id": run_id,
            "workspace_id": run["workspace_id"], "step_key": step_key,
            "status": "READY", "attempt_generation": 0,
            "lease_owner": None, "lease_expires_at": None,
            "idempotency_key": idempotency_key,
            "synthetic": run["synthetic"],
            "synthetic_namespace": run["synthetic_namespace"],
            "fixture_id": run["fixture_id"], "created_at": utc_now(),
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
            lease_expires = (datetime.now(timezone.utc)
                             + timedelta(seconds=lease_seconds)).isoformat()
            claimed = await self.store.compare_and_set(
                "workflow_steps", step_id, int(step["version"]), {
                    "status": "RUNNING", "attempt_generation": generation,
                    "lease_owner": lease_owner, "lease_expires_at": lease_expires,
                    "workload_principal": dict(workload or {}) or None,
                    "updated_at": utc_now(),
                })
            if not claimed:
                continue
            attempt_id = stable_id("attempt", step_id, str(generation))
            attempt = {
                "schema_version": 1, "attempt_id": attempt_id, "step_id": step_id,
                "run_id": step["run_id"], "workspace_id": step["workspace_id"],
                "generation": generation,
                "lease_owner": lease_owner, "status": "RUNNING",
                "workload_principal": dict(workload or {}) or None,
                "started_at": utc_now(), "completed_at": None,
                "synthetic": step["synthetic"],
                "synthetic_namespace": step["synthetic_namespace"],
                "fixture_id": step["fixture_id"], "version": 1,
            }
            await self.store.create("step_attempts", attempt_id, attempt)
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
        if (step.get("status") != "RUNNING" or step.get("lease_owner") != lease_owner
                or int(step.get("attempt_generation", 0)) != generation):
            return _error("lease_lost", "Step lease is no longer authoritative.")
        committed = await self.store.compare_and_set(
            "workflow_steps", step_id, int(step["version"]), {
                "status": "COMPLETE", "result_ref": result_ref or None,
                "lease_owner": None, "lease_expires_at": None,
                "updated_at": utc_now(), "completed_at": utc_now(),
            })
        if not committed:
            return _error("concurrency_conflict", "Step changed concurrently.")
        attempt_id = stable_id("attempt", step_id, str(generation))
        attempt = await self.store.get("step_attempts", attempt_id)
        if attempt:
            await self.store.compare_and_set(
                "step_attempts", attempt_id, int(attempt["version"]),
                {"status": "COMPLETE", "result_ref": result_ref or None,
                 "completed_at": utc_now()})
        await self.append_event(
            step["run_id"], event_kind="STEP_COMPLETED",
            idempotency_key=f"step-complete:{step_id}:{generation}",
            safe_payload={"step_id": step_id, "result_ref": result_ref})
        return {**committed, "step_status": committed.get("status"),
                "status": "success", "duplicate": False}

    async def create_wait(self, run_id: str, *, wait_kind: str,
                          correlation_key: str, wake_after: str | None = None) -> dict[str, Any]:
        run = await self.store.get("workflow_runs", run_id)
        if not run:
            return _error("run_not_found", "Workflow run does not exist.", 404)
        wait_id = stable_id("wait", run_id, wait_kind, correlation_key)
        row = {
            "schema_version": 1, "wait_id": wait_id, "run_id": run_id,
            "workspace_id": run["workspace_id"], "wait_kind": wait_kind,
            "correlation_key": correlation_key, "status": "OPEN",
            "wake_after": wake_after, "resolved_by_event_id": None,
            "generation": 1, "synthetic": run["synthetic"],
            "synthetic_namespace": run["synthetic_namespace"],
            "fixture_id": run["fixture_id"], "created_at": utc_now(),
            "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create("waits", wait_id, row)
        if not created:
            existing = await self.store.get("waits", wait_id)
            existing = existing or {}
            return {**existing, "wait_status": existing.get("status"),
                    "status": "success", "duplicate": True}
        current = await self.store.get("workflow_runs", run_id)
        if current and current.get("runtime_status") not in {
                RuntimeStatus.PAUSED.value, RuntimeStatus.CANCELLED.value}:
            await self.store.compare_and_set(
                "workflow_runs", run_id, int(current["version"]),
                {"runtime_status": RuntimeStatus.DORMANT.value,
                 "updated_at": utc_now()})
        await self.append_event(
            run_id, event_kind="WAIT_OPENED", idempotency_key=f"wait-open:{wait_id}",
            safe_payload={"wait_id": wait_id, "wait_kind": wait_kind})
        return {**row, "wait_status": row.get("status"),
                "status": "success", "duplicate": False}

    async def resolve_wait(self, wait_id: str, *, event_id: str,
                           expected_generation: int = 1) -> dict[str, Any]:
        wait = await self.store.get("waits", wait_id)
        if not wait:
            return _error("wait_not_found", "Workflow wait does not exist.", 404)
        if wait.get("status") == "RESOLVED":
            if wait.get("resolved_by_event_id") == event_id:
                return {**wait, "wait_status": wait.get("status"),
                        "status": "success", "duplicate": True}
            return _error("wait_already_resolved", "Wait was resolved by another event.")
        # Only an OPEN wait is resolvable. A late event must never revive a wait
        # that cancel_run already closed, or the cancellation record is erased
        # and a WAIT_RESOLVED event lands on a cancelled run.
        if wait.get("status") != "OPEN":
            return _error("wait_not_open",
                          "Wait is no longer open for resolution.")
        if int(wait.get("generation", 0)) != expected_generation:
            return _error("generation_mismatch", "Wait generation is stale.")
        committed = await self.store.compare_and_set(
            "waits", wait_id, int(wait["version"]), {
                "status": "RESOLVED", "resolved_by_event_id": event_id,
                "resolved_at": utc_now(), "updated_at": utc_now(),
            })
        if not committed:
            return _error("concurrency_conflict", "Wait changed concurrently.")
        run = await self.store.get("workflow_runs", wait["run_id"])
        if run and run.get("runtime_status") == RuntimeStatus.DORMANT.value:
            await self.store.compare_and_set(
                "workflow_runs", wait["run_id"], int(run["version"]),
                {"runtime_status": RuntimeStatus.ACTIVE.value, "updated_at": utc_now()})
        await self.append_event(
            wait["run_id"], event_kind="WAIT_RESOLVED",
            idempotency_key=f"wait-resolve:{wait_id}:{event_id}",
            safe_payload={"wait_id": wait_id, "external_event_id": event_id},
            causation_event_id=event_id)
        return {**committed, "wait_status": committed.get("status"),
                "status": "success", "duplicate": False}

    async def pause_run(self, run_id: str, *, actor_id: str,
                        reason: str) -> dict[str, Any]:
        """Pause bounded execution without resolving or discarding open waits."""
        for _ in range(5):
            run = await self.store.get("workflow_runs", run_id)
            if not run:
                return _error("run_not_found", "Workflow run does not exist.", 404)
            status = run.get("runtime_status")
            if status == RuntimeStatus.PAUSED.value:
                return {"status": "success", "duplicate": True, **run}
            if status in {RuntimeStatus.CANCELLED.value, RuntimeStatus.COMPLETED.value,
                          RuntimeStatus.FAILED.value}:
                return _error("terminal_run", "Terminal run cannot be paused.")
            committed = await self.store.compare_and_set(
                "workflow_runs", run_id, int(run["version"]), {
                    "runtime_status": RuntimeStatus.PAUSED.value,
                    "pause_reason": reason[:500], "paused_by_actor_id": actor_id,
                    "paused_at": utc_now(), "updated_at": utc_now(),
                })
            if committed:
                await self.append_event(
                    run_id, event_kind="RUN_PAUSED",
                    idempotency_key=f"pause:{run_id}:{committed['version']}",
                    safe_payload={"reason": reason[:200]}, actor_id=actor_id)
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
                        RuntimeStatus.ACTIVE.value, RuntimeStatus.DORMANT.value}:
                    return {"status": "success", "duplicate": True, **run}
                return _error("terminal_run", "Only a paused run can resume.")
            open_waits = await self.store.list(
                "waits", filters={"run_id": run_id, "status": "OPEN"}, limit=1000)
            next_status = (RuntimeStatus.DORMANT.value if open_waits
                           else RuntimeStatus.ACTIVE.value)
            committed = await self.store.compare_and_set(
                "workflow_runs", run_id, int(run["version"]), {
                    "runtime_status": next_status, "resumed_by_actor_id": actor_id,
                    "resumed_at": utc_now(), "updated_at": utc_now(),
                })
            if committed:
                await self.append_event(
                    run_id, event_kind="RUN_RESUMED",
                    idempotency_key=f"resume:{run_id}:{committed['version']}",
                    safe_payload={"runtime_status": next_status}, actor_id=actor_id)
                return {"status": "success", "duplicate": False, **committed}
        return _error("concurrency_conflict", "Run changed concurrently.")

    async def cancel_run(self, run_id: str, *, actor_id: str,
                         reason: str) -> dict[str, Any]:
        for _ in range(5):
            run = await self.store.get("workflow_runs", run_id)
            if not run:
                return _error("run_not_found", "Workflow run does not exist.", 404)
            if run.get("runtime_status") == RuntimeStatus.CANCELLED.value:
                await self._cancel_children(run, actor_id=actor_id, reason=reason)
                return {"status": "success", "duplicate": True, **run}
            if run.get("runtime_status") == RuntimeStatus.COMPLETED.value:
                return _error("terminal_run", "Completed run cannot be cancelled.")
            committed = await self.store.compare_and_set(
                "workflow_runs", run_id, int(run["version"]), {
                    "runtime_status": RuntimeStatus.CANCELLED.value,
                    "cancel_reason": reason[:500], "cancelled_by_actor_id": actor_id,
                    "cancelled_at": utc_now(), "updated_at": utc_now(),
                })
            if committed:
                waits = await self.store.list("waits", filters={"run_id": run_id,
                                                                 "status": "OPEN"},
                                              limit=1000)
                for wait in waits:
                    await self.store.compare_and_set(
                        "waits", wait["wait_id"], int(wait["version"]),
                        {"status": "CANCELLED", "updated_at": utc_now()})
                await self.append_event(
                    run_id, event_kind="RUN_CANCELLED",
                    idempotency_key=f"cancel:{run_id}:{committed['version']}",
                    safe_payload={"reason": reason[:200]}, actor_id=actor_id)
                await self._cancel_children(committed, actor_id=actor_id, reason=reason)
                return {"status": "success", "duplicate": False, **committed}
        return _error("concurrency_conflict", "Run changed concurrently.")

    async def _cancel_children(self, run: dict[str, Any], *, actor_id: str,
                               reason: str) -> None:
        """Recursively converge descendants; terminal completed children remain proof."""
        children = await self.store.list(
            "workflow_runs", filters={"parent_run_id": run["run_id"]}, limit=1000)
        for child in children:
            if child.get("runtime_status") == RuntimeStatus.COMPLETED.value:
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
