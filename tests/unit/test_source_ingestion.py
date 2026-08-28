"""WI-3 shared source-ingestion and orphan reconciliation tests."""

from __future__ import annotations

import pytest

from services import drive_adapter, firestore, session_resources, source_ingestion, storage

pytestmark = pytest.mark.asyncio


async def _ready_drive_grant(founder="founder", source="file-1"):
    connection = await firestore.upsert_data_connection(
        founder, "drive", account_ref="default",
        auth_kind="google_oauth", status="CONNECTED")
    grant = await firestore.create_source_grant(
        founder, connection["connection_id"], source,
        display_name="Deck.txt",
        allowed_ingestion_scopes=["profile", "reference_only"])
    return connection, grant


async def _registered(**kwargs):
    return {"status": "success", "resource_id": "resource-1",
            "link_id": "link-1"}


async def _queued(_ingestion_id, **_kwargs):
    return {"status": "success", "ingestion_status": "QUEUED"}


async def test_upload_and_drive_share_metadata_and_provenance_contract(
        fake_store, monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_gcs", lambda: None)
    monkeypatch.setattr(session_resources, "register_session_resource", _registered)
    monkeypatch.setattr("services.document_ingestion.process_ingestion", _queued)
    connection, grant = await _ready_drive_grant()
    monkeypatch.setattr(
        drive_adapter, "fetch_file_bytes",
            lambda _source, _limit, _workspace_id: {
            "status": "success", "data": b"drive text",
            "detected_name": "Deck.txt", "declared_content_type": "text/plain",
            "provider_content_type": "text/plain", "provider_version": "7",
            "provider_modified_at": "2026-08-26T00:00:00Z",
        })

    upload = await source_ingestion.register_source_ingestion(
        founder_id="founder", session_id="session-1", source_type="upload",
        source_grant_id=None, source_ref="Notes.txt", display_name="Notes.txt",
        data=b"upload text", declared_content_type="text/plain",
        scope="reference_only", occurrence_key="upload:1",
        session_verified=True)
    drive = await source_ingestion.register_source_ingestion(
        founder_id="founder", session_id="session-1",
        source_type="google_drive", source_grant_id=grant["source_grant_id"],
        source_ref=grant["source_grant_id"], display_name="ignored",
        data=None, declared_content_type="application/octet-stream",
        scope="profile", occurrence_key="drive:1", session_verified=True)

    assert upload["status"] == drive["status"] == "success"
    for result in (upload, drive):
        artifact = fake_store.artifacts[result["attachment_ref"]]
        ingestion = fake_store.ingestions[result["attachment_ref"]]
        assert artifact["status"] == ingestion["status"] == "QUEUED"
        assert artifact["sha256"] == ingestion["sha256"]
        assert artifact["storage_name"].startswith("companydoc_founder_")
    drive_row = fake_store.artifacts[drive["attachment_ref"]]
    assert drive_row["connection_id"] == connection["connection_id"]
    assert drive_row["source_grant_id"] == grant["source_grant_id"]
    assert drive_row["provider_source_id"] == "file-1"
    assert drive_row["provider_version"] == "7"
    assert drive_row["authority"] == "profile_candidate"


async def test_revoked_foreign_and_unselected_grants_never_fetch(
        fake_store, monkeypatch):
    _connection, grant = await _ready_drive_grant()
    await firestore.revoke_source_grant("founder", grant["source_grant_id"])
    calls = 0

    def _must_not_fetch(*_args):
        nonlocal calls
        calls += 1
        raise AssertionError("provider fetch crossed a revoked grant")

    monkeypatch.setattr(drive_adapter, "fetch_file_bytes", _must_not_fetch)
    base = dict(
        session_id="session-1", source_type="google_drive",
        source_ref="opaque", display_name="ignored", data=None,
        declared_content_type="application/octet-stream", scope="profile",
        occurrence_key="drive:refused", session_verified=True)
    revoked = await source_ingestion.register_source_ingestion(
        founder_id="founder", source_grant_id=grant["source_grant_id"], **base)
    foreign = await source_ingestion.register_source_ingestion(
        founder_id="other", source_grant_id=grant["source_grant_id"], **base)
    unknown = await source_ingestion.register_source_ingestion(
        founder_id="founder", source_grant_id="sg_unknown", **base)
    assert {revoked["error_code"], foreign["error_code"], unknown["error_code"]} == {
        "source_not_selected"}
    assert calls == 0


async def test_provenance_failure_records_and_reconciles_orphan(
        fake_store, monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_gcs", lambda: None)

    async def _provenance_failure(**_kwargs):
        return {"status": "error", "error": True, "message": "store unavailable"}

    monkeypatch.setattr(session_resources, "register_session_resource",
                        _provenance_failure)
    result = await source_ingestion.register_source_ingestion(
        founder_id="founder", session_id="session-1", source_type="upload",
        source_grant_id=None, source_ref="Notes.txt", display_name="Notes.txt",
        data=b"orphan text", declared_content_type="text/plain",
        scope="reference_only", occurrence_key="upload:orphan",
        session_verified=True)

    assert result["error_code"] == "provenance_failed"
    markers = [name for name in storage.list_artifacts()
               if name.startswith(".ingestion_orphan_")]
    assert len(markers) == 1
    artifact = next(iter(fake_store.artifacts.values()))
    assert artifact["status"] == "FAILED"
    assert storage.exists(artifact["storage_name"])

    reconciled = await source_ingestion.reconcile_orphans()
    assert reconciled == {"status": "success", "scanned": 1,
                          "deleted": 1, "adopted": 0, "failed": 0}
    assert not storage.exists(artifact["storage_name"])
    assert not storage.exists(markers[0])


async def test_occurrence_replay_returns_one_receipt_and_conflict_refuses(
        fake_store, monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_gcs", lambda: None)
    monkeypatch.setattr(session_resources, "register_session_resource", _registered)
    monkeypatch.setattr("services.document_ingestion.process_ingestion", _queued)
    args = dict(
        founder_id="founder", session_id="session-1", source_type="upload",
        source_grant_id=None, source_ref="Notes.txt", display_name="Notes.txt",
        data=b"same bytes", declared_content_type="text/plain",
        scope="reference_only", occurrence_key="upload:stable",
        session_verified=True)

    first = await source_ingestion.register_source_ingestion(**args)
    replay = await source_ingestion.register_source_ingestion(**args)
    conflict = await source_ingestion.register_source_ingestion(
        **{**args, "data": b"different bytes"})

    assert replay["attachment_ref"] == first["attachment_ref"]
    assert replay["duplicate"] is True
    assert conflict["error_code"] == "version_conflict"
    assert len(fake_store.artifacts) == len(fake_store.ingestions) == 1
