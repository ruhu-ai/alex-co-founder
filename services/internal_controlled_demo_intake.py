"""One-shot, hash-pinned Gmail import for the internal FDE demonstration."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from email.utils import getaddresses, parseaddr
from io import BytesIO
from typing import Any, Callable

from services.actor_identity import ActorPrincipal
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import stable_id, utc_now
from services.hiring_service import HiringService
from services.internal_controlled_demo import InternalControlledDemoService, policy
from services.internal_controlled_demo_google import InternalDemoGoogleAdapter


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}


def _parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    found = [payload]
    for child in payload.get("parts") or []:
        found.extend(_parts(child))
    return found


class InternalDemoInboxImportService:
    """Import one exact fixture mail; no watch, generic search, or free text."""

    def __init__(self, store: DurableStore | None = None,
                 gmail_factory: Callable[[], Any] | None = None,
                 hiring: HiringService | None = None):
        self.store = store or production_store()
        self.runs = InternalControlledDemoService(self.store)
        self.gmail_factory = gmail_factory
        self.hiring = hiring

    async def import_fixture(self, *, principal: ActorPrincipal, demo_run_id: str) -> dict[str, Any]:
        run = await self.runs._active_run(principal, demo_run_id)
        if run.get("error"):
            return run
        fixture = policy()["fixture"]
        expected_hash = os.environ.get("HIRING_INTERNAL_DEMO_APPLICATION_PDF_SHA256", "").strip()
        if not expected_hash.startswith("sha256:") or len(expected_hash) != 71:
            return _error("internal_demo_fixture_not_configured", "The demo PDF hash is not configured.", 503)
        credentials = None
        if self.gmail_factory is None:
            credentials, failure = await InternalDemoGoogleAdapter._alex_credentials(
                "https://www.googleapis.com/auth/gmail.readonly")
            if failure:
                return failure
        service = await asyncio.to_thread(self._gmail, credentials)
        if service is None:
            return _error("internal_demo_alex_oauth_missing", "Alex's mailbox is not connected.", 503)
        query = (f'from:{policy()["accounts"]["fixture_sender_address"]} '
                 f'to:{policy()["accounts"]["alex_sender_address"]} '
                 f'subject:"{fixture["expected_subject"]}" has:attachment')
        try:
            listed = await asyncio.to_thread(lambda: service.users().messages().list(
                userId="me", q=query, maxResults=5).execute())
            matches = listed.get("messages") or []
            if len(matches) != 1:
                return _error("fixture_message_ambiguous", "Expected exactly one matching demo application message.")
            message_id = str(matches[0].get("id") or "")
            message = await asyncio.to_thread(lambda: service.users().messages().get(
                userId="me", id=message_id, format="full").execute())
        except Exception:
            return _error("fixture_message_unavailable", "The declared demo message could not be read.", 503)
        validated = await asyncio.to_thread(self._validate_message, service, message, expected_hash)
        if validated.get("error"):
            return validated
        application_id = stable_id("idemoapp", demo_run_id, message_id)
        role_id = str(run["demo_run"].get("role_id") or "")
        if not role_id or self.hiring is None:
            return _error("internal_demo_role_not_ready",
                          "The internal demo is not attached to a candidate workflow.", 503)
        external_event_id = stable_id("idemohevent", demo_run_id, message_id)
        source_sha256 = expected_hash
        await self.store.create("external_events", external_event_id, {
            "schema_version": 2, "event_id": external_event_id,
            "workspace_id": principal.workspace_id, "founder_id": principal.workspace_id,
            "role_id": role_id, "connector_id": "alex_mail",
            "provider_event_id": message_id,
            "provider_thread_id": str(message.get("threadId") or ""),
            "event_kind": "HIRING_APPLICATION_EMAIL", "correlation_status": "EXACT",
            "correlation_basis": "INTERNAL_DEMO_HASH_PINNED_FIXTURE",
            "processing_status": "RECEIVED", "payload_hash": source_sha256,
            "received_at": utc_now(), "synthetic": True,
            "synthetic_namespace": "synthetic_hiring_ruhu_fde",
            "fixture_id": "fixture_ruhu_fde_walkthrough", "version": 1,
        })
        candidate = await self.hiring.ingest_synthetic_application(
            role_id=role_id, provider_message_id=message_id,
            provider_thread_id=str(message.get("threadId") or ""),
            identity_fields={"name": "Amira Okafor",
                             "email": "candidate.internal.demo@ruhu.invalid"},
            blocks=[
                {"block_id": "customer-deployment", "source_kind": "RESUME",
                 "criterion_ids": ["criterion_customer_deployment"],
                 "text": "Led customer-facing AI deployments from discovery through launch."},
                {"block_id": "incident-response", "source_kind": "RESUME",
                 "criterion_ids": ["criterion_incident_response"],
                 "text": "Handled production customer incidents and documented corrective learning."},
            ], source_sha256=source_sha256, external_event_id=external_event_id,
            synthetic_guard={"synthetic": True,
                             "synthetic_namespace": "synthetic_hiring_ruhu_fde",
                             "fixture_id": "fixture_ruhu_fde_walkthrough"})
        if candidate.get("error"):
            return candidate
        event = await self.store.get("external_events", external_event_id)
        if event and event.get("processing_status") != "APPLIED":
            await self.store.compare_and_set(
                "external_events", external_event_id, int(event["version"]), {
                    "processing_status": "APPLIED",
                    "candidate_application_id": candidate["candidate_application_id"],
                    "processed_at": utc_now(),
                })
        row = {"schema_version": 1, "application_id": application_id,
               "demo_run_id": demo_run_id, "workspace_id": principal.workspace_id,
               "internal_demo": True, "fixture_id": fixture["fixture_id"],
               "state": "IMPORTED", "provider_message_id": message_id,
               "provider_thread_id": str(message.get("threadId") or ""),
               "attachment_sha256": expected_hash,
               "evidence": {"candidate_name": "Amira Okafor", "role": "Forward Deployment Engineer",
                            "document_pages": validated["pages"], "source_verified": True},
               "candidate_application_id": candidate["candidate_application_id"],
               "assessment_id": candidate.get("assessment_id"),
               "created_at": utc_now(), "updated_at": utc_now(), "version": 1}
        created = await self.store.create("internal_demo_applications", application_id, row)
        existing = row if created else await self.store.get("internal_demo_applications", application_id)
        return {"status": "success", "duplicate": not created, "application_id": application_id,
                "candidate_application_id": candidate["candidate_application_id"],
                "assessment_id": candidate.get("assessment_id"),
                "evidence": existing.get("evidence") if existing else row["evidence"]}

    def _gmail(self, credentials=None):
        if self.gmail_factory is not None:
            return self.gmail_factory()
        if credentials is None:
            return None
        from googleapiclient.discovery import build
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    @staticmethod
    def _validate_message(service: Any, message: dict[str, Any], expected_hash: str) -> dict[str, Any]:
        fixture = policy()["fixture"]
        headers = {str(h.get("name") or "").casefold(): str(h.get("value") or "")
                   for h in ((message.get("payload") or {}).get("headers") or [])}
        sender = parseaddr(headers.get("from", ""))[1].casefold()
        recipients = {address.casefold() for _, address in getaddresses(
            [headers.get("to", "")]) if address}
        if (sender != policy()["accounts"]["fixture_sender_address"]
                or policy()["accounts"]["alex_sender_address"] not in recipients
                or headers.get("subject", "") != fixture["expected_subject"]):
            return _error("fixture_message_mismatch",
                          "The message sender, recipient, or subject is not the declared fixture.")
        payload = message.get("payload") or {}
        body = "".join(base64.urlsafe_b64decode((part.get("body") or {}).get("data", "") + "===").decode(
            "utf-8", "ignore") for part in _parts(payload) if part.get("mimeType") == "text/plain")
        if fixture["required_body_marker"] not in body:
            return _error("fixture_message_mismatch", "The message does not carry the required demo marker.")
        attachment = next((part for part in _parts(payload)
                           if part.get("filename") == fixture["attachment_filename"]
                           and (part.get("body") or {}).get("attachmentId")), None)
        if attachment is None:
            return _error("fixture_attachment_missing", "The declared demo PDF attachment is missing.")
        attachment_id = attachment["body"]["attachmentId"]
        try:
            blob = base64.urlsafe_b64decode(service.users().messages().attachments().get(
                userId="me", messageId=message["id"], id=attachment_id).execute()["data"] + "===")
            digest = "sha256:" + hashlib.sha256(blob).hexdigest()
            if digest != expected_hash:
                return _error("fixture_attachment_mismatch", "The attachment hash does not match the declared fixture.")
            from pypdf import PdfReader
            pages = len(PdfReader(BytesIO(blob)).pages)
            return {"status": "success", "pages": pages}
        except Exception:
            return _error("fixture_attachment_invalid", "The declared attachment is not a valid demo PDF.")
