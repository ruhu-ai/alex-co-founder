from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_durable_memory_m2_release as release
from scripts import run_spec39_m2_promotion_measurement as promotion_measurement
from services.actor_identity import ActorPrincipal, WorkspaceRole
from services.durable_memory import (
    DurableMemoryService,
    MemoryMode,
    PilotPolicy,
    StoreDeletionDenyLedger,
    founder_failure_message,
)
from services.durable_memory_release import candidate_hash
from services.durable_store import InMemoryDurableStore

ROOT = Path(__file__).resolve().parents[2]


def test_cloud_measurement_entrypoint_imports_with_exact_twenty_case_pack():
    assert len(promotion_measurement.FIXTURE_TOKENS) == 20
    assert len(set(promotion_measurement.FIXTURE_TOKENS)) == 20


def complete_evidence() -> dict:
    candidate = release.candidate_hash()
    return {
        "schema_version": 4,
        "environment": "staging",
        "candidate_sha256": candidate,
        "application_database": "(default)",
        "workspace": {"workspace_id": "workspace-a", "founder_actor_id": "actor-a"},
        "deletion_ledger": {
            "database": "memory-deny-ledger",
            "administrative_owner": "ops-a",
            "database_evidence_ref": "cloud:database:ledger",
            "high_water_evidence_ref": "cloud:ledger:head",
            "ordinary_restore_exclusion_ref": "runbook:restore-exclusion",
            "replication_evidence_ref": "cloud:ledger:replication",
        },
        "ttl": {
            "collection_group": "memory_items",
            "field_path": "expires_at_ts",
            "dry_run_evidence_ref": "artifact:ttl-dry-run",
            "applied_evidence_ref": "cloud:ttl-active",
        },
        "backup_policy": {
            "name": "staging-firestore-memory-backup",
            "policy_ref": "change:backup-123",
            "owner": "ops-a",
            "evidence_ref": "cloud:backup-policy",
            "residual_days": 30,
            "complete_waits_for_residual_expiry": True,
        },
        "memory_export": {
            "asynchronous": True,
            "encrypted_at_rest": True,
            "actor_attributed": True,
            "current_source_authorization_filter": True,
            "generic_search_excluded": True,
            "artifact_ttl_hours": 24,
            "bucket": "spec39-memory-export-staging",
            "kms_key_name": "projects/p/locations/r/keyRings/k/cryptoKeys/export",
            "delivery_design_ref": "design:memory-export-v1",
            "encryption_evidence_ref": "test:memory-export-encryption",
            "expiry_evidence_ref": "test:memory-export-expiry",
        },
        "monitoring": {
            "dashboard_ref": "monitoring:dashboard:memory-m2",
            "notification_channel_ref": "monitoring:channel:founder",
            "alert_policy_refs": {
                key: f"monitoring:alert:{key}" for key in release.REQUIRED_ALERTS
            },
            "kill_switch_rehearsal_ref": "drill:kill-switch",
            "rollback_rehearsal_ref": "drill:rollback",
            "durable_work_remained_available": True,
            "deletion_jobs_remained_active": True,
            "memory_off_after_rehearsal": True,
        },
        "pilot_results": {
            "usefulness": {
                "eligible_cases": 20,
                "useful_cases": 16,
                "admitted_hits": 20,
                "irrelevant_admitted_hits": 1,
                "evidence_ref": "eval:usefulness",
            },
            "recall_paging": {
                "max_pages": 3,
                "max_candidates_examined": 30,
                "max_source_reauthorizations": 25,
                "max_admitted_hits": 5,
                "silent_under_returns": 0,
                "unauthorized_count_leaks": 0,
                "evidence_ref": "eval:paging",
            },
            "latency_cost": {
                "recall_p95_ms": 500,
                "recall_max_ms": 1200,
                "max_context_tokens": 1500,
                "max_context_chars": 6000,
                "max_writes_per_workspace_day": 100,
                "approved_cost_usd": 5,
                "measured_cost_upper_bound_usd": 5,
                "explicit_error_rate": 1.0,
                "evidence_ref": "load:latency-cost",
            },
            "deletion": {
                "recalls_after_deny": 0,
                "writes_after_deny": 0,
                "active_probe_hits": 0,
                "reported_backup_residual_honestly": True,
                "evidence_ref": "drill:deletion",
            },
            "source_revocation": {
                "recalls_after_revocation": 0,
                "writes_after_revocation": 0,
                "existence_signals": 0,
                "evidence_ref": "drill:source-revocation",
            },
            "private_session": {
                "backend_reads": 0,
                "backend_writes": 0,
                "later_standard_recall_hits": 0,
                "descendant_memory_writes": 0,
                "evidence_ref": "drill:private-session",
            },
            "correction_supersession": {
                "old_version_recall_hits": 0,
                "active_descendants": 1,
                "atomic_supersession_rate": 1.0,
                "visible_collision_rate": 1.0,
                "evidence_ref": "eval:correction",
            },
            "restore_fence": {
                "ledger_replayed_before_traffic": True,
                "ledger_high_water": 7,
                "local_projection_high_water": 7,
                "forgotten_item_probe_hits": 0,
                "traffic_opened_before_probes": 0,
                "evidence_ref": "drill:restore-fence",
            },
            "boundary": {
                "managed_memory_calls": 0,
                "automatic_writes": 0,
                "transcript_candidates": 0,
                "backfilled_items": 0,
                "multi_member_operations": 0,
                "approval_artifact_hits": 0,
                "external_effects": 0,
                "evidence_ref": "eval:m2-boundary",
            },
            "delete_all": {
                "memory_disabled_before_deletion": True,
                "active_lineages_after": 0,
                "recall_hits_after": 0,
                "writes_committed_behind_disable": 0,
                "receipt_recorded": True,
                "backup_residual_reported": True,
                "evidence_ref": "drill:delete-all",
            },
        },
    }


