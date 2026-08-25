"""Custom ADK metrics for outcomes that exact tool arguments cannot express."""

from __future__ import annotations

from google.adk.evaluation.eval_case import get_all_tool_calls, get_all_tool_responses
from google.adk.evaluation.evaluator import EvalStatus, EvaluationResult, PerInvocationResult


def artifact_delivery_score(
    eval_metric,
    actual_invocations,
    expected_invocations=None,
    conversation_scenario=None,
) -> EvaluationResult:
    """Pass only when every invocation successfully produced a downloadable file.

    The document spec is generative, so comparing its arguments byte-for-byte is
    brittle. The non-negotiable contract is observable in the tool result: the
    agent must call ``produce_document``, the call must succeed, and it must
    return both an artifact name and a download URL. A polished chat response
    without that tool outcome fails.
    """
    del conversation_scenario
    threshold = (
        eval_metric.criterion.threshold
        if eval_metric.criterion is not None
        else 1.0
    )
    expected_invocations = expected_invocations or [None] * len(actual_invocations)
    if len(actual_invocations) != len(expected_invocations):
        raise ValueError("actual and expected invocation counts differ")

    per_invocation = []
    scores = []
    for actual, expected in zip(
        actual_invocations, expected_invocations, strict=True
    ):
        called = any(
            call.name == "produce_document"
            for call in get_all_tool_calls(actual.intermediate_data)
        )
        delivered = False
        for response in get_all_tool_responses(actual.intermediate_data):
            if response.name != "produce_document":
                continue
            payload = response.response or {}
            if (
                payload.get("status") == "success"
                and payload.get("artifact_name")
                and payload.get("download_url")
            ):
                delivered = True
                break
        score = 1.0 if called and delivered else 0.0
        status = EvalStatus.PASSED if score >= threshold else EvalStatus.FAILED
        scores.append(score)
        per_invocation.append(
            PerInvocationResult(
                actual_invocation=actual,
                expected_invocation=expected,
                score=score,
                eval_status=status,
            )
        )

    overall = sum(scores) / len(scores) if scores else 0.0
    return EvaluationResult(
        overall_score=overall,
        overall_eval_status=(
            EvalStatus.PASSED if overall >= threshold else EvalStatus.FAILED
        ),
        per_invocation_results=per_invocation,
    )
