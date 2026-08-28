"""Allowlist-only, provenance-preserving context assembly for shadow runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ContextRecord:
    record_id: str
    record_type: str
    workspace_id: str
    run_id: str = ""
    entity_id: str = ""
    session_id: str = ""
    data_class: str = "WORKSPACE_INTERNAL"
    provenance_ref: str = ""
    version: int = 1
    current: bool = True
    content: str = ""


@dataclass(frozen=True)
class ContextRequest:
    workspace_id: str
    run_id: str
    entity_id: str
    session_id: str
    allowed_record_types: frozenset[str]
    allowed_data_classes: frozenset[str]
    max_items: int
    max_bytes: int
    allow_raw_chat: bool = False


@dataclass(frozen=True)
class BuiltContext:
    records: tuple[ContextRecord, ...]
    context_hash: str
    omitted_count: int


def build_context(records: tuple[ContextRecord, ...], request: ContextRequest) -> BuiltContext:
    """Filter caller-supplied trusted projections; this function reads no store."""

    accepted: list[ContextRecord] = []
    used = 0
    for row in sorted(records, key=lambda item: (item.record_type, item.record_id, item.version)):
        if (not row.current or row.workspace_id != request.workspace_id
                or row.record_type not in request.allowed_record_types
                or row.data_class not in request.allowed_data_classes
                or not row.provenance_ref):
            continue
        if row.run_id and row.run_id != request.run_id:
            continue
        if row.entity_id and row.entity_id != request.entity_id:
            continue
        if row.record_type == "raw_chat":
            if not request.allow_raw_chat or row.session_id != request.session_id:
                continue
        size = len(row.content.encode("utf-8"))
        if len(accepted) >= request.max_items or used + size > request.max_bytes:
            continue
        accepted.append(row)
        used += size
    material = [{
        "record_id": row.record_id, "record_type": row.record_type,
        "workspace_id": row.workspace_id, "run_id": row.run_id,
        "entity_id": row.entity_id, "session_id": row.session_id,
        "data_class": row.data_class, "provenance_ref": row.provenance_ref,
        "version": row.version,
    } for row in accepted]
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return BuiltContext(tuple(accepted), f"sha256:{digest}", len(records) - len(accepted))
