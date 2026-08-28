"""Durable, auditable workspace deletion orchestration (docs/34 §7.6)."""

from __future__ import annotations

from typing import Any

from services import data_lifecycle
from services.durable_store import DurableStore, production_store
from services.persistent_memory import PersistentMemoryAdapter, configured_adapter
from services.workflow_contracts import stable_id, utc_now


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


class DeletionService:
    def __init__(self, store: DurableStore | None = None, *,
                 memory: PersistentMemoryAdapter | None = None):
        self.store = store or production_store()
        self.memory = memory if memory is not None else configured_adapter()

    async def plan_workspace(self, *, workspace_id: str,
                             requested_by_actor_id: str,
                             client_request_id: str) -> dict[str, Any]:
        if not workspace_id or not requested_by_actor_id or not client_request_id:
            return _error("deletion_contract_invalid", "Deletion request is invalid.")
        job_id = stable_id("deletionjob", workspace_id, client_request_id)
        existing = await self.store.get("deletion_jobs", job_id)
        if existing:
            return {"status": "success", "duplicate": True, "job": existing}
        inventory = await data_lifecycle.delete_founder_data(workspace_id)
        now = utc_now()
        row = {
            "schema_version": 1, "deletion_job_id": job_id,
            "workspace_id": workspace_id, "subject_scope": "WORKSPACE",
            "requested_by_actor_id": requested_by_actor_id,
            "status": "PLANNED", "writes_denied": True,
            "inventory_hash": inventory["inventory_hash"],
            "enumerated_record_count": inventory["delete_count"],
            "backend_status": {}, "attempt": 0,
            "backup_residual_until": None,
            "created_at": now, "updated_at": now, "completed_at": None,
            "version": 1,
        }
        created = await self.store.create("deletion_jobs", job_id, row)
        return {"status": "success", "duplicate": not created,
                "job": row if created else await self.store.get(
                    "deletion_jobs", job_id)}

    async def execute(self, *, workspace_id: str, deletion_job_id: str,
                      expected_inventory_hash: str) -> dict[str, Any]:
        job = await self.store.get("deletion_jobs", deletion_job_id)
        if (not job or job.get("workspace_id") != workspace_id
                or job.get("inventory_hash") != expected_inventory_hash):
            return _error("deletion_not_found", "Deletion plan is stale or absent.")
        if job.get("status") == "COMPLETED":
            return {"status": "success", "duplicate": True, "job": job}
        running = await self.store.compare_and_set(
            "deletion_jobs", deletion_job_id, int(job["version"]), {
                "status": "DELETING", "attempt": int(job.get("attempt") or 0) + 1,
                "updated_at": utc_now()})
        if not running:
            return _error("concurrency_conflict", "Deletion job changed.")
        memory_result = ({"status": "disabled", "deleted_count": 0}
                         if self.memory is None else
                         await self.memory.delete_workspace(
                             workspace_id, deletion_job_id))
        store_result = await data_lifecycle.delete_founder_data(
            workspace_id, execute=True,
            expected_inventory_hash=expected_inventory_hash)
        degraded = bool(memory_result.get("error") or store_result.get("error"))
        current = await self.store.get("deletion_jobs", deletion_job_id)
        final = await self.store.compare_and_set(
            "deletion_jobs", deletion_job_id, int(current["version"]), {
                "status": "DEGRADED" if degraded else "COMPLETED",
                "backend_status": {"memory": memory_result.get("status"),
                                   "firestore_artifacts": store_result.get("status")},
                "completion_manifest": {
                    "inventory_hash": expected_inventory_hash,
                    "records_deleted": int(store_result.get("deleted") or 0),
                    "blobs_deleted": int(store_result.get("blobs_deleted") or 0),
                    "memory_deleted": int(memory_result.get("deleted_count") or 0),
                    "negative_probe_required": True,
                },
                "completed_at": utc_now() if not degraded else None,
                "updated_at": utc_now()})
        return {"status": "error" if degraded else "success",
                "error": degraded,
                "error_code": "deletion_degraded" if degraded else None,
                "job": final, "memory": memory_result, "store": store_result}

    async def tombstone_active(self, workspace_id: str) -> bool:
        jobs = await self.store.list(
            "deletion_jobs", filters={"workspace_id": workspace_id}, limit=100)
        return any(row.get("writes_denied") and row.get("status") in {
            "PLANNED", "DELETING", "DEGRADED", "COMPLETED"} for row in jobs)
