"""Regression tests for the closed two-account internal-demo authority."""

from __future__ import annotations

import time

import pytest

from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import InMemoryDurableStore
from services.internal_controlled_demo import (
    InternalControlledDemoService,
    build_exact_action,
    require_enabled,
)
from services.internal_controlled_demo_effects import InternalDemoEffectService
from services.internal_controlled_demo_intake import InternalDemoInboxImportService


@pytest.fixture
def owner() -> ActorPrincipal:
    return ActorPrincipal(
        actor_id="owner", workspace_id="workspace_demo", role=WorkspaceRole.OWNER,
        role_grants=frozenset(), candidate_assignments=frozenset(),
        interview_assignments=frozenset(), session_auth_time=int(time.time()),
        membership_version=1)


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("HIRING_ENABLE_INTERNAL_CONTROLLED_DEMO", "1")
    monkeypatch.setenv("HIRING_INTERNAL_DEMO_ALEX_SUBJECT_SHA256", "sha256:" + "a" * 64)
    monkeypatch.setenv("HIRING_INTERNAL_DEMO_FOUNDER_SUBJECT_SHA256", "sha256:" + "b" * 64)


async def _ready_role(store, owner) -> str:
    role_id = "role_internal_demo"
    await store.create("hiring_roles", role_id, {
        "role_id": role_id, "workspace_id": owner.workspace_id,
        "synthetic": True, "fixture_id": "fixture_ruhu_fde_walkthrough",
        "current_policy_version_id": "policy_internal_demo", "role_state": "PUBLISHED", "version": 1,
    })
    return role_id


def test_internal_demo_is_disabled_without_explicit_deployment_gate(monkeypatch):
    monkeypatch.delenv("HIRING_ENABLE_INTERNAL_CONTROLLED_DEMO", raising=False)
    assert require_enabled()["error_code"] == "internal_demo_disabled"


def test_internal_demo_has_no_custom_recipient_or_text_surface(monkeypatch):
    _enable(monkeypatch)
    built = build_exact_action("INTERNAL_DEMO_SEND_RECAP", demo_run_id="demo_run_001")
    assert built["status"] == "success"
    exact = built["exact_action"]
    assert exact["alex_address"] == "alex@ruhu.ai"
    assert exact["founder_address"] == "ijidai@ruhu.ai"
    assert "recipient" not in exact and "rendered_payload" not in exact
    assert build_exact_action("SEND_EMAIL", demo_run_id="demo_run_001")["error_code"] == "internal_demo_action_invalid"


@pytest.mark.asyncio
async def test_internal_demo_approval_is_run_bound_exact_and_single_resolution(monkeypatch, owner):
    _enable(monkeypatch)
    store = InMemoryDurableStore()
    service = InternalControlledDemoService(store)
    created = await service.create_run(
        principal=owner, client_request_id="create-1", role_id=await _ready_role(store, owner))
    assert created["status"] == "success"
    approval = await service.request_approval(
        principal=owner, demo_run_id=created["demo_run_id"],
        action_kind="INTERNAL_DEMO_SEND_RECAP", client_request_id="approve-1")
    assert approval["approval_status"] == "PENDING"
    assert approval["exact_action"]["founder_address"] == "ijidai@ruhu.ai"
    granted = await service.resolve_approval(
        principal=owner, approval_id=approval["approval_id"], decision="GRANT")
    assert granted["approval_status"] == "GRANTED"
    assert (await service.resolve_approval(
        principal=owner, approval_id=approval["approval_id"], decision="GRANT"))["error_code"] == "approval_terminal"


@pytest.mark.asyncio
async def test_internal_demo_effect_consumes_only_the_exact_granted_approval(monkeypatch, owner):
    _enable(monkeypatch)
    store = InMemoryDurableStore()
    runs = InternalControlledDemoService(store)
    created = await runs.create_run(
        principal=owner, client_request_id="create-2", role_id=await _ready_role(store, owner))
    approval = await runs.request_approval(
        principal=owner, demo_run_id=created["demo_run_id"],
        action_kind="INTERNAL_DEMO_SEND_RECAP", client_request_id="approve-2")
    await runs.resolve_approval(principal=owner, approval_id=approval["approval_id"], decision="GRANT")

    class FakeAdapter:
        async def execute(self, *, exact_action, action_id):
            assert exact_action["alex_address"] == "alex@ruhu.ai"
            return {"status": "success", "provider_effect_id": "message-1", "result_ref": {"id": "message-1"}}

    effect = InternalDemoEffectService(store=store, adapter=FakeAdapter())
    first = await effect.execute(principal=owner, demo_run_id=created["demo_run_id"],
                                 approval_id=approval["approval_id"], action_kind="INTERNAL_DEMO_SEND_RECAP")
    assert first["receipt_status"] == "SUCCEEDED"
    again = await effect.execute(principal=owner, demo_run_id=created["demo_run_id"],
                                 approval_id=approval["approval_id"], action_kind="INTERNAL_DEMO_SEND_RECAP")
    assert again["duplicate"] is True


