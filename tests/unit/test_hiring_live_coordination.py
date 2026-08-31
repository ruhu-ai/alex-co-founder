"""Real Hiring H4 communication and interview authority regressions."""

from __future__ import annotations

import base64
import time
from datetime import datetime
from email import message_from_bytes

import pytest

from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_store import InMemoryDurableStore
from services.hiring_approval_service import resolve_approval
from services.hiring_coordination import (
    GoogleHiringProviderAdapter,
    HiringCoordinationService,
)
from services.hiring_scheduling_agent import set_draft_fn, set_interpreter_fn


class _Identity:
    async def reveal_restricted_identity(self, *, application, principal):
        assert application["workspace_id"] == principal.workspace_id
        return {"status": "success", "identity": {
            "name": "Ada Candidate", "email": "ada@example.test"}}


class _Provider:
    def __init__(self):
        self.calls = []

    async def preflight(self, *, workspace_id, connector_id):
        return {"status": "success", "connection_id": f"conn_{connector_id}"}

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["action_kind"] == "HIRING_SEND_EMAIL":
            return {"status": "success", "provider_effect_id": "gmail_message_1",
                    "result_ref": {"provider_thread_id": "thread_hiring_1",
                                   "rfc822_message_id": "<hiring@test>"}}
        return {"status": "success", "provider_effect_id": "calendar_event_1",
                "result_ref": {"event_id": "calendar_event_1",
                               "meet_link": "https://meet.invalid/opaque"}}

    async def reconcile(self, **kwargs):
        return {"status": "success", "provider_effect_id": "reconciled"}


class _UncertainProvider(_Provider):
    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "uncertain", "uncertainty_reason": "provider_timeout"}


class _WorkspaceIndexOnlyStore(InMemoryDurableStore):
    async def list(self, collection, *, filters=None, order_by=None,
                   descending=False, limit=100):
        assert order_by is None
        assert not filters or set(filters) == {"workspace_id"}
        return await super().list(
            collection, filters=filters, order_by=order_by,
            descending=descending, limit=limit)


@pytest.fixture
def founder() -> ActorPrincipal:
    return ActorPrincipal(
        actor_id="founder_actor", workspace_id="founder",
        role=WorkspaceRole.FOUNDER, session_auth_time=int(time.time()),
        membership_version=1)


@pytest.fixture(autouse=True)
def _reset_scheduling_interpreter():
    set_interpreter_fn(None)
    set_draft_fn(None)
    yield
    set_interpreter_fn(None)
    set_draft_fn(None)


async def _seed(store: InMemoryDurableStore, *, advanced: bool = True,
                workspace_id: str = "founder") -> str:
    application_id = "candidateapp_" + "a" * 28
    run_id = "run_candidate_live"
    role_id = "role_live"
    await store.create("workflow_runs", run_id, {
        "schema_version": 2, "run_id": run_id, "workspace_id": workspace_id,
        "journey_id": "journey_live", "run_kind": "CANDIDATE",
        "domain_ref": application_id, "runtime_status": "WAITING",
        "next_event_sequence": 1, "provenance": {}, "version": 1,
    })
    await store.create("hiring_roles", role_id, {
        "role_id": role_id, "workspace_id": workspace_id,
        "role_title": "Product Lead", "current_policy_version_id": "hpv_live",
        "current_policy_hash": "sha256:policy", "synthetic": False,
        "version": 1,
    })
    decision_id = "decision_live"
    await store.create("hiring_decisions", decision_id, {
        "decision_id": decision_id, "workspace_id": workspace_id,
        "candidate_application_id": application_id,
        "decision": "ADVANCE" if advanced else "HOLD",
        "commit_status": "COMMITTED", "version": 1,
    })
    await store.create("candidate_applications", application_id, {
        "candidate_application_id": application_id, "workspace_id": workspace_id,
        "role_id": role_id, "run_id": run_id, "candidate_code": "C-00000001",
        "source_kind": "PUBLIC_FORM", "synthetic": False,
        "candidate_state": "ADVANCED" if advanced else "HELD",
        "current_decision_id": decision_id, "version": 1,
    })
    return application_id


def _service(store, provider):
    return HiringCoordinationService(store=store, adapter=provider, intake=_Identity())


