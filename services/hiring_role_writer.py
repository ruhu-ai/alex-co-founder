"""Grounded, bounded job-spec writing for the existing Hiring role package.

The writer has no tools or durable authority. It receives only the exact
Founder description plus bounded verified company context, returns the current
role-description fields, and is followed by deterministic validation and
contract construction. Approval and publication remain separate Founder acts.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.hiring_role_draft import (
    build_contract,
    derive_role_title,
    founder_description_package,
)

WRITER_TOTAL_TIMEOUT_SECONDS = 25.0
MAX_CONTEXT_FACTS = 12


class RoleWriterOutput(BaseModel):
    """The existing editable job-spec fields produced by Gemini."""

    model_config = ConfigDict(extra="forbid")

    role_title: str = Field(min_length=3, max_length=160)
    purpose: str = Field(min_length=120, max_length=1800)
    responsibilities: list[str] = Field(min_length=6, max_length=10)
    success_outcomes: list[str] = Field(min_length=4, max_length=6)
    required_qualifications: list[str] = Field(min_length=5, max_length=8)
    preferred_qualifications: list[str] = Field(default_factory=list, max_length=5)
    relevant_experience: list[str] = Field(min_length=3, max_length=6)
    hiring_process: list[str] = Field(min_length=3, max_length=5)
    application_instructions: str = Field(min_length=20, max_length=500)
    compensation: str = Field(default="", max_length=300)
    benefits: list[str] = Field(default_factory=list, max_length=8)
    equal_opportunity_statement: str = Field(default="", max_length=800)
    accessibility_statement: str = Field(default="", max_length=800)


RoleWriterFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
_writer_fn: RoleWriterFn | None = None


def set_role_writer_fn(fn: RoleWriterFn | None) -> None:
    """Inject the provider boundary; tests remain network- and ADC-free."""
    global _writer_fn
    _writer_fn = fn


def response_schema() -> dict[str, Any]:
    """Return only Vertex's supported structural schema subset.

    Pydantic remains authoritative for lengths/counts/defaults after the call;
    sending its full JSON Schema adds unsupported metadata and constraints that
    some Vertex models reject with HTTP 400.
    """
    strings = {
        "role_title", "purpose", "application_instructions", "compensation",
        "equal_opportunity_statement", "accessibility_statement",
    }
    arrays = {
        "responsibilities", "success_outcomes", "required_qualifications",
        "preferred_qualifications", "relevant_experience", "hiring_process",
        "benefits",
    }
    return {
        "type": "object",
        "properties": {
            **{name: {"type": "string"} for name in sorted(strings)},
            **{name: {"type": "array", "items": {"type": "string"}}
               for name in sorted(arrays)},
        },
        "required": [
            "role_title", "purpose", "responsibilities", "success_outcomes",
            "required_qualifications", "relevant_experience",
            "hiring_process", "application_instructions",
        ],
    }


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, **extra}


def _normalized_tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]{3,}", value.casefold()))


def _near_duplicate(left: str, right: str) -> bool:
    first = _normalized_tokens(left)
    second = _normalized_tokens(right)
    if not first or not second:
        return False
    return len(first & second) / len(first | second) >= 0.78


_PLACEHOLDER = re.compile(
    r"\b(?:tbd|to be determined|insert here|lorem ipsum|competitive salary|"
    r"culture fit)\b", re.I)
_PROHIBITED_SELECTION = re.compile(
    r"\b(?:race|ethnicity|religion|gender|sex|age|disability|marital|pregnan\w*|"
    r"nationality|personality|school prestige|employer prestige)\b", re.I)


def _quality_errors(
        output: RoleWriterOutput, *, expected_title: str,
        founder_description: str) -> list[str]:
    """Return deterministic writing/safety failures suitable for one repair."""
    errors: list[str] = []
    if output.role_title.casefold() != expected_title.casefold():
        errors.append(f"role_title must remain exactly {expected_title!r}")
    scalar_text = " ".join((
        output.purpose, output.application_instructions,
        output.equal_opportunity_statement, output.accessibility_statement,
    ))
    all_lists = {
        "responsibilities": output.responsibilities,
        "success_outcomes": output.success_outcomes,
        "required_qualifications": output.required_qualifications,
        "preferred_qualifications": output.preferred_qualifications,
        "relevant_experience": output.relevant_experience,
        "hiring_process": output.hiring_process,
        "benefits": output.benefits,
    }
    if _PLACEHOLDER.search(scalar_text):
        errors.append("prose contains placeholder or vague culture-fit language")
    for field, values in all_lists.items():
        for index, value in enumerate(values):
            cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" -•\t")
            minimum = 8 if field == "hiring_process" else 18
            if len(cleaned) < minimum or len(cleaned) > 400:
                errors.append(
                    f"{field}[{index}] must be {minimum}..400 characters")
            if _PLACEHOLDER.search(cleaned):
                errors.append(f"{field}[{index}] contains placeholder language")
        for index, left in enumerate(values):
            if any(_near_duplicate(left, right) for right in values[index + 1:]):
                errors.append(f"{field} contains duplicate or near-duplicate items")
                break
    selection_text = " ".join((
        *output.required_qualifications,
        *output.preferred_qualifications,
        *output.relevant_experience,
    ))
    if _PROHIBITED_SELECTION.search(selection_text):
        errors.append("selection material contains a protected attribute or proxy")
    if any(_near_duplicate(responsibility, outcome)
           for responsibility in output.responsibilities
           for outcome in output.success_outcomes):
        errors.append("success outcomes must not restate responsibilities")
    # Optional commercial facts remain empty until a dedicated grounded field
    # supplies them. Mentioning them in free-form input is not enough to create
    # a contractual compensation or benefits representation.
    if output.compensation.strip():
        errors.append("compensation must remain empty without a verified field")
    if output.benefits:
        errors.append("benefits must remain empty without verified fields")
    original_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", founder_description))
    generated_selection = " ".join((
        *output.required_qualifications, *output.relevant_experience))
    if original_numbers and not original_numbers.issubset(set(re.findall(
            r"\b\d+(?:\.\d+)?\b", generated_selection))):
        errors.append("Founder-supplied numeric experience requirements were lost")
    return list(dict.fromkeys(errors))


def _bounded_company_context(source: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only short primitive verified facts; never pass whole profile state."""
    bounded: dict[str, Any] = {}
    allowed = {
        "company", "company_name", "product", "product_description",
        "mission", "sector", "market", "company_stage", "product_stage",
        "customer_description", "headquarters",
    }
    for key, value in list((source or {}).items())[:MAX_CONTEXT_FACTS * 3]:
        if len(bounded) == MAX_CONTEXT_FACTS:
            break
        if str(key).casefold() not in allowed:
            continue
        if isinstance(value, (str, int, float, bool)):
            text = re.sub(r"\s+", " ", str(value)).strip()
            if text:
                bounded[str(key)] = text[:500]
    return bounded


