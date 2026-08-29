"""Deterministic, addressed intent gate for Alex Live conversational hold.

These commands control only the Live attention state. They never authorize a
tool, workflow transition, approval, memory write, or external action.
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
    rf"){_TRAILING_POLITE}(?:\s+for\s+(?:a|one)\s+(?:moment|minute|second))?$"
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
    """Return HOLD/RESUME only for a short utterance directly addressing Alex."""
    normalized = _normalize(text)
    if not normalized or len(normalized) > 160:
        return None
    addressed = _ADDRESS.fullmatch(normalized)
    if not addressed:
        return None
    command = " ".join(addressed.group(1).split())
    if not command or "hold on to" in command:
        return None
    if _HOLD.fullmatch(command):
        return HOLD
    if _RESUME.fullmatch(command):
        return RESUME
    return None
