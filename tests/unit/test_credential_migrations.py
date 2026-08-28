from __future__ import annotations

import pytest

from services import google_oauth
from services.credential_migrations import retire_legacy_google_credential
from services.durable_store import InMemoryDurableStore

pytestmark = pytest.mark.asyncio


async def test_legacy_google_credential_requires_verified_reconnect(monkeypatch):
    store = InMemoryDurableStore()
    connection_id = "conn_founder_gmail"
    await store.create("data_connections", connection_id, {
        "connection_id": connection_id,
        "workspace_id": "workspace_a", "founder_id": "workspace_a",
        "connector_id": "founder_gmail", "auth_kind": "google_oauth",
        "credential_ref": "GOOGLE_REFRESH_TOKEN", "status": "CONNECTED",
        "version": 1,
    })
    monkeypatch.setattr(google_oauth, "runtime_value", lambda _key: pytest.fail(
        "migration must not read credential values"))

    result = await retire_legacy_google_credential(
        store, workspace_id="workspace_a", account="founder")
    duplicate = await retire_legacy_google_credential(
        store, workspace_id="workspace_a", account="founder")

    row = await store.get("data_connections", connection_id)
    assert result["receipt"]["credential_values_read"] is False
    assert row["credential_ref"] == google_oauth.credential_ref(
        "founder", "workspace_a")
    assert row["status"] == "REAUTH_REQUIRED"
    assert duplicate["duplicate"] is True


async def test_credential_migration_refuses_cross_workspace_owner():
    store = InMemoryDurableStore()
    await store.create("data_connections", "conn", {
        "connection_id": "conn", "workspace_id": "workspace_a",
        "founder_id": "workspace_b", "connector_id": "founder_gmail",
        "auth_kind": "google_oauth", "version": 1,
    })
    result = await retire_legacy_google_credential(
        store, workspace_id="workspace_a", account="founder")
    assert result["error_code"] == "migration_owner_ambiguous"