async def _activate_mandate(service, store, founder, application_id, monkeypatch):
    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="mandate_activation_contact")
    await resolve_approval(
        principal=founder, approval_id=prepared["approval_id"],
        decision="GRANT", store=store)
    sent = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert sent["receipt_status"] == "SUCCEEDED"
    mandates = await store.list(
        "hiring_coordination_mandates", filters={"workspace_id": "founder"})
    assert len(mandates) == 1
    return mandates[0]


@pytest.mark.asyncio
async def test_advance_prepares_exact_real_email_and_executes_once(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    prepared = await _service(store, provider).prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_request_0001")
    assert prepared["status"] == "success"
    assert prepared["item"]["recipients_masked"] == ["a***@example.test"]
    assert "Founder is currently available" in prepared["item"]["body"]
    approved = await resolve_approval(
        principal=founder, approval_id=prepared["approval_id"],
        decision="GRANT", store=store)
    assert approved["approval_status"] == "GRANTED"
    sent = await _service(store, provider).execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert sent["receipt_status"] == "SUCCEEDED"
    mandate = await store.get(
        "hiring_coordination_mandates", prepared["mandate_id"])
    assert mandate["status"] == "ACTIVE"
    assert mandate["email_count"] == 1
    duplicate = await _service(store, provider).execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert duplicate["duplicate"] is True
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_initial_invitation_preview_is_editable_duration_bound_and_read_only(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    preview = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_preview_0001", duration_minutes=60,
        copy_founder=False, preview_only=True)

    assert preview["status"] == "success"
    assert preview["preview_only"] is True
    assert preview["item"]["duration_minutes"] == 60
    assert "approximately 60 minutes" in preview["item"]["body"]
    assert all(
        int((datetime.fromisoformat(slot["end"]) -
             datetime.fromisoformat(slot["start"])).total_seconds()) == 3600
        for slot in preview["item"]["slot_options"])
    assert all("–" in slot["display"] for slot in preview["item"]["slot_options"])
    assert await store.list("approvals", filters={}) == []
    assert await store.list("hiring_coordination_items", filters={}) == []
    assert provider.calls == []

    custom_subject = "Your Ruhu interview — choose a one-hour time"
    custom_body = preview["item"]["body"].replace(
        "interview conversation", "one-hour interview conversation")
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_submit_0001", duration_minutes=60,
        subject=custom_subject, body=custom_body,
        availability_hash=preview["availability_hash"])

    assert prepared["item"]["subject"] == custom_subject
    assert prepared["item"]["body"] == custom_body
    assert prepared["item"]["duration_minutes"] == 60
    assert len(await store.list("approvals", filters={})) == 1
    stored = await store.get(
        "hiring_coordination_items", prepared["coordination_id"])
    assert stored["status"] == "AWAITING_APPROVAL"
    assert stored["exact_action"]["payload"]["subject"] == custom_subject
    assert stored["mandate_exact"]["duration_minutes"] == 60
    assert stored["mandate_exact"]["initial_message_hash"]
    conflict = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_submit_0001", duration_minutes=60,
        subject="Changed after approval creation", body=custom_body,
        availability_hash=preview["availability_hash"])
    assert conflict["error_code"] == "idempotency_conflict"
    unchanged = await store.get(
        "hiring_coordination_items", prepared["coordination_id"])
    assert unchanged["subject"] == custom_subject

    await resolve_approval(
        principal=founder, approval_id=prepared["approval_id"],
        decision="GRANT", store=store)
    sent = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert sent["receipt_status"] == "SUCCEEDED"
    receipt = await store.get(
        "hiring_coordination_items", prepared["coordination_id"])
    assert receipt["status"] == "SUCCEEDED"
    assert receipt["subject"] == custom_subject
    assert receipt["body"] == custom_body


@pytest.mark.asyncio
async def test_changed_availability_or_unsupported_duration_creates_no_approval(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    stale = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_stale_0001", duration_minutes=60,
        subject="Interview", body="A complete edited invitation.",
        availability_hash="sha256:stale")
    invalid = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_duration_0001", duration_minutes=75,
        preview_only=True)

    assert stale["error_code"] == "founder_availability_changed"
    assert invalid["error_code"] == "invalid_contract"
    assert await store.list("approvals", filters={}) == []
    assert await store.list("hiring_coordination_items", filters={}) == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_approved_initial_invitation_rejects_payload_tampering(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_tamper_0001")
    await resolve_approval(
        principal=founder, approval_id=prepared["approval_id"],
        decision="GRANT", store=store)
    item = await store.get(
        "hiring_coordination_items", prepared["coordination_id"])
    changed_exact = dict(item["exact_action"])
    changed_exact["payload"] = {
        **changed_exact["payload"], "body": "Tampered after approval"}
    await store.compare_and_set(
        "hiring_coordination_items", prepared["coordination_id"],
        item["version"], {"exact_action": changed_exact})

    denied = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])

    assert denied["error_code"] == "coordination_mandate_invalid"
    assert provider.calls == []
    assert await store.list("external_actions", filters={}) == []


