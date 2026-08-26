"""Idempotent BrowserRun schema backfill for docs/22 deployments."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def missing_projection_fields(
    row: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """Return only absent fields so reruns never overwrite live state."""
    now = now or datetime.now(timezone.utc)
    ttl = timedelta(minutes=30 if row.get("kind") == "fill" else 5)
    defaults = {
        "application_id": None,
        "phase": None,
        "version": 1,
        "frame_seq": 0,
        "browser_generation": 0,
        "lease_generation": 1,
        "expires_at": (now + ttl).isoformat(),
        "blocked_reason": None,
    }
    return {key: value for key, value in defaults.items() if key not in row}


async def migrate() -> dict[str, int]:
    """Backfill old runs transactionally; startup reconciliation owns orphans.

    The absence check MUST happen inside the transaction. Computing it from the
    scan-time snapshot and then issuing a plain update is check-then-act: a run
    that commits frames between the scan and the write would have `version` and
    `frame_seq` rolled backward, and a regressed counter makes the next frame
    commit collide with an already-committed sequence number (the frame create
    then fails loudly rather than silently overwriting immutable evidence).
    """
    from google.cloud import firestore as gc_firestore

    from services import firestore

    client = firestore.get_client()
    changed = scanned = 0
    async for snapshot in client.collection("browser_runs").stream():
        scanned += 1
        if not missing_projection_fields(snapshot.to_dict()):
            continue  # cheap pre-filter; the transaction re-checks authoritatively
        transaction = client.transaction()

        @gc_firestore.async_transactional
        async def _backfill(txn, ref=snapshot.reference) -> bool:
            latest = await ref.get(transaction=txn)
            if not latest.exists:
                return False
            fields = missing_projection_fields(latest.to_dict())
            if not fields:
                return False
            txn.update(ref, fields)
            return True

        changed += int(await _backfill(transaction))
    return {"scanned": scanned, "changed": changed}
