"""Structural and negative proofs for the shadow-only skills foundation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from services.capability_registry import STATIC_CAPABILITIES
from skills.audit import ShadowSkillTrace
from skills.compiler import AtomicSkillRegistry, SkillCompileError, compile_catalog
from skills.context import ContextRecord, ContextRequest, build_context
from skills.models import CompiledCatalog
from skills.policy import EligibilityContext, effective_capabilities, evaluate_capability
from skills.readiness import PilotReadinessPacket
from skills.selection import SelectionRequest, select_skill

ROOT = Path(__file__).parents[2] / "skills"


def _eligibility(**changes) -> EligibilityContext:
    values = dict(
        principal_id="actor-a", workspace_id="workspace-a", run_id="run-a",
        session_id="session-a", authorized_workspace_id="workspace-a",
        authorized_run_ids=frozenset({"run-a"}),
        agent_role="researcher", workflow_step="funding.discovery",
        principal_permissions=frozenset(),
        base_capability_ids=frozenset({"opportunity.search", "grant.draft"}),
        step_capability_ids=frozenset({"opportunity.search"}),
        workspace_capability_ids=frozenset({"opportunity.search"}),
        enabled_feature_flags=frozenset(), connector_bindings=frozenset(),
        source_grants=frozenset(),
        accepted_data_classes=frozenset({"PUBLIC", "WORKSPACE_INTERNAL"}),
        residency_allowed=True, model_qualified=True, scope_current=True,
        budgets_available=True,
        satisfied_preconditions=frozenset({"workspace.active", "budget.funding.available"}),
    )
    values.update(changes)
    return EligibilityContext(**values)


def test_catalog_compiles_deterministically_and_is_draft_only():
    first = compile_catalog(ROOT)
    second = compile_catalog(ROOT)
    assert first.catalog_hash == second.catalog_hash
    assert {skill.manifest.status.value for skill in first.skills} == {"DRAFT"}
    assert {skill.manifest.skill_id for skill in first.skills} == {
        "funding.discover-opportunities", "funding.draft-grounded-section"}
    assert not any("hiring" in skill.manifest.skill_id
                   or "browser" in skill.manifest.skill_id
                   or "vision" in skill.manifest.skill_id for skill in first.skills)


def test_metadata_progressive_disclosure_and_hash_checked_playbook():
    registry = AtomicSkillRegistry()
    catalog = registry.publish(ROOT)
    card = registry.metadata_index()["funding.discover-opportunities@1.0.0"]
    assert "playbook" not in card.model_dump()
    text = registry.load_playbook("funding.discover-opportunities@1.0.0")
    assert "Do not shortlist" in text
    assert catalog.skills[0].definition_hash.startswith("sha256:")


def test_atomic_registry_retains_prior_catalog_on_invalid_refresh(tmp_path):
    copied = tmp_path / "skills"
    shutil.copytree(ROOT, copied)
    registry = AtomicSkillRegistry()
    accepted = registry.publish(copied)
    manifest = copied / "funding/discover_opportunities/skill.yaml"
    manifest.write_text(manifest.read_text() + "\nunknown_field: forbidden\n")
    with pytest.raises(SkillCompileError):
        registry.publish(copied)
    assert registry.snapshot().catalog_hash == accepted.catalog_hash


def test_duplicate_yaml_alias_and_undeclared_file_fail_closed(tmp_path):
    copied = tmp_path / "skills"
    shutil.copytree(ROOT, copied)
    manifest = copied / "funding/discover_opportunities/skill.yaml"
    manifest.write_text(manifest.read_text() + "\nowner: duplicate\n")
    with pytest.raises(SkillCompileError, match="duplicate manifest key"):
        compile_catalog(copied)

    shutil.rmtree(copied)
    shutil.copytree(ROOT, copied)
    manifest = copied / "funding/discover_opportunities/skill.yaml"
    manifest.write_text(manifest.read_text().replace(
        "supported_intents: [funding_discovery]",
        "supported_intents: &intents [funding_discovery]\n  non_goals: *intents"))
    with pytest.raises(SkillCompileError, match="aliases"):
        compile_catalog(copied)

    shutil.rmtree(copied)
    shutil.copytree(ROOT, copied)
    (copied / "funding/discover_opportunities/hidden.txt").write_text("not declared")
    with pytest.raises(SkillCompileError, match="undeclared"):
        compile_catalog(copied)


@pytest.mark.parametrize("field,value,clause", [
    ("principal_id", "", "principal_identity"),
    ("authorized_workspace_id", "other", "workspace_scope"),
    ("authorized_run_ids", frozenset(), "run_scope"),
    ("agent_role", "writer", "agent_role"),
    ("step_capability_ids", frozenset(), "step_policy"),
    ("workspace_capability_ids", frozenset(), "workspace_policy"),
    ("accepted_data_classes", frozenset({"PUBLIC"}), "data_class"),
    ("residency_allowed", False, "residency"),
    ("model_qualified", False, "model_qualification"),
    ("scope_current", False, "scope"),
    ("budgets_available", False, "budgets"),
    ("satisfied_preconditions", frozenset(), "preconditions"),
])
def test_each_eligibility_clause_fails_closed(field, value, clause):
    skill = compile_catalog(ROOT).by_identity()["funding.discover-opportunities@1.0.0"]
    decision = evaluate_capability(
        skill, STATIC_CAPABILITIES["opportunity.search"], _eligibility(**{field: value}))
    assert not decision.eligible
    assert clause in decision.failed_clauses


def test_skill_cannot_widen_broader_agent_capabilities():
    skill = compile_catalog(ROOT).by_identity()["funding.discover-opportunities@1.0.0"]
    effective = effective_capabilities(skill, STATIC_CAPABILITIES, _eligibility())
    assert [item.capability_id for item in effective] == ["opportunity.search"]
    assert "grant.draft" not in {item.capability_id for item in effective}


def test_selector_is_shadow_only_and_never_silently_truncates():
    catalog = compile_catalog(ROOT)
    selected = select_skill(catalog, SelectionRequest(
        invocation_mode="CONVERSATION", intent="funding_discovery",
        agent_role="researcher", workflow_step="funding.discovery",
        include_draft_for_shadow=True))
    assert selected.outcome == "SELECTED"
    assert selected.selected.skill_id == "funding.discover-opportunities"
    live = select_skill(catalog, SelectionRequest(
        invocation_mode="CONVERSATION", intent="funding_discovery",
        agent_role="researcher", workflow_step="funding.discovery"))
    assert live.outcome == "NO_MATCH"


def test_more_than_five_candidates_requires_clarification_without_truncation():
    base = compile_catalog(ROOT).skills[0]
    skills = tuple(base.model_copy(update={
        "manifest": base.manifest.model_copy(update={"skill_id": f"funding.candidate-{index}"})
    }) for index in range(6))
    result = select_skill(CompiledCatalog(catalog_hash="sha256:test", skills=skills),
                          SelectionRequest(
                              invocation_mode="CONVERSATION", intent="funding_discovery",
                              agent_role="researcher", workflow_step="funding.discovery",
                              include_draft_for_shadow=True))
    assert result.outcome == "CLARIFY"
    assert len(result.candidates) == 6


def test_context_excludes_cross_scope_stale_and_cross_session_chat():
    rows = (
        ContextRecord("ok", "profile", "w", provenance_ref="profile:v1", content="safe"),
        ContextRecord("foreign", "profile", "other", provenance_ref="p:v1", content="secret"),
        ContextRecord("stale", "profile", "w", provenance_ref="p:v0", current=False, content="old"),
        ContextRecord("chat", "raw_chat", "w", session_id="other", provenance_ref="s:1", content="old chat"),
    )
    built = build_context(rows, ContextRequest(
        workspace_id="w", run_id="", entity_id="", session_id="current",
        allowed_record_types=frozenset({"profile", "raw_chat"}),
        allowed_data_classes=frozenset({"WORKSPACE_INTERNAL"}),
        max_items=10, max_bytes=1000, allow_raw_chat=True))
    assert [row.record_id for row in built.records] == ["ok"]
    assert "safe" not in built.context_hash


def test_readiness_packet_cannot_approve_unqualified_or_dual_authority_pilot():
    packet = PilotReadinessPacket(
        pilot_id="pilot-0", skill_identity="funding.discover-opportunities@1.0.0",
        journey_ref="docs/37#pilot", live_agent_ref="agents/co_founder/sub_agents/scout.py",
        live_tool_capability_map_ref="tests/tool-map", predicate_enforcement_ref="skills/policy.py",
        sole_authority_ref="services/discovery_service.py", contract_map_ref="skills/contracts.py",
        context_contract_ref="skills/context.py", error_retry_approval_ref="docs/37#12",
        proof_suite_ref="tests/unit/test_skill_foundation.py", rollback_ref="disable-shadow-import",
        definition_hash="sha256:" + "1" * 64, qualification_hash="sha256:" + "2" * 64,
        capability_closure_verified=True, typed_contracts_verified=True,
        one_authority_verified=False, negative_authority_tests_passed=True,
        qualification_passed=False, live_adoption_requested=True)
    assert not packet.implementation_approved()
    assert set(packet.blockers()) >= {"one_authority_verified", "qualification_passed"}


def test_repository_pilot_packet_is_explicitly_not_live_ready():
    payload = json.loads((ROOT / "pilots/pilot-0-funding-discovery.json").read_text())
    packet = PilotReadinessPacket.model_validate(payload)
    skill = compile_catalog(ROOT).by_identity()[packet.skill_identity]
    assert packet.definition_hash == skill.definition_hash
    assert packet.qualification_hash == skill.qualification_hash
    assert not packet.implementation_approved()
    assert packet.live_adoption_requested is False


def test_shadow_trace_is_content_free_and_has_no_authority_writes():
    trace = ShadowSkillTrace(
        correlation_id="c1", workspace_ref="workspace:hashed", run_ref="",
        skill_identity="funding.discover-opportunities@1.0.0",
        definition_hash="sha256:def", qualification_hash="sha256:qual",
        selector_version="shadow-v1", outcome="SELECTED",
        candidate_ids=("funding.discover-opportunities",),
        context_hash="sha256:context", context_item_count=2,
        effective_capability_ids=("opportunity.search",))
    assert trace.is_shadow_only()
    assert "content" not in trace.__dict__


def test_live_app_and_agent_graph_do_not_import_skills_runtime():
    repo = ROOT.parent
    for folder in (repo / "app", repo / "agents"):
        for path in folder.rglob("*.py"):
            text = path.read_text()
            assert "from skills" not in text
            assert "import skills" not in text


def test_qualification_evidence_hash_is_independent_of_definition(tmp_path):
    copied = tmp_path / "skills"
    shutil.copytree(ROOT, copied)
    before = compile_catalog(copied).by_identity()["funding.discover-opportunities@1.0.0"]
    qpath = copied / "funding/discover_opportunities/qualification.json"
    evidence = json.loads(qpath.read_text())
    evidence["model_policy_version"] = "still-unqualified-v2"
    qpath.write_text(json.dumps(evidence))
    after = compile_catalog(copied).by_identity()["funding.discover-opportunities@1.0.0"]
    assert before.definition_hash == after.definition_hash
    assert before.qualification_hash != after.qualification_hash
