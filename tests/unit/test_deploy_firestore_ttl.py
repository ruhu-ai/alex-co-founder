"""Firestore TTL deployment is manifest-driven and blocking."""

from pathlib import Path

from scripts import deploy_firestore_ttl

ROOT = Path(__file__).resolve().parents[2]


def test_ttl_manifest_builds_blocking_idempotent_commands():
    result = deploy_firestore_ttl.apply_policies(
        project="example-project", database="(default)",
        manifest=ROOT / "infra/firestore.ttl.json", dry_run=True)

    assert result["status"] == "success" and result["declared"] == 3
    assert any("--collection-group=memory_items" in command
               and command[5] == "expires_at_ts"
               for command in result["commands"])
    for command in result["commands"]:
        assert command[:5] == [
            "gcloud", "firestore", "fields", "ttls", "update"]
        assert "--enable-ttl" in command
        assert "--async" not in command
        assert "--project=example-project" in command
