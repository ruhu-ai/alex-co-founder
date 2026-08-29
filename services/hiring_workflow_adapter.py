"""Hiring-owned adapter keeping synthetic activation out of generic runtime."""

from __future__ import annotations

from typing import Any, Mapping

from services import hiring_activation
from services.workflow_contracts import WorkflowDefinition


def hiring_provenance(guard: Mapping[str, Any]) -> dict[str, str]:
    """Validate the closed H0 guard and return generic provenance metadata."""
    result = hiring_activation.require_synthetic(dict(guard))
    if result.get("error"):
        raise ValueError(str(result.get("error_code") or "production_hiring_disabled"))
    return {
        "provenance_class": "SYNTHETIC",
        "namespace": str(guard["synthetic_namespace"]),
        "fixture_set_id": str(guard["fixture_id"]),
    }


def founder_draft_provenance() -> dict[str, str]:
    """Production-shaped provenance admitted only for a non-executable role draft."""
    return {
        "provenance_class": "FOUNDER_DRAFT",
        "draft_mode": "INTERNAL_REVIEW_ONLY",
        "policy_version": "founder-role-draft-v1",
    }


def founder_application_provenance() -> dict[str, str]:
    """Production provenance for explicit Founder-triggered evidence mapping."""
    return {
        "provenance_class": "FOUNDER_APPLICATION",
        "processing_mode": "FOUNDER_TRIGGERED_EVIDENCE_ONLY",
        "policy_version": "founder-public-application-v1",
    }


class HiringWorkflowAdapter:
    """Admit synthetic runs plus the exact non-executable Founder role draft."""

    def validate_run(self, *, definition: WorkflowDefinition,
                     workspace_id: str, domain_ref: str,
                     provenance: Mapping[str, Any]) -> dict[str, Any]:
        if definition.domain != "hiring" or not workspace_id or not domain_ref:
            return {"status": "error", "error": True,
                    "error_code": "hiring_definition_invalid",
                    "message": "The hiring adapter accepts hiring definitions only."}
        if (definition.workflow_kind == "hiring_role:v1"
                and provenance == founder_draft_provenance()):
            return {"status": "success"}
        if (definition.workflow_kind == "hiring_candidate:v1"
                and provenance == founder_application_provenance()):
            return {"status": "success"}
        return hiring_activation.require_synthetic({
            "synthetic": provenance.get("provenance_class") == "SYNTHETIC",
            "synthetic_namespace": provenance.get("namespace"),
            "fixture_id": provenance.get("fixture_set_id"),
        })