@pytest.mark.asyncio
async def test_non_advanced_synthetic_cross_tenant_and_kill_switch_fail_closed(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store, advanced=False)
    denied = await _service(store, provider).prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_request_0002")
    assert denied["error_code"] == "founder_advance_required"
    other = ActorPrincipal(
        actor_id="other", workspace_id="other", role=WorkspaceRole.FOUNDER,
        session_auth_time=int(time.time()), membership_version=1)
    assert (await _service(store, provider).projection(
        principal=other, application_id=application_id))["error_code"] == "application_not_found"
    app = await store.get("candidate_applications", application_id)
    await store.compare_and_set("candidate_applications", application_id,
                                app["version"], {"candidate_state": "ADVANCED"})
    decision = await store.get("hiring_decisions", "decision_live")
    await store.compare_and_set("hiring_decisions", "decision_live",
                                decision["version"], {"decision": "ADVANCE"})
    monkeypatch.setenv("HIRING_LIVE_OPERATIONS_KILL_SWITCH", "1")
    killed = await _service(store, provider).prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_request_0003")
    assert killed["error_code"] == "hiring_operations_killed"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_projection_needs_only_existing_workspace_index(founder):
    store, provider = _WorkspaceIndexOnlyStore(), _Provider()
    application_id = await _seed(store)
    result = await _service(store, provider).projection(
        principal=founder, application_id=application_id)
    assert result["status"] == "success"
    assert result["items"] == []


@pytest.mark.asyncio
async def test_reply_requires_exact_thread_and_candidate_sender(founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_request_0004")
    await resolve_approval(principal=founder, approval_id=prepared["approval_id"],
                           decision="GRANT", store=store)
    await service.execute(principal=founder, application_id=application_id,
                          coordination_id=prepared["coordination_id"],
                          approval_id=prepared["approval_id"])
    forged = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_forged", "thread_id": "thread_hiring_1",
            "from": "Mallory <mallory@example.test>", "subject": "Re: interview",
            "excerpt": "available tomorrow", "kind": "update"})
    assert forged["error_code"] == "reply_not_correlatable"
    reply = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_exact", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>", "subject": "Re: interview",
            "excerpt": "Option two works for me.", "kind": "update"})
    assert reply["candidate_application_id"] == application_id
    assert reply["continuation_status"] == "success"
    assert [call["action_kind"] for call in provider.calls] == [
        "HIRING_SEND_EMAIL", "HIRING_CREATE_INTERVIEW"]
    duplicate = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_exact", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>", "subject": "Re: interview",
            "excerpt": "Option two works for me.", "kind": "update"})
    assert duplicate["duplicate"] is True


@pytest.mark.asyncio
async def test_exact_applicant_reply_is_not_suppressed_by_quoted_confirmation_kind(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    await _activate_mandate(service, store, founder, application_id, monkeypatch)

    reply = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_quoted_confirmation",
            "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>",
            "subject": "Re: Interview availability",
            "excerpt": "Option two works for me.",
            "kind": "confirmation", "automated": False,
        })

    assert reply["automated"] is False
    assert reply["continuation_status"] == "success"
    assert provider.calls[-1]["action_kind"] == "HIRING_CREATE_INTERVIEW"


