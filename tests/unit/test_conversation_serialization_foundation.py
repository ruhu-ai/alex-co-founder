from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.background_work import (
    FOUNDATION_TEMPLATE,
    BackgroundJobRequest,
    BackgroundWorkService,
    enabled_foundation_templates,
)
from services.conversation_serialization import (
    UNPROVED_WRITE_SURFACE,
    ADKWriteSurfaceManifest,
    ConversationDeliveryService,
    InMemoryConversationSequencePort,
    UnavailableConversationSequencePort,
    attest_write_surface,
    current_foundation_fingerprint,
)
from services.durable_store import InMemoryDurableStore

pytestmark = pytest.mark.asyncio


class ManualClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _principal(actor: str = "actor_alex") -> ActorPrincipal:
    return ActorPrincipal(
        actor_id=actor, workspace_id="workspace_alpha",
        role=WorkspaceRole.FOUNDER, session_auth_time=1,
        membership_version=1)


def _request() -> BackgroundJobRequest:
    return BackgroundJobRequest(
        client_request_id="background-delivery-0001",
        template_id=FOUNDATION_TEMPLATE.template_id,
        template_version=FOUNDATION_TEMPLATE.version,
        objective_kind=FOUNDATION_TEMPLATE.objective_kind,
        objective_summary="Prepare a bounded internal outline.",
        origin_message_id="message-origin-001",
        origin_session_id="session-origin-001")


async def _job(store: InMemoryDurableStore) -> str:
    accepted = await BackgroundWorkService(
        store, templates=enabled_foundation_templates(),
        admission_enabled=True).accept(
            principal=_principal(), request=_request())
    return str(accepted["run_id"])


def _proof() -> tuple[ADKWriteSurfaceManifest, str]:
    manifest = replace(
        UNPROVED_WRITE_SURFACE,
        proof_status="PROVED",
        fence_implementation_hash="sha256:" + "c" * 64,
        test_evidence_hash="sha256:" + "d" * 64)
    return manifest, manifest.proof_hash


async def test_production_sequence_port_and_unproved_manifest_fail_closed():
    unavailable = await UnavailableConversationSequencePort().reserve(
        session_id="session-origin-001")
    disabled = await ConversationDeliveryService(
        InMemoryDurableStore()).reserve(
            principal=_principal(), run_id="run-background-missing",
            session_id="session-origin-001", causal_event_id="event-001",
            kind="MILESTONE", safe_caption_code="BACKGROUND_MILESTONE")
    manifest = UNPROVED_WRITE_SURFACE
    attestation = attest_write_surface(
        fingerprint=current_foundation_fingerprint(), manifest=manifest,
        expected_proof_hash=manifest.proof_hash)

    assert unavailable["error_code"] == "session_serializer_unavailable"
    assert unavailable["fallback"] == "INBOX_RUN_CARD"
    assert disabled["error_code"] == "background_conversation_delivery_disabled"
    assert attestation["error_code"] == "adk_write_surface_unproved"


async def test_delivery_reservation_requires_enabled_session_and_is_idempotent():
    store = InMemoryDurableStore()
    run_id = await _job(store)
    port = InMemoryConversationSequencePort()
    service = ConversationDeliveryService(
        store, sequence_port=port, delivery_enabled=True)
    unavailable = await service.reserve(
        principal=_principal(), run_id=run_id,
        session_id="session-origin-001", causal_event_id="event-001",
        kind="MILESTONE", safe_caption_code="BACKGROUND_MILESTONE")
    await port.set_mode("session-origin-001", "ENABLED")
    first = await service.reserve(
        principal=_principal(), run_id=run_id,
        session_id="session-origin-001", causal_event_id="event-001",
        kind="MILESTONE", safe_caption_code="BACKGROUND_MILESTONE")
    duplicate = await service.reserve(
        principal=_principal(), run_id=run_id,
        session_id="session-origin-001", causal_event_id="event-001",
        kind="MILESTONE", safe_caption_code="BACKGROUND_MILESTONE")
    second = await service.reserve(
        principal=_principal(), run_id=run_id,
        session_id="session-origin-001", causal_event_id="event-002",
        kind="TERMINAL", safe_caption_code="BACKGROUND_COMPLETE")

    assert unavailable["error_code"] == "session_serializer_not_enabled"
    assert first["delivery"]["sequence"] == 1
    assert duplicate["duplicate"] is True
    assert duplicate["delivery"]["sequence"] == 1
    assert second["delivery"]["sequence"] == 2


