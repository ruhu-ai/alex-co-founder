"""Log scrubber (docs/12): "secrets never in state or logs".

A ``logging.Filter`` that replaces any registered secret value with ``***``
before a record is emitted. Secret values are registered as they are fetched
(services/secrets.get / secrets.put) and as portal credentials are read
(services/portal_accounts). The filter is installed on the root logger and its
handlers on first registration.

Cheap and fail-safe: registration is O(1), filtering is skipped when nothing is
registered, and every step swallows its own errors — logging must never raise.
"""

from __future__ import annotations

import logging

_MIN_LEN = 6  # never scrub trivially short values (would mangle ordinary text)
_secrets: set[str] = set()
_installed = False


class SecretScrubber(logging.Filter):
    """Redact any registered secret substring in the formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not _secrets:
            return True
        try:
            message = record.getMessage()
            redacted = message
            for value in _secrets:
                if value and value in redacted:
                    redacted = redacted.replace(value, "***")
            if redacted != message:
                record.msg = redacted
                record.args = ()
        except Exception:
            pass  # a scrubber bug must never suppress or crash logging
        return True


_filter = SecretScrubber()


def _install() -> None:
    global _installed
    if _installed:
        return
    try:
        root = logging.getLogger()
        root.addFilter(_filter)
        for handler in root.handlers:
            handler.addFilter(_filter)
        _installed = True
    except Exception:
        pass


def register_secret(value: str | None) -> None:
    """Register a secret value so it is scrubbed from all subsequent logs."""
    try:
        if value and len(value) >= _MIN_LEN:
            _secrets.add(value)
        _install()
    except Exception:
        pass


def install() -> None:
    """Ensure the scrubber is attached to the root logger and its handlers.

    Idempotent for the logger; re-attaches to any handlers added since the last
    call so handlers configured after startup are still covered."""
    global _installed
    _installed = False
    _install()
