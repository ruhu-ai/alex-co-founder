from __future__ import annotations

import time

import pytest

from services import approval_service
from services.actor_identity import (
    ActorPrincipal,
    WorkspaceRole,
    authorize,
    change_membership,
    create_membership,
    resolve_actor_from_claims,
)
from services.durable_store import InMemoryDurableStore
from services.workflow_runtime import WorkflowRuntime

pytestmark = pytest.mark.asyncio


async def test_multi_workspace_subject_requires_explicit_selection():
    store = InMemoryDurableStore()
    for workspace in ("workspace_a", "workspace_b"):
        created = await create_membership(
            actor_id="actor_shared", workspace_id=workspace,
            auth_subject="subject_shared", role=WorkspaceRole.FOUNDER,
            created_by="test", store=store)
        assert created["status"] == "success"

    ambiguous = await resolve_actor_from_claims(
        {"sub": "subject_shared", "auth_time": 1}, store=store)
    selected = await resolve_actor_from_claims(
        {"sub": "subject_shared", "auth_time": 1}, store=store,
        workspace_id="workspace_b")

    assert ambiguous["error_code"] == "workspace_selection_required"
    assert isinstance(selected, ActorPrincipal)
    assert selected.workspace_id == "workspace_b"


async def test_current_membership_revocation_is_effective_next_resolution():
    store = InMemoryDurableStore()
    await create_membership(
        actor_id="actor_a", workspace_id="workspace_a",
        auth_subject="subject_a", role=WorkspaceRole.FOUNDER,
        created_by="test", store=store)
    principal = await resolve_actor_from_claims(
        {"sub": "subject_a", "auth_time": 1}, store=store,
        workspace_id="workspace_a")
    assert isinstance(principal, ActorPrincipal)
    member = (await store.list(
        "workspace_members", filters={"actor_id": "actor_a"}, limit=2))[0]
    await store.compare_and_set(
        "workspace_members", member["membership_id"], member["version"],
        {"status": "REVOKED"})

    revoked = await resolve_actor_from_claims(
        {"sub": "subject_a", "auth_time": 1}, store=store,
        workspace_id="workspace_a")
    assert revoked["error_code"] == "membership_missing"


async def test_founder_is_the_only_workspace_authority_without_operator_marker():
    store = InMemoryDurableStore()
    created = await create_membership(
        actor_id="actor_founder", workspace_id="workspace_a",
        auth_subject="subject_founder", role=WorkspaceRole.FOUNDER,
        created_by="test", store=store, synthetic=False)
    assert "operator_authority" not in created
    principal = await resolve_actor_from_claims(
        {"sub": "subject_founder", "auth_time": int(time.time())},
        store=store, workspace_id="workspace_a")
    assert isinstance(principal, ActorPrincipal)
    assert "operator_authority" not in principal.audit_fields()
    changed = await change_membership(
        principal=principal, actor_id="actor_founder",
        expected_version=created["version"], status="ACTIVE",
        client_request_id="founder-membership-change-001", store=store)
    assert changed["status"] == "success"


async def test_seed_or_workload_kind_cannot_become_fresh_human_by_label():
    seeded = ActorPrincipal(
        actor_id="seeded_user", workspace_id="user", role=WorkspaceRole.FOUNDER, session_auth_time=10**12,
        membership_version=1, principal_kind="SEEDED")

    # Local principals can exercise deterministic local/eval flows, but a
    # human approval decision must be based on an INTERACTIVE principal.
    assert authorize(seeded, "resolve_approval")[
        "error_code"] == "interactive_human_required"


async def test_signed_in_initiator_may_decide_their_own_approval(monkeypatch):
    principal = ActorPrincipal(
        actor_id="actor_founder", workspace_id="workspace_a",
        role=WorkspaceRole.FOUNDER,
        session_auth_time=int(time.time()), membership_version=3,
        principal_kind="INTERACTIVE")
    captured = {}

    async def decide(approval_id, decision, founder_id, session_id, **kwargs):
        captured.update(
            approval_id=approval_id, decision=decision,
            workspace_id=founder_id, session_id=session_id, **kwargs)
        return {"status": "success", "approval_id": approval_id}

    monkeypatch.setattr(
        approval_service.firestore, "resolve_approval_decision", decide)

    async def legacy_approval(_workspace_id, _approval_id):
        return None

    monkeypatch.setattr(
        approval_service.firestore, "get_approval_for_workspace", legacy_approval)
    result = await approval_service.resolve_for_principal(
        principal=principal, approval_id="approval_a",
        decision="grant", session_id="session_a")

    assert result["status"] == "success"
    assert captured["deciding_actor_id"] == principal.actor_id
    assert captured["workspace_id"] == principal.workspace_id


async def test_approval_state_lookup_failure_fails_closed(monkeypatch):
    principal = ActorPrincipal(
        actor_id="actor_founder", workspace_id="workspace_a",
        role=WorkspaceRole.FOUNDER,
        session_auth_time=int(time.time()), membership_version=3,
        principal_kind="INTERACTIVE")

    async def unavailable(_workspace_id, _approval_id):
        raise RuntimeError("store unavailable")

    async def resolver(*_args, **_kwargs):
        pytest.fail("legacy resolver must not run without authoritative state")

    monkeypatch.setattr(
        approval_service.firestore, "get_approval_for_workspace", unavailable)
    monkeypatch.setattr(approval_service.firestore, "resolve_approval_decision", resolver)
    result = await approval_service.resolve_for_principal(
        principal=principal, approval_id="approval_a",
        decision="grant", session_id="session_a")

    assert result["error_code"] == "authoritative_state_unavailable"


async def test_model_facing_errors_never_contain_http_transport_fields():
    store = InMemoryDurableStore()
    actor_error = await resolve_actor_from_claims(None, store=store)
    runtime_error = await WorkflowRuntime(store).recover_run("missing")

    assert "http_status" not in actor_error
    assert "http_status" not in runtime_error