@pytest.mark.asyncio
async def test_exact_automated_notice_can_be_replayed_once_after_reclassification(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    await _activate_mandate(service, store, founder, application_id, monkeypatch)
    event = {
        "id": "message_replay_exact", "thread_id": "thread_hiring_1",
        "from": "Ada Candidate <ada@example.test>",
        "subject": "Re: Interview availability",
        "excerpt": "Option one works for me.", "kind": "confirmation",
        "automated": True, "automation_basis": "LEGACY_CLASSIFIER",
    }
    first = await service.correlate_reply(
        workspace_id="founder", provider_event=event)
    assert first["automated"] is True
    assert len(provider.calls) == 1

    repaired = await service.correlate_reply(
        workspace_id="founder", provider_event={**event, "automated": False,
                                                 "automation_basis": ""},
        replay_existing=True)
    duplicate = await service.correlate_reply(
        workspace_id="founder", provider_event={**event, "automated": False},
        replay_existing=True)

    assert repaired["replayed"] is True
    assert repaired["continuation_status"] == "success"
    assert provider.calls[-1]["action_kind"] == "HIRING_CREATE_INTERVIEW"
    assert duplicate["duplicate"] is True


@pytest.mark.asyncio
async def test_replay_reloads_only_receipt_bound_message_and_dry_run_is_inert(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    await _activate_mandate(service, store, founder, application_id, monkeypatch)
    first = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_bound_replay", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>",
            "subject": "Re: Interview availability", "excerpt": "Option one.",
            "automated": True, "automation_basis": "LEGACY_CLASSIFIER",
        })
    reads = []

    async def load(message_id, workspace_id):
        reads.append((message_id, workspace_id))
        return {"status": "success", "event": {
            "id": message_id, "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>",
            "subject": "Re: Interview availability", "excerpt": "Option one.",
            "automated": False,
        }}

    monkeypatch.setattr("services.alex_mailbox.load_provider_event", load)
    preview = await service.replay_misclassified_reply(
        workspace_id="founder", correlation_id=first["correlation_id"])
    replay = await service.replay_misclassified_reply(
        workspace_id="founder", correlation_id=first["correlation_id"],
        execute=True)

    assert preview["dry_run"] is True and preview["eligible"] is True
    assert reads == [("message_bound_replay", "founder")]
    assert replay["replayed"] is True
    assert provider.calls[-1]["action_kind"] == "HIRING_CREATE_INTERVIEW"


@pytest.mark.asyncio
async def test_ambiguous_reply_gets_bounded_alex_followup_without_new_approval(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    mandate = await _activate_mandate(
        service, store, founder, application_id, monkeypatch)
    approval_count = len(await store.list("approvals", filters={}))
    reply = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_ambiguous", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>",
            "subject": "Re: Interview availability",
            "excerpt": "Could you remind me of the available times?", "kind": "update",
            "rfc822_message_id": "<candidate-reply@example.test>",
            "in_reply_to": "<hiring@test>",
            "references": "<root@ruhu.ai> <hiring@test>",
        })
    assert reply["continuation_status"] == "success"
    assert len(await store.list("approvals", filters={})) == approval_count == 1
    assert [call["action_kind"] for call in provider.calls] == [
        "HIRING_SEND_EMAIL", "HIRING_SEND_EMAIL"]
    reply_payload = provider.calls[-1]["exact_action"]["payload"]
    assert reply_payload["provider_thread_id"] == "thread_hiring_1"
    assert reply_payload["in_reply_to"] == "<candidate-reply@example.test>"
    assert reply_payload["references"] == (
        "<root@ruhu.ai> <hiring@test> <candidate-reply@example.test>")
    refreshed = await store.get(
        "hiring_coordination_mandates", mandate["mandate_id"])
    assert refreshed["email_count"] == 2


@pytest.mark.asyncio
async def test_google_reply_binds_thread_and_rfc_headers(monkeypatch):
    captured = {}

    class Request:
        def execute(self):
            return {"id": "sent_reply", "threadId": "thread_hiring_1"}

    class Messages:
        def send(self, *, userId, body):  # noqa: N803 - Google API name
            assert userId == "me"
            captured.update(body)
            return Request()

    class Users:
        def messages(self):
            return Messages()

    class Gmail:
        def users(self):
            return Users()

    async def credentials(_workspace_id, _connector_id):
        return object()

    monkeypatch.setattr(
        GoogleHiringProviderAdapter, "_credentials", staticmethod(credentials))
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: Gmail())
    result = await GoogleHiringProviderAdapter()._send_email(
        "founder",
        {"candidate_application_id": "candidateapp_test",
         "recipients": ["ada@example.test"],
         "payload": {
             "subject": "Re: Interview availability", "body": "Hello Ada",
             "provider_thread_id": "thread_hiring_1",
             "in_reply_to": "<candidate-reply@example.test>",
             "references": "<root@ruhu.ai> <candidate-reply@example.test>",
         }},
        "action_reply", "provider_request_reply")

    mime = message_from_bytes(base64.urlsafe_b64decode(captured["raw"]))
    assert result["status"] == "success"
    assert captured["threadId"] == "thread_hiring_1"
    assert mime["In-Reply-To"] == "<candidate-reply@example.test>"
    assert mime["References"] == (
        "<root@ruhu.ai> <candidate-reply@example.test>")


