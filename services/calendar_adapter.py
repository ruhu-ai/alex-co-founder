"""Calendar adapter (docs/adr/002): free/busy, upcoming events, and
approval-gated booking.

Alex checks the founder's calendar to plan work around real availability and
books meetings with external contacts. Reads are ungated (calendar.readonly).
Booking creates a real event and emails invites to attendees — an external,
reputation-spending action — so it is approval-gated exactly like submit_form
and send_email: a GRANTED, unexpired, unconsumed approval resolved
server-side; consuming it is the idempotency key (principles 4, 5, 7).

Two properties the gate depends on (docs/24 §11):

* **Content binding.** The approval is bound to the *exact* meeting the founder
  saw — title, time, guests, description — through
  `approval_service.action_subject_hash`. A call whose arguments differ from the
  approved subject is REFUSED. It is never "resolved" by substituting the
  approved details over the call arguments: that silently booked meeting A when
  the caller asked for B and reported success.
* **Idempotent, reconcilable writes.** `events.insert` carries a caller-supplied
  deterministic event id derived from the same subject identity, so the provider
  itself rejects a duplicate (409) instead of double-booking, and an ambiguous
  failure can be resolved afterwards with `reconcile_event`.

The Google client is injectable so tests run without OAuth.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
from typing import Any, Callable

from services import approval_service, external_action_service, firestore, google_oauth

_CAL_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"

# Mixed into every event-id digest so a future change to the identity can never
# collide with an id minted under the old one.
_EVENT_ID_VERSION = "calendar-event-id-v1"
_RECORD = "\x1e"

# HTTP statuses that mean the request was rejected outright: the provider never
# created anything, so the outcome is known and a retry is safe to describe as a
# plain failure. Everything else (5xx, timeouts, transport errors, no status at
# all) is outcome-ambiguous — the event may exist and invites may be out.
_DEFINITIVE_FAILURES = {400, 401, 403, 404, 410, 412, 422, 429}

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
    import asyncio

    svc = await asyncio.to_thread(_service)  # cred refresh is blocking HTTP
    if svc is None:
        return _no_oauth()
    time_min, time_max = _window(days_ahead)
    try:
        resp = await asyncio.to_thread(lambda: svc.events().list(
            calendarId="primary", timeMin=time_min, timeMax=time_max,
            maxResults=max_results, singleEvents=True, orderBy="startTime",
        ).execute())
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
    import asyncio

    svc = await asyncio.to_thread(_service)
    if svc is None:
        return _no_oauth()
    time_min, time_max = _window(days_ahead)
    try:
        resp = await asyncio.to_thread(lambda: svc.freebusy().query(body={
            "timeMin": time_min, "timeMax": time_max,
            "items": [{"id": "primary"}],
        }).execute())
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"freebusy failed: {exc}"}
    busy = resp.get("calendars", {}).get("primary", {}).get("busy", [])
    return {"status": "success", "busy": busy,
            "window": {"start": time_min, "end": time_max}}


def _http_status(exc: Exception) -> int:
    """HTTP status behind a googleapiclient error, or 0 when there is none."""
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    try:
        return int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _is_duplicate(exc: Exception) -> bool:
    """Calendar's answer to "you already inserted this id": 409 duplicate.

    That is the whole point of the deterministic id — a duplicate means the
    event EXISTS, which is the outcome the caller asked for, not a failure.
    """
    return _http_status(exc) == 409 or "duplicate" in str(exc).lower()


def _is_definitive_failure(exc: Exception) -> bool:
    return _http_status(exc) in _DEFINITIVE_FAILURES


def subject_details(summary: str, start: dt.datetime, end: dt.datetime,
                    guests: list[str], description: str = "") -> dict:
    """The canonical meeting identity an approval is bound to.

    Times come from the *parsed* datetimes so two spellings of the same instant
    ("14:00+01:00" / "14:00:00+01:00") bind identically, and guests are sorted
    so ordering cannot change the subject. `description` is included because it
    is emailed verbatim inside the invite: leaving it unbound would let a later
    call keep the approved title and time while rewriting what the invitee
    reads.
    """
    return {"summary": summary.strip(), "start": start.isoformat(),
            "end": end.isoformat(), "guests": sorted(guests),
            "description": description}


def event_idempotency_id(target: str, founder_id: str, subject_hash: str) -> str:
    """The deterministic Calendar event id for one approved meeting.

    Calendar accepts a caller-supplied `id` on `events.insert` and enforces it
    as unique per calendar, which makes insert *natively* idempotent: the same
    meeting inserted twice returns 409 "duplicate" instead of creating a second
    event and emailing a second round of invites. That is the only idempotency
    key on this path that survives a process crash — the random
    `conferenceData.createRequest.requestId` it replaced was regenerated per
    call and therefore keyed nothing.

    Identity = (version, gate, target, founder, subject_hash). Session is
    deliberately excluded so a retry from a *new* session under a fresh
    approval for the same meeting still collapses onto the same event.

    Calendar restricts ids to 5–1024 characters from the base32hex alphabet
    (`a-v0-9`), so the SHA-256 identity digest is emitted with
    `base64.b32hexencode` — whose alphabet is exactly `0-9A-V` — and lowercased.
    The `cal` prefix is itself inside `a-v`. Result: 55 legal characters.
    """
    digest = hashlib.sha256(_RECORD.join([
        _EVENT_ID_VERSION, "book_meeting", target, founder_id, subject_hash,
    ]).encode()).digest()
    return "cal" + base64.b32hexencode(digest).decode().rstrip("=").lower()


async def _write_scope_missing() -> bool:
    """True only when the write scope is KNOWN to be absent.

    The pre-check exists to turn a confusing provider 403 into a "reconnect
    Calendar" instruction, so it must stay cheap and non-fatal: it reads
    google_oauth's per-token scope cache, and only when that is cold does it
    fall back to the blocking `tokeninfo` call — bounded, and treated as
    "unknown" (proceed, let Calendar answer) if it is slow or fails. It must
    never be the reason a booking cannot happen.
    """
    import asyncio

    scopes = getattr(google_oauth, "_granted", {}).get("founder")
    if scopes is None:
        try:
            scopes = await asyncio.wait_for(
                asyncio.to_thread(google_oauth.granted_scopes), timeout=2.0)
        except Exception:
            return False
    return bool(scopes) and _CAL_WRITE_SCOPE not in scopes


async def reconcile_event(event_id: str, *, founder_id: str = "",
                          action_id: str = "") -> dict:
    """Did an uncertain insert actually land? (docs/24 §11.2 step 8.)

    The deterministic id IS the reconciliation handle: fetching it answers
    "does this exact approved meeting exist on the calendar?" without guessing
    from listings. Callers use it after `provider_outcome_uncertain` before
    deciding anything else.
    """
    import asyncio

    svc = await asyncio.to_thread(_service)
    if svc is None:
        return _no_oauth()
    try:
        event = await asyncio.to_thread(lambda: svc.events().get(
            calendarId="primary", eventId=event_id).execute())
    except Exception as exc:
        if _http_status(exc) in (404, 410):
            await firestore.audit("agent:orchestrator", "book_meeting_reconcile",
                                  f"calendar_event:{event_id}", "success",
                                  "no event with this id — the booking did not land",
                                  idempotency_key=event_id)
            result = {"status": "success", "exists": False,
                      "event_id": event_id,
                      "message": "No event with this id — the booking did not land."}
            if founder_id and action_id:
                receipt = await firestore.get_external_action(founder_id, action_id)
                if not receipt or receipt.get("action_kind") != "create_calendar_event":
                    return {"status": "error", "error": True,
                            "error_code": "owner_mismatch",
                            "message": "calendar action receipt not found"}
                resolved = await external_action_service.reconcile(
                    founder_id, action_id, "FAILED",
                    action_kind="create_calendar_event",
                    idempotency_key=receipt.get("idempotency_key", ""),
                    result_ref={"event_id": event_id},
                    error_code="provider_rejected")
                if resolved.get("error"):
                    return resolved
                result["action_id"] = action_id
            return result
        await firestore.audit("agent:orchestrator", "book_meeting_reconcile",
                              f"calendar_event:{event_id}", "error",
                              f"reconcile failed: {exc}"[:200],
                              idempotency_key=event_id)
        return {"status": "error", "error": True, "uncertain": True,
                "error_code": "reconcile_failed", "event_id": event_id,
                "message": f"Could not determine whether the event exists: {exc}"}
    exists = event.get("status") != "cancelled"
    await firestore.audit("agent:orchestrator", "book_meeting_reconcile",
                          f"calendar_event:{event_id}", "success",
                          f"exists={exists} status={event.get('status', '')}",
                          idempotency_key=event_id)
    result = {"status": "success", "exists": exists,
            "event_id": event.get("id") or event_id,
            "summary": event.get("summary", ""),
            "meet_link": event.get("hangoutLink", ""),
            "event_status": event.get("status", "")}
    if founder_id and action_id:
        receipt = await firestore.get_external_action(founder_id, action_id)
        if not receipt or receipt.get("action_kind") != "create_calendar_event":
            return {"status": "error", "error": True,
                    "error_code": "owner_mismatch",
                    "message": "calendar action receipt not found"}
        resolved = await external_action_service.reconcile(
            founder_id, action_id, "SUCCEEDED" if exists else "FAILED",
            action_kind="create_calendar_event",
            idempotency_key=receipt.get("idempotency_key", ""),
            provider_effect_id=(event.get("id") or event_id) if exists else None,
            result_ref={"event_id": event.get("id") or event_id,
                        "meet_link": event.get("hangoutLink", "")},
            error_code=None if exists else "provider_rejected")
        if resolved.get("error"):
            return resolved
        result["action_id"] = action_id
    return result


async def create_event(summary: str, start_iso: str, end_iso: str,
                       attendees: list[str], description: str = "",
                       application_id: str = "", founder_id: str = "",
                       session_id: str = "") -> dict:
    """Create an event on the founder's primary calendar and email invites —
    approval-gated (principle 5).

    Without an approval bound to *this exact meeting*: creates/finds a PENDING
    approval for the founder to grant in the UI and returns needs_approval.
    With one: inserts the event under a deterministic id (Meet link
    auto-attached, invites sent), consumes the approval (single-use), audits.

    A GRANTED approval that covers a DIFFERENT meeting refuses with
    `approval_binding_mismatch`; an approval carrying no binding at all refuses
    with `approval_binding_missing`. Neither ever books anything.
    """
    import asyncio

    svc = await asyncio.to_thread(_service)
    if svc is None:
        return _no_oauth()
    if _service_factory is None and await _write_scope_missing():
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
    if not founder_id or not session_id:
        return {"status": "error", "error": True,
                "message": "Booking a meeting requires a founder-bound session."}
    summary = summary.strip()
    target = f"calendar:{application_id or 'general'}"

    # What the founder is asked to approve, and the immutable identity of it.
    # Both are derived from THIS call's arguments — the gate answers "did the
    # founder approve this meeting?", never "did the founder approve something
    # for this calendar?".
    details = {"summary": summary, "start": start.isoformat(),
               "end": end.isoformat(), "attendees": guests,
               "description": description}
    subject = subject_details(summary, start, end, guests, description)
    subject_hash = approval_service.action_subject_hash(
        "book_meeting", target, subject)
    if not subject_hash:  # unbindable subject must refuse, never pass
        return {"status": "error", "error": True,
                "error_code": "approval_binding_missing",
                "message": "Cannot bind an approval to this meeting — nothing to approve."}
    event_id = event_idempotency_id(target, founder_id, subject_hash)

    claimable = await approval_service.claim_for_action(
        target, "book_meeting", founder_id=founder_id, session_id=session_id,
        expected_subject_hash=subject_hash)
    if claimable.get("error"):
        code = claimable.get("error_code") or "approval_missing"
        # A grant exists but covers a different meeting (or carries no binding
        # at all). Refuse without auto-requesting a replacement: requesting here
        # expires every open approval for a different subject, which would let a
        # prompt-injected invocation destroy the founder's real grant.
        if code in {"approval_binding_mismatch", "approval_binding_missing"}:
            await firestore.audit(
                "agent:orchestrator", "book_meeting", target, "refused",
                f"{code}: granted approval does not cover the requested meeting",
                idempotency_key=event_id)
            message = ("Action blocked: your approval covers a different meeting, so "
                       "I did not book anything and left that approval untouched. Ask "
                       "the founder to approve this exact meeting before booking it."
                       if code == "approval_binding_mismatch" else
                       "Action blocked: the existing approval is not bound to a specific "
                       "meeting, so it cannot prove what you agreed to. Nothing was "
                       "booked and the approval was left untouched.")
            return {"status": "error", "error": True,
                    "error_code": code, "message": message}

        # No grant exists at all, so it is safe to mint/reuse a request bound to
        # this exact meeting.
        requested = await approval_service.request_approval(
            target, gate="book_meeting", details=details,
            founder_id=founder_id, session_id=session_id,
            subject_hash=subject_hash)
        if requested.get("status") != "success":
            return requested
        await firestore.audit(
            "agent:orchestrator", "book_meeting", target, "refused",
            "no GRANTED approval — requested founder approval",
            idempotency_key=event_id)
        return {"status": "needs_approval", "error": True,
                "approval_id": requested.get("approval_id", ""),
                "message": "Booking a meeting requires your approval — a request is waiting "
                           "in the approval banner. Once granted, ask me to book again."}

    event_body = {
        # Caller-supplied deterministic id: Calendar enforces uniqueness, so a
        # retry of this exact approved meeting is rejected as a duplicate rather
        # than creating a second event and a second round of invites.
        "id": event_id,
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "attendees": [{"email": a} for a in guests],
        "conferenceData": {"createRequest": {"requestId": event_id,
                                             "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
    }
    from services import profile_authority, profile_service

    authority = profile_authority.consequential_use_gate(
        await profile_service.get_profile(founder_id), details,
        exact_founder_authorization=bool(claimable.get("approval_id") and subject_hash))
    if authority.get("error"):
        return authority
    idempotency_key = event_id
    prepared = await external_action_service.prepare(
        founder_id, "calendar", "create_calendar_event", idempotency_key,
        {"event_id": event_id, "subject_hash": subject_hash},
        session_id=session_id, application_id=application_id or None,
        subject_hash=subject_hash, approval_id=claimable["approval_id"])
    if prepared.get("duplicate"):
        return external_action_service.duplicate_result(prepared)
    if prepared.get("error"):
        return prepared
    if not prepared.get("claimed"):
        return {"status": "error", "error": True,
                "error_code": "lease_conflict",
                "action_id": prepared.get("action_id"),
                "message": "This calendar action is already in progress."}
    # Reserve the single-use approval before Calendar creates the event and
    # emails invitations.  A timeout after insert is outcome-ambiguous, so a
    # retry under the same approval must never be allowed to double-book.
    if not await firestore.claim_approval(claimable["approval_id"]):
        await external_action_service.finish(
            founder_id, prepared["action_id"], prepared["lease_owner"], "FAILED",
            action_kind="create_calendar_event", idempotency_key=idempotency_key,
            error_code="approval_missing")
        await firestore.audit("agent:orchestrator", "book_meeting", target,
                              "refused", "approval already consumed",
                              idempotency_key=event_id)
        return {"status": "error", "error": True,
                "error_code": "approval_consumed",
                "message": "Action blocked: this meeting approval was already used."}
    try:
        event = await asyncio.to_thread(lambda: svc.events().insert(
            calendarId="primary", body=event_body,
            conferenceDataVersion=1, sendUpdates="all").execute())
    except Exception as exc:
        if _is_duplicate(exc):
            # The id already exists: this exact meeting is on the calendar and
            # the invites went out on the first attempt. Report the outcome the
            # caller asked for — do NOT insert again.
            existing = await reconcile_event(event_id)
            await firestore.audit(
                "agent:orchestrator", "book_meeting", target, "success",
                f"duplicate insert ignored — event_id={event_id} already exists",
                idempotency_key=event_id)
            await external_action_service.finish(
                founder_id, prepared["action_id"], prepared["lease_owner"],
                "SUCCEEDED", action_kind="create_calendar_event",
                idempotency_key=idempotency_key,
                provider_effect_id=event_id,
                result_ref={"event_id": event_id,
                            "meet_link": existing.get("meet_link", "")})
            return {"status": "success", "event_id": event_id, "duplicate": True,
                    "action_id": prepared["action_id"],
                    "meet_link": existing.get("meet_link", ""),
                    "message": f"'{summary}' was already booked — invites had already "
                               f"gone to {', '.join(guests)}; nothing was sent twice."}
        if _is_definitive_failure(exc):
            await external_action_service.finish(
                founder_id, prepared["action_id"], prepared["lease_owner"],
                "FAILED", action_kind="create_calendar_event",
                idempotency_key=idempotency_key,
                error_code="provider_rejected")
            await firestore.audit("agent:orchestrator", "book_meeting", target,
                                  "error", f"insert rejected: {exc}"[:200],
                                  idempotency_key=event_id)
            return {"status": "error", "error": True,
                    "error_code": "event_insert_failed",
                    "message": f"event insert failed: {exc}"}
        # Outcome-ambiguous: the request may have reached Calendar, the event may
        # exist, and the invites may already be emailed. The approval is spent.
        # Say so honestly and hand back the reconciliation handle (docs/24 §11.2
        # step 7) — never a blind retry.
        await firestore.audit(
            "agent:orchestrator", "book_meeting", target, "uncertain",
            f"provider outcome unknown after insert: {exc}"[:200],
            idempotency_key=event_id)
        await external_action_service.finish(
            founder_id, prepared["action_id"], prepared["lease_owner"],
            "UNCERTAIN", action_kind="create_calendar_event",
            idempotency_key=idempotency_key,
            result_ref={"event_id": event_id},
            uncertainty_reason="provider_outcome_unconfirmed",
            error_code="provider_timeout" if isinstance(exc, TimeoutError)
            else "provider_unavailable")
        return {"status": "error", "error": True, "uncertain": True,
                "error_code": "provider_outcome_uncertain", "event_id": event_id,
                "action_id": prepared["action_id"],
                "message": ("Calendar did not confirm the booking, so I cannot tell "
                            "whether the event was created and invites emailed. I have "
                            "not retried. Ask me to check that meeting before booking "
                            "again.")}
    await firestore.audit("agent:orchestrator", "book_meeting", target, "success",
                          f"summary={summary[:80]} attendees={','.join(guests)} "
                          f"event_id={event.get('id')}", idempotency_key=event_id)
    await external_action_service.finish(
        founder_id, prepared["action_id"], prepared["lease_owner"], "SUCCEEDED",
        action_kind="create_calendar_event", idempotency_key=idempotency_key,
        provider_effect_id=event.get("id") or event_id,
        result_ref={"event_id": event.get("id") or event_id,
                    "meet_link": event.get("hangoutLink", "")})
    return {"status": "success", "event_id": event.get("id") or event_id,
            "meet_link": event.get("hangoutLink", ""),
            "action_id": prepared["action_id"],
            "message": f"Booked '{summary}' — invites sent to {', '.join(guests)}."}
