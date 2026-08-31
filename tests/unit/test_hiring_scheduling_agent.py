"""Tool-less scheduling interpretation boundary regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.hiring_scheduling_agent import (
    draft_scheduling_reply,
    interpret_scheduling_reply,
    set_draft_fn,
    set_interpreter_fn,
)


@pytest.fixture(autouse=True)
def _reset_interpreter():
    set_interpreter_fn(None)
    set_draft_fn(None)
    yield
    set_interpreter_fn(None)
    set_draft_fn(None)


def _window():
    now = datetime.now(timezone.utc)
    return now, now + timedelta(days=14)


@pytest.mark.asyncio
async def test_explicit_option_uses_deterministic_boundary_without_model():
    now, end = _window()
    result = await interpret_scheduling_reply(
        "Option two works for me.", offered_slots=[{}, {}, {}],
        founder_timezone="Africa/Lagos", duration_minutes=60,
        has_booking=False, current_time=now.isoformat(),
        scheduling_window_end=end.isoformat())
    assert result["intent"] == "ACCEPT_OFFERED_SLOT"
    assert result["selected_option"] == 2
    assert result["interpretation_mode"] == "DETERMINISTIC"


@pytest.mark.asyncio
async def test_exact_rendered_slot_uses_deterministic_boundary_without_model():
    now, end = _window()
    result = await interpret_scheduling_reply(
        "Hi Alex, Tue 01 Sep, 14:00–15:00 WAT works for me. Regards, Adaeze.",
        offered_slots=[
            {"display": "Tue 01 Sep, 10:00–11:00 WAT"},
            {"display": "Tue 01 Sep, 14:00–15:00 WAT"},
            {"display": "Tue 01 Sep, 16:00–17:00 WAT"},
        ],
        founder_timezone="Africa/Lagos", duration_minutes=60,
        has_booking=False, current_time=now.isoformat(),
        scheduling_window_end=end.isoformat())
    assert result["intent"] == "ACCEPT_OFFERED_SLOT"
    assert result["selected_option"] == 2
    assert result["interpretation_mode"] == "DETERMINISTIC"


@pytest.mark.asyncio
async def test_model_may_resolve_concrete_time_but_cannot_authorize_it():
    now, end = _window()
    proposed = now + timedelta(days=2)

    async def interpreter(_payload):
        return {
            "intent": "PROPOSE_ALTERNATIVE", "selected_option": 0,
            "proposed_start": proposed.isoformat(), "timezone": "Africa/Lagos",
            "confidence": "HIGH",
        }

    set_interpreter_fn(interpreter)
    result = await interpret_scheduling_reply(
        "Tuesday afternoon would work better.", offered_slots=[],
        founder_timezone="Africa/Lagos", duration_minutes=45,
        has_booking=False, current_time=now.isoformat(),
        scheduling_window_end=end.isoformat())
    assert result["intent"] == "PROPOSE_ALTERNATIVE"
    assert datetime.fromisoformat(result["proposed_end"]) - datetime.fromisoformat(
        result["proposed_start"]) == timedelta(minutes=45)
    assert result["interpretation_mode"] == "GEMINI_GROUNDED"


@pytest.mark.asyncio
async def test_low_confidence_or_out_of_window_time_requires_clarification():
    now, end = _window()

    async def interpreter(_payload):
        return {
            "intent": "PROPOSE_ALTERNATIVE", "selected_option": 0,
            "proposed_start": (end + timedelta(days=2)).isoformat(),
            "timezone": "Africa/Lagos", "confidence": "HIGH",
        }

    set_interpreter_fn(interpreter)
    result = await interpret_scheduling_reply(
        "Some time later would work.", offered_slots=[],
        founder_timezone="Africa/Lagos", duration_minutes=60,
        has_booking=False, current_time=now.isoformat(),
        scheduling_window_end=end.isoformat())
    assert result["intent"] == "ASK_CLARIFICATION"
    assert result["proposed_start"] == ""


@pytest.mark.asyncio
async def test_multi_constraint_reply_preserves_slots_windows_and_exclusions():
    now, end = _window()
    proposed = now + timedelta(days=2, hours=1)
    window_start = now + timedelta(days=2)
    window_end = window_start + timedelta(hours=8)
    excluded_start = window_start
    excluded_end = excluded_start + timedelta(hours=1)

    async def interpreter(_payload):
        return {
            "intent": "PROPOSE_ALTERNATIVE", "selected_option": 0,
            "proposed_start": proposed.isoformat(),
            "proposed_starts": [proposed.isoformat()],
            "availability_windows": [
                f"{window_start.isoformat()}/{window_end.isoformat()}"],
            "unavailable_windows": [
                f"{excluded_start.isoformat()}/{excluded_end.isoformat()}"],
            "timezone": "Africa/Lagos", "clarification_needed": "",
            "confidence": "HIGH",
        }

    set_interpreter_fn(interpreter)
    result = await interpret_scheduling_reply(
        "Tuesday at 11, or any working hour Tuesday; not first thing.",
        offered_slots=[], founder_timezone="Africa/Lagos", duration_minutes=60,
        has_booking=False, current_time=now.isoformat(),
        scheduling_window_end=end.isoformat())
    assert result["intent"] == "PROPOSE_ALTERNATIVE"
    assert result["proposed_starts"] == [proposed.isoformat()]
    assert len(result["availability_windows"]) == 1
    assert len(result["unavailable_windows"]) == 1
    assert result["skill_version"] == "schedule-interview.v2"


@pytest.mark.asyncio
async def test_natural_draft_cannot_invent_schedule_facts():
    async def writer(_payload):
        return {
            "acknowledgement": "Tuesday at 7:30 works perfectly.",
            "closing": "See https://evil.example for the final time.",
        }

    set_draft_fn(writer)
    result = await draft_scheduling_reply(
        applicant_reply="Any time Tuesday works.", candidate_first_name="Ada",
        role_title="Product Lead", outcome="OFFER_ALTERNATIVES",
        verified_slots=[{"display": "Tue 01 Sep, 14:00–15:00 WAT"}],
        duration_minutes=60, provider_thread_id="thread_1")
    assert "Tuesday at 7:30" not in result["body"]
    assert "evil.example" not in result["body"]
    assert "Tue 01 Sep, 14:00–15:00 WAT" in result["body"]
    assert result["provider_thread_id"] == "thread_1"
