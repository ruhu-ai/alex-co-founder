"""Tool-less reasoning boundaries for the durable interview-scheduling goal.

This module is the versioned application skill for ``SCHEDULE_INTERVIEW``. A
model may interpret an applicant's untrusted reply and make the connective
prose sound natural, but it has no tools, connector access, durable writes, or
effect authority. Deterministic Hiring code supplies current facts, chooses a
bounded action, rechecks Calendar availability, and owns every state/effect.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEDULING_SKILL_VERSION = "schedule-interview.v2"
_MAX_CONSTRAINTS = 4


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
    proposed_starts: list[str] = Field(default_factory=list, max_length=_MAX_CONSTRAINTS)
    availability_windows: list[str] = Field(
        default_factory=list, max_length=_MAX_CONSTRAINTS)
    unavailable_windows: list[str] = Field(
        default_factory=list, max_length=_MAX_CONSTRAINTS)
    timezone: str = Field(default="", max_length=80)
    clarification_needed: str = Field(default="", max_length=240)
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "LOW"


class SchedulingDraft(BaseModel):
    """Non-authoritative prose fragments; code composes all schedule facts."""

    model_config = ConfigDict(extra="forbid")

    acknowledgement: str = Field(default="", max_length=280)
    closing: str = Field(default="", max_length=220)


InterpreterFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
DraftFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
_interpreter_fn: InterpreterFn | None = None
_draft_fn: DraftFn | None = None


def set_interpreter_fn(fn: InterpreterFn | None) -> None:
    """Inject the tool-less interpretation boundary; tests stay network-free."""
    global _interpreter_fn
    _interpreter_fn = fn


def set_draft_fn(fn: DraftFn | None) -> None:
    """Inject the tool-less natural-language boundary; tests stay network-free."""
    global _draft_fn
    _draft_fn = fn


def response_schema() -> dict[str, Any]:
    """Return the conservative structural subset accepted by Vertex."""
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string"},
            "selected_option": {"type": "integer"},
            "proposed_start": {"type": "string"},
            "proposed_starts": {"type": "array", "items": {"type": "string"}},
            "availability_windows": {
                "type": "array", "items": {"type": "string"}},
            "unavailable_windows": {
                "type": "array", "items": {"type": "string"}},
            "timezone": {"type": "string"},
            "clarification_needed": {"type": "string"},
            "confidence": {"type": "string"},
        },
        "required": [
            "intent", "selected_option", "proposed_start", "proposed_starts",
            "availability_windows", "unavailable_windows", "timezone",
            "clarification_needed", "confidence",
        ],
    }


def draft_response_schema() -> dict[str, Any]:
    """Return a deliberately tiny schema for non-authoritative prose."""
    return {
        "type": "object",
        "properties": {
            "acknowledgement": {"type": "string"},
            "closing": {"type": "string"},
        },
        "required": ["acknowledgement", "closing"],
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
    base = {
        "status": "success", "selected_option": selected,
        "proposed_start": "", "proposed_starts": [],
        "availability_windows": [], "unavailable_windows": [],
        "timezone": "", "clarification_needed": "",
        "skill_version": SCHEDULING_SKILL_VERSION,
    }
    if selected:
        return {
            **base,
            "intent": ("REQUEST_RESCHEDULE" if has_booking
                       else "ACCEPT_OFFERED_SLOT"),
            "confidence": "HIGH", "interpretation_mode": "DETERMINISTIC",
        }
    if re.search(r"\b(cancel|withdraw|no longer|cannot attend)\b", text):
        return {
            **base, "intent": "REQUEST_CANCELLATION", "confidence": "HIGH",
            "interpretation_mode": "DETERMINISTIC",
        }
    return {
        **base, "intent": "ASK_CLARIFICATION", "confidence": "LOW",
        "clarification_needed": "A specific option, date, or availability window is needed.",
        "interpretation_mode": "DETERMINISTIC_FALLBACK",
    }


def _parse_time(value: str, *, zone: ZoneInfo, now: datetime,
                window_end: datetime) -> str | None:
    try:
        start = datetime.fromisoformat(value)
        if start.tzinfo is None:
            start = start.replace(tzinfo=zone)
        if start <= now or start >= window_end:
            return None
        return start.isoformat()
    except ValueError:
        return None


def _parse_window(value: str, *, zone: ZoneInfo, now: datetime,
                  window_end: datetime) -> str | None:
    parts = value.split("/", 1)
    if len(parts) != 2:
        return None
    try:
        start, end = (datetime.fromisoformat(item) for item in parts)
        if start.tzinfo is None:
            start = start.replace(tzinfo=zone)
        if end.tzinfo is None:
            end = end.replace(tzinfo=zone)
        if start >= end or end <= now or start >= window_end:
            return None
        start, end = max(start, now), min(end, window_end)
        return f"{start.isoformat()}/{end.isoformat()}" if start < end else None
    except ValueError:
        return None


async def interpret_scheduling_reply(
        reply: str, *, offered_slots: list[dict[str, str]],
        founder_timezone: str, duration_minutes: int, has_booking: bool,
        current_time: str, scheduling_window_end: str,
        current_booking: dict[str, str] | None = None) -> dict[str, Any]:
    """Interpret one reply into bounded constraints, never an authorized action."""
    normalized = re.sub(r"\s+", " ", str(reply or "")).strip()[:1600]
    deterministic = _deterministic(normalized.casefold(), has_booking=has_booking)
    if deterministic["confidence"] == "HIGH" or _interpreter_fn is None:
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
        "skill_version": SCHEDULING_SKILL_VERSION,
    }
    try:
        raw = dict(await asyncio.wait_for(_interpreter_fn(payload), timeout=15))
        # Compatibility for the previously deployed interpreter contract.
        raw.setdefault("proposed_starts", [])
        raw.setdefault("availability_windows", [])
        raw.setdefault("unavailable_windows", [])
        raw.setdefault("clarification_needed", "")
        parsed = SchedulingInterpretation.model_validate(raw)
        zone = ZoneInfo(parsed.timezone or founder_timezone)
        now = datetime.fromisoformat(current_time)
        window_end = datetime.fromisoformat(scheduling_window_end)
    except (TimeoutError, asyncio.TimeoutError, ValidationError, ValueError,
            TypeError, ZoneInfoNotFoundError):
        return deterministic
    result = parsed.model_dump()
    result.update(status="success", interpretation_mode="GEMINI_GROUNDED",
                  skill_version=SCHEDULING_SKILL_VERSION)
    if parsed.confidence != "HIGH":
        result.update(intent="ASK_CLARIFICATION", selected_option=0,
                      proposed_start="", proposed_starts=[])
        return result
    if parsed.selected_option > len(offered_slots):
        result.update(intent="ASK_CLARIFICATION", selected_option=0)
    candidates = ([parsed.proposed_start] if parsed.proposed_start else [])
    candidates.extend(parsed.proposed_starts)
    valid_starts: list[str] = []
    for value in candidates[:_MAX_CONSTRAINTS]:
        parsed_time = _parse_time(value, zone=zone, now=now, window_end=window_end)
        if parsed_time and parsed_time not in valid_starts:
            valid_starts.append(parsed_time)
    valid_windows = [
        item for value in parsed.availability_windows[:_MAX_CONSTRAINTS]
        if (item := _parse_window(value, zone=zone, now=now,
                                  window_end=window_end))
    ]
    excluded = [
        item for value in parsed.unavailable_windows[:_MAX_CONSTRAINTS]
        if (item := _parse_window(value, zone=zone, now=now,
                                  window_end=window_end))
    ]
    result.update(
        proposed_start=valid_starts[0] if valid_starts else "",
        proposed_end=((datetime.fromisoformat(valid_starts[0])
                       + timedelta(minutes=duration_minutes)).isoformat()
                      if valid_starts else ""),
        proposed_starts=valid_starts,
        availability_windows=valid_windows,
        unavailable_windows=excluded,
        timezone=parsed.timezone or founder_timezone,
    )
    if (parsed.intent in {"ACCEPT_OFFERED_SLOT", "REQUEST_RESCHEDULE"}
            and not parsed.selected_option and not valid_starts
            and not valid_windows):
        result["intent"] = "ASK_CLARIFICATION"
    if parsed.intent == "PROPOSE_ALTERNATIVE" and not valid_starts and not valid_windows:
        result["intent"] = "ASK_CLARIFICATION"
    return result


def _safe_fragment(value: str, fallback: str, max_length: int) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]
    if (not value or re.search(
            r"https?://|www\.|@[A-Za-z0-9]|\b\d{1,2}:\d{2}\b|"
            r"\b(?:mon|tue|wed|thu|fri|sat|sun)(?:day)?\b",
            value, re.IGNORECASE)):
        return fallback
    return value


async def draft_scheduling_reply(
        *, applicant_reply: str, candidate_first_name: str, role_title: str,
        outcome: Literal["OFFER_ALTERNATIVES", "ASK_CLARIFICATION"],
        verified_slots: list[dict[str, str]], duration_minutes: int,
        provider_thread_id: str) -> dict[str, Any]:
    """Compose a natural same-thread reply around code-owned scheduling facts."""
    fallback_ack = ("Thanks for sharing your availability."
                    if outcome == "OFFER_ALTERNATIVES" else
                    "Thanks for the update.")
    fallback_close = ("Please reply with the option that works best."
                      if verified_slots else
                      "Please share a specific date, time, and time zone that works for you.")
    raw: dict[str, Any] = {}
    if _draft_fn is not None:
        try:
            raw = SchedulingDraft.model_validate(await asyncio.wait_for(_draft_fn({
                "applicant_reply": re.sub(r"\s+", " ", applicant_reply).strip()[:1200],
                "candidate_first_name": candidate_first_name[:80],
                "role_title": role_title[:160],
                "outcome": outcome,
                "verified_slot_count": len(verified_slots[:3]),
                "duration_minutes": duration_minutes,
                "skill_version": SCHEDULING_SKILL_VERSION,
            }), timeout=15)).model_dump()
        except (TimeoutError, asyncio.TimeoutError, ValidationError, ValueError,
                TypeError):
            raw = {}
    acknowledgement = _safe_fragment(
        str(raw.get("acknowledgement") or ""), fallback_ack, 280)
    closing = _safe_fragment(str(raw.get("closing") or ""), fallback_close, 220)
    slot_block = "\n".join(
        f"{index + 1}. {str(slot.get('display') or '')}"
        for index, slot in enumerate(verified_slots[:3]))
    details = (
        f"The Founder is currently available for a {duration_minutes}-minute interview at:\n"
        f"{slot_block}\n\n" if slot_block else "")
    body = (
        f"Hi {candidate_first_name or 'there'},\n\n{acknowledgement}\n\n"
        f"{details}{closing}\n\nAlex\nAI co-founder, Ruhu")
    return {
        "status": "success",
        "subject": f"Re: Interview availability — {role_title}",
        "body": body,
        "provider_thread_id": provider_thread_id,
        "skill_version": SCHEDULING_SKILL_VERSION,
        "draft_mode": "GEMINI_COMPOSED" if raw else "DETERMINISTIC_FALLBACK",
    }
