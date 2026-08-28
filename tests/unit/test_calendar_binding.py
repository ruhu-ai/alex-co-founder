"""Calendar booking: approval content-binding, native idempotency, and honest
uncertainty (docs/24 §11).

Every test here fails against the pre-fix adapter, which resolved a granted
`book_meeting` approval by *target only*, substituted the approved details over
the call arguments, and treated any provider exception after `events.insert` as
a plain failure even though the event may have been created and the invites
emailed.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from services import approval_service, calendar_adapter, firestore, google_oauth

FOUNDER = "founder"
SESSION = "session-1"
TARGET = "calendar:general"

MEETING_A = {"summary": "Intro call", "start_iso": "2026-08-25T14:00:00+01:00",
             "end_iso": "2026-08-25T14:30:00+01:00",
             "attendees": ["investor@fund.com"]}
MEETING_B = {"summary": "Wire transfer walkthrough",
             "start_iso": "2026-08-26T09:00:00+01:00",
             "end_iso": "2026-08-26T10:00:00+01:00",
             "attendees": ["attacker@evil.example"]}


# --- fake Calendar service -------------------------------------------------

class _FakeHttpError(Exception):
    """Shaped like googleapiclient.errors.HttpError: carries `resp.status`."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.resp = SimpleNamespace(status=status)


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _FakeEvents:
    """Enforces Calendar's real id uniqueness rule: re-inserting an existing
    id is a 409 duplicate, not a second event."""

    def __init__(self, raise_on_insert=None):
        self.events: dict[str, dict] = {}
        self.inserted: list[dict] = []
        self.raise_on_insert = raise_on_insert

    def insert(self, calendarId, body, conferenceDataVersion, sendUpdates):
        def _do():
            if self.raise_on_insert is not None:
                raise self.raise_on_insert
            event_id = body.get("id") or "server-assigned"
            if event_id in self.events:
                raise _FakeHttpError(
                    409, "The requested identifier already exists. (duplicate)")
            self.inserted.append(body)
            event = {**body, "id": event_id,
                     "hangoutLink": f"https://meet.google.com/{event_id[:11]}"}
            self.events[event_id] = event
            return event
        return _Req(_do)

    def get(self, calendarId, eventId):
        def _do():
            if eventId not in self.events:
                raise _FakeHttpError(404, "Not Found")
            return self.events[eventId]
        return _Req(_do)

    def list(self, **_kw):
        return _Req(lambda: {"items": list(self.events.values())})


class _FakeCalendar:
    def __init__(self, raise_on_insert=None):
        self._events = _FakeEvents(raise_on_insert)

    def events(self):
        return self._events


@pytest.fixture
def calendar():
    svc = _FakeCalendar()
    calendar_adapter.set_service_factory(lambda: svc)
    yield svc
    calendar_adapter.set_service_factory(None)


# --- helpers ---------------------------------------------------------------

def subject_hash_for(meeting: dict, description: str = "",
                     target: str = TARGET) -> str:
    start = dt.datetime.fromisoformat(meeting["start_iso"])
    end = dt.datetime.fromisoformat(meeting["end_iso"])
    return approval_service.action_subject_hash(
        "book_meeting", target,
        calendar_adapter.subject_details(meeting["summary"], start, end,
                                         meeting["attendees"], description))


async def grant_for(meeting: dict, subject_hash: str | None = None,
                    target: str = TARGET) -> str:
    """A GRANTED book_meeting approval, optionally bound to a different (or
    no) subject — that is what a stale or legacy grant looks like."""
    if subject_hash is None:
        subject_hash = subject_hash_for(meeting, target=target)
    approval_id = await firestore.create_approval(
        target, "book_meeting", 30,
        details={"summary": meeting["summary"], "start": meeting["start_iso"],
                 "end": meeting["end_iso"], "attendees": meeting["attendees"]},
        founder_id=FOUNDER, session_id=SESSION, subject_hash=subject_hash)
    await firestore.grant_approval(approval_id, FOUNDER)
    return approval_id


async def book(meeting: dict, **kwargs) -> dict:
    return await calendar_adapter.create_event(
        meeting["summary"], meeting["start_iso"], meeting["end_iso"],
        meeting["attendees"], founder_id=FOUNDER, session_id=SESSION, **kwargs)


# --- 1. the approval is bound to ONE meeting -------------------------------

