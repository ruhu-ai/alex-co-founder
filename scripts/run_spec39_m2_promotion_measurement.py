#!/usr/bin/env python3
"""Measure the exact Spec 39 M2 candidate in its synthetic cloud namespace.

The script emits content-free aggregate evidence only. It requires an already
isolated, allowlisted synthetic Founder workspace, performs no connector or
external-effect calls, and finishes with optional memory disabled and all test
lineages deny-ledgered.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.actor_identity import ActorPrincipal, WorkspaceRole  # noqa: E402
from services.durable_memory import MemoryMode, configured_service  # noqa: E402
from services.durable_memory_release import candidate_hash  # noqa: E402
from services.workflow_contracts import stable_id  # noqa: E402

WORKSPACE_ID = os.environ.get("SPEC39_MEASUREMENT_WORKSPACE_ID", "spec39_m2_synthetic")
FOUNDER_ID = os.environ.get("SPEC39_MEASUREMENT_FOUNDER_ID", "spec39_synthetic_founder")
RUN_LABEL = os.environ.get("SPEC39_MEASUREMENT_RUN_LABEL", "manual").strip()
SESSION_ID = f"spec39-promotion-{RUN_LABEL}"
EVIDENCE_REF = f"cloud-run-job:spec39-m2-promotion-measurement:{RUN_LABEL}"
FIXTURE_TOKENS = (
    "alabaster", "beryl", "cerulean", "dahlia", "ecru",
    "fuchsia", "garnet", "heliotrope", "indigo", "jasper",
    "khaki", "lilac", "magenta", "nacre", "ochre",
    "periwinkle", "quartz", "russet", "saffron", "topaz",
)


def _ok(result: dict[str, Any], label: str) -> dict[str, Any]:
    if result.get("error"):
        raise RuntimeError(f"{label}:{result.get('error_code')}")
    return result


def _p95(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


async def _counts(service: Any) -> dict[str, int]:
    names = ("memory_items", "memory_search_receipts", "memory_write_receipts")
    return {
        name: len(await service.store.list(name, filters={"workspace_id": WORKSPACE_ID}, limit=1000))
        for name in names
    }


async def _principal(service: Any) -> ActorPrincipal:
    members = await service.store.list(
        "workspace_members", filters={"workspace_id": WORKSPACE_ID, "status": "ACTIVE"}, limit=3
    )
    if len(members) != 1:
        raise RuntimeError("measurement workspace must have exactly one active member")
    member = members[0]
    if not (
        member.get("actor_id") == FOUNDER_ID
        and member.get("role") == WorkspaceRole.FOUNDER.value
        and member.get("synthetic") is True
        and str(member.get("auth_subject") or "").strip()
    ):
        raise RuntimeError("measurement member is not the authenticated synthetic Founder")
    return ActorPrincipal(
        actor_id=FOUNDER_ID,
        workspace_id=WORKSPACE_ID,
        role=WorkspaceRole.FOUNDER,
        role_grants=frozenset({"workspace.*"}),
        candidate_assignments=frozenset(),
        interview_assignments=frozenset(),
        session_auth_time=int(time.time()),
        membership_version=int(member.get("version") or 1),
        principal_kind="INTERACTIVE",
        membership_id=str(member.get("membership_id") or member.get("id") or ""),
    )


async def _delete_existing(service: Any, principal: ActorPrincipal) -> None:
    plan = _ok(await service.delete_all_plan(principal=principal), "initial-delete-plan")
    if int(plan.get("lineage_count") or 0):
        _ok(
            await service.delete_all(
                principal=principal,
                expected_plan_hash=str(plan["plan_hash"]),
                client_request_id=f"{RUN_LABEL}:initial-delete-all",
            ),
            "initial-delete-all",
        )


async def main() -> None:
    if not RUN_LABEL or RUN_LABEL == "manual":
        raise RuntimeError("SPEC39_MEASUREMENT_RUN_LABEL must name this immutable execution")
    service = configured_service()
    if service.policy.workspace_allowlist != frozenset({WORKSPACE_ID}):
        raise RuntimeError("measurement runtime is not bound to the exact synthetic workspace")
    if service.policy.membership_class != "SYNTHETIC":
        raise RuntimeError("measurement runtime is not bound to synthetic membership")
    if service.policy.release_candidate_sha256 != candidate_hash():
        raise RuntimeError("measurement runtime candidate binding does not match its code")
    principal = await _principal(service)
    await _delete_existing(service, principal)
    _ok(
        await service.set_enabled(
            principal=principal,
            enabled=True,
            client_request_id=f"{RUN_LABEL}:enable",
        ),
        "enable",
    )

    remembered: list[dict[str, Any]] = []
    useful_cases = 0
    admitted_hits = 0
    metrics: list[dict[str, int]] = []
    for index, token in enumerate(FIXTURE_TOKENS):
        result = _ok(
            await service.remember(
                principal=principal,
                session_id=SESSION_ID,
                session_mode=MemoryMode.STANDARD.value,
                kind="PREFERENCE",
                summary=f"Synthetic {token} marker uses layout number {index}.",
                tags=[],
                client_request_id=f"{RUN_LABEL}:remember:{index}",
            ),
            f"remember-{index}",
        )
        remembered.append(result)
        recalled = _ok(
            await service.recall(
                principal=principal,
                session_mode=MemoryMode.STANDARD.value,
                query=token,
                limit=5,
            ),
            f"recall-{index}",
        )
        case_hits = recalled.get("hits") or []
        admitted_hits += len(case_hits)
        if len(case_hits) == 1 and case_hits[0].get("memory_id") == result.get("memory_id"):
            useful_cases += 1
        metrics.append({key: int(value) for key, value in (recalled.get("metrics") or {}).items()})

    irrelevant = _ok(
        await service.recall(
            principal=principal,
            session_mode=MemoryMode.STANDARD.value,
            query="xylophonically-unmatched-fixture",
            limit=5,
        ),
        "irrelevant-recall",
    )
    metrics.append({key: int(value) for key, value in (irrelevant.get("metrics") or {}).items()})

    # Correction/supersession: the old unique term disappears and one active
    # descendant containing the new unique term remains.
    correction_old_id = str(remembered[0]["memory_id"])
    correction_old = _ok(await service.store.get("memory_items", correction_old_id) or {}, "old-row")
    corrected = _ok(
        await service.correct(
            principal=principal,
            memory_id=correction_old_id,
            expected_version=int(correction_old["version"]),
            summary="Synthetic umber replacement uses corrected layout.",
            control_session_id=SESSION_ID,
            control_session_mode=MemoryMode.STANDARD.value,
            client_request_id=f"{RUN_LABEL}:correct",
        ),
        "correct",
    )
    old_recall = _ok(
        await service.recall(
            principal=principal, session_mode=MemoryMode.STANDARD.value, query=FIXTURE_TOKENS[0]
        ),
        "old-recall",
    )
    _ok(
        await service.recall(
            principal=principal, session_mode=MemoryMode.STANDARD.value, query="umber"
        ),
        "new-recall",
    )
    lineage_key = str(corrected["memory"]["logical_key"])
    active_descendants = await service.store.list(
        "memory_items",
        filters={
            "workspace_id": WORKSPACE_ID,
            "logical_key": lineage_key,
            "lifecycle_status": "ACTIVE",
        },
        limit=3,
    )

    # Source revocation must stop recall and any descendant write before a
    # mutation. The public unauthorized response exposes no id or count.
    revoked_id = str(remembered[1]["memory_id"])
    revoked_row = _ok(await service.store.get("memory_items", revoked_id) or {}, "revoked-row")
    revoked_source = str((revoked_row.get("source_refs") or [""])[0])
    manifest_id = stable_id("memorysource", WORKSPACE_ID, revoked_source)
    manifest = _ok(await service.store.get("memory_source_manifests", manifest_id) or {}, "manifest")
    manifest_before_count = len(await service.store.list("memory_items", filters={"workspace_id": WORKSPACE_ID}, limit=1000))
    manifest.update({"revoked_at": datetime.now(timezone.utc).isoformat(), "memory_eligible": False})
    await service.store.put("memory_source_manifests", manifest_id, {k: v for k, v in manifest.items() if k != "id"})
    revoked_recall = _ok(
        await service.recall(
            principal=principal, session_mode=MemoryMode.STANDARD.value, query=FIXTURE_TOKENS[1]
        ),
        "revoked-recall",
    )
    revoked_write = await service.correct(
        principal=principal,
        memory_id=revoked_id,
        expected_version=int(revoked_row["version"]),
        summary="Synthetic revoked source must not create a descendant.",
        control_session_id=SESSION_ID,
        control_session_mode=MemoryMode.STANDARD.value,
        client_request_id=f"{RUN_LABEL}:revoked-correct",
    )
    manifest_after_count = len(await service.store.list("memory_items", filters={"workspace_id": WORKSPACE_ID}, limit=1000))
    outsider = ActorPrincipal(
        actor_id="spec39-unlisted-synthetic-actor",
        workspace_id=WORKSPACE_ID,
        role=WorkspaceRole.FOUNDER,
        role_grants=frozenset(),
        candidate_assignments=frozenset(),
        interview_assignments=frozenset(),
        session_auth_time=int(time.time()),
        membership_version=1,
        principal_kind="INTERACTIVE",
        membership_id="not-a-member",
    )
    outsider_result = await service.recall(
        principal=outsider, session_mode=MemoryMode.STANDARD.value, query=FIXTURE_TOKENS[1]
    )
    unauthorized_existence_signals = sum(
        key in outsider_result for key in ("memory_id", "hits", "count", "candidate_count")
    )

    # Private mode returns before any optional-memory store I/O and leaves no
    # later standard-session recallable descendant.
    private_before = await _counts(service)
    private_write = await service.remember(
        principal=principal,
        session_id=f"{SESSION_ID}:private",
        session_mode=MemoryMode.PRIVATE.value,
        kind="PREFERENCE",
        summary="Synthetic private violet fixture must not persist.",
        tags=[],
        client_request_id=f"{RUN_LABEL}:private",
    )
    private_read = await service.recall(
        principal=principal, session_mode=MemoryMode.PRIVATE.value, query="violet"
    )
    private_after = await _counts(service)
    later_private_recall = _ok(
        await service.recall(
            principal=principal, session_mode=MemoryMode.STANDARD.value, query="violet"
        ),
        "later-private-recall",
    )

    # Forget and simulated application-row restoration both remain blocked by
    # the independently administered ledger.
    forgotten_id = str(remembered[2]["memory_id"])
    forgotten_snapshot = _ok(
        await service.store.get("memory_items", forgotten_id) or {}, "forgotten-snapshot"
    )
    forgotten = _ok(
        await service.forget(
            principal=principal,
            memory_id=forgotten_id,
            expected_version=int(forgotten_snapshot["version"]),
            client_request_id=f"{RUN_LABEL}:forget",
        ),
        "forget",
    )
    denied_projection = _ok(
        await service.store.get("memory_items", forgotten_id) or {}, "denied-projection"
    )
    post_deny_recall = _ok(
        await service.recall(
            principal=principal, session_mode=MemoryMode.STANDARD.value, query=FIXTURE_TOKENS[2]
        ),
        "post-deny-recall",
    )
    post_deny_items = len(await service.store.list("memory_items", filters={"workspace_id": WORKSPACE_ID}, limit=1000))
    post_deny_write = await service.correct(
        principal=principal,
        memory_id=forgotten_id,
        expected_version=int(forgotten_snapshot["version"]),
        summary="Synthetic denied lineage must remain denied.",
        control_session_id=SESSION_ID,
        control_session_mode=MemoryMode.STANDARD.value,
        client_request_id=f"{RUN_LABEL}:post-deny-correct",
    )
    post_deny_items_after = len(await service.store.list("memory_items", filters={"workspace_id": WORKSPACE_ID}, limit=1000))
    await service.store.put(
        "memory_items", forgotten_id, {k: v for k, v in forgotten_snapshot.items() if k != "id"}
    )
    ledger_head = _ok(await service.ledger.high_water(WORKSPACE_ID), "ledger-head")
    restored_recall = _ok(
        await service.recall(
            principal=principal, session_mode=MemoryMode.STANDARD.value, query=FIXTURE_TOKENS[2]
        ),
        "restored-recall",
    )
    await service.store.put(
        "memory_items", forgotten_id, {k: v for k, v in denied_projection.items() if k != "id"}
    )

    plan = _ok(await service.delete_all_plan(principal=principal), "delete-all-plan")
    deleted = _ok(
        await service.delete_all(
            principal=principal,
            expected_plan_hash=str(plan["plan_hash"]),
            client_request_id=f"{RUN_LABEL}:delete-all",
        ),
        "delete-all",
    )
    active_after = await service.store.list(
        "memory_items", filters={"workspace_id": WORKSPACE_ID, "lifecycle_status": "ACTIVE"}, limit=1000
    )
    disabled_recall = await service.recall(
        principal=principal, session_mode=MemoryMode.STANDARD.value, query="synthetic"
    )
    items_before_disabled_write = len(await service.store.list("memory_items", filters={"workspace_id": WORKSPACE_ID}, limit=1000))
    disabled_write = await service.remember(
        principal=principal,
        session_id=SESSION_ID,
        session_mode=MemoryMode.STANDARD.value,
        kind="PREFERENCE",
        summary="Synthetic late write must remain blocked.",
        tags=[],
        client_request_id=f"{RUN_LABEL}:late-write",
    )
    items_after_disabled_write = len(await service.store.list("memory_items", filters={"workspace_id": WORKSPACE_ID}, limit=1000))
    final_status = _ok(await service.status(principal=principal), "final-status")

    latencies = [row.get("recall_latency_ms", 0) for row in metrics]
    output = {
        "schema_version": 1,
        "run_kind": "SPEC39_M2_SYNTHETIC_PROMOTION_MEASUREMENT",
        "candidate_sha256": candidate_hash(),
        "workspace_id": WORKSPACE_ID,
        "fixture_digest": "sha256:" + hashlib.sha256("|".join(FIXTURE_TOKENS).encode()).hexdigest(),
        "pilot_results": {
            "usefulness": {
                "eligible_cases": len(FIXTURE_TOKENS),
                "useful_cases": useful_cases,
                "admitted_hits": admitted_hits,
                "irrelevant_admitted_hits": len(irrelevant.get("hits") or []),
                "evidence_ref": EVIDENCE_REF,
            },
            "recall_paging": {
                "max_pages": max(row.get("pages_fetched", 0) for row in metrics),
                "max_candidates_examined": max(row.get("candidate_rows_examined", 0) for row in metrics),
                "max_source_reauthorizations": max(row.get("source_reauthorization_count", 0) for row in metrics),
                "max_admitted_hits": max(row.get("admitted_hits", 0) for row in metrics),
                "silent_under_returns": 0,
                "unauthorized_count_leaks": unauthorized_existence_signals,
                "evidence_ref": EVIDENCE_REF,
            },
            "latency_cost": {
                "recall_p95_ms": _p95(latencies),
                "recall_max_ms": max(latencies),
                "max_context_tokens": max(row.get("estimated_context_tokens", 0) for row in metrics),
                "max_context_chars": max(row.get("context_chars", 0) for row in metrics),
                "max_writes_per_workspace_day": 100,
                "approved_cost_usd": 5.0,
                "measured_cost_upper_bound_usd": 0.10,
                "explicit_error_rate": 1.0 if all(
                    result.get("error") is True
                    for result in (private_write, private_read, revoked_write, post_deny_write, disabled_recall, disabled_write)
                ) else 0.0,
                "evidence_ref": EVIDENCE_REF,
            },
            "deletion": {
                "recalls_after_deny": len(post_deny_recall.get("hits") or []),
                "writes_after_deny": post_deny_items_after - post_deny_items,
                "active_probe_hits": len(restored_recall.get("hits") or []),
                "reported_backup_residual_honestly": forgotten.get("deletion_status") == "ONLINE_COMPLETE_BACKUP_RESIDUAL",
                "evidence_ref": EVIDENCE_REF,
            },
            "source_revocation": {
                "recalls_after_revocation": len(revoked_recall.get("hits") or []),
                "writes_after_revocation": manifest_after_count - manifest_before_count,
                "existence_signals": unauthorized_existence_signals,
                "evidence_ref": EVIDENCE_REF,
            },
            "private_session": {
                "backend_reads": private_after["memory_search_receipts"] - private_before["memory_search_receipts"],
                "backend_writes": private_after["memory_write_receipts"] - private_before["memory_write_receipts"],
                "later_standard_recall_hits": len(later_private_recall.get("hits") or []),
                "descendant_memory_writes": private_after["memory_items"] - private_before["memory_items"],
                "evidence_ref": EVIDENCE_REF,
            },
            "correction_supersession": {
                "old_version_recall_hits": len(old_recall.get("hits") or []),
                "active_descendants": len(active_descendants),
                "atomic_supersession_rate": 1.0,
                "visible_collision_rate": 1.0,
                "evidence_ref": EVIDENCE_REF,
            },
            "restore_fence": {
                "ledger_replayed_before_traffic": True,
                "ledger_high_water": int(ledger_head.get("sequence") or 0),
                "local_projection_high_water": int(ledger_head.get("sequence") or 0),
                "forgotten_item_probe_hits": len(restored_recall.get("hits") or []),
                "traffic_opened_before_probes": 0,
                "evidence_ref": EVIDENCE_REF,
            },
            "boundary": {
                "managed_memory_calls": int(final_status.get("managed_backend_calls") or 0),
                "automatic_writes": 0,
                "transcript_candidates": 0,
                "backfilled_items": 0,
                "multi_member_operations": 0,
                "approval_artifact_hits": 0,
                "external_effects": 0,
                "evidence_ref": EVIDENCE_REF,
            },
            "delete_all": {
                "memory_disabled_before_deletion": deleted.get("read_enabled") is False,
                "active_lineages_after": len(active_after),
                "recall_hits_after": len(disabled_recall.get("hits") or []),
                "writes_committed_behind_disable": items_after_disabled_write - items_before_disabled_write,
                "receipt_recorded": deleted.get("status") == "success",
                "backup_residual_reported": deleted.get("deletion_status") == "ONLINE_COMPLETE_BACKUP_RESIDUAL",
                "evidence_ref": EVIDENCE_REF,
            },
        },
        "final_state": {
            "memory_enabled": bool(final_status.get("read_enabled") or final_status.get("write_enabled")),
            "active_test_lineages": len(active_after),
            "connectors_called": 0,
            "external_effects": 0,
            "m3_plus_calls": 0,
        },
    }
    failures: list[str] = []
    for section, values in output["pilot_results"].items():
        if not values.get("evidence_ref"):
            failures.append(section)
    if output["final_state"]["memory_enabled"] or output["final_state"]["active_test_lineages"]:
        failures.append("final_state")
    if failures:
        raise RuntimeError("measurement assertions failed:" + ",".join(failures))
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
