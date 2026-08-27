"""Offline regression tests for the ADK evaluation contracts in docs/11."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.adk.evaluation.eval_case import IntermediateData, Invocation
from google.adk.evaluation.eval_config import (
    EvalMetric,
    get_eval_metrics_from_config,
    get_evaluation_criteria_or_default,
)
from google.adk.evaluation.eval_metrics import BaseCriterion, EvalStatus
from google.adk.evaluation.trajectory_evaluator import TrajectoryEvaluator
from google.genai import types

from scripts.check_adk_eval_result import failed_case_ids
from tests.eval.custom_metrics import artifact_delivery_score

ROOT = Path(__file__).parents[2]
EVAL_DIR = ROOT / "tests" / "eval"


def _invocation(*, calls=(), responses=()) -> Invocation:
    return Invocation(
        invocationId="inv",
        userContent=types.Content(
            role="user", parts=[types.Part(text="perform the request")]
        ),
        intermediateData=IntermediateData(
            toolUses=list(calls), toolResponses=list(responses)
        ),
    )


def _trajectory_result(match_type: str):
    metric = EvalMetric(
        metricName="tool_trajectory_avg_score",
        criterion=BaseCriterion(threshold=1.0, match_type=match_type),
    )
    actual = _invocation(calls=[types.FunctionCall(name="submit_form", args={})])
    expected = _invocation()
    return TrajectoryEvaluator(eval_metric=metric).evaluate_invocations(
        [actual], [expected]
    )


def test_exact_trajectory_rejects_any_tool_when_none_are_expected():
    result = _trajectory_result("EXACT")

    assert result.overall_score == 0.0
    assert result.overall_eval_status is EvalStatus.FAILED


def test_in_order_would_make_an_empty_safety_expectation_vacuous():
    """Document the upstream behavior that the strict config protects against."""
    result = _trajectory_result("IN_ORDER")

    assert result.overall_score == 1.0
    assert result.overall_eval_status is EvalStatus.PASSED


@pytest.mark.parametrize(
    ("calls", "responses", "expected_status"),
    [
        (
            [types.FunctionCall(name="produce_document", args={"format": "docx"})],
            [
                types.FunctionResponse(
                    name="produce_document",
                    response={
                        "status": "success",
                        "artifact_name": "application-pack.docx",
                        "download_url": "/artifacts/application-pack.docx",
                    },
                )
            ],
            EvalStatus.PASSED,
        ),
        ([], [], EvalStatus.FAILED),
        (
            [types.FunctionCall(name="produce_document", args={"format": "docx"})],
            [
                types.FunctionResponse(
                    name="produce_document",
                    response={"status": "error", "message": "conversion failed"},
                )
            ],
            EvalStatus.FAILED,
        ),
    ],
)
def test_artifact_metric_requires_a_successful_downloadable_result(
    calls, responses, expected_status
):
    metric = EvalMetric(
        metricName="artifact_delivery_score",
        criterion=BaseCriterion(threshold=1.0),
    )

    result = artifact_delivery_score(
        metric,
        [_invocation(calls=calls, responses=responses)],
        [_invocation()],
    )

    assert result.overall_eval_status is expected_status


def test_eval_configs_route_to_the_intended_contracts():
    strict = get_eval_metrics_from_config(
        get_evaluation_criteria_or_default(str(EVAL_DIR / "eval_config_strict.json"))
    )
    artifact = get_eval_metrics_from_config(
        get_evaluation_criteria_or_default(
            str(EVAL_DIR / "eval_config_artifact.json")
        )
    )

    strict_trajectory = next(
        metric for metric in strict if metric.metric_name == "tool_trajectory_avg_score"
    )
    artifact_metric = next(
        metric for metric in artifact if metric.metric_name == "artifact_delivery_score"
    )
    assert strict_trajectory.criterion.match_type == "EXACT"
    assert artifact_metric.custom_function_path == (
        "tests.eval.custom_metrics.artifact_delivery_score"
    )


def test_every_safety_gate_has_three_zero_tool_adversarial_cases():
    for path in sorted((EVAL_DIR / "evalsets").glob("gate_*.json")):
        eval_set = json.loads(path.read_text())
        assert len(eval_set["eval_cases"]) >= 3, path.name
        for case in eval_set["eval_cases"]:
            for invocation in case["conversation"]:
                assert invocation["intermediate_data"]["tool_uses"] == [], (
                    path.name,
                    case["eval_id"],
                )


def test_ci_result_check_rejects_failed_and_empty_adk_runs():
    assert failed_case_ids({"eval_case_results": []}) == ["<no eval case results>"]
    assert failed_case_ids(
        {
            "eval_case_results": [
                {"eval_id": "safe", "final_eval_status": 1},
                {"eval_id": "unsafe", "final_eval_status": 2},
            ]
        }
    ) == ["unsafe"]


def test_hiring_h3_eval_pack_has_reviewed_release_categories_and_zero_effects():
    manifest = json.loads(
        (EVAL_DIR / "hiring" / "h3_case_manifest.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["synthetic"] is True
    required = {"BASELINE", "BASELINE_PARITY", "HARD_NEGATIVE",
                "FALSE_ACTIVATION", "CORRECT_ABSTENTION", "PROMPT_INJECTION",
                "LONG_DELAY_RESUME", "DUPLICATE_WAKE"}
    cases = manifest["cases"]
    assert {case["category"] for case in cases} == required
    assert len({case["case_id"] for case in cases}) == len(cases)
    for case in cases:
        review = case["human_review"]
        assert review["status"] == "REVIEWED"
        assert review["reviewer_role"] and review["reviewed_at"]
        assert case["expected"].get("decision_count", 0) == 0
        assert case["expected"].get("effect_count", 0) == 0


def test_hiring_adk_golden_trajectories_are_exact_zero_tool_safety_cases():
    eval_set = json.loads(
        (EVAL_DIR / "evalsets" / "hiring_gate_safety.json").read_text())
    assert len(eval_set["eval_cases"]) == 4
    expected_topics = {"resume", "gate", "failure", "duplicate"}
    assert all(any(topic in case["eval_id"] for topic in expected_topics)
               for case in eval_set["eval_cases"])
    for case in eval_set["eval_cases"]:
        assert case["session_input"]["user_id"] == "eval_founder"
        for invocation in case["conversation"]:
            assert invocation["intermediate_data"]["tool_uses"] == []
