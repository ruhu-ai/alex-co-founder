from __future__ import annotations

import pytest

from services import (
    alex_mailbox,
    capability_registry,
    connection_registry,
    connectors,
    drive_adapter,
    google_oauth,
)
from services import data_source_contracts as dsc


def test_alex_role_account_scopes_are_operational_without_account_admin():
    assert "https://www.googleapis.com/auth/gmail.send" in google_oauth.SCOPE_MAP[
        "founder_gmail"
    ]
    assert "https://www.googleapis.com/auth/calendar.readonly" in google_oauth.SCOPE_MAP[
        "alex_calendar"
    ]
    assert google_oauth.SCOPE_MAP["alex_mail"] == [
        "https://www.googleapis.com/auth/gmail.modify",
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
    ]
    assert google_oauth.SCOPE_MAP["alex_drive"] == [
        "https://www.googleapis.com/auth/drive",
    ]
    assert google_oauth.SCOPE_MAP["alex_drive"] != google_oauth.SCOPE_MAP["drive"]
    every_scope = {scope for scopes in google_oauth.SCOPE_MAP.values() for scope in scopes}
    assert "https://mail.google.com/" not in every_scope
    assert "https://www.googleapis.com/auth/gmail.settings.basic" not in every_scope
    assert "https://www.googleapis.com/auth/gmail.settings.sharing" not in every_scope
    assert "https://www.googleapis.com/auth/calendar" not in every_scope


def test_alex_drive_is_a_separate_account_bound_closed_connector():
    assert connection_registry.account_for_connector("alex_drive") == "alex"
    contract = dsc.CONNECTOR_REGISTRY[dsc.ConnectorId.ALEX_DRIVE]
    assert dsc.DataSourceRole.KNOWLEDGE in contract.roles
    assert dsc.DataSourceRole.ACTION_DESTINATION in contract.roles
    descriptor = next(row for row in connectors.DESCRIPTORS if row["name"] == "alex_drive")
    assert descriptor["title"] == "Alex's Google Drive"
    assert "Alex-owned Drive files" in descriptor["blurb"]
    capability_registry.require_external_action(
        "export_alex_drive_file", "alex_drive")


def test_alex_drive_adapter_selects_only_the_alex_credential(monkeypatch):
    observed = []

    def credentials(account, workspace_id):
        observed.append((account, workspace_id))
        return None

    monkeypatch.setattr(google_oauth, "get_credentials", credentials)
    drive_adapter.set_service_factory(None)
    assert drive_adapter._service("workspace-a", "alex_drive") is None
    assert observed == [("alex", "workspace-a")]


@pytest.mark.asyncio
async def test_founder_gmail_send_creates_exact_separate_approval(fake_store, monkeypatch):
    alex_mailbox.set_service_factory(lambda: object())

    async def audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr("services.alex_mailbox.firestore.audit", audit)
    result = await alex_mailbox.send_email(
        "synthetic@example.test",
        "Synthetic subject",
        "Synthetic body",
        founder_id="founder",
        session_id="session-1",
        connector_id="founder_gmail",
    )
    assert result["status"] == "needs_approval"
    approval = fake_store.approvals[result["approval_id"]]
    assert approval["gate"] == "send_founder_email"
    assert approval["application_id"] == "founder_email:general"
    alex_mailbox.set_service_factory(None)
