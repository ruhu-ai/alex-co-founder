"""Fail-closed preflight for the bounded two-account hiring demonstration.

It reads local configuration and provider metadata only.  It does not create a
role, read Gmail, create a Calendar event, send mail, or modify configuration.
The output deliberately contains only booleans and check names, never OAuth
tokens, Google subjects, or account email addresses.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import google_oauth  # noqa: E402
from services.internal_controlled_demo import configured_accounts, policy  # noqa: E402

PDF = ROOT / "output" / "pdf" / "amira-okafor-fde-demo-application.pdf"
FIXTURE_ID = "fixture_ruhu_fde_walkthrough"
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
CALENDAR_WRITE = "https://www.googleapis.com/auth/calendar.events"


def _configured_hash(key: str) -> str:
    return os.environ.get(key, "").strip()


def _pdf_hash() -> str:
    if not PDF.is_file():
        return ""
    return "sha256:" + hashlib.sha256(PDF.read_bytes()).hexdigest()


def main() -> int:
    load_dotenv(ROOT / ".env")
    expected = configured_accounts()
    alex_scopes = google_oauth.granted_scopes("alex")
    founder_scopes = google_oauth.granted_scopes("founder")
    actual_alex = google_oauth.account_subject_hash("alex")
    actual_founder = google_oauth.account_subject_hash("founder")
    checks = {
        "synthetic_fixture_enabled": (
            os.environ.get("HIRING_ENABLE_SYNTHETIC_DEMO") == "1"
            and FIXTURE_ID in {item.strip() for item in os.environ.get(
                "HIRING_SYNTHETIC_FIXTURE_IDS", "").split(",")}),
        "internal_demo_explicitly_enabled": (
            os.environ.get("HIRING_ENABLE_INTERNAL_CONTROLLED_DEMO") == "1"),
        "closed_internal_demo_policy": (
            policy().get("status") == "INTERNAL_DEMO_ONLY"
            and policy().get("activation", {}).get("real_candidate_processing") is False
            and policy().get("activation", {}).get("gmail_watch") is False),
        "account_pins_configured": expected is not None,
        "alex_subject_matches_pin": bool(expected and actual_alex == expected["alex_subject_hash"]),
        "founder_subject_matches_pin": bool(
            expected and actual_founder == expected["founder_subject_hash"]),
        "alex_gmail_read_scope": GMAIL_READ in alex_scopes,
        "alex_gmail_send_scope": GMAIL_SEND in alex_scopes,
        "alex_calendar_write_scope": CALENDAR_WRITE in alex_scopes,
        "founder_calendar_write_scope": CALENDAR_WRITE in founder_scopes,
        "fixture_pdf_hash_matches_pin": bool(
            _pdf_hash() and _pdf_hash() == _configured_hash(
                "HIRING_INTERNAL_DEMO_APPLICATION_PDF_SHA256")),
    }
    print(json.dumps({"status": "ready" if all(checks.values()) else "not_ready",
                      "checks": checks}, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
