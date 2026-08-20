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
