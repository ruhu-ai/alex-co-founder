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


def _scrub(text: str) -> str:
    for value in _secrets:
        if value and value in text:
            text = text.replace(value, "***")
    return text


class SecretScrubber(logging.Filter):
    """Redact any registered secret value from a record — the formatted message
    AND the exception traceback / stack text.

    Scrubbing only ``getMessage()`` leaves the biggest leak vector open: a
    secret passed as an exception argument (``raise RuntimeError(token)``) or
    captured in a frame is rendered into the traceback by the formatter, not the
    message. Pre-formatting and scrubbing ``exc_text`` here, before any handler
    formats the record, closes that path.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not _secrets:
            return True
        try:
            message = record.getMessage()
            redacted = _scrub(message)
            if redacted != message:
                record.msg = redacted
                record.args = ()
            if record.exc_info and not record.exc_text:
                # Materialize the traceback now so we scrub it once; every
                # handler's formatter then reuses this cached, redacted text.
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = _scrub(record.exc_text)
            if record.stack_info:
                record.stack_info = _scrub(record.stack_info)
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