async def write_founder_role_package(
        description: str, *, company_name: str, location: str,
        work_arrangement: str, employment_type: str,
        company_context: dict[str, Any] | None = None,
        headcount_target: int = 1) -> dict[str, Any]:
    """Write and validate one detailed role package with one bounded repair."""
    normalized = re.sub(r"\s+", " ", str(description or "")).strip()
    if len(normalized) < 12:
        return _error(
            "role_description_too_short",
            "Describe the role title and the job-related experience needed.")
    expected_title = derive_role_title(normalized, company_name=company_name)
    if not expected_title or len(expected_title) > 160:
        return _error("role_title_missing",
                      "Start the description with the exact role title.")
    assumptions = {
        "company_name": company_name,
        "location": location,
        "work_arrangement": work_arrangement,
        "employment_type": employment_type,
        "headcount_target": headcount_target,
        "target_date": (date.today() + timedelta(days=120)).isoformat(),
    }
    if _writer_fn is None:
        if (not os.environ.get("K_SERVICE")
                and os.environ.get(
                    "HIRING_ROLE_WRITER_ALLOW_DEGRADED_LOCAL") == "1"):
            fallback = founder_description_package(
                normalized, company_name=company_name, location=location,
                work_arrangement=work_arrangement,
                employment_type=employment_type,
                headcount_target=headcount_target)
            if fallback.get("status") == "success":
                fallback["drafting_mode"] = "DEGRADED_LOCAL"
            return fallback
        return _error(
            "role_writer_unavailable",
            "The detailed job-spec writer is unavailable. Try again when Gemini is configured.")

    request = {
        "mode": "WRITE",
        "founder_description": normalized,
        "expected_role_title": expected_title,
        "company_name": company_name,
        "company_context": _bounded_company_context(company_context),
        "working_assumptions": {
            "location": location,
            "work_arrangement": work_arrangement,
            "employment_type": employment_type,
        },
    }
    last_errors: list[str] = []
    raw: dict[str, Any] = {}
    deadline = asyncio.get_running_loop().time() + WRITER_TOTAL_TIMEOUT_SECONDS
    for attempt in range(2):
        payload = request if attempt == 0 else {
            **request,
            "mode": "REPAIR",
            "previous_output": raw,
            "validation_errors": last_errors,
        }
        try:
            remaining = max(0.1, deadline - asyncio.get_running_loop().time())
            raw = await asyncio.wait_for(
                _writer_fn(payload), timeout=remaining)
        except (TimeoutError, asyncio.TimeoutError):
            return _error("role_writer_unavailable",
                          "The detailed job-spec writer timed out.")
        except Exception:
            return _error("role_writer_unavailable",
                          "The detailed job-spec writer is temporarily unavailable.")
        try:
            output = RoleWriterOutput.model_validate(raw)
        except ValidationError as exc:
            last_errors = [
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in exc.errors()[:20]
            ]
            continue
        last_errors = _quality_errors(
            output, expected_title=expected_title,
            founder_description=normalized)
        if last_errors:
            continue
        target_date = assumptions["target_date"]
        package = build_contract(
            company_name=company_name, role_title=output.role_title,
            role_summary=output.purpose, headcount_target=headcount_target,
            target_date=target_date, location=location,
            work_arrangement=work_arrangement,
            employment_type=employment_type,
            compensation_envelope="", required_criteria=list(
                output.required_qualifications),
            preferred_criteria=list(output.preferred_qualifications),
            relevant_experience=list(output.relevant_experience),
            responsibilities=list(output.responsibilities),
            success_outcomes=list(output.success_outcomes), benefits=[],
            hiring_process=list(output.hiring_process),
            application_instructions=output.application_instructions,
            equal_opportunity_statement=output.equal_opportunity_statement,
            accessibility_statement=output.accessibility_statement,
            # Purpose is already rendered as Role overview; passing it here
            # avoids duplicating the raw Founder prompt as "Additional context".
            public_job_description=output.purpose,
        )
        if package.get("status") != "success":
            last_errors = [str(package.get("message") or
                               package.get("missing_fields") or
                               "contract validation failed")]
            continue
        package["assumptions"] = assumptions
        package["drafting_mode"] = "GEMINI_GROUNDED"
        return package
    return _error(
        "role_draft_unsafe",
        "The generated job specification did not pass deterministic review.",
        validation_errors=last_errors[:20])
