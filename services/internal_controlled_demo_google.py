"""Google provider boundary for the closed internal Ruhu demonstration."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from email.message import EmailMessage
from typing import Any

from services import google_oauth
from services.internal_controlled_demo import configured_accounts


def _failed(code: str) -> dict[str, Any]:
    return {"status": "failed", "error_code": code}


class InternalDemoGoogleAdapter:
    """Execute only fixed recap and calendar actions as pinned Alex account."""

    @staticmethod
    async def _alex_credentials(required_scope: str) -> tuple[Any | None, dict[str, Any] | None]:
        """Refresh and prove both the account subject and one exact scope."""
        accounts = configured_accounts()
        if not accounts:
            return None, _failed("internal_demo_account_mismatch")
        credentials = await asyncio.to_thread(google_oauth.get_credentials, "alex")
        if credentials is None:
            return None, _failed("internal_demo_alex_oauth_missing")
        try:
            from googleapiclient.discovery import build
            oauth = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
            info = await asyncio.to_thread(lambda: oauth.tokeninfo(
                access_token=credentials.token).execute())
            identity = await asyncio.to_thread(lambda: oauth.userinfo().get().execute())
            subject = "sha256:" + hashlib.sha256(
                str(identity.get("id") or "").encode()).hexdigest()
            scopes = set(str(info.get("scope") or "").split())
            if (not identity.get("id") or subject != accounts["alex_subject_hash"]
                    or required_scope not in scopes):
                return None, _failed("internal_demo_account_or_scope_mismatch")
            return credentials, None
        except Exception:
            return None, {"status": "uncertain", "uncertainty_reason": "provider_unavailable"}

    @staticmethod
    def _http_status(exc: Exception) -> int:
        try:
            return int(getattr(getattr(exc, "resp", None), "status", 0)
                       or getattr(exc, "status_code", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    async def _accounts_match(exact_action: dict[str, Any]) -> bool:
        accounts = configured_accounts()
        if not (accounts and exact_action.get("alex_address") == accounts["alex_address"]
                and exact_action.get("founder_address") == accounts["founder_address"]
                and exact_action.get("alex_subject_hash") == accounts["alex_subject_hash"]
                and exact_action.get("founder_subject_hash") == accounts["founder_subject_hash"]):
            return False
        # The founder is an invitee/recipient rather than the acting account,
        # but its stable Google identity is still a deployment pin.  Check it
        # immediately before an effect instead of trusting a value written
        # during an earlier setup flow.
        subject = await asyncio.to_thread(google_oauth.account_subject_hash, "founder")
        email = await asyncio.to_thread(google_oauth.account_email, "founder")
        return subject == accounts["founder_subject_hash"] and email.casefold() == accounts["founder_address"]

    async def execute(self, *, exact_action: dict[str, Any], action_id: str) -> dict[str, Any]:
        action_kind = str(exact_action.get("action_kind") or "")
        if not await self._accounts_match(exact_action):
            return _failed("internal_demo_account_mismatch")
        if action_kind == "INTERNAL_DEMO_SEND_RECAP":
            return await self._send_recap(exact_action, action_id)
        if action_kind in {"INTERNAL_DEMO_CREATE_CALENDAR_EVENT",
                           "INTERNAL_DEMO_UPDATE_CALENDAR_EVENT",
                           "INTERNAL_DEMO_CANCEL_CALENDAR_EVENT"}:
            return await self._calendar_effect(exact_action, action_id)
        return _failed("internal_demo_provider_not_configured")

    async def _send_recap(self, exact_action: dict[str, Any], action_id: str) -> dict[str, Any]:
        accounts = configured_accounts()
        assert accounts is not None
        credentials, failure = await self._alex_credentials(
            "https://www.googleapis.com/auth/gmail.send")
        if failure:
            return failure
        try:
            from googleapiclient.discovery import build
            template = exact_action.get("template") or {}
            message = EmailMessage()
            message["To"] = accounts["founder_address"]
            message["Subject"] = str(template.get("subject") or "")
            message["Message-ID"] = f"<{action_id}@internal-demo.ruhu.invalid>"
            message["X-CoFounder-Internal-Demo"] = str(exact_action.get("demo_run_id") or "")
            message.set_content(str(template.get("body") or ""))
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            response = await asyncio.to_thread(lambda: build(
                "gmail", "v1", credentials=credentials, cache_discovery=False).users().messages().send(
                    userId="me", body={"raw": raw}).execute())
            provider_id = str(response.get("id") or "")
            return ({"status": "success", "provider_effect_id": provider_id,
                     "result_ref": {"thread_id": str(response.get("threadId") or ""),
                                    "message_id_hash": "sha256:" + hashlib.sha256(
                                        message["Message-ID"].encode()).hexdigest()}}
                    if provider_id else {"status": "uncertain"})
        except Exception:
            return {"status": "uncertain", "uncertainty_reason": "provider_unavailable"}

    async def _calendar_effect(self, exact_action: dict[str, Any], action_id: str) -> dict[str, Any]:
        """Create/update/cancel one deterministic internal-only invite.

        `sendUpdates=all` is intentional: the only attendee is the deployment
        pinned founder account, so the visible invite is a useful receipt in
        the demo rather than an invisible side effect.
        """
        accounts = configured_accounts()
        assert accounts is not None
        event_id = str(exact_action.get("calendar_event_id") or "")
        calendar = exact_action.get("calendar")
        template = exact_action.get("template")
        if (not event_id or not isinstance(calendar, dict) or not isinstance(template, dict)
                or not calendar.get("start_at") or not calendar.get("end_at")):
            return _failed("internal_demo_calendar_contract_invalid")
        credentials, failure = await self._alex_credentials(
            "https://www.googleapis.com/auth/calendar.events")
        if failure:
            return failure
        try:
            from googleapiclient.discovery import build
            service = await asyncio.to_thread(lambda: build(
                "calendar", "v3", credentials=credentials, cache_discovery=False))
            kind = exact_action["action_kind"]
            if kind == "INTERNAL_DEMO_CANCEL_CALENDAR_EVENT":
                try:
                    await asyncio.to_thread(lambda: service.events().delete(
                        calendarId="primary", eventId=event_id, sendUpdates="all").execute())
                except Exception as exc:
                    if self._http_status(exc) not in {404, 410}:
                        raise
                return {"status": "success", "provider_effect_id": event_id,
                        "result_ref": {"event_id": event_id, "event_status": "cancelled"}}
            body = {
                "id": event_id, "summary": str(template.get("summary") or ""),
                "description": str(template.get("description") or ""),
                "start": {"dateTime": str(calendar["start_at"]),
                          "timeZone": str(calendar.get("timezone") or "Africa/Lagos")},
                "end": {"dateTime": str(calendar["end_at"]),
                        "timeZone": str(calendar.get("timezone") or "Africa/Lagos")},
                "attendees": [{"email": accounts["founder_address"]}],
                "extendedProperties": {"private": {
                    "cofounder_internal_demo_action": action_id,
                    "cofounder_internal_demo": "true"}},
            }
            if kind == "INTERNAL_DEMO_UPDATE_CALENDAR_EVENT":
                event = await asyncio.to_thread(lambda: service.events().update(
                    calendarId="primary", eventId=event_id, body=body,
                    sendUpdates="all").execute())
            else:
                try:
                    event = await asyncio.to_thread(lambda: service.events().insert(
                        calendarId="primary", body=body, sendUpdates="all").execute())
                except Exception as exc:
                    if self._http_status(exc) != 409:
                        raise
                    event = await asyncio.to_thread(lambda: service.events().get(
                        calendarId="primary", eventId=event_id).execute())
            return {"status": "success", "provider_effect_id": str(event.get("id") or event_id),
                    "result_ref": {"event_id": str(event.get("id") or event_id),
                                   "event_status": str(event.get("status") or "confirmed"),
                                   "html_link": str(event.get("htmlLink") or "")}}
        except Exception as exc:
            if self._http_status(exc) in {400, 401, 403, 404, 410, 422}:
                return _failed("internal_demo_calendar_rejected")
            return {"status": "uncertain", "uncertainty_reason": "provider_unavailable"}

    async def reconcile(self, *, exact_action: dict[str, Any], action_id: str) -> dict[str, Any]:
        """Read the deterministic provider handle after an uncertain write."""
        action_kind = str(exact_action.get("action_kind") or "")
        if not await self._accounts_match(exact_action):
            return _failed("internal_demo_account_mismatch")
        if action_kind == "INTERNAL_DEMO_SEND_RECAP":
            # Gmail's send response does not offer a stable lookup key without
            # broad search. Refuse to guess or resend; manual inspection is the
            # honest recovery path for an ambiguous recap send.
            return {"status": "uncertain", "uncertainty_reason": "gmail_reconciliation_unavailable"}
        event_id = str(exact_action.get("calendar_event_id") or "")
        if not event_id:
            return _failed("internal_demo_calendar_contract_invalid")
        credentials, failure = await self._alex_credentials(
            "https://www.googleapis.com/auth/calendar.events")
        if failure:
            return failure
        try:
            from googleapiclient.discovery import build
            service = await asyncio.to_thread(lambda: build(
                "calendar", "v3", credentials=credentials, cache_discovery=False))
            event = await asyncio.to_thread(lambda: service.events().get(
                calendarId="primary", eventId=event_id).execute())
            cancelled = str(event.get("status") or "") == "cancelled"
            if action_kind == "INTERNAL_DEMO_CANCEL_CALENDAR_EVENT":
                if cancelled:
                    return {"status": "success", "provider_effect_id": event_id,
                            "result_ref": {"event_id": event_id, "event_status": "cancelled"}}
                return _failed("internal_demo_calendar_not_cancelled")
            if cancelled:
                return _failed("internal_demo_calendar_not_found")
            return {"status": "success", "provider_effect_id": str(event.get("id") or event_id),
                    "result_ref": {"event_id": str(event.get("id") or event_id),
                                   "event_status": str(event.get("status") or "confirmed"),
                                   "html_link": str(event.get("htmlLink") or "")}}
        except Exception as exc:
            if self._http_status(exc) in {404, 410}:
                if action_kind == "INTERNAL_DEMO_CANCEL_CALENDAR_EVENT":
                    return {"status": "success", "provider_effect_id": event_id,
                            "result_ref": {"event_id": event_id, "event_status": "cancelled"}}
                return _failed("internal_demo_calendar_not_found")
            return {"status": "uncertain", "uncertainty_reason": "provider_unavailable"}
