"""Deterministic M1 "Since you were away" brief (docs/39 §5.1).

The assembler reads only registered durable projections.  It imports no ADK
session service, transcript/catalog search helper, model client, or optional
memory adapter.  Each contributor fails independently and returns bounded,
server-rendered items; Hiring records are excluded until a dedicated
assignment-aware contributor is separately approved.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from services.actor_identity import ActorPrincipal
from services.durable_store import DurableStore, production_store
from services.workflow_contracts import RuntimeStatus, normalize_runtime_status

MAX_WINDOW_DAYS = 30
MAX_NEEDS_YOU = 3
MAX_MILESTONES = 5
MAX_WAITS = 5
MAX_INBOX = 5
MAX_RECEIPTS = 5
_HIRING_RUN_KINDS = {"ROLE", "CANDIDATE", "ONBOARDING"}
_TERMINAL = {
    RuntimeStatus.SUCCEEDED.value,
    RuntimeStatus.FAILED.value,
    RuntimeStatus.REJECTED.value,
    RuntimeStatus.CANCELLED.value,
}
_MILESTONE_EVENTS = {
    "RUN_CREATED": "Work started",
    "STEP_COMPLETED": "Step completed",
    "WAIT_OPENED": "Waiting",
    "WAIT_RESOLVED": "Wait resolved",
    "RUN_PAUSED": "Work paused",
    "RUN_RESUMED": "Work resumed",
    "RUN_CANCELLING": "Cancellation started",
    "RUN_CANCELLED": "Work cancelled",
    "RUN_SUCCEEDED": "Work completed",
}


def release_enabled(workspace_id: str) -> bool:
    """M1 is default-off and requires an explicit workspace allowlist."""
    enabled = os.environ.get(
        "DURABLE_BRIEF_M1_ENABLED", "false").lower() in {
            "1", "true", "yes", "on",
        }
    allowlist = {
        item.strip() for item in os.environ.get(
            "DURABLE_BRIEF_M1_WORKSPACE_ALLOWLIST", "").split(",")
        if item.strip()
    }
    return enabled and workspace_id in allowlist


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clamp_since(value: str, *, now: datetime) -> datetime:
    floor = now - timedelta(days=MAX_WINDOW_DAYS)
    parsed = _parse(value)
    if parsed is None:
        return max(floor, now - timedelta(days=1))
    return min(now, max(floor, parsed))


def _safe_ref(kind: str, value: str) -> dict[str, str]:
    return {"resource_kind": kind, "resource_id": str(value or "")[:160]}


def _item(*, kind: str, domain_family: str, title: str, summary: str,
          occurred_at: str, focus_kind: str, focus_id: str,
          source_ref: str, version: Any, urgency: str = "NORMAL",
          terminal_status: str = "") -> dict[str, Any]:
    return {
        "kind": kind, "domain_family": domain_family,
        "title": str(title)[:160], "safe_summary": str(summary)[:300],
        "occurred_at": str(occurred_at or "")[:48],
        "urgency": urgency, "terminal_status": terminal_status or None,
        "focus": _safe_ref(focus_kind, focus_id),
        "source_ref": str(source_ref)[:200],
        "projection_version": int(version or 1),
    }


def _eligible_run(run: dict[str, Any] | None,
                  workspace_id: str) -> bool:
    return bool(run and run.get("workspace_id") == workspace_id
                and run.get("run_kind") not in _HIRING_RUN_KINDS
                and not str(run.get("workflow_kind") or "").startswith("hiring_"))


class BriefContributor(Protocol):
    name: str
    section: str

    async def read(self, *, principal: ActorPrincipal, since: datetime,
                   now: datetime, limit: int,
                   session_id: str = "") -> list[dict[str, Any]]: ...


@dataclass
class PendingApprovalContributor:
    store: DurableStore
    name: str = "platform_approvals"
    section: str = "needs_you"

    async def read(self, *, principal: ActorPrincipal, since: datetime,
                   now: datetime, limit: int,
                   session_id: str = "") -> list[dict[str, Any]]:
        del since
        rows = await self.store.list(
            "approvals", filters={"workspace_id": principal.workspace_id,
                                  "status": "PENDING"},
            order_by="created_at", descending=True, limit=limit * 4)
        out = []
        for row in rows:
            if str(row.get("expires_at") or "") <= now.isoformat():
                continue
            run_id = str(row.get("run_id") or "")
            run = None
            if run_id:
                run = await self.store.get("workflow_runs", run_id)
                if not _eligible_run(run, principal.workspace_id):
                    continue
            origin_session_id = str(
                (run or {}).get("origin_session_id")
                or row.get("origin_session_id") or "")
            if session_id and origin_session_id != session_id:
                continue
            if str(row.get("approval_domain") or "").startswith("HIRING"):
                continue
            approval_id = str(row.get("approval_id") or row.get("id") or "")
            gate = str(row.get("gate") or row.get("action_kind") or "action")
            title = "Approval required: " + gate.replace("_", " ").strip().title()
            out.append(_item(
                kind="PENDING_APPROVAL", domain_family="platform",
                title=title,
                summary=("Review the exact consequence in Decisions before "
                         f"{str(row.get('expires_at') or '')[:32]}."),
                occurred_at=str(row.get("created_at") or ""),
                focus_kind="approval", focus_id=approval_id,
                source_ref=f"approval:{approval_id}", version=row.get("version"),
                urgency="NEEDS_FOUNDER"))
            if len(out) >= limit:
                break
        return out


@dataclass
class WorkflowMilestoneContributor:
    store: DurableStore
    name: str = "workflow_milestones"
    section: str = "milestones"

    async def read(self, *, principal: ActorPrincipal, since: datetime,
                   now: datetime, limit: int,
                   session_id: str = "") -> list[dict[str, Any]]:
        del now
        rows = await self.store.list(
            "run_events", filters={"workspace_id": principal.workspace_id},
            order_by="occurred_at", descending=True, limit=limit * 12)
        out = []
        for row in rows:
            occurred = _parse(str(row.get("occurred_at") or ""))
            if occurred is None or occurred < since:
                continue
            event_kind = str(row.get("event_kind") or "")
            title = _MILESTONE_EVENTS.get(event_kind)
            if not title:
                continue
            run_id = str(row.get("run_id") or "")
            run = await self.store.get("workflow_runs", run_id)
            if not _eligible_run(run, principal.workspace_id):
                continue
            if (session_id
                    and str(run.get("origin_session_id") or "") != session_id):
                continue
            run_label = str(run.get("run_kind") or "work").replace("_", " ").title()
            event_id = str(row.get("event_id") or row.get("id") or "")
            out.append(_item(
                kind=event_kind, domain_family=str(
                    run.get("workflow_kind") or "platform").split(":", 1)[0],
                title=f"{run_label}: {title.lower()}",
                summary="A durable workflow transition was recorded.",
                occurred_at=str(row.get("occurred_at") or ""),
                focus_kind="run", focus_id=run_id,
                source_ref=f"run_event:{event_id}", version=row.get("version"),
                terminal_status=(normalize_runtime_status(
                    str(run.get("runtime_status") or ""))
                    if run.get("runtime_status") else "")))
            if len(out) >= limit:
                break
        return out


@dataclass
class DurableWaitContributor:
    store: DurableStore
    name: str = "durable_waits"
    section: str = "waits"

    async def read(self, *, principal: ActorPrincipal, since: datetime,
                   now: datetime, limit: int,
                   session_id: str = "") -> list[dict[str, Any]]:
        del since, now
        rows = await self.store.list(
            "waits", filters={"workspace_id": principal.workspace_id,
                              "status": "OPEN"},
            order_by="created_at", descending=True, limit=limit * 4)
        out = []
        for row in rows:
            run_id = str(row.get("run_id") or "")
            run = await self.store.get("workflow_runs", run_id)
            if not _eligible_run(run, principal.workspace_id):
                continue
            if (session_id
                    and str(run.get("origin_session_id") or "") != session_id):
                continue
            wait_id = str(row.get("wait_id") or row.get("id") or "")
            wait_kind = str(row.get("wait_kind") or "WORK").replace("_", " ").title()
            wake = str(row.get("wake_after") or "")
            summary = (f"Next server wake/check: {wake[:32]}." if wake
                       else "Waiting for the registered event; no polling is active.")
            out.append(_item(
                kind="OPEN_WAIT", domain_family=str(
                    run.get("workflow_kind") or "platform").split(":", 1)[0],
                title=f"Waiting: {wait_kind}", summary=summary,
                occurred_at=str(row.get("created_at") or ""),
                focus_kind="run", focus_id=run_id,
                source_ref=f"wait:{wait_id}", version=row.get("version")))
            if len(out) >= limit:
                break
        return out


@dataclass
class SafeInboxContributor:
    store: DurableStore
    name: str = "safe_founder_inbox"
    section: str = "inbox"

    async def read(self, *, principal: ActorPrincipal, since: datetime,
                   now: datetime, limit: int,
                   session_id: str = "") -> list[dict[str, Any]]:
        del since, now
        rows = await self.store.list(
            "founder_inbox", filters={"workspace_id": principal.workspace_id,
                                      "status": "UNREAD"},
            order_by="created_at", descending=True, limit=limit * 4)
        out = []
        for row in rows:
            # Generic M1 never reveals candidate refs or Hiring inbox rows.
            if (row.get("candidate_refs")
                    or str(row.get("inbox_domain") or "").startswith("HIRING")):
                continue
            origin_session_id = str(
                row.get("resolved_session_id")
                or row.get("origin_session_id") or "")
            # Ambiguous/unmatched inbox items normally have no exact session.
            # They remain visible in the global Founder inbox and must never be
            # copied into whichever conversation happens to be open.
            if session_id and origin_session_id != session_id:
                continue
            item_id = str(row.get("inbox_item_id") or row.get("id") or "")
            out.append(_item(
                kind=str(row.get("item_kind") or "INBOX_ITEM"),
                domain_family="inbox", title=str(row.get("title") or "Needs review"),
                summary=str(row.get("summary") or "Open the inbox item for details."),
                occurred_at=str(row.get("created_at") or ""),
                focus_kind="inbox_item", focus_id=item_id,
                source_ref=f"founder_inbox:{item_id}", version=row.get("version"),
                urgency="NEEDS_FOUNDER"))
            if len(out) >= limit:
                break
        return out


@dataclass
class TerminalReceiptContributor:
    store: DurableStore
    name: str = "terminal_receipts"
    section: str = "terminal_receipts"

    async def read(self, *, principal: ActorPrincipal, since: datetime,
                   now: datetime, limit: int,
                   session_id: str = "") -> list[dict[str, Any]]:
        del now
        rows = await self.store.list(
            "workflow_runs", filters={"workspace_id": principal.workspace_id},
            order_by="updated_at", descending=True, limit=limit * 8)
        out = []
        for run in rows:
            if not _eligible_run(run, principal.workspace_id):
                continue
            if (session_id
                    and str(run.get("origin_session_id") or "") != session_id):
                continue
            occurred_at = str(run.get("completed_at") or run.get("updated_at") or "")
            occurred = _parse(occurred_at)
            if occurred is None or occurred < since:
                continue
            try:
                status = normalize_runtime_status(str(run.get("runtime_status") or ""))
            except ValueError:
                continue
            if status not in _TERMINAL:
                continue
            run_id = str(run.get("run_id") or run.get("id") or "")
            label = str(run.get("run_kind") or "work").replace("_", " ").title()
            out.append(_item(
                kind="TERMINAL_RECEIPT",
                domain_family=str(run.get("workflow_kind") or "platform").split(":", 1)[0],
                title=f"{label}: {status.lower()}",
                summary="Terminal status comes from the current durable run record.",
                occurred_at=occurred_at, focus_kind="run", focus_id=run_id,
                source_ref=f"workflow_run:{run_id}", version=run.get("version"),
                terminal_status=status))
            if len(out) >= limit:
                break
        return out


class WorkspaceBriefAssembler:
    """Registered, independently failable M1 contributors."""

    def __init__(self, store: DurableStore | None = None,
                 contributors: tuple[BriefContributor, ...] | None = None):
        self.store = store or production_store()
        self.contributors = contributors or (
            PendingApprovalContributor(self.store),
            WorkflowMilestoneContributor(self.store),
            DurableWaitContributor(self.store),
            SafeInboxContributor(self.store),
            TerminalReceiptContributor(self.store),
        )

    async def assemble(self, *, principal: ActorPrincipal, since: str = "",
                       now: datetime | None = None,
                       session_id: str = "") -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        bounded_since = clamp_since(since, now=now)
        sections: dict[str, list[dict[str, Any]]] = {
            "needs_you": [], "milestones": [], "waits": [], "inbox": [],
            "terminal_receipts": [],
        }
        limits = {
            "needs_you": MAX_NEEDS_YOU, "milestones": MAX_MILESTONES,
            "waits": MAX_WAITS, "inbox": MAX_INBOX,
            "terminal_receipts": MAX_RECEIPTS,
        }
        errors: list[dict[str, str]] = []
        for contributor in self.contributors:
            try:
                values = await contributor.read(
                    principal=principal, since=bounded_since, now=now,
                    limit=limits[contributor.section], session_id=session_id)
                sections[contributor.section].extend(
                    values[:limits[contributor.section]])
            except Exception:  # noqa: BLE001 - one contributor never blanks M1
                errors.append({"contributor": contributor.name,
                               "safe_error_code": "brief_contributor_unavailable"})
        return {
            "status": "success", "schema_version": 1,
            "as_of": now.isoformat(), "since": bounded_since.isoformat(),
            "scope": "WORKSPACE",
            "partial": bool(errors),
            "unavailable": bool(errors) and len(errors) == len(self.contributors),
            **sections,
            "contributor_errors": errors,
            "source": "registered_durable_projections",
            "model_calls": 0, "memory_backend_calls": 0,
            "transcript_reads": 0, "pending_signals_reads": 0,
        }


def configured_assembler() -> WorkspaceBriefAssembler:
    return WorkspaceBriefAssembler()
