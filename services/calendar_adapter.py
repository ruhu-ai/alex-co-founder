"""Calendar adapter (docs/adr/002): free/busy, upcoming events, and
approval-gated booking.

Alex checks the founder's calendar to plan work around real availability and
books meetings with external contacts. Reads are ungated (calendar.readonly).
Booking creates a real event and emails invites to attendees — an external,
reputation-spending action — so it is approval-gated exactly like submit_form
and send_email: a GRANTED, unexpired, unconsumed approval resolved
server-side; consuming it is the idempotency key (principles 4, 5, 7).

The Google client is injectable so tests run without OAuth.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Callable

from services import firestore, google_oauth

_service_factory: Callable[[], Any] | None = None


def set_service_factory(fn: Callable[[], Any] | None) -> None:
    """Tests inject a fake Calendar service; prod builds from OAuth creds."""
    global _service_factory
    _service_factory = fn


def _service():
    if _service_factory is not None:
        return _service_factory()
    creds = google_oauth.get_credentials()
    if creds is None:
        return None
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _window(days_ahead: int) -> tuple[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    end = now + dt.timedelta(days=max(1, min(days_ahead, 30)))
    return now.isoformat(), end.isoformat()


def _no_oauth() -> dict:
    return {"status": "error", "error": True,
            "message": "Google OAuth not configured (run scripts/oauth_setup.py)"}


async def list_upcoming(days_ahead: int = 7, max_results: int = 10) -> dict:
    """Upcoming events on the founder's primary calendar."""
    svc = _service()
    if svc is None:
        return _no_oauth()
    time_min, time_max = _window(days_ahead)
    try:
        resp = svc.events().list(
            calendarId="primary", timeMin=time_min, timeMax=time_max,
            maxResults=max_results, singleEvents=True, orderBy="startTime",
        ).execute()
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"calendar list failed: {exc}"}
    events = [{
        "summary": e.get("summary", "(no title)"),
        "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
        "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
        "attendees": len(e.get("attendees", [])),
        "meet_link": e.get("hangoutLink", ""),
    } for e in resp.get("items", [])]
    return {"status": "success", "events": events, "window_end": time_max}


async def check_availability(days_ahead: int = 7) -> dict:
    """Busy blocks on the founder's primary calendar via the free/busy API.

    Returns busy intervals only — the model reasons about the free gaps.
    Free/busy of EXTERNAL contacts is not queryable (their calendars are not
    shared); Alex proposes times from the founder's availability only.
    """
    svc = _service()
    if svc is None:
        return _no_oauth()
    time_min, time_max = _window(days_ahead)
    try:
        resp = svc.freebusy().query(body={
            "timeMin": time_min, "timeMax": time_max,
            "items": [{"id": "primary"}],
        }).execute()
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"freebusy failed: {exc}"}
    busy = resp.get("calendars", {}).get("primary", {}).get("busy", [])
    return {"status": "success", "busy": busy,
            "window": {"start": time_min, "end": time_max}}


async def create_event(summary: str, start_iso: str, end_iso: str,
                       attendees: list[str], description: str = "",
                       application_id: str = "") -> dict:
    """Create an event on the founder's primary calendar and email invites —
    approval-gated (principle 5).

    Without a valid approval: creates/finds a PENDING approval for the founder
    to grant in the UI and returns needs_approval. With one: inserts the event
    (Meet link auto-attached, invites sent), consumes the approval
    (single-use idempotency), and audits.
    """
    svc = _service()
    if svc is None:
        return _no_oauth()
    if (_service_factory is None  # prod only: pre-check the write scope
            and "https://www.googleapis.com/auth/calendar.events"
            not in google_oauth.granted_scopes()):
        return {"status": "error", "error": True,
                "message": "Calendar write not granted — reconnect Calendar in the "
                           "Connectors panel to enable booking."}
    if not summary.strip():
        return {"status": "error", "error": True, "message": "summary is required"}
    try:
        start = dt.datetime.fromisoformat(start_iso)
        end = dt.datetime.fromisoformat(end_iso)
    except ValueError:
        return {"status": "error", "error": True,
                "message": "start/end must be ISO-8601 (e.g. 2026-08-25T14:00:00+01:00)"}
    if end <= start:
        return {"status": "error", "error": True, "message": "end must be after start"}
    guests = [a.strip() for a in attendees if "@" in a]
    if not guests:
        return {"status": "error", "error": True,
                "message": "at least one attendee email is required"}
    target = f"calendar:{application_id or 'general'}"

    approval = await firestore.find_valid_approval(target)
    if not approval:
        pending = await firestore.find_pending_approval(target)
        if not pending:
            from services import approval_service

            requested = await approval_service.request_approval(
                target, gate="book_meeting",
                details={"summary": summary, "start": start_iso, "end": end_iso,
                         "attendees": guests})
            pending = {"id": requested.get("approval_id")}
        await firestore.audit("agent:orchestrator", "book_meeting", target,
                              "refused", "no GRANTED approval — requested founder approval")
        return {"status": "needs_approval", "error": True,
                "approval_id": pending.get("id"),
                "message": "Booking a meeting requires your approval — a request is waiting "
                           "in the approval banner. Once granted, ask me to book again."}

    event_body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "attendees": [{"email": a} for a in guests],
        "conferenceData": {"createRequest": {"requestId": uuid.uuid4().hex,
                                             "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
    }
    try:
        event = svc.events().insert(
            calendarId="primary", body=event_body,
            conferenceDataVersion=1, sendUpdates="all").execute()
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"event insert failed: {exc}"}
    await firestore.consume_approval(approval["id"])
    await firestore.audit("agent:orchestrator", "book_meeting", target, "success",
                          f"summary={summary[:80]} attendees={','.join(guests)} "
                          f"event_id={event.get('id')}")
    return {"status": "success", "event_id": event.get("id"),
            "meet_link": event.get("hangoutLink", ""),
            "message": f"Booked '{summary}' — invites sent to {', '.join(guests)}."}
