"""WI-2 durable connection, grant, and disconnect invariants (docs/24)."""

from __future__ import annotations

from pathlib import Path

import pytest

from services import connection_registry, connectors, firestore, google_oauth

pytestmark = pytest.mark.asyncio


async def _connection(founder: str, connector: str, scopes=None):
    is_alex = connector in {"alex_mail", "alex_calendar"}
    return await firestore.upsert_data_connection(
        founder, connector,
        account_ref=("alex-role-mailbox" if is_alex
                     else "default"),
        auth_kind="google_oauth",
        credential_ref=("ALEX_OAUTH_REFRESH_TOKEN" if is_alex
                        else "GOOGLE_OAUTH_REFRESH_TOKEN"),
        granted_scopes=scopes or google_oauth.SCOPE_MAP[connector],
        status="CONNECTED")


async def test_verified_consent_projects_each_scope_without_render_probe(fake_store):
    granted = sorted({scope for connector in ("drive", "founder_gmail")
                      for scope in google_oauth.SCOPE_MAP[connector]})
    result = await connection_registry.project_verified_consent(
        "founder", "drive", "founder", account_hint="f***@example.com",
        granted_scopes=granted)

    assert result["status"] == "success"
    assert {row["connector_id"] for row in result["connections"]} == {
        "drive", "founder_gmail"}
    assert all(row["last_verified_at"] for row in result["connections"])

    # A panel/catalogue read consumes only those durable rows.
    projection = await connection_registry.list_connection_status("founder")
    catalog = connectors.catalog(projection["connections"])
    assert next(row for row in catalog if row["name"] == "drive")["connected"]


async def test_alex_calendar_is_a_separate_role_account_connector(fake_store):
    result = await connection_registry.project_verified_consent(
        "founder", "alex_calendar", "alex", account_hint="a***@ruhu.ai",
        granted_scopes=google_oauth.SCOPE_MAP["alex_calendar"])

    assert result["status"] == "success"
    assert [row["connector_id"] for row in result["connections"]] == ["alex_calendar"]
    projection = await connection_registry.list_connection_status("founder")
    catalog = connectors.catalog(projection["connections"])
    calendar = next(row for row in catalog if row["name"] == "alex_calendar")
    assert calendar["connected"] is True


async def test_stale_failure_cannot_overwrite_later_reconnect(fake_store):
    first = await _connection("founder", "drive")
    reconnected = await firestore.upsert_data_connection(
        "founder", "drive", account_ref="default", auth_kind="google_oauth",
        status="CONNECTED", expected_version=first["version"])

    stale = await firestore.transition_data_connection(
        "founder", first["connection_id"], expected_version=first["version"],
        status="REAUTH_REQUIRED", error_code="auth_required")

    assert stale["error_code"] == "version_conflict"
    current = await firestore.get_data_connection(
        "founder", first["connection_id"])
    assert current["version"] == reconnected["version"]
    assert current["status"] == "CONNECTED"


async def test_shared_account_disconnect_is_local_only_and_revokes_sources(
        fake_store, monkeypatch):
    drive = await _connection("founder", "drive")
    await _connection("founder", "founder_gmail")
    grant = await firestore.create_source_grant(
        "founder", drive["connection_id"], "file-1", display_name="Deck",
        allowed_ingestion_scopes=["profile"])

    monkeypatch.setattr(google_oauth, "revoke_account_grant",
                        lambda *_args, **_kwargs: pytest.fail("remote revoke"))
    monkeypatch.setattr(google_oauth, "delete_account_credential",
                        lambda *_args, **_kwargs: pytest.fail("credential delete"))
    result = await connection_registry.disconnect_connection(
        "founder", drive["connection_id"], expected_version=drive["version"])

    assert result["outcome"] == "LOCAL_ONLY"
    assert result["provider_revoked"] is False
    assert result["other_connectors"] == ["founder_gmail"]
    assert fake_store.source_grants[grant["source_grant_id"]]["status"] == "REVOKED"


async def test_last_connector_uncertain_is_honest_and_locally_disabled(
        fake_store, monkeypatch):
    drive = await _connection("founder", "drive")
    monkeypatch.setattr(
        google_oauth, "revoke_account_grant",
        lambda *_args, **_kwargs: {
            "status": "error", "error": True,
            "error_code": "remote_revocation_uncertain"})
    monkeypatch.setattr(google_oauth, "delete_account_credential",
                        lambda *_args, **_kwargs: {"status": "success"})

    result = await connection_registry.disconnect_connection(
        "founder", drive["connection_id"], expected_version=drive["version"])

    assert result["outcome"] == "UNCERTAIN"
    assert result["action_required"] is True
    assert result["local_access_removed"] is True
    assert "revoked" not in result["message"].lower()
    current = await firestore.get_data_connection("founder", drive["connection_id"])
    assert current["status"] == "DISCONNECTED"
    assert current["last_error_code"] == "remote_revocation_uncertain"


async def test_github_is_unavailable_and_has_no_credential_route_or_secret_name():
    row = next(item for item in connectors.catalog() if item["name"] == "github")
    assert row["available"] is False
    assert row["connected"] is False
    root = Path(__file__).resolve().parents[2]
    runtime = "\n".join((root / path).read_text(encoding="utf-8") for path in (
        "app/main.py", "services/connectors.py", "scripts/deploy.sh"))
    assert "/api/connectors/github/token" not in runtime
    assert "GITHUB_TOKEN" not in runtime
    assert "GITHUB_LOGIN" not in runtime
