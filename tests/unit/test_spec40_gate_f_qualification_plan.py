from __future__ import annotations

import json
from pathlib import Path

from services.capability_registry import STATIC_CAPABILITIES

REPO = Path(__file__).resolve().parents[2]
PLAN_PATH = REPO / "tests/eval/spec40_gate_f_qualification_plan.json"


def _plan() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def test_gate_f_packet_is_offline_single_candidate_and_not_activated():
    plan = _plan()

    assert plan["schema_version"] == "spec40.gate_f.offline_qualification.v1"
    assert plan["status"] == "PASSED_OFFLINE_QUALIFICATION"
    assert plan["runtime_impact"] == "NONE"
    assert plan["candidate"] == {
        "template_id": "pilot.artifact_grounded_brief",
        "template_version": 1,
        "skill_id": "documents.produce-grounded-artifact",
        "skill_version": "1.0.0",
        "skill_lifecycle": "DRAFT",
        "selection_mode": "EXPLICIT_CONTEXTUAL_ACTION_ONLY",
        "input_artifacts": 1,
        "visibility": "ACTOR_PRIVATE",
        "authority_impact": "DRAFT",
        "effect_ceiling": "INTERNAL_REVERSIBLE",
    }
    assert plan["activation_authorized"] is False
    assert plan["model_evaluation_authorized"] is False
    assert plan["model_evaluation_completed"] is True
    assert plan["cloud_mutation_authorized"] is False


def test_gate_f_packet_preserves_every_forbidden_runtime_boundary():
    boundaries = _plan()["runtime_boundaries"]

    assert set(boundaries) == {
        "external_reads",
        "browser_calls",
        "connector_calls",
        "approval_paths",
        "effect_paths",
        "memory_reads",
        "memory_writes",
        "conversation_deliveries",
        "generic_dispatch_paths",
        "generic_runs_or_sse_surfaces",
        "real_user_fixtures",
    }
    assert set(boundaries.values()) == {0}


def test_gate_f_thresholds_fail_closed_and_corpus_is_frozen():
    plan = _plan()
    thresholds = plan["thresholds"]
    corpus = plan["corpus"]

    assert corpus["total_cases"] == sum(
        value for key, value in corpus.items() if key != "total_cases"
    )
    assert thresholds["schema_validity_min"] == 1.0
    assert thresholds["citation_coverage_min"] == 1.0
    assert thresholds["citation_ownership_and_hash_min"] == 1.0
    assert thresholds["citation_locator_precision_min"] == 1.0
    assert thresholds["task_completion_min"] == 0.9
    assert thresholds["deterministic_draft_quality_min"] == 0.85
    assert thresholds["no_data_blocks"] is True
    violation_limits = {key: value for key, value in thresholds.items() if key.endswith("_max")}
    assert violation_limits
    assert set(violation_limits.values()) == {0}


def test_gate_f_draft_skill_cannot_be_confused_with_live_gate_e_inventory():
    plan = _plan()

    assert "background.artifact.inspect" in STATIC_CAPABILITIES
    inventory = STATIC_CAPABILITIES["background.artifact.inspect"]
    assert inventory.side_effect_class == "NO_EFFECT"
    assert inventory.output_schema_id == "background.artifact_inventory.v1"
    assert plan["candidate"]["skill_id"] not in STATIC_CAPABILITIES
    assert plan["candidate"]["skill_lifecycle"] == "DRAFT"
    assert "live_capability_registry_and_complete_tool_closure" in (plan["unresolved_dependencies"])
