"""Profile service (docs/06) — the Founder Profile: facts, voice rules,
canonical answers, rejection history, decision patterns. Plus document
ingestion: autonomous by default (auto-apply confident, non-conflicting
proposals), founder asked only about conflicts and low-confidence items.

All profile writes go through apply_update (versioned, audited).
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from services import firestore

# Injectable for tests / offline dev: (artifact_name, founder_id) -> proposals list
ExtractFn = Callable[[str, str], list[dict[str, Any]]]
_extract_fn: ExtractFn | None = None


def set_extract_fn(fn: ExtractFn) -> None:
    """Tests and offline dev inject a fake extractor; prod wires the Gemini
    document-understanding call (Day 3 live check, needs ADC)."""
    global _extract_fn
    _extract_fn = fn


async def get_profile(founder_id: str) -> dict[str, Any]:
    return await firestore.get_profile(founder_id)


async def record_answer(founder_id: str, question_key: str, question: str, answer: str) -> dict:
    """Interview answers are durable knowledge: profile facts + audit."""
    profile = await firestore.get_profile(founder_id)
    facts = dict(profile.get("facts", {}))
    facts[question_key] = answer
    version = await firestore.apply_profile_update(
        founder_id, "fact_update", {question_key: answer},
        evidence=f"interview answer: {question[:160]}",
    )
    return {"status": "success", "question_key": question_key, "profile_version": version}


async def get_voice_rules(founder_id: str) -> list[dict[str, Any]]:
    profile = await firestore.get_profile(founder_id)
    rules = [r for r in profile.get("voice_rules", []) if r.get("active", True)]
    return sorted(rules, key=lambda r: r.get("created_at", ""), reverse=True)


async def get_relevant_answers(founder_id: str, section_key: str, limit: int = 5) -> list[dict[str, Any]]:
    """Deterministic retrieval (docs/06 §retrieval): exact question_key match,
    then tag overlap, then recency. Max `limit`."""
    profile = await firestore.get_profile(founder_id)
    answers = profile.get("canonical_answers", [])
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


async def auto_apply_profile_updates(founder_id: str, ingestion_id: str) -> dict:
    """Autonomous ingestion review (docs/06 §bootstrap): apply every confident,
    non-conflicting proposal immediately (versioned, evidenced, audited).
    Conflicts and low-confidence items stay PENDING in `needs_founder` — the
    agent asks the founder about exactly those, nothing else."""
    ingestion = await firestore.get_ingestion(ingestion_id)
    if not ingestion:
        return {"status": "error", "error": True, "message": f"ingestion {ingestion_id} not found"}
    profile = await firestore.get_profile(founder_id)
    applied, needs_founder = [], []
    for p in ingestion["proposed_updates"]:
        if p["status"] != "PENDING":
            continue
        conflict = _conflict(profile, p)
        if conflict or p.get("confidence") == "low":
            needs_founder.append({
                "id": p["id"], "kind": p["kind"], "payload": p["payload"],
                "evidence_quote": p.get("evidence_quote", ""),
                "reason": conflict or "low-confidence extraction (inferred, not stated)",
            })
            continue
        await firestore.apply_profile_update(
            founder_id, p["kind"], p["payload"],
            p.get("evidence_quote", f"ingestion {ingestion_id}"),
        )
        p["status"] = "APPROVED"
        p["auto_applied"] = True
        applied.append(p["id"])
        # Re-fetch so later proposals conflict-check against earlier applies.
        profile = await firestore.get_profile(founder_id)
    remaining = [p for p in ingestion["proposed_updates"] if p["status"] == "PENDING"]
    await firestore.update_ingestion(
        ingestion_id, proposed_updates=ingestion["proposed_updates"],
        status="NEEDS_FOUNDER" if remaining else "CONFIRMED")
    return {"status": "success", "ingestion_id": ingestion_id,
            "auto_applied": len(applied), "needs_founder": needs_founder}

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
    proposals = _extract_fn(artifact_name, founder_id)
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


async def propose_profile_updates(ingestion_id: str, limit: int = 8) -> dict:
    ingestion = await firestore.get_ingestion(ingestion_id)
    if not ingestion:
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
    applied = rejected_count = 0
    reason_map = dict(zip(rejected, rejection_reasons))
    for proposal in ingestion["proposed_updates"]:
        if proposal["id"] in approved and proposal["status"] == "PENDING":
            await firestore.apply_profile_update(
                founder_id, proposal["kind"], proposal["payload"],
                evidence=proposal.get("evidence_quote", f"ingestion {ingestion_id}"),
            )
            proposal["status"] = "APPROVED"
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
    await firestore.update_ingestion(ingestion_id, proposed_updates=ingestion["proposed_updates"],
                                     status="CONFIRMED")
    return {"status": "success", "applied": applied, "rejected": rejected_count}
