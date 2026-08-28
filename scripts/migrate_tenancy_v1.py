"""Dry-run/apply the receipted Phase 2A workspace/discriminator backfill."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import firestore  # noqa: E402
from services.durable_store import production_store  # noqa: E402
from services.tenancy_migrations import DOMAIN_FIELDS, migrate_tenant_row  # noqa: E402


async def main(apply: bool, owner_override: str) -> int:
    manifest = {
        "schema_version": 1, "migration": "workspace-discriminator-v1",
        "mode": "apply" if apply else "dry_run", "collections": {},
        "explicit_owner_override": owner_override or None,
        "failed": [],
    }
    store = production_store()
    for collection in sorted(DOMAIN_FIELDS):
        snapshots = [item async for item in firestore.get_client().collection(
            collection).stream()]
        record = {"total": len(snapshots), "migrated": 0, "current": 0}
        manifest["collections"][collection] = record
        for snapshot in snapshots:
            row = snapshot.to_dict()
            field = DOMAIN_FIELDS[collection][0]
            if row.get("workspace_id") and row.get(field):
                record["current"] += 1
                continue
            if not apply:
                continue
            result = await migrate_tenant_row(
                store, collection=collection, document_id=snapshot.id,
                owner_override=owner_override)
            if result.get("error"):
                manifest["failed"].append({
                    "collection": collection, "document_id": snapshot.id,
                    "error_code": result.get("error_code")})
            else:
                record["migrated"] += int(not result.get("duplicate"))
    print(json.dumps(manifest, sort_keys=True))
    return 1 if manifest["failed"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--assign-unowned-to-workspace", default="",
        help=("Explicit owner for legacy rows with no durable owner. Existing "
              "different owners are refused; the choice is receipted."))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(
        args.apply, args.assign_unowned_to_workspace)))
