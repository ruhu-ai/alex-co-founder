"""The waiting adapter (docs/24 §4) — the seam.

Four readers turn records that exist today into ``WaitView``s. Everything
above this module — component, copy, ordering, tests — is written against
``WaitView`` and never against ``pending_signals``, so when 21 Phase 1B lands
its ``waits`` collection replaces these readers and nothing above the seam
changes.

Two rules make that safe:

* every reader is independently failable — one raising degrades only itself
  and sets ``partial``, because a broken reader must not blank the digest;
* nothing here is stored. The result is a pure function of durable records
  plus a caller-supplied ``since``, so it is identical after a cold start
  (docs/24 §3 invariant 9).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from services import activity as act
from services.activity import WaitKind, WaitView

logger = logging.getLogger(__name__)

# Session-state keys read by the pending_signals reader. Imported lazily by
# name so this module never depends on the agent package.
_K_PENDING_SIGNALS = "pending_signals"
_K_ACTIVE_APPLICATION_ID = "active_application_id"

# pending_signals value -> wait kind. Signals with a dedicated reader (an
# approval carries a real expiry) are deliberately absent: the richer reader
# owns them, and a duplicate would render the same wait twice.
_SIGNAL_KINDS: dict[str, str] = {
    "founder_feedback": WaitKind.FOUNDER_FEEDBACK,
    "portal_confirmation": WaitKind.PORTAL_CONFIRMATION,
}

_session_reader: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None


def configure(*, session_state_reader=None) -> None:
    """Wire the server-side session-state reader (app/main.py startup)."""
    global _session_reader
    _session_reader = session_state_reader


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Readers. Each returns a list and raises nothing to the caller it can help.
# ---------------------------------------------------------------------------

async def _read_pending_signals(founder_id: str, session_id: str | None,
                                now: datetime) -> list[WaitView]:
    """What the conversation itself declared it is dormant on (docs/03).

    Bounded by design: signals live in session state, so this reader needs a
    session. Founder-wide callers get the other three readers, which is why
    they are separate.
    """
    if not session_id or _session_reader is None:
        return []
    state = await _session_reader(session_id)
    if not state:
        return []
    signals = state.get(_K_PENDING_SIGNALS) or []
    if not isinstance(signals, list):
        return []
    application_id = str(state.get(_K_ACTIVE_APPLICATION_ID) or "")
    out: list[WaitView] = []
    for signal in signals:
        kind = _SIGNAL_KINDS.get(str(signal))
        if kind is None:
            continue
        out.append(act.build_wait(
            kind,
            # Session state carries no opened-at stamp; the wait is real but
            # its start is unknown, so it is left empty rather than guessed.
            since="",
            next_check=None,
            focus={"kind": "application", "id": application_id}
            if application_id else {},
            source="pending_signals", now=now))
    return out


async def _read_approvals(founder_id: str, session_id: str | None,
                          now: datetime) -> list[WaitView]:
    """Blocked on the founder, with a real expiry."""
    from services import firestore

    rows = await firestore.list_pending_approvals(founder_id=founder_id,
                                                  session_id=session_id or "")
    out: list[WaitView] = []
    for row in rows[:act.MAX_WAITS]:
        out.append(act.build_wait(
            WaitKind.FOUNDER_APPROVAL,
            since=str(row.get("created_at") or ""),
            next_check=str(row.get("expires_at") or "") or None,
            focus={"kind": "approval", "id": str(row.get("id") or "")},
            source="approval", now=now))
    return out


async def _read_followups(founder_id: str, session_id: str | None,
                          now: datetime) -> list[WaitView]:
    """Scheduled checks with a due date — the timer waits."""
    from services import firestore

    applications = await firestore.list_inflight_applications(founder_id)
    out: list[WaitView] = []
    for app in applications:
        opportunity_name = ""
        opportunity_id = str(app.get("opportunity_id") or "")
        if opportunity_id:
            try:
                opportunity = await firestore.get_opportunity(opportunity_id)
                opportunity_name = str((opportunity or {}).get("name") or "")
            except Exception:  # noqa: BLE001 — a name is cosmetic
                opportunity_name = ""
        for followup in (app.get("followups") or []):
            if not isinstance(followup, dict):
                continue
            if str(followup.get("status") or "").upper() in ("DONE", "CANCELLED"):
                continue
            out.append(act.build_wait(
                WaitKind.DEADLINE_TICK,
                since=str(app.get("updated_at") or app.get("created_at") or ""),
                next_check=str(followup.get("due_at") or "") or None,
                subject=opportunity_name,
                focus={"kind": "application", "id": str(app.get("id") or "")},
                source="followup", now=now))
            if len(out) >= act.MAX_WAITS:
                return out
    return out


async def _read_discovery(founder_id: str, session_id: str | None,
                          now: datetime) -> list[WaitView]:
    """Background runs still in flight, from their durable receipts (docs/23)."""
    from services import firestore

    rows = await firestore.list_active_discovery_requests(founder_id)
    out: list[WaitView] = []
    for row in rows[:act.MAX_WAITS]:
        if session_id and str(row.get("origin_session_id") or "") != session_id:
            continue
        out.append(act.build_wait(
            WaitKind.DISCOVERY_RUNNING,
            since=str(row.get("created_at") or ""),
            next_check=None,
            focus={"kind": "discovery_request", "id": str(row.get("id") or "")},
            source="discovery_receipt", now=now))
    return out


_READERS: tuple[tuple[str, Callable], ...] = (
    ("pending_signals", _read_pending_signals),
    ("approval", _read_approvals),
    ("followup", _read_followups),
    ("discovery_receipt", _read_discovery),
)


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------

async def list_waits(founder_id: str, *, session_id: str | None = None,
                     now: datetime | None = None) -> dict[str, Any]:
    """Every open wait, ordered blocked-on-you first.

    Returns ``{"waits": [...], "partial": bool, "failed_readers": [...]}``.
    A reader that raises contributes nothing and sets ``partial``; the others
    still render (docs/24 §10).
    """
    now = now or _now()
    waits: list[WaitView] = []
    failed: list[str] = []
    for name, reader in _READERS:
        try:
            waits.extend(await reader(founder_id, session_id, now))
        except Exception:  # noqa: BLE001 — one reader never blanks the digest
            logger.exception("waiting reader failed: %s", name)
            failed.append(name)
    ordered = act.sort_waits(waits)[:act.MAX_WAITS]
    return {"waits": ordered, "partial": bool(failed), "failed_readers": failed}


async def changed_since(founder_id: str, since: datetime, *,
                        session_id: str | None = None,
                        limit: int = act.MAX_WAITS) -> list[dict[str, Any]]:
    """What finished while the founder was away.

    Read from the session-resource links that docs/23 already writes, so the
    digest needs no new writer and no new collection.
    """
    from services import firestore

    stamp = since.isoformat()
    try:
        if session_id:
            links = await firestore.list_session_links(
                founder_id, session_id, limit=limit * 4)
        else:
            links = await firestore.list_recent_resources(
                founder_id, limit=limit * 4)
    except Exception:  # noqa: BLE001 — errors are data at this seam
        logger.exception("changed_since read failed")
        return []

    out: list[dict[str, Any]] = []
    for row in links:
        at = str(row.get("occurred_at") or row.get("updated_at") or "")
        if not at or at <= stamp:
            continue
        out.append({
            "kind": str(row.get("resource_type") or ""),
            "title": str(row.get("title_snapshot") or row.get("title") or "")[:act.MAX_TITLE],
            "at": at,
            "focus": _focus_for(row),
        })
        if len(out) >= limit:
            break
    return out


def _focus_for(row: dict[str, Any]) -> dict[str, str]:
    """Closed focus enum only — never a URL (docs/23 §7.1)."""
    from services import session_resources as sr

    resource_type = str(row.get("resource_type") or "")
    spec = sr.RESOURCE_REGISTRY.get(resource_type)
    canonical = row.get("canonical_ref") or {}
    return {"kind": spec.focus_kind if spec else "resource",
            "id": str(canonical.get("id") or "")}
