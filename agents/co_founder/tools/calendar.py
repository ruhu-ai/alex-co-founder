"""Calendar tools (docs/adr/002): read-only availability + upcoming meetings.

Read-only by construction — booking stays with the founder until
approval-gated invites ship. Errors as data (principle 2).
"""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext

from services import calendar_adapter


async def get_upcoming_meetings(tool_context: ToolContext, days_ahead: int = 7) -> dict:
    """List the founder's upcoming calendar events (next `days_ahead` days).

    Use to see what meetings are coming — e.g. a program interview — and to
    plan work around them.

    Args:
        days_ahead: How many days ahead to look (1–30). Default 7.

    Returns:
        {"status": "success", "events": [{summary, start, end, attendees, meet_link}]}
        or {"error": true, "message": ...} when Calendar is not connected.
    """
    return await calendar_adapter.list_upcoming(days_ahead=days_ahead)


async def check_availability(tool_context: ToolContext, days_ahead: int = 7) -> dict:
    """Check when the founder is busy over the next `days_ahead` days.

    Use to propose meeting times or plan drafting work around real
    availability. Returns BUSY blocks only; the free gaps are everything else
    in the window. Only the founder's own calendar is visible — external
    contacts' availability cannot be checked; propose times and let them
    accept or counter.

    Args:
        days_ahead: How many days ahead to check (1–30). Default 7.

    Returns:
        {"status": "success", "busy": [{start, end}], "window": {...}}
        or {"error": true, "message": ...} when Calendar is not connected.
    """
    return await calendar_adapter.check_availability(days_ahead=days_ahead)


async def book_meeting(tool_context: ToolContext, summary: str, start_iso: str,
                       end_iso: str, attendees: list[str],
                       application_id: str = "") -> dict:
    """Book a meeting on the founder's calendar and email invites — approval-gated.

    Use after agreeing a time with the founder (check_availability first).
    Creates the event with a Google Meet link and emails every attendee. The
    founder must approve in the approval banner first — without a granted
    approval this returns needs_approval and files the request; never retry
    to bypass the gate.

    Args:
        summary: Event title (e.g. "Madica intro call — Ruhu").
        start_iso: Start, ISO-8601 with timezone (e.g. 2026-08-25T14:00:00+01:00).
        end_iso: End, ISO-8601 with timezone; must be after start.
        attendees: Recipient emails — external contacts plus the founder.
        application_id: The application this meeting relates to, if any.

    Returns:
        {"status": "success", "event_id", "meet_link"} after a granted
        approval, or {"status": "needs_approval", ...} while pending.
    """
    session = getattr(tool_context, "session", None)
    return await calendar_adapter.create_event(
        summary=summary, start_iso=start_iso, end_iso=end_iso,
        attendees=attendees, application_id=application_id,
        founder_id=tool_context.state.get("user:profile_id", "founder"),
        session_id=(getattr(session, "id", "")
                    or getattr(session, "session_id", "")))
