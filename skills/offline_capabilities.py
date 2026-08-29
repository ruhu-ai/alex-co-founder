"""Closed capability descriptors for offline Gate F compilation only.

These descriptors are deliberately absent from ``services.capability_registry``
and therefore cannot be resolved by the live app, worker, or agent graph. Their
``DRAFT`` lifecycle also fails the runtime eligibility predicate.
"""

from __future__ import annotations

from services.capability_registry import CapabilityDescriptor


def _draft_capability(
    capability_id: str,
    *,
    side_effect_class: str,
    output_schema_id: str,
    implementation_binding: str,
    required_permission: str,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id,
        semantic_version="1.0.0",
        implementation_binding=implementation_binding,
        input_schema_id=f"{capability_id}.input.v1",
        output_schema_id=output_schema_id,
        error_schema_id="model.error.v1",
        allowed_agent_roles=frozenset({"deterministic_worker"}),
        required_permissions=frozenset({required_permission}),
        side_effect_class=side_effect_class,
        approval_policy_id="none.v1",
        idempotency_contract="idempotency.skill-invocation.v1",
        retry_contract="retry.gate-f-offline.v1",
        timeout_contract="bounded_step_timeout.v1",
        reconciliation_contract="offline.no-runtime-reconciliation.v1",
        lifecycle="DRAFT",
        reviewing_owner="platform-security",
        decision_reference="docs/background-work-gate-f-offline-qualification.md",
        evidence_reference="tests/unit/test_spec40_gate_f_offline.py",
        completion_contract_id="documents.grounded-artifact-draft.v1",
        budget_contract_id="budget.gate-f.available",
        observability_contract_id="offline.content-free-trace.v1",
        eval_suite_id="skill.gate-f-offline.v1",
        provenance_contract_id="evidence.artifact-chunk-citation.v1",
    )


OFFLINE_DRAFT_CAPABILITIES: dict[str, CapabilityDescriptor] = {
    "background.artifact.read_selected_evidence": _draft_capability(
        "background.artifact.read_selected_evidence",
        side_effect_class="READ_ONLY",
        output_schema_id="evidence.selected-artifact-context.v1",
        implementation_binding="offline-contract:selected-artifact-context",
        required_permission="artifact.read_selected",
    ),
    "documents.persist_internal_draft": _draft_capability(
        "documents.persist_internal_draft",
        side_effect_class="INTERNAL_REVERSIBLE",
        output_schema_id="skill.documents.grounded-artifact.output.v1",
        implementation_binding="offline-contract:internal-draft-persistence",
        required_permission="artifact.write_private_draft",
    ),
}