def _observations(*, members: list[dict] | None = None, synthetic: bool = True) -> dict:
    return {
        "mode": "LIVE",
        "members": members
        or [
            {
                "workspace_id": "workspace-a",
                "actor_id": "actor-a",
                "status": "ACTIVE",
                "role": "FOUNDER",
                "synthetic": synthetic,
                "auth_subject": "auth:actor-a",
            }
        ],
        "ledger": {"status": "success", "sequence": 7},
        "ttl_rows": [
            {
                "name": (
                    "projects/p/databases/(default)/collectionGroups/"
                    "memory_items/fields/expires_at_ts"
                ),
                "ttlConfig": {"state": "ACTIVE"},
            }
        ],
        "errors": {},
    }


@pytest.fixture(autouse=True)
def release_environment(monkeypatch):
    monkeypatch.setenv("DURABLE_MEMORY_M2_ENABLED", "false")
    monkeypatch.setenv("DURABLE_MEMORY_M2_WORKSPACE_ALLOWLIST", "workspace-a")
    monkeypatch.setenv("DURABLE_MEMORY_M2_MEMBERSHIP_CLASS", "SYNTHETIC")
    monkeypatch.setenv("PERSISTENT_MEMORY_BACKEND", "disabled")
    monkeypatch.setenv("FIRESTORE_DATABASE", "(default)")
    monkeypatch.setenv("MEMORY_DELETION_LEDGER_DATABASE", "memory-deny-ledger")
    monkeypatch.setenv("DURABLE_MEMORY_BACKUP_POLICY_ATTESTED", "true")
    monkeypatch.setenv("DURABLE_MEMORY_BACKUP_POLICY_REF", "change:backup-123")
    monkeypatch.setenv("DURABLE_MEMORY_BACKUP_RESIDUAL_DAYS", "30")
    monkeypatch.setenv("DURABLE_MEMORY_M2_EXPORT_DELIVERY_ATTESTED", "true")
    monkeypatch.setenv("DURABLE_MEMORY_EXPORT_BUCKET", "spec39-memory-export-staging")
    monkeypatch.setenv(
        "DURABLE_MEMORY_EXPORT_KMS_KEY_NAME", "projects/p/locations/r/keyRings/k/cryptoKeys/export"
    )
    monkeypatch.setenv("DURABLE_MEMORY_M2_ENTRY_GATE_ATTESTED", "false")
    monkeypatch.delenv("DURABLE_MEMORY_M2_RELEASE_CANDIDATE_SHA256", raising=False)
    monkeypatch.delenv("DURABLE_MEMORY_M2_ENTRY_ATTESTATION_SHA256", raising=False)


