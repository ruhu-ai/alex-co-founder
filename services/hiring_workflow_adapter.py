"""Hiring-owned adapter keeping synthetic activation out of generic runtime."""

from __future__ import annotations

from typing import Any, Mapping

from services import hiring_activation
from services.workflow_contracts import WorkflowDefinition


def hiring_provenance(guard: Mapping[str, Any]) -> dict[str, str]:
    """Validate fixture/live hiring mode and return generic provenance."""
    result = hiring_activation.require_role_mode(dict(guard))
    if result.get("error"):
        raise ValueError(str(result.get("error_code") or "production_hiring_disabled"))
    if guard.get("synthetic") is not True:
        return {
            "provenance_class": "PRODUCTION",
            "namespace": "hiring_live_internal",
            "fixture_set_id": "",
            "data_mode": "LIVE_INTERNAL",
        }
    return {
        "provenance_class": "SYNTHETIC",
        "namespace": str(guard["synthetic_namespace"]),
        "fixture_set_id": str(guard["fixture_id"]),
    }


class HiringWorkflowAdapter:
    """Admit hiring definitions under their code-owned data mode."""

    def validate_run(self, *, definition: WorkflowDefinition,
                     workspace_id: str, domain_ref: str,
                     provenance: Mapping[str, Any]) -> dict[str, Any]:
        if definition.domain != "hiring" or not workspace_id or not domain_ref:
            return {"status": "error", "error": True,
                    "error_code": "hiring_definition_invalid",
                    "message": "The hiring adapter accepts hiring definitions only."}
        return hiring_activation.require_role_mode({
            "synthetic": provenance.get("provenance_class") == "SYNTHETIC",
            "synthetic_namespace": provenance.get("namespace"),
            "fixture_id": provenance.get("fixture_set_id"),
            "data_mode": provenance.get("data_mode"),
        })
