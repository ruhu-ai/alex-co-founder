"""Real, candidate-bound Hiring communication and interview coordination.

This is deliberately separate from H4S.  A live action is admitted only for a
non-synthetic public application whose current human decision is ``ADVANCE``.
Candidate identity, connector identity, recipients and provider targets are
resolved server-side.  One fresh Founder consent activates a candidate-bound,
time-bounded ``SCHEDULE_INTERVIEW`` goal; every provider mutation is still
written to ``external_actions`` before the provider is called.  Provider
ambiguity is terminal ``UNCERTAIN`` until reconciliation; the service never
blind-retries an email or calendar mutation.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services import calendar_adapter, connection_registry, google_oauth
from services.actor_identity import ActorPrincipal, WorkspaceRole, authorize
from services.capability_registry import require_controlled_action
from services.durable_store import AtomicMutation, DurableStore, production_store
from services.hiring_approval_service import request_approval, validate_approval_claim
from services.hiring_contracts import canonical_hash, stable_id, utc_now
from services.hiring_public_intake import HiringPublicIntakeService
from services.hiring_scheduling_agent import (
    SCHEDULING_SKILL_VERSION,
    draft_scheduling_reply,
    interpret_scheduling_reply,
)
from services.workflow_runtime import WorkflowRuntime

LIVE_ACTIONS = frozenset({
    "HIRING_SEND_EMAIL",
    "HIRING_CREATE_INTERVIEW",
    "HIRING_UPDATE_INTERVIEW",
    "HIRING_CANCEL_INTERVIEW",
})
MANDATE_ACTION = "HIRING_COORDINATE_INTERVIEW"
_EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")
_MAX_SUBJECT = 240
_MAX_BODY = 12_000
_ALEX_ADDRESS = "alex@ruhu.ai"
_MANDATE_DAYS = 14
_DEFAULT_INTERVIEW_MINUTES = 60
_INTERVIEW_DURATION_OPTIONS = frozenset({30, 45, 60, 90})
_RFC822_MESSAGE_ID = re.compile(r"<[^\s<>@\r\n]{1,200}@[^\s<>@\r\n]{1,200}>")


def _error(code: str, message: str, http_status: int = 409) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, "http_status": http_status}


def _kill_switch() -> bool:
    return os.environ.get("HIRING_LIVE_OPERATIONS_KILL_SWITCH", "0") == "1"


def _founder_copy_address() -> str:
    value = os.environ.get("HIRING_FOUNDER_COPY_EMAIL", "").strip().casefold()
    return value if _EMAIL.fullmatch(value) else ""


def _message_id(value: Any) -> str:
    """Return one injection-safe RFC 5322 message id or an empty string."""
    text = str(value or "").strip()
    match = _RFC822_MESSAGE_ID.fullmatch(text)
    return match.group(0) if match else ""


def _reference_chain(*values: Any) -> str:
    """Return a bounded, de-duplicated RFC 5322 References chain."""
    ordered: list[str] = []
    for value in values:
        for token in _RFC822_MESSAGE_ID.findall(str(value or "")):
            if token not in ordered:
                ordered.append(token)
    return " ".join(ordered[-20:])[:4000]


def _provider_error(exc: Exception) -> dict[str, Any]:
    status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
    if isinstance(exc, TimeoutError) or status >= 500 or status == 0:
        return {"status": "uncertain", "error": True,
                "error_code": "provider_outcome_unconfirmed",
                "uncertainty_reason": "provider_unavailable"}
    if status in {401, 403}:
        return _error("auth_required", "Reconnect the required Google connector.", 409)
    return _error("provider_rejected", "Google rejected the exact approved action.", 409)


def _calendar_event_id(action_id: str) -> str:
    return "hiring" + hashlib.sha256(action_id.encode()).hexdigest()[:40]


class HiringProviderAdapter(Protocol):
    async def preflight(self, *, workspace_id: str, connector_id: str) -> dict[str, Any]: ...

    async def execute(self, *, workspace_id: str, action_kind: str,
                      exact_action: dict[str, Any], action_id: str,
                      provider_request_id: str) -> dict[str, Any]: ...

    async def reconcile(self, *, workspace_id: str,
                        action: dict[str, Any]) -> dict[str, Any]: ...


class GoogleHiringProviderAdapter:
    """Normal account-bound Alex Mail and Founder Calendar provider boundary."""

    async def preflight(self, *, workspace_id: str,
                        connector_id: str) -> dict[str, Any]:
        gate = await connection_registry.authorize_connector_operation(
            workspace_id, connector_id)
        if gate.get("error"):
            return gate
        connection = gate.get("connection")
        if not connection or connection.get("status") != "CONNECTED":
            return _error("auth_required", f"Connect {connector_id} before continuing.", 409)
        required = set(google_oauth.SCOPE_MAP[connector_id])
        granted = set(connection.get("granted_scopes") or [])
        if not required.issubset(granted):
            return _error("scope_missing", f"Reconnect {connector_id} with its exact scopes.")
        account = connection_registry.account_for_connector(connector_id)
        credentials = await asyncio.to_thread(
            google_oauth.get_credentials, account, workspace_id, connector_id)
        if credentials is None:
            return _error("auth_required", f"Reconnect {connector_id} before continuing.")
        return {"status": "success", "connection_id": connection["connection_id"]}

    async def execute(self, *, workspace_id: str, action_kind: str,
                      exact_action: dict[str, Any], action_id: str,
                      provider_request_id: str) -> dict[str, Any]:
        try:
            if action_kind == "HIRING_SEND_EMAIL":
                return await self._send_email(
                    workspace_id, exact_action, action_id, provider_request_id)
            if action_kind == "HIRING_CREATE_INTERVIEW":
                return await self._create_interview(workspace_id, exact_action, action_id)
            if action_kind == "HIRING_UPDATE_INTERVIEW":
                return await self._update_interview(workspace_id, exact_action, action_id)
            if action_kind == "HIRING_CANCEL_INTERVIEW":
                return await self._cancel_interview(workspace_id, exact_action)
            return _error("invalid_contract", "Unknown Hiring action.", 400)
        except Exception as exc:
            return _provider_error(exc)

    async def reconcile(self, *, workspace_id: str,
                        action: dict[str, Any]) -> dict[str, Any]:
        try:
            if action.get("action_kind") == "HIRING_SEND_EMAIL":
                return await self._reconcile_email(workspace_id, action)
            return await self._reconcile_calendar(workspace_id, action)
        except Exception as exc:
            return _provider_error(exc)

    @staticmethod
    async def _credentials(workspace_id: str, connector_id: str):
        account = connection_registry.account_for_connector(connector_id)
        credentials = await asyncio.to_thread(
            google_oauth.get_credentials, account, workspace_id, connector_id)
        if credentials is None:
            raise PermissionError("connector credential unavailable")
        return credentials

    async def _send_email(self, workspace_id: str, exact: dict[str, Any],
                          action_id: str, provider_request_id: str) -> dict[str, Any]:
        credentials = await self._credentials(workspace_id, "alex_mail")
        recipients = list(exact["recipients"])
        payload = dict(exact["payload"])
        message = EmailMessage()
        message["To"] = recipients[0]
        if len(recipients) == 2:
            message["Cc"] = recipients[1]
        message["From"] = "Alex (Ruhu AI co-founder) <alex@ruhu.ai>"
        message["Subject"] = str(payload["subject"])
        rfc822_id = (
            f"<cofounder-hiring-{hashlib.sha256(provider_request_id.encode()).hexdigest()[:32]}"
            "@ruhu.ai>")
        message["Message-ID"] = rfc822_id
        message["X-CoFounder-Hiring-Action"] = action_id
        message["X-CoFounder-Hiring-Candidate"] = str(
            exact["candidate_application_id"])
        prior_thread_id = str(payload.get("provider_thread_id") or "")
        if prior_thread_id:
            in_reply_to = _message_id(payload.get("in_reply_to"))
            references = _reference_chain(payload.get("references"), in_reply_to)
            if not in_reply_to or not references:
                return _error(
                    "reply_anchor_missing",
                    "The verified email reply anchor is unavailable.", 409)
            message["In-Reply-To"] = in_reply_to
            message["References"] = references
        message.set_content(str(payload["body"]))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        from googleapiclient.discovery import build

        request_body = {"raw": raw}
        if prior_thread_id:
            request_body["threadId"] = prior_thread_id
        response = await asyncio.to_thread(
            lambda: build("gmail", "v1", credentials=credentials,
                          cache_discovery=False).users().messages().send(
                              userId="me", body=request_body).execute())
        provider_id = str(response.get("id") or "")
        if not provider_id:
            return {"status": "uncertain", "uncertainty_reason": "missing_provider_receipt"}
        return {"status": "success", "provider_effect_id": provider_id,
                "result_ref": {"provider_thread_id": str(response.get("threadId") or ""),
                               "rfc822_message_id": rfc822_id}}

    async def _create_interview(self, workspace_id: str, exact: dict[str, Any],
                                action_id: str) -> dict[str, Any]:
        credentials = await self._credentials(workspace_id, "calendar")
        payload = dict(exact["payload"])
        event_id = _calendar_event_id(action_id)
        event = self._event_body(exact, action_id, event_id)
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
                               "meet_link": str(response.get("hangoutLink") or "")[:280],
                               "start": str(payload["start"]), "end": str(payload["end"])}}

    async def _update_interview(self, workspace_id: str, exact: dict[str, Any],
                                action_id: str) -> dict[str, Any]:
        credentials = await self._credentials(workspace_id, "calendar")
        target = str(exact["payload"]["target_event_id"])
        event = self._event_body(exact, action_id, target)
        event.pop("id", None)
        from googleapiclient.discovery import build

        response = await asyncio.to_thread(
            lambda: build("calendar", "v3", credentials=credentials,
                          cache_discovery=False).events().update(
                              calendarId="primary", eventId=target, body=event,
                              sendUpdates="all", conferenceDataVersion=1).execute())
        return {"status": "success", "provider_effect_id": str(response.get("id") or target),
                "result_ref": {"event_id": str(response.get("id") or target),
                               "meet_link": str(response.get("hangoutLink") or "")[:280]}}

    async def _cancel_interview(self, workspace_id: str,
                                exact: dict[str, Any]) -> dict[str, Any]:
        credentials = await self._credentials(workspace_id, "calendar")
        target = str(exact["payload"]["target_event_id"])
        from googleapiclient.discovery import build

        await asyncio.to_thread(
            lambda: build("calendar", "v3", credentials=credentials,
                          cache_discovery=False).events().delete(
                              calendarId="primary", eventId=target,
                              sendUpdates="all").execute())
        return {"status": "success", "provider_effect_id": target,
                "result_ref": {"event_id": target, "cancelled": "true"}}

    @staticmethod
    def _event_body(exact: dict[str, Any], action_id: str,
                    event_id: str) -> dict[str, Any]:
        payload = dict(exact["payload"])
        return {
            "id": event_id,
            "summary": str(payload["summary"]),
            "description": str(payload["description"]),
            "start": {"dateTime": str(payload["start"]),
                      "timeZone": str(payload["timezone"])},
            "end": {"dateTime": str(payload["end"]),
                    "timeZone": str(payload["timezone"])},
            "attendees": [{"email": address} for address in exact["recipients"]],
            "conferenceData": {"createRequest": {"requestId": (
                "hiring" + hashlib.sha256(action_id.encode()).hexdigest()[:40])}},
            "extendedProperties": {"private": {
                "cofounder_hiring_application": str(exact["candidate_application_id"]),
                "cofounder_hiring_action": action_id,
            }},
        }

    async def _reconcile_email(self, workspace_id: str,
                               action: dict[str, Any]) -> dict[str, Any]:
        credentials = await self._credentials(workspace_id, "alex_mail")
        message_id = str((action.get("result_ref") or {}).get(
            "rfc822_message_id") or "")
        if not message_id:
            request = str(action.get("provider_request_id") or "")
            message_id = (
                f"<cofounder-hiring-{hashlib.sha256(request.encode()).hexdigest()[:32]}"
                "@ruhu.ai>")
        from googleapiclient.discovery import build

        response = await asyncio.to_thread(
            lambda: build("gmail", "v1", credentials=credentials,
                          cache_discovery=False).users().messages().list(
                              userId="me", q=f"rfc822msgid:{message_id.strip('<>')}",
                              maxResults=2).execute())
        matches = response.get("messages") or []
        if len(matches) == 1:
            return {"status": "success", "provider_effect_id": str(matches[0]["id"]),
                    "result_ref": {"reconciled": "gmail_rfc822_message_id"}}
        if not matches:
            return {"status": "failed", "error_code": "provider_effect_absent"}
        return {"status": "uncertain", "uncertainty_reason": "provider_evidence_ambiguous"}

    async def _reconcile_calendar(self, workspace_id: str,
                                  action: dict[str, Any]) -> dict[str, Any]:
        credentials = await self._credentials(workspace_id, "calendar")
        exact = dict(action.get("exact_action") or {})
        payload = dict(exact.get("payload") or {})
        target = str(payload.get("target_event_id") or _calendar_event_id(
            str(action["action_id"])))
        from googleapiclient.discovery import build

        try:
            response = await asyncio.to_thread(
                lambda: build("calendar", "v3", credentials=credentials,
                              cache_discovery=False).events().get(
                                  calendarId="primary", eventId=target).execute())
        except Exception as exc:
            status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
            if status in {404, 410}:
                expected_absent = action.get("action_kind") == "HIRING_CANCEL_INTERVIEW"
                return ({"status": "success", "provider_effect_id": target,
                         "result_ref": {"cancelled": "true"}}
                        if expected_absent else
                        {"status": "failed", "error_code": "provider_effect_absent"})
            raise
        properties = ((response.get("extendedProperties") or {}).get("private") or {})
        action_kind = str(action.get("action_kind") or "")
        if action_kind == "HIRING_CANCEL_INTERVIEW":
            if response.get("status") == "cancelled":
                return {"status": "success", "provider_effect_id": target,
                        "result_ref": {"cancelled": "true"}}
            return {"status": "failed", "error_code": "provider_effect_present"}
        expected_payload = dict(exact.get("payload") or {})
        provider_start = str((response.get("start") or {}).get("dateTime") or "")
        provider_end = str((response.get("end") or {}).get("dateTime") or "")
        if (properties.get("cofounder_hiring_application") == exact.get(
                "candidate_application_id")
                and properties.get("cofounder_hiring_action") == action.get("action_id")
                and provider_start == str(expected_payload.get("start") or "")
                and provider_end == str(expected_payload.get("end") or "")):
            return {"status": "success", "provider_effect_id": target,
                    "result_ref": {"reconciled": "calendar_event_id"}}
        return {"status": "uncertain", "uncertainty_reason": "provider_evidence_mismatch"}


class HiringCoordinationService:
    """Durable live Hiring lane after one attributed Founder ADVANCE."""

    def __init__(self, store: DurableStore | None = None,
                 adapter: HiringProviderAdapter | None = None,
                 intake: HiringPublicIntakeService | None = None):
        self.store = store or production_store()
        self.adapter = adapter or GoogleHiringProviderAdapter()
        self.intake = intake or HiringPublicIntakeService(store=self.store)
        self.runtime = WorkflowRuntime(self.store)

    async def projection(self, *, principal: ActorPrincipal,
                         application_id: str) -> dict[str, Any]:
        context = await self._context(principal, application_id, require_advanced=False)
        if context.get("error"):
            return context
        items = [
            row for row in await self._workspace_rows(
                "hiring_coordination_items", principal.workspace_id)
            if row.get("candidate_application_id") == application_id
        ][:200]
        replies = [
            row for row in await self._workspace_rows(
                "hiring_reply_correlations", principal.workspace_id)
            if row.get("candidate_application_id") == application_id
            and row.get("mode") == "LIVE"
        ][:200]
        actions = [
            row for row in await self._workspace_rows(
                "external_actions", principal.workspace_id)
            if row.get("approval_domain") == "HIRING"
            and row.get("application_id") == application_id
            and row.get("action_kind") in LIVE_ACTIONS
        ][:500]
        safe_items = [{key: row.get(key) for key in (
            "coordination_id", "item_kind", "status", "action_kind", "approval_id",
            "mandate_id",
            "subject", "body", "recipients_masked", "slot_options", "start", "end",
            "timezone", "duration_minutes", "target_event_id", "action_id",
            "error_code", "created_at",
            "updated_at")} for row in items]
        for safe, source in zip(safe_items, items, strict=True):
            if (not source.get("mandate_id")
                    and source.get("status") in {
                        "AWAITING_APPROVAL", "APPROVED", "EXECUTING"}):
                safe["status"] = "SUPERSEDED_BY_COORDINATION_CONSENT"
        safe_replies = [{key: row.get(key) for key in (
            "correlation_id", "status", "message_kind", "safe_subject",
            "safe_excerpt", "auto_submitted", "provider_thread_id",
            "injection_suspected", "continuation_status",
            "continuation_error_code", "created_at")}
            for row in replies]
        safe_actions = [{key: row.get(key) for key in (
            "action_id", "action_kind", "status", "approval_id", "provider_effect_id",
            "error_code", "uncertainty_reason", "result_ref", "created_at", "updated_at")}
            for row in actions]
        application = context["application"]
        active = await self._active_mandate(
            principal.workspace_id, application_id, context=context)
        return {"status": "success", "live": True,
                "kill_switch": _kill_switch(),
                "founder_copy_available": bool(_founder_copy_address()),
                "eligible": application.get("candidate_state") == "ADVANCED",
                "candidate_state": application.get("candidate_state"),
                "mandate": ({key: active.get(key) for key in (
                    "mandate_id", "status", "copy_founder", "confirmed_slots",
                    "duration_minutes", "email_count", "calendar_action_count",
                    "goal_kind", "goal_status", "goal_step", "current_event_id",
                    "scheduling_skill_version",
                    "last_reply_correlation_id", "last_transition_at",
                    "scheduling_window_start", "scheduling_window_end",
                    "founder_timezone", "activated_at", "expires_at")}
                    if active else None),
                "items": safe_items, "replies": safe_replies, "actions": safe_actions}

    async def prepare_contact(self, *, principal: ActorPrincipal,
                              application_id: str, client_request_id: str,
                              reply: bool = False,
                              copy_founder: bool = False,
                              duration_minutes: int = _DEFAULT_INTERVIEW_MINUTES,
                              subject: str | None = None,
                              body: str | None = None,
                              availability_hash: str = "",
                              preview_only: bool = False) -> dict[str, Any]:
        context = await self._context(
            principal, application_id, require_advanced=True)
        if context.get("error"):
            return context
        if _kill_switch():
            return _error("hiring_operations_killed", "Hiring external operations are disabled.", 503)
        mandate = await self._active_mandate(
            principal.workspace_id, application_id, context=context)
        if reply and not mandate:
            return _error(
                "coordination_mandate_required",
                "The Founder must approve interview coordination once.", 409)
        if mandate:
            candidate_email = str(mandate["candidate_email"])
            candidate_name = str(mandate.get("candidate_first_name") or "there")
            copy_founder = bool(mandate.get("copy_founder"))
            duration_minutes = int(
                mandate.get("duration_minutes") or _DEFAULT_INTERVIEW_MINUTES)
            if reply:
                availability = await self._available_slots(
                    principal.workspace_id, duration_minutes=duration_minutes)
                if availability.get("error"):
                    return availability
                slots = list(availability["slots"])
                current = await self.store.get(
                    "hiring_coordination_mandates", str(mandate["mandate_id"]))
                if not current or not await self.store.compare_and_set(
                        "hiring_coordination_mandates", str(mandate["mandate_id"]),
                        int(current["version"]),
                        {"confirmed_slots": slots, "updated_at": utc_now()}):
                    return _error(
                        "concurrency_conflict",
                        "Founder availability changed concurrently.")
                mandate = await self.store.get(
                    "hiring_coordination_mandates", str(mandate["mandate_id"]))
            else:
                slots = list(mandate["confirmed_slots"])
        else:
            if duration_minutes not in _INTERVIEW_DURATION_OPTIONS:
                return _error(
                    "invalid_contract",
                    "Interview duration must be 30, 45, 60, or 90 minutes.", 400)
            fresh = await self._context(
                principal, application_id, require_advanced=True,
                require_fresh=True)
            if fresh.get("error"):
                return fresh
            context = fresh
            identity = context["identity"]
            candidate_email = str(identity["email"]).strip().casefold()
            candidate_name = str(identity["name"]).strip().split()[0]
            availability = await self._available_slots(
                principal.workspace_id, duration_minutes=duration_minutes)
            if availability.get("error"):
                return availability
            slots = availability["slots"]
            if copy_founder and not _founder_copy_address():
                return _error(
                    "founder_copy_unconfigured",
                    "Configure the Founder copy address before including it.", 409)
        role = context["role"]
        default_subject = (
            f"Interview availability — {role.get('role_title', 'your application')}")
        provider_thread_id = ""
        in_reply_to = ""
        references = ""
        if reply:
            default_subject = f"Re: {default_subject}"
            prior_sends = [
                row for row in await self._workspace_rows(
                    "external_actions", principal.workspace_id,
                    descending=True)
                if row.get("approval_domain") == "HIRING"
                and row.get("application_id") == application_id
                and row.get("action_kind") == "HIRING_SEND_EMAIL"
                and row.get("status") == "SUCCEEDED"
                and (row.get("result_ref") or {}).get("provider_thread_id")
            ]
            if not prior_sends:
                return _error(
                    "reply_thread_missing",
                    "No verified applicant email thread is available.", 409)
            provider_thread_id = str(
                (prior_sends[0].get("result_ref") or {})["provider_thread_id"])
            latest_reply_id = str((mandate or {}).get(
                "last_reply_correlation_id") or "")
            latest_reply = (await self.store.get(
                "hiring_reply_correlations", latest_reply_id)
                if latest_reply_id else None)
            if (latest_reply
                    and latest_reply.get("candidate_application_id") == application_id
                    and latest_reply.get("correlation_basis") in {
                        "PROVIDER_THREAD", "RFC822_REPLY_ANCHOR"}
                    and _message_id(latest_reply.get("rfc822_message_id"))):
                # Gmail may assign an applicant reply to a new thread even when
                # it carries an exact RFC reply anchor. Continue on the thread
                # that now owns the applicant's message, not the obsolete send
                # receipt thread.
                provider_thread_id = str(
                    latest_reply.get("provider_thread_id") or provider_thread_id)
                in_reply_to = _message_id(
                    latest_reply.get("rfc822_message_id"))
                references = _reference_chain(
                    latest_reply.get("references"),
                    latest_reply.get("in_reply_to"), in_reply_to)
            if not in_reply_to:
                in_reply_to = _message_id(
                    (prior_sends[0].get("result_ref") or {}).get(
                        "rfc822_message_id"))
                references = _reference_chain(references, in_reply_to)
            if not in_reply_to or not references:
                return _error(
                    "reply_anchor_missing",
                    "The verified applicant email reply anchor is unavailable.", 409)
        rendered = "\n".join(
            f"{index + 1}. {slot['display']}" for index, slot in enumerate(slots))
        opening = ("Thank you for your reply." if reply else
                   "Thank you for applying and for the evidence you shared.")
        default_body = (
            f"Hi {candidate_name},\n\n{opening} The Founder has reviewed your application "
            "and asked me to advance you to an interview conversation.\n\n"
            f"The interview is expected to last approximately {duration_minutes} minutes.\n\n"
            "The Founder is currently available at these times:\n"
            f"{rendered}\n\nPlease reply with the option that works best, or suggest alternatives "
            "in the same time zone. I will coordinate the final invitation with the Founder.\n\n"
            "Alex\nAI co-founder, Ruhu")
        current_availability_hash = canonical_hash({
            "duration_minutes": duration_minutes,
            "slots": slots,
        })
        if preview_only:
            if reply or mandate:
                return _error(
                    "invalid_contract",
                    "Only a new initial invitation can be previewed.", 409)
            recipients = [candidate_email]
            if copy_founder:
                recipients.append(_founder_copy_address())
            return {
                "status": "success", "preview_only": True,
                "availability_hash": current_availability_hash,
                "item": {
                    "item_kind": "INITIAL_CONTACT",
                    "subject": default_subject, "body": default_body,
                    "recipients_masked": [
                        self._mask_email(value) for value in recipients],
                    "slot_options": slots,
                    "duration_minutes": duration_minutes,
                },
            }
        custom_draft = subject is not None or body is not None
        if custom_draft:
            if (not availability_hash
                    or availability_hash != current_availability_hash):
                return _error(
                    "founder_availability_changed",
                    "Founder availability changed; review the refreshed invitation.", 409)
            final_subject = str(subject or "").strip()
            final_body = str(body or "").strip()
        else:
            final_subject, final_body = default_subject, default_body
        recipients = [candidate_email]
        if copy_founder:
            recipients.append(_founder_copy_address())
        return await self._prepare_item(
            principal=principal, context=context, client_request_id=client_request_id,
            item_kind="AVAILABILITY_REPLY" if reply else "INITIAL_CONTACT",
            action_kind="HIRING_SEND_EMAIL", recipients=recipients,
            payload={"subject": final_subject, "body": final_body,
                     "provider_thread_id": provider_thread_id,
                     "in_reply_to": in_reply_to,
                     "references": references,
                     "candidate_recipient": candidate_email},
            slot_options=slots, mandate=mandate,
            candidate_first_name=candidate_name,
            copy_founder=copy_founder,
            duration_minutes=duration_minutes)

    async def prepare_interview(self, *, principal: ActorPrincipal,
                                application_id: str, start: str, end: str,
                                timezone_name: str, client_request_id: str,
                                target_event_id: str = "",
                                cancel: bool = False) -> dict[str, Any]:
        context = await self._context(
            principal, application_id, require_advanced=True)
        if context.get("error"):
            return context
        if _kill_switch():
            return _error("hiring_operations_killed", "Hiring external operations are disabled.", 503)
        mandate = await self._active_mandate(
            principal.workspace_id, application_id, context=context)
        if not mandate:
            return _error(
                "coordination_mandate_required",
                "The Founder must approve interview coordination once.", 409)
        prior = await self._owned_event(principal.workspace_id, application_id,
                                        target_event_id) if target_event_id else None
        if target_event_id and not prior:
            return _error("interview_not_found", "The interview is not owned by this candidate.", 404)
        if cancel:
            action_kind = "HIRING_CANCEL_INTERVIEW"
            payload = {"target_event_id": target_event_id}
            recipients: list[str] = []
            item_kind = "INTERVIEW_CANCELLATION"
        else:
            parsed = self._validate_interval(start, end, timezone_name)
            if parsed.get("error"):
                return parsed
            available = await self._slot_still_available(
                principal.workspace_id, parsed["start"], parsed["end"])
            if available.get("error"):
                return available
            role_title = str(context["role"].get("role_title") or "Role")
            action_kind = ("HIRING_UPDATE_INTERVIEW" if target_event_id
                           else "HIRING_CREATE_INTERVIEW")
            item_kind = ("INTERVIEW_UPDATE" if target_event_id else "INTERVIEW_BOOKING")
            recipients = [str(mandate["candidate_email"]), _ALEX_ADDRESS]
            payload = {
                "summary": f"Interview — {role_title}",
                "description": ("Founder-approved interview for candidate "
                                f"{context['application'].get('candidate_code')}. "
                                "Alex is included for coordination. Candidate evidence remains "
                                "in the restricted Hiring workspace."),
                "start": parsed["start"], "end": parsed["end"],
                "timezone": timezone_name,
                "target_event_id": target_event_id,
            }
        return await self._prepare_item(
            principal=principal, context=context, client_request_id=client_request_id,
            item_kind=item_kind, action_kind=action_kind, recipients=recipients,
            payload=payload, slot_options=[], mandate=mandate)

    async def execute(self, *, principal: ActorPrincipal, application_id: str,
                      coordination_id: str, approval_id: str) -> dict[str, Any]:
        context = await self._context(principal, application_id, require_advanced=True)
        if context.get("error"):
            return context
        if _kill_switch():
            return _error("hiring_operations_killed", "Hiring external operations are disabled.", 503)
        item = await self.store.get("hiring_coordination_items", coordination_id)
        if (not item or item.get("workspace_id") != principal.workspace_id
                or item.get("candidate_application_id") != application_id
                or item.get("status") not in {
                    "AWAITING_APPROVAL", "AUTHORIZED", "APPROVED", "EXECUTING",
                    "SUCCEEDED", "FAILED", "UNCERTAIN"}):
            return _error("coordination_not_found", "Coordination item is unavailable.", 404)
        exact = dict(item["exact_action"])
        connector_id = str(exact["connector_id"])
        checked = self._validate_payload(
            str(exact.get("action_kind") or ""),
            list(exact.get("recipients") or []),
            dict(exact.get("payload") or {}),
        )
        if checked.get("error"):
            return checked
        preflight = await self.adapter.preflight(
            workspace_id=principal.workspace_id, connector_id=connector_id)
        if preflight.get("status") != "success":
            return preflight
        action_kind = str(exact["action_kind"])
        try:
            capability = require_controlled_action(action_kind, connector_id)
        except ValueError:
            return _error("capability_disabled", "Hiring action is not registered.", 403)
        action_id = stable_id("hiringaction", principal.workspace_id,
                              canonical_hash(exact))
        current = await self.store.get("external_actions", action_id)
        if current:
            return await self._existing_action(current, principal.workspace_id, exact)
        mandate_id = str(item.get("mandate_id") or "")
        mandate = await self.store.get(
            "hiring_coordination_mandates", mandate_id) if mandate_id else None
        activating = mandate is None
        approval = None
        mandate_exact = dict(item.get("mandate_exact") or {})
        if activating:
            validated = await validate_approval_claim(
                principal=principal, approval_id=approval_id,
                run_id=str(context["application"]["run_id"]),
                policy_version_id=str(context["role"]["current_policy_version_id"]),
                action_kind=MANDATE_ACTION, exact_action=mandate_exact,
                store=self.store, require_fresh=True)
            if validated.get("error"):
                return validated
            approval = validated["approval"]
            preview = {
                **mandate_exact, "status": "ACTIVE",
                "expires_at": (datetime.now(timezone.utc) + timedelta(
                    days=int(mandate_exact["valid_days"]))).isoformat(),
                "email_count": 0, "calendar_action_count": 0,
            }
            allowed = self._mandate_allows_action(preview, action_kind, exact)
            if allowed.get("error"):
                return allowed
        else:
            approval_id = str(mandate.get("approval_id") or "")
            allowed = self._mandate_allows_action(mandate, action_kind, exact)
            if allowed.get("error"):
                return allowed
        now = utc_now()
        claim_id = stable_id("claim", approval_id, action_id)
        provider_request_id = stable_id("providerrequest", action_id, "1")
        row = {
            "schema_version": 2, "action_id": action_id,
            "workspace_id": principal.workspace_id,
            "founder_id": principal.workspace_id, "actor_id": principal.actor_id,
            "approval_domain": "HIRING", "action_domain": "HIRING",
            "application_id": application_id,
            "candidate_application_id": application_id,
            "session_id": str(context["application"]["run_id"]),
            "run_id": str(context["application"]["run_id"]),
            "role_id": str(context["application"]["role_id"]),
            "domain_ref": application_id, "action_kind": action_kind,
            "connector_id": connector_id,
            "connection_id": preflight.get("connection_id"),
            "capability_id": capability.capability_id,
            "capability_version": capability.semantic_version,
            "idempotency_key": action_id, "request_hash": canonical_hash(exact),
            "approval_id": approval_id, "claim_id": claim_id,
            "authorization_kind": "HIRING_COORDINATION_MANDATE",
            "mandate_id": mandate_id,
            "coordination_id": coordination_id, "exact_action": exact,
            "status": "PREPARED", "provider_started_at": None,
            "provider_request_id": provider_request_id,
            "provider_effect_id": None, "result_ref": {},
            "error_code": None, "uncertainty_reason": None,
            "synthetic": False, "created_at": now, "updated_at": now,
            "version": 1,
        }
        mutations = [
            AtomicMutation("external_actions", action_id, None, record=row),
            AtomicMutation("hiring_coordination_items", coordination_id,
                           int(item["version"]),
                           updates={"status": "APPROVED", "approval_id": approval_id,
                                    "action_id": action_id, "updated_at": now}),
        ]
        if activating:
            expires_at = (datetime.now(timezone.utc) + timedelta(
                days=int(mandate_exact["valid_days"]))).isoformat()
            mandate_record = {
                **mandate_exact,
                "schema_version": 1, "approval_id": approval_id,
                "status": "ACTIVE", "email_count": (
                    1 if action_kind == "HIRING_SEND_EMAIL" else 0),
                "calendar_action_count": (
                    0 if action_kind == "HIRING_SEND_EMAIL" else 1),
                "goal_kind": "SCHEDULE_INTERVIEW",
                "goal_status": "CONTACTING",
                "goal_step": "SEND_INITIAL_INVITATION",
                "current_event_id": "",
                "last_reply_correlation_id": "",
                "last_transition_at": now,
                "activated_by_actor_id": principal.actor_id,
                "activated_at": now, "expires_at": expires_at,
                "created_at": now, "updated_at": now, "version": 1,
            }
            mutations.extend((
                AtomicMutation("approvals", approval_id, int(approval["version"]),
                               updates={"status": "CLAIMED", "claim_id": claim_id,
                                        "claimed_action_id": action_id,
                                        "claimed_by_actor_id": principal.actor_id,
                                        "claimed_at": now, "updated_at": now}),
                AtomicMutation("hiring_coordination_mandates", mandate_id,
                               None, record=mandate_record),
            ))
        else:
            count_field = ("email_count" if action_kind == "HIRING_SEND_EMAIL"
                           else "calendar_action_count")
            mutations.append(AtomicMutation(
                "hiring_coordination_mandates", mandate_id,
                int(mandate["version"]),
                updates={count_field: int(mandate.get(count_field) or 0) + 1,
                         "updated_at": now}))
        committed = await self.store.atomic_compare_and_set(tuple(mutations))
        if not committed:
            return _error("concurrency_conflict", "Approval or coordination changed concurrently.")
        action = committed[("external_actions", action_id)]
        started_at = utc_now()
        start_mutations = [AtomicMutation(
            "external_actions", action_id, int(action["version"]),
            updates={"status": "EXECUTING",
                     "provider_started_at": started_at,
                     "consequence_start_committed_at": started_at,
                     "updated_at": started_at})]
        if activating:
            approval_now = committed[("approvals", approval_id)]
            start_mutations.append(AtomicMutation(
                "approvals", approval_id, int(approval_now["version"]),
                updates={"status": "CONSUMED", "consumed_at": started_at,
                         "terminal_action_id": action_id,
                         "updated_at": started_at}))
        started = await self.store.atomic_compare_and_set(tuple(start_mutations))
        if not started:
            return _error("concurrency_conflict", "Action start changed concurrently.")
        action = started[("external_actions", action_id)]
        try:
            result = await self.adapter.execute(
                workspace_id=principal.workspace_id, action_kind=action_kind,
                exact_action=exact, action_id=action_id,
                provider_request_id=provider_request_id)
        except Exception:
            result = {"status": "uncertain", "error": True,
                      "error_code": "provider_outcome_unconfirmed",
                      "uncertainty_reason": "provider_call_interrupted"}
        return await self._finish(item_id=coordination_id, action=action, result=result)

    async def reconcile(self, *, principal: ActorPrincipal, application_id: str,
                        action_id: str) -> dict[str, Any]:
        context = await self._context(principal, application_id, require_advanced=False)
        if context.get("error"):
            return context
        action = await self.store.get("external_actions", action_id)
        if (not action or action.get("workspace_id") != principal.workspace_id
                or action.get("application_id") != application_id
                or action.get("status") not in {"UNCERTAIN", "EXECUTING"}):
            return _error("reconciliation_required", "No uncertain Hiring action exists.", 404)
        result = await self.adapter.reconcile(
            workspace_id=principal.workspace_id, action=action)
        if result.get("status") not in {"success", "failed"}:
            return _error("reconciliation_required", "Provider evidence is inconclusive.", 503)
        return await self._finish(item_id=str(action["coordination_id"]),
                                  action=action, result=result)

    async def replay_misclassified_reply(
            self, *, workspace_id: str, correlation_id: str,
            execute: bool = False) -> dict[str, Any]:
        """Safely inspect or replay one exact old ``AUTOMATED_NOTICE`` receipt.

        Dry-run is the default and reads no provider content. Execute reloads
        only the immutable provider message referenced by the receipt, then
        repeats exact thread/sender/header checks. A duplicate, a genuinely
        automated message, a changed candidate/policy/mandate, or an already
        advanced receipt remains inert.
        """
        row = await self.store.get("hiring_reply_correlations", correlation_id)
        if (not row or row.get("workspace_id") != workspace_id
                or row.get("mode") != "LIVE"):
            return _error("reply_not_found", "Reply receipt is unavailable.", 404)
        eligible = (row.get("status") == "AUTOMATED_NOTICE"
                    and not row.get("continuation_status")
                    and bool(row.get("provider_message_id"))
                    and bool(row.get("provider_thread_id")))
        if not execute:
            return {"status": "success", "dry_run": True,
                    "eligible": eligible, "correlation_id": correlation_id,
                    "candidate_application_id": row.get(
                        "candidate_application_id")}
        if not eligible:
            return _error("reply_replay_not_eligible",
                          "Reply receipt is not eligible for replay.", 409)
        from services import alex_mailbox

        loaded = await alex_mailbox.load_provider_event(
            str(row["provider_message_id"]), workspace_id)
        if loaded.get("error"):
            return loaded
        event = dict(loaded["event"])
        if (event.get("id") != row.get("provider_message_id")
                or event.get("thread_id") != row.get("provider_thread_id")):
            return _error("reply_replay_source_changed",
                          "Provider source no longer matches the receipt.", 409)
        return await self.correlate_reply(
            workspace_id=workspace_id, provider_event=event,
            replay_existing=True)

    async def correlate_reply(self, *, workspace_id: str,
                              provider_event: dict[str, Any],
                              replay_existing: bool = False) -> dict[str, Any]:
        """Bind one Alex-mail event to one send; optionally repair a bad receipt.

        Exact thread + applicant sender is resolved before message semantics.
        Automated mail is admitted only from provider envelope/header evidence,
        never a keyword copied from the quoted invitation body.
        """
        thread_id = str(provider_event.get("thread_id") or "")[:512]
        provider_message_id = str(provider_event.get("id") or "")[:512]
        sender = parseaddr(str(provider_event.get("from") or ""))[1].strip().casefold()
        if not thread_id or not provider_message_id or not sender:
            return _error("reply_not_correlatable", "Reply metadata is incomplete.", 404)
        raw_subject = str(provider_event.get("subject") or "")
        raw_excerpt = str(provider_event.get("excerpt") or "")
        automated_text = f"{sender} {raw_subject}".casefold()
        explicit_auto = provider_event.get("automated")
        auto = (bool(explicit_auto) if explicit_auto is not None else any(
            marker in automated_text for marker in (
                "mailer-daemon", "postmaster@", "delivery status notification",
                "undeliverable", "automatic reply", "auto-reply",
                "out of office")))
        reply_anchors = set(_RFC822_MESSAGE_ID.findall(" ".join((
            str(provider_event.get("in_reply_to") or ""),
            str(provider_event.get("references") or ""),
        ))))
        candidates: list[dict[str, Any]] = []
        candidate_basis: dict[str, str] = {}
        for action_row in await self._workspace_rows(
                "external_actions", workspace_id, descending=True):
            result_ref = dict(action_row.get("result_ref") or {})
            thread_match = (
                str(result_ref.get("provider_thread_id") or "") == thread_id)
            action_message_id = _message_id(
                result_ref.get("rfc822_message_id"))
            anchor_match = bool(
                action_message_id and action_message_id in reply_anchors)
            if (action_row.get("approval_domain") != "HIRING"
                    or action_row.get("action_kind") != "HIRING_SEND_EMAIL"
                    or action_row.get("status") != "SUCCEEDED"
                    or not (thread_match or anchor_match)
                    or (not auto and sender != str(
                        ((action_row.get("exact_action") or {}).get(
                            "payload") or {}).get(
                                "candidate_recipient") or "").casefold())):
                continue
            candidates.append(action_row)
            candidate_basis[str(action_row["action_id"])] = (
                "PROVIDER_THREAD" if thread_match else "RFC822_REPLY_ANCHOR")
        application_ids = {
            str(row.get("application_id") or "") for row in candidates
        }
        if len(application_ids) != 1:
            return _error("reply_not_correlatable", "Reply is not uniquely linked.", 404)
        action = candidates[0]
        correlation_basis = candidate_basis[str(action["action_id"])]
        application_id = str(action["application_id"])
        correlation_id = stable_id("hiringreply", workspace_id,
                                   application_id, provider_message_id)
        subject = re.sub(r"\s+", " ", raw_subject)[:200]
        excerpt = re.sub(r"\s+", " ", raw_excerpt)[:1000]
        from services import browser_service

        injection_suspected = browser_service.scan_injection(
            " ".join((sender, subject, excerpt)))
        if injection_suspected:
            subject = "Message content withheld"
            excerpt = ("Review the candidate reply directly in Alex's mailbox; "
                       "instruction-shaped content was not imported.")
        row = {
            "schema_version": 1, "correlation_id": correlation_id,
            "workspace_id": workspace_id, "mode": "LIVE",
            "candidate_application_id": application_id,
            "candidate_run_id": str(action.get("run_id") or ""),
            "action_id": action["action_id"],
            "correlation_basis": correlation_basis,
            "provider_message_id": provider_message_id,
            "provider_thread_id": thread_id,
            "rfc822_message_id": _message_id(
                provider_event.get("rfc822_message_id")),
            "in_reply_to": _message_id(provider_event.get("in_reply_to")),
            "references": _reference_chain(provider_event.get("references")),
            "message_kind": "AUTOMATED" if auto else "APPLICANT_REPLY",
            "auto_submitted": auto,
            "automation_basis": str(
                provider_event.get("automation_basis") or "")[:80],
            "status": "AUTOMATED_NOTICE" if auto else "REPLY_RECEIVED",
            "safe_subject": subject, "safe_excerpt": excerpt,
            "content_authority": "UNTRUSTED_CANDIDATE_MESSAGE",
            "injection_suspected": injection_suspected,
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create(
            "hiring_reply_correlations", correlation_id, row)
        replayed = False
        current = (row if created else await self.store.get(
            "hiring_reply_correlations", correlation_id))
        if (not created and replay_existing and current
                and current.get("workspace_id") == workspace_id
                and current.get("candidate_application_id") == application_id
                and current.get("provider_message_id") == provider_message_id
                and current.get("provider_thread_id") == thread_id
                and current.get("status") == "AUTOMATED_NOTICE" and not auto):
            replay_commit = await self.store.compare_and_set(
                "hiring_reply_correlations", correlation_id,
                int(current["version"]), {
                    "message_kind": "APPLICANT_REPLY",
                    "auto_submitted": False,
                    "automation_basis": "",
                    "status": "REPLY_RECEIVED",
                    "safe_subject": subject,
                    "safe_excerpt": excerpt,
                    "replayed_at": utc_now(),
                    "updated_at": utc_now(),
                })
            replayed = bool(replay_commit)
            if replayed:
                current = await self.store.get(
                    "hiring_reply_correlations", correlation_id)
        continuation: dict[str, Any] | None = None
        if created or replayed:
            await self.runtime.append_event(
                str(action.get("run_id") or ""),
                event_kind=("CANDIDATE_REPLY_RECEIVED" if not auto
                            else "CANDIDATE_AUTOMATED_MAIL_RECEIVED"),
                idempotency_key=(f"hiring-reply-replay:{correlation_id}"
                                 if replayed else f"hiring-reply:{correlation_id}"),
                safe_payload={"correlation_id": correlation_id,
                              "message_kind": row["message_kind"]})
            if not auto and not injection_suspected:
                continuation = await self._continue_from_reply(
                    workspace_id=workspace_id, application_id=application_id,
                    correlation_id=correlation_id, excerpt=excerpt)
                latest = await self.store.get(
                    "hiring_reply_correlations", correlation_id)
                if latest:
                    await self.store.compare_and_set(
                    "hiring_reply_correlations", correlation_id,
                    int(latest["version"]), {
                        "continuation_status": (
                            "GOAL_ADVANCED" if not continuation.get("error")
                            else "GOAL_BLOCKED"),
                        "continuation_error_code": str(
                            continuation.get("error_code") or ""),
                        "updated_at": utc_now(),
                    })
        return {"status": "success", "duplicate": not created and not replayed,
                "replayed": replayed,
                "correlation_id": correlation_id,
                "candidate_application_id": application_id,
                "candidate_run_id": row["candidate_run_id"],
                "automated": auto,
                "continuation_status": (
                    continuation.get("status") if continuation else None),
                "continuation_error_code": (
                    continuation.get("error_code") if continuation else None)}

    async def _continue_from_reply(
            self, *, workspace_id: str, application_id: str,
            correlation_id: str, excerpt: str) -> dict[str, Any]:
        """Wake and advance the durable scheduling goal for one exact reply."""
        application = await self.store.get("candidate_applications", application_id)
        role = (await self.store.get("hiring_roles", str(
            application.get("role_id") or "")) if application else None)
        if (not application or not role
                or application.get("workspace_id") != workspace_id
                or application.get("candidate_state") != "ADVANCED"):
            return _error("coordination_mandate_invalid",
                          "The candidate is no longer in interview coordination.")
        context = {"status": "success", "application": application, "role": role}
        mandate = await self._active_mandate(
            workspace_id, application_id, context=context)
        if not mandate:
            return _error("coordination_mandate_required",
                          "The Founder coordination mandate is not active.")
        correlation = await self.store.get(
            "hiring_reply_correlations", correlation_id)
        if (correlation
                and correlation.get("correlation_basis") == "RFC822_REPLY_ANCHOR"
                and correlation.get("provider_thread_id")
                and correlation.get("provider_thread_id") != mandate.get(
                    "provider_thread_id")):
            correlated_action = await self.store.get(
                "external_actions", str(correlation.get("action_id") or ""))
            if (not correlated_action
                    or correlated_action.get("mandate_id") != mandate.get(
                        "mandate_id")
                    or correlated_action.get("application_id") != application_id):
                return _error(
                    "reply_not_correlatable",
                    "The reply anchor is outside the active interview mandate.", 409)
            migrated = await self.store.compare_and_set(
                "hiring_coordination_mandates", str(mandate["mandate_id"]),
                int(mandate["version"]), {
                    "provider_thread_id": str(
                        correlation["provider_thread_id"]),
                    "thread_migrated_from": str(
                        mandate.get("provider_thread_id") or ""),
                    "thread_migration_correlation_id": correlation_id,
                    "updated_at": utc_now(),
                })
            if not migrated:
                return _error(
                    "concurrency_conflict",
                    "The applicant email thread changed concurrently.")
            mandate = await self.store.get(
                "hiring_coordination_mandates", str(mandate["mandate_id"]))
        principal = ActorPrincipal(
            actor_id="agent:alex", workspace_id=workspace_id,
            role=WorkspaceRole.FOUNDER, session_auth_time=0,
            membership_version=0, principal_kind="WORKLOAD")
        transitioned = await self._transition_goal(
            mandate, goal_status="PROCESSING_REPLY",
            goal_step="INTERPRET_CANDIDATE_REPLY",
            correlation_id=correlation_id)
        if transitioned.get("error"):
            return transitioned
        mandate = transitioned["mandate"]
        current_event_id = str(mandate.get("current_event_id") or "")
        current_event = await self._current_owned_event(
            workspace_id, application_id)
        if not current_event_id:
            current_event_id = str((current_event or {}).get("event_id") or "")
        try:
            interpretation = await interpret_scheduling_reply(
                excerpt,
                offered_slots=list(mandate.get("confirmed_slots") or []),
                founder_timezone=str(
                    mandate.get("founder_timezone") or "Africa/Lagos"),
                duration_minutes=int(
                    mandate.get("duration_minutes") or
                    _DEFAULT_INTERVIEW_MINUTES),
                has_booking=bool(current_event_id),
                current_time=utc_now(),
                scheduling_window_end=str(
                    mandate.get("scheduling_window_end") or
                    mandate.get("expires_at") or utc_now()),
                current_booking=current_event,
            )
        except Exception:
            interpretation = {
                "status": "error", "error": True,
                "error_code": "scheduling_interpreter_unavailable",
            }
        intent = str(interpretation.get("intent") or "ASK_CLARIFICATION")
        selected_option = int(interpretation.get("selected_option") or 0)
        slots = list(mandate.get("confirmed_slots") or [])
        proposed_timezone = str(
            interpretation.get("timezone") or
            mandate.get("founder_timezone") or "Africa/Lagos")
        transitioned = await self._transition_goal(
            mandate, goal_status="PROCESSING_REPLY",
            goal_step="CHECK_FOUNDER_AVAILABILITY",
            correlation_id=correlation_id)
        if transitioned.get("error"):
            return transitioned
        mandate = transitioned["mandate"]

        candidates: list[dict[str, str]] = []
        if 0 < selected_option <= len(slots):
            candidates.append(dict(slots[selected_option - 1]))
        duration = int(
            mandate.get("duration_minutes") or _DEFAULT_INTERVIEW_MINUTES)
        for value in list(interpretation.get("proposed_starts") or []):
            try:
                start = datetime.fromisoformat(str(value))
                end = start + timedelta(minutes=duration)
            except ValueError:
                continue
            candidates.append({
                "start": start.isoformat(), "end": end.isoformat(),
                "timezone": proposed_timezone,
                "display": start.astimezone(
                    ZoneInfo(proposed_timezone)).strftime("%a %d %b, %H:%M %Z"),
            })

        refreshed: dict[str, Any] | None = None
        windows = list(interpretation.get("availability_windows") or [])
        excluded = list(interpretation.get("unavailable_windows") or [])
        if windows:
            refreshed = await self._available_slots(
                workspace_id, duration_minutes=duration)
            if not refreshed.get("error"):
                for slot in refreshed.get("slots") or []:
                    slot_start = datetime.fromisoformat(str(slot["start"]))
                    slot_end = datetime.fromisoformat(str(slot["end"]))
                    inside = any(
                        datetime.fromisoformat(window.split("/", 1)[0]) <= slot_start
                        and slot_end <= datetime.fromisoformat(
                            window.split("/", 1)[1])
                        for window in windows)
                    blocked = any(
                        slot_start < datetime.fromisoformat(
                            window.split("/", 1)[1])
                        and slot_end > datetime.fromisoformat(
                            window.split("/", 1)[0])
                        for window in excluded)
                    if inside and not blocked:
                        candidates.append(dict(slot))

        selected: dict[str, str] | None = None
        seen: set[tuple[str, str]] = set()
        for slot in candidates:
            key = (str(slot.get("start") or ""), str(slot.get("end") or ""))
            if key in seen or not self._slot_within_window(mandate, slot):
                continue
            seen.add(key)
            available = await self._slot_still_available(
                workspace_id, key[0], key[1])
            if not available.get("error"):
                selected = slot
                break

        if (selected and current_event_id and current_event
                and self._same_event_slot(current_event, selected)):
            # A repeated provider message is still a distinct inbound receipt,
            # but confirming the already-booked instant is not a new Calendar
            # mutation and must not generate another availability email.
            transitioned = await self._transition_goal(
                mandate, goal_status="SCHEDULED",
                goal_step="INTERVIEW_CONFIRMED",
                correlation_id=correlation_id,
                event_id=current_event_id)
            if transitioned.get("error"):
                return transitioned
            return {
                "status": "success", "duplicate": True,
                "no_external_effect": True,
                "goal_status": "SCHEDULED",
                "goal_step": "INTERVIEW_CONFIRMED",
                "event_id": current_event_id,
            }

        if (selected and intent in {
                "ACCEPT_OFFERED_SLOT", "PROPOSE_ALTERNATIVE",
                "REQUEST_RESCHEDULE"}):
            transitioned = await self._transition_goal(
                mandate, goal_status="PROCESSING_REPLY",
                goal_step=("PREPARE_INTERVIEW_UPDATE" if current_event_id
                           else "PREPARE_INTERVIEW_CREATE"),
                correlation_id=correlation_id)
            if transitioned.get("error"):
                return transitioned
            mandate = transitioned["mandate"]
            prepared = await self.prepare_interview(
                principal=principal, application_id=application_id,
                start=str(selected["start"]), end=str(selected["end"]),
                timezone_name=str(selected["timezone"]),
                client_request_id=f"reply_schedule:{correlation_id}",
                target_event_id=current_event_id)
        elif intent == "REQUEST_CANCELLATION" and current_event_id:
            transitioned = await self._transition_goal(
                mandate, goal_status="PROCESSING_REPLY",
                goal_step="PREPARE_INTERVIEW_CANCELLATION",
                correlation_id=correlation_id)
            if transitioned.get("error"):
                return transitioned
            mandate = transitioned["mandate"]
            prepared = await self.prepare_interview(
                principal=principal, application_id=application_id,
                start="", end="", timezone_name=proposed_timezone,
                client_request_id=f"reply_cancel:{correlation_id}",
                target_event_id=current_event_id, cancel=True)
        else:
            if refreshed is None:
                refreshed = await self._available_slots(
                    workspace_id, duration_minutes=duration)
            if refreshed.get("error"):
                prepared = refreshed
            else:
                verified_slots = list(refreshed.get("slots") or [])
                drafted = await draft_scheduling_reply(
                    applicant_reply=excerpt,
                    candidate_first_name=str(
                        mandate.get("candidate_first_name") or "there"),
                    role_title=str(role.get("role_title") or "your application"),
                    outcome=("OFFER_ALTERNATIVES" if candidates
                             else "ASK_CLARIFICATION"),
                    verified_slots=verified_slots,
                    duration_minutes=duration,
                    provider_thread_id=str(
                        mandate.get("provider_thread_id") or ""),
                )
                transitioned = await self._transition_goal(
                    mandate, goal_status="PROCESSING_REPLY",
                    goal_step="DRAFT_BOUNDED_CONTINUATION",
                    correlation_id=correlation_id)
                if transitioned.get("error"):
                    return transitioned
                mandate = transitioned["mandate"]
                prepared = await self.prepare_contact(
                    principal=principal, application_id=application_id,
                    client_request_id=f"reply_continue:{correlation_id}",
                    reply=True, subject=str(drafted["subject"]),
                    body=str(drafted["body"]),
                    availability_hash=canonical_hash({
                        "duration_minutes": duration,
                        "slots": verified_slots,
                    }))
        if prepared.get("error"):
            latest = await self.store.get(
                "hiring_coordination_mandates", str(mandate["mandate_id"]))
            if latest:
                await self._transition_goal(
                    latest, goal_status="BLOCKED",
                    goal_step=str(prepared.get("error_code") or
                                  "REVIEW_REQUIRED"),
                    correlation_id=correlation_id)
            return prepared
        return await self.execute(
            principal=principal, application_id=application_id,
            coordination_id=str(prepared["coordination_id"]), approval_id="")

    async def _transition_goal(
            self, mandate: dict[str, Any], *, goal_status: str,
            goal_step: str, correlation_id: str = "",
            event_id: str | None = None) -> dict[str, Any]:
        """CAS one content-free transition on the durable scheduling goal."""
        updates: dict[str, Any] = {
            "goal_kind": "SCHEDULE_INTERVIEW",
            "goal_status": goal_status,
            "goal_step": goal_step,
            "scheduling_skill_version": SCHEDULING_SKILL_VERSION,
            "last_transition_at": utc_now(),
            "updated_at": utc_now(),
        }
        if correlation_id:
            updates["last_reply_correlation_id"] = correlation_id
        if event_id is not None:
            updates["current_event_id"] = event_id
        committed = await self.store.compare_and_set(
            "hiring_coordination_mandates", str(mandate["mandate_id"]),
            int(mandate["version"]), updates)
        if not committed:
            return _error(
                "concurrency_conflict", "Scheduling goal changed concurrently.")
        current = await self.store.get(
            "hiring_coordination_mandates", str(mandate["mandate_id"]))
        return {"status": "success", "mandate": current}

    async def _prepare_item(self, *, principal: ActorPrincipal,
                            context: dict[str, Any], client_request_id: str,
                            item_kind: str, action_kind: str,
                            recipients: list[str], payload: dict[str, Any],
                            slot_options: list[dict[str, str]],
                            mandate: dict[str, Any] | None = None,
                            candidate_first_name: str = "",
                            copy_founder: bool = False,
                            duration_minutes: int | None = None) -> dict[str, Any]:
        application = context["application"]
        role = context["role"]
        connector_id = "alex_mail" if action_kind == "HIRING_SEND_EMAIL" else "calendar"
        checked = self._validate_payload(action_kind, recipients, payload)
        if checked.get("error"):
            return checked
        preflight = await self.adapter.preflight(
            workspace_id=principal.workspace_id, connector_id=connector_id)
        if preflight.get("status") != "success":
            return preflight
        coordination_id = stable_id(
            "hcoord", principal.workspace_id, application["candidate_application_id"],
            client_request_id)
        exact = {
            "schema_version": 1, "workspace_id": principal.workspace_id,
            "role_id": application["role_id"],
            "candidate_application_id": application["candidate_application_id"],
            "candidate_run_id": application["run_id"],
            "decision_id": application["current_decision_id"],
            "policy_version_id": role["current_policy_version_id"],
            "action_kind": action_kind, "connector_id": connector_id,
            "recipients": recipients, "payload": payload,
        }
        mandate_id = (str(mandate["mandate_id"]) if mandate else stable_id(
            "hiringmandate", principal.workspace_id,
            application["candidate_application_id"],
            application["current_decision_id"],
            role["current_policy_version_id"]))
        mandate_exact = None
        if mandate:
            approval_id = str(mandate["approval_id"])
            approval_status = "MANDATE_ACTIVE"
            item_status = "AUTHORIZED"
        else:
            prepared_at = datetime.now(timezone.utc)
            founder_timezone = os.environ.get(
                "FOUNDER_TIMEZONE", "Africa/Lagos")
            mandate_exact = {
                "schema_version": 1,
                "mandate_id": mandate_id,
                "workspace_id": principal.workspace_id,
                "role_id": application["role_id"],
                "candidate_application_id": application["candidate_application_id"],
                "candidate_run_id": application["run_id"],
                "decision_id": application["current_decision_id"],
                "policy_version_id": role["current_policy_version_id"],
                "candidate_email": str(payload["candidate_recipient"]),
                "candidate_first_name": candidate_first_name,
                "confirmed_slots": slot_options,
                "duration_minutes": (
                    duration_minutes or _DEFAULT_INTERVIEW_MINUTES),
                "goal_kind": "SCHEDULE_INTERVIEW",
                "scheduling_window_start": prepared_at.isoformat(),
                "scheduling_window_end": (
                    prepared_at + timedelta(days=_MANDATE_DAYS)).isoformat(),
                "founder_timezone": founder_timezone,
                "initial_message_hash": canonical_hash({
                    "recipients": recipients, "payload": payload}),
                "copy_founder": copy_founder,
                "founder_copy_email": (_founder_copy_address()
                                       if copy_founder else ""),
                "allowed_action_kinds": sorted(LIVE_ACTIONS),
                "valid_days": _MANDATE_DAYS,
            }
            approval = await request_approval(
                principal=principal, run_id=str(application["run_id"]),
                role_id=str(application["role_id"]),
                policy_version_id=str(role["current_policy_version_id"]),
                action_kind=MANDATE_ACTION, exact_action=mandate_exact,
                client_request_id=f"{client_request_id}:mandate", store=self.store,
                ttl_minutes=1440)
            if approval.get("error"):
                return approval
            approval_id = str(approval["approval_id"])
            approval_status = str(approval["approval_status"])
            item_status = "AWAITING_APPROVAL"
        masked = [self._mask_email(value) for value in recipients]
        row = {
            "schema_version": 1, "coordination_id": coordination_id,
            "workspace_id": principal.workspace_id,
            "role_id": application["role_id"],
            "candidate_application_id": application["candidate_application_id"],
            "candidate_run_id": application["run_id"],
            "decision_id": application["current_decision_id"],
            "item_kind": item_kind, "action_kind": action_kind,
            "status": item_status, "approval_id": approval_id,
            "mandate_id": mandate_id, "mandate_exact": mandate_exact,
            "exact_action": exact, "subject": payload.get("subject"),
            "body": payload.get("body"), "recipients_masked": masked,
            "slot_options": slot_options,
            "duration_minutes": duration_minutes,
            "start": payload.get("start"), "end": payload.get("end"),
            "timezone": payload.get("timezone"),
            "target_event_id": payload.get("target_event_id"),
            "action_id": None, "error_code": None,
            "created_by_actor_id": principal.actor_id,
            "created_at": utc_now(), "updated_at": utc_now(), "version": 1,
        }
        created = await self.store.create(
            "hiring_coordination_items", coordination_id, row)
        if not created:
            existing = await self.store.get("hiring_coordination_items", coordination_id)
            if not existing or canonical_hash(existing.get("exact_action") or {}) != canonical_hash(exact):
                return _error("idempotency_conflict", "Request id names different coordination.")
        return {"status": "success", "duplicate": not created,
                "coordination_id": coordination_id,
                "approval_id": approval_id,
                "approval_status": approval_status,
                "mandate_id": mandate_id,
                "mandate_active": bool(mandate),
                "item": {key: row.get(key) for key in (
                    "item_kind", "action_kind", "subject", "body", "recipients_masked",
                    "slot_options", "duration_minutes", "start", "end", "timezone",
                    "target_event_id")}}

    async def _finish(self, *, item_id: str, action: dict[str, Any],
                      result: dict[str, Any]) -> dict[str, Any]:
        provider_status = str(result.get("status") or "uncertain")
        status = {"success": "SUCCEEDED", "failed": "FAILED"}.get(
            provider_status, "UNCERTAIN")
        action_now = await self.store.get("external_actions", str(action["action_id"]))
        item = await self.store.get("hiring_coordination_items", item_id)
        if not action_now or not item:
            return _error("action_receipt_missing", "Action receipt is unavailable.", 503)
        safe_result = self._safe_ref(result.get("result_ref"))
        mandate = await self.store.get(
            "hiring_coordination_mandates",
            str(action_now.get("mandate_id") or ""))
        provider_thread_id = ""
        if status == "SUCCEEDED" and action_now.get(
                "action_kind") == "HIRING_SEND_EMAIL":
            provider_thread_id = str(safe_result.get("provider_thread_id") or "")
            existing_thread_id = str(
                (mandate or {}).get("provider_thread_id") or "")
            if (not mandate or not provider_thread_id
                    or (existing_thread_id
                        and existing_thread_id != provider_thread_id)):
                status = "UNCERTAIN"
                result = {
                    "error_code": "provider_thread_unconfirmed",
                    "uncertainty_reason": "provider_thread_unconfirmed",
                }
                safe_result = {}
        mutations = [
            AtomicMutation("external_actions", action_now["action_id"],
                           int(action_now["version"]), updates={
                               "status": status,
                               "provider_effect_id": result.get("provider_effect_id"),
                               "result_ref": safe_result,
                               "error_code": result.get("error_code"),
                               "uncertainty_reason": result.get("uncertainty_reason"),
                               "completed_at": utc_now(), "updated_at": utc_now(),
                           }),
            AtomicMutation("hiring_coordination_items", item_id,
                           int(item["version"]), updates={
                               "status": status, "action_id": action_now["action_id"],
                               "error_code": result.get("error_code"),
                               "updated_at": utc_now(),
                           }),
        ]
        if mandate is not None:
            action_kind = str(action_now.get("action_kind") or "")
            goal_updates: dict[str, Any] = {
                "goal_kind": "SCHEDULE_INTERVIEW",
                "last_transition_at": utc_now(),
                "updated_at": utc_now(),
            }
            if status == "SUCCEEDED" and action_kind == "HIRING_SEND_EMAIL":
                goal_updates.update(
                    goal_status="WAITING_FOR_REPLY",
                    goal_step="WAIT_FOR_APPLICANT_REPLY")
                if not mandate.get("provider_thread_id"):
                    goal_updates["provider_thread_id"] = provider_thread_id
            elif status == "SUCCEEDED" and action_kind in {
                    "HIRING_CREATE_INTERVIEW", "HIRING_UPDATE_INTERVIEW"}:
                goal_updates.update(
                    goal_status="SCHEDULED",
                    goal_step="INTERVIEW_CONFIRMED",
                    current_event_id=str(
                        safe_result.get("event_id") or
                        result.get("provider_effect_id") or ""))
            elif status == "SUCCEEDED" and action_kind == "HIRING_CANCEL_INTERVIEW":
                goal_updates.update(
                    goal_status="CANCELLED",
                    goal_step="INTERVIEW_CANCELLED",
                    current_event_id="")
            elif status == "UNCERTAIN":
                goal_updates.update(
                    goal_status="BLOCKED",
                    goal_step="RECONCILIATION_REQUIRED")
            elif status == "FAILED":
                goal_updates.update(
                    goal_status="BLOCKED",
                    goal_step=str(result.get("error_code") or
                                  "PROVIDER_REJECTED"))
            mutations.append(AtomicMutation(
                "hiring_coordination_mandates", str(mandate["mandate_id"]),
                int(mandate["version"]), updates=goal_updates))
        committed = await self.store.atomic_compare_and_set(tuple(mutations))
        if not committed:
            return _error("concurrency_conflict", "Action receipt changed concurrently.")
        finished = committed[("external_actions", action_now["action_id"])]
        if status == "SUCCEEDED":
            event_kind = {
                "HIRING_SEND_EMAIL": "CANDIDATE_EMAIL_SENT",
                "HIRING_CREATE_INTERVIEW": "INTERVIEW_BOOKED",
                "HIRING_UPDATE_INTERVIEW": "INTERVIEW_UPDATED",
                "HIRING_CANCEL_INTERVIEW": "INTERVIEW_CANCELLED",
            }[str(finished["action_kind"])]
            await self.runtime.append_event(
                str(finished["run_id"]), event_kind=event_kind,
                idempotency_key=f"hiring-action:{finished['action_id']}",
                safe_payload={"action_id": finished["action_id"],
                              "action_kind": finished["action_kind"]},
                actor_id=str(finished.get("actor_id") or ""))
            return {"status": "success", "duplicate": False,
                    "action_id": finished["action_id"],
                    "receipt_status": status,
                    "provider_effect_id": finished.get("provider_effect_id"),
                    "result_ref": finished.get("result_ref") or {}}
        if status == "UNCERTAIN":
            return _error("reconciliation_required",
                          "Provider outcome is uncertain; nothing was retried.", 503)
        return _error(str(finished.get("error_code") or "provider_rejected"),
                      "The provider rejected the exact approved action.")

    async def _context(self, principal: ActorPrincipal, application_id: str, *,
                       require_advanced: bool, require_fresh: bool = False) -> dict[str, Any]:
        gate = authorize(principal, "read_candidate", require_fresh=require_fresh)
        if gate.get("error"):
            return gate
        application = await self.store.get("candidate_applications", application_id)
        if (not application or application.get("workspace_id") != principal.workspace_id
                or application.get("synthetic") is not False
                or application.get("source_kind") != "PUBLIC_FORM"):
            return _error("application_not_found", "Application does not exist.", 404)
        if require_advanced:
            decision = await self.store.get(
                "hiring_decisions", str(application.get("current_decision_id") or ""))
            if (application.get("candidate_state") != "ADVANCED" or not decision
                    or decision.get("decision") != "ADVANCE"
                    or decision.get("commit_status") != "COMMITTED"):
                return _error("founder_advance_required",
                              "The Founder must advance this candidate after evidence review.", 409)
        role = await self.store.get("hiring_roles", str(application.get("role_id") or ""))
        if not role or role.get("workspace_id") != principal.workspace_id:
            return _error("role_not_found", "Role does not exist.", 404)
        result: dict[str, Any] = {"status": "success", "application": application,
                                  "role": role}
        if require_fresh:
            revealed = await self.intake.reveal_restricted_identity(
                application=application, principal=principal)
            if revealed.get("error"):
                return revealed
            result["identity"] = revealed["identity"]
        return result

    async def _available_slots(
            self, workspace_id: str, *,
            duration_minutes: int = _DEFAULT_INTERVIEW_MINUTES) -> dict[str, Any]:
        if duration_minutes not in _INTERVIEW_DURATION_OPTIONS:
            return _error(
                "invalid_contract",
                "Interview duration must be 30, 45, 60, or 90 minutes.", 400)
        preflight = await self.adapter.preflight(
            workspace_id=workspace_id, connector_id="calendar")
        if preflight.get("status") != "success":
            return preflight
        available = await calendar_adapter.check_availability(
            days_ahead=14, workspace_id=workspace_id)
        if available.get("status") != "success":
            return _error("calendar_unavailable",
                          "Founder availability could not be read.", 503)
        timezone_name = os.environ.get("FOUNDER_TIMEZONE", "Africa/Lagos")
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            return _error("timezone_invalid", "Founder time zone is not configured.", 503)
        busy: list[tuple[datetime, datetime]] = []
        for block in available.get("busy", []):
            try:
                busy.append((datetime.fromisoformat(str(block["start"]).replace("Z", "+00:00")),
                             datetime.fromisoformat(str(block["end"]).replace("Z", "+00:00"))))
            except (KeyError, ValueError):
                continue
        now = datetime.now(timezone.utc)
        slots: list[dict[str, str]] = []
        for offset in range(1, 15):
            day = (now.astimezone(zone) + timedelta(days=offset)).date()
            if day.weekday() >= 5:
                continue
            for hour in (10, 14, 16):
                start = datetime.combine(day, time(hour=hour), zone)
                end = start + timedelta(minutes=duration_minutes)
                start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
                if all(end_utc <= blocked_start or start_utc >= blocked_end
                       for blocked_start, blocked_end in busy):
                    slots.append({"start": start.isoformat(), "end": end.isoformat(),
                                  "timezone": timezone_name,
                                  "display": (
                                      f"{start.strftime('%a %d %b, %H:%M')}–"
                                      f"{end.strftime('%H:%M %Z')}")})
                if len(slots) == 3:
                    return {"status": "success", "slots": slots,
                            "timezone": timezone_name}
        return _error("availability_exhausted",
                      "No bounded Founder availability was found in the next 14 days.")

    async def _slot_still_available(
            self, workspace_id: str, start: str, end: str) -> dict[str, Any]:
        available = await calendar_adapter.check_availability(
            days_ahead=14, workspace_id=workspace_id)
        if available.get("status") != "success":
            return _error("calendar_unavailable",
                          "Founder availability could not be confirmed.", 503)
        start_dt, end_dt = datetime.fromisoformat(start), datetime.fromisoformat(end)
        for block in available.get("busy", []):
            try:
                busy_start = datetime.fromisoformat(
                    str(block["start"]).replace("Z", "+00:00"))
                busy_end = datetime.fromisoformat(
                    str(block["end"]).replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if not (end_dt <= busy_start or start_dt >= busy_end):
                return _error(
                    "founder_slot_no_longer_available",
                    "The Founder calendar changed; new options require consent.", 409)
        return {"status": "success"}

    async def _active_mandate(
            self, workspace_id: str, application_id: str, *,
            context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = await self._workspace_rows(
            "hiring_coordination_mandates", workspace_id, descending=True)
        mandate = next((row for row in rows
                        if row.get("candidate_application_id") == application_id
                        and row.get("status") == "ACTIVE"), None)
        if not mandate or str(mandate.get("expires_at") or "") <= utc_now():
            return None
        if context and (
                mandate.get("decision_id") != context["application"].get(
                    "current_decision_id")
                or mandate.get("policy_version_id") != context["role"].get(
                    "current_policy_version_id")):
            return None
        return mandate

    @staticmethod
    def _mandate_allows_action(
            mandate: dict[str, Any], action_kind: str,
            exact: dict[str, Any]) -> dict[str, Any]:
        if (mandate.get("status") != "ACTIVE"
                or str(mandate.get("expires_at") or "") <= utc_now()
                or action_kind not in set(mandate.get("allowed_action_kinds") or [])
                or mandate.get("candidate_application_id") != exact.get(
                    "candidate_application_id")
                or mandate.get("decision_id") != exact.get("decision_id")
                or mandate.get("policy_version_id") != exact.get(
                    "policy_version_id")):
            return _error(
                "coordination_mandate_invalid",
                "The interview coordination mandate is no longer valid.", 409)
        payload = dict(exact.get("payload") or {})
        recipients = list(exact.get("recipients") or [])
        candidate = str(mandate.get("candidate_email") or "")
        if action_kind == "HIRING_SEND_EMAIL":
            allowed = [candidate]
            if mandate.get("copy_founder"):
                allowed.append(str(mandate.get("founder_copy_email") or ""))
            if recipients != allowed or payload.get("candidate_recipient") != candidate:
                return _error("coordination_mandate_invalid",
                              "Email recipients are outside the mandate.", 409)
            expected_thread_id = str(mandate.get("provider_thread_id") or "")
            actual_thread_id = str(payload.get("provider_thread_id") or "")
            if (int(mandate.get("email_count") or 0) == 0
                    and actual_thread_id):
                return _error(
                    "coordination_mandate_invalid",
                    "The first message cannot join an unrelated thread.", 409)
            if (int(mandate.get("email_count") or 0) == 0
                    and mandate.get("initial_message_hash") != canonical_hash({
                        "recipients": recipients, "payload": payload})):
                return _error(
                    "coordination_mandate_invalid",
                    "The initial invitation changed after Founder approval.", 409)
            if (int(mandate.get("email_count") or 0) > 0
                    and (not expected_thread_id
                         or actual_thread_id != expected_thread_id)):
                return _error(
                    "coordination_mandate_invalid",
                    "Email continuation must use the approved applicant thread.", 409)
        else:
            if action_kind == "HIRING_CANCEL_INTERVIEW":
                if recipients:
                    return _error(
                        "coordination_mandate_invalid",
                        "Interview cancellation cannot add recipients.", 409)
            else:
                if recipients != [candidate, _ALEX_ADDRESS]:
                    return _error(
                        "coordination_mandate_invalid",
                        "Calendar attendees are outside the mandate.", 409)
                slot = {"start": payload.get("start"), "end": payload.get("end"),
                        "timezone": payload.get("timezone")}
                allowed_slots = [{key: row.get(key) for key in (
                    "start", "end", "timezone")}
                    for row in mandate.get("confirmed_slots") or []]
                if (slot not in allowed_slots
                        and not HiringCoordinationService._slot_within_window(
                            mandate, slot)):
                    return _error(
                        "slot_outside_mandate",
                        "The interview time is outside the approved scheduling window.",
                        409)
        return {"status": "success"}

    @staticmethod
    def _slot_within_window(
            mandate: dict[str, Any], slot: dict[str, Any]) -> bool:
        """Admit an alternative only inside the consented window and duration."""
        # Mandates activated before the explicit long-running-goal projection
        # already bound the same interval as activated_at..expires_at. Keep
        # those live consents usable without widening either endpoint.
        window_start = str(
            mandate.get("scheduling_window_start")
            or mandate.get("activated_at")
            or mandate.get("created_at")
            or "")
        window_end = str(
            mandate.get("scheduling_window_end")
            or mandate.get("expires_at")
            or "")
        if not window_start or not window_end:
            return False
        try:
            ZoneInfo(str(slot.get("timezone") or ""))
            start = datetime.fromisoformat(str(slot.get("start") or ""))
            end = datetime.fromisoformat(str(slot.get("end") or ""))
            lower = datetime.fromisoformat(window_start)
            upper = datetime.fromisoformat(window_end)
        except (ValueError, ZoneInfoNotFoundError):
            return False
        if any(value.tzinfo is None for value in (start, end, lower, upper)):
            return False
        expected = int(
            mandate.get("duration_minutes") or _DEFAULT_INTERVIEW_MINUTES)
        actual = int((end - start).total_seconds() / 60)
        try:
            founder_zone = ZoneInfo(str(
                mandate.get("founder_timezone") or "Africa/Lagos"))
        except ZoneInfoNotFoundError:
            return False
        local_start = start.astimezone(founder_zone)
        local_end = end.astimezone(founder_zone)
        return (actual == expected
                and start >= max(lower, datetime.now(timezone.utc))
                and end <= upper
                and local_start.weekday() < 5
                and time(9, 0) <= local_start.timetz().replace(tzinfo=None)
                and local_end.timetz().replace(tzinfo=None) <= time(18, 0))

    async def _owned_event(self, workspace_id: str, application_id: str,
                           event_id: str) -> dict[str, Any] | None:
        if not event_id:
            return None
        rows = await self._workspace_rows(
            "external_actions", workspace_id, descending=True)
        return next((row for row in rows
                     if row.get("application_id") == application_id
                     and row.get("action_kind") in {
                         "HIRING_CREATE_INTERVIEW", "HIRING_UPDATE_INTERVIEW"}
                     and row.get("status") == "SUCCEEDED"
                     and str((row.get("result_ref") or {}).get("event_id") or
                             row.get("provider_effect_id") or "") == event_id), None)

    async def _current_owned_event(
            self, workspace_id: str,
            application_id: str) -> dict[str, str] | None:
        """Resolve the latest still-active Hiring-owned Calendar event."""
        rows = [row for row in await self._workspace_rows(
            "external_actions", workspace_id, descending=True)
            if row.get("application_id") == application_id
            and row.get("status") == "SUCCEEDED"
            and row.get("action_kind") in {
                "HIRING_CREATE_INTERVIEW", "HIRING_UPDATE_INTERVIEW",
                "HIRING_CANCEL_INTERVIEW"}]
        for row in rows:
            event_id = str((row.get("result_ref") or {}).get("event_id") or
                           row.get("provider_effect_id") or
                           ((row.get("exact_action") or {}).get("payload") or {}).get(
                               "target_event_id") or "")
            if not event_id:
                continue
            if row.get("action_kind") == "HIRING_CANCEL_INTERVIEW":
                return None
            payload = dict((row.get("exact_action") or {}).get("payload") or {})
            return {"event_id": event_id,
                    "action_id": str(row.get("action_id") or ""),
                    "start": str(payload.get("start") or ""),
                    "end": str(payload.get("end") or ""),
                    "timezone": str(payload.get("timezone") or "")}
        return None

    @staticmethod
    def _same_event_slot(current: dict[str, str],
                         selected: dict[str, str]) -> bool:
        """Compare exact instants so offset-only formatting never causes an update."""
        try:
            return (
                datetime.fromisoformat(str(current.get("start") or ""))
                == datetime.fromisoformat(str(selected.get("start") or ""))
                and datetime.fromisoformat(str(current.get("end") or ""))
                == datetime.fromisoformat(str(selected.get("end") or ""))
            )
        except ValueError:
            return False

    async def _workspace_rows(self, collection: str, workspace_id: str, *,
                              descending: bool = False) -> list[dict[str, Any]]:
        """Use the ubiquitous workspace index; avoid rollout-only composites."""
        rows = await self.store.list(
            collection, filters={"workspace_id": workspace_id}, limit=2000)
        return sorted(
            rows, key=lambda row: str(row.get("created_at") or ""),
            reverse=descending)

    @staticmethod
    def _validate_payload(action_kind: str, recipients: list[str],
                          payload: dict[str, Any]) -> dict[str, Any]:
        if len(recipients) > 3 or len(set(recipients)) != len(recipients):
            return _error("invalid_contract", "Recipient set is invalid.", 400)
        if any(not _EMAIL.fullmatch(value) or "\r" in value or "\n" in value
               for value in recipients):
            return _error("invalid_contract", "Recipient address is invalid.", 400)
        if action_kind == "HIRING_SEND_EMAIL":
            subject, body = str(payload.get("subject") or ""), str(payload.get("body") or "")
            candidate = str(payload.get("candidate_recipient") or "")
            if (len(recipients) not in {1, 2} or recipients[0] != candidate
                    or (len(recipients) == 2
                        and recipients[1] != _founder_copy_address())
                    or not subject or len(subject) > _MAX_SUBJECT
                    or not body or len(body) > _MAX_BODY
                    or any(token in subject for token in ("\r", "\n"))):
                return _error("invalid_contract", "Email draft is invalid.", 400)
        elif action_kind == "HIRING_CANCEL_INTERVIEW":
            if recipients or not payload.get("target_event_id"):
                return _error("invalid_contract", "Cancellation contract is invalid.", 400)
        else:
            required = {"summary", "description", "start", "end", "timezone"}
            if (not required.issubset(payload) or len(recipients) != 2
                    or recipients[1] != _ALEX_ADDRESS
                    or recipients[0] == _ALEX_ADDRESS):
                return _error("invalid_contract", "Interview contract is invalid.", 400)
        return {"status": "success"}

    @staticmethod
    def _validate_interval(start: str, end: str,
                           timezone_name: str) -> dict[str, Any]:
        try:
            ZoneInfo(timezone_name)
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
        except (ValueError, ZoneInfoNotFoundError):
            return _error("invalid_contract", "Interview time is invalid.", 400)
        if start_dt.tzinfo is None or end_dt.tzinfo is None:
            return _error("invalid_contract", "Interview time needs an explicit offset.", 400)
        duration = int((end_dt - start_dt).total_seconds())
        now = datetime.now(timezone.utc)
        if (duration < 900 or duration > 7200
                or start_dt.astimezone(timezone.utc) <= now
                or start_dt.astimezone(timezone.utc) > now + timedelta(days=60)):
            return _error("invalid_contract", "Interview must be 15–120 minutes within 60 days.", 400)
        return {"status": "success", "start": start_dt.isoformat(),
                "end": end_dt.isoformat()}

    @staticmethod
    async def _existing_action(row: dict[str, Any], workspace_id: str,
                               exact: dict[str, Any]) -> dict[str, Any]:
        if row.get("workspace_id") != workspace_id:
            return _error("action_not_found", "Action does not exist.", 404)
        if row.get("request_hash") != canonical_hash(exact):
            return _error("idempotency_conflict", "Action payload changed.")
        if row.get("status") == "SUCCEEDED":
            return {"status": "success", "duplicate": True,
                    "action_id": row["action_id"], "receipt_status": "SUCCEEDED",
                    "provider_effect_id": row.get("provider_effect_id"),
                    "result_ref": row.get("result_ref") or {}}
        if row.get("status") == "FAILED":
            return _error(str(row.get("error_code") or "provider_rejected"),
                          "The provider rejected the action.")
        return _error("reconciliation_required",
                      "The action is in progress or its provider outcome is uncertain.", 503)

    @staticmethod
    def _safe_ref(value: Any) -> dict[str, str]:
        return ({str(key)[:64]: str(item)[:280] for key, item in value.items()}
                if isinstance(value, dict) else {})

    @staticmethod
    def _mask_email(value: str) -> str:
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}" if local and domain else "recipient"
