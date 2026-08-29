"""Safe founder session deletion: ownership, sharing, and UI boundary."""

from __future__ import annotations

import pathlib

import pytest

from services import session_deletion as deletion


class _SessionService:
    def __init__(self, exists: bool = True, *, state=None):
        self.session = type("Session", (), {"state": state or {}})() \
            if exists else None
        self.deleted: list[tuple[str, str, str]] = []

    async def get_session(self, *, app_name, user_id, session_id):
        return self.session

    async def delete_session(self, *, app_name, user_id, session_id):
        self.deleted.append((app_name, user_id, session_id))
        self.session = None


def _base_wiring(monkeypatch, *, links, catalog=None):
    writes = {"catalog": [], "audit": [], "stopped": [], "live_media": []}

    async def _catalog(_sid):
        return catalog or {"founder_id": "founder", "status": "active"}

    async def _links(_founder, _session, *, limit=250, start_after=None):
        return list(links) if start_after is None else []

    async def _upsert(sid, fields):
        writes["catalog"].append((sid, dict(fields)))

    async def _stop(key, actor, run_id=None):
        writes["stopped"].append((key, actor))
        return {"status": "success", "already_closed": True}

    async def _tombstone(_founder, _session):
        return len(links)

    async def _delete_live_media(founder, session):
        writes["live_media"].append((founder, session))
        return 0

    async def _audit(*args, **kwargs):
        writes["audit"].append((args, kwargs))
        return "audit-1"

    monkeypatch.setattr(deletion.firestore, "get_session_catalog", _catalog)
    monkeypatch.setattr(deletion.firestore, "list_session_links", _links)
    monkeypatch.setattr(deletion.firestore, "upsert_session_catalog", _upsert)
    monkeypatch.setattr(deletion.firestore, "tombstone_session_links", _tombstone)
    monkeypatch.setattr(
        deletion.firestore, "delete_session_live_media_metadata",
        _delete_live_media)
    monkeypatch.setattr(deletion.firestore, "audit", _audit)
    monkeypatch.setattr(deletion.firestore, "now_iso", lambda: "2026-08-26T00:00:00+00:00")
    monkeypatch.setattr(deletion.browser_service, "stop_browser", _stop)
    return writes


@pytest.mark.asyncio
async def test_deletes_only_exclusive_session_files(monkeypatch):
    links = [
        {"resource_id": "r-art", "resource_type": "artifact"},
        {"resource_id": "r-doc", "resource_type": "document"},
        {"resource_id": "r-app", "resource_type": "application"},
    ]
    writes = _base_wiring(monkeypatch, links=links)
    resources = {
        "r-art": {"founder_id": "founder", "canonical_ref": {"id": "a-1"}},
        "r-doc": {"founder_id": "founder", "canonical_ref": {"id": "d-1"}},
    }
    deleted = {"blobs": [], "artifacts": [], "documents": [], "indexes": []}

    async def _not_shared(*_args, **_kwargs):
        return False

    async def _resource(resource_id):
        return resources.get(resource_id)

    async def _artifact(_artifact_id):
        return {"founder_id": "founder", "session_id": "s-1",
                "retention_policy": "session", "storage_name": "upload.pdf"}

    async def _document(_document_id):
        return {"founder_id": "founder", "session_id": "s-1",
                "artifact_name": "draft.docx"}

    async def _delete_artifact(founder, session, artifact_id):
        deleted["artifacts"].append((founder, session, artifact_id))
        return True

    async def _delete_document(founder, session, document_id):
        deleted["documents"].append((founder, session, document_id))
        return True

    async def _delete_index(founder, resource_id):
        deleted["indexes"].append((founder, resource_id))
        return True

    monkeypatch.setattr(deletion.firestore, "resource_has_other_session_link", _not_shared)
    monkeypatch.setattr(deletion.firestore, "get_resource", _resource)
    monkeypatch.setattr(deletion.firestore, "get_artifact", _artifact)
    monkeypatch.setattr(deletion.firestore, "get_document", _document)
    monkeypatch.setattr(deletion.firestore, "delete_session_artifact_records", _delete_artifact)
    monkeypatch.setattr(deletion.firestore, "delete_session_document_record", _delete_document)
    monkeypatch.setattr(deletion.firestore, "delete_resource_projection", _delete_index)
    monkeypatch.setattr(deletion.storage, "delete_artifact", deleted["blobs"].append)

    sessions = _SessionService()
    result = await deletion.delete_session(
        founder_id="founder", session_id="s-1", app_name="co_founder",
        session_service=sessions)

    assert result == {
        "status": "success", "session_id": "s-1", "already_deleted": False,
        "tombstoned_links": 3, "deleted_files": 2, "retained_items": 1,
        "cleanup_errors": [],
    }
    assert deleted["blobs"] == ["upload.pdf", "draft.docx"]
    assert deleted["artifacts"] == [("founder", "s-1", "a-1")]
    assert deleted["documents"] == [("founder", "s-1", "d-1")]
    assert deleted["indexes"] == [("founder", "r-art"), ("founder", "r-doc")]
    assert sessions.deleted == [("co_founder", "founder", "s-1")]
    assert writes["catalog"][0][1]["status"] == "deleting"
    assert writes["catalog"][-1][1]["status"] == "deleted"
    assert writes["audit"]


