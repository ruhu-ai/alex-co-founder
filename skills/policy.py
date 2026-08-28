"""Deterministic narrowing predicate for shadow skill evaluation.

The live capability registry and existing server guards remain authoritative.
This module can only remove descriptors from a caller-supplied base set.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.capability_registry import CapabilityDescriptor
from skills.models import CompiledSkill, EffectClass

EFFECT_ALIAS_MAP = {"IRREVERSIBLE_EXTERNAL": EffectClass.EXTERNAL_CONSEQUENTIAL}
EFFECT_ORDER = {
    EffectClass.NO_EFFECT: 0,
    EffectClass.READ_ONLY: 1,
    EffectClass.INTERNAL_REVERSIBLE: 2,
    EffectClass.EXTERNAL_REVERSIBLE: 3,
    EffectClass.EXTERNAL_CONSEQUENTIAL: 4,
}

# Doc 37 proposal. This binding is deliberately complete only for Pilot-0.
# Missing entries fail closed and cannot be inferred from an effect class.
CAPABILITY_AUTHORITY_IMPACTS: dict[str, frozenset[str]] = {
    "opportunity.search": frozenset({"ADVISORY"}),
    "grant.draft": frozenset({"DRAFT_PREPARATION"}),
}


@dataclass(frozen=True)
class EligibilityContext:
    principal_id: str
    workspace_id: str
    run_id: str
    session_id: str
    authorized_workspace_id: str
    authorized_run_ids: frozenset[str]
    agent_role: str
    workflow_step: str
    principal_permissions: frozenset[str]
    base_capability_ids: frozenset[str]
    step_capability_ids: frozenset[str]
    workspace_capability_ids: frozenset[str]
    enabled_feature_flags: frozenset[str]
    connector_bindings: frozenset[str]
    source_grants: frozenset[str]
    accepted_data_classes: frozenset[str]
    residency_allowed: bool
    model_qualified: bool
    scope_current: bool
    budgets_available: bool
    satisfied_preconditions: frozenset[str]


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    failed_clauses: tuple[str, ...]


def canonical_effect(value: str) -> EffectClass | None:
    if value in EFFECT_ALIAS_MAP:
        return EFFECT_ALIAS_MAP[value]
    try:
        return EffectClass(value)
    except ValueError:
        return None


def evaluate_capability(
    skill: CompiledSkill,
    descriptor: CapabilityDescriptor,
    context: EligibilityContext,
) -> EligibilityDecision:
    """Evaluate every doc37 §4.2 clause; missing facts are denials."""

    manifest = skill.manifest
    failures: list[str] = []
    if not context.principal_id:
        failures.append("principal_identity")
    if (not context.workspace_id
            or context.workspace_id != context.authorized_workspace_id):
        failures.append("workspace_scope")
    if context.run_id and context.run_id not in context.authorized_run_ids:
        failures.append("run_scope")
    pins = {(pin.capability_id, pin.version) for pin in manifest.capabilities.allow}
    if (descriptor.capability_id, descriptor.semantic_version) not in pins:
        failures.append("manifest_pin")
    if descriptor.capability_id not in context.base_capability_ids:
        failures.append("base_agent_ceiling")
    if descriptor.lifecycle not in {"ACTIVE", "CANARY", "QUALIFIED"}:
        failures.append("capability_lifecycle")
    if context.agent_role not in descriptor.allowed_agent_roles:
        failures.append("agent_role")
    if (context.workflow_step not in manifest.assignments.workflow_steps
            or descriptor.capability_id not in context.step_capability_ids):
        failures.append("step_policy")
    required = set(descriptor.required_permissions) | set(manifest.capabilities.required_permissions)
    if not required.issubset(context.principal_permissions):
        failures.append("principal_permissions")
    if descriptor.capability_id not in context.workspace_capability_ids:
        failures.append("workspace_policy")
    if not set(manifest.policy.required_feature_flags).issubset(context.enabled_feature_flags):
        failures.append("feature_flags")
    binding = descriptor.implementation_binding
    if (binding.startswith("connector:") and binding not in context.connector_bindings):
        failures.append("connector_grant")
    if binding.startswith("source:") and binding not in context.source_grants:
        failures.append("source_grant")
    if not set(manifest.policy.accepted_data_classes).issubset(context.accepted_data_classes):
        failures.append("data_class")
    if not context.residency_allowed:
        failures.append("residency")
    if not context.model_qualified:
        failures.append("model_qualification")
    if not context.scope_current:
        failures.append("scope")
    effect = canonical_effect(descriptor.side_effect_class)
    ceiling = manifest.capabilities.effect_policy.ceiling
    if (effect is None or effect in {EffectClass.FINANCIAL_OR_LEGAL, EffectClass.PROHIBITED}
            or ceiling not in EFFECT_ORDER or EFFECT_ORDER[effect] > EFFECT_ORDER[ceiling]):
        failures.append("effect_ceiling")
    impacts = CAPABILITY_AUTHORITY_IMPACTS.get(descriptor.capability_id)
    if impacts is None or not impacts.issubset(
            set(manifest.capabilities.effect_policy.authority_impacts)):
        failures.append("authority_impact")
    if not context.budgets_available:
        failures.append("budgets")
    if not set(manifest.policy.precondition_ids).issubset(context.satisfied_preconditions):
        failures.append("preconditions")
    return EligibilityDecision(not failures, tuple(failures))


def effective_capabilities(
    skill: CompiledSkill,
    descriptors: dict[str, CapabilityDescriptor],
    context: EligibilityContext,
) -> tuple[CapabilityDescriptor, ...]:
    """Return a strict subset of the caller's already-registered base tools."""

    result = []
    for capability_id in sorted(context.base_capability_ids):
        descriptor = descriptors.get(capability_id)
        if descriptor and evaluate_capability(skill, descriptor, context).eligible:
            result.append(descriptor)
    return tuple(result)
