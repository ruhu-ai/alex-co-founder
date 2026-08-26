"""Dry-run-first additive migration for docs/24 M1–M5.

The migration never reads credential values, synthesizes historical events, or
deletes legacy rows.  Apply only creates deterministic canonical projections;
re-running skips rows already present.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from services import data_source_contracts as dsc
from services import firestore


def _legacy_file(row: Any) -> tuple[str, str] | None:
    if isinstance(row, str):
        return (row, row) if row else None
    if not isinstance(row, dict):
        return None
    provider_id = str(row.get("id") or row.get("file_id") or "")
    if not provider_id:
        return None
    return provider_id, str(row.get("name") or "Selected Drive file")[:240]


async def _plan(founder_id: str) -> dict[str, Any]:
    legacy = await firestore.get_legacy_data_source_snapshot(founder_id)
    files = [item for item in (_legacy_file(row)
                               for row in legacy.get("drive_files", [])) if item]
    connectors: list[str] = []
    if files:
        connectors.append("drive")
    if legacy.get("integrations_exists") and legacy.get("gmail_label_configured"):
        connectors.append("founder_gmail")
    connection_ids = {
        connector: dsc.data_connection_id(founder_id, connector, "default")
        for connector in connectors
    }
    grants = [{
        "source_grant_id": dsc.source_grant_id(
            founder_id, connection_ids["drive"], provider_id),
        "connection_id": connection_ids["drive"],
        "provider_source_id": provider_id, "display_name": name,
    } for provider_id, name in files] if "drive" in connection_ids else []
    plan_material = {
        "founder_id": founder_id, "connections": connection_ids,
        "source_grants": grants,
    }
    plan_hash = hashlib.sha256(json.dumps(
        plan_material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"legacy": legacy, "connections": connection_ids,
            "source_grants": grants, "plan_hash": plan_hash}


async def migrate(founder_id: str = "founder", *, apply: bool = False) -> dict[str, Any]:
    """Report or apply deterministic M1/M2 projections; M3–M5 are code cutovers."""
    plan = await _plan(founder_id)
    created_connections = created_grants = skipped = 0
    if apply:
        for connector, connection_id in plan["connections"].items():
            existing = await firestore.get_data_connection(founder_id, connection_id)
            if existing:
                skipped += 1
                continue
            created = await firestore.upsert_data_connection(
                founder_id, connector, account_ref="default",
                auth_kind="google_oauth", status="DEGRADED")
            if created.get("error"):
                return created | {"phase": "M2_CONNECTIONS"}
            created_connections += 1
        for grant in plan["source_grants"]:
            existing = await firestore.get_source_grant(
                founder_id, grant["source_grant_id"])
            if existing:
                skipped += 1
                continue
            created = await firestore.create_source_grant(
                founder_id, grant["connection_id"],
                grant["provider_source_id"],
                display_name=grant["display_name"],
                allowed_ingestion_scopes=["profile", "reference_only"])
            if created.get("error"):
                return created | {"phase": "M2_SOURCE_GRANTS"}
            created_grants += 1

    canonical_grants = await firestore.list_source_grants(founder_id)
    legacy_ids = sorted(item["provider_source_id"] for item in plan["source_grants"])
    canonical_ids = sorted(
        str(row.get("provider_source_id") or "") for row in canonical_grants
        if row.get("connection_id") == plan["connections"].get("drive"))
    parity = legacy_ids == canonical_ids if apply else None
    rollback_paths = [
        *(f"data_connections/{value}" for value in plan["connections"].values()),
        *(f"source_grants/{row['source_grant_id']}" for row in plan["source_grants"]),
    ]
    return {
        "status": "success", "dry_run": not apply,
        "plan_hash": plan["plan_hash"],
        "phases": {
            "M1": "schemas_registered",
            "M2": "planned" if not apply else "applied",
            "M3": "external_event_producers_cut_over",
            "M4": "external_action_producers_cut_over",
            "M5": "canonical_reads_with_legacy_projection",
        },
        "legacy_counts": {
            "selected_drive_ids": len(legacy_ids),
            "processed_gmail_ids": plan["legacy"]["processed_gmail_count"],
            "processed_alex_ids": plan["legacy"]["processed_alex_count"],
            "missing_owner_refs": plan["legacy"]["missing_owner_count"],
            "missing_session_refs": plan["legacy"]["missing_session_count"],
        },
        "target_counts": {"data_connections": len(plan["connections"]),
                          "source_grants": len(plan["source_grants"])},
        "created": {"data_connections": created_connections,
                    "source_grants": created_grants},
        "skipped_existing": skipped,
        "dual_read_parity": parity,
        "rollback": {
            "automatic_delete": False,
            "canonical_paths": sorted(rollback_paths),
            "instruction": ("Keep legacy reads enabled and remove only the exact "
                            "canonical paths after a separate reviewed inventory."),
        },
        "historical_events_synthesized": 0,
        "credential_values_read": False,
    }
