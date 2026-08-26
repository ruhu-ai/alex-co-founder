#!/usr/bin/env python3
"""Install and verify every composite index in the reviewed manifest.

This is a deploy-time command, not an application worker.  It intentionally
waits for ``gcloud`` to finish each create operation and then fails closed if
any declared index is absent or not READY.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "infra" / "firestore.indexes.json"


def _field_signature(field: dict[str, Any]) -> tuple[str, str, str]:
    """Return the comparison form for one manifest or gcloud index field."""
    return (
        str(field.get("fieldPath", "")),
        str(field.get("order", "")).upper(),
        str(field.get("arrayConfig", "")).upper(),
    )


def index_signature(index: dict[str, Any]) -> tuple[
        str, str, tuple[tuple[str, str, str], ...]]:
    """Return a stable signature, excluding Firestore's implicit __name__."""
    collection_group = str(index.get("collectionGroup", ""))
    if not collection_group:
        name = str(index.get("name", ""))
        marker = "/collectionGroups/"
        if marker in name:
            collection_group = name.split(marker, 1)[1].split("/", 1)[0]
    fields = tuple(
        _field_signature(field)
        for field in index.get("fields", [])
        if field.get("fieldPath") != "__name__"
    )
    return (
        collection_group,
        str(index.get("queryScope", "COLLECTION")).upper(),
        fields,
    )


def load_manifest(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    """Load and validate the source-controlled index manifest."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    indexes = payload.get("indexes")
    if not isinstance(indexes, list) or not indexes:
        raise ValueError("Firestore index manifest has no indexes")
    signatures = [index_signature(index) for index in indexes]
    if any(not group or not fields for group, _scope, fields in signatures):
        raise ValueError("Firestore index manifest contains an incomplete index")
    if len(set(signatures)) != len(signatures):
        raise ValueError("Firestore index manifest contains duplicate indexes")
    return indexes


def _run_json(command: list[str]) -> Any:
    completed = subprocess.run(
        command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout or "[]")


def list_indexes(project: str, database: str) -> list[dict[str, Any]]:
    """Read the complete installed composite-index inventory through gcloud."""
    result = _run_json([
        "gcloud", "firestore", "indexes", "composite", "list",
        f"--project={project}", f"--database={database}", "--format=json",
    ])
    if not isinstance(result, list):
        raise RuntimeError("gcloud returned an invalid Firestore index inventory")
    return result


def create_command(index: dict[str, Any], project: str,
                   database: str) -> list[str]:
    """Build one non-async gcloud create command from a manifest row."""
    scope = str(index.get("queryScope", "COLLECTION")).lower().replace("_", "-")
    command = [
        "gcloud", "firestore", "indexes", "composite", "create", "--quiet",
        f"--project={project}", f"--database={database}",
        f"--collection-group={index['collectionGroup']}",
        f"--query-scope={scope}",
    ]
    for field in index["fields"]:
        path = field["fieldPath"]
        if "arrayConfig" in field:
            config = f"field-path={path},array-config=contains"
        else:
            order = str(field["order"]).lower()
            config = f"field-path={path},order={order}"
        command.append(f"--field-config={config}")
    return command


def ensure_indexes(project: str, database: str = "(default)",
                   manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, int]:
    """Create missing indexes and verify all manifest rows are installed READY."""
    declared = load_manifest(manifest_path)
    installed = list_indexes(project, database)
    installed_signatures = {index_signature(index) for index in installed}
    missing = [index for index in declared
               if index_signature(index) not in installed_signatures]
    for index in missing:
        subprocess.run(create_command(index, project, database), check=True)

    verified = list_indexes(project, database)
    verified_by_signature = {
        index_signature(index): index for index in verified
    }
    absent = [index_signature(index) for index in declared
              if index_signature(index) not in verified_by_signature]
    if absent:
        raise RuntimeError(f"Firestore indexes still absent after deploy: {absent}")
    not_ready = [signature for signature, index in verified_by_signature.items()
                 if signature in {index_signature(row) for row in declared}
                 and str(index.get("state", "")).upper() != "READY"]
    if not_ready:
        raise RuntimeError(f"Firestore indexes are not READY: {not_ready}")
    return {"declared": len(declared), "created": len(missing),
            "ready": len(declared)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--database", default="(default)")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    print(json.dumps(ensure_indexes(
        args.project, args.database, args.manifest), sort_keys=True))


if __name__ == "__main__":
    main()
