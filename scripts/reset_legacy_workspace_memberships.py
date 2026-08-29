"""Inspect or remove obsolete pre-production workspace membership roles.

Dry-run is the default. Execution requires the same explicit workspace id in
both arguments and deletes only rows whose role is in the closed legacy set.
FOUNDER rows and unknown role values are never deleted by this command.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any

from services.actor_identity import WorkspaceRole
from services.durable_store import DurableStore, production_store
from services.hiring_contracts import stable_id, utc_now

_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
LEGACY_MEMBERSHIP_ROLES = frozenset({
    "OWNER", "REVIEWER", "HIRING_MANAGER", "INTERVIEWER", "OBSERVER",
})


async def inspect_legacy_memberships(
        workspace_id: str, *, store: DurableStore | None = None
        ) -> list[dict[str, str]]:
    """Return a content-minimal exact deletion plan; reject unknown roles."""

    if not _WORKSPACE_ID.fullmatch(workspace_id):
        raise ValueError("workspace id is invalid")
    durable = store or production_store()
    rows = await durable.list(
        "workspace_members", filters={"workspace_id": workspace_id}, limit=1000)
    unknown = sorted({
        str(row.get("role") or "") for row in rows
        if str(row.get("role") or "")
        not in {WorkspaceRole.FOUNDER.value, *LEGACY_MEMBERSHIP_ROLES}
    })
    if unknown:
        raise RuntimeError(
            "unknown membership roles require manual inspection: "
            + ", ".join(unknown))
    return sorted(({
        "membership_id": str(row.get("membership_id") or row.get("id") or ""),
        "actor_id": str(row.get("actor_id") or ""),
        "role": str(row.get("role") or ""),
        "status": str(row.get("status") or ""),
    } for row in rows if row.get("role") in LEGACY_MEMBERSHIP_ROLES),
        key=lambda row: row["membership_id"])


async def reset_legacy_memberships(
        workspace_id: str, *, execute: bool,
        confirm_workspace_id: str = "", store: DurableStore | None = None,
        ) -> dict[str, Any]:
    """Dry-run or delete only the inspected legacy-role membership rows."""

    durable = store or production_store()
    plan = await inspect_legacy_memberships(workspace_id, store=durable)
    if not execute:
        return {"status": "success", "dry_run": True, "workspace_id": workspace_id,
                "delete_count": len(plan), "memberships": plan}
    if confirm_workspace_id != workspace_id:
        raise ValueError("execution requires an exact --confirm-workspace-id match")
    for item in plan:
        membership_id = item["membership_id"]
        if not membership_id:
            raise RuntimeError("legacy membership has no stable id")
        audit_id = stable_id(
            "audit", workspace_id, "legacy_membership_reset", membership_id)
        await durable.create("audit", audit_id, {
            "schema_version": 2, "audit_id": audit_id,
            "founder_id": workspace_id, "workspace_id": workspace_id,
            "actor": "system:founder_only_role_reset",
            "actor_id": "system:founder_only_role_reset",
            "action": "workspace_membership.legacy_role_reset",
            "target": f"workspace_members/{membership_id}",
            "result": "success", "detail": f"removed_role={item['role']}",
            "created_at": utc_now(), "version": 1,
        })
        await durable.delete("workspace_members", membership_id)
    return {"status": "success", "dry_run": False,
            "workspace_id": workspace_id, "deleted_count": len(plan)}


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-workspace-id", default="")
    args = parser.parse_args()
    result = await reset_legacy_memberships(
        args.workspace_id, execute=args.execute,
        confirm_workspace_id=args.confirm_workspace_id)
    if result["dry_run"]:
        print(f"dry-run: {result['delete_count']} legacy memberships")
        for item in result["memberships"]:
            print(
                f"{item['membership_id']} actor={item['actor_id']} "
                f"role={item['role']} status={item['status']}")
        print("No data changed. Re-run with --execute and an exact "
              "--confirm-workspace-id to apply this plan.")
    else:
        print(f"deleted {result['deleted_count']} legacy memberships")


if __name__ == "__main__":
    asyncio.run(_main())
