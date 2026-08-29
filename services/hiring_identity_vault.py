"""Application-authorized encrypted candidate identity storage."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any, Awaitable, Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from services import hiring_activation
from services.actor_identity import ActorPrincipal, WorkspaceRole, authorize
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import canonical_hash, stable_id, utc_now

KeyWrapper = Callable[[bytes, str], Awaitable[bytes]]
KeyUnwrapper = Callable[[bytes, str], Awaitable[bytes]]


class CandidateIdentityVault:
    """Envelope-encrypt identity with one random DEK per application.

    Production candidate intake requires Cloud KMS wrappers and an explicit
    live-data mode. A local fixture wrapper can process live data only in a
    non-Cloud-Run development process; deployed services fail closed without
    the configured KMS key and deduplication secret.
    """

    def __init__(self, *, wrap_key: KeyWrapper, unwrap_key: KeyUnwrapper,
                 dedup_key: bytes,
                 store: DurableStore | None = None,
                 allow_live: bool = False):
        if len(dedup_key) < 32:
            raise ValueError("identity dedup key must be at least 256 bits")
        self._wrap = wrap_key
        self._unwrap = unwrap_key
        self._dedup_key = bytes(dedup_key)
        self.store = store or production_store()
        self._allow_live = bool(allow_live)

    async def store_identity(self, *, workspace_id: str, role_id: str,
                             candidate_application_id: str,
                             identity_fields: dict[str, str],
                             synthetic_guard: dict[str, Any] | None = None,
                             provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        mode = dict(provenance or synthetic_guard or {})
        gate = hiring_activation.require_application_mode(mode)
        if gate.get("error"):
            return gate
        if mode.get("synthetic") is not True and not self._allow_live:
            return _error("production_keying_required")
        allowed = {"name", "email", "phone", "provider_candidate_id"}
        if not identity_fields or set(identity_fields) - allowed:
            return _error("invalid_identity_contract")
        identity_id = stable_id("identity", workspace_id, candidate_application_id)
        identity_payload_hash = canonical_hash({
            "workspace_id": workspace_id, "role_id": role_id,
            "candidate_application_id": candidate_application_id,
            "identity_fields": identity_fields,
            "data_mode": ("SYNTHETIC" if mode.get("synthetic") is True
                          else "LIVE_INTERNAL"),
        })
        aad = f"v1\x1f{workspace_id}\x1f{role_id}\x1f{candidate_application_id}".encode()
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        plaintext = json.dumps(identity_fields, sort_keys=True,
                               separators=(",", ":")).encode()
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
        try:
            wrapped_key = await self._wrap(dek, identity_id)
        except Exception:
            return _error("encryption_unavailable")
        row = {
            "schema_version": 1, "candidate_id": identity_id,
            "workspace_id": workspace_id, "role_id": role_id,
            "candidate_application_id": candidate_application_id,
            "ciphertext": base64.b64encode(ciphertext).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "wrapped_key": base64.b64encode(wrapped_key).decode(),
            "aad_hash": canonical_hash({"aad": aad.decode()}),
            "identity_dedup_hash": "hmac-sha256:" + hmac.new(
                self._dedup_key,
                (workspace_id + "\x1f" + str(identity_fields.get("email", ""))
                 .strip().lower()).encode(), hashlib.sha256).hexdigest(),
            "identity_payload_hash": identity_payload_hash,
            "notice_receipts": [], "consent_receipts": [],
            "retention_status": "ACTIVE", "legal_hold": False,
            "synthetic": mode.get("synthetic") is True,
            "synthetic_namespace": mode.get("synthetic_namespace"),
            "fixture_id": mode.get("fixture_id"),
            "data_mode": ("SYNTHETIC" if mode.get("synthetic") is True
                          else "LIVE_INTERNAL"),
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create("candidate_identities", identity_id, row)
        if not created:
            existing = await self.store.get("candidate_identities", identity_id)
            if (not existing
                    or existing.get("candidate_application_id") != candidate_application_id
                    or existing.get("identity_payload_hash") != identity_payload_hash):
                return _error("idempotency_conflict")
            return {"status": "success", "duplicate": True,
                    "candidate_id": identity_id}
        return {"status": "success", "duplicate": False,
                "candidate_id": identity_id}

    async def reveal_identity(self, *, identity_id: str, workspace_id: str,
                              role_id: str, candidate_application_id: str,
                              principal: ActorPrincipal) -> dict[str, Any]:
        gate = authorize(principal, "read_candidate", role_id=role_id,
                         candidate_application_id=candidate_application_id,
                         require_fresh=True)
        if (gate.get("error") or principal.workspace_id != workspace_id
                or principal.role not in {WorkspaceRole.OWNER,
                                          WorkspaceRole.HIRING_MANAGER}):
            return _error("identity_access_forbidden")
        row = await self.store.get("candidate_identities", identity_id)
        if (not row or row.get("workspace_id") != workspace_id
                or row.get("role_id") != role_id
                or row.get("candidate_application_id") != candidate_application_id):
            return _error("identity_not_found")
        aad = f"v1\x1f{workspace_id}\x1f{role_id}\x1f{candidate_application_id}".encode()
        try:
            dek = await self._unwrap(base64.b64decode(row["wrapped_key"]), identity_id)
            plaintext = AESGCM(dek).decrypt(
                base64.b64decode(row["nonce"]), base64.b64decode(row["ciphertext"]), aad)
            fields = json.loads(plaintext)
        except Exception:
            return _error("decryption_failed")
        return {"status": "success", "identity": fields}


def fixture_key_wrapper(master_key: bytes) -> tuple[KeyWrapper, KeyUnwrapper]:
    """Deterministic test-only key wrapper; never accepted for real records."""
    if len(master_key) not in {16, 24, 32}:
        raise ValueError("fixture master key must be an AES key")

    async def wrap(dek: bytes, identity_id: str) -> bytes:
        # A fresh random nonce per wrap. Deriving it from identity_id reused the
        # same (key, nonce) pair every time an application was re-ingested,
        # while store_identity mints a new random DEK on each call — the exact
        # AES-GCM nonce-reuse condition that leaks plaintext XOR and the GHASH
        # authentication key.
        nonce = os.urandom(12)
        return nonce + AESGCM(master_key).encrypt(nonce, dek, identity_id.encode())

    async def unwrap(wrapped: bytes, identity_id: str) -> bytes:
        return AESGCM(master_key).decrypt(
            wrapped[:12], wrapped[12:], identity_id.encode())

    return wrap, unwrap


def kms_key_wrapper(kms_key_name: str) -> tuple[KeyWrapper, KeyUnwrapper]:
    """Production Cloud KMS DEK wrapper; client is created per async call."""
    if not kms_key_name.startswith("projects/") or "/cryptoKeys/" not in kms_key_name:
        raise ValueError("fully-qualified Cloud KMS key name required")

    async def wrap(dek: bytes, identity_id: str) -> bytes:
        from google.cloud import kms_v1
        client = kms_v1.KeyManagementServiceAsyncClient()
        response = await client.encrypt(request={
            "name": kms_key_name, "plaintext": dek,
            "additional_authenticated_data": identity_id.encode(),
        })
        return bytes(response.ciphertext)

    async def unwrap(wrapped: bytes, identity_id: str) -> bytes:
        from google.cloud import kms_v1
        client = kms_v1.KeyManagementServiceAsyncClient()
        response = await client.decrypt(request={
            "name": kms_key_name, "ciphertext": wrapped,
            "additional_authenticated_data": identity_id.encode(),
        })
        return bytes(response.plaintext)

    return wrap, unwrap


def _error(code: str) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": "Candidate identity operation was refused."}
