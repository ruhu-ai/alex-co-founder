"""Immutable candidate and entry-attestation bindings for Spec 39 M2.

This module is intentionally dependency-light so both the read-only release
checker and the application runtime calculate the same candidate identity.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

CANDIDATE_FILES = (
    ".env.example",
    "agents/co_founder/callbacks.py",
    "agents/co_founder/instructions.py",
    "agents/co_founder/state_schema.py",
    "app/live.py",
    "app/main.py",
    "app/static/index.html",
    "app/static/m2-pilot.html",
    "docs/39-durable-cross-session-memory.md",
    "infra/firestore.indexes.json",
    "infra/firestore.ttl.json",
    "infra/durable-memory-m2-release-attestation.example.json",
    "scripts/check_durable_memory_m2_release.py",
    "scripts/check_spec39_safe_path.py",
    "scripts/deploy_firestore_ttl.py",
    "scripts/local_m2_pilot.py",
    "scripts/migrate_durable_memory_m2.py",
    "scripts/run_spec39_m2_promotion_measurement.py",
    "scripts/validate_local_m2_pilot.py",
    "services/data_lifecycle.py",
    "services/durable_memory.py",
    "services/durable_memory_release.py",
    "services/durable_store.py",
    "services/firestore.py",
    "services/local_pilot_store.py",
    "services/memory_export.py",
    "services/session_deletion.py",
    "services/workspace_brief.py",
)


@lru_cache(maxsize=4)
def candidate_hash(root: Path = ROOT) -> str:
    """Hash the exact load-bearing M1/M2 and release-control file bytes."""
    digest = hashlib.sha256()
    for relative in CANDIDATE_FILES:
        path = root / relative
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def attestation_hash(evidence: dict[str, Any]) -> str:
    """Return a deterministic content hash for a completed evidence record."""
    payload = json.dumps(
        evidence,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def is_sha256_binding(value: str) -> bool:
    """Accept only the canonical ``sha256:<64 lowercase hex>`` form."""
    prefix, separator, digest = str(value or "").partition(":")
    return (
        separator == ":"
        and prefix == "sha256"
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )
