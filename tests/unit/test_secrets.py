"""Least-privilege Secret Manager persistence tests."""

from types import SimpleNamespace

from google.api_core import exceptions as gexc

from services import secrets


class _SecretManagerClient:
    def __init__(self, *, missing: bool = False, versions=()):
        self.missing = missing
        self.versions = tuple(versions)
        self.add_requests = []
        self.create_requests = []
        self.list_requests = []
        self.destroy_requests = []

    def add_secret_version(self, *, request):
        self.add_requests.append(request)
        if self.missing:
            self.missing = False
            raise gexc.NotFound("missing")

    def create_secret(self, *, request):
        self.create_requests.append(request)

    def list_secret_versions(self, *, request):
        self.list_requests.append(request)
        return [SimpleNamespace(name=name) for name in self.versions]

    def destroy_secret_version(self, *, request):
        self.destroy_requests.append(request)


def _install(monkeypatch, client):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example-project")
    monkeypatch.setattr(
        "google.cloud.secretmanager.SecretManagerServiceClient",
        lambda: client,
    )
    monkeypatch.setattr(secrets, "_register", lambda value: None)
    secrets._cache.clear()


def test_put_adds_version_without_secret_creation(monkeypatch):
    client = _SecretManagerClient()
    _install(monkeypatch, client)

    secrets.put("WORKSPACE_TOKEN_SLOT", "refresh-token")

    assert client.create_requests == []
    assert client.add_requests == [{
        "parent": "projects/example-project/secrets/WORKSPACE_TOKEN_SLOT",
        "payload": {"data": b"refresh-token"},
    }]
    assert secrets._cache["WORKSPACE_TOKEN_SLOT"][1] == "refresh-token"


def test_put_creates_only_when_slot_is_absent(monkeypatch):
    client = _SecretManagerClient(missing=True)
    _install(monkeypatch, client)

    secrets.put("WORKSPACE_TOKEN_SLOT", "refresh-token")

    assert client.create_requests == [{
        "parent": "projects/example-project",
        "secret_id": "WORKSPACE_TOKEN_SLOT",
        "secret": {"replication": {"automatic": {}}},
    }]
    assert len(client.add_requests) == 2


def test_delete_destroys_enabled_versions_but_preserves_slot(monkeypatch):
    client = _SecretManagerClient(versions=(
        "projects/example-project/secrets/WORKSPACE_TOKEN_SLOT/versions/1",
        "projects/example-project/secrets/WORKSPACE_TOKEN_SLOT/versions/2",
    ))
    _install(monkeypatch, client)
    secrets._cache["WORKSPACE_TOKEN_SLOT"] = (0.0, "refresh-token")

    secrets.delete("WORKSPACE_TOKEN_SLOT")

    assert client.list_requests == [{
        "parent": "projects/example-project/secrets/WORKSPACE_TOKEN_SLOT",
        "filter": "state:ENABLED",
    }]
    assert client.destroy_requests == [
        {"name": "projects/example-project/secrets/WORKSPACE_TOKEN_SLOT/versions/1"},
        {"name": "projects/example-project/secrets/WORKSPACE_TOKEN_SLOT/versions/2"},
    ]
    assert "WORKSPACE_TOKEN_SLOT" not in secrets._cache