def test_entry_and_promotion_gates_can_pass_only_with_exact_live_evidence(monkeypatch):
    for gate, decision, synthetic in (
        ("entry", "READY_FOR_AUTHORIZED_STAGING_PILOT", True),
        ("promotion", "READY_FOR_AUTHORIZED_M2_ROLLOUT", False),
    ):
        monkeypatch.setenv(
            "DURABLE_MEMORY_M2_MEMBERSHIP_CLASS", "SYNTHETIC" if gate == "entry" else "FOUNDER"
        )
        evidence = complete_evidence()
        result = release.evaluate(
            evidence=evidence,
            gate=gate,
            workspace_id="workspace-a",
            founder_actor_id="actor-a",
            observations=_observations(synthetic=synthetic),
            provisional_probe={"passed": True},
        )
        assert result["ready"] is True
        assert result["decision"] == decision
        assert result["mutations_performed"] == 0
        assert result["observed_release_state"] == "DISABLED"
        assert result["unauthorized_tiers"] == ["M3", "M4", "M5", "M6"]
        assert result["runtime_binding"]["enablement_authorized"] is True


def test_entry_and_promotion_fail_closed_for_wrong_founder_class(monkeypatch):
    evidence = complete_evidence()
    entry = release.evaluate(
        evidence=evidence,
        gate="entry",
        workspace_id="workspace-a",
        founder_actor_id="actor-a",
        observations=_observations(synthetic=False),
        provisional_probe={"passed": True},
    )
    monkeypatch.setenv("DURABLE_MEMORY_M2_MEMBERSHIP_CLASS", "FOUNDER")
    promotion = release.evaluate(
        evidence=evidence,
        gate="promotion",
        workspace_id="workspace-a",
        founder_actor_id="actor-a",
        observations=_observations(synthetic=True),
        provisional_probe={"passed": True},
    )
    assert "live_exact_active_authenticated_founder" in entry["blocked_checks"]
    assert "live_exact_active_authenticated_founder" in promotion["blocked_checks"]


def test_membership_class_config_is_an_executable_gate(monkeypatch):
    evidence = complete_evidence()
    monkeypatch.setenv("DURABLE_MEMORY_M2_MEMBERSHIP_CLASS", "FOUNDER")
    result = release.evaluate(
        evidence=evidence,
        gate="entry",
        workspace_id="workspace-a",
        founder_actor_id="actor-a",
        observations=_observations(synthetic=True),
        provisional_probe={"passed": True},
    )
    assert "exact_membership_class_binding" in result["blocked_checks"]


def test_console_only_alert_policies_do_not_satisfy_promotion(monkeypatch):
    evidence = complete_evidence()
    evidence["monitoring"]["notification_channel_ref"] = "PENDING"
    monkeypatch.setenv("DURABLE_MEMORY_M2_MEMBERSHIP_CLASS", "FOUNDER")
    result = release.evaluate(
        evidence=evidence,
        gate="promotion",
        workspace_id="workspace-a",
        founder_actor_id="actor-a",
        observations=_observations(synthetic=False),
        provisional_probe={"passed": True},
    )
    assert "monitoring_and_alerts" in result["blocked_checks"]


