"""Deterministic validation for code-reviewed workflow templates (Phase 5)."""

from __future__ import annotations

import re
from typing import Any

from services import capability_registry
from services.canonical import canonical_hash
from services.workflow_contracts import PLAN_TEMPLATES, WORKFLOW_DEFINITIONS

STEP_CAPABILITIES: dict[str, dict[str, str]] = {
    "alex_background_job:v1": {
        "analyze_artifact": "background.artifact.inspect"},
    "alex_background_artifact_draft:v1": {
        "produce_grounded_artifact": "documents.persist_internal_draft"},
    "opportunity_discovery:v1": {
        "discover": "opportunity.search", "score": "opportunity.score",
        "publish_receipt": "workflow.receipt"},
    "grant_application:v1": {
        "interview": "grant.interview", "draft": "grant.draft",
        "fill": "browser.fill", "human_approval": "workflow.approval",
        "submit": "external.submit_application", "wait": "workflow.wait"},
    "investor_outreach:v1": {
        "research": "investor.search", "rank": "investor.rank",
        "draft": "outreach.draft", "human_approval": "workflow.approval",
        "send": "external.send_email", "wait_reply": "workflow.wait",
        "meeting_brief": "meeting_brief.compose"},
    "external_consequence:v1": {
        "human_approval": "workflow.approval", "execute": "workflow.receipt",
        "reconcile": "workflow.receipt"},
    "hiring_role:v1": {step: "hiring.synthetic_step" for step in
                         PLAN_TEMPLATES["hiring_role:v1"]},
    "hiring_candidate:v1": {step: "hiring.synthetic_step" for step in
                              PLAN_TEMPLATES["hiring_candidate:v1"]},
    "hiring_onboarding:v1": {step: "hiring.synthetic_step" for step in
                               PLAN_TEMPLATES["hiring_onboarding:v1"]},
}

# A reviewed plan may contain one parameterized control step where the concrete
# work item is deliberately bound later by an exact approval.  This is needed
# for standalone consequences: one immutable plan can safely carry distinct
# approval records for distinct, hash-bound actions without pretending that a
# model invented a new plan node.  Keep this allowlist deliberately tiny; all
# other step keys must exactly match the reviewed template.
PARAMETERIZED_STEP_CAPABILITIES: dict[str, frozenset[str]] = {
    "external_consequence:v1": frozenset({"human_approval"}),
}
_STEP_INSTANCE_SUFFIX = re.compile(
    r"^[a-z][a-z0-9_]{0,63}(?::[a-z0-9][a-z0-9_.-]{0,63}){0,2}$")


def capability_for_step(workflow_kind: str, step_key: str) -> str | None:
    """Resolve an exact reviewed step, or a narrowly registered instance.

    Parameterized keys are only accepted for a named static plan node and use
    a bounded identifier grammar.  The suffix distinguishes separately
    approval-bound actions; it never grants a new capability or expands a
    plan template.
    """
    capabilities = STEP_CAPABILITIES.get(workflow_kind, {})
    exact = capabilities.get(step_key)
    if exact:
        return exact
    base, separator, suffix = step_key.partition(":")
    if (not separator or base not in
            PARAMETERIZED_STEP_CAPABILITIES.get(workflow_kind, frozenset())
            or not _STEP_INSTANCE_SUFFIX.fullmatch(suffix)):
        return None
    return capabilities.get(base)

REVIEWED_VARIANTS: dict[tuple[str, str], tuple[str, ...]] = {
    (workflow_kind, "default"): steps
    for workflow_kind, steps in PLAN_TEMPLATES.items()
}
REVIEWED_VARIANTS[("investor_outreach:v1", "draft_only")] = (
    "research", "rank", "draft")


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message}


def validate_plan(plan: dict[str, Any], *, variant: str = "default",
                  budgets: dict[str, int] | None = None) -> dict[str, Any]:
    allowed_fields = {"schema_version", "workflow_kind",
                      "workflow_definition_version", "steps"}
    if set(plan) - allowed_fields:
        return _error("plan_unknown_field", "Plan contains an unknown field.")
    workflow_kind = str(plan.get("workflow_kind") or "")
    definition = WORKFLOW_DEFINITIONS.get(workflow_kind)
    reviewed = REVIEWED_VARIANTS.get((workflow_kind, variant))
    steps = plan.get("steps")
    if (not definition or reviewed is None or plan.get("schema_version") != 1
            or plan.get("workflow_definition_version") != definition.version
            or not isinstance(steps, list)
            or any(not isinstance(step, str) for step in steps)
            or tuple(steps) != reviewed or len(set(steps)) != len(steps)):
        return _error("plan_template_invalid",
                      "Plan is not an exact reviewed acyclic template.")
    caps = STEP_CAPABILITIES.get(workflow_kind)
    if not caps or set(reviewed) - set(caps):
        return _error("plan_capability_missing", "Plan step is unregistered.")
    descriptors = {}
    try:
        for step in reviewed:
            descriptor = capability_registry.require_capability(caps[step])
            descriptors[step] = descriptor.capability_id
    except ValueError:
        return _error("plan_capability_disabled", "Plan capability is disabled.")
    budget = dict(budgets or {})
    limits = {"max_steps": 100, "max_model_calls": 30,
              "max_provider_calls": 20, "max_tokens": 1_000_000,
              "max_active_seconds": 900, "max_wall_seconds": 86_400,
              "max_artifact_bytes": 100_000_000,
              "max_artifact_chunks": 10_000,
              "max_output_bytes": 10_000_000, "max_retries": 10,
              "max_concurrent": 100}
    if any(key not in limits or not isinstance(value, int) or value < 0
           or value > limits[key] for key, value in budget.items()):
        return _error("plan_budget_invalid", "Plan exceeds its run budget.")
    return {"status": "success", "variant": variant,
            "capabilities": descriptors,
            "plan_hash": canonical_hash(plan, domain="workflow-plan")}


def validate_replan(*, workflow_kind: str, old_steps: list[str],
                    completed_steps: set[str], variant: str,
                    changed_effect_subjects: set[str]) -> dict[str, Any]:
    reviewed = REVIEWED_VARIANTS.get((workflow_kind, variant))
    if reviewed is None:
        return _error("replan_variant_unreviewed", "Variant is not reviewed.")
    if not completed_steps <= set(reviewed):
        return _error("replan_completed_step_removed",
                      "Replanning cannot remove completed work.")
    effect_steps = {step for step, capability in
                    STEP_CAPABILITIES.get(workflow_kind, {}).items()
                    if capability_registry.require_capability(
                        capability).side_effect_class == "IRREVERSIBLE_EXTERNAL"}
    if completed_steps & effect_steps and not completed_steps <= set(old_steps):
        return _error("replan_effect_history_invalid", "Effect history is invalid.")
    return {"status": "success", "steps": list(reviewed),
            "invalidate_steps": sorted(set(old_steps) - completed_steps),
            "requires_reapproval": bool(changed_effect_subjects),
            "changed_effect_subjects": sorted(changed_effect_subjects)}
