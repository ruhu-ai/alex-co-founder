"""Closed resource sensitivity and retrieval eligibility registry."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ResourceSensitivity(str, Enum):
    GENERAL = "GENERAL"
    FOUNDER_PRIVATE = "FOUNDER_PRIVATE"
    HIRING_RESTRICTED = "HIRING_RESTRICTED"


class IngestionScope(str, Enum):
    PROFILE = "profile"
    REFERENCE_ONLY = "reference_only"
    HIRING_RESTRICTED = "HIRING_RESTRICTED"


def registration_policy(*, scope: str, sensitivity: str) -> dict[str, Any]:
    """Return code-owned projection eligibility for one closed pair."""
    try:
        closed_scope = IngestionScope(scope)
        closed_sensitivity = ResourceSensitivity(sensitivity)
    except ValueError:
        return {"status": "error", "error": True,
                "error_code": "invalid_sensitivity_contract",
                "message": "Unknown ingestion sensitivity."}
    if closed_scope is IngestionScope.HIRING_RESTRICTED:
        if closed_sensitivity is not ResourceSensitivity.HIRING_RESTRICTED:
            return {"status": "error", "error": True,
                    "error_code": "sensitivity_mismatch",
                    "message": "Hiring scope requires hiring-restricted sensitivity."}
        return {"status": "success", "general_search": False,
                "session_resource": False, "founder_profile": False,
                "company_knowledge": False, "authorized_hiring_queries": True}
    if closed_sensitivity is ResourceSensitivity.HIRING_RESTRICTED:
        return {"status": "error", "error": True,
                "error_code": "sensitivity_mismatch",
                "message": "Hiring-restricted content requires its closed scope."}
    return {"status": "success", "general_search": True,
            "session_resource": True,
            "founder_profile": closed_scope is IngestionScope.PROFILE,
            "company_knowledge": closed_scope is IngestionScope.REFERENCE_ONLY,
            "authorized_hiring_queries": False}
