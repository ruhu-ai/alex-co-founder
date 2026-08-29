#!/usr/bin/env python3
"""Suppress legacy optional-memory rows; never backfill or promote transcripts.

Dry-run is the default. ``--execute`` only adds the M2 fail-closed lifecycle
marker to pre-v2 rows. It does not create a new memory item, inspect sessions,
or copy content into another backend.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from services.durable_store import DurableStore, production_store
from services.workflow_contracts import utc_now


async def migrate(*, store: DurableStore, execute: bool = False) -> dict[str, Any]:
    if os.environ.get("PERSISTENT_MEMORY_BACKEND", "disabled") != "disabled":
        return {
            "status": "error", "error": True,
            "error_code": "generic_memory_adapter_enabled",
            "message": "Disable the generic ADK memory adapter before migration.",
        }
    rows = await store.list("memory_items", filters={}, limit=1000)
    legacy = [row for row in rows if int(row.get("schema_version") or 0) < 2]
    changed = 0
    conflicts = 0
    if execute:
        for row in legacy:
            memory_id = str(row.get("memory_id") or row.get("id") or "")
            version = int(row.get("version") or 0)
            if not memory_id or version < 1:
                conflicts += 1
                continue
            committed = await store.compare_and_set(
                "memory_items", memory_id, version, {
                "status": "SUPPRESSED",
                "lifecycle_status": "SUPPRESSED",
                "summary": "", "search_terms": [], "normalized_tags": [],
                "purpose_allowlist": [], "content_hash": "",
                "migration_disposition": "LEGACY_UNREVIEWED_NO_BACKFILL",
                "migration_at": utc_now(),
                })
            changed += bool(committed)
            conflicts += not bool(committed)
    return {
        "status": "success" if not conflicts else "error",
        "execute": execute, "legacy_rows": len(legacy),
        "suppressed": changed, "conflicts": conflicts,
        "new_memory_items": 0, "transcript_reads": 0,
        "managed_backend_calls": 0,
    }


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(await migrate(store=production_store(), execute=args.execute),
                     sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
