"""Static and migration evidence for the non-production M1/M2 release boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.deploy_firestore_ttl import load_policies
from scripts.migrate_durable_memory_m2 import migrate
from services.durable_store import FirestoreDurableStore, InMemoryDurableStore

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_firestore_document_id_cursor_uses_a_document_reference(monkeypatch):
    from services import durable_store

    class FakeQuery:
        def __init__(self):
            self.where_calls = []
            self.cursor_ref = object()

        def document(self, document_id):
            assert document_id == "memory-cursor"
            return self.cursor_ref

        def where(self, field, operator, value):
            self.where_calls.append((field, operator, value))
            return self

        def order_by(self, *args, **kwargs):
            return self

        def limit(self, value):
            assert value == 10
            return self

        async def stream(self):
            if False:
                yield None

    query = FakeQuery()
    monkeypatch.setattr(durable_store, "_collection_ref", lambda _name: query)
    rows = await FirestoreDurableStore().list(
        "memory_items",
        filters={"workspace_id": "workspace-a"},
        order_by="id",
        limit=10,
        start_after=("id", "memory-cursor"),
    )
    assert rows == []
    assert any(value is query.cursor_ref for _, _, value in query.where_calls)


def test_memory_ui_has_explicit_controls_and_private_entry_point():
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    assert "Since you were away" in html
    assert "What Alex knows" in html
    assert 'newSession("PRIVATE")' in html
    for endpoint in (
            "/api/v1/workspace-brief", "/api/v1/memory/status",
            "/api/v1/memories", "/api/v1/memory/settings"):
        assert endpoint in html
    refresh = html.split("async function refreshWaiting()")[1].split("\n}")[0]
    # The current main UI owns the session-mode projection directly.  A private
    # conversation returns before any workspace-brief or memory request.
    assert 'sessionMemoryMode === "PRIVATE"' in refresh
    assert "URLSearchParams({session_id: context})" in refresh
    assert "/api/v1/waits" not in refresh
    for control in (
            "Correct", "Pin", "Forget", "Start private conversation"):
        assert control in html
    assert 'toggle.textContent = state.read_enabled ? "Disable" : "Enable"' in html
    assert 'id="memorySettingsGroup" aria-live="polite"' in html
    assert "configureMemorySurface(false);" in html
    assert 'if (memorySurfaceEnabled) loadMemoryPanel();' in html
    assert 'if (!memorySurfaceEnabled || !sessionId) return;' in html
    assert '.catch(() => configureMemorySurface(false))' in html


def test_release_is_default_off_and_not_added_to_deployment():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/deploy.sh").read_text(encoding="utf-8")
    assert "DURABLE_BRIEF_M1_ENABLED=false" in example
    assert "DURABLE_MEMORY_M2_ENABLED=false" in example
    assert "DURABLE_MEMORY_BACKUP_POLICY_ATTESTED=false" in example
    assert "PERSISTENT_MEMORY_BACKEND=disabled" in example
    assert "DURABLE_MEMORY_M2_ENABLED" not in deploy
    assert "DURABLE_BRIEF_M1_ENABLED" not in deploy
    assert "configured_adk_service" not in (
        ROOT / "app/live.py").read_text(encoding="utf-8")


def test_release_flags_are_explicit_and_independent(monkeypatch):
    from services.workspace_brief import release_enabled

    monkeypatch.setenv("DURABLE_BRIEF_M1_ENABLED", "true")
    monkeypatch.setenv("DURABLE_BRIEF_M1_WORKSPACE_ALLOWLIST", "workspace-a")
    monkeypatch.setenv("DURABLE_MEMORY_M2_ENABLED", "false")
    assert release_enabled("workspace-a") is True
    assert release_enabled("workspace-b") is False


@pytest.mark.asyncio
async def test_migration_never_reads_transcripts_or_promotes_legacy_content(
        monkeypatch):
    monkeypatch.setenv("PERSISTENT_MEMORY_BACKEND", "disabled")
    store = InMemoryDurableStore()
    await store.create("memory_items", "legacy-memory", {
        "memory_id": "legacy-memory", "workspace_id": "workspace-a",
        "schema_version": 1, "summary": "legacy unreviewed content",
        "version": 1,
    })
    dry_run = await migrate(store=store, execute=False)
    assert dry_run["legacy_rows"] == 1
    assert dry_run["suppressed"] == 0
    executed = await migrate(store=store, execute=True)
    assert executed["new_memory_items"] == 0
    assert executed["transcript_reads"] == 0
    assert executed["managed_backend_calls"] == 0
    row = await store.get("memory_items", "legacy-memory")
    assert row["lifecycle_status"] == "SUPPRESSED"
    assert row["summary"] == ""


def test_m2_ttl_manifest_is_explicit_and_deploy_uses_the_reviewed_helper():
    policies = load_policies(ROOT / "infra/firestore.ttl.json")
    assert {
        "collectionGroup": "memory_items",
        "fieldPath": "expires_at_ts",
        "ttl": True,
    } in policies
    deploy = (ROOT / "scripts/deploy.sh").read_text(encoding="utf-8")
    assert ('scripts/deploy_firestore_ttl.py --project '
            '"$GOOGLE_CLOUD_PROJECT"') in deploy
