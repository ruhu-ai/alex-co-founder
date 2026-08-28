"""Offline-only schema and citation validation for the Gate F draft skill."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


@dataclass(frozen=True)
class EvidenceChunk:
    chunk_id: str
    content_sha256: str
    locator: dict[str, int]


@dataclass(frozen=True)
class ArtifactEvidence:
    artifact_id: str
    artifact_version: str
    chunks: tuple[EvidenceChunk, ...]


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    error_codes: tuple[str, ...]


def validate_schema(payload: object, schema_path: str | Path) -> ValidationResult:
    """Validate one synthetic payload without returning its content in errors."""

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    errors = tuple(
        "schema_invalid"
        for _ in Draft202012Validator(schema).iter_errors(payload)
    )
    return ValidationResult(not errors, errors)


def validate_grounded_output(
    payload: dict[str, Any],
    evidence: ArtifactEvidence,
    *,
    schema_path: str | Path,
    max_output_bytes: int = 65_536,
) -> ValidationResult:
    """Enforce closed output, same-artifact lineage, and exact chunk citations."""

    schema_result = validate_schema(payload, schema_path)
    codes = list(schema_result.error_codes)
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()
    if len(canonical) > max_output_bytes:
        codes.append("output_budget_exceeded")
    if payload.get("source_artifact_id") != evidence.artifact_id:
        codes.append("source_artifact_mismatch")
    if payload.get("source_artifact_version") != evidence.artifact_version:
        codes.append("source_version_mismatch")

    chunks = {chunk.chunk_id: chunk for chunk in evidence.chunks}
    for section in payload.get("sections", ()):
        if not isinstance(section, dict):
            continue
        for claim in section.get("claims", ()):
            if not isinstance(claim, dict):
                continue
            for citation in claim.get("citations", ()):
                if not isinstance(citation, dict):
                    continue
                if citation.get("artifact_id") != evidence.artifact_id:
                    codes.append("citation_artifact_mismatch")
                if citation.get("artifact_version") != evidence.artifact_version:
                    codes.append("citation_version_mismatch")
                chunk = chunks.get(str(citation.get("chunk_id", "")))
                if chunk is None:
                    codes.append("citation_chunk_unknown")
                    continue
                if citation.get("content_sha256") != chunk.content_sha256:
                    codes.append("citation_hash_mismatch")
                if citation.get("locator") != chunk.locator:
                    codes.append("citation_locator_mismatch")
    ordered = tuple(sorted(set(codes)))
    return ValidationResult(not ordered, ordered)
