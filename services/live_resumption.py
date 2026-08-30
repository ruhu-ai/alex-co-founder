"""Encrypted, short-lived Gemini Live resumption handles.

Handles are provider credentials for cached Live context. They never reach the
browser, logs, session state, or transcript. Firestore stores only AES-GCM
ciphertext bound to the exact workspace/session pair.
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TTL_SECONDS = 9 * 60


def _key() -> bytes:
    secret = os.environ.get("APP_SESSION_SECRET", "")
    if not secret and not os.environ.get("K_SERVICE"):
        secret = os.environ.get("APP_AUTH_TOKEN", "") or "local-live-resumption"
    if not secret:
        raise RuntimeError("APP_SESSION_SECRET is required for Live resumption")
    return hashlib.sha256(("live-resumption-v1\x1f" + secret).encode()).digest()


def _aad(workspace_id: str, session_id: str) -> bytes:
    return f"co-founder-live-v1\x1f{workspace_id}\x1f{session_id}".encode()


def _seal(handle: str, workspace_id: str, session_id: str) -> str:
    nonce = os.urandom(12)
    encrypted = AESGCM(_key()).encrypt(
        nonce, handle.encode("utf-8"), _aad(workspace_id, session_id))
    return base64.urlsafe_b64encode(nonce + encrypted).decode()


def _open(value: str, workspace_id: str, session_id: str) -> str:
    raw = base64.urlsafe_b64decode(value.encode())
    return AESGCM(_key()).decrypt(
        raw[:12], raw[12:], _aad(workspace_id, session_id)).decode("utf-8")


async def save(*, workspace_id: str, session_id: str,
               handle: str) -> dict[str, Any]:
    """Persist one encrypted opaque handle; return no handle material."""
    if not workspace_id or not session_id or not handle or len(handle) > 16_384:
        return {"status": "error", "error": True,
                "error_code": "live_resumption_invalid"}
    from services import firestore

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS)
    try:
        await firestore.upsert_live_session_resumption(
            workspace_id, session_id,
            ciphertext=_seal(handle, workspace_id, session_id),
            expires_at=expires_at,
        )
    except Exception:  # noqa: BLE001 - voice continues without cached context
        return {"status": "error", "error": True,
                "error_code": "live_resumption_store_unavailable"}
    return {"status": "success", "expires_at": expires_at.isoformat()}


async def load(*, workspace_id: str, session_id: str) -> str:
    """Return a valid handle or an empty string; corruption fails closed."""
    from services import firestore

    try:
        row = await firestore.get_live_session_resumption(
            workspace_id, session_id)
        if not row:
            return ""
        raw_expiry = row.get("expires_at")
        expiry = (raw_expiry if isinstance(raw_expiry, datetime)
                  else datetime.fromisoformat(str(raw_expiry or "")))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            await firestore.delete_live_session_resumption(
                workspace_id, session_id)
            return ""
        return _open(str(row.get("ciphertext") or ""), workspace_id, session_id)
    except Exception:  # noqa: BLE001 - stale/corrupt handles are never fatal
        try:
            await firestore.delete_live_session_resumption(
                workspace_id, session_id)
        except Exception:  # noqa: BLE001
            pass
        return ""


async def delete(*, workspace_id: str, session_id: str) -> None:
    """Remove cached provider continuity after an explicit end or deletion."""
    from services import firestore
    await firestore.delete_live_session_resumption(workspace_id, session_id)
