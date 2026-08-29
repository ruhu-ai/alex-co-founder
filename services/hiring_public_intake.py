"""Closed, local-staged public application intake for manually published roles.

This module deliberately does not activate a production intake channel.  The
form path is available only in an explicitly enabled non-Cloud local process,
for a non-synthetic role whose exact policy and manual publication receipt are
current.  Applicant identity, cover note, and resume bytes are encrypted before
they enter the durable candidate queue or artifact store.  No assessment,
ranking, contact, or provider action is started by intake.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from services import document_ingestion, storage
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.hiring_contracts import CandidateState, RoleState, stable_id, utc_now
from services.resource_sensitivity import registration_policy

MAX_RESUME_BYTES = 5 * 1024 * 1024
TOKEN_TTL_SECONDS = 15 * 60
PRIVACY_NOTICE = (
    "We use the details and resume you submit only to review your application "
    "for this role. They are kept in the role's restricted candidate queue and "
    "are not used for automated ranking or a hiring decision."
)
_EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_ROLE_EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _configured_local_key() -> bytes | None:
    """Resolve the explicit local-only key; never derive it from app auth state."""
    if os.environ.get("K_SERVICE") or os.environ.get(
            "HIRING_PUBLIC_INTAKE_LOCAL_ENABLED") != "1":
        return None
    raw = os.environ.get("HIRING_PUBLIC_INTAKE_LOCAL_KEY", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", raw):
        return None
    return bytes.fromhex(raw)


def _active_manual_receipt(role: dict[str, Any]) -> dict[str, Any] | None:
    policy_id = str(role.get("current_policy_version_id") or "")
    policy_hash = str(role.get("current_policy_hash") or "")
    receipts = [
        item for item in role.get("publication_receipts", [])
        if item.get("policy_version_id") == policy_id
        and item.get("policy_hash") == policy_hash
        and item.get("automated_publication") is False
    ]
    return (sorted(receipts, key=lambda item: str(
        item.get("recorded_at") or ""))[-1] if receipts else None)


def _form_is_eligible(role: dict[str, Any]) -> bool:
    return bool(
        role.get("synthetic") is False
        and role.get("role_state") == RoleState.PUBLISHED.value
        and role.get("publication_allowed") is not False
        and role.get("candidate_processing_allowed") is True
        and role.get("public_application_form_enabled") is True
        and _active_manual_receipt(role)
    )


def _role_email(role: dict[str, Any]) -> str:
    """Expose only an explicitly verified role route, never a general inbox."""
    binding = dict(role.get("mailbox_binding") or {})
    address = str((role.get("publication_package") or {}).get(
        "application_address") or "").strip()
    if (role.get("synthetic") is False
            and role.get("candidate_processing_allowed") is True
            and binding.get("status") == "ACTIVE"
            and binding.get("provider_route_id")
            and binding.get("provider_label_id")
            and _ROLE_EMAIL.fullmatch(address)):
        return address
    return ""


def _token(key: bytes, *, role_id: str, policy_hash: str,
           receipt_id: str, expires_at: int) -> str:
    payload = json.dumps({
        "role_id": role_id,
        "policy_hash": policy_hash,
        "receipt_id": receipt_id,
        "exp": expires_at,
    }, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def build_public_intake_projection(role: dict[str, Any]) -> dict[str, Any]:
    """Return candidate-safe intake availability for an already public role."""
    key = _configured_local_key()
    receipt = _active_manual_receipt(role)
    form_available = bool(key and _form_is_eligible(role) and receipt)
    projection: dict[str, Any] = {
        "form_available": form_available,
        "email_available": bool(_role_email(role)),
        "role_email": _role_email(role),
        "privacy_notice": PRIVACY_NOTICE,
        "resume_max_bytes": MAX_RESUME_BYTES,
        "resume_types": ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    }
    if form_available and key and receipt:
        projection["intake_token"] = _token(
            key, role_id=str(role["role_id"]),
            policy_hash=str(role["current_policy_hash"]),
            receipt_id=str(receipt.get("receipt_id") or ""),
            expires_at=int(time.time()) + TOKEN_TTL_SECONDS)
    return projection


class HiringPublicIntakeService:
    """Validate and enqueue one local-staged public application."""

    def __init__(self, *, store: DurableStore | None = None,
                 local_key: bytes | None = None,
                 local_enabled: bool | None = None):
        self.store = store or production_store()
        self._key = local_key if local_key is not None else _configured_local_key()
        self._enabled = bool(self._key) if local_enabled is None else local_enabled
        if self._key is not None and len(self._key) != 32:
            raise ValueError("public intake local key must be 256 bits")

    def _verify_token(self, value: str, *, role: dict[str, Any],
                      receipt: dict[str, Any]) -> bool:
        if not self._key or len(value) > 4096 or value.count(".") != 1:
            return False
        try:
            payload_raw, signature_raw = value.split(".", 1)
            payload = _unb64(payload_raw)
            supplied = _unb64(signature_raw)
            expected = hmac.new(self._key, payload, hashlib.sha256).digest()
            claims = json.loads(payload)
        except (ValueError, TypeError, json.JSONDecodeError):
            return False
        return bool(
            hmac.compare_digest(supplied, expected)
            and claims == {
                "role_id": role.get("role_id"),
                "policy_hash": role.get("current_policy_hash"),
                "receipt_id": receipt.get("receipt_id"),
                "exp": claims.get("exp"),
            }
            and isinstance(claims.get("exp"), int)
            and int(time.time()) <= claims["exp"] <= int(time.time()) + TOKEN_TTL_SECONDS
        )

    async def submit(
            self, *, role_id: str, intake_token: str,
            client_request_id: str, applicant_name: str, email: str,
            cover_note: str, consent_accepted: bool, filename: str,
            content_type: str, resume_bytes: bytes) -> dict[str, Any]:
        if not self._enabled or not self._key or os.environ.get("K_SERVICE"):
            return _error("public_intake_not_enabled",
                          "Online applications are not enabled for this role.", 503)
        role = await self.store.get("hiring_roles", role_id)
        receipt = _active_manual_receipt(role or {})
        if not role or not receipt or not _form_is_eligible(role):
            return _error("public_intake_not_open",
                          "Online applications are not open for this role.", 404)
        policy = await self.store.get(
            "hiring_policy_versions", str(role.get("current_policy_version_id") or ""))
        if (not policy or policy.get("status") != "APPROVED"
                or policy.get("canonical_hash") != role.get("current_policy_hash")
                or policy.get("role_id") != role_id):
            return _error("public_intake_not_open",
                          "Online applications are not open for this role.", 404)
        if not self._verify_token(intake_token, role=role, receipt=receipt):
            return _error("intake_token_invalid",
                          "This application form expired. Reload the role page.", 403)

        name = " ".join(applicant_name.split())
        normalized_email = email.strip().casefold()
        note = cover_note.strip()
        if not (1 <= len(name) <= 160):
            return _error("applicant_name_invalid", "Enter your name.", 400)
        if len(normalized_email) > 254 or not _EMAIL.fullmatch(normalized_email):
            return _error("applicant_email_invalid", "Enter a valid email address.", 400)
        if not (1 <= len(note) <= 4000):
            return _error("cover_note_invalid",
                          "Add a message of no more than 4,000 characters.", 400)
        if not consent_accepted:
            return _error("privacy_consent_required",
                          "Confirm the application privacy notice.", 400)
        if not _REQUEST_ID.fullmatch(client_request_id):
            return _error("intake_request_invalid", "Reload the form and try again.", 400)
        if not resume_bytes or len(resume_bytes) > MAX_RESUME_BYTES:
            return _error("resume_size_invalid", "Resume files must be 5 MB or smaller.", 413)
        extension = Path(filename or "").suffix.casefold()
        if extension not in {".pdf", ".docx"}:
            return _error("resume_type_invalid", "Upload a PDF or DOCX resume.", 415)
        checked = document_ingestion.validate_upload(
            resume_bytes, filename, content_type)
        if checked.get("status") != "success":
            return _error("resume_validation_failed",
                          str(checked.get("message") or "Upload a valid PDF or DOCX resume."), 415)

        application_id = stable_id("candidateapp", role_id, client_request_id)
        candidate_id = stable_id("identity", role["workspace_id"], application_id)
        artifact_id = stable_id("hartifact", application_id,
                                hashlib.sha256(resume_bytes).hexdigest())
        request_fingerprint = hmac.new(self._key, json.dumps({
            "role_id": role_id, "policy_hash": role["current_policy_hash"],
            "name": name, "email": normalized_email, "cover_note": note,
            "resume_sha256": hashlib.sha256(resume_bytes).hexdigest(),
            "consent_version": "public-intake-v1",
        }, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        existing = await self.store.get("candidate_applications", application_id)
        if existing:
            if existing.get("request_fingerprint") != request_fingerprint:
                return _error("intake_idempotency_conflict",
                              "This application request names different content.", 409)
            return {"status": "success", "duplicate": True,
                    "application_reference": existing.get("candidate_code"),
                    "application_status": existing.get("candidate_state")}

        now = utc_now()
        candidate_number = int(hashlib.sha256(
            application_id.encode()).hexdigest()[:12], 16) % 100_000_000
        candidate_code = f"C-{candidate_number:08d}"
        aad_prefix = f"v1\x1f{role['workspace_id']}\x1f{role_id}\x1f{application_id}"
        dek = AESGCM.generate_key(bit_length=256)
        identity_nonce = os.urandom(12)
        identity_ciphertext = AESGCM(dek).encrypt(
            identity_nonce,
            json.dumps({"name": name, "email": normalized_email,
                        "cover_note": note, "original_filename": Path(filename).name},
                       sort_keys=True, separators=(",", ":")).encode(),
            (aad_prefix + "\x1fidentity").encode())
        wrap_nonce = os.urandom(12)
        wrapped_key = AESGCM(self._key).encrypt(
            wrap_nonce, dek, candidate_id.encode())
        resume_nonce = os.urandom(12)
        encrypted_resume = AESGCM(dek).encrypt(
            resume_nonce, resume_bytes, (aad_prefix + "\x1f" + artifact_id).encode())
        storage_name = f"hiring-intake/{role_id}/{uuid.uuid4().hex}.bin"
        try:
            storage.save_bytes(storage_name, encrypted_resume)
        except Exception:
            return _error("intake_storage_unavailable",
                          "The application could not be stored. Try again later.", 503)

        resource_policy = registration_policy(
            scope="HIRING_RESTRICTED", sensitivity="HIRING_RESTRICTED")
        if resource_policy.get("error"):
            storage.delete_artifact(storage_name)
            return resource_policy
        consent_receipt_id = stable_id("consent", application_id, "public-intake-v1")
        application = {
            "schema_version": 1, "candidate_application_id": application_id,
            "workspace_id": role["workspace_id"], "role_id": role_id,
            "candidate_id": candidate_id, "candidate_code": candidate_code,
            "run_id": None, "journey_id": role.get("journey_id"),
            "source_kind": "PUBLIC_FORM", "inbound_channel": "PUBLIC_FORM",
            "candidate_state": CandidateState.RECEIVED.value,
            "current_policy_version_id": role["current_policy_version_id"],
            "current_assessment_id": None, "current_decision_id": None,
            "artifact_ids": [artifact_id], "withdrawal": None,
            "retention_status": "ACTIVE", "processing_status": "FOUNDER_REVIEW_REQUIRED",
            "automatic_assessment_allowed": False, "external_actions": [],
            "request_fingerprint": request_fingerprint,
            "consent_receipt_id": consent_receipt_id,
            "intake_mode": "LOCAL_STAGED", "synthetic": False,
            "created_at": now, "updated_at": now,
        }
        identity = {
            "schema_version": 1, "candidate_id": candidate_id,
            "workspace_id": role["workspace_id"], "role_id": role_id,
            "candidate_application_id": application_id,
            "ciphertext": base64.b64encode(identity_ciphertext).decode(),
            "nonce": base64.b64encode(identity_nonce).decode(),
            "wrapped_key": base64.b64encode(wrapped_key).decode(),
            "wrap_nonce": base64.b64encode(wrap_nonce).decode(),
            "aad_hash": hashlib.sha256((aad_prefix + "\x1fidentity").encode()).hexdigest(),
            "identity_dedup_hash": "hmac-sha256:" + hmac.new(
                self._key, (role["workspace_id"] + "\x1f" + normalized_email).encode(),
                hashlib.sha256).hexdigest(),
            "notice_receipts": [], "consent_receipts": [{
                "receipt_id": consent_receipt_id, "notice_version": "public-intake-v1",
                "accepted_at": now,
            }],
            "retention_status": "ACTIVE", "legal_hold": False,
            "intake_mode": "LOCAL_STAGED", "synthetic": False,
            "created_at": now, "updated_at": now,
        }
        artifact = {
            "schema_version": 1, "artifact_id": artifact_id,
            "workspace_id": role["workspace_id"], "role_id": role_id,
            "candidate_application_id": application_id,
            "scope": "HIRING_RESTRICTED", "sensitivity": "HIRING_RESTRICTED",
            "storage_name": storage_name,
            "ciphertext_sha256": hashlib.sha256(encrypted_resume).hexdigest(),
            "source_sha256": hashlib.sha256(resume_bytes).hexdigest(),
            "content_type": checked.get("content_type") or content_type,
            "resume_nonce": base64.b64encode(resume_nonce).decode(),
            "general_search_registered": resource_policy["general_search"],
            "session_resource_registered": resource_policy["session_resource"],
            "profile_eligible": resource_policy["founder_profile"],
            "company_knowledge_eligible": resource_policy["company_knowledge"],
            "intake_mode": "LOCAL_STAGED", "synthetic": False,
            "created_at": now,
        }
        audit_id = stable_id("audit", application_id, "public-intake")
        audit = {
            "schema_version": 1, "audit_id": audit_id,
            "workspace_id": role["workspace_id"], "actor_id": "public_applicant",
            "action": "hiring.public_application_received",
            "resource_type": "candidate_application", "resource_id": application_id,
            "role_id": role_id, "safe_metadata": {
                "channel": "PUBLIC_FORM", "policy_version_id": role["current_policy_version_id"],
                "consent_receipt_id": consent_receipt_id,
            }, "created_at": now,
        }
        committed = await self.store.atomic_compare_and_set([
            AtomicMutation("candidate_applications", application_id, None,
                           record=application),
            AtomicMutation("candidate_identities", candidate_id, None,
                           record=identity),
            AtomicMutation("hiring_candidate_artifacts", artifact_id, None,
                           record=artifact),
            AtomicMutation("audit", audit_id, None, record=audit),
        ])
        if not committed:
            storage.delete_artifact(storage_name)
            existing = await self.store.get("candidate_applications", application_id)
            if existing and existing.get("request_fingerprint") == request_fingerprint:
                return {"status": "success", "duplicate": True,
                        "application_reference": existing.get("candidate_code"),
                        "application_status": existing.get("candidate_state")}
            return _error("intake_concurrency_conflict",
                          "The application could not be committed. Try again.", 409)
        return {"status": "success", "duplicate": False,
                "application_reference": candidate_code,
                "application_status": CandidateState.RECEIVED.value}


def _error(code: str, message: str, http_status: int) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}
