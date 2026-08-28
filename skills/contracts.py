"""Auditable contract ownership/status map for the offline Gate F package."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractEntry:
    contract_id: str
    owner: str
    status: str
    canonical_reference: str
    acceptance_evidence: str


def _entry(contract_id: str, owner: str, reference: str, evidence: str,
           *, status: str = "DRAFT") -> ContractEntry:
    return ContractEntry(contract_id, owner, status, reference, evidence)


_CONTRACT_ENTRIES = (
    _entry("model.error.v1", "platform-runtime", "services/error_contracts.py",
           "tests/unit/test_spec40_gate_f_offline.py", status="CURRENT"),
    _entry("skill.documents.grounded-artifact.input.v1", "documents-runtime",
           "skills/documents/produce_grounded_artifact/schemas/input.v1.json",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("skill.documents.grounded-artifact.output.v1", "documents-runtime",
           "skills/documents/produce_grounded_artifact/schemas/output.v1.json",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("background.artifact.read_selected_evidence.input.v1",
           "platform-evidence",
           "skills/documents/produce_grounded_artifact/schemas/input.v1.json",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("evidence.selected-artifact-context.v1", "platform-evidence",
           "docs/background-work-gate-f-offline-qualification.md",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("documents.persist_internal_draft.input.v1", "documents-runtime",
           "skills/documents/produce_grounded_artifact/schemas/output.v1.json",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("documents.grounded-artifact-draft.v1", "documents-runtime",
           "docs/background-work-gate-f-offline-qualification.md",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("context.selected-artifact-evidence.v1", "platform-runtime",
           "skills/context.py", "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("evidence.artifact-chunk-citation.v1", "platform-evidence",
           "docs/37-production-skills-system.md#84-evidence-and-citations",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("citations.all-material-claims-required.v1", "platform-evidence",
           "docs/background-work-gate-f-offline-qualification.md",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("none.v1", "platform-security", "services/capability_registry.py",
           "tests/unit/test_spec40_gate_f_offline.py", status="CURRENT"),
    _entry("workspace.active", "platform-identity", "services/actor_identity.py",
           "tests/unit/test_spec40_gate_f_offline.py", status="CURRENT"),
    _entry("artifact.selected-ready-current", "platform-evidence",
           "services/background_pilot.py", "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("budget.gate-f.available", "platform-runtime", "skills/policy.py",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("gate-f.offline-only", "skills-governance",
           "docs/background-work-gate-f-offline-qualification.md",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("retry.gate-f-offline.v1", "platform-runtime",
           "docs/background-work-gate-f-offline-qualification.md",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("bounded_step_timeout.v1", "platform-runtime",
           "docs/background-work-gate-f-offline-qualification.md",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("offline.no-runtime-reconciliation.v1", "platform-runtime",
           "docs/background-work-gate-f-offline-qualification.md",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("idempotency.skill-invocation.v1", "platform-runtime",
           "docs/37-production-skills-system.md#122-retry-rules",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("offline.no-rollout.v1", "skills-governance",
           "docs/background-work-gate-f-offline-qualification.md",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("skill.gate-f-offline.v1", "safety-evaluation",
           "tests/eval/spec40_gate_f_qualification_plan.json",
           "tests/unit/test_spec40_gate_f_offline.py"),
    _entry("offline.content-free-trace.v1", "platform-operations",
           "skills/audit.py", "tests/unit/test_spec40_gate_f_offline.py"),
)

if len(_CONTRACT_ENTRIES) != len({entry.contract_id for entry in _CONTRACT_ENTRIES}):
    raise RuntimeError("duplicate offline contract id")

CONTRACTS = {entry.contract_id: entry for entry in _CONTRACT_ENTRIES}


def require_contract(contract_id: str) -> ContractEntry:
    try:
        return CONTRACTS[contract_id]
    except KeyError as exc:
        raise ValueError(f"unknown contract id: {contract_id}") from exc