class TestApprovalContentBinding:
    async def test_booking_b_under_an_approval_for_a_is_refused(
            self, fake_store, calendar):
        """The defect: `find_valid_approval` by target only, then the approved
        details were substituted over the call args and `drift_ignored` audited
        — so asking for B booked A and reported "Booked A"."""
        approval_id = await grant_for(MEETING_A)

        result = await book(MEETING_B)

        assert result["status"] == "error" and result["error"] is True
        assert result["error_code"] == "approval_binding_mismatch"
        assert calendar._events.inserted == []       # nothing was booked
        assert calendar._events.events == {}         # and A is NOT on the calendar
        assert "different meeting" in result["message"]
        # The grant for A was not spent on B.
        assert (await firestore.get_approval(approval_id))["status"] != "CONSUMED"
        assert any(row["result"] == "refused"
                   and "approval_binding_mismatch" in row["detail"]
                   for row in fake_store.audit)
        # Nothing is ever "resolved" by substitution any more.
        assert not any(row["result"] == "drift_ignored" for row in fake_store.audit)

    async def test_approving_a_then_asking_for_b_books_nothing(
            self, fake_store, calendar):
        """The same defect end to end, through the public API only: approve
        exactly what Alex asked for, then let Alex ask for something else."""
        asked = await book(MEETING_A)
        await firestore.grant_approval(asked["approval_id"], FOUNDER)

        result = await book(MEETING_B)

        assert calendar._events.inserted == []
        assert result["status"] != "success"
        assert "Booked" not in result["message"]

    async def test_mismatch_does_not_replace_the_founders_existing_grant(
            self, fake_store, calendar):
        approval_id = await grant_for(MEETING_A)
        result = await book(MEETING_B)
        assert result["error_code"] == "approval_binding_mismatch"
        assert not result.get("approval_id")
        assert (await firestore.get_approval(approval_id))["status"] == "GRANTED"
        assert not any(
            row.get("status") == "PENDING"
            and row.get("subject_hash") == subject_hash_for(MEETING_B)
            for row in fake_store.approvals.values())

    async def test_approval_without_a_subject_hash_fails_closed(
            self, fake_store, calendar):
        """A legacy/unbound grant proves nothing about what the founder saw."""
        await grant_for(MEETING_A, subject_hash="")

        result = await book(MEETING_A)

        assert result["status"] == "error"
        assert result["error_code"] == "approval_binding_missing"
        assert "not bound to a specific" in result["message"]
        assert calendar._events.inserted == []

    async def test_changed_description_alone_is_refused(self, fake_store, calendar):
        """Title/time/guests identical, invite body rewritten — still a
        different thing than the founder approved."""
        await grant_for(MEETING_A, subject_hash=subject_hash_for(MEETING_A))

        result = await book(MEETING_A, description="Please wire funds to ...")

        assert result["error_code"] == "approval_binding_mismatch"
        assert calendar._events.inserted == []

    async def test_matching_approval_books_and_consumes_it(self, fake_store, calendar):
        approval_id = await grant_for(MEETING_A)

        result = await book(MEETING_A)

        assert result["status"] == "success"
        booked = calendar._events.inserted[0]
        assert booked["summary"] == "Intro call"
        assert booked["attendees"] == [{"email": "investor@fund.com"}]
        assert (await firestore.get_approval(approval_id))["status"] == "CONSUMED"

    async def test_no_approval_requests_one_bound_to_this_meeting(
            self, fake_store, calendar):
        result = await book(MEETING_A)
        assert result["status"] == "needs_approval"
        pending = await firestore.get_approval(result["approval_id"])
        assert pending["gate"] == "book_meeting"
        assert pending["subject_hash"] == subject_hash_for(MEETING_A)
        assert calendar._events.inserted == []

    async def test_equivalent_time_spellings_bind_identically(
            self, fake_store, calendar):
        """The subject is the instant, not the string: "14:00+01:00" and
        "14:00:00+01:00" are the same meeting and must not refuse."""
        await grant_for(MEETING_A)
        result = await book({**MEETING_A, "start_iso": "2026-08-25T14:00+01:00"})
        assert result["status"] == "success"


# --- 2. deterministic event id = native idempotency ------------------------

class TestDeterministicEventId:
    def test_id_is_stable_and_uses_calendars_charset(self):
        subject = subject_hash_for(MEETING_A)
        first = calendar_adapter.event_idempotency_id(TARGET, FOUNDER, subject)
        again = calendar_adapter.event_idempotency_id(TARGET, FOUNDER, subject)
        assert first == again                       # a retry keys the same event
        assert 5 <= len(first) <= 1024              # Calendar's length rule
        legal = set("abcdefghijklmnopqrstuv0123456789")  # base32hex, lowercased
        assert set(first) <= legal

    def test_a_different_meeting_gets_a_different_id(self):
        assert (calendar_adapter.event_idempotency_id(
                    TARGET, FOUNDER, subject_hash_for(MEETING_A))
                != calendar_adapter.event_idempotency_id(
                    TARGET, FOUNDER, subject_hash_for(MEETING_B)))

    async def test_insert_carries_the_deterministic_id_not_a_random_uuid(
            self, fake_store, calendar):
        await grant_for(MEETING_A)
        result = await book(MEETING_A)
        expected = calendar_adapter.event_idempotency_id(
            TARGET, FOUNDER, subject_hash_for(MEETING_A))
        body = calendar._events.inserted[0]
        assert body["id"] == expected
        assert result["event_id"] == expected
        # the Meet requestId is keyed to the same identity, not uuid4()
        assert body["conferenceData"]["createRequest"]["requestId"] == expected

    async def test_duplicate_insert_is_success_and_creates_no_second_event(
            self, fake_store, calendar):
        """The founder re-approves after an ambiguous outcome and Alex retries:
        Calendar answers 409 duplicate, which means the event EXISTS."""
        await grant_for(MEETING_A)
        first = await book(MEETING_A)
        assert first["status"] == "success"

        await grant_for(MEETING_A)          # fresh grant, same meeting
        second = await book(MEETING_A)

        assert second["status"] == "success" and second["duplicate"] is True
        assert second["event_id"] == first["event_id"]
        assert second["meet_link"] == first["meet_link"]
        assert len(calendar._events.events) == 1     # no double booking
        assert len(calendar._events.inserted) == 1   # no second round of invites
        assert "already booked" in second["message"]


