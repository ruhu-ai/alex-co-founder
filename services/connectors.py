"""Connector registry (pattern from andrewyng/openworker connectors/descriptors.py).

Adding a connector is DATA, not UI code: a descriptor declares its auth
method, the fields the user pastes, step-by-step instructions, and (for token
connectors) a validate that confirms the credential with a real API call and
returns the identity to show back. The Connections panel renders from
GET /api/connectors and never hardcodes a connector.

Auth modes: "builtin" (no credential), "google_oauth" (per-connector
incremental scopes, services/google_oauth.py), "token" (manual paste, stored
in .env via google_oauth.save_env_var — prod: Secret Manager, docs/12).
available=False renders as "Soon" — roadmap connectors (brief §6.5, adr/002).
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from typing import Any

from services import google_oauth

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
        "name": "gmail", "title": "Gmail", "icon": "✉", "brand": "#ea4335",
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
        "blurb": "Repositories, issues, and CI — credential validated and stored now; tools land with the dev workflow.",
        "auth": "token", "aliases": ("repo", "issues", "prs"),
        "fields": [{"key": "token", "label": "Personal access token", "secret": True,
                    "placeholder": "github_pat_…",
                    "help": "Fine-grained PAT, read-only repository permissions."}],
        "instructions": [
            "GitHub → Settings → Developer settings → Personal access tokens → Fine-grained",
            "Grant read-only access to the repositories Alex may see",
            "Paste the token — it is validated against the API, then stored as a secret",
        ],
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
        "name": "jira", "title": "Jira", "icon": "▣", "brand": "#0052cc",
        "blurb": "Search, summarize, create, and update issues.",
        "auth": "token", "available": False, "aliases": ("tickets", "issues"),
        "soon_note": "Hiring/ops workflows — future workflow definitions (brief §6.5).",
    },
]


def _github_identity() -> str:
    return os.environ.get("GITHUB_LOGIN", "")


def catalog() -> list[dict[str, Any]]:
    """Descriptors + live status, in display order. The panel renders this."""
    out = []
    for d in DESCRIPTORS:
        c = {k: v for k, v in d.items() if k in (
            "name", "title", "icon", "brand", "blurb", "auth", "aliases",
            "fields", "instructions", "soon_note")}
        c["available"] = d.get("available", True)
        if d["auth"] == "builtin":
            c["connected"], c["status_line"] = True, "Built in"
        elif d["auth"] == "google_oauth":
            c["connected"] = google_oauth.configured(d["name"])
            c["status_line"] = "Connected" if c["connected"] else ""
        elif d["name"] == "github":
            c["connected"] = bool(os.environ.get("GITHUB_TOKEN"))
            c["status_line"] = (_github_identity() or "Connected") if c["connected"] else ""
        else:
            c["connected"], c["status_line"] = False, ""
        out.append(c)
    return out


def _github_validate(token: str) -> dict:
    """Confirm the token with a real API call; return the login to show back."""
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "co-founder",
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"ok": True, "identity": json.loads(resp.read()).get("login", "")}
    except Exception as exc:
        return {"ok": False, "error": f"github validation failed: {exc}"}


async def github_connect(token: str) -> dict:
    """Validate a GitHub PAT and store it as a secret (.env local / Secret
    Manager prod). Errors as data (principle 2)."""
    token = token.strip()
    if not token:
        return {"status": "error", "error": True, "message": "token is empty"}
    result = await asyncio.to_thread(_github_validate, token)
    if not result["ok"]:
        return {"status": "error", "error": True, "message": result["error"]}
    google_oauth.save_env_var("GITHUB_TOKEN", token)
    google_oauth.save_env_var("GITHUB_LOGIN", result["identity"])
    return {"status": "success", "login": result["identity"]}


async def github_disconnect() -> dict:
    os.environ.pop("GITHUB_TOKEN", None)
    os.environ.pop("GITHUB_LOGIN", None)
    google_oauth.save_env_var("GITHUB_TOKEN", "")
    google_oauth.save_env_var("GITHUB_LOGIN", "")
    return {"status": "success"}
