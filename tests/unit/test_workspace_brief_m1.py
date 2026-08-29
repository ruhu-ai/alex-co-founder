from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import InMemoryDurableStore
from services.workspace_brief import WorkspaceBriefAssembler

pytestmark = pytest.mark.asyncio


def principal() -> ActorPrincipal:
    return ActorPrincipal(
        actor_id="actor-a", workspace_id="workspace-a",
        role=WorkspaceRole.FOUNDER, session_auth_time=1_800_000_000,
        membership_version=1, membership_id="member-a")


async def test_brief_reads_registered_durable_projections_and_excludes_hiring():
    store = InMemoryDurableStore()
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    await store.create("workflow_runs", "run-general", {
        "run_id": "run-general", "workspace_id": "workspace-a",
        "run_kind": "OPPORTUNITY_DISCOVERY",
        "workflow_kind": "opportunity_discovery:v1",
        "runtime_status": "SUCCEEDED", "updated_at": stamp,
        "completed_at": stamp, "version": 1,
    })
    await store.create("workflow_runs", "run-hiring", {
        "run_id": "run-hiring", "workspace_id": "workspace-a",
        "run_kind": "CANDIDATE", "workflow_kind": "hiring_candidate:v1",
        "runtime_status": "WAITING", "updated_at": stamp, "version": 1,
    })
    await store.create("run_events", "event-general", {
        "event_id": "event-general", "workspace_id": "workspace-a",
        "run_id": "run-general", "event_kind": "RUN_SUCCEEDED",
        "occurred_at": stamp, "version": 1,
    })
    await store.create("run_events", "event-hiring", {
        "event_id": "event-hiring", "workspace_id": "workspace-a",
        "run_id": "run-hiring", "event_kind": "WAIT_OPENED",
        "occurred_at": stamp, "version": 1,
    })
    await store.create("waits", "wait-general", {
        "wait_id": "wait-general", "workspace_id": "workspace-a",
        "run_id": "run-general", "wait_kind": "TIMER", "status": "OPEN",
        "created_at": stamp, "wake_after": (now + timedelta(days=1)).isoformat(),
        "version": 1,
    })
    await store.create("approvals", "approval-general", {
        "approval_id": "approval-general", "workspace_id": "workspace-a",
        "status": "PENDING", "run_id": "run-general", "gate": "send_email",
        "created_at": stamp, "expires_at": (now + timedelta(hours=1)).isoformat(),
        "version": 1,
    })
    await store.create("founder_inbox", "inbox-safe", {
        "inbox_item_id": "inbox-safe", "workspace_id": "workspace-a",
        "status": "UNREAD", "item_kind": "AMBIGUOUS_EVENT",
        "title": "Choose where this update belongs",
        "summary": "Open the bounded inbox projection.",
        "candidate_refs": [], "created_at": stamp, "version": 1,
    })
    await store.create("founder_inbox", "inbox-hiring", {
        "inbox_item_id": "inbox-hiring", "workspace_id": "workspace-a",
        "status": "UNREAD", "item_kind": "CANDIDATE_EVENT",
        "title": "Candidate exists", "summary": "restricted",
        "candidate_refs": [{"candidate_id": "secret"}],
        "created_at": stamp, "version": 1,
    })

    result = await WorkspaceBriefAssembler(store).assemble(
        principal=principal(), since=(now - timedelta(days=2)).isoformat(), now=now)
    assert result["model_calls"] == 0
    assert result["memory_backend_calls"] == 0
    assert result["transcript_reads"] == 0
    assert result["pending_signals_reads"] == 0
    assert result["scope"] == "WORKSPACE"
    assert result["unavailable"] is False
    assert [row["source_ref"] for row in result["needs_you"]] == [
        "approval:approval-general"]
    assert all("hiring" not in str(row).lower() for section in (
        "needs_you", "milestones", "waits", "inbox", "terminal_receipts")
        for row in result[section])
    assert [row["source_ref"] for row in result["inbox"]] == [
        "founder_inbox:inbox-safe"]


async def test_one_contributor_failure_is_partial_not_guessed():
    class Broken:
        name = "broken"
        section = "milestones"

        async def read(self, **kwargs):
            raise RuntimeError("raw internal detail must not escape")

    result = await WorkspaceBriefAssembler(
        InMemoryDurableStore(), contributors=(Broken(),)).assemble(
            principal=principal())
    assert result["partial"] is True
    assert result["unavailable"] is True
    assert result["milestones"] == []
    assert result["contributor_errors"] == [{
        "contributor": "broken",
        "safe_error_code": "brief_contributor_unavailable",
    }]