@pytest.mark.asyncio
async def test_calendar_invite_is_server_scheduled_and_reset_requires_its_cancellation(monkeypatch, owner):
    _enable(monkeypatch)
    store = InMemoryDurableStore()
    runs = InternalControlledDemoService(store)
    created = await runs.create_run(
        principal=owner, client_request_id="create-calendar", role_id=await _ready_role(store, owner))
    invitation = await runs.request_approval(
        principal=owner, demo_run_id=created["demo_run_id"],
        action_kind="INTERNAL_DEMO_CREATE_CALENDAR_EVENT", client_request_id="approve-calendar")
    exact = invitation["exact_action"]
    assert exact["calendar_event_id"].startswith("idemo")
    assert exact["calendar"]["timezone"] == "Africa/Lagos"
    assert exact["founder_address"] == "ijidai@ruhu.ai"
    assert "rendered_payload" not in exact
    await runs.resolve_approval(principal=owner, approval_id=invitation["approval_id"], decision="GRANT")

    class FakeCalendarAdapter:
        async def execute(self, *, exact_action, action_id):
            return {"status": "success", "provider_effect_id": exact_action["calendar_event_id"],
                    "result_ref": {"event_id": exact_action["calendar_event_id"]}}

    effects = InternalDemoEffectService(store=store, adapter=FakeCalendarAdapter())
    created_event = await effects.execute(
        principal=owner, demo_run_id=created["demo_run_id"], approval_id=invitation["approval_id"],
        action_kind="INTERNAL_DEMO_CREATE_CALENDAR_EVENT")
    assert created_event["receipt_status"] == "SUCCEEDED"
    assert (await runs.reset(principal=owner, demo_run_id=created["demo_run_id"])
            )["error_code"] == "calendar_cancellation_required"

    cancellation = await runs.request_approval(
        principal=owner, demo_run_id=created["demo_run_id"],
        action_kind="INTERNAL_DEMO_CANCEL_CALENDAR_EVENT", client_request_id="approve-cancel")
    await runs.resolve_approval(principal=owner, approval_id=cancellation["approval_id"], decision="GRANT")
    cancelled = await effects.execute(
        principal=owner, demo_run_id=created["demo_run_id"], approval_id=cancellation["approval_id"],
        action_kind="INTERNAL_DEMO_CANCEL_CALENDAR_EVENT")
    assert cancelled["receipt_status"] == "SUCCEEDED"
    assert (await runs.reset(principal=owner, demo_run_id=created["demo_run_id"])
            )["status"] == "success"


@pytest.mark.asyncio
async def test_marked_inbox_import_creates_a_candidate_case_and_evidence_passport(monkeypatch, owner):
    _enable(monkeypatch)
    monkeypatch.setenv("HIRING_INTERNAL_DEMO_APPLICATION_PDF_SHA256", "sha256:" + "c" * 64)
    store = InMemoryDurableStore()
    role_id = await _ready_role(store, owner)
    runs = InternalControlledDemoService(store)
    created = await runs.create_run(
        principal=owner, client_request_id="create-import", role_id=role_id)

    class _Execute:
        def __init__(self, value): self.value = value
        def execute(self): return self.value

    class FakeGmail:
        def users(self): return self
        def messages(self): return self
        def list(self, **_): return _Execute({"messages": [{"id": "gmail-message-1"}]})
        def get(self, **_): return _Execute({"id": "gmail-message-1", "threadId": "thread-1"})

    class FakeHiring:
        async def ingest_synthetic_application(self, **kwargs):
            assert kwargs["role_id"] == role_id
            assert kwargs["external_event_id"]
            assert kwargs["source_sha256"] == "sha256:" + "c" * 64
            return {"status": "success", "candidate_application_id": "candidate-case-1",
                    "assessment_id": "passport-1"}

    importer = InternalDemoInboxImportService(
        store, gmail_factory=FakeGmail, hiring=FakeHiring())
    monkeypatch.setattr(importer, "_validate_message", lambda *_: {"status": "success", "pages": 2})
    result = await importer.import_fixture(principal=owner, demo_run_id=created["demo_run_id"])
    assert result["candidate_application_id"] == "candidate-case-1"
    assert result["assessment_id"] == "passport-1"
    events = await store.list("external_events", filters={"role_id": role_id})
    assert events[0]["processing_status"] == "APPLIED"
    rows = await store.list("internal_demo_applications", filters={"demo_run_id": created["demo_run_id"]})
    assert rows[0]["candidate_application_id"] == "candidate-case-1"
