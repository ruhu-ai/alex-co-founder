"""Deterministic intent gate for Alex Live conversational hold.

These commands control only the Live attention state. They never authorize a
tool, workflow transition, approval, memory write, or external action.

Hold accepts either an addressed command ("Alex, hold on") or a short direct
imperative in an active conversation ("Just hold on, I'll get back to you").
Resume remains addressed-only because background speech is intentionally kept
on-device while held and must never wake Alex accidentally.
"""

from __future__ import annotations

import re
import unicodedata

HOLD = "hold"
RESUME = "resume"

_ADDRESS = re.compile(
    r"^(?:(?:hey|hi|hello|okay|ok|please)\s+){0,2}alex\b[\s,.:;!?-]*(.*)$")
_POLITE = r"(?:please\s+)?"
_TRAILING_POLITE = r"(?:\s+please)?"
_REQUEST = (
    r"(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:(?:actually|just)\s+)?"
    r"(?:(?:i\s+(?:need|want)\s+you\s+to)\s+)?"
)
_HOLD = re.compile(
    rf"^{_POLITE}{_REQUEST}{_POLITE}(?:"
    r"hold(?:\s+on)?|pause(?:\s+and\s+wait)?|"
    r"wait(?:\s+(?:(?:a|one)\s+)?(?:moment|minute|second|sec))?|"
    r"hang\s+on|stand\s+by|"
    r"give\s+me\s+(?:(?:a|one)\s+)?(?:moment|minute|second|sec)|"
    r"(?:do\s+not|don't)\s+(?:listen|respond)(?:\s+(?:yet|for\s+now))?"
    rf"){_TRAILING_POLITE}(?:"
    r"\s+for\s+(?:a|one)\s+(?:moment|minute|second)|"
    r"\s+i(?:'ll|\s+will)\s+get\s+back\s+to\s+you(?:\s+(?:then|later))?|"
    r"\s+until\s+(?:i|we)\s+(?:call|say|refer\s+to)\s+"
    r"(?:you|your\s+name)(?:\s+again)?"
    r")?$"
)
_RESUME = re.compile(
    rf"^{_POLITE}(?:"
    r"resume|resume\s+(?:listening|the\s+conversation)|"
    r"(?:you\s+can\s+)?(?:listen|respond|continue)(?:\s+(?:again|now))?|"
    r"start\s+listening(?:\s+again)?|"
    r"come\s+back|carry\s+on|"
    r"(?:i\s+am|i'm|we\s+are|we're)\s+ready(?:\s+now)?"
    rf"){_TRAILING_POLITE}$"
)


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    value = value.replace("’", "'")
    value = re.sub(r"[^a-z0-9'\s]+", " ", value)
    return " ".join(value.split())


def classify_addressed_attention_intent(text: str) -> str | None:
    """Return bounded HOLD, or addressed-only RESUME, for one short utterance."""
    normalized = _normalize(text)
    if not normalized or len(normalized) > 160:
        return None
    addressed = _ADDRESS.fullmatch(normalized)
    command = " ".join(
        (addressed.group(1) if addressed else normalized).split())
    if not command or "hold on to" in command:
        return None
    if _HOLD.fullmatch(command):
        return HOLD
    if addressed and _RESUME.fullmatch(command):
        return RESUME
    return None
