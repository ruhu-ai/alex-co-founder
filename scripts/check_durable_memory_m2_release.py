#!/usr/bin/env python3
"""Read-only entry/promotion gate for the bounded Document 39 M2 pilot.

The checker never changes configuration, applies TTL, runs a migration, or
enables memory.  Offline mode is deliberately provisional.  A release-ready
decision requires live membership/ledger/TTL reads plus evidence references
for controls that cannot be proven from application data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.actor_identity import ActorPrincipal, WorkspaceRole  # noqa: E402
from services.durable_memory import (  # noqa: E402
    DurableMemoryService,
    MemoryMode,
    PilotPolicy,
    StoreDeletionDenyLedger,
    configured_ledger,
)
from services.durable_memory_release import (  # noqa: E402
    attestation_hash,
    candidate_hash,
)
from services.durable_store import (  # noqa: E402
    InMemoryDurableStore,
    production_store,
)

ATTESTATION_SCHEMA_VERSION = 4
REQUIRED_ALERTS = {
    "cross_scope_hit",
    "missing_source_hit",
    "deleted_item_retrieval",
    "private_session_backend_call",
    "deletion_deadline_breach",
    "backend_error_spike",
    "cost_budget_exhaustion",
    "hiring_boundary",
}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _ref(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text.upper() not in {"TODO", "TBD", "PENDING"})


def _check(
    checks: list[dict[str, Any]],
    check_id: str,
    passed: bool,
    *,
    evidence: str = "",
    detail: str = "",
    provisional: bool = False,
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "status": (
                "PROVISIONAL" if provisional and passed else "PASS" if passed else "BLOCKED"
            ),
            "evidence_ref": evidence if _ref(evidence) else None,
            "detail": detail,
        }
    )


def exact_membership_check(
    rows: list[dict[str, Any]],
    *,
    workspace_id: str,
    founder_actor_id: str,
    require_synthetic: bool,
) -> bool:
    """Require exactly one active authenticated Founder of the expected class."""
    active = [
        row
        for row in rows
        if row.get("workspace_id") == workspace_id and row.get("status") == "ACTIVE"
    ]
    return (
        len(active) == 1
        and active[0].get("actor_id") == founder_actor_id
        and active[0].get("role") == WorkspaceRole.FOUNDER.value
        and _ref(active[0].get("auth_subject"))
        and active[0].get("synthetic") is require_synthetic
    )


def ttl_is_active(rows: list[dict[str, Any]]) -> bool:
    """Accept only the exact memory_items.expires_at_ts active TTL field."""
    matching = []
    for row in rows:
        name = str(row.get("name") or "")
        field = str(row.get("fieldPath") or name.rsplit("/fields/", 1)[-1])
        collection = str(row.get("collectionGroup") or "")
        state = str((row.get("ttlConfig") or {}).get("state") or row.get("state") or "")
        if field == "expires_at_ts" and (
            collection == "memory_items" or "/collectionGroups/memory_items/" in name
        ):
            matching.append(state == "ACTIVE")
    return matching == [True]


def _gcloud_json(*args: str) -> Any:
    completed = subprocess.run(
        ("gcloud", *args, "--format=json"), check=False, capture_output=True, text=True
    )
    if completed.returncode:
        return {"error": True, "message": completed.stderr.strip()[-500:]}
    try:
        return json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return {"error": True, "message": "gcloud returned invalid JSON"}


async def live_observations(*, workspace_id: str, project: str, database: str) -> dict[str, Any]:
    """Read current membership, independent-ledger head, and TTL metadata."""
    result: dict[str, Any] = {"mode": "LIVE", "errors": {}}
    try:
        result["members"] = await production_store().list(
            "workspace_members", filters={"workspace_id": workspace_id, "status": "ACTIVE"}, limit=3
        )
    except Exception as exc:
        result["members"] = []
        result["errors"]["membership"] = type(exc).__name__
    try:
        result["ledger"] = await configured_ledger().high_water(workspace_id)
    except Exception as exc:
        result["ledger"] = {"error": True}
        result["errors"]["ledger"] = type(exc).__name__
    ttl = _gcloud_json(
        "firestore",
        "fields",
        "ttls",
        "list",
        "--collection-group=memory_items",
        f"--database={database}",
        f"--project={project}",
    )
    result["ttl_rows"] = ttl if isinstance(ttl, list) else []
    if isinstance(ttl, dict) and ttl.get("error"):
        result["errors"]["ttl"] = ttl.get("message")
    return result


async def provisional_safety_probe() -> dict[str, Any]:
    """Exercise independent-ledger restore fencing without cloud mutations."""
    workspace_id, actor_id = "synthetic-memory-probe", "synthetic-founder"
    store = InMemoryDurableStore()
    ledger_store = InMemoryDurableStore()
    await store.create(
        "workspace_members",
        "probe-member",
        {
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "role": WorkspaceRole.FOUNDER.value,
            "status": "ACTIVE",
            "auth_subject": "synthetic:release-probe",
            "synthetic": True,
            "version": 1,
        },
    )
    principal = ActorPrincipal(
        workspace_id=workspace_id,
        actor_id=actor_id,
        role=WorkspaceRole.FOUNDER,
        role_grants=frozenset(),
        candidate_assignments=frozenset(),
        interview_assignments=frozenset(),
        session_auth_time=1_800_000_000,
        membership_version=1,
        principal_kind="INTERACTIVE",
        membership_id="probe-member",
    )
    policy = PilotPolicy(
        deployment_enabled=True,
        workspace_allowlist=frozenset({workspace_id}),
        backup_residual_days=30,
        backup_policy_attested=True,
        backup_policy_ref="probe-backup-policy",
        entry_gate_attested=True,
        release_candidate_sha256=candidate_hash(),
        entry_attestation_sha256="sha256:" + "0" * 64,
        application_database="(default)",
        deletion_ledger_database="probe-memory-deny-ledger",
        export_delivery_attested=True,
        export_bucket="probe-memory-export-bucket",
        export_kms_key_name=("projects/probe/locations/global/keyRings/spec39/cryptoKeys/export"),
        membership_class="SYNTHETIC",
    )
    ledger = StoreDeletionDenyLedger(ledger_store)
    service = DurableMemoryService(store, ledger=ledger, policy=policy)
    enabled = await service.set_enabled(
        principal=principal, enabled=True, client_request_id="probe-enable"
    )
    remembered = await service.remember(
        principal=principal,
        session_id="probe-session",
        session_mode=MemoryMode.STANDARD.value,
        kind="PREFERENCE",
        summary="Use concise progress summaries",
        tags=["format"],
        client_request_id="probe-remember",
    )
    before_forget = deepcopy(store.records)
    forgotten = await service.forget(
        principal=principal,
        memory_id=str(remembered.get("memory_id") or ""),
        expected_version=1,
        client_request_id="probe-forget",
    )
    after_forget = await service.recall(
        principal=principal,
        session_mode=MemoryMode.STANDARD.value,
        query="concise progress summaries",
    )
    restored_store = InMemoryDurableStore(records=before_forget)
    restored_service = DurableMemoryService(restored_store, ledger=ledger, policy=policy)
    restored_recall = await restored_service.recall(
        principal=principal,
        session_mode=MemoryMode.STANDARD.value,
        query="concise progress summaries",
    )
    private_before = len(store.records.get("memory_items", {}))
    private = await service.remember(
        principal=principal,
        session_id="private-probe",
        session_mode=MemoryMode.PRIVATE.value,
        kind="PREFERENCE",
        summary="Do not persist this",
        tags=[],
        client_request_id="probe-private",
    )
    return {
        "enabled_for_local_probe": not enabled.get("error"),
        "independent_ledger_advanced": int(
            (await ledger.high_water(workspace_id)).get("sequence", 0)
        )
        == 1,
        "online_recall_after_forget": len(after_forget.get("hits") or []),
        "restored_recall_before_traffic": len(restored_recall.get("hits") or []),
        "deletion_status": forgotten.get("deletion_status"),
        "private_write_error": private.get("error_code"),
        "private_item_delta": (len(store.records.get("memory_items", {})) - private_before),
        "passed": (
            not enabled.get("error")
            and int((await ledger.high_water(workspace_id)).get("sequence", 0)) == 1
            and len(after_forget.get("hits") or []) == 0
            and len(restored_recall.get("hits") or []) == 0
            and forgotten.get("deletion_status") == "ONLINE_COMPLETE_BACKUP_RESIDUAL"
            and private.get("error_code") == "memory_disabled_for_session"
            and len(store.records.get("memory_items", {})) == private_before
        ),
    }


def _pilot_checks(checks: list[dict[str, Any]], pilot: dict[str, Any]) -> None:
    usefulness = pilot.get("usefulness") or {}
    eligible = int(usefulness.get("eligible_cases") or 0)
    admitted = int(usefulness.get("admitted_hits") or 0)
    useful = int(usefulness.get("useful_cases") or 0)
    irrelevant = int(usefulness.get("irrelevant_admitted_hits") or 0)
    _check(
        checks,
        "pilot_usefulness",
        _ref(usefulness.get("evidence_ref"))
        and eligible >= 20
        and useful / max(eligible, 1) >= 0.80
        and irrelevant / max(admitted, 1) <= 0.05,
        evidence=usefulness.get("evidence_ref"),
        detail=">=20 labelled cases; useful >=80%; irrelevant admitted <=5%",
    )

    paging = pilot.get("recall_paging") or {}
    _check(
        checks,
        "pilot_recall_paging",
        _ref(paging.get("evidence_ref"))
        and all(
            (
                int(paging.get("max_pages", 999)) <= 3,
                int(paging.get("max_candidates_examined", 999)) <= 30,
                int(paging.get("max_source_reauthorizations", 999)) <= 25,
                int(paging.get("max_admitted_hits", 999)) <= 5,
                int(paging.get("silent_under_returns", 999)) == 0,
                int(paging.get("unauthorized_count_leaks", 999)) == 0,
            )
        ),
        evidence=paging.get("evidence_ref"),
        detail="<=3 pages/30 candidates/25 source checks/5 hits; no silent partial or leak",
    )

    performance = pilot.get("latency_cost") or {}
    approved_cost = float(performance.get("approved_cost_usd") or -1)
    measured_cost = performance.get("actual_cost_usd")
    bounded_cost = performance.get("measured_cost_upper_bound_usd")
    cost_value = float(measured_cost if measured_cost is not None else bounded_cost or 1e30)
    _check(
        checks,
        "pilot_latency_cost",
        _ref(performance.get("evidence_ref"))
        and all(
            (
                float(performance.get("recall_p95_ms") or 1e30) <= 500,
                float(performance.get("recall_max_ms") or 1e30) <= 1200,
                int(performance.get("max_context_tokens") or 999999) <= 1500,
                int(performance.get("max_context_chars") or 999999) <= 6000,
                int(performance.get("max_writes_per_workspace_day") or 999999) <= 100,
                approved_cost >= 0,
                cost_value <= approved_cost,
                float(performance.get("explicit_error_rate") or 0) == 1.0,
            )
        ),
        evidence=performance.get("evidence_ref"),
        detail=(
            "p95<=500ms, max<=1200ms, context/write caps, measured cost or "
            "conservative measured-run upper bound within approval, 100% explicit errors"
        ),
    )

    deletion = pilot.get("deletion") or {}
    _check(
        checks,
        "pilot_deletion",
        _ref(deletion.get("evidence_ref"))
        and all(
            (
                int(deletion.get("recalls_after_deny", 999)) == 0,
                int(deletion.get("writes_after_deny", 999)) == 0,
                int(deletion.get("active_probe_hits", 999)) == 0,
                deletion.get("reported_backup_residual_honestly") is True,
            )
        ),
        evidence=deletion.get("evidence_ref"),
        detail="zero post-deny read/write/live hits; backup residual reported",
    )

    revocation = pilot.get("source_revocation") or {}
    _check(
        checks,
        "pilot_source_revocation",
        _ref(revocation.get("evidence_ref"))
        and all(
            (
                int(revocation.get("recalls_after_revocation", 999)) == 0,
                int(revocation.get("writes_after_revocation", 999)) == 0,
                int(revocation.get("existence_signals", 999)) == 0,
            )
        ),
        evidence=revocation.get("evidence_ref"),
        detail="zero recall/write/existence signals after source access revocation",
    )

    private = pilot.get("private_session") or {}
    _check(
        checks,
        "pilot_private_session",
        _ref(private.get("evidence_ref"))
        and all(
            (
                int(private.get("backend_reads", 999)) == 0,
                int(private.get("backend_writes", 999)) == 0,
                int(private.get("later_standard_recall_hits", 999)) == 0,
                int(private.get("descendant_memory_writes", 999)) == 0,
            )
        ),
        evidence=private.get("evidence_ref"),
        detail="zero optional I/O, later recall, and descendant writes",
    )

    correction = pilot.get("correction_supersession") or {}
    _check(
        checks,
        "pilot_correction_supersession",
        _ref(correction.get("evidence_ref"))
        and all(
            (
                int(correction.get("old_version_recall_hits", 999)) == 0,
                int(correction.get("active_descendants", 999)) == 1,
                float(correction.get("atomic_supersession_rate") or 0) == 1.0,
                float(correction.get("visible_collision_rate") or 0) == 1.0,
            )
        ),
        evidence=correction.get("evidence_ref"),
        detail="old hidden, one active descendant, 100% atomic/collision-visible",
    )

    restore = pilot.get("restore_fence") or {}
    _check(
        checks,
        "pilot_restore_fence",
        _ref(restore.get("evidence_ref"))
        and all(
            (
                restore.get("ledger_replayed_before_traffic") is True,
                int(restore.get("ledger_high_water") or -1)
                == int(restore.get("local_projection_high_water") or -2),
                int(restore.get("forgotten_item_probe_hits", 999)) == 0,
                int(restore.get("traffic_opened_before_probes", 999)) == 0,
            )
        ),
        evidence=restore.get("evidence_ref"),
        detail="current ledger replayed before traffic; equal high-water; zero hits",
    )

    boundary = pilot.get("boundary") or {}
    _check(
        checks,
        "pilot_m2_boundary",
        _ref(boundary.get("evidence_ref"))
        and all(
            int(boundary.get(key) or 0) == 0
            for key in (
                "managed_memory_calls",
                "automatic_writes",
                "transcript_candidates",
                "backfilled_items",
                "multi_member_operations",
                "approval_artifact_hits",
                "external_effects",
            )
        ),
        evidence=boundary.get("evidence_ref"),
        detail="M3+ and external-effect counters all zero",
    )

    delete_all = pilot.get("delete_all") or {}
    _check(
        checks,
        "pilot_delete_all",
        _ref(delete_all.get("evidence_ref"))
        and all(
            (
                delete_all.get("memory_disabled_before_deletion") is True,
                int(delete_all.get("active_lineages_after") or 0) == 0,
                int(delete_all.get("recall_hits_after") or 0) == 0,
                int(delete_all.get("writes_committed_behind_disable") or 0) == 0,
                delete_all.get("receipt_recorded") is True,
                delete_all.get("backup_residual_reported") is True,
            )
        ),
        evidence=delete_all.get("evidence_ref"),
        detail="fresh plan, disable fence, zero active/recall/late writes, receipt",
    )


def evaluate(
    *,
    evidence: dict[str, Any],
    gate: str,
    workspace_id: str,
    founder_actor_id: str,
    observations: dict[str, Any],
    provisional_probe: dict[str, Any],
) -> dict[str, Any]:
    candidate = candidate_hash()
    checks: list[dict[str, Any]] = []
    _check(
        checks, "attestation_schema", evidence.get("schema_version") == ATTESTATION_SCHEMA_VERSION
    )
    _check(
        checks,
        "candidate_hash_bound",
        evidence.get("candidate_sha256") == candidate,
        detail="all technical evidence must bind the exact candidate hash",
    )
    _check(checks, "staging_only", evidence.get("environment") == "staging")
    workspace = evidence.get("workspace") or {}
    _check(
        checks,
        "exact_workspace_and_founder",
        workspace.get("workspace_id") == workspace_id
        and workspace.get("founder_actor_id") == founder_actor_id,
    )

    allowlist = {
        item.strip()
        for item in os.environ.get("DURABLE_MEMORY_M2_WORKSPACE_ALLOWLIST", "").split(",")
        if item.strip()
    }
    _check(
        checks,
        "release_flag_still_disabled",
        not _truthy(os.environ.get("DURABLE_MEMORY_M2_ENABLED")),
        detail="checker does not authorize or perform enablement",
    )
    _check(checks, "exact_single_workspace_allowlist", allowlist == {workspace_id})
    _check(
        checks,
        "exact_membership_class_binding",
        os.environ.get("DURABLE_MEMORY_M2_MEMBERSHIP_CLASS", "").strip()
        == ("SYNTHETIC" if gate == "entry" else "FOUNDER"),
        detail="entry and normal-Founder promotion cannot share a membership-class binding",
    )
    _check(
        checks,
        "generic_memory_backend_disabled",
        os.environ.get("PERSISTENT_MEMORY_BACKEND", "disabled") == "disabled",
    )

    live = observations.get("mode") == "LIVE"
    _check(
        checks,
        "live_exact_active_authenticated_founder",
        exact_membership_check(
            observations.get("members") or [],
            workspace_id=workspace_id,
            founder_actor_id=founder_actor_id,
            require_synthetic=gate == "entry",
        ),
        detail=(
            "live read required; entry requires a synthetic Founder and promotion "
            "requires the single authenticated non-synthetic Founder"
        ),
        provisional=not live,
    )
    ledger = evidence.get("deletion_ledger") or {}
    app_database = str(evidence.get("application_database") or "")
    ledger_database = str(ledger.get("database") or "")
    configured_app_database = (
        os.environ.get("FIRESTORE_DATABASE", "(default)").strip() or "(default)"
    )
    configured_ledger_database = os.environ.get("MEMORY_DELETION_LEDGER_DATABASE", "").strip()
    ledger_live = observations.get("ledger") or {}
    _check(
        checks,
        "database_configuration_bound",
        app_database == configured_app_database and ledger_database == configured_ledger_database,
        detail="attestation names must exactly match observed deployment config",
    )
    _check(
        checks,
        "independent_ledger_database",
        bool(ledger_database and ledger_database not in {app_database, "(default)"})
        and _ref(ledger.get("database_evidence_ref")),
        evidence=ledger.get("database_evidence_ref"),
    )
    _check(
        checks,
        "live_ledger_high_water_read",
        live
        and not ledger_live.get("error")
        and isinstance(ledger_live.get("sequence"), int)
        and _ref(ledger.get("high_water_evidence_ref")),
        evidence=ledger.get("high_water_evidence_ref"),
    )
    _check(
        checks,
        "ledger_excluded_from_application_restore",
        _ref(ledger.get("ordinary_restore_exclusion_ref"))
        and _ref(ledger.get("administrative_owner"))
        and _ref(ledger.get("replication_evidence_ref")),
        evidence=ledger.get("ordinary_restore_exclusion_ref"),
        detail="separate database alone is insufficient",
    )
    _check(
        checks,
        "provisional_non_restoration_probe",
        provisional_probe.get("passed") is True,
        detail="local proof only; promotion also requires a staging restore drill",
        provisional=True,
    )

    ttl = evidence.get("ttl") or {}
    _check(
        checks,
        "ttl_manifest_dry_run",
        ttl.get("collection_group") == "memory_items"
        and ttl.get("field_path") == "expires_at_ts"
        and _ref(ttl.get("dry_run_evidence_ref")),
        evidence=ttl.get("dry_run_evidence_ref"),
    )
    _check(
        checks,
        "live_ttl_active",
        live
        and ttl_is_active(observations.get("ttl_rows") or [])
        and _ref(ttl.get("applied_evidence_ref")),
        evidence=ttl.get("applied_evidence_ref"),
        detail="read-only cloud verification; this checker never applies TTL",
    )

    backup = evidence.get("backup_policy") or {}
    residual_days = int(backup.get("residual_days") or 0)
    try:
        configured_residual_days = int(os.environ.get("DURABLE_MEMORY_BACKUP_RESIDUAL_DAYS", "30"))
    except ValueError:
        configured_residual_days = -1
    _check(
        checks,
        "named_backup_policy_and_residual_window",
        all(
            (
                _ref(backup.get("name")),
                _ref(backup.get("policy_ref")),
                _ref(backup.get("owner")),
                _ref(backup.get("evidence_ref")),
                1 <= residual_days <= 365,
                backup.get("complete_waits_for_residual_expiry") is True,
                _truthy(os.environ.get("DURABLE_MEMORY_BACKUP_POLICY_ATTESTED")),
                os.environ.get("DURABLE_MEMORY_BACKUP_POLICY_REF", "").strip()
                == backup.get("policy_ref"),
                configured_residual_days == residual_days,
            )
        ),
        evidence=backup.get("evidence_ref"),
        detail="no cryptographic-erasure shortcut is accepted",
    )

    memory_export = evidence.get("memory_export") or {}
    export_ttl_hours = int(memory_export.get("artifact_ttl_hours") or 0)
    _check(
        checks,
        "qualified_memory_export_delivery",
        all(
            (
                memory_export.get("asynchronous") is True,
                memory_export.get("encrypted_at_rest") is True,
                memory_export.get("actor_attributed") is True,
                memory_export.get("current_source_authorization_filter") is True,
                memory_export.get("generic_search_excluded") is True,
                1 <= export_ttl_hours <= 24,
                _truthy(os.environ.get("DURABLE_MEMORY_M2_EXPORT_DELIVERY_ATTESTED")),
                os.environ.get("DURABLE_MEMORY_EXPORT_BUCKET", "").strip()
                == memory_export.get("bucket"),
                os.environ.get("DURABLE_MEMORY_EXPORT_KMS_KEY_NAME", "").strip()
                == memory_export.get("kms_key_name"),
                _ref(memory_export.get("bucket")),
                _ref(memory_export.get("kms_key_name")),
                _ref(memory_export.get("delivery_design_ref")),
                _ref(memory_export.get("encryption_evidence_ref")),
                _ref(memory_export.get("expiry_evidence_ref")),
            )
        ),
        evidence=memory_export.get("delivery_design_ref"),
        detail="async encrypted <=24h export; inaccessible rows and omission counts absent",
    )

    if gate == "promotion":
        _pilot_checks(checks, evidence.get("pilot_results") or {})
        monitoring = evidence.get("monitoring") or {}
        alerts = monitoring.get("alert_policy_refs") or {}
        _check(
            checks,
            "monitoring_and_alerts",
            _ref(monitoring.get("dashboard_ref"))
            and _ref(monitoring.get("notification_channel_ref"))
            and REQUIRED_ALERTS == {key for key, value in alerts.items() if _ref(value)},
            evidence=monitoring.get("dashboard_ref"),
        )
        _check(
            checks,
            "kill_switch_rehearsal",
            _ref(monitoring.get("kill_switch_rehearsal_ref"))
            and monitoring.get("durable_work_remained_available") is True
            and monitoring.get("deletion_jobs_remained_active") is True,
            evidence=monitoring.get("kill_switch_rehearsal_ref"),
        )
        _check(
            checks,
            "rollback_rehearsal",
            _ref(monitoring.get("rollback_rehearsal_ref"))
            and monitoring.get("memory_off_after_rehearsal") is True,
            evidence=monitoring.get("rollback_rehearsal_ref"),
        )
    blocked = [row["check_id"] for row in checks if row["status"] == "BLOCKED"]
    provisional = [row["check_id"] for row in checks if row["status"] == "PROVISIONAL"]
    # Provisional checks are explicitly labelled and never substitute for the
    # live requirements above. They are informational once every blocking live
    # entry/promotion check passes.
    ready = not blocked and live
    decision = (
        "READY_FOR_AUTHORIZED_STAGING_PILOT"
        if ready and gate == "entry"
        else "READY_FOR_AUTHORIZED_M2_ROLLOUT"
        if ready
        else "BLOCKED"
    )
    result = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "gate": gate,
        "decision": decision,
        "ready": ready,
        "candidate_sha256": candidate,
        "checks": checks,
        "blocked_checks": blocked,
        "provisional_checks": provisional,
        "observed_release_state": "DISABLED",
        "unauthorized_tiers": ["M3", "M4", "M5", "M6"],
        "mutations_performed": 0,
    }
    result["runtime_binding"] = {
        "eligible": ready,
        "candidate_sha256": candidate,
        "attestation_sha256": attestation_hash(evidence),
        "workspace_id": workspace_id,
        "founder_actor_id": founder_actor_id,
        "enablement_authorized": ready,
        "detail": (
            "All technical gates passed. The checker is read-only; the release "
            "automation may bind these values and enable the authorized tier."
        ),
    }
    return result


def _load_membership_snapshot(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("members") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("membership snapshot must contain a members list")
    return [row for row in rows if isinstance(row, dict)]


async def _main(args: argparse.Namespace) -> int:
    evidence = json.loads(args.attestation.read_text(encoding="utf-8"))
    probe = await provisional_safety_probe()
    if args.live:
        observations = await live_observations(
            workspace_id=args.workspace_id, project=args.project, database=args.database
        )
    else:
        observations = {
            "mode": "OFFLINE",
            "members": _load_membership_snapshot(args.membership_snapshot),
            "ledger": {},
            "ttl_rows": [],
            "errors": {},
        }
    result = evaluate(
        evidence=evidence,
        gate=args.gate,
        workspace_id=args.workspace_id,
        founder_actor_id=args.founder_actor_id,
        observations=observations,
        provisional_probe=probe,
    )
    result["observation_errors"] = observations.get("errors") or {}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attestation", type=Path)
    parser.add_argument("--gate", choices=("entry", "promotion"), default="entry")
    parser.add_argument("--workspace-id")
    parser.add_argument("--founder-actor-id")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--membership-snapshot", type=Path)
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
    parser.add_argument("--database", default=os.environ.get("FIRESTORE_DATABASE", "(default)"))
    parser.add_argument("--print-candidate-hash", action="store_true")
    args = parser.parse_args()
    if args.print_candidate_hash:
        print(candidate_hash())
        return 0
    missing = [
        name
        for name in ("attestation", "workspace_id", "founder_actor_id")
        if not getattr(args, name)
    ]
    if missing:
        parser.error("required unless --print-candidate-hash: " + ", ".join(missing))
    if args.live and not args.project:
        parser.error("--live requires --project or GOOGLE_CLOUD_PROJECT")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
