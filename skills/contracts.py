"""Auditable contract ownership/status map for Pilot-0 skill packages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractEntry:
    contract_id: str
    owner: str
    status: str
    canonical_reference: str
    acceptance_evidence: str


def _entry(contract_id: str, owner: str, reference: str, evidence: str) -> ContractEntry:
    return ContractEntry(contract_id, owner, "CURRENT", reference, evidence)


CONTRACTS = {entry.contract_id: entry for entry in (
    _entry("model.error.v1", "platform-runtime", "services/error_contracts.py", "tests/unit/test_skill_foundation.py"),
    _entry("skill.funding.discover.input.v1", "funding-runtime", "skills/funding/discover_opportunities/schemas/input.v1.json", "tests/unit/test_skill_foundation.py"),
    _entry("skill.funding.discover.output.v1", "funding-runtime", "skills/funding/discover_opportunities/schemas/output.v1.json", "tests/unit/test_skill_foundation.py"),
    _entry("skill.funding.draft.input.v1", "funding-runtime", "skills/funding/draft_grounded_section/schemas/input.v1.json", "tests/unit/test_skill_foundation.py"),
    _entry("skill.funding.draft.output.v1", "funding-runtime", "skills/funding/draft_grounded_section/schemas/output.v1.json", "tests/unit/test_skill_foundation.py"),
    _entry("funding.candidate-set.v1", "funding-runtime", "services/capability_registry.py", "tests/unit/test_skill_foundation.py"),
    _entry("funding.grounded-draft.v1", "funding-runtime", "services/capability_registry.py", "tests/unit/test_skill_foundation.py"),
    _entry("context.funding-discovery.v1", "platform-runtime", "skills/context.py", "tests/unit/test_skill_foundation.py"),
    _entry("context.grounded-drafting.v1", "platform-runtime", "skills/context.py", "tests/unit/test_skill_foundation.py"),
    _entry("evidence.source-citation.v1", "platform-evidence", "docs/37-production-skills-system.md#9", "tests/unit/test_skill_foundation.py"),
    _entry("evidence.run-citation.v1", "platform-evidence", "docs/37-production-skills-system.md#9", "tests/unit/test_skill_foundation.py"),
    _entry("citations.material-claims-required.v1", "platform-evidence", "docs/37-production-skills-system.md#9", "tests/unit/test_skill_foundation.py"),
    _entry("none.v1", "platform-security", "services/capability_registry.py", "tests/unit/test_skill_foundation.py"),
    _entry("workspace.active", "platform-identity", "services/actor_identity.py", "tests/unit/test_skill_foundation.py"),
    _entry("budget.funding.available", "platform-runtime", "skills/policy.py", "tests/unit/test_skill_foundation.py"),
    _entry("source.scope.current-workspace", "platform-security", "skills/policy.py", "tests/unit/test_skill_foundation.py"),
    _entry("retry.read-transient.v1", "platform-runtime", "docs/37-production-skills-system.md#12", "tests/unit/test_skill_foundation.py"),
    _entry("retry.internal-reversible.v1", "platform-runtime", "docs/37-production-skills-system.md#12", "tests/unit/test_skill_foundation.py"),
    _entry("idempotency.skill-invocation.v1", "platform-runtime", "docs/37-production-skills-system.md#12", "tests/unit/test_skill_foundation.py"),
    _entry("canary.internal-workspaces.v1", "platform-runtime", "docs/37-production-skills-system.md#13", "tests/unit/test_skill_foundation.py"),
    _entry("skill.standard-read-only.v1", "skills-governance", "docs/37-production-skills-system.md#15", "tests/unit/test_skill_foundation.py"),
    _entry("skill.standard-internal-reversible.v1", "skills-governance", "docs/37-production-skills-system.md#15", "tests/unit/test_skill_foundation.py"),
)}


def require_contract(contract_id: str) -> ContractEntry:
    try:
        return CONTRACTS[contract_id]
    except KeyError as exc:
        raise ValueError(f"unknown contract id: {contract_id}") from exc
