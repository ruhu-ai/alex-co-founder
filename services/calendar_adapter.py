"""Calendar adapter (docs/adr/002): read-only free/busy + upcoming events.

Alex checks the founder's calendar to plan work around real availability
("you're free Thursday afternoon — I'll have the draft ready by then") and to
see upcoming meetings. Read-only by construction: the OAuth scope is
calendar.readonly. Booking (calendar.events) ships only with approval-gated
invites — see docs/adr/002 §Calendar.

The Google client is injectable so tests run without OAuth.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from services import google_oauth

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
