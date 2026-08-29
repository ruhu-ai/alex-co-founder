"""Code-owned H0 activation gate; prompts and clients cannot weaken it."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_POLICY = Path(__file__).resolve().parents[1] / "policies" / "hiring" / "h0-policy.json"


@lru_cache(maxsize=1)
def policy() -> dict[str, Any]:
    loaded = json.loads(_POLICY.read_text(encoding="utf-8"))
    if loaded.get("schema_version") != 1 or loaded.get("status") != "SYNTHETIC_ONLY":
        raise RuntimeError("unreviewed hiring activation policy")
    return loaded


def require_synthetic(record: dict[str, Any]) -> dict[str, Any]:
    """Return an error as data unless the persisted synthetic guard is valid."""
    expected = str(policy()["synthetic"]["namespace_prefix"])
    if (record.get("synthetic") is not True
            or not str(record.get("synthetic_namespace", "")).startswith(expected)
            or not record.get("fixture_id")):
        return {
            "status": "error", "error": True,
            "error_code": "production_hiring_disabled",
            "message": "Real candidate processing is disabled pending qualified review.",
        }
    return {"status": "success"}


def live_role_intake_enabled() -> bool:
    """Role drafting is preparatory and safe to enable independently."""
    return os.environ.get("HIRING_ENABLE_ROLE_INTAKE", "1").lower() in {
        "1", "true", "yes", "on"}


def live_applications_enabled() -> bool:
    """Whether this deployment accepts candidate data on public role pages."""
    default = "0" if os.environ.get("K_SERVICE") else "1"
    return os.environ.get("HIRING_ENABLE_PUBLIC_APPLICATIONS", default).lower() in {
        "1", "true", "yes", "on"}


def require_role_mode(record: dict[str, Any]) -> dict[str, Any]:
    """Accept a reviewed synthetic fixture or the explicit live-intake mode."""
    if record.get("synthetic") is True:
        return require_synthetic(record)
    if (record.get("synthetic") is False
            and record.get("data_mode") == "LIVE_INTERNAL"
            and live_role_intake_enabled()):
        return {"status": "success"}
    return {
        "status": "error", "error": True,
        "error_code": "production_hiring_disabled",
        "message": "Live role intake is disabled by deployment policy.",
    }


def require_application_mode(record: dict[str, Any]) -> dict[str, Any]:
    """Accept fixture data or an explicitly enabled public application."""
    gate = require_role_mode(record)
    if gate.get("error"):
        return gate
    if record.get("synthetic") is not True and not live_applications_enabled():
        return {
            "status": "error", "error": True,
            "error_code": "public_applications_disabled",
            "message": "This deployment is not accepting public applications.",
        }
    return {"status": "success"}


def require_effect_disabled(effect: str) -> dict[str, Any]:
    key = {
        "email": "external_email_writes",
        "calendar": "external_calendar_writes",
        "linkedin": "linkedin_automation",
        "public_verification": "public_url_verification",
    }.get(effect)
    if key is None or policy()["activation"].get(key) is not False:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract", "message": "unknown effect class"}
    return {"status": "error", "error": True,
            "error_code": "effect_disabled",
            "message": f"{effect} effects are disabled in the H0-H3 build."}
