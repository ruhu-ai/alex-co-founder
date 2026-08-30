"""Tool-less intent boundary for the durable interview-scheduling goal.

The model may interpret an applicant's untrusted reply, but it has no tools,
connector access, durable writes, or effect authority.  Deterministic Hiring
code validates every returned time against the active Founder mandate and the
live Founder Calendar before preparing an email or Calendar mutation.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SchedulingInterpretation(BaseModel):
    """Closed, non-authoritative interpretation of one applicant reply."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "ACCEPT_OFFERED_SLOT",
        "PROPOSE_ALTERNATIVE",
        "REQUEST_RESCHEDULE",
        "REQUEST_CANCELLATION",
        "ASK_CLARIFICATION",
        "OTHER",
    ]
    selected_option: int = Field(default=0, ge=0, le=3)
    proposed_start: str = Field(default="", max_length=64)
    timezone: str = Field(default="", max_length=80)
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "LOW"


InterpreterFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
_interpreter_fn: InterpreterFn | None = None


def set_interpreter_fn(fn: InterpreterFn | None) -> None:
    """Inject the tool-less provider boundary; tests stay network-free."""
    global _interpreter_fn
    _interpreter_fn = fn


def response_schema() -> dict[str, Any]:
    """Return the structural subset accepted by Vertex structured output."""
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string"},
            "selected_option": {"type": "integer"},
            "proposed_start": {"type": "string"},
            "timezone": {"type": "string"},
            "confidence": {"type": "string"},
        },
        "required": [
            "intent", "selected_option", "proposed_start", "timezone",
            "confidence",
        ],
    }


def _selected_option(text: str) -> int:
    patterns = (
        (1, r"\b(?:option|slot)\s*(?:1|one|first)\b|\bfirst\s+(?:option|slot)\b"),
        (2, r"\b(?:option|slot)\s*(?:2|two|second)\b|\bsecond\s+(?:option|slot)\b"),
        (3, r"\b(?:option|slot)\s*(?:3|three|third)\b|\bthird\s+(?:option|slot)\b"),
    )
    matches = [index for index, pattern in patterns if re.search(pattern, text)]
    return matches[0] if len(matches) == 1 else 0


def _deterministic(text: str, *, has_booking: bool) -> dict[str, Any]:
    selected = _selected_option(text)
    if selected:
        return {
            "status": "success", "intent": (
                "REQUEST_RESCHEDULE" if has_booking else "ACCEPT_OFFERED_SLOT"),
            "selected_option": selected, "proposed_start": "",
            "timezone": "", "confidence": "HIGH",
            "interpretation_mode": "DETERMINISTIC",
        }
    if re.search(r"\b(cancel|withdraw|no longer|cannot attend)\b", text):
        return {
            "status": "success", "intent": "REQUEST_CANCELLATION",
            "selected_option": 0, "proposed_start": "", "timezone": "",
            "confidence": "HIGH", "interpretation_mode": "DETERMINISTIC",
        }
    return {
        "status": "success", "intent": "ASK_CLARIFICATION",
        "selected_option": 0, "proposed_start": "", "timezone": "",
        "confidence": "LOW", "interpretation_mode": "DETERMINISTIC_FALLBACK",
    }


async def interpret_scheduling_reply(
        reply: str, *, offered_slots: list[dict[str, str]],
        founder_timezone: str, duration_minutes: int, has_booking: bool,
        current_time: str, scheduling_window_end: str,
        current_booking: dict[str, str] | None = None) -> dict[str, Any]:
    """Interpret one reply and return only a deterministically bounded intent."""
    normalized = re.sub(r"\s+", " ", str(reply or "")).strip()[:1000]
    folded = normalized.casefold()
    deterministic = _deterministic(folded, has_booking=has_booking)
    if deterministic["confidence"] == "HIGH":
        return deterministic
    if _interpreter_fn is None:
        return deterministic
    payload = {
        "reply": normalized,
        "offered_slots": [
            {key: slot.get(key, "") for key in ("start", "end", "timezone", "display")}
            for slot in offered_slots[:3]
        ],
        "founder_timezone": founder_timezone,
        "duration_minutes": duration_minutes,
        "has_existing_booking": has_booking,
        "current_booking": {
            key: str((current_booking or {}).get(key) or "")
            for key in ("start", "end", "timezone")
        },
        "current_time": current_time,
        "scheduling_window_end": scheduling_window_end,
    }
    try:
        raw = await asyncio.wait_for(_interpreter_fn(payload), timeout=15)
        parsed = SchedulingInterpretation.model_validate(raw)
    except (TimeoutError, asyncio.TimeoutError, ValidationError, ValueError, TypeError):
        return deterministic
    result = parsed.model_dump()
    result.update(status="success", interpretation_mode="GEMINI_GROUNDED")
    if parsed.confidence != "HIGH":
        result.update(intent="ASK_CLARIFICATION", selected_option=0,
                      proposed_start="", timezone="")
        return result
    if parsed.intent in {"ACCEPT_OFFERED_SLOT", "REQUEST_RESCHEDULE"}:
        if parsed.selected_option:
            if parsed.selected_option > len(offered_slots):
                result.update(intent="ASK_CLARIFICATION", selected_option=0)
            return result
        if not parsed.proposed_start:
            result.update(intent="ASK_CLARIFICATION")
            return result
    if parsed.intent == "PROPOSE_ALTERNATIVE":
        if not parsed.proposed_start:
            result.update(intent="ASK_CLARIFICATION")
            return result
    if parsed.proposed_start:
        try:
            zone = ZoneInfo(parsed.timezone or founder_timezone)
            start = datetime.fromisoformat(parsed.proposed_start)
            if start.tzinfo is None:
                start = start.replace(tzinfo=zone)
            end = start + timedelta(minutes=duration_minutes)
            now = datetime.fromisoformat(current_time)
            window_end = datetime.fromisoformat(scheduling_window_end)
            if start <= now or end > window_end:
                raise ValueError("outside scheduling window")
        except (ValueError, ZoneInfoNotFoundError):
            result.update(intent="ASK_CLARIFICATION", selected_option=0,
                          proposed_start="", timezone="")
            return result
        result["proposed_start"] = start.isoformat()
        result["proposed_end"] = end.isoformat()
        result["timezone"] = parsed.timezone or founder_timezone
    return result
