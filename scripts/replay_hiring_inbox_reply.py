#!/usr/bin/env python3
"""Dry-run or replay one exact Hiring reply stranded in the Founder inbox."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.external_event_service import replay_inboxed_hiring_reply  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--inbox-item-id", required=True)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    result = await replay_inboxed_hiring_reply(
        args.workspace_id, args.event_id, args.inbox_item_id,
        execute=args.execute)
    safe = {key: result.get(key) for key in (
        "status", "error", "error_code", "dry_run", "eligible", "replayed",
        "duplicate", "event_id", "inbox_item_id", "candidate_application_id",
        "correlation_id", "continuation_status", "continuation_error_code",
    ) if key in result}
    print(json.dumps(safe, sort_keys=True))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_args())))
