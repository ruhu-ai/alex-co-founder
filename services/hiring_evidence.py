"""Hiring-restricted redaction, context construction and envelope validation."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from services.hiring_contracts import (
    AnalystInput,
    AnalystOutput,
    ContentRisk,
    Criterion,
    EvidenceItem,
    EvidenceStatus,
    canonical_hash,
)

REDACTION_POLICY_VERSION = 1

# Deliberately conservative for H0-H3. Unknown/conflicting blocks are withheld,
# not guessed safe. The fixture corpus exercises these categories.
_PROTECTED_PATTERNS = {
    "DATE_OF_BIRTH": re.compile(r"\b(?:date of birth|dob|born)\s*[:=-]", re.I),
    "AGE": re.compile(r"\bage\s*[:=-]|\b\d{1,2}\s+years? old\b", re.I),
    "NATIONALITY": re.compile(r"\b(?:nationality|citizenship)\s*[:=-]", re.I),
    "FAMILY_STATUS": re.compile(r"\b(?:marital status|married|children)\s*[:=-]", re.I),
    "RELIGION": re.compile(r"\b(?:religion|faith)\s*[:=-]", re.I),
    "MEDICAL": re.compile(r"\b(?:disability|medical condition|diagnosis)\s*[:=-]", re.I),
    "RACE_ETHNICITY": re.compile(r"\b(?:race|ethnicity)\s*[:=-]", re.I),
    "SEX_GENDER": re.compile(r"\b(?:sex|gender)\s*[:=-]", re.I),
    "GRADUATION_YEAR": re.compile(r"\bgraduat(?:ed|ion)\s+(?:in\s+)?(?:19|20)\d{2}\b", re.I),
}
_INJECTION = re.compile(
    r"(?:ignore|disregard)\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|"
    r"system\s*prompt|developer\s*message|tool\s*call", re.I)


def redact_block(text: str, *, block_id: str,
                 classification_confidence: float = 1.0,
                 classifier_disagreed: bool = False) -> dict[str, Any]:
    """Fail closed at block granularity and never echo protected values."""
    text = str(text or "")
    categories = [name for name, pattern in _PROTECTED_PATTERNS.items()
                  if pattern.search(text)]
    if categories or classification_confidence < 0.98 or classifier_disagreed:
        return {
            "status": "success", "action": "WITHHELD", "safe_text": "",
            "block_id": block_id, "categories": sorted(categories) or ["UNCLASSIFIED"],
            "content_risk": ContentRisk.WITHHELD.value,
            "rule_version": REDACTION_POLICY_VERSION,
            "inbox_required": True,
        }
    if _INJECTION.search(text):
        return {
            "status": "success", "action": "WITHHELD", "safe_text": "",
            "block_id": block_id, "categories": ["PROMPT_INJECTION"],
            "content_risk": ContentRisk.INJECTION_SUSPECTED.value,
            "rule_version": REDACTION_POLICY_VERSION,
            "inbox_required": True,
        }
    return {
        "status": "success", "action": "CLEAR", "safe_text": text[:4000],
        "block_id": block_id, "categories": [],
        "content_risk": ContentRisk.CLEAR.value,
        "rule_version": REDACTION_POLICY_VERSION,
        "inbox_required": False,
    }


def build_analyst_input(*, invocation_id: str, workspace_id: str, role_id: str,
                        candidate_application_id: str, candidate_code: str,
                        policy_version_id: str, policy_hash: str,
                        criteria: list[Criterion], evidence: list[EvidenceItem]
                        ) -> AnalystInput | dict[str, Any]:
    try:
        return AnalystInput(
            invocation_id=invocation_id, workspace_id=workspace_id,
            role_id=role_id, candidate_application_id=candidate_application_id,
            candidate_code=candidate_code, policy_version_id=policy_version_id,
            policy_hash=policy_hash, criteria=criteria, evidence=evidence)
    except ValidationError:
        return {"status": "error", "error": True,
                "error_code": "invalid_analyst_input",
                "message": "Evidence context failed the identity/scope boundary."}


def validate_analyst_output(raw: dict[str, Any], *, expected: AnalystInput,
                            model_id: str, prompt_version: str) -> dict[str, Any]:
    """Validate version, invocation, policy, citations and forbidden output."""
    try:
        # Model responses arrive as JSON. Pydantic's strict JSON path accepts
        # wire-format enum strings while still rejecting coercive Python
        # objects and every unknown field.
        output = AnalystOutput.model_validate_json(json.dumps(raw))
    except ValidationError:
        return _invalid("invalid_analyst_output")
    if (output.invocation_id != expected.invocation_id
            or output.workspace_id != expected.workspace_id
            or output.role_id != expected.role_id
            or output.candidate_application_id != expected.candidate_application_id):
        return _invalid("invocation_mismatch")
    if (output.policy_version_id != expected.policy_version_id
            or output.policy_hash != expected.policy_hash):
        return _invalid("stale_policy")
    criteria = [item.criterion_id for item in expected.criteria]
    if [item.criterion_id for item in output.criteria] != criteria:
        return _invalid("criterion_order_mismatch")
    evidence_hashes = {
        item.evidence_id: canonical_hash(item) for item in expected.evidence
        if item.content_risk is ContentRisk.CLEAR
    }
    for assessment in output.criteria:
        for citation in assessment.citations:
            if evidence_hashes.get(citation.evidence_id) != citation.evidence_hash:
                return _invalid("unauthorized_citation")
        if assessment.status is not EvidenceStatus.UNKNOWN and not assessment.citations:
            return _invalid("uncited_claim")
    permitted_withheld = {item.evidence_id for item in expected.evidence
                          if item.content_risk is not ContentRisk.CLEAR}
    if not set(output.withheld_evidence_ids).issubset(permitted_withheld):
        return _invalid("invalid_withheld_reference")
    return {
        "status": "success", "output": output,
        "assessment_hash": canonical_hash(output), "model_id": model_id,
        "prompt_version": prompt_version, "schema_version": output.schema_version,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }


def _invalid(code: str) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": "Evidence Analyst output was rejected before persistence."}
