"""Portal access tests (docs/17): credential store, email verification loop."""

import pytest

from services import alex_mailbox, portal_accounts

pytestmark = pytest.mark.asyncio


@pytest.fixture
def secrets_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_SECRETS_FILE", str(tmp_path / "secrets.json"))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    portal_accounts._CACHE.clear()
    yield tmp_path
    portal_accounts._CACHE.clear()


class TestPortalAccounts:
    def test_password_complexity(self):
        pw = portal_accounts.generate_password()
        assert len(pw) >= 24
        assert any(c.islower() for c in pw) and any(c.isupper() for c in pw)
        assert any(c.isdigit() for c in pw) and any(c in "!@#$%^&*" for c in pw)

    def test_store_fetch_delete(self, secrets_tmp):
        assert portal_accounts.store_credential("example.co", "alex@ruhu.ai", "pw123!X")["status"] == "success"
        cred = portal_accounts.get_credential("example.co")
        assert cred["email"] == "alex@ruhu.ai" and cred["password"] == "pw123!X"
        # metadata listing never leaks the password
        listed = portal_accounts.list_portals()
        assert listed["example.co"]["email"] == "alex@ruhu.ai"
        assert "password" not in listed["example.co"]
        portal_accounts.delete_credential("example.co")
        assert portal_accounts.get_credential("example.co") is None

    def test_unknown_host_is_none(self, secrets_tmp):
        assert portal_accounts.get_credential("nope.example") is None

    def test_persists_across_cache_clear(self, secrets_tmp):
        portal_accounts.store_credential("persist.example", "a@b.co", "pw!X1")
        portal_accounts._CACHE.clear()  # simulate restart
        cred = portal_accounts.get_credential("persist.example")
        assert cred and cred["password"] == "pw!X1"

    def test_pending_verification_metadata_never_leaks_password(self, secrets_tmp):
        portal_accounts.store_credential(
            "portal.example", "alex@ruhu.ai", "pw!X1", verified=False,
            portal_url="https://portal.example", session_id="session-1")
        pending = portal_accounts.pending_registrations()
        assert pending == [{
            "host": "portal.example", "email": "alex@ruhu.ai",
            "portal_url": "https://portal.example", "session_id": "session-1",
        }]
        assert "password" not in pending[0]
        assert portal_accounts.mark_verified("portal.example")["status"] == "success"
        assert portal_accounts.pending_registrations() == []


class _Execute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _Messages:
    def __init__(self, messages):
        self.messages = messages

    def list(self, **_kwargs):
        return _Execute({"messages": self.messages})


class _Users:
    def __init__(self, messages):
        self._messages = _Messages(messages)

    def messages(self):
        return self._messages


class _Service:
    def __init__(self, messages):
        self._users = _Users(messages)

    def users(self):
        return self._users


class TestWaitForEmail:
    async def test_workspace_mailbox_link_extraction(self, monkeypatch):
        monkeypatch.setattr(alex_mailbox, "_service",
                            lambda workspace_id="": _Service([{"id": "m1"}]))
        monkeypatch.setattr(alex_mailbox, "_message_to_event", lambda _svc, _stub: {
            "from": "noreply@portal.example",
            "subject": "Verify your account",
            "excerpt": "Welcome",
        })
        monkeypatch.setattr(
            alex_mailbox, "_full_body",
            lambda _svc, _message_id: "Click: https://portal.example/verify?token=abc123")
        result = await alex_mailbox.wait_for_email(
            from_contains="portal.example", timeout_s=1, poll_s=0,
            workspace_id="founder")
        assert result["status"] == "success"
        assert result["link"] == "https://portal.example/verify?token=abc123"
        assert result["code"] == ""

    async def test_timeout_is_error_data(self, monkeypatch):
        monkeypatch.setattr(alex_mailbox, "_service",
                            lambda workspace_id="": _Service([]))
        result = await alex_mailbox.wait_for_email(
            timeout_s=0, poll_s=0, workspace_id="founder")
        assert result["status"] == "error" and result["error"] is True

    def test_extract_code(self):
        link, code = alex_mailbox._extract_link_or_code("Your code is 482913. Enter it.")
        assert link == "" and code == "482913"