@pytest.mark.asyncio
async def test_google_reply_fails_closed_without_verified_rfc_anchor(monkeypatch):
    async def credentials(_workspace_id, _connector_id):
        return object()

    monkeypatch.setattr(
        GoogleHiringProviderAdapter, "_credentials", staticmethod(credentials))
    result = await GoogleHiringProviderAdapter()._send_email(
        "founder",
        {"candidate_application_id": "candidateapp_test",
         "recipients": ["ada@example.test"],
         "payload": {
             "subject": "Re: Interview availability", "body": "Hello Ada",
             "provider_thread_id": "thread_hiring_1",
             "in_reply_to": "bad\r\nBcc: attacker@example.test",
             "references": "",
         }},
        "action_reply", "provider_request_reply")

    assert result["error_code"] == "reply_anchor_missing"


@pytest.mark.asyncio
async def test_free_form_candidate_time_is_interpreted_then_calendar_guarded(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    mandate = await _activate_mandate(
        service, store, founder, application_id, monkeypatch)
    alternative = datetime.fromisoformat(
        mandate["confirmed_slots"][0]["start"]).replace(hour=12)

    async def interpret(_payload):
        return {
            "intent": "PROPOSE_ALTERNATIVE", "selected_option": 0,
            "proposed_start": alternative.isoformat(),
            "timezone": "Africa/Lagos", "confidence": "HIGH",
        }

    set_interpreter_fn(interpret)
    reply = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_free_form", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>",
            "subject": "Re: Interview availability",
            "excerpt": "Could we meet at noon on that day instead?",
            "kind": "update"})
    assert reply["continuation_status"] == "success"
    assert [call["action_kind"] for call in provider.calls] == [
        "HIRING_SEND_EMAIL", "HIRING_CREATE_INTERVIEW"]
    exact = provider.calls[-1]["exact_action"]
    assert exact["payload"]["start"] == alternative.isoformat()
    current = await store.get(
        "hiring_coordination_mandates", mandate["mandate_id"])
    assert current["goal_kind"] == "SCHEDULE_INTERVIEW"
    assert current["goal_status"] == "SCHEDULED"
    assert current["current_event_id"] == "calendar_event_1"


@pytest.mark.asyncio
async def test_candidate_availability_window_is_planned_against_live_calendar(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    mandate = await _activate_mandate(
        service, store, founder, application_id, monkeypatch)
    first = mandate["confirmed_slots"][0]
    window_start = datetime.fromisoformat(first["start"]).replace(hour=9)
    window_end = window_start.replace(hour=18)

    async def interpret(_payload):
        return {
            "intent": "PROPOSE_ALTERNATIVE", "selected_option": 0,
            "proposed_start": "", "proposed_starts": [],
            "availability_windows": [
                f"{window_start.isoformat()}/{window_end.isoformat()}"],
            "unavailable_windows": [], "timezone": "Africa/Lagos",
            "clarification_needed": "", "confidence": "HIGH",
        }

    set_interpreter_fn(interpret)
    reply = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_window", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>",
            "subject": "Re: Interview availability",
            "excerpt": "I can do any working hour that Tuesday.",
            "kind": "update", "automated": False,
        })

    assert reply["continuation_status"] == "success"
    assert provider.calls[-1]["action_kind"] == "HIRING_CREATE_INTERVIEW"
    assert provider.calls[-1]["exact_action"]["payload"]["start"] == first["start"]


@pytest.mark.asyncio
async def test_candidate_change_after_booking_updates_same_owned_event(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    mandate = await _activate_mandate(
        service, store, founder, application_id, monkeypatch)
    first = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_book_first", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>",
            "subject": "Re: Interview availability",
            "excerpt": "Option one works for me.", "kind": "update"})
    assert first["continuation_status"] == "success"
    changed = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_change_second", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>",
            "subject": "Re: Interview availability",
            "excerpt": "Please reschedule me to option two instead.",
            "kind": "update"})
    assert changed["continuation_status"] == "success"
    assert [call["action_kind"] for call in provider.calls] == [
        "HIRING_SEND_EMAIL", "HIRING_CREATE_INTERVIEW",
        "HIRING_UPDATE_INTERVIEW"]
    assert provider.calls[-1]["exact_action"]["payload"][
        "target_event_id"] == "calendar_event_1"
    current = await store.get(
        "hiring_coordination_mandates", mandate["mandate_id"])
    assert current["goal_status"] == "SCHEDULED"
    assert current["goal_step"] == "INTERVIEW_CONFIRMED"


