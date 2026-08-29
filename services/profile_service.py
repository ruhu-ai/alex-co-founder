"""Profile service (docs/06) — the Founder Profile: facts, voice rules,
canonical answers, rejection history, decision patterns. Plus document
ingestion: autonomous by default (auto-apply confident, non-conflicting
proposals), founder asked only about conflicts and low-confidence items.

All profile writes go through apply_update (versioned, audited).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from typing import Any, Callable

from services import firestore, profile_authority
from services.canonical import canonical_hash

# Injectable for tests / offline dev: (artifact_name, founder_id) -> proposals list
ExtractFn = Callable[[str, str], list[dict[str, Any]]]
_extract_fn: ExtractFn | None = None
ChunkExtractFn = Callable[[list[dict[str, Any]], str], list[dict[str, Any]]]
_chunk_extract_fn: ChunkExtractFn | None = None

# Injectable embeddings (docs/06 §retrieval, 19 §P1.6). None in tests and
# offline dev → deterministic tag retrieval stays primary (fallback guard).
EmbedFn = Callable[[list[str]], list[list[float]]]
_embed_fn: EmbedFn | None = None


def set_extract_fn(fn: ExtractFn) -> None:
    """Tests and offline dev inject a fake extractor; prod wires the Gemini
    document-understanding call (Day 3 live check, needs ADC)."""
    global _extract_fn
    _extract_fn = fn


def set_chunk_extract_fn(fn: ChunkExtractFn | None) -> None:
    """Inject profile extraction over validated structured chunks.

    New uploads use this path so reading DOCX/PPTX/XLSX never depends on a
    desktop converter. ``set_extract_fn`` remains for the legacy Drive flow.
    """
    global _chunk_extract_fn
    _chunk_extract_fn = fn


def set_embed_fn(fn: EmbedFn | None) -> None:
    global _embed_fn
    _embed_fn = fn


async def get_profile(founder_id: str) -> dict[str, Any]:
    profile = await firestore.get_profile(founder_id)
    read_mode = os.environ.get("PROFILE_FACT_READ_MODE", "compatibility")
    if read_mode in {"dual", "target"}:
        from services.profile_fact_service import ProfileFactService

        target = await ProfileFactService().current(
            workspace_id=founder_id, actor_id=founder_id,
            include_actor_private=True)
        if not target.get("error"):
            parity = canonical_hash(
                dict(profile.get("facts") or {}), domain="profile-parity") == canonical_hash(
                    target["facts"], domain="profile-parity")
            if not parity:
                await firestore.audit(
                    "system:profile_migration", "profile_read_parity",
                    f"profiles/{founder_id}", "mismatch",
                    f"compat_count={len(profile.get('facts') or {})};"
                    f"target_count={len(target['facts'])}")
            if read_mode == "target":
                profile = {**profile, "facts": target["facts"],
                           "target_fact_records": target["records"],
                           "profile_storage_schema_version": 2}
    projected = {**profile,
                 "fact_provenance": dict(profile.get("fact_provenance") or {}),
                 "canonical_answers": [dict(row) for row in
                                       profile.get("canonical_answers", [])]}
    for key, record in list(projected["fact_provenance"].items()):
        item = dict(record or {})
        grant_id = item.get("source_grant_id")
        if grant_id:
            grant = await firestore.get_source_grant(founder_id, grant_id)
            item["source_available"] = bool(
                grant and grant.get("status") == "ACTIVE")
            item["requires_revalidation"] = bool(
                not item["source_available"]
                and profile_authority.is_high_impact_key(key))
        projected["fact_provenance"][key] = item
    for answer in projected["canonical_answers"]:
        grant_id = answer.get("source_grant_id")
        if grant_id:
            grant = await firestore.get_source_grant(founder_id, grant_id)
            answer["source_available"] = bool(
                grant and grant.get("status") == "ACTIVE")
    return projected


async def record_answer(founder_id: str, question_key: str, question: str,
                        answer: str, application_id: str = "") -> dict:
    """Compatibility wrapper for the guarded, atomic interview write path."""
    if not application_id:
        return {"status": "error", "error": True,
                "message": "an active INTERVIEWING application is required"}
    return await firestore.record_interview_answer(
        founder_id, application_id, question_key, question, answer)


async def get_voice_rules(founder_id: str) -> list[dict[str, Any]]:
    profile = await get_profile(founder_id)
    rules = [r for r in profile.get("voice_rules", []) if r.get("active", True)]
    return sorted(rules, key=lambda r: r.get("created_at", ""), reverse=True)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


async def get_relevant_answers(founder_id: str, section_key: str, limit: int = 5) -> list[dict[str, Any]]:
    """Retrieval (docs/06 §retrieval). Semantic when embeddings are wired:
    cosine top-k over answer texts — 'describe your traction' matches '1,200
    patients on follow-up plans' with zero shared words. Deterministic
    (exact key, tag overlap, recency) when offline, in tests, or on backend
    error — the fallback guard from 19 §P1.6."""
    profile = await get_profile(founder_id)
    answers = profile.get("canonical_answers", [])
    if _embed_fn is not None and answers:
        try:
            texts = [f"{a.get('question_key', '')}: {a.get('text', '')}" for a in answers]
            vecs = await asyncio.to_thread(_embed_fn, [section_key] + texts)
            section_vec, answer_vecs = vecs[0], vecs[1:]
            ranked = sorted(zip(answer_vecs, answers),
                            key=lambda p: _cosine(p[0], section_vec), reverse=True)
            return [a for _, a in ranked[:limit]]
        except Exception:
            pass  # backend hiccup → deterministic path, never an outage
    exact = [a for a in answers if a.get("question_key") == section_key]
    tagged = [a for a in answers
              if a.get("question_key") != section_key and section_key in a.get("tags", [])]
    rest = [a for a in answers if a not in exact and a not in tagged]
    key = lambda a: a.get("approved_at", a.get("created_at", ""))  # noqa: E731
    return (sorted(exact, key=key, reverse=True)
            + sorted(tagged, key=key, reverse=True)
            + sorted(rest, key=key, reverse=True))[:limit]


async def apply_update(founder_id: str, kind: str, payload: dict, evidence: str) -> dict:
    version = await firestore.apply_profile_update(founder_id, kind, payload, evidence)
    return {"status": "success", "profile_version": version}


# ---------------------------------------------------------------------------
# Document ingestion (docs/06 §bootstrap): autonomous auto-apply + founder
# review of conflicts only. Proposals are staged, never written directly by
# ingest_document itself.
# ---------------------------------------------------------------------------


def _conflict(profile: dict, proposal: dict) -> str | None:
    """Deterministic conflict check against the current profile. Returns a
    human-readable conflict description, else None."""
    kind = proposal.get("kind")
    payload = proposal.get("payload", {})
    if kind == "fact_update":
        for key, value in payload.items():
            existing = profile.get("facts", {}).get(key)
            if existing is not None and existing != value:
                return f"fact '{key}': profile has {existing!r}, document says {value!r}"
    elif kind == "canonical_answer_update":
        qk = payload.get("question_key")
        for a in profile.get("canonical_answers", []):
            if a.get("question_key") == qk and a.get("text") != payload.get("text"):
                return f"canonical answer '{qk}' already exists with different text"
    return None


async def auto_apply_profile_updates(founder_id: str, ingestion_id: str, *,
                                     commit_status: bool = True,
                                     lease_owner: str = "") -> dict:
    """Autonomous ingestion review (docs/06 §bootstrap): apply every confident,
    non-conflicting proposal immediately (versioned, evidenced, audited).
    Conflicts and low-confidence items stay PENDING in `needs_founder` — the
    agent asks the founder about exactly those, nothing else."""
    ingestion = await firestore.get_ingestion(ingestion_id)
    if not ingestion:
        return {"status": "error", "error": True, "message": f"ingestion {ingestion_id} not found"}
    if ingestion.get("founder_id") != founder_id:
        return {"status": "error", "error": True, "message": f"ingestion {ingestion_id} not found"}
    artifact = await firestore.get_artifact(ingestion_id)
    chunks = await firestore.list_artifact_chunks(ingestion_id)
    profile = await get_profile(founder_id)
    applied, needs_founder = [], []
    for p in ingestion["proposed_updates"]:
        if p["status"] != "PENDING":
            continue
        conflict = _conflict(profile, p)
        citation = p.get("citation") if isinstance(p.get("citation"), dict) else {}
        cited = bool(artifact and artifact.get("authority") == "profile_candidate"
                     and artifact.get("scope") == "profile"
                     and profile_authority.valid_quote_citation(
                         citation, p.get("evidence_quote", ""), chunks,
                         artifact_id=ingestion_id))
        source_available = True
        if artifact and artifact.get("source_grant_id"):
            grant = await firestore.get_source_grant(
                founder_id, artifact["source_grant_id"])
            source_available = bool(grant and grant.get("status") == "ACTIVE")
        excluded_kind = p.get("kind") in {"voice_rule", "decision_pattern"}
        issues = [conflict] if conflict else []
        if p.get("confidence") == "low":
            issues.append("low-confidence extraction (inferred, not stated)")
        if not cited:
            issues.append("proposal has no exact hash-matching source citation")
        if not source_available:
            issues.append("the cited source is revoked or unavailable")
        if excluded_kind:
            issues.append("this proposal kind has no deterministic conflict and "
                          "supersession semantics")
        if issues:
            p["review_reason"] = "; ".join(issues)
            p["verification_level"] = "UNCONFIRMED_EVIDENCE"
            p["source_available"] = source_available
            needs_founder.append({
                "id": p["id"], "kind": p["kind"], "payload": p["payload"],
                "evidence_quote": p.get("evidence_quote", ""),
                "citation": citation, "reason": p["review_reason"],
                "verification_level": "UNCONFIRMED_EVIDENCE",
                "source_available": source_available,
            })
            continue
        evidence = p.get("evidence_quote", f"ingestion {ingestion_id}")
        if citation:
            evidence = f"{evidence} [{_citation_label(citation)}]"
        await firestore.apply_profile_update(
            founder_id, p["kind"], p["payload"],
            evidence,
            idempotency_key=f"ingestion:{ingestion_id}:proposal:{p['id']}",
            verification_level="EVIDENCE_VERIFIED",
            provenance={
                "source": artifact.get("source_type") or "upload",
                "source_id": ingestion_id,
                "source_grant_id": artifact.get("source_grant_id"),
                "source_version": artifact.get("provider_version"),
                "citation": citation, "source_available": source_available,
            },
        )
        p["status"] = "APPROVED"
        p["auto_applied"] = True
        p["verification_level"] = "EVIDENCE_VERIFIED"
        applied.append(p["id"])
        # Re-fetch so later proposals conflict-check against earlier applies.
        profile = await get_profile(founder_id)
    remaining = [p for p in ingestion["proposed_updates"] if p["status"] == "PENDING"]
    updates: dict[str, Any] = {"proposed_updates": ingestion["proposed_updates"]}
    if commit_status:
        # Legacy direct-ingestion callers do not have a worker lease to commit
        # the terminal status. The durable worker passes False and publishes
        # the terminal status atomically through finish_ingestion instead.
        updates["status"] = "NEEDS_FOUNDER" if remaining else "CONFIRMED"
    persisted = (await firestore.update_ingestion_leased(
        ingestion_id, lease_owner, **updates) if lease_owner else
        await firestore.update_ingestion(ingestion_id, **updates))
    if lease_owner and not persisted:
        return {"status": "error", "error": True,
                "error_code": "lease_changed",
                "message": "Document worker lease changed before profile review committed"}
    return {"status": "success", "ingestion_id": ingestion_id,
            "auto_applied": len(applied), "needs_founder": needs_founder}


def _citation_label(citation: dict) -> str:
    locator = citation.get("locator") if isinstance(citation.get("locator"), dict) else {}
    location = next(
        (f"{key} {locator[key]}" for key in ("page", "slide", "paragraph", "sheet")
         if locator.get(key) not in (None, "")),
        "document",
    )
    return f"artifact {citation.get('artifact_id', '?')}, {location}"


def _proposal_citation(artifact_id: str, quote: str,
                       chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized_quote = " ".join(quote.lower().split())
    chosen = None
    for chunk in chunks:
        if normalized_quote and normalized_quote in " ".join(
                str(chunk.get("content", "")).lower().split()):
            chosen = chunk
            break
    if chosen is None and chunks:
        terms = set(re.findall(r"[a-z0-9]{2,}", normalized_quote))
        scored = [
            (len(terms & set(re.findall(
                r"[a-z0-9]{2,}", str(chunk.get("content", "")).lower()))), chunk)
            for chunk in chunks
        ]
        overlap, candidate = max(scored, key=lambda item: item[0])
        # Never bind unrelated model output to an arbitrary chunk merely
        # because it was the least-bad match.
        if overlap:
            chosen = candidate
    if chosen is None:
        return None
    selected = quote[:1200]
    return {
        "artifact_id": artifact_id,
        "chunk_id": chosen.get("id", ""),
        "locator": chosen.get("locator") or {},
        "quote": selected,
        "quote_sha256": hashlib.sha256(selected.encode("utf-8")).hexdigest(),
    }


async def extract_profile_proposals(founder_id: str, artifact_id: str,
                                    artifact_name: str,
                                    chunks: list[dict[str, Any]]) -> dict:
    """Extract and validate profile proposals, binding every claim to a chunk.

    Model output cannot create an unbounded proposal shape. A purportedly
    high-confidence quote that cannot be located in extracted source content is
    downgraded to low confidence and therefore requires founder review.
    """
    if _chunk_extract_fn is None and _extract_fn is None:
        return {"status": "error", "error": True,
                "error_code": "extractor_unavailable",
                "message": "document extraction requires Gemini credentials"}
    try:
        if _chunk_extract_fn is not None:
            raw = await asyncio.to_thread(_chunk_extract_fn, chunks, founder_id)
        else:
            raw = await asyncio.to_thread(_extract_fn, artifact_name, founder_id)
    except Exception as exc:
        from services.retry_policy import is_transient_exception

        return {"status": "error", "error": True,
                "error_code": "proposal_extraction_failed",
                "retryable": is_transient_exception(exc),
                "message": f"profile proposal extraction failed: {exc}"[:240]}
    if not isinstance(raw, list):
        return {"status": "error", "error": True,
                "error_code": "invalid_proposal_output",
                "message": "profile proposal extraction returned an invalid shape"}
    allowed = {"fact_update", "voice_rule", "canonical_answer_update", "decision_pattern"}
    proposals = []
    for item in raw[:50]:
        if not isinstance(item, dict) or item.get("kind") not in allowed \
                or not isinstance(item.get("payload"), dict):
            continue
        quote = str(item.get("evidence_quote") or "").strip()[:1200]
        citation = _proposal_citation(artifact_id, quote, chunks)
        confidence = "high" if item.get("confidence") == "high" else "low"
        if not quote or citation is None:
            confidence = "low"
        payload = json.loads(json.dumps(item["payload"], default=str))
        proposal_id = hashlib.sha256(json.dumps({
            "artifact_id": artifact_id, "kind": item["kind"],
            "payload": payload, "evidence_quote": quote,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
        proposals.append({
            "id": proposal_id, "kind": item["kind"],
            "payload": payload,
            "evidence_quote": quote, "citation": citation or {},
            "confidence": confidence, "status": "PENDING",
        })
    return {"status": "success", "proposals": proposals}

async def ingest_document(founder_id: str, source_type: str, ref: str,
                          artifact_name: str) -> dict:
    """Extract structured proposals from a company document. Never writes the
    profile — the founder confirms item by item."""
    if _extract_fn is None:
        return {
            "status": "error",
            "error": True,
            "message": "document extraction requires Gemini credentials "
                       "(run `gcloud auth application-default login`)",
        }
    try:
        proposals = await asyncio.to_thread(_extract_fn, artifact_name, founder_id)
    except Exception as exc:
        return {"status": "error", "error": True,
                "message": f"document extraction failed: {exc}"[:240]}
    if not proposals:
        return {"status": "error", "error": True,
                "error_code": "no_profile_updates",
                "message": "No supported Founder Profile facts were extracted from the document"}
    for p in proposals:
        p.setdefault("id", uuid.uuid4().hex[:12])
        p.setdefault("status", "PENDING")
    ingestion_id = await firestore.create_ingestion(
        founder_id=founder_id, source_type=source_type, source_ref=ref,
        artifact=artifact_name, proposed_updates=proposals,
    )
    return {
        "status": "success",
        "ingestion_id": ingestion_id,
        "proposed_count": len(proposals),
        "summary": f"extracted {len(proposals)} proposed updates from {ref}",
    }


async def propose_profile_updates(founder_id: str, ingestion_id: str,
                                  limit: int = 8) -> dict:
    ingestion = await firestore.get_ingestion(ingestion_id)
    if not ingestion:
        return {"status": "error", "error": True, "message": f"ingestion {ingestion_id} not found"}
    if ingestion.get("founder_id") != founder_id:
        return {"status": "error", "error": True, "message": f"ingestion {ingestion_id} not found"}
    pending = [p for p in ingestion["proposed_updates"] if p["status"] == "PENDING"]
    return {"status": "success", "ingestion_id": ingestion_id,
            "proposals": pending[:limit], "remaining": max(0, len(pending) - limit)}


async def confirm_profile_updates(founder_id: str, ingestion_id: str,
                                  approved: list[str], rejected: list[str],
                                  rejection_reasons: list[str]) -> dict:
    """Approved → profile writes (evidence = doc citation). Rejected → feedback
    rows with verbatim reasons (distiller queue)."""
    ingestion = await firestore.get_ingestion(ingestion_id)
    if not ingestion:
        return {"status": "error", "error": True, "message": f"ingestion {ingestion_id} not found"}
    if ingestion.get("founder_id") != founder_id:
        return {"status": "error", "error": True, "message": f"ingestion {ingestion_id} not found"}
    artifact = await firestore.get_artifact(ingestion_id) or {}
    applied = rejected_count = 0
    reason_map = dict(zip(rejected, rejection_reasons))
    for proposal in ingestion["proposed_updates"]:
        if proposal["id"] in approved and proposal["status"] == "PENDING":
            citation = proposal.get("citation") if isinstance(proposal.get("citation"), dict) else {}
            evidence = proposal.get("evidence_quote", f"ingestion {ingestion_id}")
            if citation:
                evidence = f"{evidence} [{_citation_label(citation)}]"
            await firestore.apply_profile_update(
                founder_id, proposal["kind"], proposal["payload"],
                evidence=evidence,
                idempotency_key=(
                    f"ingestion:{ingestion_id}:proposal:{proposal['id']}"),
                verification_level="FOUNDER_CONFIRMED",
                provenance={
                    "source": artifact.get("source_type")
                    or ingestion.get("source_type") or "upload",
                    "source_id": ingestion_id,
                    "source_grant_id": artifact.get("source_grant_id"),
                    "source_version": artifact.get("provider_version"),
                    "citation": citation,
                    "source_available": True,
                },
            )
            proposal["status"] = "APPROVED"
            proposal["verification_level"] = "FOUNDER_CONFIRMED"
            applied += 1
        elif proposal["id"] in rejected and proposal["status"] == "PENDING":
            await firestore.create_feedback(
                founder_id=founder_id, application_id="", section_id="",
                feedback_type="reject",
                original=str(proposal["payload"]),
                reason=reason_map.get(proposal["id"], "rejected during ingestion review"),
                edited_text="",
            )
            proposal["status"] = "REJECTED"
            rejected_count += 1
    remaining = any(proposal.get("status") == "PENDING"
                    for proposal in ingestion["proposed_updates"])
    await firestore.update_ingestion(
        ingestion_id, proposed_updates=ingestion["proposed_updates"],
        status="NEEDS_FOUNDER" if remaining else "CONFIRMED")
    return {"status": "success", "applied": applied, "rejected": rejected_count}
