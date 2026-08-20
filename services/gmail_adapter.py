"""Gmail adapter (docs/07): read-only, label-scoped portal mail → follow-ups.

Grant portals communicate by email — submission confirmations, requests for
information, decisions. The adapter scans ONE founder-chosen label (default
"grants") for unread messages, classifies them with a deterministic keyword
pass, and returns events. Processed message ids persist in Firestore so
re-scans are idempotent (principle 4). The agent never sends mail — the scope
is gmail.readonly by construction.

The Google client is injectable so tests run without OAuth.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Callable

from services import firestore, google_oauth

_RULES = [
    ("result_positive", ("congratulations", "you've been selected", "you have been selected",
                         "we are pleased to", "awarded", "winner")),
    ("result_negative", ("unfortunately", "not selected", "regret to inform",
                         "not moving forward", "will not be")),
    ("confirmation", ("confirmation", "we received your application", "submission received",
                      "thank you for applying", "application received")),
    ("request", ("additional information", "clarification", "interview invitation",
                 "interview invite", "action required", "complete your application")),
]

_service_factory: Callable[[], Any] | None = None


def set_service_factory(fn: Callable[[], Any] | None) -> None:
    """Tests inject a fake Gmail service; prod builds from OAuth creds."""
    global _service_factory
    _service_factory = fn


def _service():
    if _service_factory is not None:
        return _service_factory()
    creds = google_oauth.get_credentials()
    if creds is None:
        return None
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def classify(subject: str, snippet: str) -> str:
    """Deterministic keyword classification — no model call."""
    text = f"{subject} {snippet}".lower()
    for kind, needles in _RULES:
        if any(n in text for n in needles):
            return kind
    return "update"


def _header(headers: list[dict], name: str) -> str:
    return next((h["value"] for h in headers if h.get("name", "").lower() == name), "")


def _body_text(payload: dict) -> str:
    """Best-effort plain text from a message payload (no HTML parsing)."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []):
        text = _body_text(part)
        if text:
            return text
    return ""


async def scan(label: str = "grants", max_results: int = 20) -> dict:
    """Unread mail under one label → classified events. Idempotent: message ids
    already in gmail_state/processed are skipped."""
    svc = _service()
    if svc is None:
        return {"status": "error", "error": True,
                "message": "Google OAuth not configured (run scripts/oauth_setup.py)"}
    try:
        resp = svc.users().messages().list(
            userId="me", q=f"label:{label} is:unread", maxResults=max_results).execute()
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"gmail list failed: {exc}"}

    processed = set(await firestore.get_processed_gmail_ids())
    events = []
    for stub in resp.get("messages", []):
        if stub["id"] in processed:
            continue
        try:
            msg = svc.users().messages().get(
                userId="me", id=stub["id"], format="full").execute()
        except Exception:
            continue  # one unreadable message never fails the scan
        headers = msg.get("payload", {}).get("headers", [])
        subject = _header(headers, "subject")
        sender = _header(headers, "from")
        body = _body_text(msg.get("payload", {}))[:2000] or msg.get("snippet", "")
        events.append({
            "id": stub["id"],
            "from": sender,
            "subject": subject,
            "kind": classify(subject, body),
            "excerpt": re.sub(r"\s+", " ", body)[:280],
        })
    if events:
        await firestore.add_processed_gmail_ids([e["id"] for e in events])
    return {"status": "success", "events": events, "scanned": len(resp.get("messages", []))}
