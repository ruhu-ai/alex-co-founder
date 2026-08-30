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


class _GmailService:
    def __init__(self, email: str):
        self.email = email

    def users(self):
        return self

    def getProfile(self, *, userId: str):
        assert userId == "me"
        return _Request({"emailAddress": self.email})


def test_alex_calendar_verifies_identity_without_calendar_list_scope(monkeypatch):
    scopes = google_oauth.SCOPE_MAP["alex_calendar"]
    # Google echoes the OIDC alias ``email`` even though the connector asks
    # for the equivalent userinfo.email scope.
    oauth2 = _OAuth2Service([*scopes, "email"], "alex@ruhu.ai")

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


def test_founder_calendar_accepts_only_google_email_alias(monkeypatch):
    scopes = google_oauth.SCOPE_MAP["calendar"]
    oauth2 = _OAuth2Service([*scopes, "email"], "founder@ruhu.ai")
    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda *_args, **_kwargs: oauth2,
    )

    result = google_oauth.verify_consent(
        SimpleNamespace(token="access-token"), "calendar")

    assert result["status"] == "success"
    assert result["granted_scopes"] == sorted(set(scopes))


def test_alex_mail_accepts_google_email_alias_but_no_other_scope(monkeypatch):
    scopes = google_oauth.SCOPE_MAP["alex_mail"]
    oauth2 = _OAuth2Service([*scopes, "email"], "alex@ruhu.ai")
    gmail = _GmailService("alex@ruhu.ai")

    def build(api, _version, **_kwargs):
        return oauth2 if api == "oauth2" else gmail

    monkeypatch.setattr("googleapiclient.discovery.build", build)

    accepted = google_oauth.verify_consent(
        SimpleNamespace(token="access-token"), "alex_mail")
    assert accepted["status"] == "success"
    assert accepted["granted_scopes"] == sorted(set(scopes))

    oauth2.scopes.append("https://www.googleapis.com/auth/drive")
    rejected = google_oauth.verify_consent(
        SimpleNamespace(token="access-token"), "alex_mail")
    assert rejected["error_code"] == "scope_excess"


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


def test_alex_drive_rejects_inherited_scope_before_token_persistence(monkeypatch):
    scopes = [
        *google_oauth.SCOPE_MAP["alex_drive"],
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    oauth2 = _OAuth2Service(scopes, "alex@ruhu.ai")
    monkeypatch.setattr(
        "googleapiclient.discovery.build",
        lambda *_args, **_kwargs: oauth2,
    )

    result = google_oauth.verify_consent(
        SimpleNamespace(token="access-token"), "alex_drive")

    assert result == {
        "status": "error",
        "error": True,
        "error_code": "scope_excess",
        "message": "Google returned access outside this connector's scope contract",
    }


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
