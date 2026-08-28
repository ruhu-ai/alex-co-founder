"""Google API adapter for *separate* H4S Gmail and Calendar test accounts.

This adapter is deliberately not built on ``google_oauth.get_credentials`` or
the normal mailbox/calendar adapters. Its only credential source is the named
H4S test-account secret selected by deployment configuration and revalidated
against the binding immediately before every provider call.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

from services import secrets
from services.hiring_sandbox_config import configured_test_connector, subject_hash


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "failed", "error": True, "error_code": code,
            "message": message}


class H4SProviderConfigurationError(Exception):
    """A closed sandbox deployment prerequisite is absent or has drifted."""


def _provider_error(exc: Exception) -> dict[str, Any]:
    """Map provider failures into data; never disclose provider bodies/tokens."""
    message = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timeout" in message:
        return {"status": "uncertain", "uncertainty_reason": "provider_timeout"}
    if any(token in message for token in ("401", "invalid_grant", "credential", "oauth")):
        return _error("auth_required", "H4S test-account authorization needs attention.")
    if any(token in message for token in ("403", "scope", "permission")):
        return _error("scope_missing", "H4S test-account scope is insufficient.")
    if any(token in message for token in ("400", "404", "invalid")):
        return _error("provider_rejected", "The test provider rejected the action.")
    return {"status": "uncertain", "uncertainty_reason": "provider_unavailable"}


class H4SGoogleEffectAdapter:
    """Execute and reconcile H4S effects through Google APIs only."""

    async def preflight(self, *, binding: dict[str, Any]) -> dict[str, Any]:
        """Prove the binding names the configured test identity before approval.

        This is deliberately credential-free: an absent or mismatched deployment
        configuration must not consume a founder's exact approval merely to
        discover that no provider call can be made.
        """
        provider_kind = str(binding.get("provider_kind") or "")
        configured = configured_test_connector(provider_kind)
        if (not configured
                or binding.get("connector_grant_id") != configured.connector_grant_id
                or binding.get("provider_account_subject_hash")
                != configured.provider_account_subject_hash):
            return _error("sandbox_provider_not_configured",
                          "The required H4S test-account provider is not configured.")
        if (not os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
                or not os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()):
            return _error("sandbox_provider_not_configured",
                          "The H4S Google OAuth client is not configured.")
        return {"status": "success"}

    async def execute(self, *, action_kind: str, binding: dict[str, Any],
                      normalized_destinations: list[str],
                      rendered_payload: dict[str, Any], action_id: str,
                      causal_token: str) -> dict[str, Any]:
        try:
            if action_kind == "H4S_SEND_EMAIL":
                return await self._send_email(
                    binding, normalized_destinations, rendered_payload,
                    action_id, causal_token)
            if action_kind == "H4S_CREATE_CALENDAR_EVENT":
                return await self._create_calendar_event(
                    binding, normalized_destinations, rendered_payload, action_id)
            if action_kind == "H4S_UPDATE_CALENDAR_EVENT":
                return await self._update_calendar_event(
                    binding, normalized_destinations, rendered_payload, action_id)
            if action_kind == "H4S_CANCEL_CALENDAR_EVENT":
                return await self._cancel_calendar_event(binding, rendered_payload)
            return _error("invalid_contract", "Unknown H4S effect kind.")
        except H4SProviderConfigurationError:
            return _error("sandbox_provider_not_configured",
                          "The required H4S test-account provider is not configured.")
        except Exception as exc:  # no provider exception escapes to agent/UI
            return _provider_error(exc)

    async def reconcile(self, *, action: dict[str, Any],
                        binding: dict[str, Any]) -> dict[str, Any]:
        """Search provider-owned deterministic identities; never resend/rebook."""
        try:
            if action.get("action_kind") == "H4S_SEND_EMAIL":
                return await self._reconcile_email(action, binding)
            if action.get("action_kind") == "H4S_CREATE_CALENDAR_EVENT":
                return await self._reconcile_calendar(action, binding)
            if action.get("action_kind") == "H4S_UPDATE_CALENDAR_EVENT":
                return await self._reconcile_calendar(action, binding)
            if action.get("action_kind") == "H4S_CANCEL_CALENDAR_EVENT":
                return await self._reconcile_calendar_cancel(action, binding)
            return _error("invalid_contract", "Unknown H4S effect kind.")
        except H4SProviderConfigurationError:
            return _error("sandbox_provider_not_configured",
                          "The required H4S test-account provider is not configured.")
        except Exception as exc:
            return _provider_error(exc)

    async def fetch_inbound_reply(self, *, binding: dict[str, Any],
                                  provider_message_id: str) -> dict[str, Any]:
        """Fetch only the verified metadata needed for H4S reply correlation.

        This is invoked by the authenticated sandbox worker, never a browser
        request. MIME body, attachments, subject and untrusted free text are
        excluded from the Gmail field set and never returned or persisted.
        """
        if not provider_message_id or len(provider_message_id) > 512:
            return _error("invalid_contract", "Sandbox Gmail message identity is invalid.")
        try:
            credentials = await self._credentials(binding, "GMAIL_TEST")
            from googleapiclient.discovery import build
            response = await asyncio.to_thread(
                lambda: build("gmail", "v1", credentials=credentials,
                              cache_discovery=False).users().messages().get(
                                  userId="me", id=provider_message_id,
                                  format="metadata",
                                  metadataHeaders=[
                                      "Message-ID", "In-Reply-To",
                                      "X-CoFounder-H4S-Causal-Token", "From",
                                      "Auto-Submitted",
                                  ]).execute())
            headers = {
                str(item.get("name") or "").casefold(): str(item.get("value") or "")
                for item in ((response.get("payload") or {}).get("headers") or [])
            }
            sender = parseaddr(headers.get("from", ""))[1].strip().casefold()
            return {"status": "success", "provider_message_id": str(response.get("id") or ""),
                    "provider_thread_id": str(response.get("threadId") or ""),
                    "in_reply_to_message_id": headers.get("in-reply-to", "").strip(),
                    "causal_token": headers.get("x-cofounder-h4s-causal-token", "").strip(),
                    "sender_address": sender,
                    "message_kind": ("INBOX" if "INBOX" in (response.get("labelIds") or [])
                                     else "NON_INBOX"),
                    "auto_submitted": bool(headers.get("auto-submitted", "").strip()
                                           and headers.get("auto-submitted", "").strip().casefold() != "no")}
        except H4SProviderConfigurationError:
            return _error("sandbox_provider_not_configured",
                          "The required H4S test-account provider is not configured.")
        except Exception as exc:
            return _provider_error(exc)

    async def _credentials(self, binding: dict[str, Any], provider_kind: str):
        configured = configured_test_connector(provider_kind)
        if (not configured
                or binding.get("provider_kind") != provider_kind
                or binding.get("connector_grant_id") != configured.connector_grant_id
                or binding.get("provider_account_subject_hash")
                != configured.provider_account_subject_hash):
            raise H4SProviderConfigurationError("H4S test binding is not configured")
        client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise H4SProviderConfigurationError("Google OAuth client is not configured")
        refresh_token = await asyncio.to_thread(secrets.get, configured.refresh_token_secret)
        import google.auth.transport.requests
        import google.oauth2.credentials
        from googleapiclient.discovery import build

        credentials = google.oauth2.credentials.Credentials(
            token=None, refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token", client_id=client_id,
            client_secret=client_secret, scopes=None)
        await asyncio.to_thread(credentials.refresh, google.auth.transport.requests.Request())
        info = await asyncio.to_thread(
            lambda: build("oauth2", "v2", credentials=credentials,
                          cache_discovery=False).tokeninfo(
                              access_token=credentials.token).execute())
        granted = frozenset(str(info.get("scope") or "").split())
        if not configured.required_scopes.issubset(granted):
            raise PermissionError("H4S scope drift")
        if subject_hash(str(info.get("user_id") or "")) != configured.provider_account_subject_hash:
            raise PermissionError("H4S account subject drift")
        return credentials

    async def _send_email(self, binding: dict[str, Any], destinations: list[str],
                          payload: dict[str, Any], action_id: str,
                          causal_token: str) -> dict[str, Any]:
        if (not destinations or any("\n" in value or "\r" in value for value in destinations)
                or any("\n" in str(payload.get(key, "")) or "\r" in str(payload.get(key, ""))
                       for key in ("subject", "body"))):
            return _error("invalid_contract", "H4S email payload is invalid.")
        credentials = await self._credentials(binding, "GMAIL_TEST")
        message_id = f"<{action_id}@h4s.invalid>"
        message = EmailMessage()
        message["To"] = ", ".join(destinations)
        message["Subject"] = str(payload["subject"])
        message["Message-ID"] = message_id
        message["X-CoFounder-H4S-Action"] = action_id
        message["X-CoFounder-H4S-Causal-Token"] = causal_token
        message.set_content(str(payload["body"]))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        from googleapiclient.discovery import build
        response = await asyncio.to_thread(
            lambda: build("gmail", "v1", credentials=credentials,
                          cache_discovery=False).users().messages().send(
                              userId="me", body={"raw": raw}).execute())
        provider_id = str(response.get("id") or "")
        if not provider_id:
            return {"status": "uncertain", "uncertainty_reason": "missing_provider_receipt"}
        return {"status": "success", "provider_effect_id": provider_id,
                "result_ref": {"thread_id": str(response.get("threadId") or ""),
                               "causal_message_id_hash": "sha256:" + hashlib.sha256(
                                   message_id.encode()).hexdigest()}}

    async def _create_calendar_event(self, binding: dict[str, Any], destinations: list[str],
                                     payload: dict[str, Any], action_id: str) -> dict[str, Any]:
        if not destinations:
            return _error("invalid_contract", "H4S Calendar needs a test attendee.")
        credentials = await self._credentials(binding, "CALENDAR_TEST")
        # Google event IDs accept lower-case hexadecimal; the deterministic id
        # is both provider idempotency and reconciliation evidence.
        event_id = "h4s" + hashlib.sha256(action_id.encode()).hexdigest()[:40]
        event = {
            "id": event_id, "summary": str(payload["summary"]),
            "description": str(payload["description"]),
            "start": {"dateTime": str(payload["start"]), "timeZone": str(payload["timezone"])},
            "end": {"dateTime": str(payload["end"]), "timeZone": str(payload["timezone"])},
            "attendees": [{"email": address} for address in destinations],
            "extendedProperties": {"private": {"cofounder_h4s_action": action_id,
                                                   "cofounder_h4s_last_action": action_id}},
        }
        if str(payload.get("conference")) == "true":
            event["conferenceData"] = {"createRequest": {"requestId": event_id}}
        from googleapiclient.discovery import build
        response = await asyncio.to_thread(
            lambda: build("calendar", "v3", credentials=credentials,
                          cache_discovery=False).events().insert(
                              calendarId="primary", body=event, sendUpdates="all",
                              conferenceDataVersion=1).execute())
        provider_id = str(response.get("id") or "")
        if not provider_id:
            return {"status": "uncertain", "uncertainty_reason": "missing_provider_receipt"}
        return {"status": "success", "provider_effect_id": provider_id,
                "result_ref": {"event_id": provider_id,
                               "html_link": str(response.get("htmlLink") or "")[:280]}}

    async def _update_calendar_event(self, binding: dict[str, Any], destinations: list[str],
                                     payload: dict[str, Any], action_id: str) -> dict[str, Any]:
        if not destinations or not payload.get("target_event_id"):
            return _error("invalid_contract", "H4S Calendar update is invalid.")
        credentials = await self._credentials(binding, "CALENDAR_TEST")
        event = {
            "summary": str(payload["summary"]), "description": str(payload["description"]),
            "start": {"dateTime": str(payload["start"]), "timeZone": str(payload["timezone"])},
            "end": {"dateTime": str(payload["end"]), "timeZone": str(payload["timezone"])},
            "attendees": [{"email": address} for address in destinations],
            "extendedProperties": {"private": {
                "cofounder_h4s_action": str(payload["target_action_id"]),
                "cofounder_h4s_last_action": action_id}},
        }
        if str(payload.get("conference")) == "true":
            event["conferenceData"] = {"createRequest": {
                "requestId": "h4s" + hashlib.sha256(action_id.encode()).hexdigest()[:40]}}
        from googleapiclient.discovery import build
        response = await asyncio.to_thread(
            lambda: build("calendar", "v3", credentials=credentials,
                          cache_discovery=False).events().update(
                              calendarId="primary", eventId=str(payload["target_event_id"]),
                              body=event, sendUpdates="all", conferenceDataVersion=1).execute())
        provider_id = str(response.get("id") or "")
        if not provider_id:
            return {"status": "uncertain", "uncertainty_reason": "missing_provider_receipt"}
        return {"status": "success", "provider_effect_id": provider_id,
                "result_ref": {"event_id": provider_id,
                               "html_link": str(response.get("htmlLink") or "")[:280]}}

    async def _cancel_calendar_event(self, binding: dict[str, Any],
                                     payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("target_event_id") or "")
        if not event_id:
            return _error("invalid_contract", "H4S Calendar cancellation is invalid.")
        credentials = await self._credentials(binding, "CALENDAR_TEST")
        from googleapiclient.discovery import build
        await asyncio.to_thread(
            lambda: build("calendar", "v3", credentials=credentials,
                          cache_discovery=False).events().delete(
                              calendarId="primary", eventId=event_id, sendUpdates="all").execute())
        return {"status": "success", "provider_effect_id": event_id,
                "result_ref": {"event_id": event_id, "cancelled": "true"}}

    async def _reconcile_email(self, action: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
        credentials = await self._credentials(binding, "GMAIL_TEST")
        needle = f"<{action['action_id']}@h4s.invalid>"
        from googleapiclient.discovery import build
        response = await asyncio.to_thread(
            lambda: build("gmail", "v1", credentials=credentials,
                          cache_discovery=False).users().messages().list(
                              userId="me", q=f"rfc822msgid:{needle}", maxResults=2).execute())
        matches = response.get("messages") or []
        if len(matches) == 1:
            return {"status": "success", "provider_effect_id": str(matches[0].get("id") or ""),
                    "result_ref": {"reconciled": "gmail_rfc822_message_id"}}
        return {"status": "uncertain", "uncertainty_reason": "provider_evidence_inconclusive"}

    async def _reconcile_calendar(self, action: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
        credentials = await self._credentials(binding, "CALENDAR_TEST")
        payload = (action.get("exact_action") or {}).get("payload") or {}
        event_id = (str(payload.get("target_event_id") or "")
                    if action.get("action_kind") == "H4S_UPDATE_CALENDAR_EVENT"
                    else "h4s" + hashlib.sha256(str(action["action_id"]).encode()).hexdigest()[:40])
        from googleapiclient.discovery import build
        try:
            response = await asyncio.to_thread(
                lambda: build("calendar", "v3", credentials=credentials,
                              cache_discovery=False).events().get(
                                  calendarId="primary", eventId=event_id).execute())
        except Exception as exc:
            return _provider_error(exc)
        properties = ((response.get("extendedProperties") or {}).get("private") or {})
        if (str(response.get("id") or "") == event_id
                and properties.get("cofounder_h4s_last_action") == action["action_id"]):
            return {"status": "success", "provider_effect_id": event_id,
                    "result_ref": {"reconciled": "calendar_event_id"}}
        return {"status": "uncertain", "uncertainty_reason": "provider_evidence_inconclusive"}

    async def _reconcile_calendar_cancel(self, action: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
        credentials = await self._credentials(binding, "CALENDAR_TEST")
        event_id = str(((action.get("exact_action") or {}).get("payload") or {}).get(
            "target_event_id") or "")
        from googleapiclient.discovery import build
        try:
            await asyncio.to_thread(
                lambda: build("calendar", "v3", credentials=credentials,
                              cache_discovery=False).events().get(
                                  calendarId="primary", eventId=event_id).execute())
        except Exception as exc:
            if "404" in str(exc):
                return {"status": "success", "provider_effect_id": event_id,
                        "result_ref": {"reconciled": "calendar_event_absent"}}
            return _provider_error(exc)
        return {"status": "uncertain", "uncertainty_reason": "provider_evidence_inconclusive"}
