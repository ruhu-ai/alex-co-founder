from __future__ import annotations

from pathlib import Path

import pytest

from services import firestore
from services.durable_store import InMemoryDurableStore
from services.tenancy_migrations import DOMAIN_FIELDS, migrate_tenant_row

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("collection", sorted(DOMAIN_FIELDS))
async def test_tenancy_backfill_is_atomic_receipted_and_idempotent(collection: str):
    store = InMemoryDurableStore()
    await store.create(collection, "row_a", {
        "founder_id": "workspace_a", "version": 1})

    migrated = await migrate_tenant_row(
        store, collection=collection, document_id="row_a")
    duplicate = await migrate_tenant_row(
        store, collection=collection, document_id="row_a")

    domain_field, default_domain = DOMAIN_FIELDS[collection]
    assert migrated["row"]["workspace_id"] == "workspace_a"
    assert migrated["row"][domain_field] == default_domain
    assert duplicate["duplicate"] is True
    receipts = await store.list(
        "tenancy_migration_receipts",
        filters={"workspace_id": "workspace_a"}, limit=20)
    assert len(receipts) == 1


async def test_tenancy_backfill_refuses_ambiguous_owner():
    store = InMemoryDurableStore()
    await store.create("approvals", "approval_a", {"version": 1})
    result = await migrate_tenant_row(
        store, collection="approvals", document_id="approval_a")
    assert result["error_code"] == "migration_owner_ambiguous"


async def test_tenancy_backfill_receipts_explicit_owner_for_unowned_row():
    store = InMemoryDurableStore()
    await store.create("opportunities", "legacy", {"version": 1})
    result = await migrate_tenant_row(
        store, collection="opportunities", document_id="legacy",
        owner_override="workspace_a")
    assert result["row"]["workspace_id"] == "workspace_a"
    assert result["receipt"]["owner_assignment"] == "EXPLICIT_OPERATOR_OVERRIDE"


async def test_tenancy_backfill_refuses_override_of_existing_owner():
    store = InMemoryDurableStore()
    await store.create("applications", "app", {
        "founder_id": "workspace_a", "version": 1})
    result = await migrate_tenant_row(
        store, collection="applications", document_id="app",
        owner_override="workspace_b")
    assert result["error_code"] == "migration_owner_conflict"


async def test_tenancy_backfill_accepts_pre_version_legacy_row():
    store = InMemoryDurableStore()
    # InMemoryDurableStore supplies version=1 on create. Replace the fixture
    # through its explicit test store to model Firestore documents that predate
    # the optimistic-version convention.
    store.records["approvals"] = {
        "approval_old": {"founder_id": "workspace_a"}}

    result = await migrate_tenant_row(
        store, collection="approvals", document_id="approval_old")

    assert result["row"]["workspace_id"] == "workspace_a"
    assert result["row"]["version"] == 1


async def test_application_point_read_hides_foreign_workspace(fake_store):
    fake_store.applications["app_a"] = {
        "id": "app_a", "founder_id": "workspace_a", "state": "DRAFTING"}

    own = await firestore.get_application("app_a", "workspace_a")
    foreign = await firestore.get_application("app_a", "workspace_b")

    assert own and own["state"] == "DRAFTING"
    assert foreign is None


async def test_approval_point_read_hides_foreign_workspace(fake_store):
    fake_store.approvals["approval_a"] = {
        "id": "approval_a", "workspace_id": "workspace_a",
        "founder_id": "workspace_a", "status": "PENDING"}

    own = await firestore.get_approval_for_workspace(
        "workspace_a", "approval_a")
    foreign = await firestore.get_approval_for_workspace(
        "workspace_b", "approval_a")

    assert own and own["status"] == "PENDING"
    assert foreign is None


async def test_opportunity_reads_and_lists_hide_foreign_workspace(fake_store):
    fake_store.opportunities.update({
        "opp_a": {"id": "opp_a", "workspace_id": "workspace_a",
                  "founder_id": "workspace_a", "state": "DISCOVERED",
                  "created_at": "2026-01-01T00:00:00+00:00"},
        "opp_b": {"id": "opp_b", "workspace_id": "workspace_b",
                  "founder_id": "workspace_b", "state": "DISCOVERED",
                  "created_at": "2026-01-02T00:00:00+00:00"},
    })
    assert await firestore.get_opportunity("opp_b", "workspace_a") is None
    rows = await firestore.list_opportunities(founder_id="workspace_a")
    assert [row["id"] for row in rows] == ["opp_a"]


async def test_agent_tools_never_default_durable_scope_to_global_founder():
    root = Path(__file__).resolve().parents[2] / "agents" / "co_founder"
    offenders = []
    for path in [root / "callbacks.py", *(root / "tools").glob("*.py")]:
        text = path.read_text()
        if 'get(ss.K_USER_PROFILE_ID, "founder")' in text \
                or 'get("user:profile_id", "founder")' in text \
                or 'os.environ.get("FOUNDER_ID"' in text:
            offenders.append(str(path.relative_to(root.parents[1])))
    assert offenders == []
