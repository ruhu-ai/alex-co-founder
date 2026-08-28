"""Dry-run/apply the idempotent Phase 1 workflow run migration.

Usage:
  python scripts/migrate_workflow_runtime.py          # inventory only
  python scripts/migrate_workflow_runtime.py --apply  # fenced migration
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import firestore  # noqa: E402
from services.durable_store import production_store  # noqa: E402
from services.workflow_migrations import (  # noqa: E402
    link_grant_application_run,
    migrate_run,
)


async def main(apply: bool) -> int:
    snapshots = [row async for row in firestore.get_client().collection(
        "workflow_runs").stream()]
    application_snapshots = [row async for row in firestore.get_client().collection(
        "applications").stream()]
    manifest = {
        "schema_version": 1, "migration": "workflow-runtime-v2",
        "mode": "apply" if apply else "dry_run", "total": len(snapshots),
        "migrated": 0, "already_current": 0,
        "applications": {"total": len(application_snapshots),
                         "linked": 0, "already_current": 0},
        "failed": [],
    }
    if apply:
        store = production_store()
        for snapshot in snapshots:
            result = await migrate_run(store, snapshot.id)
            if result.get("error"):
                manifest["failed"].append({
                    "run_id": snapshot.id,
                    "error_code": result.get("error_code")})
            elif result.get("duplicate"):
                manifest["already_current"] += 1
            else:
                manifest["migrated"] += 1
        for snapshot in application_snapshots:
            result = await link_grant_application_run(store, snapshot.id)
            if result.get("error"):
                manifest["failed"].append({
                    "application_id": snapshot.id,
                    "error_code": result.get("error_code")})
            elif result.get("duplicate"):
                manifest["applications"]["already_current"] += 1
            else:
                manifest["applications"]["linked"] += 1
    else:
        manifest["already_current"] = sum(
            int((snapshot.to_dict().get("runtime_status_schema_version") or 0) >= 2)
            for snapshot in snapshots)
    print(json.dumps(manifest, sort_keys=True))
    return 1 if manifest["failed"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    raise SystemExit(asyncio.run(main(parser.parse_args().apply)))
