"""Durable capability lifecycle controls and dependent-run fencing."""

from __future__ import annotations

from typing import Any

from services import capability_registry
from services.durable_store import DurableStore, production_store
from services.workflow_contracts import stable_id, utc_now


def state_id(capability_id: str, version: str) -> str:
    return stable_id("capstate", capability_id, version)


class CapabilityGovernanceService:
    def __init__(self, store: DurableStore | None = None):
        self.store = store or production_store()

    async def lifecycle(self, capability_id: str, version: str) -> str:
        row = await self.store.get(
            "capability_states", state_id(capability_id, version))
        return str((row or {}).get("lifecycle") or
                   capability_registry.require_capability(capability_id).lifecycle)

    async def set_lifecycle(self, *, capability_id: str, version: str,
                            lifecycle: str, actor_id: str,
                            reason: str) -> dict[str, Any]:
        descriptor = capability_registry.require_capability(capability_id)
        if (version != descriptor.semantic_version
                or lifecycle not in {"ACTIVE", "DEPRECATED", "DISABLED"}
                or not actor_id or not reason):
            return {"status": "error", "error": True,
                    "error_code": "capability_lifecycle_invalid"}
        identifier = state_id(capability_id, version)
        existing = await self.store.get("capability_states", identifier)
        now = utc_now()
        row = ({"schema_version": 1, "capability_state_id": identifier,
                "capability_id": capability_id, "capability_version": version,
                "lifecycle": lifecycle, "changed_by_actor_id": actor_id,
                "reason": reason[:240], "created_at": now,
                "updated_at": now, "version": 1})
        if existing:
            row = await self.store.compare_and_set(
                "capability_states", identifier, int(existing["version"]), {
                    "lifecycle": lifecycle, "changed_by_actor_id": actor_id,
                    "reason": reason[:240], "updated_at": now})
        else:
            await self.store.create("capability_states", identifier, row)
        paused = []
        if lifecycle == "DISABLED":
            steps = await self.store.list(
                "workflow_steps", filters={"capability_id": capability_id},
                limit=1000)
            for step in steps:
                if step.get("status") not in {"READY", "PENDING"}:
                    continue
                run = await self.store.get("workflow_runs", step["run_id"])
                if not run or run.get("runtime_status") in {
                        "CANCELLED", "SUCCEEDED", "FAILED", "REJECTED"}:
                    continue
                changed = await self.store.compare_and_set(
                    "workflow_runs", run["run_id"], int(run["version"]), {
                        "runtime_status": "PAUSED",
                        "pause_reason": f"capability_disabled:{capability_id}",
                        "updated_at": now})
                if changed:
                    paused.append(run["run_id"])
                    inbox_id = stable_id(
                        "inbox", run["workspace_id"], run["run_id"], capability_id)
                    await self.store.create("founder_inbox", inbox_id, {
                        "schema_version": 1, "inbox_item_id": inbox_id,
                        "workspace_id": run["workspace_id"],
                        "founder_id": run["workspace_id"],
                        "item_kind": "CAPABILITY_DISABLED",
                        "title": "Workflow paused by a capability change",
                        "summary": f"{capability_id} was disabled before execution.",
                        "run_id": run["run_id"], "status": "UNREAD",
                        "created_at": now, "updated_at": now, "version": 1})
        return {"status": "success", "state": row, "paused_run_ids": paused}
