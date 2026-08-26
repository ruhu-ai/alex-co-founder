"""Code-enforced evidence and consequential-use authority for profile facts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from services import data_source_contracts as dsc

_HIGH_IMPACT_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"(?:arr|mrr|revenue|profit|valuation|financial|runway|burn|funding)",
    r"(?:ownership|equity|cap[_ ]?table|shareholding)",
    r"(?:legal|compliance|incorporat|registered|licensed|regulatory)",
    r"(?:customer|client|user|patient).*(?:name|identity|count|number|total)",
    r"(?:commitment|contract|obligation|guarantee)",
    r"(?:credential|password|secret|api[_ ]?key)",
    r"(?:payment|bank|account|card|routing|iban)",
    r"(?:signature|signatory)",
    r"(?:prior|previous|earlier).*(?:submission|application|award|funding)",
    r"(?:award|grant|funding).*(?:received|held|won|amount)",
))


def is_high_impact_key(key: str) -> bool:
    """Whether a fact category needs founder authority for representation."""
    normalized = str(key or "").replace("-", "_")
    return any(pattern.search(normalized) for pattern in _HIGH_IMPACT_PATTERNS)


def valid_quote_citation(citation: dict[str, Any], quote: str,
                         chunks: list[dict[str, Any]], *,
                         artifact_id: str) -> bool:
    """Require an exact quote/hash/locator bound to a persisted source chunk."""
    if not isinstance(citation, dict) or citation.get("artifact_id") != artifact_id:
        return False
    selected = str(citation.get("quote") or "")
    if not selected or selected != str(quote or "")[:1200]:
        return False
    if citation.get("quote_sha256") != hashlib.sha256(selected.encode()).hexdigest():
        return False
    locator = citation.get("locator")
    if not isinstance(locator, dict) or not locator:
        return False
    chunk = next((row for row in chunks
                  if row.get("id") and row.get("id") == citation.get("chunk_id")), None)
    if not chunk or (chunk.get("locator") or {}) != locator:
        return False
    return " ".join(selected.lower().split()) in " ".join(
        str(chunk.get("content") or "").lower().split())


def consequential_use_gate(profile: dict[str, Any], payload: Any, *,
                            exact_founder_authorization: bool) -> dict[str, Any]:
    """Refuse unconfirmed high-impact values absent exact payload approval.

    Exact draft/email/calendar approval, or a direct founder click over exact
    produced bytes, authorizes that one payload without globally promoting the
    underlying profile fact.
    """
    text = json.dumps(payload, sort_keys=True, default=str).lower()
    blocked: list[str] = []
    facts = profile.get("facts") or {}
    provenance = profile.get("fact_provenance") or {}
    for key, value in facts.items():
        if not is_high_impact_key(key) or str(value).lower() not in text:
            continue
        record = provenance.get(key) or {}
        if (record.get("verification_level") !=
                dsc.VerificationLevel.FOUNDER_CONFIRMED.value
                or record.get("source_available") is False):
            blocked.append(str(key))
    if blocked and not exact_founder_authorization:
        return {"status": "error", "error": True,
                "error_code": "high_impact_confirmation_required",
                "high_impact_keys": sorted(blocked),
                "message": ("Founder confirmation or exact payload approval is required "
                            "for high-impact profile facts.")}
    return {"status": "success", "exact_payload_authorized": bool(blocked)}
