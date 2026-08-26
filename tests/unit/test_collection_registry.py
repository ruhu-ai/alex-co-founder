"""Collection-registry coverage (docs/23 §9.7, docs/02 north-star rule).

The registry in services/firestore.py is the enumeration that future
export/deletion and coverage checks derive from. These tests make it
impossible to touch a Firestore collection from services/ or app/ without
registering it — the drift that let `founder_state` exist outside every
accessor for months.
"""

from __future__ import annotations

import re
from pathlib import Path

from services import firestore

REPO = Path(__file__).resolve().parents[2]

# `.collection("literal")` — the only sanctioned way code names a collection.
_COLLECTION_LITERAL = re.compile(r"\.collection\(\s*[\"']([A-Za-z0-9_]+)[\"']")

# Scanned trees. mock_portal/ is a separate service with its own store and is
# deliberately out of scope; tests fabricate collections freely.
_SCANNED_DIRS = ("services", "app", "agents")


def _collection_literals() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for tree in _SCANNED_DIRS:
        for path in sorted((REPO / tree).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for match in _COLLECTION_LITERAL.finditer(text):
                found.setdefault(match.group(1), []).append(
                    str(path.relative_to(REPO)))
    return found


def test_every_collection_literal_is_registered():
    unregistered = {
        name: sorted(set(files))
        for name, files in _collection_literals().items()
        if name not in firestore.REGISTERED_COLLECTIONS
    }
    assert not unregistered, (
        "Unregistered Firestore collections found. Add them to "
        "services.firestore.TOP_LEVEL_COLLECTIONS (or SUBCOLLECTIONS) so "
        f"export/deletion coverage can enumerate them: {unregistered}")


def test_registry_has_no_dead_entries():
    """Every registered top-level collection is actually referenced somewhere.

    Keeps the registry honest in the other direction: a renamed or removed
    collection must leave the registry in the same change. The three docs/23
    projections are exempt until their accessors land (they are asserted
    present by name below instead).
    """
    referenced = set(_collection_literals())
    grace = {"resource_index", "session_resource_links", "session_catalog"}
    dead = firestore.REGISTERED_COLLECTIONS - referenced - grace
    assert not dead, f"Registered but unreferenced collections: {sorted(dead)}"


def test_docs23_projections_are_registered():
    for name in ("resource_index", "session_resource_links", "session_catalog"):
        assert name in firestore.TOP_LEVEL_COLLECTIONS
    assert "founder_state" in firestore.TOP_LEVEL_COLLECTIONS


def test_docs24_safety_records_are_registered_and_lifecycle_covered():
    from services import data_lifecycle

    expected = {"data_connections", "source_grants", "external_events",
                "founder_inbox", "external_actions"}
    assert expected.issubset(firestore.TOP_LEVEL_COLLECTIONS)
    assert data_lifecycle.registry_coverage()["status"] == "success"
