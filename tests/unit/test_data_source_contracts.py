"""Closed data-source contracts and WI-1 persistence primitives (docs/24)."""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from services import data_lifecycle, firestore
from services import data_source_contracts as dsc

pytestmark = pytest.mark.asyncio


def test_every_closed_registry_rejects_unknown_values():
    registries = (
        dsc.ConnectorId, dsc.DataSourceRole, dsc.ConnectionAuthKind,
        dsc.ConnectionStatus, dsc.SourceGrantStatus, dsc.SourceKind,
        dsc.IngestionScope, dsc.ExternalEventKind, dsc.ContentRisk,
        dsc.EventProcessingStatus, dsc.CorrelationStatus,
        dsc.CorrelationBasis, dsc.DeliveryStatus, dsc.FounderInboxKind,
        dsc.FounderInboxStatus, dsc.InboxResolution,
        dsc.ExternalActionKind, dsc.ExternalActionStatus, dsc.SafeErrorCode,
    )
    for registry in registries:
        with pytest.raises(ValueError):
            dsc.require_closed("model_authored_unknown", registry)


def test_connector_registry_is_exact_and_role_scoped():
    assert set(dsc.CONNECTOR_REGISTRY) == set(dsc.ConnectorId)
    assert dsc.validate_connector_role("founder_gmail", "event")
    assert not dsc.validate_connector_role("founder_gmail", "knowledge")
    assert not dsc.validate_connector_role("github", "event")
    assert not dsc.validate_connector_role("drive", "made_up_role")


def test_deterministic_ids_follow_the_accepted_namespaces_exactly():
    def expected(prefix, namespace, *parts):
        return prefix + hashlib.sha256(
            (namespace + "".join(parts)).encode()).hexdigest()[:32]

    cid = dsc.data_connection_id("f1", "drive", "acct")
    assert cid == expected("dc_", "data-connection:v1", "f1", "drive", "acct")
    gid = dsc.source_grant_id("f1", cid, "file-1")
    assert gid == expected("sg_", "source-grant:v1", "f1", cid, "file-1")
    eid = dsc.external_event_id("f1", cid, "msg-1")
    assert eid == expected("xe_", "external-event:v1", "f1", cid, "msg-1")
    assert dsc.founder_inbox_id("f1", eid, "UNMATCHED_EVENT") == expected(
        "fi_", "founder-inbox:v1", "f1", eid, "UNMATCHED_EVENT")
    assert dsc.external_action_id("f1", "send_email", "mail-1") == expected(
        "xa_", "external-action:v1", "f1", "send_email", "mail-1")


def test_canonical_hash_is_stable_bounded_and_strict():
    assert dsc.canonical_hash({"b": 2, "a": 1}) == dsc.canonical_hash(
        {"a": 1, "b": 2})
    with pytest.raises(ValueError):
        dsc.canonical_hash({"body": "x" * 40_000})
    with pytest.raises(ValueError):
        dsc.canonical_hash({"not_json": object()})


async def _drive_connection():
    return await firestore.upsert_data_connection(
        "founder", "drive", account_ref="google-sub-1",
        roles=["knowledge", "action_destination"],
        granted_scopes=["drive.readonly"])


async def test_connection_version_and_owner_contract(fake_store):
    created = await _drive_connection()
    assert created["created"] is True and created["version"] == 1
    stale = await firestore.upsert_data_connection(
        "founder", "drive", account_ref="google-sub-1",
        expected_version=8)
    assert stale["error_code"] == "version_conflict"
    assert await firestore.get_data_connection("other", created["connection_id"]) is None


async def test_source_grant_is_deterministic_reactivatable_and_owner_scoped(fake_store):
    connection = await _drive_connection()
    first = await firestore.create_source_grant(
        "founder", connection["connection_id"], "file-1",
        display_name="Deck.pdf", allowed_ingestion_scopes=["profile"])
    again = await firestore.create_source_grant(
        "founder", connection["connection_id"], "file-1",
        display_name="Deck.pdf", allowed_ingestion_scopes=["profile"])
    assert first["source_grant_id"] == again["source_grant_id"]
    assert await firestore.get_source_grant("other", first["source_grant_id"]) is None
    revoked = await firestore.revoke_source_grant(
        "founder", first["source_grant_id"])
    assert revoked["status"] == "success"
    assert fake_store.source_grants[first["source_grant_id"]]["status"] == "REVOKED"


async def test_alex_drive_source_grant_preserves_its_closed_connector(fake_store):
    connection = await firestore.upsert_data_connection(
        "founder", "alex_drive", account_ref="alex-role-mailbox",
        roles=["knowledge", "action_destination"],
        granted_scopes=["drive.readonly", "drive.file"],
    )

    grant = await firestore.create_source_grant(
        "founder", connection["connection_id"], "synthetic-file-1",
        display_name="Synthetic source", allowed_ingestion_scopes=["reference_only"],
    )

    assert not grant.get("error"), grant
    assert grant["status"] == "ACTIVE"
    assert grant["connector_id"] == "alex_drive"
    assert fake_store.source_grants[grant["source_grant_id"]]["connector_id"] == "alex_drive"


