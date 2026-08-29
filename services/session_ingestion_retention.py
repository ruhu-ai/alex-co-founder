"""Bounded lifecycle for unsaved/session-only imports (docs/39 M2)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from services import firestore, session_resources, storage


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def expire_reference_only(
        ingestion_id: str, *, force: bool = False,
        now: datetime | None = None) -> dict[str, Any]:
    """Delete bytes and projections for one session-only import.

    Missing records are idempotent success. Profile/governed imports are never
    accepted by this path. A task delivered early refuses so Cloud Tasks can
    retry; it never silently shortens a governed retention period.
    """
    artifact = await firestore.get_artifact(ingestion_id)
    ingestion = await firestore.get_ingestion(ingestion_id)
    if not artifact and not ingestion:
        return {"status": "success", "duplicate": True, "deleted": False}
    row = artifact or ingestion or {}
    if (row.get("scope") != "reference_only"
            or row.get("retention_policy", "session") != "session"):
        return {"status": "error", "error": True,
                "error_code": "retention_scope_forbidden",
                "message": "Only session-only imports use this expiry path."}
    expires = _parse(str(row.get("retention_expires_at") or ""))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not force and (expires is None or current < expires):
        return {"status": "error", "error": True,
                "error_code": "retention_not_due", "retryable": True,
                "message": "Session import retention has not elapsed."}
    founder_id = str(row.get("founder_id") or row.get("workspace_id") or "")
    session_id = str(row.get("session_id") or "")
    if not founder_id or not session_id:
        return {"status": "error", "error": True,
                "error_code": "retention_owner_missing",
                "message": "Session import ownership is unavailable."}
    names = []
    for source in (artifact or {}, ingestion or {}):
        names.extend([str(source.get("storage_name") or ""),
                      str(source.get("artifact") or ""),
                      str(source.get("normalized_storage_name") or "")])
    for name in dict.fromkeys(name for name in names if name):
        await asyncio.to_thread(storage.delete_artifact, name)
    resource_id = session_resources.resource_id_for(
        founder_id, session_resources.ResourceType.ARTIFACT,
        "artifacts", ingestion_id)
    await firestore.delete_session_artifact_records(
        founder_id, session_id, ingestion_id)
    await firestore.tombstone_session_resource_links(
        founder_id, session_id, resource_id)
    await firestore.delete_resource_projection(founder_id, resource_id)
    await firestore.audit(
        "system:retention", "expire_session_import",
        f"artifacts/{ingestion_id}", "success",
        "reference_only bytes and projections deleted")
    return {"status": "success", "duplicate": False, "deleted": True,
            "ingestion_id": ingestion_id}

