#!/usr/bin/env python3
"""Apply the reviewed Firestore TTL manifest before a Cloud Run rollout."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "infra" / "firestore.ttl.json"


def load_policies(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    policies = payload.get("policies")
    if not isinstance(policies, list) or not policies:
        raise ValueError("Firestore TTL manifest has no policies")
    seen: set[str] = set()
    clean: list[dict[str, Any]] = []
    for row in policies:
        if not isinstance(row, dict):
            raise ValueError("Firestore TTL policy must be an object")
        collection = str(row.get("collectionGroup") or "")
        field = str(row.get("fieldPath") or "")
        if (not collection or not field or row.get("ttl") is not True
                or collection in seen):
            raise ValueError("Firestore TTL policy is invalid or duplicated")
        seen.add(collection)
        clean.append({"collectionGroup": collection, "fieldPath": field,
                      "ttl": True})
    return clean


def command_for(policy: dict[str, Any], *, project: str,
                database: str) -> list[str]:
    return [
        "gcloud", "firestore", "fields", "ttls", "update",
        str(policy["fieldPath"]),
        f"--collection-group={policy['collectionGroup']}",
        f"--database={database}", f"--project={project}",
        "--enable-ttl", "--quiet",
    ]


def apply_policies(*, project: str, database: str, manifest: Path,
                   dry_run: bool = False) -> dict[str, Any]:
    policies = load_policies(manifest)
    commands = [command_for(row, project=project, database=database)
                for row in policies]
    if not dry_run:
        for command in commands:
            # No --async: a zero exit status means the control-plane operation
            # completed rather than merely being submitted.
            subprocess.run(command, check=True)
    return {"status": "success", "applied": 0 if dry_run else len(commands),
            "declared": len(commands), "commands": commands if dry_run else []}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--database", default="(default)")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply_policies(
        project=args.project, database=args.database,
        manifest=args.manifest, dry_run=args.dry_run), sort_keys=True))


if __name__ == "__main__":
    main()