# --- 3. UNCERTAIN receipts and reconciliation ------------------------------

class TestUncertainOutcome:
    async def test_timeout_after_insert_is_uncertain_not_a_plain_error(
            self, fake_store, monkeypatch):
        svc = _FakeCalendar(raise_on_insert=TimeoutError("deadline exceeded"))
        calendar_adapter.set_service_factory(lambda: svc)
        try:
            await grant_for(MEETING_A)
            result = await book(MEETING_A)
        finally:
            calendar_adapter.set_service_factory(None)

        assert result["status"] == "error"
        assert result["error_code"] == "provider_outcome_uncertain"
        assert result["uncertain"] is True
        # the reconciliation handle comes back with the receipt
        assert result["event_id"] == calendar_adapter.event_idempotency_id(
            TARGET, FOUNDER, subject_hash_for(MEETING_A))
        assert any(row["result"] == "uncertain"
                   and row["idempotency_key"] == result["event_id"]
                   for row in fake_store.audit)

    async def test_rejected_request_is_a_definitive_failure(self, fake_store):
        """A 403 means Calendar created nothing — do not cry uncertainty."""
        svc = _FakeCalendar(raise_on_insert=_FakeHttpError(403, "Forbidden"))
        calendar_adapter.set_service_factory(lambda: svc)
        try:
            await grant_for(MEETING_A)
            result = await book(MEETING_A)
        finally:
            calendar_adapter.set_service_factory(None)
        assert result["error_code"] == "event_insert_failed"
        assert result.get("uncertain") is not True

    async def test_reconcile_finds_the_event_that_did_land(
            self, fake_store, calendar):
        await grant_for(MEETING_A)
        booked = await book(MEETING_A)

        resolved = await calendar_adapter.reconcile_event(booked["event_id"])

        assert resolved["status"] == "success" and resolved["exists"] is True
        assert resolved["summary"] == "Intro call"
        assert resolved["meet_link"] == booked["meet_link"]

    async def test_reconcile_reports_a_booking_that_never_landed(
            self, fake_store, calendar):
        event_id = calendar_adapter.event_idempotency_id(
            TARGET, FOUNDER, subject_hash_for(MEETING_A))
        resolved = await calendar_adapter.reconcile_event(event_id)
        assert resolved["status"] == "success" and resolved["exists"] is False
        assert any(row["action"] == "book_meeting_reconcile"
                   for row in fake_store.audit)


# --- 4. the scope pre-check stays off the hot path -------------------------

class TestScopePreCheck:
    """`_service_factory is None` is the prod path, so `_service` itself is
    swapped here to keep the pre-check live without OAuth."""

    @pytest.fixture(autouse=True)
    def prod_service(self, monkeypatch):
        svc = _FakeCalendar()
        monkeypatch.setattr(
            calendar_adapter, "_service", lambda _workspace_id="": svc)
        monkeypatch.setattr(calendar_adapter, "_service_factory", None)
        return svc

    async def test_cached_scopes_are_used_without_a_tokeninfo_call(
            self, fake_store, prod_service, monkeypatch):
        def _boom(*_a, **_k):
            raise AssertionError("granted_scopes() hit the network on the hot path")
        monkeypatch.setattr(google_oauth, "granted_scopes", _boom)
        monkeypatch.setitem(google_oauth._granted, (FOUNDER, "founder"),
                            frozenset({calendar_adapter._CAL_WRITE_SCOPE}))

        await grant_for(MEETING_A)
        result = await book(MEETING_A)

        assert result["status"] == "success"

    async def test_cached_scopes_without_write_still_refuse(
            self, fake_store, prod_service, monkeypatch):
        monkeypatch.setitem(
            google_oauth._granted, (FOUNDER, "founder"),
            frozenset({"https://www.googleapis.com/auth/calendar.readonly"}))
        result = await book(MEETING_A)
        assert result["status"] == "error"
        assert "reconnect Calendar" in result["message"]
        assert prod_service._events.inserted == []

    async def test_an_unavailable_scope_lookup_never_blocks_booking(
            self, fake_store, prod_service, monkeypatch):
        """Cold cache + a failing/slow tokeninfo is "unknown", not "denied"."""
        google_oauth._granted.pop((FOUNDER, "founder"), None)

        def _fail(*_a, **_k):
            raise RuntimeError("tokeninfo unreachable")
        monkeypatch.setattr(google_oauth, "granted_scopes", _fail)

        await grant_for(MEETING_A)
        result = await book(MEETING_A)

        assert result["status"] == "success"