def test_normal_runtime_surface_requires_complete_exact_candidate_binding(monkeypatch):
    monkeypatch.setenv("DURABLE_MEMORY_M2_ENABLED", "true")
    assert PilotPolicy.from_environment().surface_enabled is False

    monkeypatch.setenv("DURABLE_MEMORY_M2_ENTRY_GATE_ATTESTED", "true")
    monkeypatch.setenv("DURABLE_MEMORY_M2_RELEASE_CANDIDATE_SHA256", candidate_hash())
    monkeypatch.setenv("DURABLE_MEMORY_M2_ENTRY_ATTESTATION_SHA256", "sha256:" + "b" * 64)
    assert PilotPolicy.from_environment().surface_enabled is True

    monkeypatch.setenv("DURABLE_MEMORY_M2_RELEASE_CANDIDATE_SHA256", "sha256:" + "0" * 64)
    assert PilotPolicy.from_environment().surface_enabled is False

    monkeypatch.setenv("DURABLE_MEMORY_M2_RELEASE_CANDIDATE_SHA256", candidate_hash())
    monkeypatch.setenv("DURABLE_MEMORY_M2_WORKSPACE_ALLOWLIST", "workspace-a,workspace-b")
    assert PilotPolicy.from_environment().surface_enabled is False


def test_membership_growth_and_offline_snapshot_never_produce_ready_decision():
    evidence = complete_evidence()
    members = _observations()["members"] + [
        {
            "workspace_id": "workspace-a",
            "actor_id": "actor-b",
            "status": "ACTIVE",
            "role": "FOUNDER",
            "synthetic": True,
            "auth_subject": "auth:actor-b",
        }
    ]
    grown = release.evaluate(
        evidence=evidence,
        gate="entry",
        workspace_id="workspace-a",
        founder_actor_id="actor-a",
        observations=_observations(members=members),
        provisional_probe={"passed": True},
    )
    assert grown["ready"] is False
    assert "live_exact_active_authenticated_founder" in grown["blocked_checks"]

    offline = release.evaluate(
        evidence=evidence,
        gate="entry",
        workspace_id="workspace-a",
        founder_actor_id="actor-a",
        observations={
            "mode": "OFFLINE",
            "members": _observations()["members"],
            "ledger": {},
            "ttl_rows": [],
        },
        provisional_probe={"passed": True},
    )
    assert offline["ready"] is False
    assert offline["decision"] == "BLOCKED"


def test_legacy_owner_role_cannot_satisfy_founder_canary_gate():
    evidence = complete_evidence()
    members = [
        {
            **_observations()["members"][0],
            "role": "OWNER",
        }
    ]
    result = release.evaluate(
        evidence=evidence,
        gate="entry",
        workspace_id="workspace-a",
        founder_actor_id="actor-a",
        observations=_observations(members=members),
        provisional_probe={"passed": True},
    )
    assert result["ready"] is False
    assert "live_exact_active_authenticated_founder" in result["blocked_checks"]


def test_example_attestation_is_intentionally_blocked_and_contains_no_claim():
    payload = json.loads(
        (ROOT / "infra/durable-memory-m2-release-attestation.example.json").read_text(
            encoding="utf-8"
        )
    )
    result = release.evaluate(
        evidence=payload,
        gate="promotion",
        workspace_id="workspace-a",
        founder_actor_id="actor-a",
        observations=_observations(synthetic=False),
        provisional_probe={"passed": True},
    )
    assert result["ready"] is False
    assert "candidate_hash_bound" in result["blocked_checks"]


@pytest.mark.asyncio
async def test_provisional_probe_uses_independent_ledger_and_blocks_restoration():
    probe = await release.provisional_safety_probe()
    assert probe["passed"] is True
    assert probe["independent_ledger_advanced"] is True
    assert probe["online_recall_after_forget"] == 0
    assert probe["restored_recall_before_traffic"] == 0
    assert probe["private_item_delta"] == 0


