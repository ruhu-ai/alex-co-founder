"""Code-owned H0 activation gate; prompts and clients cannot weaken it."""

from __future__ import annotations

import json
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
