"""Deployment-owned identity configuration for the H4S provider sandbox.

Normal product OAuth connections are intentionally absent from this module.
H4S may obtain a credential only through a named secret for a separately
controlled test identity whose stable Google subject is pinned by hash.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

from services.hiring_contracts import stable_id

_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class H4STestConnector:
    """Non-secret reference to one deployment-owned test connector."""

    provider_kind: str
    refresh_token_secret: str
    provider_account_subject_hash: str
    required_scopes: frozenset[str]

    @property
    def connector_grant_id(self) -> str:
        """Derive an opaque id without exposing a token or account address."""
        return stable_id(
            "h4sgrant", self.provider_kind, self.refresh_token_secret,
            self.provider_account_subject_hash)


_CONFIG = {
    "GMAIL_TEST": (
        "HIRING_H4S_GMAIL_TEST_REFRESH_TOKEN_SECRET",
        "HIRING_H4S_GMAIL_TEST_ACCOUNT_SUBJECT_SHA256",
        frozenset({"https://www.googleapis.com/auth/gmail.readonly",
                   "https://www.googleapis.com/auth/gmail.send"}),
    ),
    "CALENDAR_TEST": (
        "HIRING_H4S_CALENDAR_TEST_REFRESH_TOKEN_SECRET",
        "HIRING_H4S_CALENDAR_TEST_ACCOUNT_SUBJECT_SHA256",
        frozenset({"https://www.googleapis.com/auth/calendar.readonly",
                   "https://www.googleapis.com/auth/calendar.events"}),
    ),
}

_DESTINATIONS = {
    "TEST_CANDIDATE": "HIRING_H4S_TEST_CANDIDATE_ADDRESS",
    "TEST_FOUNDER": "HIRING_H4S_TEST_FOUNDER_ADDRESS",
    "TEST_CALENDAR_ATTENDEE": "HIRING_H4S_TEST_CALENDAR_ATTENDEE_ADDRESS",
}


def configured_test_connector(provider_kind: str) -> H4STestConnector | None:
    """Return a complete test-only connector definition, or ``None``.

    This deliberately does not fall back to GOOGLE_OAUTH_REFRESH_TOKEN,
    ALEX_OAUTH_REFRESH_TOKEN, a normal connection projection, or a client
    parameter. Missing deployment configuration is a safe disabled state.
    """
    fields = _CONFIG.get(provider_kind)
    if not fields:
        return None
    secret_env, subject_env, scopes = fields
    secret_name = os.environ.get(secret_env, "").strip()
    subject_hash = os.environ.get(subject_env, "").strip().casefold()
    if not secret_name or not _HASH.fullmatch(subject_hash):
        return None
    return H4STestConnector(
        provider_kind=provider_kind,
        refresh_token_secret=secret_name,
        provider_account_subject_hash=subject_hash,
        required_scopes=scopes,
    )


def subject_hash(subject: str) -> str:
    """Hash a provider subject using the persisted H4S representation."""
    return "sha256:" + hashlib.sha256(subject.encode()).hexdigest()


def configured_test_destination(destination_kind: str) -> str | None:
    """Return a deployment-owned H4S destination, never a client address."""
    name = _DESTINATIONS.get(destination_kind)
    if not name:
        return None
    value = os.environ.get(name, "").strip().casefold()
    if (not value or len(value) > 320 or value.count("@") != 1
            or any(char.isspace() for char in value)):
        return None
    local, domain = value.split("@", 1)
    return value if local and "." in domain else None
