"""Google connector consent verification regressions."""

from __future__ import annotations

from types import SimpleNamespace

from services import google_oauth


class _Request:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _OAuth2Service:
    def __init__(self, scopes: list[str], email: str):
        self.scopes = scopes
        self.email = email

    def tokeninfo(self, *, access_token: str):
        assert access_token == "access-token"
        return _Request({"scope": " ".join(self.scopes)})

    def userinfo(self):
        return self

    def get(self):
        return _Request({"email": self.email, "id": "provider-subject"})


def test_alex_calendar_verifies_identity_without_calendar_list_scope(monkeypatch):
    scopes = google_oauth.SCOPE_MAP["alex_calendar"]
    oauth2 = _OAuth2Service(scopes, "alex@ruhu.ai")

    def build(api, version, **_kwargs):
        assert (api, version) == ("oauth2", "v2")
        return oauth2

    monkeypatch.setattr("googleapiclient.discovery.build", build)

    result = google_oauth.verify_consent(
        SimpleNamespace(token="access-token"), "alex_calendar")

    assert result == {
        "status": "success",
        "granted_scopes": sorted(set(scopes)),
        "account_hint": "a***@ruhu.ai",
        "provider_account_hash": google_oauth.provider_account_hash("alex@ruhu.ai"),
    }


def test_alex_calendar_rejects_founder_account_in_role_slot(monkeypatch):
    scopes = google_oauth.SCOPE_MAP["alex_calendar"]
    oauth2 = _OAuth2Service(scopes, "founder@ruhu.ai")
    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda *_args, **_kwargs: oauth2,
    )

    result = google_oauth.verify_consent(
        SimpleNamespace(token="access-token"), "alex_calendar")

    assert result["status"] == "error"
    assert result["error_code"] == "account_mismatch"
    assert "founder@ruhu.ai" not in result["message"]


def test_calendar_verification_fails_closed_when_userinfo_has_no_email(monkeypatch):
    scopes = google_oauth.SCOPE_MAP["calendar"]
    oauth2 = _OAuth2Service(scopes, "")
    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda *_args, **_kwargs: oauth2,
    )

    result = google_oauth.verify_consent(
        SimpleNamespace(token="access-token"), "calendar")

    assert result["status"] == "error"
    assert result["error_code"] == "provider_rejected"


def test_workspace_credential_slots_are_distinct_and_opaque(monkeypatch):
    writes = []
    monkeypatch.setattr(
        google_oauth, "save_env_var",
        lambda key, value: writes.append((key, value)) or {"status": "success"})

    assert google_oauth.save_refresh_token("token-a", "founder", "workspace-a")[
        "status"] == "success"
    assert google_oauth.save_refresh_token("token-b", "founder", "workspace-b")[
        "status"] == "success"

    assert writes[0][0] != writes[1][0]
    assert "workspace-a" not in writes[0][0]
    assert "workspace-b" not in writes[1][0]


def test_scoped_credential_never_falls_back_to_legacy_global_token(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "legacy-global-token")
    google_oauth.reset_for_tests()

    assert google_oauth._refresh_token("founder") == "legacy-global-token"
    assert google_oauth._refresh_token("founder", "workspace-a") == ""
