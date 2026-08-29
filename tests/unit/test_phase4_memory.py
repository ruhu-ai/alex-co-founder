from __future__ import annotations

import asyncio

import pytest

from services import data_lifecycle
from services.deletion_service import DeletionService
from services.durable_store import InMemoryDurableStore
from services.memory_evaluation import evaluate
from services.persistent_memory import FirestorePersistentMemoryAdapter
from services.profile_fact_service import ProfileFactService

pytestmark = pytest.mark.asyncio


async def test_profile_facts_are_immutable_untruncated_and_independently_contended():
    store = InMemoryDurableStore()
    service = ProfileFactService(store)
    previous = None
    for version in range(250):
        result = await service.put(
            workspace_id="workspace-a", actor_id="actor-a",
            scope="WORKSPACE_BUSINESS", key="traction",
            value={"customers": version},
            verification_status="FOUNDER_CONFIRMED",
            source_refs=[f"answer:{version}"],
            idempotency_key=f"traction-{version}")
        assert result["fact"]["supersedes_fact_id"] == previous
        previous = result["fact"]["fact_id"]
    assert len(await store.list(
        "profile_facts", filters={"workspace_id": "workspace-a"}, limit=1000)) == 250

    results = await asyncio.gather(*(service.put(
        workspace_id="workspace-a", actor_id="actor-a",
        scope="WORKSPACE_BUSINESS", key=f"independent-{index}", value=index,
        verification_status="EVIDENCE_VERIFIED", source_refs=["artifact:a"],
        idempotency_key=f"independent-{index}") for index in range(30)))
    assert all(not result.get("error") for result in results)


async def test_actor_preferences_are_private_and_workspace_facts_are_shared():
    store = InMemoryDurableStore()
    service = ProfileFactService(store)
    await service.put(
        workspace_id="workspace-a", actor_id="actor-a",
        scope="ACTOR_PREFERENCE", key="writing_voice", value="direct",
        verification_status="FOUNDER_CONFIRMED", source_refs=["feedback:1"],
        idempotency_key="voice-1")
    await service.put(
        workspace_id="workspace-a", actor_id="actor-a",
        scope="WORKSPACE_BUSINESS", key="company_name", value="Ruhu",
        verification_status="FOUNDER_CONFIRMED", source_refs=["answer:1"],
        idempotency_key="company-1")

    other = await service.current(
        workspace_id="workspace-a", actor_id="actor-b",
        include_actor_private=True)
    assert other["facts"] == {"company_name": "Ruhu"}
    owner = await service.current(
        workspace_id="workspace-a", actor_id="actor-a",
        include_actor_private=True)
    assert owner["facts"]["writing_voice"] == "direct"


async def test_memory_filters_before_rank_labels_untrusted_and_deletes_by_source():
    store = InMemoryDurableStore()
    memory = FirestorePersistentMemoryAdapter(store)
    item = {
        "workspace_id": "workspace-a", "actor_id": "actor-a",
        "source_ref": "email:reply-1", "summary": "Investor prefers seed rounds",
        "purpose": "outreach", "scope": "WORKSPACE", "sensitivity": "INTERNAL",
        "retention_class": "STANDARD", "source_trust": "UNTRUSTED_EXTERNAL",
    }
    assert (await memory.put(item, "reply-1"))["status"] == "success"
    await memory.put({**item, "workspace_id": "workspace-b",
                      "source_ref": "email:foreign"}, "foreign")

    found = await memory.search(
        "seed investor", workspace_id="workspace-a", actor_id="actor-a",
        allowed_scopes={"WORKSPACE"}, sensitivity_ceiling="INTERNAL",
        purpose="meeting_brief", limit=10)
    assert len(found["hits"]) == 1
    assert found["hits"][0]["summary"].startswith("<untrusted-memory>")
    deleted = await memory.delete_by_source(
        "email:reply-1", workspace_id="workspace-a", deletion_job_id="delete-1")
    assert deleted["deleted_count"] == 1
    after = await memory.search(
        "seed investor", workspace_id="workspace-a", actor_id="actor-a",
        allowed_scopes={"WORKSPACE"}, sensitivity_ceiling="INTERNAL",
        purpose="meeting_brief", limit=10)
    assert after["hits"] == []
    unavailable = FirestorePersistentMemoryAdapter(store, available=False)
    failed = await unavailable.search(
        "anything", workspace_id="workspace-a", actor_id="actor-a",
        allowed_scopes={"WORKSPACE"}, sensitivity_ceiling="INTERNAL",
        purpose="test", limit=1)
    assert failed["error_code"] == "memory_backend_unavailable"
    assert failed["degraded"] is True


async def test_deletion_job_preserves_manifest_and_reports_partial_failure(monkeypatch):
    store = InMemoryDurableStore()
    memory = FirestorePersistentMemoryAdapter(store)

    async def lifecycle(_workspace_id, *, execute=False,
                        expected_inventory_hash=""):
        if not execute:
            return {"status": "success", "inventory_hash": "inventory-1",
                    "delete_count": 4}
        assert expected_inventory_hash == "inventory-1"
        return {"status": "success", "deleted": 4, "blobs_deleted": 2}

    monkeypatch.setattr(data_lifecycle, "delete_founder_data", lifecycle)
    service = DeletionService(store, memory=memory)
    planned = await service.plan_workspace(
        workspace_id="workspace-a", requested_by_actor_id="actor-a",
        client_request_id="delete-workspace-1")
    result = await service.execute(
        workspace_id="workspace-a",
        deletion_job_id=planned["job"]["deletion_job_id"],
        expected_inventory_hash="inventory-1")
    assert result["job"]["status"] == "COMPLETED"
    assert result["job"]["completion_manifest"]["blobs_deleted"] == 2
    assert await service.tombstone_active("workspace-a") is True


async def test_memory_release_gate_requires_usefulness_and_zero_leak_conflict_deletion():
    rows = []
    for scenario in (
            "useful_recall", "cross_workspace_probe",
            "authority_conflict_probe", "deleted_source_probe",
            "backend_failure_probe", "hiring_probe",
            "restore_after_forget_probe", "automatic_write_probe",
            "transcript_import_probe", "managed_backend_probe",
            "private_session_probe", "membership_growth_probe",
            "memory_hit_disclosure", "no_hit_disclosure"):
        rows.append({
            "scenario": scenario,
            "useful": scenario == "useful_recall",
            "backend_failure_explicit": scenario == "backend_failure_probe",
            "disclosure_shown": scenario == "memory_hit_disclosure",
        })
    passing = evaluate(rows)
    assert passing["passed"] is True
    leaking_rows = [dict(row) for row in rows]
    next(row for row in leaking_rows
         if row["scenario"] == "cross_workspace_probe")[
             "cross_workspace_hit"] = True
    leaking = evaluate(leaking_rows)
    assert leaking["passed"] is False

    incomplete = evaluate([{"scenario": "useful_recall", "useful": True}])
    assert incomplete["error_code"] == "memory_eval_coverage_incomplete"