@pytest.mark.asyncio
async def test_founder_copy_is_fixed_by_mandate_and_never_candidate_authority(
        founder, monkeypatch):
    monkeypatch.setenv("HIRING_FOUNDER_COPY_EMAIL", "founder@example.test")
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_copy_founder", copy_founder=True)
    item = await store.get("hiring_coordination_items", prepared["coordination_id"])
    assert item["exact_action"]["recipients"] == [
        "ada@example.test", "founder@example.test"]
    await resolve_approval(principal=founder, approval_id=prepared["approval_id"],
                           decision="GRANT", store=store)
    await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    denied = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "founder_thread_reply", "thread_id": "thread_hiring_1",
            "from": "Founder <founder@example.test>",
            "subject": "Re: Interview availability", "excerpt": "Option one",
            "kind": "update"})
    assert denied["error_code"] == "reply_not_correlatable"


@pytest.mark.asyncio
async def test_reply_email_continues_verified_thread_and_reply_stays_correlatable(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    initial = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_thread_initial")
    await resolve_approval(principal=founder, approval_id=initial["approval_id"],
                           decision="GRANT", store=store)
    await service.execute(principal=founder, application_id=application_id,
                          coordination_id=initial["coordination_id"],
                          approval_id=initial["approval_id"])
    reply = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_thread_reply", reply=True)
    item = await store.get("hiring_coordination_items", reply["coordination_id"])
    assert item["exact_action"]["payload"]["provider_thread_id"] == "thread_hiring_1"
    assert item["subject"].startswith("Re:")
    assert reply["mandate_active"] is True
    await service.execute(principal=founder, application_id=application_id,
                          coordination_id=reply["coordination_id"],
                          approval_id="")
    correlated = await service.correlate_reply(
        workspace_id="founder", provider_event={
            "id": "message_after_two_sends", "thread_id": "thread_hiring_1",
            "from": "Ada Candidate <ada@example.test>",
            "subject": "Re: Interview availability", "excerpt": "Option one works.",
            "kind": "update"})
    assert correlated["candidate_application_id"] == application_id


@pytest.mark.asyncio
async def test_active_mandate_rejects_other_thread_and_expiry_not_volume(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    mandate = await _activate_mandate(
        service, store, founder, application_id, monkeypatch)
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_wrong_thread", reply=True)
    item = await store.get(
        "hiring_coordination_items", prepared["coordination_id"])
    exact = dict(item["exact_action"])
    exact["payload"] = {
        **exact["payload"], "provider_thread_id": "unrelated_thread"}
    await store.compare_and_set(
        "hiring_coordination_items", item["coordination_id"],
        item["version"], {"exact_action": exact})
    denied = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"], approval_id="")
    assert denied["error_code"] == "coordination_mandate_invalid"
    assert len(provider.calls) == 1

    current = await store.get(
        "hiring_coordination_mandates", mandate["mandate_id"])
    await store.compare_and_set(
        "hiring_coordination_mandates", mandate["mandate_id"],
        current["version"], {"email_count": 100, "calendar_action_count": 100})
    high_volume_reply = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_high_volume", reply=True)
    sent = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=high_volume_reply["coordination_id"], approval_id="")
    assert sent["receipt_status"] == "SUCCEEDED"
    current = await store.get(
        "hiring_coordination_mandates", mandate["mandate_id"])
    slot = current["confirmed_slots"][0]
    interview = await service.prepare_interview(
        principal=founder, application_id=application_id,
        start=slot["start"], end=slot["end"],
        timezone_name=slot["timezone"],
        client_request_id="interview_high_volume")
    booked = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=interview["coordination_id"], approval_id="")
    assert booked["receipt_status"] == "SUCCEEDED"

    current = await store.get(
        "hiring_coordination_mandates", mandate["mandate_id"])
    await store.compare_and_set(
        "hiring_coordination_mandates", mandate["mandate_id"],
        current["version"], {"expires_at": "2000-01-01T00:00:00+00:00"})
    expired = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_expired", reply=True)
    assert expired["error_code"] == "coordination_mandate_required"


