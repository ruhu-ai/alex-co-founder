"""Activity and waiting — vocabulary and view types (docs/24).

Two regimes share one vocabulary: attention-time (a turn the founder is
watching) and absence-time (durable waits while nobody is there). This module
owns the closed, server-side maps for both. Models never author status text,
verbs, state, or ordering (docs/24 §3 invariant 7).

Nothing here does I/O. The readers that populate WaitView live in
``services/waiting.py`` behind one adapter seam, so 21's ``waits`` can replace
them without changing a component, a string, or a test above the seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA_VERSION = 1

MAX_TITLE = 120
MAX_PROMISE = 160
MAX_OBJECT = 80
MAX_STEPS = 24
MAX_WAITS = 8
MAX_SINCE_DAYS = 30
ELAPSED_THRESHOLD_SECONDS = 5


# ---------------------------------------------------------------------------
# Closed enums (docs/24 §5)
# ---------------------------------------------------------------------------

class BlockedOn:
    FOUNDER = "founder"
    WORLD = "world"
    TIMER = "timer"


class Urgency:
    NONE = "none"
    SOON = "soon"
    CRITICAL = "critical"
    EXPIRED = "expired"


class StepState:
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class WaitKind:
    FOUNDER_FEEDBACK = "founder_feedback"
    FOUNDER_APPROVAL = "founder_approval"
    PORTAL_CONFIRMATION = "portal_confirmation"
    DEADLINE_TICK = "deadline_tick"
    DISCOVERY_RUNNING = "discovery_running"
    ACTION_UNCERTAIN = "action_uncertain"


# Every kind, and which reader is allowed to emit it. A kind with no reader or
# a reader with no kind is a failing test (docs/24 §12).
WAIT_READERS: dict[str, str] = {
    WaitKind.FOUNDER_FEEDBACK: "pending_signals",
    WaitKind.FOUNDER_APPROVAL: "approval",
    WaitKind.PORTAL_CONFIRMATION: "pending_signals",
    WaitKind.DEADLINE_TICK: "followup",
    WaitKind.DISCOVERY_RUNNING: "discovery_receipt",
    WaitKind.ACTION_UNCERTAIN: "browser_action",
}

BLOCKED_ON: dict[str, str] = {
    WaitKind.FOUNDER_FEEDBACK: BlockedOn.FOUNDER,
    WaitKind.FOUNDER_APPROVAL: BlockedOn.FOUNDER,
    WaitKind.PORTAL_CONFIRMATION: BlockedOn.WORLD,
    WaitKind.DEADLINE_TICK: BlockedOn.TIMER,
    WaitKind.DISCOVERY_RUNNING: BlockedOn.WORLD,
    WaitKind.ACTION_UNCERTAIN: BlockedOn.FOUNDER,
}

# Blocked-on-you always sorts first (docs/24 §3 invariant 3).
_BLOCKED_RANK = {BlockedOn.FOUNDER: 0, BlockedOn.WORLD: 1, BlockedOn.TIMER: 2}
_URGENCY_RANK = {Urgency.EXPIRED: 0, Urgency.CRITICAL: 1,
                 Urgency.SOON: 2, Urgency.NONE: 3}


@dataclass(frozen=True)
class WaitView:
    """One durable dormancy, rendered honestly. See docs/24 §5.1."""

    wait_kind: str
    blocked_on: str
    title: str
    since: str
    next_check: str | None
    next_check_action: str
    urgency: str
    focus: dict[str, str] = field(default_factory=dict)
    source: str = ""          # provenance; never rendered

    def as_dict(self) -> dict[str, Any]:
        return {
            "wait_kind": self.wait_kind,
            "blocked_on": self.blocked_on,
            "title": self.title[:MAX_TITLE],
            "since": self.since,
            "next_check": self.next_check,
            "next_check_action": self.next_check_action[:MAX_PROMISE],
            "urgency": self.urgency,
            "focus": self.focus,
        }


@dataclass(frozen=True)
class TraceStep:
    """One unit of work inside a turn the founder is watching."""

    verb: str
    object: str = ""
    state: str = StepState.DONE
    started_at: str = ""
    ended_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"verb": self.verb, "object": self.object[:MAX_OBJECT],
                "state": self.state, "started_at": self.started_at,
                "ended_at": self.ended_at}


# ---------------------------------------------------------------------------
# Step vocabulary (docs/24 §6.1). Closed, founder-voice, tense by persistence:
# these rows survive the turn as a receipt, so finished steps read past tense.
# ---------------------------------------------------------------------------

# signal -> (running, completed, takes_object)
_STEPS: dict[str, tuple[str, str, bool]] = {
    "agent:scout_agent": ("Looking for programmes", "Looked for programmes", False),
    "agent:matchmaker_agent": ("Scoring the fit against your profile",
                               "Scored the fit", False),
    "agent:interviewer_agent": ("Working out what's still missing",
                                "Worked out what's missing", False),
    "agent:drafter_agent": ("Drafting", "Drafted", False),
    "agent:form_filler_agent": ("Filling in the form", "Filled in the form", False),
    "tool:search_programs": ("Searching the web", "Searched the web", False),
    "tool:fetch_source": ("Reading", "Read", True),
    "tool:save_opportunity": ("Saving a programme", "Saved a programme", False),
    "tool:save_draft_section": ("Writing", "Wrote", True),
    "tool:complete_drafting": ("Checking the draft against your evidence",
                               "Checked the draft", False),
    "tool:produce_document": ("Building your application pack",
                              "Built your application pack", False),
    "tool:open_page": ("Browsing", "Browsed", True),
    "tool:browser_action": ("Browsing", "Browsed", True),
    "tool:read_page": ("Reading the page", "Read the page", False),
    "tool:request_approval": ("Asking for your approval",
                              "Asked for your approval", False),
    "tool:choose_opportunity": ("Starting your application",
                                "Started your application", False),
    "tool:record_feedback": ("Recording your feedback",
                             "Recorded your feedback", False),
    "tool:submit_form": ("Submitting", "Submitted", False),
}

THINKING_VERB = "Thinking"
GENERIC_RUNNING = "Working"
GENERIC_DONE = "Did some work"

# Argument names, in preference order, that may supply a step's object. The
# object is DATA (a host, a section key) and is rendered as text, never markup.
_OBJECT_ARGS = ("url", "source_url", "section_key", "title", "query", "name")


def _host(value: str) -> str:
    """Bare host for display. Never a full URL: no credentials, no query."""
    text = value.strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in text:                       # strip any userinfo
        text = text.rsplit("@", 1)[1]
    return text.split(":", 1)[0][:MAX_OBJECT]


def step_object(args: dict[str, Any] | None) -> str:
    """Derive a bounded, safe display object from trusted tool arguments."""
    if not isinstance(args, dict):
        return ""
    for key in _OBJECT_ARGS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            if key in ("url", "source_url"):
                return _host(value)
            return " ".join(value.split())[:MAX_OBJECT]
    return ""


def describe_step(signal: str, *, running: bool,
                  args: dict[str, Any] | None = None) -> TraceStep:
    """Map a trusted signal to founder-voice copy.

    An unmapped signal degrades to a generic verb; a tool or agent name never
    reaches the founder (docs/24 §6.1, §12).
    """
    entry = _STEPS.get(signal)
    if entry is None:
        return TraceStep(verb=GENERIC_RUNNING if running else GENERIC_DONE,
                         state=StepState.RUNNING if running else StepState.DONE)
    run_text, done_text, takes_object = entry
    return TraceStep(
        verb=run_text if running else done_text,
        object=step_object(args) if takes_object else "",
        state=StepState.RUNNING if running else StepState.DONE,
    )


# ---------------------------------------------------------------------------
# Wait copy (docs/24 §6.2). Two halves: what is awaited, and the next check.
# ---------------------------------------------------------------------------

_WAIT_TITLES: dict[str, str] = {
    WaitKind.FOUNDER_FEEDBACK: "Needs your review",
    WaitKind.FOUNDER_APPROVAL: "Needs you before I can submit",
    WaitKind.PORTAL_CONFIRMATION: "Waiting for the portal to confirm",
    WaitKind.DEADLINE_TICK: "Following up after the decision date",
    WaitKind.DISCOVERY_RUNNING: "Searching for programmes",
    WaitKind.ACTION_UNCERTAIN: "Not sure the submission went through",
}

# Said once, where it is true: an open wait holds no worker (docs/24 §3).
NOTHING_RUNNING = "nothing is running"
NO_DATE_FALLBACK = "I'll tell you as soon as I hear"


def wait_title(kind: str) -> str:
    return _WAIT_TITLES.get(kind, "Waiting")[:MAX_TITLE]


MAX_SUBJECT = 60


def next_check_action(kind: str, next_check: str | None, *,
                      subject: str = "") -> str:
    """The founder-visible promise. Never invents a date (invariant 2).

    Self-bounding: the subject is caller data (a programme name), so it is
    clipped here rather than relying on the view to do it.
    """
    subject = " ".join((subject or "").split())[:MAX_SUBJECT]
    return _clip_promise(_next_check_action(kind, next_check, subject,
                                            _human_date(next_check)))


def _next_check_action(kind: str, next_check: str | None, subject: str,
                       when: str) -> str:
    if kind == WaitKind.FOUNDER_APPROVAL:
        if not when:
            return "waiting on you"
        return f"approval expires {when}, then I'll need a fresh fill"
    if kind == WaitKind.FOUNDER_FEEDBACK:
        return "nothing moves until you look"
    if kind == WaitKind.PORTAL_CONFIRMATION:
        if not when:
            return f"{NOTHING_RUNNING} · {NO_DATE_FALLBACK}"
        return f"{NOTHING_RUNNING} · if there's no word by {when} I'll chase it"
    if kind == WaitKind.DEADLINE_TICK:
        who = subject or "the programme"
        if not when:
            return f"{NOTHING_RUNNING} · {NO_DATE_FALLBACK}"
        return (f"{who} decides {when} · I'll check then and tell you "
                "either way")
    if kind == WaitKind.DISCOVERY_RUNNING:
        return "running in the background · closing this chat is safe"
    if kind == WaitKind.ACTION_UNCERTAIN:
        return ("I'm checking with them before trying again — "
                "I won't resubmit blind")
    return NO_DATE_FALLBACK


def _clip_promise(text: str) -> str:
    return text[:MAX_PROMISE]


# ---------------------------------------------------------------------------
# Time helpers. Absence-time uses absolute time and expectation, never a
# running counter (docs/24 §3 invariant 4).
# ---------------------------------------------------------------------------

def _parse(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _human_date(value: str | None, *, now: datetime | None = None) -> str:
    """A date a founder reads, not a timestamp. Empty when underivable."""
    moment = _parse(value)
    if moment is None:
        return ""
    now = now or datetime.now(timezone.utc)
    delta = moment - now
    hours = delta.total_seconds() / 3600
    if delta.total_seconds() < 0:
        return "already"
    if hours < 1:
        minutes = max(1, int(delta.total_seconds() // 60))
        return f"in {minutes} minute{'s' if minutes != 1 else ''}"
    if hours < 24:
        whole = int(hours)
        return f"in {whole} hour{'s' if whole != 1 else ''}"
    days = int(hours // 24)
    if days <= 6:
        return moment.strftime("%A")            # "Friday"
    return moment.strftime("%-d %b")            # "5 Sep"


def urgency_for(next_check: str | None, *,
                now: datetime | None = None) -> str:
    """Urgency from the real expiry, never from a guess."""
    moment = _parse(next_check)
    if moment is None:
        return Urgency.NONE
    now = now or datetime.now(timezone.utc)
    remaining = (moment - now).total_seconds()
    if remaining <= 0:
        return Urgency.EXPIRED
    if remaining <= 6 * 3600:
        return Urgency.CRITICAL
    if remaining <= 3 * 24 * 3600:
        return Urgency.SOON
    return Urgency.NONE


def clamp_since(value: str | None, *, now: datetime | None = None) -> datetime:
    """Bound the digest window: future -> now, older than 30 days -> 30 days."""
    now = now or datetime.now(timezone.utc)
    floor = now - timedelta(days=MAX_SINCE_DAYS)
    parsed = _parse(value)
    if parsed is None:
        return floor
    return max(floor, min(parsed, now))


def sort_waits(waits: list[WaitView]) -> list[WaitView]:
    """Blocked-on-you first, then by urgency, then oldest first."""
    return sorted(waits, key=lambda w: (
        _BLOCKED_RANK.get(w.blocked_on, 9),
        _URGENCY_RANK.get(w.urgency, 9),
        w.since or "",
    ))


def build_wait(kind: str, *, since: str, next_check: str | None = None,
               subject: str = "", focus: dict[str, str] | None = None,
               source: str = "", now: datetime | None = None) -> WaitView:
    """Assemble one WaitView with all copy and urgency derived in code."""
    return WaitView(
        wait_kind=kind,
        blocked_on=BLOCKED_ON.get(kind, BlockedOn.WORLD),
        title=wait_title(kind),
        since=since,
        next_check=next_check,
        next_check_action=next_check_action(kind, next_check, subject=subject),
        urgency=urgency_for(next_check, now=now),
        focus=focus or {},
        source=source or WAIT_READERS.get(kind, ""),
    )


# ---------------------------------------------------------------------------
# Turn trace (docs/24 §7.2). Collected inside the existing /wake event loop
# from trusted ADK signals; models never author any of it. A trace failure is
# swallowed by the caller and the turn returns normally (invariant 11).
# ---------------------------------------------------------------------------

class TraceCollector:
    """Accumulates founder-voice steps from ADK events during one turn.

    Deliberately forgiving: any event shape it does not recognise is ignored
    rather than raising, because narration must never be able to fail a turn.
    """

    def __init__(self, root_agent_name: str = "") -> None:
        self._steps: list[TraceStep] = []
        self._root = root_agent_name
        self._seen_agents: set[str] = set()

    def observe(self, event: Any) -> None:
        try:
            self._observe(event)
        except Exception:  # noqa: BLE001 — narration never fails a turn
            pass

    def _observe(self, event: Any) -> None:
        if len(self._steps) >= MAX_STEPS:
            return
        author = str(getattr(event, "author", "") or "")
        # A sub-agent taking over is a step the founder understands.
        if (author and author != self._root and author != "user"
                and author not in self._seen_agents):
            self._seen_agents.add(author)
            step = describe_step(f"agent:{author}", running=False)
            if step.verb != GENERIC_DONE:      # unmapped agents stay silent
                self._append(step)
        content = getattr(event, "content", None)
        for part in (getattr(content, "parts", None) or []):
            call = getattr(part, "function_call", None)
            if call is None:
                continue
            name = str(getattr(call, "name", "") or "")
            if not name:
                continue
            args = getattr(call, "args", None)
            self._append(describe_step(f"tool:{name}", running=False,
                                       args=dict(args) if args else None))

    def _append(self, step: TraceStep) -> None:
        # Collapse an immediate repeat: three fetches of one host read as one
        # line, not three identical rows.
        if self._steps:
            last = self._steps[-1]
            if last.verb == step.verb and last.object == step.object:
                return
        if len(self._steps) < MAX_STEPS:
            self._steps.append(step)

    def as_payload(self) -> list[dict[str, Any]]:
        return [step.as_dict() for step in self._steps]

    @property
    def truncated(self) -> bool:
        return len(self._steps) >= MAX_STEPS