def test_ttl_parser_requires_exact_active_field():
    assert release.ttl_is_active(_observations()["ttl_rows"]) is True
    assert (
        release.ttl_is_active(
            [
                {
                    "name": "projects/p/databases/d/collectionGroups/memory_items/fields/other",
                    "ttlConfig": {"state": "ACTIVE"},
                }
            ]
        )
        is False
    )
    assert (
        release.ttl_is_active(
            [
                {
                    "name": (
                        "projects/p/databases/d/collectionGroups/memory_items/fields/expires_at_ts"
                    ),
                    "ttlConfig": {"state": "CREATING"},
                }
            ]
        )
        is False
    )


def _principal() -> ActorPrincipal:
    return ActorPrincipal(
        actor_id="actor-a",
        workspace_id="workspace-a",
        role=WorkspaceRole.FOUNDER,
        role_grants=frozenset(),
        candidate_assignments=frozenset(),
        interview_assignments=frozenset(),
        session_auth_time=1_800_000_000,
        membership_version=1,
        membership_id="member-a",
    )


@pytest.mark.asyncio
async def test_recall_receipt_reports_bounded_paging_and_context_metrics():
    store, ledger_store = InMemoryDurableStore(), InMemoryDurableStore()
    principal = _principal()
    await store.create(
        "workspace_members",
        "member-a",
        {
            "workspace_id": "workspace-a",
            "actor_id": "actor-a",
            "role": "FOUNDER",
            "status": "ACTIVE",
            "synthetic": True,
            "auth_subject": "auth:actor-a",
            "version": 1,
        },
    )
    service = DurableMemoryService(
        store,
        ledger=StoreDeletionDenyLedger(ledger_store),
        policy=PilotPolicy(
            deployment_enabled=True,
            workspace_allowlist=frozenset({"workspace-a"}),
            backup_residual_days=30,
            backup_policy_attested=True,
            backup_policy_ref="test-backup-policy",
            entry_gate_attested=True,
            release_candidate_sha256=candidate_hash(),
            entry_attestation_sha256="sha256:" + "a" * 64,
            application_database="(default)",
            deletion_ledger_database="memory-deny-ledger",
            export_delivery_attested=True,
            export_bucket="test-memory-export-bucket",
            export_kms_key_name=(
                "projects/test/locations/global/keyRings/spec39/cryptoKeys/export"
            ),
            membership_class="SYNTHETIC",
        ),
    )
    assert not (
        await service.set_enabled(principal=principal, enabled=True, client_request_id="enable")
    ).get("error")
    for index in range(12):
        result = await service.remember(
            principal=principal,
            session_id=f"session-{index}",
            session_mode=MemoryMode.STANDARD.value,
            kind="PREFERENCE",
            summary=f"Keep update format concise number {index}",
            tags=["format"],
            client_request_id=f"remember-{index}",
        )
        assert not result.get("error")
    recalled = await service.recall(
        principal=principal,
        session_mode=MemoryMode.STANDARD.value,
        query="concise update format",
        limit=5,
    )
    assert len(recalled["hits"]) == 5
    assert recalled["metrics"]["pages_fetched"] == 2
    assert recalled["metrics"]["candidate_rows_examined"] == 12
    assert recalled["metrics"]["source_reauthorization_count"] == 12
    assert recalled["metrics"]["admitted_hits"] == 5
    assert recalled["metrics"]["estimated_context_tokens"] > 0
    assert recalled["partial"] is False
    receipts = await store.list("memory_search_receipts", filters={"workspace_id": "workspace-a"})
    assert receipts[0]["pages_fetched"] == 2
    assert receipts[0]["candidate_rows_examined"] == 12


def test_founder_failure_copy_is_honest_and_default_off_is_quiet():
    message = founder_failure_message(
        {"error": True, "error_code": "memory_deny_ledger_unavailable"}
    )
    assert message == (
        "Optional recall is unavailable. Current workspace facts and work are still up to date."
    )
    assert founder_failure_message({"error": True, "error_code": "memory_pilot_disabled"}) is None
    assert (
        founder_failure_message({"error": True, "error_code": "memory_disabled_for_session"})
        is None
    )
