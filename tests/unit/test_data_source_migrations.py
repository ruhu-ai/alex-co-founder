"""M1–M5 migration is additive, dry-run-first, and repeatable."""

from __future__ import annotations

import json

from services import data_source_metrics, data_source_migrations


async def test_dry_run_plans_exact_targets_without_writes(fake_store):
    fake_store.integrations["founder"] = {
        "gmail_label": "grants",
        "drive_files": [{"id": "drive-1", "name": "Deck"}, "drive-2"],
    }
    fake_store.processed_gmail_ids = ["m1", "m2"]
    report = await data_source_migrations.migrate("founder")
    assert report["dry_run"] is True
    assert report["target_counts"] == {"data_connections": 2,
                                       "source_grants": 2}
    assert report["legacy_counts"]["processed_gmail_ids"] == 2
    assert report["historical_events_synthesized"] == 0
    assert report["credential_values_read"] is False
    assert report["rollback"]["automatic_delete"] is False
    assert fake_store.data_connections == {}
    assert fake_store.source_grants == {}


async def test_apply_is_idempotent_and_dual_read_parity_is_exact(fake_store):
    fake_store.integrations["founder"] = {
        "drive_files": [{"id": "drive-1", "name": "Deck"}],
    }
    first = await data_source_migrations.migrate("founder", apply=True)
    connection_versions = {key: row["version"]
                           for key, row in fake_store.data_connections.items()}
    second = await data_source_migrations.migrate("founder", apply=True)
    assert first["dual_read_parity"] is True
    assert first["created"] == {"data_connections": 1, "source_grants": 1}
    assert second["created"] == {"data_connections": 0, "source_grants": 0}
    assert second["skipped_existing"] == 2
    assert {key: row["version"] for key, row in
            fake_store.data_connections.items()} == connection_versions
    assert len(fake_store.source_grants) == 1


def test_metrics_drop_content_secrets_urls_and_provider_ids(caplog):
    caplog.set_level("INFO", logger="data_sources.metrics")
    data_source_metrics.reset()
    data_source_metrics.record(
        "external_action_finish", action_kind="send_email", status="SUCCEEDED",
        body="secret body", approval_token="token", url="https://signed.example",
        provider_id="gmail-high-cardinality")
    payload = json.loads(caplog.records[-1].message)
    assert payload == {"metric": "external_action_finish",
                       "action_kind": "send_email", "status": "SUCCEEDED"}
    assert data_source_metrics.snapshot() == {"external_action_finish": 1}
