"""Founder-scoped canonical conversation continuity."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.co_founder import state_schema as ss
from services import conversation_history as history


def _event(event_id: str, author: str, text: str, timestamp: float = 0):
    return SimpleNamespace(
        id=event_id,
        author=author,
        timestamp=timestamp,
        content=SimpleNamespace(parts=[SimpleNamespace(text=text)]),
    )


def _session(session_id: str, *, mode: str = "STANDARD", events=(), updated=0):
    return SimpleNamespace(
        id=session_id,
        user_id="founder",
        state={ss.K_MEMORY_MODE: mode},
        events=list(events),
        last_update_time=updated,
    )


class _Sessions:
    def __init__(self, sessions):
        self.sessions = {item.id: item for item in sessions}

    async def get_session(self, *, app_name, user_id, session_id):
        assert app_name == "co_founder"
        return self.sessions.get(session_id) if user_id == "founder" else None

    async def list_sessions(self, *, app_name, user_id):
        rows = list(self.sessions.values()) if user_id == "founder" else []
        return SimpleNamespace(sessions=rows)


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "test-session-secret")

    async def _audit(*_args, **_kwargs):
        return True

    monkeypatch.setattr(history, "_audit", _audit)
    yield
    history.configure(session_service=None)


@pytest.mark.asyncio
async def test_searches_owned_standard_sessions_and_opens_cited_context():
    current = _session("current")
    old = _session("old", updated=5, events=[
        _event("e1", "user", "We discussed the Acme funding round", 1),
        _event("e2", "alex", "The evidence pointed to a seed extension", 2),
        _event("e3", "user", "Capture the next actions", 3),
    ])
    private = _session("private", mode="PRIVATE", updated=10, events=[
        _event("secret", "user", "Acme funding round private detail"),
    ])
    history.configure(session_service=_Sessions([current, old, private]))

    result = await history.search(
        founder_id="founder", current_session_id="current",
        query="Acme funding", limit=8)

    assert result["status"] == "success"
    assert [row["session_id"] for row in result["results"]] == ["old"]
    assert result["results"][0]["citation"] == "conversation:old#e1"
    assert result["authority"] == "advisory_transcript_only"

    opened = await history.open_context(
        founder_id="founder", current_session_id="current",
        session_id="old", event_id="e2", surrounding_turns=1)
    assert [row["event_id"] for row in opened["messages"]] == ["e1", "e2", "e3"]
    assert opened["citation"] == "conversation:old#e2"


@pytest.mark.asyncio
async def test_private_current_session_cannot_search_or_open_other_sessions():
    private = _session("current", mode="PRIVATE")
    old = _session("old", events=[_event("e1", "user", "funding research")])
    history.configure(session_service=_Sessions([private, old]))

    search = await history.search(
        founder_id="founder", current_session_id="current",
        query="funding research")
    opened = await history.open_context(
        founder_id="founder", current_session_id="current",
        session_id="old", event_id="e1")

    assert search["error_code"] == "conversation_search_private"
    assert opened["error_code"] == "conversation_search_private"


@pytest.mark.asyncio
async def test_cursor_is_bound_to_founder_and_query_and_pages_arbitrary_history(
        monkeypatch):
    monkeypatch.setattr(history, "MAX_SESSIONS_PER_PAGE", 1)
    sessions = [
        _session("current", updated=4),
        _session("newer", updated=3, events=[_event("n", "user", "other topic")]),
        _session("older", updated=2, events=[_event("o", "user", "funding research")]),
    ]
    history.configure(session_service=_Sessions(sessions))

    first = await history.search(
        founder_id="founder", current_session_id="current",
        query="funding research")
    assert first["results"] == []
    assert first["truncated"] is True
    assert first["next_cursor"]

    second = await history.search(
        founder_id="founder", current_session_id="current",
        query="funding research", cursor=first["next_cursor"])
    assert [row["session_id"] for row in second["results"]] == ["older"]

    midpoint = len(first["next_cursor"]) // 2
    tampered = (first["next_cursor"][:midpoint]
                + ("A" if first["next_cursor"][midpoint] != "A" else "B")
                + first["next_cursor"][midpoint + 1:])
    refused = await history.search(
        founder_id="founder", current_session_id="current",
        query="funding research", cursor=tampered)
    assert refused["error_code"] == "conversation_cursor_invalid"


@pytest.mark.asyncio
async def test_result_limit_cursor_does_not_skip_unreturned_matches(monkeypatch):
    monkeypatch.setattr(history, "MAX_SESSIONS_PER_PAGE", 10)
    sessions = [_session("current", updated=5)] + [
        _session(f"old-{index}", updated=4 - index,
                 events=[_event(f"e-{index}", "user", "funding research")])
        for index in range(3)
    ]
    history.configure(session_service=_Sessions(sessions))

    first = await history.search(
        founder_id="founder", current_session_id="current",
        query="funding research", limit=1)
    second = await history.search(
        founder_id="founder", current_session_id="current",
        query="funding research", limit=1, cursor=first["next_cursor"])

    assert [row["session_id"] for row in first["results"]] == ["old-0"]
    assert [row["session_id"] for row in second["results"]] == ["old-1"]


@pytest.mark.asyncio
async def test_capability_contract_is_truthful_and_missing_session_fails_closed():
    standard = _session("standard")
    private = _session("private", mode="PRIVATE")
    history.configure(session_service=_Sessions([standard, private]))

    normal = await history.capabilities(founder_id="founder", session_id="standard")
    hidden = await history.capabilities(founder_id="founder", session_id="private")
    missing = await history.capabilities(founder_id="founder", session_id="missing")

    assert normal["past_conversation_search"] is True
    assert normal["raw_transcript_is_authority"] is False
    assert hidden["past_conversation_search"] is False
    assert missing["error_code"] == "conversation_not_found"


@pytest.mark.asyncio
async def test_search_returns_no_excerpt_when_content_free_audit_is_unavailable(
        monkeypatch):
    current = _session("current")
    old = _session("old", events=[_event("e", "user", "funding research")])
    history.configure(session_service=_Sessions([current, old]))

    async def _audit_failed(*_args, **_kwargs):
        return False

    monkeypatch.setattr(history, "_audit", _audit_failed)
    result = await history.search(
        founder_id="founder", current_session_id="current",
        query="funding research")
    assert result["error_code"] == "conversation_audit_unavailable"
    assert "results" not in result


def test_shared_context_envelope_never_claims_live_saved_context_is_loaded():
    envelope = history.build_envelope(
        session_id="s", session_mode="STANDARD", saved_context_available=None)
    assert envelope.historical_search_available is True
    assert "not preloaded into this Live connection" in envelope.instruction()