async def test_actor_private_delivery_and_proof_gate_block_claim():
    store = InMemoryDurableStore()
    run_id = await _job(store)
    port = InMemoryConversationSequencePort()
    await port.set_mode("session-origin-001", "ENABLED")
    service = ConversationDeliveryService(
        store, sequence_port=port, delivery_enabled=True)
    reserved = await service.reserve(
        principal=_principal(), run_id=run_id,
        session_id="session-origin-001", causal_event_id="event-001",
        kind="FAILURE", safe_caption_code="BACKGROUND_TERMINAL_FAILURE")
    delivery_id = reserved["delivery"]["delivery_id"]
    hidden_reserve = await service.reserve(
        principal=_principal("actor_other"), run_id=run_id,
        session_id="session-origin-001", causal_event_id="event-002",
        kind="MILESTONE", safe_caption_code="BACKGROUND_MILESTONE")
    unproved = await service.claim(
        workspace_id="workspace_alpha", actor_id="actor_alex",
        delivery_id=delivery_id, lease_owner="delivery-worker")

    assert hidden_reserve["error_code"] == "conversation_delivery_invalid"
    assert unproved["error_code"] == "adk_write_surface_unproved"


async def test_turn_lease_serializes_and_generation_fences_stale_append():
    store = InMemoryDurableStore()
    run_id = await _job(store)
    clock = ManualClock()
    port = InMemoryConversationSequencePort(clock=clock)
    await port.set_mode("session-origin-001", "ENABLED")
    manifest, proof_hash = _proof()
    service = ConversationDeliveryService(
        store, sequence_port=port, clock=clock,
        fingerprint=manifest.fingerprint, manifest=manifest,
        expected_proof_hash=proof_hash, delivery_enabled=True)
    reserved = await service.reserve(
        principal=_principal(), run_id=run_id,
        session_id="session-origin-001", causal_event_id="event-001",
        kind="TERMINAL", safe_caption_code="BACKGROUND_COMPLETE")
    delivery_id = reserved["delivery"]["delivery_id"]
    first = await service.claim(
        workspace_id="workspace_alpha", actor_id="actor_alex",
        delivery_id=delivery_id, lease_owner="worker-one", lease_seconds=5)
    conflict = await service.claim(
        workspace_id="workspace_alpha", actor_id="actor_alex",
        delivery_id=delivery_id, lease_owner="worker-two", lease_seconds=5)
    clock.advance(6)
    reclaimed = await service.claim(
        workspace_id="workspace_alpha", actor_id="actor_alex",
        delivery_id=delivery_id, lease_owner="worker-two", lease_seconds=5)
    stale = await service.complete(
        workspace_id="workspace_alpha", actor_id="actor_alex",
        delivery_id=delivery_id, lease_owner="worker-one",
        generation=first["delivery"]["lease_generation"],
        append_receipt={
            "delivery_id": delivery_id, "event_id": "adk-event-old",
            "event_ordinal": 1,
            "turn_lease_generation": first["delivery"]["turn_lease_generation"],
            "generation_fenced": True,
        })
    completed = await service.complete(
        workspace_id="workspace_alpha", actor_id="actor_alex",
        delivery_id=delivery_id, lease_owner="worker-two",
        generation=reclaimed["delivery"]["lease_generation"],
        append_receipt={
            "delivery_id": delivery_id, "event_id": "adk-event-new",
            "event_ordinal": 1,
            "turn_lease_generation":
                reclaimed["delivery"]["turn_lease_generation"],
            "generation_fenced": True,
        })

    assert conflict["error_code"] == "delivery_lease_conflict"
    assert reclaimed["delivery"]["lease_generation"] == 2
    assert stale["error_code"] == "append_receipt_invalid"
    assert completed["delivery"]["status"] == "DELIVERED"


async def test_append_requires_current_generation_fenced_receipt():
    store = InMemoryDurableStore()
    run_id = await _job(store)
    port = InMemoryConversationSequencePort()
    await port.set_mode("session-origin-001", "ENABLED")
    manifest, proof_hash = _proof()
    service = ConversationDeliveryService(
        store, sequence_port=port, fingerprint=manifest.fingerprint,
        manifest=manifest, expected_proof_hash=proof_hash,
        delivery_enabled=True)
    reserved = await service.reserve(
        principal=_principal(), run_id=run_id,
        session_id="session-origin-001", causal_event_id="event-001",
        kind="MILESTONE", safe_caption_code="BACKGROUND_MILESTONE")
    delivery_id = reserved["delivery"]["delivery_id"]
    claimed = await service.claim(
        workspace_id="workspace_alpha", actor_id="actor_alex",
        delivery_id=delivery_id, lease_owner="worker-one")
    invalid = await service.complete(
        workspace_id="workspace_alpha", actor_id="actor_alex",
        delivery_id=delivery_id, lease_owner="worker-one",
        generation=claimed["delivery"]["lease_generation"],
        append_receipt={
            "delivery_id": delivery_id, "event_id": "adk-event-001",
            "event_ordinal": 1,
            "turn_lease_generation":
                claimed["delivery"]["turn_lease_generation"],
            "generation_fenced": False,
        })

    assert invalid["error_code"] == "append_receipt_invalid"
    current = await store.get("conversation_deliveries", delivery_id)
    assert current["status"] == "LEASED"
