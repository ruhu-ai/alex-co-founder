"""Shared upload/Drive knowledge-source registration (docs/24 §7)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from services import (
    data_source_metrics,
    document_ingestion,
    firestore,
    image_ingestion,
    session_resources,
    storage,
)

MAX_SOURCE_BYTES = 20 * 1024 * 1024
_ORPHAN_PREFIX = ".ingestion_orphan_"


def _error(code: str, message: str, http_status: int) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}


def is_image_candidate(data: bytes, declared_content_type: str) -> bool:
    """Return whether bytes/declaration select the explicit-still validator."""
    declared = str(declared_content_type or "").lower()
    return (declared.startswith("image/") or data.startswith(b"\xff\xd8\xff")
            or data.startswith(b"\x89PNG\r\n\x1a\n")
            or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def _safe_name(value: str, fallback: str = "document") -> str:
    name = os.path.basename(str(value or ""))
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).replace("..", "_")
    return (name.strip("._") or fallback)[:120]


def _ingestion_id(founder_id: str, session_id: str,
                  occurrence_key: str) -> str:
    return hashlib.sha256(
        f"source-ingestion:v1{founder_id}{session_id}{occurrence_key}".encode()
    ).hexdigest()[:32]


def _duplicate(existing: dict[str, Any], ingestion_id: str) -> dict[str, Any]:
    return {
        "status": "success", "accepted": True, "duplicate": True,
        "http_status": 202, "attachment_ref": ingestion_id,
        "artifact": existing.get("artifact", ""),
        "source_grant_id": existing.get("source_grant_id"),
        "scope": existing.get("scope"), "authority": existing.get("authority"),
        "ingestion_status": existing.get("status", "QUEUED"),
        "auto_applied": int(existing.get("auto_applied") or 0),
        "needs_founder": int(existing.get("needs_founder_count") or 0),
        "kind": existing.get("kind", "document"),
        "width": existing.get("width"), "height": existing.get("height"),
        "sha256": existing.get("sha256", ""),
    }


def _existing_receipt(existing: dict[str, Any], ingestion_id: str
                      ) -> dict[str, Any]:
    if existing.get("provenance_status") == "COMMITTED":
        return _duplicate(existing, ingestion_id)
    if existing.get("status") == "FAILED":
        return _error("provenance_failed",
                      "The prior registration did not complete.", 503)
    return _error("lease_conflict", "Document registration is still in progress.", 409)


def _record_orphan(storage_name: str, *, founder_id: str, session_id: str,
                   ingestion_id: str | None, reason: str) -> str:
    marker = f"{_ORPHAN_PREFIX}{uuid.uuid4().hex}.json"
    storage.save_text(marker, json.dumps({
        "schema_version": 1, "storage_name": storage_name,
        "founder_id": founder_id, "session_id": session_id,
        "ingestion_id": ingestion_id or "", "reason": reason[:80],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, sort_keys=True, separators=(",", ":")))
    return marker


async def _mark_failed(ingestion_id: str | None, code: str,
                       message: str) -> None:
    if not ingestion_id:
        return
    try:
        await firestore.update_ingestion(
            ingestion_id, status="FAILED", provenance_status="FAILED",
            error_code=code, message=message)
        await firestore.update_artifact(
            ingestion_id, status="FAILED", provenance_status="FAILED",
            error_code=code, message=message)
    except Exception:
        return


async def register_source_ingestion(
        *, founder_id: str, session_id: str, source_type: str,
        source_grant_id: str | None, source_ref: str, display_name: str,
        data: bytes | None, declared_content_type: str, scope: str,
        occurrence_key: str, session_verified: bool = False) -> dict[str, Any]:
    """Validate bytes, persist artifact/provenance, and dispatch exactly once."""
    if not session_verified and not await session_resources.verify_founder_session(
            session_id):
        return _error("owner_mismatch", "not found", 404)
    if scope not in {"profile", "reference_only"}:
        return _error("invalid_contract", "unsupported attachment scope", 400)
    if source_type not in {"upload", "google_drive"}:
        return _error("invalid_contract", "unsupported source type", 400)
    data_source_metrics.record(
        "ingestion_registration", operation=source_type, status="attempted")
    if not occurrence_key or len(occurrence_key) > 256:
        return _error("invalid_contract", "invalid occurrence key", 400)
    registration_id = _ingestion_id(founder_id, session_id, occurrence_key)

    connection_id = None
    provider_source_id = None
    provider_version = None
    provider_modified_at = None
    provider_content_type = None
    grant = None
    if source_type == "upload":
        existing = await firestore.get_ingestion(registration_id)
        if existing:
            expected_hash = hashlib.sha256(bytes(data or b"")).hexdigest()
            if (existing.get("founder_id") != founder_id
                    or existing.get("session_id") != session_id
                    or existing.get("scope") != scope
                    or existing.get("source_type") != source_type
                    or existing.get("source_ref") != source_ref
                    or existing.get("sha256") != expected_hash):
                return _error("version_conflict",
                              "Ingestion occurrence already has different content.", 409)
            return _existing_receipt(existing, registration_id)
    if source_type == "google_drive":
        if not source_grant_id:
            return _error("source_not_selected", "That file is not available.", 404)
        grant = await firestore.get_source_grant(founder_id, source_grant_id)
        if (not grant or grant.get("status") != "ACTIVE"
                or grant.get("connector_id") != "drive"
                or scope not in (grant.get("allowed_ingestion_scopes") or [])):
            return _error("source_not_selected", "That file is not available.", 404)
        connection_id = str(grant.get("connection_id") or "")
        connection = await firestore.get_data_connection(founder_id, connection_id)
        if not connection or connection.get("status") not in {"CONNECTED", "DEGRADED"}:
            return _error("auth_required", "Reconnect Google Drive first.", 409)
        provider_source_id = str(grant.get("provider_source_id") or "")
        if not provider_source_id:
            return _error("source_not_selected", "That file is not available.", 404)
        existing = await firestore.get_ingestion(registration_id)
        if existing:
            if (existing.get("founder_id") != founder_id
                    or existing.get("session_id") != session_id
                    or existing.get("scope") != scope
                    or existing.get("source_type") != source_type
                    or existing.get("source_grant_id") != source_grant_id):
                return _error("version_conflict",
                              "Ingestion occurrence already has different authority.", 409)
            return _existing_receipt(existing, registration_id)
        from services import drive_adapter

        try:
            fetched = await asyncio.wait_for(asyncio.to_thread(
                drive_adapter.fetch_file_bytes, provider_source_id,
                MAX_SOURCE_BYTES, founder_id), timeout=45)
        except TimeoutError:
            fetched = _error("provider_timeout", "Drive fetch timed out.", 504)
        from services import connection_registry

        await connection_registry.record_operation_result(
            founder_id, "drive", "drive_fetch", fetched)
        if fetched.get("status") != "success":
            if fetched.get("error_code") in {"source_missing", "provider_rejected"}:
                await firestore.mark_source_grant_missing(founder_id, source_grant_id)
            return {**fetched, "http_status": int(fetched.get("http_status") or 502)}
        data = fetched["data"]
        display_name = str(fetched.get("detected_name") or grant.get("display_name")
                           or "Drive document")
        declared_content_type = str(
            fetched.get("declared_content_type") or "application/octet-stream")
        provider_content_type = str(fetched.get("provider_content_type") or "") or None
        provider_version = str(fetched.get("provider_version") or "") or None
        provider_modified_at = str(fetched.get("provider_modified_at") or "") or None

    data = bytes(data or b"")
    if not data or len(data) > MAX_SOURCE_BYTES:
        return _error("source_too_large", "Document is empty or exceeds 20 MB.", 413)
    kind = "image" if is_image_candidate(data, declared_content_type) else "document"
    if kind == "image" and source_type != "upload":
        return _error(
            "unsupported_image_source",
            "Add images explicitly from this device or Capture still.", 415)
    if kind == "image" and scope != "reference_only":
        return _error(
            "image_scope_forbidden",
            "Images can only be used in this conversation.", 400)
    checked = (image_ingestion.validate_image(
        data, display_name, declared_content_type) if kind == "image" else
        document_ingestion.validate_upload(data, display_name, declared_content_type))
    if checked.get("status") != "success":
        return {**checked, "http_status": (
            415 if checked.get("ingestion_status") == "UNSUPPORTED" else 400)}

    storage_name = (
        f"explicitstill_{founder_id}_{uuid.uuid4().hex}_{_safe_name(display_name)}"
        if kind == "image" else
        f"companydoc_{founder_id}_{uuid.uuid4().hex}_{_safe_name(display_name)}")
    try:
        await asyncio.to_thread(storage.save_bytes, storage_name, data)
    except Exception:
        return _error("provider_unavailable", "Artifact storage is unavailable.", 503)

    ingestion_id: str | None = None
    try:
        ingestion_id = await firestore.register_artifact_ingestion(
            founder_id=founder_id, session_id=session_id, scope=scope,
            source_type=source_type,
            source_ref=(display_name if source_type == "google_drive" else source_ref),
            storage_name=storage_name,
            declared_content_type=declared_content_type,
            detected_content_type=checked["detected_content_type"],
            detected_extension=checked["detected_extension"],
            size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
            connection_id=connection_id, source_grant_id=source_grant_id,
            provider_source_id=provider_source_id,
            provider_version=provider_version,
            provider_modified_at=provider_modified_at,
            provider_content_type=provider_content_type,
            authority=("profile_candidate" if scope == "profile"
                       else "reference_only"),
            kind=kind,
            image_metadata=({
                "width": checked["width"], "height": checked["height"],
                "pixel_count": checked["pixel_count"],
                "source_sha256": hashlib.sha256(data).hexdigest(),
                "metadata_policy": "PENDING_NORMALIZATION",
            } if kind == "image" else None),
            document_id=registration_id, occurrence_key=occurrence_key)
        stored_artifact = await firestore.get_artifact(ingestion_id)
        if stored_artifact and stored_artifact.get("storage_name") != storage_name:
            try:
                await asyncio.to_thread(storage.delete_artifact, storage_name)
            except Exception:
                await asyncio.to_thread(
                    _record_orphan, storage_name, founder_id=founder_id,
                    session_id=session_id, ingestion_id=None,
                    reason="duplicate_blob")
            stored_ingestion = await firestore.get_ingestion(ingestion_id) or {}
            return _existing_receipt(stored_ingestion, ingestion_id)
        registered = await session_resources.register_session_resource(
            founder_id=founder_id, session_id=session_id,
            resource_type=session_resources.ResourceType.ARTIFACT,
            canonical_id=ingestion_id,
            relationship=session_resources.Relationship.CREATED,
            occurrence_key=occurrence_key,
            producer_kind="api" if not session_verified else "tool",
            producer_id="source_ingestion", producer_output_key="artifact",
            title=display_name[:200],
            summary=(("Explicit still" if kind == "image" else
                      "Drive import" if source_type == "google_drive"
                      else "Uploaded document") + f" · {scope}"),
            status="QUEUED", session_verified=True)
        if registered.get("error"):
            raise RuntimeError("provenance_failed")
        await firestore.update_artifact(
            ingestion_id, provenance_status="COMMITTED")
        await firestore.update_ingestion(
            ingestion_id, provenance_status="COMMITTED")
    except Exception as exc:
        code = "provenance_failed" if ingestion_id else "metadata_failed"
        message = "Document provenance could not be recorded."
        await _mark_failed(ingestion_id, code, message)
        try:
            await asyncio.to_thread(
                _record_orphan, storage_name, founder_id=founder_id,
                session_id=session_id, ingestion_id=ingestion_id,
                reason=type(exc).__name__)
        except Exception:
            pass
        data_source_metrics.record(
            "ingestion_provenance_failure", operation=source_type,
            status="FAILED", error_code=code)
        return _error(code, message, 503)

    # Session-only imports are never optional-memory sources and must not live
    # indefinitely after the founder leaves. Schedule byte-first deletion at
    # the server-authored 24-hour ceiling. If durable scheduling is unavailable
    # in production, delete immediately and refuse the import rather than keep
    # an unbounded sensitive copy.
    stored_artifact = await firestore.get_artifact(ingestion_id)
    if scope == "reference_only" and os.environ.get("K_SERVICE"):
        from services import session_ingestion_retention, task_queue

        expiry = str((stored_artifact or {}).get("retention_expires_at") or "")
        expiry_task = await asyncio.to_thread(
            task_queue.enqueue, "/tasks/expire_session_ingestion",
            {"ingestion_id": ingestion_id}, f"expire-ingestion:{ingestion_id}",
            queue_name="co-founder-timers", schedule_at=expiry)
        if expiry_task.get("error"):
            await session_ingestion_retention.expire_reference_only(
                ingestion_id, force=True)
            return _error(
                "retention_schedule_failed",
                "The session-only import could not be retained safely; it was deleted.",
                503)

    worker_result: dict[str, Any] = {
        "status": "success", "ingestion_status": "QUEUED"}
    if os.environ.get("K_SERVICE"):
        from services import task_queue

        dispatch = await asyncio.to_thread(
            task_queue.enqueue, "/tasks/ingest_document",
            {"ingestion_id": ingestion_id}, f"ingest:{ingestion_id}",
            queue_name="co-founder-discovery-ingestion")
        if dispatch.get("status") != "success":
            message = "Document worker could not be queued."
            await _mark_failed(ingestion_id, "dispatch_failed", message)
            return _error("dispatch_failed", message, 503)
    else:
        worker_result = await (image_ingestion.process_ingestion(ingestion_id)
                               if kind == "image" else
                               document_ingestion.process_ingestion(ingestion_id))

    data_source_metrics.record(
        "ingestion_registration", operation=source_type,
        status=worker_result.get("ingestion_status", "QUEUED"))
    return {
        "status": "success", "accepted": True, "http_status": 202,
        "attachment_ref": ingestion_id, "artifact": storage_name,
        "source_grant_id": source_grant_id, "scope": scope,
        "authority": "profile_candidate" if scope == "profile" else "reference_only",
        "ingestion_status": worker_result.get("ingestion_status", "QUEUED"),
        "auto_applied": int(worker_result.get("auto_applied") or 0),
        "needs_founder": int(worker_result.get("needs_founder") or 0),
        "kind": kind,
        "width": checked.get("width"), "height": checked.get("height"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "resource_id": registered.get("resource_id", ""),
    }


async def reconcile_orphans(limit: int = 100) -> dict[str, Any]:
    """Delete failed/unregistered blobs recorded by the registration boundary."""
    markers = [name for name in await asyncio.to_thread(storage.list_artifacts)
               if name.startswith(_ORPHAN_PREFIX) and name.endswith(".json")]
    deleted = adopted = failed = 0
    for marker in markers[:max(1, min(limit, 500))]:
        try:
            record = json.loads(await asyncio.to_thread(storage.read_text, marker))
            target = str(record.get("storage_name") or "")
            ingestion_id = str(record.get("ingestion_id") or "")
            artifact = await firestore.get_artifact(ingestion_id) \
                if ingestion_id else None
            if artifact and artifact.get("status") not in {"FAILED"}:
                adopted += 1
            else:
                await asyncio.to_thread(storage.delete_artifact, target)
                deleted += 1
            await asyncio.to_thread(storage.delete_artifact, marker)
        except Exception:
            failed += 1
    return {"status": "success", "scanned": min(len(markers), limit),
            "deleted": deleted, "adopted": adopted, "failed": failed}
