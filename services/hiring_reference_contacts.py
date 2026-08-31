"""Encrypted reference-contact resolver for Hiring H5.

Reference names and addresses are candidate-provided restricted identity data.
They are envelope-encrypted under a dedicated key and are revealed only while
executing the exact Founder-approved outreach.  General projections, model
inputs, approvals and external-action rows retain only an opaque contact id, a
masked address and a keyed address hash.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from services.actor_identity import ActorPrincipal, authorize
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import canonical_hash, stable_id, utc_now
from services.hiring_identity_vault import (
    KeyUnwrapper,
    KeyWrapper,
    fixture_key_wrapper,
    kms_key_wrapper,
)

_EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")
_LOCAL_KEY_ENV = "HIRING_REFERENCE_CONTACT_LOCAL_KEY"
_LOCAL_KEY_FILE_ENV = "HIRING_REFERENCE_CONTACT_LOCAL_KEY_FILE"
_KMS_KEY_ENV = "HIRING_REFERENCE_CONTACT_KMS_KEY_NAME"
_RESPONSE_SECRET_ENV = "HIRING_REFERENCE_RESPONSE_TOKEN_SECRET"


def _error(code: str, http_status: int = 409) -> dict[str, Any]:
    return {
        "status": "error", "error": True, "error_code": code,
        "message": "Reference contact operation was refused.",
        "http_status": http_status,
    }


def _local_key_path() -> Path:
    configured = os.environ.get(_LOCAL_KEY_FILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    state = os.environ.get("XDG_STATE_HOME", "").strip()
    root = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return root / "cofounder" / "hiring-reference-contact.key"


def _read_private_key(path: Path) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return None
        os.fchmod(descriptor, 0o600)
        raw = os.read(descriptor, 129).decode("ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(descriptor)
    return bytes.fromhex(raw) if re.fullmatch(r"[0-9a-fA-F]{64}", raw) else None


def _local_key() -> bytes | None:
    raw = os.environ.get(_LOCAL_KEY_ENV, "").strip()
    if raw:
        return bytes.fromhex(raw) if re.fullmatch(r"[0-9a-fA-F]{64}", raw) else None
    path = _local_key_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        return None
    existing = _read_private_key(path)
    if existing:
        return existing
    material = secrets.token_bytes(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return _read_private_key(path)
    except OSError:
        return None
    try:
        os.write(descriptor, material.hex().encode("ascii"))
        os.fsync(descriptor)
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    finally:
        os.close(descriptor)
    return _read_private_key(path)


def production_reference_resolver_configured() -> bool:
    """Return whether Cloud Run has a dedicated reviewed KMS key."""
    value = os.environ.get(_KMS_KEY_ENV, "").strip()
    return value.startswith("projects/") and "/cryptoKeys/" in value


def reference_response_secret(*, production_only: bool = False) -> bytes | None:
    """Resolve the bearer-token signing key; cloud never uses a local fallback."""
    raw = os.environ.get(_RESPONSE_SECRET_ENV, "").strip()
    if raw:
        return bytes.fromhex(raw) if re.fullmatch(r"[0-9a-fA-F]{64}", raw) else None
    if production_only or os.environ.get("K_SERVICE"):
        return None
    return _local_key()


def reference_response_token(reference_check_id: str, nonce: str) -> str | None:
    """Build a stable opaque token that can be regenerated only at execution."""
    key = reference_response_secret()
    if not key:
        return None
    payload = base64.urlsafe_b64encode(
        f"v1\x1f{reference_check_id}\x1f{nonce}".encode()).decode().rstrip("=")
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _keyed_binding(value: str) -> str | None:
    """Return a non-enumerable identity/idempotency binding."""
    key = reference_response_secret()
    if not key:
        return None
    return "hmac-sha256:" + hmac.new(
        key, value.encode(), hashlib.sha256).hexdigest()


def reference_contact_vault(
        store: DurableStore | None = None) -> "ReferenceContactVault | None":
    """Build the runtime vault without ever falling back to local keys in cloud."""
    if os.environ.get("K_SERVICE"):
        key_name = os.environ.get(_KMS_KEY_ENV, "").strip()
        if not production_reference_resolver_configured():
            return None
        wrap, unwrap = kms_key_wrapper(key_name)
    else:
        key = _local_key()
        if not key:
            return None
        wrap, unwrap = fixture_key_wrapper(key)
    return ReferenceContactVault(wrap_key=wrap, unwrap_key=unwrap,
                                 store=store or production_store())


class ReferenceContactVault:
    """Envelope-encrypt one scoped reference identity per opaque contact id."""

    def __init__(self, *, wrap_key: KeyWrapper, unwrap_key: KeyUnwrapper,
                 store: DurableStore):
        self._wrap = wrap_key
        self._unwrap = unwrap_key
        self.store = store

    async def store_contact(
            self, *, principal: ActorPrincipal, application: dict[str, Any],
            name: str, email: str, label: str,
            client_request_id: str) -> dict[str, Any]:
        gate = authorize(principal, "human_decision", require_fresh=True)
        if gate.get("error"):
            return gate
        normalized_email = email.strip().casefold()
        if (application.get("workspace_id") != principal.workspace_id
                or not name.strip() or not _EMAIL.fullmatch(normalized_email)):
            return _error("reference_contact_invalid", 400)
        application_id = str(application["candidate_application_id"])
        role_id = str(application["role_id"])
        contact_id = stable_id("referencecontact", application_id,
                               client_request_id)
        existing = await self.store.get("hiring_reference_contacts", contact_id)
        canonical_request = canonical_hash({
            "application_id": application_id, "name": name.strip(),
            "email": normalized_email, "label": label.strip(),
        })
        request_hash = _keyed_binding(canonical_request)
        email_hash = _keyed_binding(
            principal.workspace_id + "\x1f" + normalized_email)
        if not request_hash or not email_hash:
            return _error("reference_contact_binding_unavailable", 503)
        if existing:
            if existing.get("request_hash") != request_hash:
                return _error("idempotency_conflict")
            return {
                "status": "success", "duplicate": True,
                "reference_contact_ref": contact_id,
                "masked_email": existing["masked_email"],
            }
        aad = (
            f"v1\x1f{principal.workspace_id}\x1f{role_id}\x1f"
            f"{application_id}\x1f{contact_id}"
        ).encode()
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(
            nonce,
            json.dumps({"name": name.strip(), "email": normalized_email},
                       sort_keys=True, separators=(",", ":")).encode(),
            aad,
        )
        try:
            wrapped = await self._wrap(dek, contact_id)
        except Exception:
            return _error("reference_contact_encryption_unavailable", 503)
        local, domain = normalized_email.split("@", 1)
        row = {
            "schema_version": 1, "reference_contact_id": contact_id,
            "workspace_id": principal.workspace_id, "role_id": role_id,
            "candidate_application_id": application_id,
            "ciphertext": base64.b64encode(ciphertext).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "wrapped_key": base64.b64encode(wrapped).decode(),
            "aad_hash": canonical_hash({"aad": aad.decode()}),
            "masked_email": f"{local[:1]}***@{domain}",
            "email_hash": email_hash,
            "label": label.strip(), "request_hash": request_hash,
            "retention_status": "ACTIVE", "created_at": utc_now(),
            "updated_at": utc_now(), "version": 1,
        }
        if not await self.store.create("hiring_reference_contacts", contact_id, row):
            return _error("concurrency_conflict")
        return {
            "status": "success", "duplicate": False,
            "reference_contact_ref": contact_id,
            "masked_email": row["masked_email"],
        }

    async def reveal_for_outreach(
            self, *, principal: ActorPrincipal, contact_id: str,
            application: dict[str, Any]) -> dict[str, Any]:
        """Decrypt only during a fresh exact Founder-authorized action."""
        gate = authorize(principal, "resolve_approval", require_fresh=True)
        if gate.get("error"):
            return gate
        row = await self.store.get("hiring_reference_contacts", contact_id)
        if (not row or row.get("workspace_id") != principal.workspace_id
                or row.get("role_id") != application.get("role_id")
                or row.get("candidate_application_id") !=
                application.get("candidate_application_id")
                or row.get("retention_status") != "ACTIVE"):
            return _error("reference_contact_not_found", 404)
        aad = (
            f"v1\x1f{principal.workspace_id}\x1f{row['role_id']}\x1f"
            f"{row['candidate_application_id']}\x1f{contact_id}"
        ).encode()
        try:
            dek = await self._unwrap(base64.b64decode(row["wrapped_key"]), contact_id)
            plaintext = AESGCM(dek).decrypt(
                base64.b64decode(row["nonce"]),
                base64.b64decode(row["ciphertext"]), aad)
            fields = json.loads(plaintext)
        except Exception:
            return _error("reference_contact_decryption_failed", 503)
        email = str(fields.get("email") or "").casefold()
        if not _EMAIL.fullmatch(email):
            return _error("reference_contact_decryption_failed", 503)
        return {
            "status": "success", "contact": {
                "name": str(fields.get("name") or ""), "email": email,
                "masked_email": row["masked_email"],
                "email_hash": row["email_hash"], "label": row["label"],
            },
        }
