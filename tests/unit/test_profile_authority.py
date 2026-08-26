"""Evidence authority, verification levels, and high-impact code gates."""

from __future__ import annotations

import hashlib

from services import document_ingestion, firestore, profile_authority, profile_service


def _seed_cited(fake_store, *, kind="fact_update", payload=None,
                quote="Acme serves 42 clinics.", source_grant_id=None):
    ingestion_id = "a" * 32
    chunk_id = "chunk_0000_source"
    citation = {
        "artifact_id": ingestion_id, "chunk_id": chunk_id,
        "locator": {"page": 1}, "quote": quote,
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
    }
    fake_store.artifacts[ingestion_id] = {
        "id": ingestion_id, "founder_id": "f1", "session_id": "s1",
        "scope": "profile", "authority": "profile_candidate",
        "source_type": "google_drive" if source_grant_id else "upload",
        "source_grant_id": source_grant_id, "provider_version": "7",
    }
    fake_store.artifact_chunks[ingestion_id] = [{
        "id": chunk_id, "content": quote, "locator": {"page": 1},
    }]
    fake_store.ingestions[ingestion_id] = {
        "id": ingestion_id, "founder_id": "f1", "source_type": "upload",
        "status": "INDEXING", "proposed_updates": [{
            "id": "p1", "kind": kind, "payload": payload or {"customers": 42},
            "evidence_quote": quote, "citation": citation,
            "confidence": "high", "status": "PENDING",
        }],
    }
    return ingestion_id


async def test_exact_citation_auto_applies_as_evidence_verified(fake_store):
    ingestion_id = _seed_cited(fake_store)
    result = await profile_service.auto_apply_profile_updates("f1", ingestion_id)
    assert result["auto_applied"] == 1
    profile = await profile_service.get_profile("f1")
    provenance = profile["fact_provenance"]["customers"]
    assert provenance["verification_level"] == "EVIDENCE_VERIFIED"
    assert provenance["source_id"] == ingestion_id
    assert provenance["citation"]["quote_sha256"] == hashlib.sha256(
        b"Acme serves 42 clinics.").hexdigest()


async def test_citation_hash_mutation_and_unsupported_kinds_never_auto_apply(fake_store):
    ingestion_id = _seed_cited(fake_store)
    fake_store.ingestions[ingestion_id]["proposed_updates"][0]["citation"][
        "quote_sha256"] = "0" * 64
    bad_hash = await profile_service.auto_apply_profile_updates("f1", ingestion_id)
    assert bad_hash["auto_applied"] == 0
    assert "hash-matching" in bad_hash["needs_founder"][0]["reason"]

    fake_store.ingestions.clear()
    fake_store.artifacts.clear()
    fake_store.artifact_chunks.clear()
    voice_id = _seed_cited(
        fake_store, kind="voice_rule", payload={"rule": "Avoid buzzwords"})
    voice = await profile_service.auto_apply_profile_updates("f1", voice_id)
    assert voice["auto_applied"] == 0
    assert "supersession semantics" in voice["needs_founder"][0]["reason"]


async def test_revoked_source_remains_history_but_requires_revalidation(fake_store):
    connection = await firestore.upsert_data_connection(
        "f1", "drive", account_ref="default", status="CONNECTED")
    grant = await firestore.create_source_grant(
        "f1", connection["connection_id"], "drive-file-1",
        display_name="Deck", allowed_ingestion_scopes=["profile"])
    ingestion_id = _seed_cited(
        fake_store, payload={"customer_count": 42},
        source_grant_id=grant["source_grant_id"])
    applied = await profile_service.auto_apply_profile_updates("f1", ingestion_id)
    assert applied["auto_applied"] == 1

    await firestore.revoke_source_grant("f1", grant["source_grant_id"])
    profile = await profile_service.get_profile("f1")
    provenance = profile["fact_provenance"]["customer_count"]
    assert profile["facts"]["customer_count"] == 42
    assert provenance["source_available"] is False
    assert provenance["requires_revalidation"] is True


async def test_founder_correction_supersedes_without_erasing_history(fake_store):
    await firestore.apply_profile_update(
        "f1", "fact_update", {"prior_award_received": "$10k"}, "deck",
        verification_level="EVIDENCE_VERIFIED")
    await firestore.apply_profile_update(
        "f1", "fact_update", {"prior_award_received": "$25k"}, "founder correction",
        verification_level="FOUNDER_CONFIRMED")
    profile = await profile_service.get_profile("f1")
    assert profile["facts"]["prior_award_received"] == "$25k"
    assert profile["fact_provenance"]["prior_award_received"][
        "verification_level"] == "FOUNDER_CONFIRMED"
    assert profile["fact_history"][0]["value"] == "$10k"
    assert profile["fact_history"][0]["verification_level"] == "SUPERSEDED"


def test_high_impact_fact_requires_confirmation_or_exact_payload_approval():
    profile = {
        "facts": {"prior_award_received": "$25k"},
        "fact_provenance": {"prior_award_received": {
            "verification_level": "EVIDENCE_VERIFIED", "source_available": True,
        }},
    }
    payload = {"body": "We previously received $25k in awards."}
    blocked = profile_authority.consequential_use_gate(
        profile, payload, exact_founder_authorization=False)
    assert blocked["error_code"] == "high_impact_confirmation_required"
    allowed = profile_authority.consequential_use_gate(
        profile, payload, exact_founder_authorization=True)
    assert allowed["status"] == "success"


async def test_retrieval_refuses_a_success_shaped_incomplete_citation(fake_store):
    artifact_id = "c" * 32
    fake_store.artifacts[artifact_id] = {
        "id": artifact_id, "founder_id": "f1", "session_id": "s1",
        "status": "READY", "source_type": "upload", "source_ref": "deck.txt",
    }
    fake_store.artifact_chunks[artifact_id] = [{
        "id": "chunk-1", "content": "Acme serves 42 clinics.",
        "locator": {}, "ordinal": 0,
    }]
    result = await document_ingestion.search_attachments(
        founder_id="f1", session_id="s1", attachment_refs=[artifact_id],
        query="clinics")
    assert result["status"] == "error"
    assert result["error_code"] == "incomplete_evidence_citation"
    assert "results" not in result
