"""Encrypted, short-lived artifact storage for Spec 39 memory exports.

The local implementation is restricted to the ignored synthetic-pilot
namespace and uses AES-256-GCM. The production implementation writes directly
to a dedicated GCS bucket with a configured Cloud KMS key and never creates a
plaintext local cache. Neither implementation is selected unless its exact
configuration is present; configuration does not itself satisfy the external
release attestation.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from services.local_pilot_store import PILOT_ROOT

EXPORT_TTL_HOURS = 24
LOCAL_EXPORT_ROOT = PILOT_ROOT / "exports"
LOCAL_EXPORT_KEY_PATH = PILOT_ROOT / "export-key.bin"
_EXPORT_ID = re.compile(r"[a-z0-9_]{16,160}")
_MAGIC = b"SPEC39-M2-EXPORT-AESGCM-V1\0"


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "status": "error",
        "error": True,
        "error_code": code,
        "message": message,
        "retryable": retryable,
    }


class MemoryExportArtifactStore(Protocol):
    """Encrypted artifact boundary; plaintext exists only in request memory."""

    @property
    def available(self) -> bool: ...

    async def write(
        self, *, export_id: str, payload: bytes, aad: bytes, expires_at: str
    ) -> dict[str, Any]: ...

    async def read(self, *, export_id: str, aad: bytes) -> dict[str, Any]: ...

    async def delete(self, *, export_id: str) -> dict[str, Any]: ...


class UnavailableMemoryExportStore:
    """Fail-closed store used until an export delivery is configured."""

    @property
    def available(self) -> bool:
        return False

    async def write(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return _error(
            "memory_export_unavailable", "Memory export delivery is not configured.", retryable=True
        )

    async def read(self, **kwargs: Any) -> dict[str, Any]:
        return await self.write(**kwargs)

    async def delete(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"status": "success", "deleted": False}


class LocalEncryptedMemoryExportStore:
    """AES-GCM artifact store inside the isolated local pilot namespace."""

    def __init__(self, *, root: Path = LOCAL_EXPORT_ROOT, key_path: Path = LOCAL_EXPORT_KEY_PATH):
        self.root = root.resolve()
        self.key_path = key_path.resolve()
        try:
            self.root.relative_to(PILOT_ROOT)
            self.key_path.relative_to(PILOT_ROOT)
        except ValueError as exc:
            raise ValueError("local export paths must stay inside the pilot directory") from exc

    @property
    def available(self) -> bool:
        return not bool(os.environ.get("K_SERVICE"))

    def _path(self, export_id: str) -> Path:
        if not _EXPORT_ID.fullmatch(export_id):
            raise ValueError("invalid memory export id")
        path = (self.root / f"{export_id}.aesgcm").resolve()
        path.relative_to(self.root)
        return path

    def _key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.is_symlink():
            raise ValueError("local export key cannot be a symlink")
        try:
            descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            descriptor = -1
        if descriptor >= 0:
            try:
                os.write(descriptor, secrets.token_bytes(32))
            finally:
                os.close(descriptor)
        self.key_path.chmod(0o600)
        key = self.key_path.read_bytes()
        if len(key) != 32:
            raise ValueError("local export key must be exactly 32 bytes")
        return key

    async def write(
        self, *, export_id: str, payload: bytes, aad: bytes, expires_at: str
    ) -> dict[str, Any]:
        del expires_at
        if not self.available:
            return await UnavailableMemoryExportStore().write()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.root.chmod(0o700)
            nonce = secrets.token_bytes(12)
            encrypted = _MAGIC + nonce + AESGCM(self._key()).encrypt(nonce, payload, aad)
            path = self._path(export_id)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(encrypted)
            temporary.chmod(0o600)
            temporary.replace(path)
            return {
                "status": "success",
                "artifact_ref": f"local-encrypted-memory-export:{export_id}",
                "artifact_sha256": hashlib.sha256(encrypted).hexdigest(),
                "encrypted_bytes": len(encrypted),
            }
        except Exception:
            return _error(
                "memory_export_storage_failed",
                "The encrypted export could not be stored.",
                retryable=True,
            )

    async def read(self, *, export_id: str, aad: bytes) -> dict[str, Any]:
        try:
            raw = self._path(export_id).read_bytes()
            if not raw.startswith(_MAGIC) or len(raw) <= len(_MAGIC) + 12:
                raise ValueError("invalid encrypted memory export")
            nonce = raw[len(_MAGIC) : len(_MAGIC) + 12]
            plaintext = AESGCM(self._key()).decrypt(nonce, raw[len(_MAGIC) + 12 :], aad)
            return {"status": "success", "payload": plaintext}
        except FileNotFoundError:
            return _error("memory_export_not_found", "Memory export is unavailable.")
        except Exception:
            return _error(
                "memory_export_decryption_failed", "Memory export is unavailable.", retryable=True
            )

    async def delete(self, *, export_id: str) -> dict[str, Any]:
        try:
            path = self._path(export_id)
            existed = path.exists()
            if existed:
                path.unlink()
            return {"status": "success", "deleted": existed}
        except Exception:
            return _error(
                "memory_export_cleanup_failed",
                "Expired export cleanup needs retry.",
                retryable=True,
            )


class GCSKmsMemoryExportStore:
    """Dedicated GCS+CMEK store, selected only by an attested configuration."""

    def __init__(self, *, project: str, bucket_name: str, kms_key_name: str):
        self.project = project
        self.bucket_name = bucket_name
        self.kms_key_name = kms_key_name

    @property
    def available(self) -> bool:
        return bool(self.project and self.bucket_name and self.kms_key_name)

    def _blob_name(self, export_id: str) -> str:
        if not _EXPORT_ID.fullmatch(export_id):
            raise ValueError("invalid memory export id")
        return f"spec39-memory-exports/{export_id}.json"

    async def write(
        self, *, export_id: str, payload: bytes, aad: bytes, expires_at: str
    ) -> dict[str, Any]:
        del aad

        def _upload() -> dict[str, Any]:
            from google.cloud import storage

            client = storage.Client(project=self.project)
            bucket = client.bucket(self.bucket_name)
            blob = bucket.blob(self._blob_name(export_id), kms_key_name=self.kms_key_name)
            blob.metadata = {
                "spec39-export": "true",
                "expires-at": expires_at,
                "generic-search": "excluded",
            }
            blob.upload_from_string(payload, content_type="application/json")
            return {
                "status": "success",
                "artifact_ref": f"gs://{self.bucket_name}/{blob.name}",
                "artifact_sha256": hashlib.sha256(payload).hexdigest(),
                "encrypted_bytes": int(blob.size or len(payload)),
            }

        try:
            return await asyncio.to_thread(_upload)
        except Exception:
            return _error(
                "memory_export_storage_failed",
                "The encrypted export could not be stored.",
                retryable=True,
            )

    async def read(self, *, export_id: str, aad: bytes) -> dict[str, Any]:
        del aad

        def _download() -> bytes:
            from google.cloud import storage

            client = storage.Client(project=self.project)
            return (
                client.bucket(self.bucket_name).blob(self._blob_name(export_id)).download_as_bytes()
            )

        try:
            return {"status": "success", "payload": await asyncio.to_thread(_download)}
        except Exception:
            return _error("memory_export_not_found", "Memory export is unavailable.")

    async def delete(self, *, export_id: str) -> dict[str, Any]:
        def _delete() -> None:
            from google.cloud import storage

            client = storage.Client(project=self.project)
            client.bucket(self.bucket_name).blob(self._blob_name(export_id)).delete()

        try:
            await asyncio.to_thread(_delete)
            return {"status": "success", "deleted": True}
        except Exception:
            return _error(
                "memory_export_cleanup_failed",
                "Expired export cleanup needs retry.",
                retryable=True,
            )


def configured_export_store(*, local_pilot: bool) -> MemoryExportArtifactStore:
    """Select no delivery unless the exact local or attested GCS lane exists."""
    if local_pilot and not os.environ.get("K_SERVICE"):
        return LocalEncryptedMemoryExportStore()
    attested = os.environ.get("DURABLE_MEMORY_M2_EXPORT_DELIVERY_ATTESTED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    bucket = os.environ.get("DURABLE_MEMORY_EXPORT_BUCKET", "").strip()
    key = os.environ.get("DURABLE_MEMORY_EXPORT_KMS_KEY_NAME", "").strip()
    if attested and project and bucket and key:
        return GCSKmsMemoryExportStore(project=project, bucket_name=bucket, kms_key_name=key)
    return UnavailableMemoryExportStore()
