"""Drive adapter (docs/06 §bootstrap): founder-selected files/folders only,
drive.readonly — no blanket indexing. Downloads a file to local artifact
storage; the standard ingestion pipeline takes it from there.

The Google client is injectable so tests run without OAuth.
"""

from __future__ import annotations

import io
import socket
from typing import Any, Callable

from services import google_oauth, storage

# Google-native formats are exported, binaries downloaded as-is.
# Presentations MUST export to PDF, not text/plain: Slides' plain-text export is
# sparse (often just titles / speaker notes) and drops the real content, which
# left the extractor hallucinating generic proposals. PDF preserves every slide
# for multimodal document understanding (see gemini_backends.doc_extract_fn).
_EXPORT_MIME = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}

_service_factory: Callable[[], Any] | None = None
MAX_FETCH_BYTES = 20 * 1024 * 1024


def _safe_component(value: str) -> str:
    """Allowlist a filename component: no separators, no traversal, bounded."""
    import re

    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(value or ""))
    cleaned = cleaned.replace("..", "_")
    return cleaned[:80]


def _http_status(exc: Exception) -> int:
    response = getattr(exc, "resp", None)
    value = (getattr(response, "status", None)
             or getattr(exc, "status_code", None)
             or getattr(exc, "status", None))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def set_service_factory(fn: Callable[[], Any] | None) -> None:
    """Tests inject a fake Drive service; prod builds from OAuth creds."""
    global _service_factory
    _service_factory = fn


def _service(workspace_id: str = ""):
    if _service_factory is not None:
        return _service_factory()
    creds = (google_oauth.get_credentials("founder", workspace_id)
             if workspace_id else google_oauth.get_credentials())
    if creds is None:
        return None
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_files(folder_id: str, limit: int = 25, workspace_id: str = "") -> dict:
    """List files in ONE founder-selected folder (no recursive crawling)."""
    svc = _service(workspace_id)
    if svc is None:
        return {"status": "error", "error": True,
                "message": "Google OAuth not configured (run scripts/oauth_setup.py)"}
    try:
        resp = svc.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="files(id,name,mimeType,modifiedTime)",
            pageSize=limit,
        ).execute()
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"drive list failed: {exc}"}
    return {"status": "success",
            "files": [{"id": f["id"], "name": f["name"],
                       "mime": f.get("mimeType", "")} for f in resp.get("files", [])]}


def fetch_file(file_id: str, workspace_id: str = "") -> dict:
    """Compatibility wrapper that stores bytes returned by ``fetch_file_bytes``."""
    fetched = fetch_file_bytes(file_id, workspace_id=workspace_id)
    if fetched.get("status") != "success":
        return fetched
    ext = fetched["detected_name"].rsplit(".", 1)[-1] \
        if "." in fetched["detected_name"] else ""
    suffix = f".{ext}" if ext else ""
    artifact = f"companydoc_drive_{_safe_component(file_id)}{_safe_component(suffix)}"
    storage.save_bytes(artifact, fetched["data"])
    return {"status": "success", "artifact": artifact,
            "name": fetched["detected_name"]}


