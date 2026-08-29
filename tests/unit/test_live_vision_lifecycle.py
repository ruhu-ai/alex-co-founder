"""Registry, TTL, deletion, and content-minimization release gates."""

from __future__ import annotations

import json
from pathlib import Path

from services import data_lifecycle, firestore

ROOT = Path(__file__).resolve().parents[2]


def test_new_records_are_registered_and_ttl_manifest_is_complete():
    required = {"media_consent_grants", "live_media_shares"}
    assert required.issubset(firestore.REGISTERED_COLLECTIONS)
    assert required.issubset(data_lifecycle.TOP_LEVEL_LIFECYCLE)
    assert "image_observations" in firestore.REGISTERED_COLLECTIONS
    assert "image_observations" in data_lifecycle.SUBCOLLECTION_LIFECYCLE

    manifest = json.loads((ROOT / "infra/firestore.ttl.json").read_text())
    policies = {(row["collectionGroup"], row["fieldPath"], row["ttl"])
                for row in manifest["policies"]}
    assert {
        ("media_consent_grants", "retention_expires_at", True),
        ("live_media_shares", "retention_expires_at", True),
    }.issubset(policies)


def test_live_metadata_writers_have_no_frame_or_content_fields():
    source = (ROOT / "services/live_media.py").read_text()
    start = source.index("await firestore.create_live_media_share")
    end = source.index("return {\"type\": \"media.started\"", start)
    persisted = source[start:end]
    assert '"data"' not in persisted
    assert '"frame"' not in persisted
    assert '"content"' not in persisted
    assert '"effective_profile"' in persisted


def test_explicit_attachment_delete_covers_original_derivative_and_indexes():
    main = (ROOT / "app/main.py").read_text()
    route = main[main.index('@app.delete("/api/v1/ingestions/{attachment_ref}")'):
                 main.index('@app.get("/api/v1/ingestions/{attachment_ref}/profile-review")')]
    assert 'artifact.get("storage_name")' in route
    assert 'artifact.get("normalized_storage_name")' in route
    assert "delete_session_artifact_records" in route
    assert "tombstone_session_resource_links" in route
    assert "delete_resource_projection" in route
    assert 'command_type="ingestion.delete"' in route
