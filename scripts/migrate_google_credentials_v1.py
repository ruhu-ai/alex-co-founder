"""Retire a legacy global Google token through an explicit reconnect gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.credential_migrations import (  # noqa: E402
    retire_legacy_google_credential,
)
from services.durable_store import production_store  # noqa: E402


async def main(*, workspace_id: str, account: str, apply: bool,
               confirmed: bool) -> int:
    plan = {
        "schema_version": 1,
        "migration": "google-oauth-workspace-v1",
        "workspace_id": workspace_id,
        "account": account,
        "strategy": "RECONNECT_REQUIRED",
        "credential_values_read": False,
        "credential_values_copied": False,
        "mode": "apply" if apply else "dry_run",
    }
    if not apply:
        print(json.dumps(plan, sort_keys=True))
        return 0
    if not confirmed:
        raise SystemExit("--confirm-reconnect-required is required with --apply")
    result = await retire_legacy_google_credential(
        production_store(), workspace_id=workspace_id, account=account)
    print(json.dumps({**plan, "result": result}, sort_keys=True))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--account", choices=("founder", "alex"), required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-reconnect-required", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(
        workspace_id=args.workspace_id, account=args.account,
        apply=args.apply, confirmed=args.confirm_reconnect_required)))