def fetch_file_bytes(file_id: str, max_bytes: int = MAX_FETCH_BYTES,
                     workspace_id: str = "") -> dict:
    """Fetch one Drive file with bounded bytes and provider metadata as evidence."""
    svc = _service(workspace_id)
    if svc is None:
        return {"status": "error", "error": True,
                "message": "Google OAuth not configured (run scripts/oauth_setup.py)"}
    try:
        meta = svc.files().get(
            fileId=file_id,
            fields="name,mimeType,version,modifiedTime,size,md5Checksum").execute()
        mime = meta.get("mimeType", "")
        if mime in _EXPORT_MIME:
            export_mime, ext = _EXPORT_MIME[mime]
            data = svc.files().export(fileId=file_id, mimeType=export_mime).execute()
            if isinstance(data, str):
                data = data.encode("utf-8")
            declared = export_mime
        else:
            ext = "." + meta["name"].rsplit(".", 1)[-1] if "." in meta.get("name", "") else ""
            declared = mime or "application/octet-stream"
            if int(meta.get("size") or 0) > max_bytes:
                return {"status": "error", "error": True,
                        "error_code": "source_too_large",
                        "message": "The selected Drive file exceeds the 20 MB limit."}
            request = svc.files().get_media(fileId=file_id)
            from googleapiclient.http import MediaIoBaseDownload

            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
                if buffer.tell() > max_bytes:
                    return {"status": "error", "error": True,
                            "error_code": "source_too_large",
                            "message": "The selected Drive file exceeds the 20 MB limit."}
            data = buffer.getvalue()
        if len(data) > max_bytes:
            return {"status": "error", "error": True,
                    "error_code": "source_too_large",
                    "message": "The selected Drive file exceeds the 20 MB limit."}
    except Exception as exc:
        status = _http_status(exc)
        if status == 404:
            code, message = "source_missing", "The selected Drive file is unavailable."
        elif status == 401:
            code, message = "auth_required", "Reconnect Google Drive first."
        elif status == 403:
            code, message = "permission_denied", "Drive access is no longer permitted."
        else:
            code, message = "provider_unavailable", "The selected Drive file could not be fetched."
        return {"status": "error", "error": True,
                "error_code": code, "message": message}
    display_name = str(meta.get("name") or file_id)
    detected_name = display_name if display_name.lower().endswith(ext.lower()) \
        else display_name + ext
    return {
        "status": "success", "data": data, "detected_name": detected_name,
        "declared_content_type": declared,
        "provider_content_type": mime,
        "provider_version": str(meta.get("version") or meta.get("md5Checksum") or ""),
        "provider_modified_at": str(meta.get("modifiedTime") or ""),
    }


def _escape_drive_query(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def reconcile_export(source_artifact_id: str, checksum: str,
                     workspace_id: str = "") -> dict:
    """Find an exported produced file by immutable app properties."""
    svc = _service(workspace_id)
    if svc is None:
        return {"status": "error", "error": True,
                "error_code": "auth_required",
                "message": "Google Drive is not connected."}
    source = _escape_drive_query(source_artifact_id)
    digest = _escape_drive_query(checksum)
    query = (
        "trashed = false and "
        f"appProperties has {{ key='cofounderSourceId' and value='{source}' }} and "
        f"appProperties has {{ key='cofounderSha256' and value='{digest}' }}")
    try:
        result = svc.files().list(
            q=query, fields="files(id,name,webViewLink,appProperties)",
            pageSize=2).execute()
    except Exception:
        return {"status": "error", "error": True, "uncertain": True,
                "error_code": "provider_unavailable",
                "message": "Drive reconciliation could not be completed."}
    matches = result.get("files") or []
    if not matches:
        return {"status": "success", "exists": False}
    match = matches[0]
    return {"status": "success", "exists": True,
            "file_id": match.get("id", ""),
            "url": match.get("webViewLink", ""),
            "name": match.get("name", "")}


def upload_file(name: str, local_path: str, mime: str, *,
                source_artifact_id: str, checksum: str,
                workspace_id: str = "") -> dict:
    """Copy a produced document to the founder's Drive (docs/15 §security).

    Uses the per-file `drive.file` scope — never full-drive. Called only from
    the founder-clicked sync endpoint: the click IS the approval."""
    svc = _service(workspace_id)
    if svc is None:
        return {"status": "error", "error": True,
                "message": "Google OAuth not configured (run scripts/oauth_setup.py)"}
    try:
        from googleapiclient.http import MediaFileUpload

        meta = svc.files().create(
            body={"name": name, "appProperties": {
                "cofounderSourceId": source_artifact_id,
                "cofounderSha256": checksum,
            }},
            media_body=MediaFileUpload(local_path, mimetype=mime),
            fields="id,name,webViewLink",
        ).execute()
    except Exception as exc:
        status = _http_status(exc)
        definitive = status in {400, 401, 403, 404, 409, 422}
        timeout = isinstance(exc, (TimeoutError, socket.timeout))
        return {"status": "error", "error": True,
                "uncertain": timeout or not definitive,
                "error_code": ("provider_rejected" if definitive
                               else "provider_timeout" if timeout
                               else "provider_unavailable"),
                "message": ("Drive rejected the export."
                            if definitive else
                            "Drive did not confirm whether the export completed.")}
    return {"status": "success", "file_id": meta["id"],
            "url": meta.get("webViewLink", "")}
