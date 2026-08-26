"""Deployment fails closed when a declared Firestore index is unavailable."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "deploy_firestore_indexes", ROOT / "scripts" / "deploy_firestore_indexes.py")
assert SPEC and SPEC.loader
deploy_indexes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy_indexes)


def _index(state: str = "READY") -> dict:
    return {
        "collectionGroup": "founder_inbox",
        "queryScope": "COLLECTION",
        "state": state,
        "fields": [
            {"fieldPath": "founder_id", "order": "ASCENDING"},
            {"fieldPath": "status", "order": "ASCENDING"},
            {"fieldPath": "created_at", "order": "DESCENDING"},
            {"fieldPath": "__name__", "order": "DESCENDING"},
        ],
    }


def test_create_command_preserves_manifest_order_and_is_async():
    command = deploy_indexes.create_command(_index(), "project-1", "(default)")
    assert command == [
        "gcloud", "firestore", "indexes", "composite", "create", "--quiet",
        "--async",
        "--project=project-1", "--database=(default)",
        "--collection-group=founder_inbox", "--query-scope=collection",
        "--field-config=field-path=founder_id,order=ascending",
        "--field-config=field-path=status,order=ascending",
        "--field-config=field-path=created_at,order=descending",
        "--field-config=field-path=__name__,order=descending",
    ]
    assert command.count("--async") == 1


def test_ensure_indexes_creates_missing_then_verifies_ready(
        monkeypatch, tmp_path):
    manifest = tmp_path / "indexes.json"
    manifest.write_text(
        '{"indexes": [{"collectionGroup": "founder_inbox", '
        '"queryScope": "COLLECTION", "fields": ['
        '{"fieldPath": "founder_id", "order": "ASCENDING"}, '
        '{"fieldPath": "status", "order": "ASCENDING"}, '
        '{"fieldPath": "created_at", "order": "DESCENDING"}]}]}',
        encoding="utf-8")
    inventories = iter([[], [_index()]])
    monkeypatch.setattr(deploy_indexes, "list_indexes",
                        lambda _project, _database: next(inventories))
    calls = []
    monkeypatch.setattr(deploy_indexes.subprocess, "run",
                        lambda command, check: calls.append((command, check)))
    report = deploy_indexes.ensure_indexes("project-1", manifest_path=manifest)
    assert report == {"declared": 1, "created": 1, "ready": 1}
    assert calls[0][1] is True


def test_ensure_indexes_rejects_present_but_building(monkeypatch, tmp_path):
    manifest = tmp_path / "indexes.json"
    manifest.write_text(
        '{"indexes": [{"collectionGroup": "founder_inbox", '
        '"queryScope": "COLLECTION", "fields": ['
        '{"fieldPath": "founder_id", "order": "ASCENDING"}, '
        '{"fieldPath": "status", "order": "ASCENDING"}, '
        '{"fieldPath": "created_at", "order": "DESCENDING"}]}]}',
        encoding="utf-8")
    monkeypatch.setattr(
        deploy_indexes, "list_indexes",
        lambda _project, _database: [_index(state="CREATING")])
    with pytest.raises(RuntimeError, match="not_ready"):
        deploy_indexes.ensure_indexes(
            "project-1", manifest_path=manifest,
            timeout_seconds=0, poll_interval=0)


def test_deploy_script_runs_index_gate_before_migration():
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    index_gate = deploy.index("scripts/deploy_firestore_indexes.py")
    migration = deploy.index("scripts/migrate_browser_runs.py")
    cloud_run = deploy.index("gcloud run deploy co-founder")
    assert index_gate < migration < cloud_run


def test_deploy_uses_least_privilege_and_never_duplicates_fixed_secrets():
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "secretmanager.secretAccessor" in deploy
    assert "secretmanager.admin" not in deploy
    assert "FIXED_SECRET_BINDING_KEYS=(APP_AUTH_TOKEN PORTAL_WEBHOOK_TOKEN)" in deploy
    assert '${FIXED_SECRET_BINDING_KEYS[*]}' in deploy


def test_deploy_uses_the_verified_virtualenv_interpreter_only():
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert 'PYTHON="$ROOT/.venv/bin/python"' in deploy
    assert "python3" not in deploy
    for script in ("check_browser_invariants.py", "deploy_firestore_indexes.py",
                   "migrate_browser_runs.py"):
        assert f'"$PYTHON" scripts/{script}' in deploy
