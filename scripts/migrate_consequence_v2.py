"""Dry-run/apply legacy internal-demo receipts onto external_actions."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import firestore  # noqa: E402
from services.consequence_migrations import (  # noqa: E402
    migrate_internal_demo_action,
    migrate_internal_demo_approval,
    migrate_legacy_grant_approval,
)
from services.durable_store import production_store  # noqa: E402


async def main(apply: bool) -> int:
    manifest: dict = {
        "schema_version": 1,
        "migration": "internal-demo-consequence-v2",
        "mode": "apply" if apply else "dry_run",
        "collections": {},
        "failed": [],
    }
    store = production_store()
    approval_snapshots = [row async for row in firestore.get_client().collection(
        "approvals").stream()]
    approval_counts = {"total": len(approval_snapshots), "migrated": 0,
                       "current": 0}
    manifest["collections"]["approvals"] = approval_counts
    for snapshot in approval_snapshots:
        row = snapshot.to_dict()
        if int(row.get("schema_version") or 1) >= 2:
            approval_counts["current"] += 1
            continue
        if not apply:
            continue
        result = await migrate_legacy_grant_approval(store, snapshot.id)
        if result.get("error"):
            manifest["failed"].append({
                "collection": "approvals", "document_id": snapshot.id,
                "error_code": result.get("error_code")})
        else:
            approval_counts["migrated"] += int(not result.get("duplicate"))
    migrations = {
        "internal_demo_approvals": (
            "approvals", migrate_internal_demo_approval),
        "internal_demo_actions": (
            "external_actions", migrate_internal_demo_action),
    }
    for collection, (target, migrate) in migrations.items():
        snapshots = [row async for row in firestore.get_client().collection(
            collection).stream()]
        counts = {"total": len(snapshots), "migrated": 0, "current": 0}
        manifest["collections"][collection] = counts
        for snapshot in snapshots:
            row = snapshot.to_dict()
            if row.get("migrated_to") == target:
                counts["current"] += 1
                continue
            if not apply:
                continue
            result = await migrate(store, snapshot.id)
            if result.get("error"):
                manifest["failed"].append({
                    "collection": collection, "document_id": snapshot.id,
                    "error_code": result.get("error_code")})
            else:
                counts["migrated"] += int(not result.get("duplicate"))
    print(json.dumps(manifest, sort_keys=True))
    return 1 if manifest["failed"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    raise SystemExit(asyncio.run(main(parser.parse_args().apply)))
