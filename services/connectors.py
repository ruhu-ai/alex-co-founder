"""Founder-visible connector descriptors (docs/24 §5).

This catalogue is presentation data over the closed product registry.  It does
not validate credentials, call providers, or dynamically enable connectors.
Durable status is supplied by ``ConnectionRegistry`` at request time.
"""

from __future__ import annotations

from typing import Any

DESCRIPTORS: list[dict[str, Any]] = [
    {
        "name": "browser", "title": "Browser", "icon": "◍", "brand": "#6b7280",
        "blurb": "Navigates application portals and pre-fills forms — submission is approval-gated.",
        "auth": "builtin", "aliases": ("playwright", "portal", "form"),
    },
    {
        "name": "drive", "title": "Google Drive", "icon": "▲", "brand": "#1a73e8",
        "blurb": "Company records — decks, plans, models. Founder-selected files only.",
        "auth": "google_oauth", "aliases": ("docs", "sheets", "files"),
    },
    {
        "name": "founder_gmail", "title": "Gmail", "icon": "✉", "brand": "#ea4335",
        "blurb": "Portal mail — confirmations, requests, results. One watched label, read-only.",
        "auth": "google_oauth", "aliases": ("email", "mail"),
    },
    {
        "name": "calendar", "title": "Google Calendar", "icon": "◷", "brand": "#188038",
        "blurb": "Free/busy and upcoming meetings — plans work around your real availability.",
        "auth": "google_oauth", "aliases": ("meetings", "schedule", "gcal"),
    },
    {
        "name": "github", "title": "GitHub", "icon": "◆", "brand": "#1f2328",
        "blurb": "Repository work is outside the current funding workflow.",
        "auth": "unavailable", "available": False,
        "aliases": ("repo", "issues", "prs"),
        "soon_note": "Not available in this product scope.",
    },
    {
        "name": "slack", "title": "Slack", "icon": "#", "brand": "#611f69",
        "blurb": "Two-way messaging — chat with Alex from your workspace.",
        "auth": "oauth", "available": False, "aliases": ("messaging", "chat"),
        "soon_note": "Chat surface for Alex — roadmap (adr/002 meeting-presence ladder).",
    },
    {
        "name": "telegram", "title": "Telegram", "icon": "✈", "brand": "#229ed9",
        "blurb": "Two-way messaging with a Telegram bot.",
        "auth": "bot_token", "available": False, "aliases": ("messaging", "chat"),
        "soon_note": "Chat surface for Alex — roadmap, behind Slack.",
    },
    {
        "name": "imap", "title": "Email (IMAP)", "icon": "✉", "brand": "#6b7280",
        "blurb": "Read and search mail from any IMAP account.",
        "auth": "token", "available": False, "aliases": ("email", "mail"),
        "soon_note": "Superseded by the alex@ruhu.ai role mailbox plan (adr/001) unless a non-Google mailbox is needed.",
    },
    {
        "name": "alex_mail", "title": "Alex's Mailbox", "icon": "A", "brand": "#7c5cd6",
        "blurb": "alex@ruhu.ai — Alex's own inbox: program replies land here and wake the pipeline; sending is approval-gated.",
        "auth": "google_oauth", "aliases": ("email", "alex", "mailbox"),
    },
    {
        "name": "alex_calendar", "title": "Alex's Calendar", "icon": "A", "brand": "#0f9d58",
        "blurb": "alex@ruhu.ai — the role calendar for exact, founder-approved interview invitations and receipts.",
        "auth": "google_oauth", "aliases": ("calendar", "alex", "interviews", "scheduling"),
    },
    {
        "name": "jira", "title": "Jira", "icon": "▣", "brand": "#0052cc",
        "blurb": "Search, summarize, create, and update issues.",
        "auth": "token", "available": False, "aliases": ("tickets", "issues"),
        "soon_note": "Hiring/ops workflows — future workflow definitions (brief §6.5).",
    },
]


def catalog(status_by_connector: dict[str, dict[str, Any]] | None = None
            ) -> list[dict[str, Any]]:
    """Descriptors plus a caller-supplied durable status projection."""
    status_by_connector = status_by_connector or {}
    out = []
    for d in DESCRIPTORS:
        c = {k: v for k, v in d.items() if k in (
            "name", "title", "icon", "brand", "blurb", "auth", "aliases",
            "fields", "instructions", "soon_note")}
        c["available"] = d.get("available", True)
        status = status_by_connector.get(d["name"], {})
        if d["auth"] == "builtin":
            c["connected"], c["status_line"] = True, "Built in"
        else:
            c["connected"] = status.get("status") in {"CONNECTED", "DEGRADED"}
            c["status_line"] = status.get("status_line", "")
        c["connection"] = status or None
        out.append(c)
    return out
