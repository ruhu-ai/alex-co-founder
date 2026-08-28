from __future__ import annotations

import pytest

from services.durable_store import InMemoryDurableStore
from services.workflow_projection_service import WorkflowProjectionService

pytestmark = pytest.mark.asyncio


async def test_grants_discovery_and_hiring_share_control_contract_without_leakage():
    store = InMemoryDurableStore()
    projections = WorkflowProjectionService(store)
    discovery = await projections.ensure_discovery(
        workspace_id="workspace_a", discovery_request_id="discovery_a",
        originating_actor_id="actor_a")
    grant_a = await projections.ensure_grant_application(
        workspace_id="workspace_a", application_id="application_a",
        originating_actor_id="actor_a")
    grant_b = await projections.ensure_grant_application(
        workspace_id="workspace_a", application_id="application_b",
        originating_actor_id="actor_a")

    assert {discovery["workflow_kind"], grant_a["workflow_kind"]} == {
        "opportunity_discovery:v1", "grant_application:v1"}
    assert grant_a["run_id"] != grant_b["run_id"]
    await projections.runtime.create_wait(
        grant_a["run_id"], wait_kind="FOUNDER_APPROVAL",
        correlation_key="approval_a")
    run_b_waits = await store.list(
        "waits", filters={"run_id": grant_b["run_id"]}, limit=10)
    assert run_b_waits == []
    assert (await projections.runtime.verify_projection(
        discovery["run_id"]))["status"] == "success"
