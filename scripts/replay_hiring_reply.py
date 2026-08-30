#!/usr/bin/env python3
"""Dry-run or replay one exact misclassified Hiring reply receipt.

The command is intentionally narrow. It cannot select a candidate from email
text, scan a mailbox, or replay arbitrary messages. ``--execute`` reloads only
the provider message already bound to the supplied durable correlation id.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.hiring_coordination import HiringCoordinationService  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--correlation-id", required=True)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    result = await HiringCoordinationService().replay_misclassified_reply(
        workspace_id=args.workspace_id,
        correlation_id=args.correlation_id,
        execute=args.execute,
    )
    safe = {key: result.get(key) for key in (
        "status", "error", "error_code", "dry_run", "eligible", "replayed",
        "duplicate", "correlation_id", "candidate_application_id",
        "continuation_status", "continuation_error_code") if key in result}
    print(json.dumps(safe, sort_keys=True))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_args())))
