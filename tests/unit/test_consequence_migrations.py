from __future__ import annotations

import pytest

from services.consequence_migrations import (
    migrate_internal_demo_action,
    migrate_internal_demo_approval,
    migrate_legacy_grant_approval,
)
from services.durable_store import InMemoryDurableStore

pytestmark = pytest.mark.asyncio


async def test_internal_demo_action_moves_to_platform_ledger_once():
    store = InMemoryDurableStore()
    await store.create("internal_demo_actions", "action_a", {
        "schema_version": 1, "action_id": "action_a",
        "workspace_id": "workspace_a", "demo_run_id": "demo_a",
        "status": "SUCCEEDED", "provider_effect_id": "message_a",
        "version": 1,
    })

    first = await migrate_internal_demo_action(store, "action_a")
    duplicate = await migrate_internal_demo_action(store, "action_a")

    assert first["action"]["action_domain"] == "INTERNAL_CONTROLLED_DEMO"
    assert first["action"]["provider_effect_id"] == "message_a"
    assert duplicate["duplicate"] is True
    assert (await store.get("internal_demo_actions", "action_a"))[
        "migrated_to"] == "external_actions"


async def test_internal_demo_action_migration_refuses_identity_collision():
    store = InMemoryDurableStore()
    await store.create("internal_demo_actions", "action_a", {
        "workspace_id": "workspace_a", "status": "PREPARED", "version": 1})
    await store.create("external_actions", "action_a", {
        "workspace_id": "workspace_b", "status": "SUCCEEDED", "version": 1})

    result = await migrate_internal_demo_action(store, "action_a")

    assert result["error_code"] == "idempotency_conflict"


async def test_internal_demo_approval_moves_to_shared_collection_once():
    store = InMemoryDurableStore()
    await store.create("internal_demo_approvals", "approval_a", {
        "workspace_id": "workspace_a", "status": "GRANTED",
        "subject_hash": "sha256:test", "version": 1})

    first = await migrate_internal_demo_approval(store, "approval_a")
    duplicate = await migrate_internal_demo_approval(store, "approval_a")

    assert first["approval"]["approval_domain"] == \
        "INTERNAL_CONTROLLED_DEMO"
    assert duplicate["duplicate"] is True


async def test_open_legacy_grant_approval_expires_instead_of_gaining_authority():
    store = InMemoryDurableStore()
    await store.create("approvals", "approval_old", {
        "schema_version": 1, "founder_id": "workspace_a",
        "application_id": "email:general", "gate": "send_email",
        "subject_hash": "sha256:subject", "status": "GRANTED",
        "token": "legacy-token", "version": 1,
    })

    migrated = await migrate_legacy_grant_approval(store, "approval_old")
    duplicate = await migrate_legacy_grant_approval(store, "approval_old")

    assert migrated["approval"]["schema_version"] == 2
    assert migrated["approval"]["status"] == "EXPIRED"
    assert migrated["approval"]["token"] is None
    assert migrated["approval"]["capability_id"] == "external.send_email"
    assert migrated["receipt"]["disposition"] == \
        "EXPIRED_REAPPROVAL_REQUIRED"
    assert duplicate["duplicate"] is True
