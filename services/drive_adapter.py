"""Drive adapter (docs/06 §bootstrap): founder-selected files/folders only,
drive.readonly — no blanket indexing. Downloads a file to local artifact
storage; the standard ingestion pipeline takes it from there.

The Google client is injectable so tests run without OAuth.
"""

from __future__ import annotations

import io
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


def set_service_factory(fn: Callable[[], Any] | None) -> None:
    """Tests inject a fake Drive service; prod builds from OAuth creds."""
    global _service_factory
    _service_factory = fn


def _service():
    if _service_factory is not None:
        return _service_factory()
    creds = google_oauth.get_credentials()
    if creds is None:
        return None
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_files(folder_id: str, limit: int = 25) -> dict:
    """List files in ONE founder-selected folder (no recursive crawling)."""
    svc = _service()
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


def fetch_file(file_id: str) -> dict:
    """Download one founder-selected file into artifact storage."""
    svc = _service()
    if svc is None:
        return {"status": "error", "error": True,
                "message": "Google OAuth not configured (run scripts/oauth_setup.py)"}
    try:
        meta = svc.files().get(fileId=file_id, fields="name,mimeType").execute()
        mime = meta.get("mimeType", "")
        if mime in _EXPORT_MIME:
            export_mime, ext = _EXPORT_MIME[mime]
            data = svc.files().export(fileId=file_id, mimeType=export_mime).execute()
            if isinstance(data, str):
                data = data.encode("utf-8")
        else:
            ext = "." + meta["name"].rsplit(".", 1)[-1] if "." in meta.get("name", "") else ""
            request = svc.files().get_media(fileId=file_id)
            from googleapiclient.http import MediaIoBaseDownload

            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            data = buffer.getvalue()
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"drive fetch failed: {exc}"}
    artifact = f"companydoc_drive_{file_id}{ext}"
    storage.save_bytes(artifact, data)
    return {"status": "success", "artifact": artifact, "name": meta.get("name", file_id)}


def upload_file(name: str, local_path: str, mime: str) -> dict:
    """Copy a produced document to the founder's Drive (docs/15 §security).

    Uses the per-file `drive.file` scope — never full-drive. Called only from
    the founder-clicked sync endpoint: the click IS the approval."""
    svc = _service()
    if svc is None:
        return {"status": "error", "error": True,
                "message": "Google OAuth not configured (run scripts/oauth_setup.py)"}
    try:
        from googleapiclient.http import MediaFileUpload

        meta = svc.files().create(
            body={"name": name},
            media_body=MediaFileUpload(local_path, mimetype=mime),
            fields="id,name,webViewLink",
        ).execute()
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"drive upload failed: {exc}"}
    return {"status": "success", "file_id": meta["id"],
            "url": meta.get("webViewLink", "")}
