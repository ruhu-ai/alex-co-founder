from __future__ import annotations

from dataclasses import replace

import pytest

from services import capability_registry
from services.capability_governance import CapabilityGovernanceService
from services.durable_store import InMemoryDurableStore
from services.workflow_contracts import PLAN_TEMPLATES
from services.workflow_plan_validator import (
    STEP_CAPABILITIES,
    validate_plan,
    validate_replan,
)
from services.workflow_runtime import WorkflowRuntime


def _plan(kind="investor_outreach:v1"):
    return {"schema_version": 1, "workflow_kind": kind,
            "workflow_definition_version": "1",
            "steps": list(PLAN_TEMPLATES[kind])}


def test_all_reviewed_templates_resolve_every_step_to_a_descriptor():
    for workflow_kind, steps in PLAN_TEMPLATES.items():
        result = validate_plan(_plan(workflow_kind))
        assert result["status"] == "success", (workflow_kind, result)
        assert set(result["capabilities"]) == set(steps)


def test_unknown_fields_loops_budgets_and_disabled_capabilities_fail_closed(
        monkeypatch):
    unknown = {**_plan(), "model_sql": "DROP TABLE"}
    assert validate_plan(unknown)["error_code"] == "plan_unknown_field"
    loop = _plan()
    loop["steps"].append("research")
    assert validate_plan(loop)["error_code"] == "plan_template_invalid"
    assert validate_plan(_plan(), budgets={"max_steps": 101})[
        "error_code"] == "plan_budget_invalid"

    original = capability_registry.STATIC_CAPABILITIES["investor.search"]
    monkeypatch.setitem(
        capability_registry.STATIC_CAPABILITIES, "investor.search",
        replace(original, lifecycle="DISABLED"))
    assert validate_plan(_plan())["error_code"] == "plan_capability_disabled"


def test_role_context_gets_only_step_allowed_capabilities():
    allowed = set(STEP_CAPABILITIES["investor_outreach:v1"].values())
    writer = capability_registry.descriptors_for_role("writer", allowed)
    assert {item.capability_id for item in writer} == {
        "outreach.draft", "meeting_brief.compose"}
    assert all("connector" not in item.required_permissions for item in writer)


def test_reviewed_replan_preserves_effects_and_reapproves_changed_subject():
    result = validate_replan(
        workflow_kind="investor_outreach:v1",
        old_steps=list(PLAN_TEMPLATES["investor_outreach:v1"]),
        completed_steps={"research", "rank", "draft"},
        variant="default", changed_effect_subjects={"send"})
    assert result["requires_reapproval"] is True
    assert not ({"research", "rank", "draft"} & set(result["invalidate_steps"]))
    refused = validate_replan(
        workflow_kind="investor_outreach:v1",
        old_steps=list(PLAN_TEMPLATES["investor_outreach:v1"]),
        completed_steps={"send"}, variant="draft_only",
        changed_effect_subjects=set())
    assert refused["error_code"] == "replan_completed_step_removed"


@pytest.mark.asyncio
async def test_disabling_capability_pauses_unexecuted_dependent_runs():
    store = InMemoryDurableStore()
    runtime = WorkflowRuntime(store)
    run = await runtime.create_run(
        workspace_id="workspace-a", journey_id="journey-a",
        run_kind="INVESTOR_OUTREACH", workflow_kind="investor_outreach:v1",
        idempotency_key="run-a", domain_ref="outreach-a")
    await runtime.create_step(
        run["run_id"], step_key="draft", idempotency_key="draft-a")
    disabled = await CapabilityGovernanceService(store).set_lifecycle(
        capability_id="outreach.draft", version="1.0.0",
        lifecycle="DISABLED", actor_id="operator-a", reason="security review")
    assert disabled["paused_run_ids"] == [run["run_id"]]
    assert (await store.get("workflow_runs", run["run_id"]))[
        "runtime_status"] == "PAUSED"
    blocked = await runtime.create_run(
        workspace_id="workspace-a", journey_id="journey-b",
        run_kind="INVESTOR_OUTREACH", workflow_kind="investor_outreach:v1",
        idempotency_key="run-b", domain_ref="outreach-b")
    assert blocked["error_code"] == "plan_capability_disabled"
