"""Static and migration evidence for the non-production M1/M2 release boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.migrate_durable_memory_m2 import migrate
from services.durable_store import InMemoryDurableStore

ROOT = Path(__file__).resolve().parents[2]


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
    assert 'sessionMemoryMode === "PRIVATE"' in refresh
    assert "URLSearchParams({session_id: context})" in refresh
    assert "/api/v1/waits" not in refresh
    for control in (
            "Correct", "Pin", "Forget", "Start private conversation"):
        assert control in html
    assert 'toggle.textContent = state.read_enabled ? "Disable" : "Enable"' in html


def test_release_is_default_off_and_not_added_to_deployment():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/deploy.sh").read_text(encoding="utf-8")
    assert "DURABLE_BRIEF_M1_ENABLED=false" in example
    assert "DURABLE_MEMORY_M2_ENABLED=false" in example
    assert "DURABLE_MEMORY_BACKUP_POLICY_ATTESTED=false" in example
    assert "PERSISTENT_MEMORY_BACKEND=disabled" in example
    assert "DURABLE_MEMORY_M2_ENABLED" not in deploy
    assert "DURABLE_BRIEF_M1_ENABLED" not in deploy


def test_local_release_enables_only_m1():
    def selected(path):
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {
                    "DURABLE_BRIEF_M1_ENABLED",
                    "DURABLE_BRIEF_M1_WORKSPACE_ALLOWLIST",
                    "DURABLE_MEMORY_M2_ENABLED",
                    "PERSISTENT_MEMORY_BACKEND"}:
                values[key] = value.strip()
        return values

    local = selected(ROOT / ".env")
    assert local == {
        "DURABLE_BRIEF_M1_ENABLED": "true",
        "DURABLE_BRIEF_M1_WORKSPACE_ALLOWLIST": "founder",
        "DURABLE_MEMORY_M2_ENABLED": "false",
        "PERSISTENT_MEMORY_BACKEND": "disabled",
    }
    production = selected(ROOT / ".env.prod")
    assert production.get("DURABLE_BRIEF_M1_ENABLED") in {None, "false"}
    assert production.get("DURABLE_MEMORY_M2_ENABLED") in {None, "false"}


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