@pytest.mark.asyncio
async def test_uncertain_email_never_retries_without_reconciliation(founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _UncertainProvider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_uncertain")
    await resolve_approval(principal=founder, approval_id=prepared["approval_id"],
                           decision="GRANT", store=store)
    first = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert first["error_code"] == "reconciliation_required"
    second = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert second["error_code"] == "reconciliation_required"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_interview_binds_candidate_and_alex_and_rejects_unowned_change(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    mandate = await _activate_mandate(
        service, store, founder, application_id, monkeypatch)
    slot = mandate["confirmed_slots"][0]
    prepared = await service.prepare_interview(
        principal=founder, application_id=application_id,
        start=slot["start"], end=slot["end"], timezone_name=slot["timezone"],
        client_request_id="interview_request_0001")
    assert prepared["item"]["action_kind"] == "HIRING_CREATE_INTERVIEW"
    exact = (await store.get("hiring_coordination_items",
                             prepared["coordination_id"]))["exact_action"]
    assert exact["recipients"] == ["ada@example.test", "alex@ruhu.ai"]
    denied = await service.prepare_interview(
        principal=founder, application_id=application_id,
        start=slot["start"], end=slot["end"], timezone_name=slot["timezone"],
        client_request_id="interview_request_0002",
        target_event_id="not_owned")
    assert denied["error_code"] == "interview_not_found"


@pytest.mark.asyncio
async def test_one_mandate_covers_create_update_and_cancel_without_more_approvals(
        founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)
    service = _service(store, provider)
    mandate = await _activate_mandate(
        service, store, founder, application_id, monkeypatch)
    slots = mandate["confirmed_slots"]

    async def prepare_execute(*, request_id, start, end, target="", cancel=False):
        prepared = await service.prepare_interview(
            principal=founder, application_id=application_id,
            start=start.isoformat() if start else "",
            end=end.isoformat() if end else "", timezone_name="Africa/Lagos",
            client_request_id=request_id, target_event_id=target, cancel=cancel)
        assert prepared["mandate_active"] is True
        return await service.execute(
            principal=founder, application_id=application_id,
            coordination_id=prepared["coordination_id"],
            approval_id="")

    created = await prepare_execute(
        request_id="interview_create_exact",
        start=datetime.fromisoformat(slots[0]["start"]),
        end=datetime.fromisoformat(slots[0]["end"]))
    assert created["receipt_status"] == "SUCCEEDED"
    event_id = created["result_ref"]["event_id"]
    updated = await prepare_execute(
        request_id="interview_update_exact",
        start=datetime.fromisoformat(slots[1]["start"]),
        end=datetime.fromisoformat(slots[1]["end"]), target=event_id)
    assert updated["receipt_status"] == "SUCCEEDED"
    cancelled = await prepare_execute(
        request_id="interview_cancel_exact", start=None, end=None,
        target=event_id, cancel=True)
    assert cancelled["receipt_status"] == "SUCCEEDED"
    assert [call["action_kind"] for call in provider.calls] == [
        "HIRING_SEND_EMAIL",
        "HIRING_CREATE_INTERVIEW", "HIRING_UPDATE_INTERVIEW",
        "HIRING_CANCEL_INTERVIEW"]


@pytest.mark.asyncio
async def test_tampered_draft_cannot_consume_approval(founder, monkeypatch):
    store, provider = InMemoryDurableStore(), _Provider()
    application_id = await _seed(store)

    async def availability(*args, **kwargs):
        return {"status": "success", "busy": [], "window": {}}

    monkeypatch.setattr("services.calendar_adapter.check_availability", availability)
    service = _service(store, provider)
    prepared = await service.prepare_contact(
        principal=founder, application_id=application_id,
        client_request_id="contact_request_0005")
    item = await store.get("hiring_coordination_items", prepared["coordination_id"])
    exact = dict(item["exact_action"])
    exact["recipients"] = ["attacker@example.test"]
    await store.compare_and_set("hiring_coordination_items", item["coordination_id"],
                                item["version"], {"exact_action": exact})
    await resolve_approval(principal=founder, approval_id=prepared["approval_id"],
                           decision="GRANT", store=store)
    result = await service.execute(
        principal=founder, application_id=application_id,
        coordination_id=prepared["coordination_id"],
        approval_id=prepared["approval_id"])
    assert result["error_code"] == "invalid_contract"
    assert provider.calls == []
