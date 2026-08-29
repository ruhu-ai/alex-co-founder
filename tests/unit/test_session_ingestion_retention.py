"""Document 39 ceiling for unsaved, session-only imports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services import firestore, session_ingestion_retention, session_resources, storage

pytestmark = pytest.mark.asyncio


def _row(*, expires_at: datetime, scope: str = "reference_only") -> dict:
    return {
        "artifact_id": "artifact-a",
        "founder_id": "workspace-a",
        "session_id": "session-a",
        "scope": scope,
        "retention_policy": "session" if scope == "reference_only" else "profile",
        "retention_expires_at": expires_at.isoformat(),
        "storage_name": "workspace-a/session-a/source.bin",
    }


async def test_reference_only_expiry_is_byte_first_and_idempotent(monkeypatch):
    now = datetime.now(timezone.utc)
    artifact = _row(expires_at=now - timedelta(seconds=1))
    deleted_bytes: list[str] = []
    operations: list[str] = []

    async def get_artifact(_ingestion_id):
        return artifact

    async def get_ingestion(_ingestion_id):
        return {**artifact, "artifact": artifact["storage_name"]}

    async def delete_records(*_args):
        operations.append("records")

    async def tombstone_links(*_args):
        operations.append("links")

    async def delete_projection(*_args):
        operations.append("projection")

    async def audit(*_args):
        operations.append("audit")

    def delete_artifact(name):
        deleted_bytes.append(name)
        operations.append("bytes")

    monkeypatch.setattr(firestore, "get_artifact", get_artifact)
    monkeypatch.setattr(firestore, "get_ingestion", get_ingestion)
    monkeypatch.setattr(firestore, "delete_session_artifact_records", delete_records)
    monkeypatch.setattr(firestore, "tombstone_session_resource_links", tombstone_links)
    monkeypatch.setattr(firestore, "delete_resource_projection", delete_projection)
    monkeypatch.setattr(firestore, "audit", audit)
    monkeypatch.setattr(storage, "delete_artifact", delete_artifact)
    monkeypatch.setattr(
        session_resources, "resource_id_for", lambda *_args: "resource-a")

    result = await session_ingestion_retention.expire_reference_only(
        "artifact-a", now=now)

    assert result == {
        "status": "success", "duplicate": False, "deleted": True,
        "ingestion_id": "artifact-a",
    }
    assert deleted_bytes == [artifact["storage_name"]]
    assert operations[0] == "bytes"
    assert operations[1:] == ["records", "links", "projection", "audit"]

    async def missing(_ingestion_id):
        return None
    monkeypatch.setattr(firestore, "get_artifact", missing)
    monkeypatch.setattr(firestore, "get_ingestion", missing)
    replay = await session_ingestion_retention.expire_reference_only("artifact-a")
    assert replay == {"status": "success", "duplicate": True, "deleted": False}


async def test_expiry_refuses_early_or_profile_deletion(monkeypatch):
    now = datetime.now(timezone.utc)
    current = _row(expires_at=now + timedelta(hours=23, minutes=59))

    async def get_artifact(_ingestion_id):
        return current

    async def no_ingestion(_ingestion_id):
        return None

    monkeypatch.setattr(firestore, "get_artifact", get_artifact)
    monkeypatch.setattr(firestore, "get_ingestion", no_ingestion)
    early = await session_ingestion_retention.expire_reference_only(
        "artifact-a", now=now)
    assert early["error_code"] == "retention_not_due"

    current = _row(expires_at=now - timedelta(days=1), scope="profile")
    governed = await session_ingestion_retention.expire_reference_only(
        "artifact-a", now=now)
    assert governed["error_code"] == "retention_scope_forbidden"
