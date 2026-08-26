"""Durable inbound provider receipts and deterministic correlation (docs/24).

Provider text is untrusted data. This service records a deterministic receipt
before any application mutation, correlates only from persisted causal facts,
and routes ambiguity to the founder inbox without guessing an active session.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from services import data_source_contracts as dsc
from services import data_source_metrics, firestore

WakeFn = Callable[[str, str, str], Awaitable[None]]

_MAIL_KIND = {
    "confirmation": dsc.ExternalEventKind.MAIL_CONFIRMATION.value,
    "request": dsc.ExternalEventKind.MAIL_REQUEST.value,
    "result_positive": dsc.ExternalEventKind.MAIL_RESULT.value,
    "result_negative": dsc.ExternalEventKind.MAIL_RESULT.value,
    "update": dsc.ExternalEventKind.MAIL_UPDATE.value,
}


def _sender_domain(sender: str) -> str:
    match = re.search(r"@([A-Za-z0-9.-]{1,253})", str(sender or ""))
    return match.group(1).rstrip(".>").lower() if match else ""


def _host(value: str) -> str:
    value = str(value or "").lower().strip()
    value = value.split(":", 1)[0].rstrip(".")
    return value.removeprefix("www.")


def _safe_mail_metadata(event: dict[str, Any]) -> tuple[dict[str, str], str]:
    from services import browser_service

    sender = str(event.get("from") or "")[:200]
    subject = re.sub(r"\s+", " ", str(event.get("subject") or ""))[:200]
    excerpt = re.sub(r"\s+", " ", str(event.get("excerpt") or ""))[:280]
    if browser_service.scan_injection(" ".join((sender, subject, excerpt))):
        return ({"title": "Message content withheld",
                 "sender_domain": _sender_domain(sender),
                 "excerpt": "Review this message directly in the connected mailbox."},
                dsc.ContentRisk.WITHHELD.value)
    return ({"title": subject or "Mailbox update", "sender": sender,
             "sender_domain": _sender_domain(sender), "excerpt": excerpt},
            dsc.ContentRisk.CLEAR.value)


async def _connection(founder_id: str, connector_id: str) -> dict[str, Any]:
    """A successful real mailbox operation may establish the durable projection."""
    account_ref = "alex-role-mailbox" if connector_id == "alex_mail" else "default"
    existing_id = dsc.data_connection_id(founder_id, connector_id, account_ref)
    existing = await firestore.get_data_connection(founder_id, existing_id)
    if existing:
        return existing
    contract = dsc.CONNECTOR_REGISTRY[dsc.require_closed(
        connector_id, dsc.ConnectorId)]
    return await firestore.upsert_data_connection(
        founder_id, connector_id, account_ref=account_ref,
        roles=[role.value for role in contract.roles],
        auth_kind=contract.auth_kind.value, status="CONNECTED")


async def _causal_candidates(founder_id: str, event: dict[str, Any],
                             provider_event: dict[str, Any]) -> list[dict[str, str]]:
    """Return exact persisted causal mappings, never subject/name inference."""
    candidates: dict[tuple[str, str, str], dict[str, str]] = {}
    thread_id = str(event.get("provider_thread_id") or "")
    if thread_id:
        for action in await firestore.list_external_actions(founder_id):
            result_ref = action.get("result_ref") or {}
            if (action.get("status") == "SUCCEEDED"
                    and str(result_ref.get("provider_thread_id") or "") == thread_id
                    and action.get("session_id")):
                item = {
                    "application_id": str(action.get("application_id") or ""),
                    "session_id": str(action.get("session_id") or ""),
                    "resource_id": str(action.get("resource_id") or
                                       action.get("application_id") or ""),
                    "basis": dsc.CorrelationBasis.CAUSAL_ACTION.value,
                }
                candidates[(item["application_id"], item["session_id"],
                            item["basis"])] = item
        for prior in await firestore.list_external_events_by_thread(
                founder_id, thread_id):
            if (prior.get("correlation_status") == "EXACT"
                    and prior.get("session_id")):
                item = {
                    "application_id": str(prior.get("application_id") or ""),
                    "session_id": str(prior.get("session_id") or ""),
                    "resource_id": str(prior.get("resource_id") or ""),
                    "basis": dsc.CorrelationBasis.PROVIDER_THREAD.value,
                }
                candidates[(item["application_id"], item["session_id"],
                            item["basis"])] = item

    sender_domain = _sender_domain(str(provider_event.get("from") or ""))
    if sender_domain:
        for registration in await firestore.list_pending_portal_registrations(
                founder_id):
            # Exact normalized domain equality is causal because the row was
            # created by our own account-registration action. Substring/name
            # similarity is deliberately excluded.
            if (_host(registration.get("host", "")) == _host(sender_domain)
                    and registration.get("session_id")):
                item = {
                    "application_id": str(registration.get("application_id") or ""),
                    "session_id": str(registration["session_id"]),
                    "resource_id": str(registration.get("application_id") or ""),
                    "basis": dsc.CorrelationBasis.PORTAL_REGISTRATION.value,
                }
                candidates[(item["application_id"], item["session_id"],
                            item["basis"])] = item
    return list(candidates.values())


async def _heuristic_candidates(founder_id: str,
                                provider_event: dict[str, Any]) -> list[dict[str, str]]:
    """Non-authoritative suggestions for inbox display only."""
    text = " ".join((str(provider_event.get("subject") or ""),
                     str(provider_event.get("excerpt") or ""))).lower()
    results = []
    for application in await firestore.list_inflight_applications(founder_id):
        opportunity = await firestore.get_opportunity(
            str(application.get("opportunity_id") or ""))
        name = str((opportunity or {}).get("name") or "").strip()
        if name and name.lower() in text:
            results.append({
                "resource_id": str(application.get("id") or ""),
                "application_id": str(application.get("id") or ""),
                "reason_code": "subject_name_only",
            })
    return results[:5]


async def _deliver(founder_id: str, event_id: str, session_id: str,
                   notice: str, wake: WakeFn | None) -> bool:
    if not wake:
        return True
    claim = await firestore.claim_external_event_delivery(founder_id, event_id)
    if claim.get("duplicate"):
        return claim.get("delivery_status") in {"DELIVERED", "NOT_REQUIRED"}
    if not claim.get("claimed"):
        return False
    try:
        await wake(founder_id, session_id, notice)
    except Exception:
        await firestore.finish_external_event_delivery(
            founder_id, event_id, delivered=False)
        return False
    await firestore.finish_external_event_delivery(
        founder_id, event_id, delivered=True)
    return True


async def process_mail_event(founder_id: str, connector_id: str,
                             provider_event: dict[str, Any], *,
                             wake: WakeFn | None = None) -> dict[str, Any]:
    """Receipt → lease → exact effect or inbox → optional origin-bound wake."""
    if connector_id not in {"founder_gmail", "alex_mail"}:
        return {"status": "error", "error": True,
                "error_code": "invalid_contract", "message": "invalid mail connector"}
    provider_event_id = str(provider_event.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", provider_event_id):
        return {"status": "error", "error": True,
                "error_code": "invalid_contract", "message": "invalid provider event"}
    connection = await _connection(founder_id, connector_id)
    if connection.get("status") == "error":
        return connection
    safe_display, risk = _safe_mail_metadata(provider_event)
    kind = _MAIL_KIND.get(str(provider_event.get("kind") or ""),
                          dsc.ExternalEventKind.MAIL_UPDATE.value)
    thread_id = str(provider_event.get("thread_id") or "")[:512] or None
    payload_hash = dsc.canonical_hash({
        "provider_event_id": provider_event_id, "provider_thread_id": thread_id,
        "kind": kind, "from": str(provider_event.get("from") or "")[:200],
        "subject": str(provider_event.get("subject") or "")[:200],
        "excerpt": str(provider_event.get("excerpt") or "")[:280],
    })
    receipt = await firestore.create_external_event(
        founder_id, connection["connection_id"], connector_id,
        provider_event_id, kind, payload_hash=payload_hash,
        provider_thread_id=thread_id,
        source_ref={"message_id": provider_event_id,
                    "thread_id": thread_id or ""},
        safe_display=safe_display, content_risk=risk)
    data_source_metrics.record(
        "external_event_received", connector_id=connector_id,
        event_kind=kind, status=receipt.get("status"),
        error_code=receipt.get("error_code"))
    if receipt.get("error"):
        return receipt
    event_id = receipt["event_id"]
    current = await firestore.get_external_event(founder_id, event_id) or receipt
    if current.get("processing_status") == "APPLIED":
        delivered = await _deliver(
            founder_id, event_id, str(current.get("session_id") or ""),
            "A provider message was applied to its causally linked work item.", wake)
        return {"status": "success", "duplicate": True,
                "settled": delivered, "event_id": event_id,
                "effect_ref": current.get("effect_ref")}
    if current.get("processing_status") == "INBOXED":
        return {"status": "success", "duplicate": True, "settled": True,
                "event_id": event_id, "effect_ref": current.get("effect_ref")}

    claim = await firestore.claim_external_event(founder_id, event_id)
    if claim.get("in_progress"):
        return {"status": "success", "in_progress": True,
                "settled": False, "event_id": event_id}
    if claim.get("duplicate"):
        return {"status": "success", "duplicate": True,
                "settled": True, "event_id": event_id}
    if not claim.get("claimed"):
        return claim

    exact = await _causal_candidates(founder_id, current, provider_event)
    if len(exact) == 1:
        match = exact[0]
        if match["application_id"]:
            applied = await firestore.apply_external_event_to_application(
                founder_id, event_id, claim["lease_owner"],
                match["application_id"], match["session_id"], match["basis"],
                {"kind": f"email_{provider_event.get('kind', 'update')}",
                 "due_at": "", "status": "PENDING",
                 "note": ("[provider message] " + safe_display.get("title", "")
                          + " — " + safe_display.get("excerpt", ""))[:500],
                 "source": connector_id})
        else:
            applied = await firestore.apply_external_event_signal(
                founder_id, event_id, claim["lease_owner"],
                session_id=match["session_id"], correlation_basis=match["basis"])
        if applied.get("error"):
            return applied
        delivered = await _deliver(
            founder_id, event_id, match["session_id"],
            "A provider message arrived for the exact work item linked to this session.",
            wake)
        data_source_metrics.record(
            "external_event_correlated", connector_id=connector_id,
            correlation_status="EXACT", status="APPLIED",
            delivery_status="DELIVERED" if delivered else "FAILED")
        return {"status": "success", "settled": delivered,
                "event_id": event_id, "effect_ref": applied.get("effect_ref")}

    suggestions = [
        {"resource_id": item.get("resource_id", ""),
         "application_id": item.get("application_id", ""),
         "reason_code": "multiple_causal_matches"}
        for item in exact
    ] or await _heuristic_candidates(founder_id, provider_event)
    item_kind = (dsc.FounderInboxKind.AMBIGUOUS_EVENT.value
                 if suggestions else dsc.FounderInboxKind.UNMATCHED_EVENT.value)
    inbox = await firestore.create_founder_inbox_item(
        founder_id, event_id, item_kind,
        title=safe_display.get("title", "Mailbox update"),
        summary=(safe_display.get("excerpt")
                 or "Choose the related work item, or dismiss this message."),
        candidate_refs=suggestions, lease_owner=claim["lease_owner"])
    data_source_metrics.record(
        "external_event_correlated", connector_id=connector_id,
        correlation_status="AMBIGUOUS" if suggestions else "UNMATCHED",
        status=inbox.get("status"))
    return {"status": inbox.get("status", "error"),
            "error": inbox.get("error", False), "settled": not inbox.get("error"),
            "event_id": event_id, "inbox_item_id": inbox.get("inbox_item_id"),
            "duplicate": inbox.get("duplicate", False)}


async def process_mail_batch(founder_id: str, connector_id: str,
                             events: list[dict[str, Any]], *,
                             wake: WakeFn | None = None) -> dict[str, Any]:
    """Process bounded provider events independently; errors stay as data."""
    results = []
    settled_provider_ids = []
    for event in events[:50]:
        result = await process_mail_event(
            founder_id, connector_id, event, wake=wake)
        results.append(result)
        if result.get("settled"):
            settled_provider_ids.append(str(event.get("id") or ""))
    return {"status": "success", "results": results,
            "settled_provider_ids": settled_provider_ids,
            "processed": len(results)}