@pytest.mark.asyncio
async def test_shared_and_profile_artifacts_are_retained(monkeypatch):
    links = [
        {"resource_id": "r-shared", "resource_type": "artifact"},
        {"resource_id": "r-profile", "resource_type": "artifact"},
    ]
    _base_wiring(monkeypatch, links=links)
    deleted_blobs: list[str] = []

    async def _shared(_founder, resource_id, *, excluding_session_id):
        return resource_id == "r-shared"

    async def _resource(resource_id):
        return {"founder_id": "founder", "canonical_ref": {"id": resource_id}}

    async def _artifact(_artifact_id):
        return {"founder_id": "founder", "session_id": "s-1",
                "retention_policy": "profile", "storage_name": "deck.pdf"}

    monkeypatch.setattr(deletion.firestore, "resource_has_other_session_link", _shared)
    monkeypatch.setattr(deletion.firestore, "get_resource", _resource)
    monkeypatch.setattr(deletion.firestore, "get_artifact", _artifact)
    monkeypatch.setattr(deletion.storage, "delete_artifact", deleted_blobs.append)

    result = await deletion.delete_session(
        founder_id="founder", session_id="s-1", app_name="co_founder",
        session_service=_SessionService())

    assert result["deleted_files"] == 0
    assert result["retained_items"] == 2
    assert deleted_blobs == []


@pytest.mark.asyncio
async def test_cleanup_failure_is_explicit_and_does_not_fake_full_success(monkeypatch):
    links = [{"resource_id": "r-art", "resource_type": "artifact"}]
    writes = _base_wiring(monkeypatch, links=links)

    async def _not_shared(*_args, **_kwargs):
        return False

    async def _resource(_resource_id):
        return {"founder_id": "founder", "canonical_ref": {"id": "a-1"}}

    async def _artifact(_artifact_id):
        return {"founder_id": "founder", "session_id": "s-1",
                "retention_policy": "session", "storage_name": "upload.pdf"}

    def _delete_blob(_name):
        raise OSError("storage unavailable")

    monkeypatch.setattr(deletion.firestore, "resource_has_other_session_link", _not_shared)
    monkeypatch.setattr(deletion.firestore, "get_resource", _resource)
    monkeypatch.setattr(deletion.firestore, "get_artifact", _artifact)
    monkeypatch.setattr(deletion.storage, "delete_artifact", _delete_blob)
    sessions = _SessionService()

    result = await deletion.delete_session(
        founder_id="founder", session_id="s-1", app_name="co_founder",
        session_service=sessions)

    assert result["status"] == "success"
    assert result["deleted_files"] == 0
    assert result["cleanup_errors"] == ["file cleanup failed for r-art"]
    assert sessions.deleted
    assert writes["audit"][0][0][3] == "partial"


@pytest.mark.asyncio
async def test_deleted_session_is_idempotent_but_unknown_is_not_found(monkeypatch):
    _base_wiring(
        monkeypatch, links=[],
        catalog={"founder_id": "founder", "status": "deleted"})
    result = await deletion.delete_session(
        founder_id="founder", session_id="s-1", app_name="co_founder",
        session_service=_SessionService(exists=False))
    assert result["status"] == "success"
    assert result["already_deleted"] is True

    async def _missing(_sid):
        return None

    monkeypatch.setattr(deletion.firestore, "get_session_catalog", _missing)
    result = await deletion.delete_session(
        founder_id="founder", session_id="unknown", app_name="co_founder",
        session_service=_SessionService(exists=False))
    assert result == {"status": "error", "error": True, "message": "not found"}


@pytest.mark.asyncio
async def test_private_session_deletion_performs_zero_optional_memory_io(monkeypatch):
    _base_wiring(monkeypatch, links=[])

    def memory_service_must_not_load():
        raise AssertionError("private session touched optional memory")

    monkeypatch.setattr(
        "services.durable_memory.configured_service", memory_service_must_not_load)
    result = await deletion.delete_session(
        founder_id="founder", session_id="s-private", app_name="co_founder",
        session_service=_SessionService(
            state={"platform:memory_mode": "PRIVATE"}),
        memory_principal=object(), client_request_id="delete-private")
    assert result["status"] == "success"


def test_delete_route_requires_confirmation_and_is_registered():
    source = (pathlib.Path(__file__).resolve().parents[2]
              / "app/main.py").read_text(encoding="utf-8")
    assert '@app.delete("/api/sessions/{session_id}")' in source
    assert "if not payload.confirm:" in source
    assert "explicit confirmation is required" in source
