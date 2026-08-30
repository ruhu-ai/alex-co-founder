"""Tool-less scheduling interpretation boundary regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.hiring_scheduling_agent import (
    interpret_scheduling_reply,
    set_interpreter_fn,
)


@pytest.fixture(autouse=True)
def _reset_interpreter():
    set_interpreter_fn(None)
    yield
    set_interpreter_fn(None)


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
