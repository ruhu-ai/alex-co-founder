"""Versioned code-owned capability manifest for consequence enforcement."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityDescriptor:
    capability_id: str
    semantic_version: str
    implementation_binding: str
    input_schema_id: str
    output_schema_id: str
    error_schema_id: str
    allowed_agent_roles: frozenset[str]
    required_permissions: frozenset[str]
    side_effect_class: str
    approval_policy_id: str
    idempotency_contract: str
    retry_contract: str
    timeout_contract: str
    reconciliation_contract: str
    lifecycle: str
    reviewing_owner: str
    decision_reference: str
    evidence_reference: str
    completion_contract_id: str
    budget_contract_id: str
    observability_contract_id: str
    eval_suite_id: str
    provenance_contract_id: str


def _effect(action_kind: str, connector_id: str, *, approval: str,
            reconciliation: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=f"external.{action_kind}", semantic_version="1.0.0",
        implementation_binding=f"connector:{connector_id}",
        input_schema_id=f"external.{action_kind}.input.v1",
        output_schema_id="external.action.receipt.v1",
        error_schema_id="model.error.v1",
        allowed_agent_roles=frozenset({"deterministic_worker"}),
        required_permissions=frozenset({f"connector.{connector_id}.execute"}),
        side_effect_class="IRREVERSIBLE_EXTERNAL",
        approval_policy_id=approval,
        idempotency_contract="stable_key_and_prepared_receipt.v1",
        retry_contract="no_retry_after_provider_start.v1",
        timeout_contract="provider_timeout_to_uncertain.v1",
        reconciliation_contract=reconciliation,
        lifecycle="ACTIVE", reviewing_owner="platform-security",
        decision_reference="docs/34#8",
        evidence_reference="tests/unit/test_phase0_stabilization.py",
        completion_contract_id="provider_receipt.v1",
        budget_contract_id="provider_calls.v1",
        observability_contract_id="consequence_trace.v1",
        eval_suite_id="consequence-kill-points.v1",
        provenance_contract_id="approval_action_lineage.v1",
    )


def _internal(capability_id: str, role: str, side_effect_class: str,
              *, output: str, implementation: str) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id, semantic_version="1.0.0",
        implementation_binding=implementation,
        input_schema_id=f"{capability_id}.input.v1",
        output_schema_id=output, error_schema_id="model.error.v1",
        allowed_agent_roles=frozenset({role}), required_permissions=frozenset(),
        side_effect_class=side_effect_class, approval_policy_id="none.v1",
        idempotency_contract="stable_input_hash.v1",
        retry_contract="bounded_transient_3.v1",
        timeout_contract="bounded_step_timeout.v1",
        reconciliation_contract="durable_output_receipt.v1",
        lifecycle="ACTIVE", reviewing_owner="platform-runtime",
        decision_reference="docs/34#phase-6",
        evidence_reference="tests/unit/test_investor_outreach_service.py",
        completion_contract_id="durable_output.v1",
        budget_contract_id="workflow_step.v1",
        observability_contract_id="workflow_attempt_trace.v1",
        eval_suite_id=f"{capability_id}.v1",
        provenance_contract_id="run_step_evidence.v1")


STATIC_CAPABILITIES: dict[str, CapabilityDescriptor] = {
    "investor.search": _internal(
        "investor.search", "researcher", "READ_ONLY",
        output="investor.candidate_set.v1",
        implementation="service:investor_outreach.search"),
    "investor.rank": _internal(
        "investor.rank", "analyst", "NO_EFFECT",
        output="investor.ranked_set.v1",
        implementation="service:investor_outreach.rank"),
    "outreach.draft": _internal(
        "outreach.draft", "writer", "INTERNAL_REVERSIBLE",
        output="outreach.recipient_bound_drafts.v1",
        implementation="service:investor_outreach.draft"),
    "reply.correlate": _internal(
        "reply.correlate", "deterministic_worker", "NO_EFFECT",
        output="outreach.correlated_reply.v1",
        implementation="service:investor_outreach.reply"),
    "meeting_brief.compose": _internal(
        "meeting_brief.compose", "writer", "INTERNAL_REVERSIBLE",
        output="outreach.meeting_brief.v1",
        implementation="service:investor_outreach.meeting_brief"),
    "opportunity.search": _internal(
        "opportunity.search", "researcher", "READ_ONLY",
        output="opportunity.candidates.v1", implementation="service:discovery.search"),
    "opportunity.score": _internal(
        "opportunity.score", "analyst", "NO_EFFECT",
        output="opportunity.ranking.v1", implementation="service:discovery.score"),
    "grant.interview": _internal(
        "grant.interview", "interviewer", "INTERNAL_REVERSIBLE",
        output="grant.confirmed_answers.v1", implementation="service:profile.answers"),
    "grant.draft": _internal(
        "grant.draft", "writer", "INTERNAL_REVERSIBLE",
        output="grant.draft.v1", implementation="service:drafting.grant"),
    "browser.fill": _internal(
        "browser.fill", "deterministic_worker", "INTERNAL_REVERSIBLE",
        output="browser.fill_receipt.v1", implementation="service:browser.fill"),
    "workflow.wait": _internal(
        "workflow.wait", "deterministic_worker", "NO_EFFECT",
        output="workflow.wait.v1", implementation="service:runtime.wait"),
    "workflow.approval": _internal(
        "workflow.approval", "deterministic_worker", "NO_EFFECT",
        output="approval.decision.v2", implementation="service:approval.request"),
    "workflow.receipt": _internal(
        "workflow.receipt", "deterministic_worker", "NO_EFFECT",
        output="workflow.receipt.v1", implementation="service:runtime.receipt"),
    "hiring.synthetic_step": _internal(
        "hiring.synthetic_step", "deterministic_worker", "INTERNAL_REVERSIBLE",
        output="hiring.synthetic_output.v1", implementation="service:hiring.synthetic"),
}


EXTERNAL_ACTION_CAPABILITIES: dict[str, CapabilityDescriptor] = {
    "submit_application": _effect(
        "submit_application", "browser", approval="exact_human_approval.v1",
        reconciliation="portal_confirmation_or_operator.v1"),
    "create_portal_account": _effect(
        "create_portal_account", "browser", approval="exact_human_approval.v1",
        reconciliation="portal_account_lookup_or_operator.v1"),
    "send_email": _effect(
        "send_email", "alex_mail", approval="exact_human_approval.v1",
        reconciliation="gmail_rfc822_message_id.v1"),
    "create_calendar_event": _effect(
        "create_calendar_event", "calendar", approval="exact_human_approval.v1",
        reconciliation="calendar_event_id.v1"),
    "export_drive_file": _effect(
        "export_drive_file", "drive", approval="signed_in_human_click.v1",
        reconciliation="drive_source_checksum.v1"),
}


CONTROLLED_ACTION_CAPABILITIES: dict[str, CapabilityDescriptor] = {
    kind: _effect(
        kind, binding, approval="exact_human_approval.v1",
        reconciliation=reconciliation)
    for kind, binding, reconciliation in (
        ("H4S_SEND_EMAIL", "h4s_google", "gmail_rfc822_message_id.v1"),
        ("H4S_CREATE_CALENDAR_EVENT", "h4s_google", "calendar_event_id.v1"),
        ("H4S_UPDATE_CALENDAR_EVENT", "h4s_google", "calendar_event_id.v1"),
        ("H4S_CANCEL_CALENDAR_EVENT", "h4s_google", "calendar_event_id.v1"),
        ("INTERNAL_DEMO_SEND_RECAP", "internal_demo_google",
         "gmail_rfc822_message_id.v1"),
        ("INTERNAL_DEMO_CREATE_CALENDAR_EVENT", "internal_demo_google",
         "calendar_event_id.v1"),
        ("INTERNAL_DEMO_UPDATE_CALENDAR_EVENT", "internal_demo_google",
         "calendar_event_id.v1"),
        ("INTERNAL_DEMO_CANCEL_CALENDAR_EVENT", "internal_demo_google",
         "calendar_event_id.v1"),
    )
}


def require_external_action(action_kind: str,
                            connector_id: str) -> CapabilityDescriptor:
    descriptor = EXTERNAL_ACTION_CAPABILITIES.get(action_kind)
    if (descriptor is None or descriptor.lifecycle != "ACTIVE"
            or descriptor.implementation_binding != f"connector:{connector_id}"):
        raise ValueError("external capability is not enabled for this binding")
    return descriptor


def require_controlled_action(action_kind: str,
                              implementation: str) -> CapabilityDescriptor:
    descriptor = CONTROLLED_ACTION_CAPABILITIES.get(action_kind)
    if (descriptor is None or descriptor.lifecycle != "ACTIVE"
            or descriptor.implementation_binding != f"connector:{implementation}"):
        raise ValueError("controlled capability is not enabled for this binding")
    return descriptor


def require_static(capability_id: str) -> CapabilityDescriptor:
    descriptor = STATIC_CAPABILITIES.get(capability_id)
    if descriptor is None or descriptor.lifecycle != "ACTIVE":
        raise ValueError("static capability is not enabled")
    if (capability_id.startswith(("investor.", "outreach.", "reply.",
                                  "meeting_brief."))
            and os.environ.get("INVESTOR_OUTREACH_ENABLED", "1") != "1"):
        raise ValueError("investor outreach is disabled")
    return descriptor


def require_capability(capability_id: str) -> CapabilityDescriptor:
    """Resolve one reviewed descriptor; callers never supply bindings or paths."""
    descriptor = STATIC_CAPABILITIES.get(capability_id)
    if descriptor is None:
        descriptor = next((item for item in (
            *EXTERNAL_ACTION_CAPABILITIES.values(),
            *CONTROLLED_ACTION_CAPABILITIES.values())
            if item.capability_id == capability_id), None)
    if descriptor is None or descriptor.lifecycle not in {"ACTIVE", "DEPRECATED"}:
        raise ValueError("capability is not available")
    return descriptor


def descriptors_for_role(role: str, allowed_ids: set[str]) -> tuple[CapabilityDescriptor, ...]:
    """Return only the intersection of step allowance and agent role."""
    out = []
    for capability_id in sorted(allowed_ids):
        descriptor = require_capability(capability_id)
        if role in descriptor.allowed_agent_roles:
            out.append(descriptor)
    return tuple(out)


def validate_manifest() -> None:
    """Fail CI/import checks on placeholders or duplicate stable identities."""
    identities: set[tuple[str, str]] = set()
    for descriptor in (*STATIC_CAPABILITIES.values(),
                       *EXTERNAL_ACTION_CAPABILITIES.values(),
                       *CONTROLLED_ACTION_CAPABILITIES.values()):
        identity = (descriptor.capability_id, descriptor.semantic_version)
        if identity in identities:
            raise ValueError("duplicate capability identity")
        identities.add(identity)
        for value in descriptor.__dict__.values():
            if value is None or value == "" or value == "TODO":
                raise ValueError("capability descriptor contains placeholder")


validate_manifest()
