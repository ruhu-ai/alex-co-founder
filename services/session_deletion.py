"""Founder-initiated session deletion and session-owned file cleanup.

Session links are many-to-many. Deleting a conversation therefore tombstones
its links but never destroys shared business records. Only file-bearing
resources that are owned by this session, retained at session scope, and have
no live link from another session are physically removed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from services import browser_gateway as browser_service
from services import firestore, session_resources, storage

_PAGE_SIZE = 250
_MAX_LINKS = 10_000


async def _all_live_links(founder_id: str, session_id: str) -> list[dict[str, Any]]:
    """Read a bounded complete deletion plan before mutating any state."""
    links: list[dict[str, Any]] = []
    cursor: tuple[str, str] | None = None
    while len(links) <= _MAX_LINKS:
        page = await firestore.list_session_links(
            founder_id, session_id, limit=_PAGE_SIZE, start_after=cursor)
        links.extend(page)
        if len(page) < _PAGE_SIZE:
            return links
        last = page[-1]
        cursor = (
            str(last.get("occurred_at") or ""),
            str(last.get("link_id") or last.get("id") or ""),
        )
    raise ValueError("session has too many linked items to delete safely")


async def _delete_exclusive_file(
    founder_id: str,
    session_id: str,
    link: dict[str, Any],
) -> tuple[bool, str]:
    """Delete one exclusive session-owned artifact/document, if eligible."""
    resource_id = str(link.get("resource_id") or "")
    resource_type = str(link.get("resource_type") or "")
    if resource_type not in {
        session_resources.ResourceType.ARTIFACT,
        session_resources.ResourceType.DOCUMENT,
    }:
        return False, "retained_resource"
    if await firestore.resource_has_other_session_link(
        founder_id, resource_id, excluding_session_id=session_id
    ):
        return False, "shared"

    resource = await firestore.get_resource(resource_id)
    if not resource or resource.get("founder_id") != founder_id:
        return False, "missing"
    canonical = resource.get("canonical_ref") or {}
    canonical_id = str(canonical.get("id") or "")
    if not canonical_id:
        return False, "missing"

    storage_name = ""
    if resource_type == session_resources.ResourceType.ARTIFACT:
        artifact = await firestore.get_artifact(canonical_id)
        if (
            not artifact
            or artifact.get("founder_id") != founder_id
            or artifact.get("session_id") != session_id
            or artifact.get("retention_policy") != "session"
        ):
            return False, "profile_or_foreign"
        storage_name = str(artifact.get("storage_name") or artifact.get("artifact") or "")
        if storage_name:
            await asyncio.to_thread(storage.delete_artifact, storage_name)
        removed = await firestore.delete_session_artifact_records(
            founder_id, session_id, canonical_id
        )
        if not removed:
            raise RuntimeError("artifact ownership changed during deletion")
    else:
        document = await firestore.get_document(canonical_id)
        if (
            not document
            or document.get("founder_id") != founder_id
            or document.get("session_id") != session_id
        ):
            return False, "shared_or_foreign"
        storage_name = str(document.get("artifact_name") or "")
        if storage_name:
            await asyncio.to_thread(storage.delete_artifact, storage_name)
        removed = await firestore.delete_session_document_record(
            founder_id, session_id, canonical_id
        )
        if not removed:
            raise RuntimeError("document ownership changed during deletion")

    if not await firestore.delete_resource_projection(founder_id, resource_id):
        raise RuntimeError("resource projection could not be deleted")
    return True, storage_name or canonical_id


async def delete_session(
    *,
    founder_id: str,
    session_id: str,
    app_name: str,
    session_service: Any,
) -> dict[str, Any]:
    """Delete one owned session and safely clean up its exclusive files.

    The operation is idempotent after the catalog reaches ``deleted``. Cleanup
    errors are returned explicitly; they never turn a partially completed
    destructive operation into a false success.
    """
    catalog = await firestore.get_session_catalog(session_id)
    if catalog and catalog.get("founder_id") != founder_id:
        return {"status": "error", "error": True, "message": "not found"}

    session = await session_service.get_session(
        app_name=app_name, user_id=founder_id, session_id=session_id
    )
    if session is None:
        if catalog and catalog.get("status") == "deleted":
            return {
                "status": "success",
                "session_id": session_id,
                "already_deleted": True,
                "deleted_files": 0,
                "retained_items": 0,
                "cleanup_errors": [],
            }
        return {"status": "error", "error": True, "message": "not found"}

    try:
        links = await _all_live_links(founder_id, session_id)
    except (TypeError, ValueError) as exc:
        return {"status": "error", "error": True, "message": str(exc)[:240]}

    # Hide the picker row before cross-store cleanup begins. A retry remains
    # safe because deterministic tombstones and record ownership checks are
    # idempotent.
    await firestore.upsert_session_catalog(
        session_id,
        {
            "founder_id": founder_id,
            "status": "deleting",
            "deletion_started_at": firestore.now_iso(),
        },
    )

    cleanup_errors: list[str] = []
    try:
        stopped = await browser_service.stop_browser(
            {"app_name": app_name, "user_id": founder_id, "session_id": session_id},
            actor="founder:session_delete",
        )
        if stopped.get("error") and stopped.get("error_code") != "not_found":
            cleanup_errors.append("active browser work could not be stopped")
    except Exception:  # noqa: BLE001 - deletion continues and reports cleanup
        cleanup_errors.append("active browser work could not be stopped")

    deleted_files: list[str] = []
    retained_items = 0
    seen: set[str] = set()
    for link in links:
        resource_id = str(link.get("resource_id") or "")
        if not resource_id or resource_id in seen:
            continue
        seen.add(resource_id)
        try:
            deleted, detail = await _delete_exclusive_file(
                founder_id, session_id, link
            )
            if deleted:
                deleted_files.append(detail)
            else:
                retained_items += 1
        except Exception:  # noqa: BLE001 - remaining cleanup must continue
            cleanup_errors.append(f"file cleanup failed for {resource_id[:16]}")

    await session_service.delete_session(
        app_name=app_name, user_id=founder_id, session_id=session_id
    )
    tombstoned = await firestore.tombstone_session_links(founder_id, session_id)
    await firestore.upsert_session_catalog(
        session_id,
        {
            "founder_id": founder_id,
            "status": "deleted",
            "preview": "",
            "message_count": 0,
            "resource_count": 0,
            "resource_types": [],
            "search_terms": [],
            "search_prefixes": [],
            "deleted_at": firestore.now_iso(),
        },
    )
    await firestore.audit(
        "founder",
        "session_delete",
        f"sessions/{session_id}",
        "partial" if cleanup_errors else "success",
        (
            f"links={tombstoned} files={len(deleted_files)} "
            f"retained={retained_items} cleanup_errors={len(cleanup_errors)}"
        ),
    )
    return {
        "status": "success",
        "session_id": session_id,
        "already_deleted": False,
        "tombstoned_links": tombstoned,
        "deleted_files": len(deleted_files),
        "retained_items": retained_items,
        "cleanup_errors": cleanup_errors,
    }