async def test_fifty_duplicate_event_deliveries_create_one_receipt(fake_store):
    connection = await firestore.upsert_data_connection(
        "founder", "alex_mail", account_ref="alex-role-mailbox",
        roles=["event", "action_destination"])
    payload_hash = dsc.canonical_hash({"message_id": "m-1", "thread": "t-1"})

    async def deliver():
        return await firestore.create_external_event(
            "founder", connection["connection_id"], "alex_mail", "m-1",
            "mail_confirmation", payload_hash=payload_hash,
            source_ref={"message_id": "m-1"})

    results = await asyncio.gather(*(deliver() for _ in range(50)))
    assert len(fake_store.external_events) == 1
    assert sum(not result["duplicate"] for result in results) == 1


async def test_event_claim_and_inbox_commit_are_idempotent(fake_store):
    connection = await firestore.upsert_data_connection(
        "founder", "founder_gmail", roles=["event"])
    event = await firestore.create_external_event(
        "founder", connection["connection_id"], "founder_gmail", "m-2",
        "mail_update", payload_hash=dsc.canonical_hash({"id": "m-2"}))
    claim = await firestore.claim_external_event("founder", event["event_id"])
    first = await firestore.create_founder_inbox_item(
        "founder", event["event_id"], "UNMATCHED_EVENT", title="New message",
        summary="No exact application link", lease_owner=claim["lease_owner"])
    second = await firestore.create_founder_inbox_item(
        "founder", event["event_id"], "UNMATCHED_EVENT", title="New message",
        summary="No exact application link")
    assert first["duplicate"] is False and second["duplicate"] is True
    assert len(fake_store.founder_inbox) == 1
    assert fake_store.external_events[event["event_id"]]["processing_status"] == "INBOXED"


async def test_external_action_payload_drift_and_uncertainty_block_retry(fake_store):
    connection = await _drive_connection()
    prepared = await firestore.prepare_external_action(
        "founder", connection["connection_id"], "export_drive_file", "copy-1",
        dsc.canonical_hash({"artifact": "doc-1", "sha256": "a" * 64}))
    drift = await firestore.prepare_external_action(
        "founder", connection["connection_id"], "export_drive_file", "copy-1",
        dsc.canonical_hash({"artifact": "doc-2", "sha256": "b" * 64}))
    assert drift["error_code"] == "version_conflict"
    started = await firestore.start_external_action(
        "founder", prepared["action_id"], prepared["lease_owner"])
    assert started["status"] == "success"
    await firestore.finish_external_action(
        "founder", prepared["action_id"], prepared["lease_owner"], "UNCERTAIN",
        uncertainty_reason="provider_timeout", error_code="provider_timeout")
    retry = await firestore.prepare_external_action(
        "founder", connection["connection_id"], "export_drive_file", "copy-1",
        dsc.canonical_hash({"artifact": "doc-1", "sha256": "a" * 64}))
    assert retry["error_code"] == "reconciliation_required"


async def test_expired_prepared_is_reclaimed_but_expired_execution_is_uncertain(
        fake_store):
    """T1 is recoverable; only expiry after T2 has an ambiguous outcome."""
    connection = await _drive_connection()
    request_hash = dsc.canonical_hash({"artifact": "doc-1"})
    prepared = await firestore.prepare_external_action(
        "founder", connection["connection_id"], "export_drive_file", "copy-crash",
        request_hash, lease_seconds=1)
    fake_store.external_actions[prepared["action_id"]]["lease_started_at"] = (
        "2000-01-01T00:00:00+00:00")

    after_crash = await firestore.prepare_external_action(
        "founder", connection["connection_id"], "export_drive_file", "copy-crash",
        request_hash, lease_seconds=1)

    assert after_crash["status"] == "success"
    assert after_crash["reclaimed"] is True
    assert after_crash["action_id"] == prepared["action_id"]

    started = await firestore.start_external_action(
        "founder", prepared["action_id"], after_crash["lease_owner"])
    assert started["status"] == "success"
    fake_store.external_actions[prepared["action_id"]]["lease_started_at"] = (
        "2000-01-01T00:00:00+00:00")
    after_provider_window = await firestore.prepare_external_action(
        "founder", connection["connection_id"], "export_drive_file", "copy-crash",
        request_hash, lease_seconds=1)
    assert after_provider_window["error_code"] == "reconciliation_required"
    assert after_provider_window["status"] == "UNCERTAIN"
    assert fake_store.external_actions[prepared["action_id"]]["lease_owner"] is None

    resolved = await firestore.reconcile_external_action(
        "founder", prepared["action_id"], "FAILED",
        error_code="provider_rejected")
    assert resolved["status"] == "FAILED"


def test_lifecycle_registry_exactly_covers_collection_registry():
    assert data_lifecycle.registry_coverage() == {
        "status": "success", "missing_top": [], "extra_top": [],
        "missing_subcollections": [], "extra_subcollections": [],
    }


def test_firestore_indexes_cover_all_new_collections():
    path = data_lifecycle.__file__.rsplit("/services/", 1)[0]
    with open(path + "/infra/firestore.indexes.json", encoding="utf-8") as handle:
        configured = {row["collectionGroup"] for row in json.load(handle)["indexes"]}
    assert {"data_connections", "source_grants", "external_events",
            "founder_inbox", "external_actions"}.issubset(configured)
