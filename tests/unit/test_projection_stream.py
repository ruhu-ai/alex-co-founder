from __future__ import annotations

import pytest

from services.durable_store import InMemoryDurableStore
from services.projection_stream import (
    ProjectionEventService,
    sse_event,
    workspace_revision,
)

pytestmark = pytest.mark.asyncio


async def test_projection_sequence_replay_and_cursor_are_workspace_scoped():
    store = InMemoryDurableStore()
    service = ProjectionEventService(store)
    first = await service.publish(
        workspace_id="workspace_a", projection_type="run",
        aggregate_id="run_1", aggregate_version=1,
        safe_payload={"runtime_status": "QUEUED"}, run_id="run_1",
        idempotency_key="run-created")
    second = await service.publish(
        workspace_id="workspace_a", projection_type="run",
        aggregate_id="run_1", aggregate_version=2,
        safe_payload={"runtime_status": "WAITING"}, run_id="run_1",
        idempotency_key="wait-opened")
    await service.publish(
        workspace_id="workspace_b", projection_type="run",
        aggregate_id="run_b", aggregate_version=1,
        safe_payload={"runtime_status": "QUEUED"}, run_id="run_b",
        idempotency_key="run-created")

    cursor = await service.resolve_cursor(
        workspace_id="workspace_a", event_id=first["event_id"])
    replay = await service.replay(
        workspace_id="workspace_a", after_sequence=cursor["sequence"])

    assert [row["event_id"] for row in replay["events"]] == [second["event_id"]]
    assert workspace_revision("workspace_a") >= 2
    assert "workspace_b" not in sse_event(replay["events"][0])
    foreign = await service.resolve_cursor(
        workspace_id="workspace_b", event_id=first["event_id"])
    assert foreign["error_code"] == "foreign_cursor"


async def test_duplicate_publish_is_stable_and_changed_subject_conflicts():
    service = ProjectionEventService(InMemoryDurableStore())
    kwargs = dict(
        workspace_id="workspace_a", projection_type="approval",
        aggregate_id="approval_1", aggregate_version=1,
        safe_payload={"status": "PENDING"},
        idempotency_key="approval-request")
    first = await service.publish(**kwargs)
    duplicate = await service.publish(**kwargs)
    conflict = await service.publish(**{**kwargs, "aggregate_version": 2})

    assert first["sequence"] == 1
    assert duplicate["duplicate"] is True
    assert conflict["error_code"] == "idempotency_conflict"


async def test_replay_overflow_requires_snapshot_without_unbounded_buffer():
    service = ProjectionEventService(InMemoryDurableStore())
    for index in range(4):
        await service.publish(
            workspace_id="workspace_a", projection_type="command",
            aggregate_id=f"command_{index}", aggregate_version=1,
            safe_payload={"status": "COMPLETED"},
            idempotency_key=f"command-{index}")

    replay = await service.replay(
        workspace_id="workspace_a", after_sequence=0, limit=2)

    assert len(replay["events"]) == 2
    assert replay["snapshot_required"] is True


async def test_missing_cursor_requests_snapshot_and_payload_is_bounded():
    service = ProjectionEventService(InMemoryDurableStore())
    missing = await service.resolve_cursor(
        workspace_id="workspace_a", event_id="projection_missing")
    too_large = await service.publish(
        workspace_id="workspace_a", projection_type="message",
        aggregate_id="message_1", aggregate_version=1,
        safe_payload={"text": "x" * 9000}, idempotency_key="too-large")

    assert missing["error_code"] == "snapshot_required"
    assert too_large["error_code"] == "projection_payload_too_large"
