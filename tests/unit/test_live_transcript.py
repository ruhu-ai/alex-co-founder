"""One canonical, incremental Live transcript authority."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.genai import types

from services.live_transcript import (
    CanonicalLiveSessionService,
    LiveTranscriptAccumulator,
    LiveTranscriptCommitter,
)


class FakeSessionService:
    def __init__(self) -> None:
        self.session = SimpleNamespace(events=[])
        self.append_calls = 0

    async def get_session(self, **_kwargs):
        return self.session

    async def append_event(self, _session, event):
        self.append_calls += 1
        if any(existing.id == event.id for existing in self.session.events):
            raise ValueError("duplicate event")
        self.session.events.append(event)
        return event


class RevisionedSessionService:
    """Synthetic store with the same optimistic revision rule as ADK."""

    def __init__(self) -> None:
        self.events = []
        self.revision = 0

    async def get_session(self, **_kwargs):
        return SimpleNamespace(
            app_name="co_founder",
            user_id="founder",
            id="session",
            events=list(self.events),
            state={},
            last_update_time=float(self.revision),
            _storage_update_marker=self.revision,
        )

    async def append_event(self, session, event):
        if session._storage_update_marker != self.revision:
            raise RuntimeError("stale session")
        if any(existing.id == event.id for existing in self.events):
            raise ValueError("duplicate event")
        self.events.append(event)
        self.revision += 1
        session.events.append(event)
        session.last_update_time = float(self.revision)
        session._storage_update_marker = self.revision
        return event


class LostAckRevisionedSessionService(RevisionedSessionService):
    """Persist once, then simulate losing the acknowledgement and local marker."""

    async def append_event(self, session, event):
        if session._storage_update_marker != self.revision:
            raise RuntimeError("stale session")
        if any(existing.id == event.id for existing in self.events):
            raise ValueError("duplicate event")
        self.events.append(event)
        self.revision += 1
        raise ConnectionError("acknowledgement lost after storage commit")


def test_live_caption_segments_accumulate_until_the_real_turn_boundary():
    accumulator = LiveTranscriptAccumulator()

    assert accumulator.add(
        speaker="user", text="Hello Alex", finished=True) == "Hello Alex"
    assert accumulator.add(
        speaker="user", text="can you hear me?", finished=True,
    ) == "Hello Alex can you hear me?"
    assert accumulator.add(
        speaker="agent", text="Yes, I can", finished=False,
    ) == "Yes, I can"
    assert accumulator.add(
        speaker="agent", text="Yes, I can hear you.", finished=True,
    ) == "Yes, I can hear you."

    assert accumulator.final_text("user") == "Hello Alex can you hear me?"
    assert accumulator.final_text("agent") == "Yes, I can hear you."
    assert accumulator.preview("agent") == "Yes, I can hear you."

    accumulator.clear()
    assert accumulator.final_text("user") == ""
    assert accumulator.final_text("agent") == ""


def test_live_caption_accumulator_deduplicates_cumulative_provider_updates():
    accumulator = LiveTranscriptAccumulator()

    accumulator.add(speaker="agent", text="I will focus", finished=True)
    accumulator.add(
        speaker="agent", text="I will focus on the role brief", finished=True,
    )
    accumulator.add(speaker="agent", text="role brief first", finished=True)

    assert accumulator.final_text("agent") == "I will focus on the role brief first"


def test_turn_boundary_can_finalize_the_last_partial_caption():
    accumulator = LiveTranscriptAccumulator()
    accumulator.add(
        speaker="user", text="Please pause after this sentence", finished=False,
    )

    assert accumulator.final_text("user") == ""
    assert accumulator.preview("user") == "Please pause after this sentence"


def test_runner_accepts_canonical_live_session_service_contract():
    service = FakeSessionService()
    wrapper = CanonicalLiveSessionService(service)

    assert isinstance(wrapper, BaseSessionService)
    runner = Runner(
        app=App(
            name="live_contract_smoke",
            root_agent=Agent(name="live_contract_root"),
        ),
        session_service=wrapper,
    )

    assert runner.session_service is wrapper


async def test_final_turn_commit_is_idempotent_after_lost_ack(monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "unit-test-live-secret")
    service = FakeSessionService()
    committer = LiveTranscriptCommitter(
        session_service=service, app_name="co_founder", user_id="founder",
        session_id="session", connection_generation=7)
    turn_id = committer.new_turn_id()

    first = await committer.commit(
        speaker="user", turn_id=turn_id, text="  hello   Alex ",
        source="input_transcription")
    replay = await committer.commit(
        speaker="user", turn_id=turn_id, text="hello Alex",
        source="input_transcription")

    assert first["created"] is True
    assert replay["idempotent"] is True
    assert first["event_id"] == replay["event_id"]
    assert len(service.session.events) == 1
    assert service.session.events[0].custom_metadata["live_turn_id"] == turn_id


async def test_changed_text_cannot_reuse_a_final_turn_identity(monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "unit-test-live-secret")
    service = FakeSessionService()
    committer = LiveTranscriptCommitter(
        session_service=service, app_name="co_founder", user_id="founder",
        session_id="session", connection_generation=7)
    turn_id = committer.new_turn_id()
    await committer.commit(
        speaker="agent", turn_id=turn_id, text="First final",
        source="output_transcription")

    conflict = await committer.commit(
        speaker="agent", turn_id=turn_id, text="Changed final",
        source="output_transcription")

    assert conflict["error_code"] == "transcript_commit_failed"
    assert len(service.session.events) == 1


async def test_exact_lost_ack_advances_the_shared_session_revision(monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "unit-test-live-secret")
    service = LostAckRevisionedSessionService()
    session = await service.get_session()
    committer = LiveTranscriptCommitter(
        session_service=service,
        app_name="co_founder",
        user_id="founder",
        session_id="session",
        connection_generation=7,
        session=session,
    )

    result = await committer.commit(
        speaker="user",
        turn_id=committer.new_turn_id(),
        text="Persisted before the acknowledgement was lost",
        source="input_transcription",
    )

    assert result["status"] == "success"
    assert result["idempotent"] is True
    assert result["created"] is False
    assert session._storage_update_marker == service.revision == 1
    assert [event.id for event in session.events] == [result["event_id"]]


async def test_runner_wrapper_suppresses_raw_transcript_and_model_text():
    service = FakeSessionService()
    wrapper = CanonicalLiveSessionService(service)
    raw = Event(
        author="co_founder", invocation_id="live:raw",
        output_transcription=types.Transcription(text="spoken", finished=True),
        content=types.Content(role="model", parts=[
            types.Part.from_text(text="raw model text")]),
    )
    await wrapper.append_event(service.session, raw)

    canonical = Event(
        author="co_founder", invocation_id="live:canonical",
        content=types.Content(role="model", parts=[
            types.Part.from_text(text="canonical text")]),
        custom_metadata={"canonical_live_transcript": True},
    )
    await wrapper.append_event(service.session, canonical)

    assert service.session.events == [canonical]
    assert service.append_calls == 1


async def test_two_live_turns_share_one_serialized_session_revision(monkeypatch):
    """Runner control events remain writable after each canonical transcript."""
    monkeypatch.setenv("APP_SESSION_SECRET", "unit-test-live-secret")
    service = RevisionedSessionService()
    session = await service.get_session()
    wrapper = CanonicalLiveSessionService(service)
    committer = LiveTranscriptCommitter(
        session_service=service,
        app_name="co_founder",
        user_id="founder",
        session_id="session",
        connection_generation=7,
        session=session,
    )

    await wrapper.append_event(
        session,
        Event(author="co_founder", invocation_id="live:first-control"),
    )
    first_turn = committer.new_turn_id()
    first_user = await committer.commit(
        speaker="user",
        turn_id=first_turn,
        text="First synthetic turn",
        source="input_transcription",
    )
    first_agent = await committer.commit(
        speaker="agent",
        turn_id=first_turn,
        text="First synthetic response",
        source="output_transcription",
    )

    await wrapper.append_event(
        session,
        Event(author="co_founder", invocation_id="live:second-control"),
    )
    second_turn = committer.new_turn_id()
    second_user = await committer.commit(
        speaker="user",
        turn_id=second_turn,
        text="Second synthetic turn",
        source="input_transcription",
    )
    second_agent = await committer.commit(
        speaker="agent",
        turn_id=second_turn,
        text="Second synthetic response",
        source="output_transcription",
    )
    await wrapper.append_event(
        session,
        Event(author="co_founder", invocation_id="live:final-control"),
    )

    results = (first_user, first_agent, second_user, second_agent)
    assert all(result["status"] == "success" for result in results)
    assert len({result["event_id"] for result in results}) == 4
    assert service.revision == 7
    assert session._storage_update_marker == service.revision
    assert [event.invocation_id for event in service.events] == [
        "live:first-control",
        f"live:{first_turn}",
        f"live:{first_turn}",
        "live:second-control",
        f"live:{second_turn}",
        f"live:{second_turn}",
        "live:final-control",
    ]


def test_live_endpoint_passes_one_session_to_runner_and_committer():
    source = (Path(__file__).resolve().parents[2] / "app" / "live.py").read_text(
        encoding="utf-8",
    )

    assert (
        "connection_generation=media.connection_generation,\n"
        "            session=session)"
    ) in source
    assert (
        "async for event in runner.run_live(\n"
        "                        session=session,"
    ) in source
