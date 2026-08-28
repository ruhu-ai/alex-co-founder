"""Offline release gate for advisory episodic memory."""

from __future__ import annotations

from typing import Any

THRESHOLDS = {
    "useful_recall_rate": 0.80,
    "cross_workspace_leakage_rate": 0.0,
    "authority_conflict_acceptance_rate": 0.0,
    "deleted_source_recall_rate": 0.0,
    "explicit_backend_failure_rate": 1.0,
}


def evaluate(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    if not outcomes:
        return {"status": "error", "error": True,
                "error_code": "memory_eval_empty"}
    total = len(outcomes)
    metrics = {
        "useful_recall_rate": sum(bool(row.get("useful")) for row in outcomes) / total,
        "cross_workspace_leakage_rate": sum(bool(row.get("cross_workspace_hit"))
                                             for row in outcomes) / total,
        "authority_conflict_acceptance_rate": sum(bool(row.get("accepted_conflict"))
                                                   for row in outcomes) / total,
        "deleted_source_recall_rate": sum(bool(row.get("deleted_source_hit"))
                                          for row in outcomes) / total,
        "explicit_backend_failure_rate": sum(bool(row.get("backend_failure_explicit"))
                                             for row in outcomes) / total,
    }
    passed = all(
        value >= THRESHOLDS[name] if name in {
            "useful_recall_rate", "explicit_backend_failure_rate"}
        else value <= THRESHOLDS[name]
        for name, value in metrics.items())
    return {"status": "success" if passed else "error", "passed": passed,
            "metrics": metrics, "thresholds": THRESHOLDS}
