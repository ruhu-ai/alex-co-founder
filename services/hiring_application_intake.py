"""Conservative document-to-criterion mapping for public applications."""

from __future__ import annotations

import re
from typing import Any

from services.hiring_contracts import Criterion

_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "into", "your",
    "have", "will", "role", "work", "using", "through", "candidate", "experience",
}


def map_application_blocks(*, chunks: list[dict[str, Any]],
                           criteria: list[Criterion], cover_note: str = "") \
        -> list[dict[str, Any]]:
    """Return exact source blocks mapped conservatively to role criteria.

    Mapping is retrieval only. Live assessments label matching candidate claims
    PARTIAL and require human verification; no model score or recommendation is
    produced.
    """
    sources = []
    if cover_note.strip():
        sources.append({"block_id": "application_cover_note",
                        "text": cover_note.strip()[:4000],
                        "source_kind": "APPLICATION"})
    for index, chunk in enumerate(chunks[:300]):
        text = str(chunk.get("content") or "").strip()
        if text:
            sources.append({
                "block_id": f"resume_{index + 1}", "text": text[:4000],
                "source_kind": "RESUME",
            })
    output: list[dict[str, Any]] = []
    for source in sources:
        source_tokens = _tokens(source["text"])
        matches: list[str] = []
        for criterion in criteria:
            criterion_text = " ".join([
                criterion.label, criterion.description,
                *criterion.evidence_examples,
            ])
            criterion_tokens = _tokens(criterion_text)
            overlap = source_tokens & criterion_tokens
            meaningful = {token for token in overlap if len(token) >= 4}
            if len(meaningful) >= 2 or any(
                    phrase.lower() in source["text"].lower()
                    for phrase in criterion.evidence_examples if len(phrase) >= 12):
                matches.append(criterion.criterion_id)
        if matches:
            output.append({**source, "criterion_ids": matches,
                           "classification_confidence": 1.0,
                           "classifier_disagreed": False})
    return output[:120]


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9+#.-]{2,}", value.lower())
            if token not in _STOP}
