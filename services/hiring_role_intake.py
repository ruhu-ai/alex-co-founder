"""Closed model-backed role intake for the production hiring command.

The model proposes one RoleContract. Pydantic and code-owned hiring policy are
the authority; malformed or unsafe output is returned as data and no role is
created. The backend is injectable so tests and offline fixtures never need
network credentials.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from pydantic import ValidationError

from services.hiring_contracts import RoleContract, canonical_hash

RoleContractGenerator = Callable[[str, dict[str, Any]], dict[str, Any]]
_generator: RoleContractGenerator | None = None

_PROTECTED_OR_PROXY = re.compile(
    r"\b(?:age|race|ethnic|religion|gender|sex|pregnan|disab|marital|"
    r"nationality|tribe|culture\s*fit|personality|attractive|young|native\s+speaker|"
    r"school\s+prestige|employer\s+prestige)\b",
    re.IGNORECASE,
)


def set_role_contract_generator(fn: RoleContractGenerator | None) -> None:
    """Install the production or deterministic test role compiler."""
    global _generator
    _generator = fn


async def draft_role_contract(*, description: str, workspace_context: dict[str, Any] | None = None) \
        -> dict[str, Any]:
    """Turn founder prose into one validated, non-authoritative role draft.

    The returned contract still requires the existing exact founder approval
    before it can become active or public.
    """
    normalized = re.sub(r"\s+", " ", description).strip()
    if len(normalized) < 12:
        return _error("role_description_too_short",
                      "Describe the role, company, location and main outcomes.")
    if len(normalized) > 4000:
        return _error("role_description_too_long",
                      "Role descriptions must be 4,000 characters or fewer.")
    if _generator is None:
        return _error("role_intake_unavailable",
                      "Role drafting is temporarily unavailable; no role was created.",
                      retryable=True)
    try:
        raw = await asyncio.to_thread(
            _generator, normalized, dict(workspace_context or {}))
        contract = RoleContract.model_validate(raw)
    except ValidationError as exc:
        return _error("role_contract_invalid",
                      f"The draft was incomplete: {_safe_validation_error(exc)}")
    except Exception:
        return _error("role_intake_failed",
                      "Alex could not prepare a safe role draft; no role was created.",
                      retryable=True)
    unsafe = _unsafe_contract_fields(contract)
    if unsafe:
        return _error("prohibited_role_criterion",
                      "The draft contained a protected trait or prohibited proxy and was refused.")
    payload = contract.model_dump(mode="json")
    return {
        "status": "success", "contract": contract,
        "contract_hash": canonical_hash(payload),
        "normalized_description": normalized,
        "description_hash": canonical_hash({"description": normalized}),
    }


def _unsafe_contract_fields(contract: RoleContract) -> list[str]:
    values = [contract.role_summary]
    for criterion in contract.criteria:
        values.extend([criterion.label, criterion.description, *criterion.evidence_examples])
    for question in contract.interview_plan:
        values.append(question.text)
        values.extend(question.rubric)
    return [value for value in values if _PROTECTED_OR_PROXY.search(value)]


def _safe_validation_error(exc: ValidationError) -> str:
    first = exc.errors(include_url=False)[0] if exc.errors() else {}
    location = ".".join(str(item) for item in first.get("loc", [])) or "contract"
    message = str(first.get("msg") or "invalid value")
    return f"{location}: {message}"[:240]


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "retryable": retryable}
