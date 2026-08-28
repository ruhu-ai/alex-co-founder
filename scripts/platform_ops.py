#!/usr/bin/env python3
"""Content-free operator CLI for docs/34 Phase 7.

The CLI uses Application Default Credentials and the same closed durable-store
registry as the application. It never accepts raw user content, provider
payloads, success overrides, or an action retry command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.platform_operations import PlatformOperationsService  # noqa: E402


def _json_file(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("evidence must be one JSON object")
    return value


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    service = PlatformOperationsService()
    if args.command == "inspect":
        return await service.inspect_workspace(args.workspace)
    if args.command == "governance":
        return await service.governance_report(args.workspace)
    if args.command == "slo-report":
        return await service.error_budget_report(
            workspace_id=args.workspace, objective_id=args.objective)
    evidence = _json_file(args.evidence)
    if args.command == "record-slo":
        return await service.record_slo_observation(**evidence)
    if args.command == "record-recovery":
        return await service.record_recovery_drill(**evidence)
    if args.command == "record-chaos":
        return await service.record_chaos_drill(**evidence)
    if args.command == "record-migration":
        return await service.record_migration_drill(**evidence)
    raise ValueError("unknown command")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "governance"):
        command = commands.add_parser(name)
        command.add_argument("--workspace", required=True)
    slo = commands.add_parser("slo-report")
    slo.add_argument("--workspace", required=True)
    slo.add_argument("--objective", required=True)
    for name in ("record-slo", "record-recovery", "record-chaos",
                 "record-migration"):
        command = commands.add_parser(name)
        command.add_argument(
            "--evidence", required=True,
            help="Path to a content-free JSON evidence record")
    return parser


def main() -> int:
    try:
        result = asyncio.run(_run(_parser().parse_args()))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": True,
                          "error_code": "operator_input_invalid",
                          "message": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True, default=str))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
