"""Session-linked resources and global search — contracts and services (docs/23).

Three app-owned projections make Alex's durable work discoverable from the
conversation that produced it:

- ``resource_index/{resource_id}``      one row per logical founder-visible resource
- ``session_resource_links/{link_id}``  one immutable occurrence per (resource, session)
- ``session_catalog/{session_id}``      bounded conversation search projection

Everything identity-bearing here is server-derived: models never choose
collections, relationships, tokens, or IDs (docs/23 §3 invariant 7). All
functions return errors as data, never raise to a model-facing caller.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1
CURSOR_VERSION = 1

_RESOURCE_ID_SALT = "resource-index:v1"
_LINK_ID_SALT = "session-resource-link:v1"

MAX_TITLE = 200
MAX_SUMMARY = 500
MAX_TERMS = 64
MAX_PREFIXES = 256
TOKEN_MIN = 2
TOKEN_MAX = 48
PREFIX_MIN = 2
PREFIX_MAX = 16
MAX_QUERY_CHARS = 160
MAX_REPRESENTATION_REFS = 8


# ---------------------------------------------------------------------------
# Closed enums (docs/23 §5.4). Plain string constants, matching state_schema.
# ---------------------------------------------------------------------------

class ResourceType:
    DISCOVERY_REQUEST = "discovery_request"
    OPPORTUNITY = "opportunity"
    APPLICATION = "application"
    DOCUMENT = "document"
    ARTIFACT = "artifact"
    BROWSER_REPORT = "browser_report"
    EVIDENCE_REPORT = "evidence_report"
    # Reserved for later slices; schema-valid, no producer yet.
    LEAD = "lead"
    WORKFLOW_RUN = "workflow_run"


class Relationship:
    CREATED = "created"
    DISCOVERED = "discovered"
    SELECTED = "selected"
    PRODUCED = "produced"
    CONTINUED = "continued"
    # Reserved enum values with no v1 producer (docs/23 §5.2).
    REFERENCED = "referenced"
    RESURFACED = "resurfaced"


class Visibility:
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    INTERNAL = "internal"


RELATIONSHIPS: frozenset[str] = frozenset({
    Relationship.CREATED, Relationship.DISCOVERED, Relationship.SELECTED,
    Relationship.PRODUCED, Relationship.CONTINUED,
    Relationship.REFERENCED, Relationship.RESURFACED,
})

VISIBILITIES: frozenset[str] = frozenset({
    Visibility.PRIMARY, Visibility.SUPPORTING, Visibility.INTERNAL,
})


@dataclass(frozen=True)
class ResourceTypeSpec:
    """One row of the code-owned resource registry (docs/23 §5.4)."""

    resource_type: str
    collection: str
    default_visibility: str
    focus_kind: str
    enabled: bool = True


# The closed registry: the ONLY collections a resource may canonically point
# at, and the focus kinds the search API may return. Never model text.
RESOURCE_REGISTRY: dict[str, ResourceTypeSpec] = {
    spec.resource_type: spec for spec in (
        ResourceTypeSpec(ResourceType.DISCOVERY_REQUEST, "discovery_requests",
                         Visibility.PRIMARY, "discovery_request"),
        ResourceTypeSpec(ResourceType.OPPORTUNITY, "opportunities",
                         Visibility.PRIMARY, "opportunity"),
        ResourceTypeSpec(ResourceType.APPLICATION, "applications",
                         Visibility.PRIMARY, "application"),
        ResourceTypeSpec(ResourceType.DOCUMENT, "documents",
                         Visibility.PRIMARY, "document"),
        ResourceTypeSpec(ResourceType.ARTIFACT, "artifacts",
                         Visibility.PRIMARY, "artifact"),
        ResourceTypeSpec(ResourceType.BROWSER_REPORT, "browser_runs",
                         Visibility.SUPPORTING, "browser_report"),
        ResourceTypeSpec(ResourceType.EVIDENCE_REPORT, "evidence_checks",
                         Visibility.SUPPORTING, "evidence_report"),
        ResourceTypeSpec(ResourceType.LEAD, "", Visibility.PRIMARY,
                         "lead", enabled=False),
        ResourceTypeSpec(ResourceType.WORKFLOW_RUN, "", Visibility.PRIMARY,
                         "workflow_run", enabled=False),
    )
}

SEARCHABLE_TYPES: frozenset[str] = frozenset(
    t for t, spec in RESOURCE_REGISTRY.items() if spec.enabled) | {"session"}


@dataclass(frozen=True)
class ResourceProducer:
    kind: str
    id: str
    output_key: str


@dataclass(frozen=True)
class RepresentationRef:
    kind: str
    ref: str  # opaque record id — never a URL (docs/23 §5.1)


# ---------------------------------------------------------------------------
# Text normalization, tokens, prefixes (docs/23 §7.2) — shared by write and
# read paths so indexed data and queries can never disagree.
# ---------------------------------------------------------------------------

def normalize_text(value: str) -> str:
    """NFKC-normalize, casefold, strip control/format chars, collapse spaces."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    # Strip control/format characters, but never whitespace controls (\t, \n)
    # — those are token separators and must survive until the collapse.
    value = "".join(
        ch for ch in value
        if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf"))
    return " ".join(value.casefold().split())


def _is_wordchar(ch: str) -> bool:
    return unicodedata.category(ch)[0] in ("L", "N")


_OPAQUE_TOKEN = re.compile(r"^(?=.*\d)[0-9a-f_-]{12,}$")


def _looks_opaque(token: str) -> bool:
    """Hashes/UUIDs/IDs index as whole terms but generate no prefixes."""
    return bool(_OPAQUE_TOKEN.fullmatch(token)) or len(token) > 24


def tokenize(value: str) -> list[str]:
    """Ordered unique tokens: letter/number runs, 2–48 chars, max 64."""
    normalized = normalize_text(value)
    tokens: list[str] = []
    seen: set[str] = set()
    current: list[str] = []

    def flush() -> None:
        token = "".join(current)
        current.clear()
        if TOKEN_MIN <= len(token) <= TOKEN_MAX and token not in seen:
            seen.add(token)
            tokens.append(token)

    for ch in normalized:
        if _is_wordchar(ch):
            current.append(ch)
        elif current:
            flush()
    if current:
        flush()
    return tokens[:MAX_TERMS]


def prefixes_for(tokens: list[str]) -> list[str]:
    """Bounded search prefixes: lengths 2–16 per non-opaque token, max 256.

    Opaque tokens (hashes, UUIDs, ids) contribute the WHOLE term and no
    prefix ladder. The whole term must still be present because the indexed
    query is a single `array_contains` over this field: omitting it made
    exact-id lookup silently return nothing in production while the test
    fake — which also matched `search_terms` — reported success.
    """
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if _looks_opaque(token):
            if token not in seen and len(out) < MAX_PREFIXES:
                seen.add(token)
                out.append(token)
            continue
        for length in range(PREFIX_MIN, min(len(token), PREFIX_MAX) + 1):
            prefix = token[:length]
            if prefix not in seen:
                seen.add(prefix)
                out.append(prefix)
                if len(out) >= MAX_PREFIXES:
                    return out
    return out


def search_fields(*texts: str) -> tuple[list[str], list[str]]:
    """(search_terms, search_prefixes) for one or more display strings."""
    tokens: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for token in tokenize(text or ""):
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    tokens = tokens[:MAX_TERMS]
    return tokens, prefixes_for(tokens)


def choose_probe_prefix(query_terms: list[str]) -> str | None:
    """Deterministic index probe: the longest per-term prefix (≤16 chars);
    ties break lexicographically (docs/23 §7.2 step 2)."""
    candidates = [t[:PREFIX_MAX] for t in query_terms
                  if len(t) >= PREFIX_MIN and not _looks_opaque(t)]
    if not candidates:
        # Opaque-only query: probe with the whole term for exact support.
        exact = [t for t in query_terms if len(t) >= PREFIX_MIN]
        return min(exact, default=None)
    return min(candidates, key=lambda p: (-len(p), p))


# ---------------------------------------------------------------------------
# Deterministic identities (docs/23 §5.1, §5.2)
# ---------------------------------------------------------------------------

def resource_id_for(founder_id: str, resource_type: str,
                    canonical_collection: str, canonical_id: str) -> str:
    payload = (f"{_RESOURCE_ID_SALT}{founder_id}{resource_type}"
               f"{canonical_collection}/{canonical_id}")
    return "r_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def link_id_for(founder_id: str, session_id: str, occurrence_key: str,
                resource_id: str, relationship: str) -> str:
    payload = (f"{_LINK_ID_SALT}{founder_id}{session_id}{occurrence_key}"
               f"{resource_id}{relationship}")
    return "l_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


# ---------------------------------------------------------------------------
# Cursor codec (docs/23 §7.1) — opaque, versioned, filter-bound, keyset on
# immutable per-stream frontiers. The frontier is the last SCANNED position.
# ---------------------------------------------------------------------------

def filter_binding(query: str, types: list[str], session_id: str | None) -> str:
    canonical = json.dumps(
        {"q": normalize_text(query)[:MAX_QUERY_CHARS],
         "types": sorted(set(types)),
         "session_id": session_id or ""},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def encode_cursor(binding: str, frontiers: dict[str, list[str] | None]) -> str:
    payload = json.dumps(
        {"v": CURSOR_VERSION, "f": binding, "s": frontiers},
        sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str, binding: str) -> dict[str, Any]:
    """Decode and validate. Returns {"error": True, ...} as data on any
    mismatch — a cursor issued for different filters is rejected."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return {"error": True, "message": "malformed cursor"}
    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        return {"error": True, "message": "unsupported cursor version"}
    if payload.get("f") != binding:
        return {"error": True, "message": "cursor does not match filters"}
    frontiers = payload.get("s")
    if not isinstance(frontiers, dict):
        return {"error": True, "message": "malformed cursor"}
    clean: dict[str, list[str] | None] = {}
    for stream, frontier in frontiers.items():
        if frontier is None:
            clean[stream] = None
        elif (isinstance(frontier, list) and len(frontier) == 2
              and all(isinstance(v, str) for v in frontier)):
            clean[stream] = frontier
        else:
            return {"error": True, "message": "malformed cursor"}
    return {"error": False, "frontiers": clean}


# ---------------------------------------------------------------------------
# Query normalization (docs/23 §7.1)
# ---------------------------------------------------------------------------

def normalize_query(q: str) -> str:
    return normalize_text(q or "")[:MAX_QUERY_CHARS]


def query_terms(q: str) -> list[str]:
    return tokenize(normalize_query(q))


def terms_match(candidate_terms: list[str], candidate_prefixes: list[str],
                q_terms: list[str]) -> bool:
    """Every query term must match a term or prefix in code (§7.2 step 5)."""
    terms = set(candidate_terms or [])
    prefs = set(candidate_prefixes or [])
    for term in q_terms:
        probe = term[:PREFIX_MAX]
        if term in terms or probe in prefs:
            continue
        if any(t.startswith(term) for t in terms):
            continue
        return False
    return True


# ---------------------------------------------------------------------------
# Service seam (docs/23 §6). All producers call register_session_resource;
# every identifier is server-resolved or validated against the closed
# registry. Errors are data — nothing here raises to a model-facing caller.
# ---------------------------------------------------------------------------

# v1-producible relationships: reserved values are schema-valid but have no
# producer, so the seam refuses them (docs/23 §5.2).
_PRODUCIBLE_RELATIONSHIPS = frozenset({
    Relationship.CREATED, Relationship.DISCOVERED, Relationship.SELECTED,
    Relationship.PRODUCED, Relationship.CONTINUED,
})

_session_exists = None  # wired by app startup; tools trust ToolContext.


def configure(*, session_exists=None) -> None:
    """Wire the founder-session ownership check (app/main.py startup)."""
    global _session_exists
    _session_exists = session_exists


async def verify_founder_session(session_id: str) -> bool:
    """Use the configured server-owned session lookup; fail closed."""
    if not session_id or _session_exists is None:
        return False
    try:
        return bool(await _session_exists(session_id))
    except Exception:
        return False


def _clip(value: str, limit: int) -> str:
    value = " ".join((value or "").split())
    return value[:limit]


async def register_session_resource(
    *,
    founder_id: str,
    session_id: str,
    resource_type: str,
    canonical_id: str,
    relationship: str,
    occurrence_key: str,
    producer_kind: str,
    producer_id: str,
    producer_output_key: str,
    title: str,
    summary: str = "",
    status: str = "",
    visibility: str | None = None,
    request_id: str | None = None,
    message_id: str | None = None,
    run_id: str | None = None,
    journey_id: str | None = None,
    parent_resource_id: str | None = None,
    representation_refs: tuple[RepresentationRef, ...] = (),
    session_verified: bool = False,
    extra_search_text: str = "",
) -> dict[str, Any]:
    """Register one canonical resource and its session occurrence.

    Repeating the same occurrence is a successful idempotent replay returning
    the existing IDs. A tombstoned occurrence is final (docs/23 §5.2).

    Args:
        founder_id: Server-resolved owner; never model text.
        session_id: Originating founder conversation; verified unless
            session_verified is True (ToolContext-derived callers).
        resource_type: Closed registry type (docs/23 §5.4).
        canonical_id: Document id inside the type's registered collection.
        relationship: One of the v1-producible relationship values.
        occurrence_key: Server-trusted idempotency identity for this
            occurrence (request id, invocation id, run output key, ...).
        producer_kind/producer_id/producer_output_key: Trusted producer
            identity recorded on the resource row.
        title/summary/status: Bounded founder-facing display projection.
        visibility: Defaults to the registry's default for the type.
        representation_refs: Opaque {kind, ref} record ids — never URLs.
        session_verified: True only when the session came from ToolContext.
        extra_search_text: Additional display text to index (e.g. executed
            queries for a discovery request) — sensitivity-filtered upstream.
    """
    from services import firestore

    spec = RESOURCE_REGISTRY.get(resource_type)
    if spec is None or not spec.enabled:
        return {"status": "error", "error": True,
                "message": f"unknown resource type: {resource_type!r}"}
    if relationship not in _PRODUCIBLE_RELATIONSHIPS:
        return {"status": "error", "error": True,
                "message": f"relationship has no producer: {relationship!r}"}
    if visibility is None:
        visibility = spec.default_visibility
    if visibility not in VISIBILITIES:
        return {"status": "error", "error": True,
                "message": f"unknown visibility: {visibility!r}"}
    if not founder_id or not session_id or not canonical_id or not occurrence_key:
        return {"status": "error", "error": True,
                "message": "founder, session, canonical id, and occurrence key are required"}
    for ref in representation_refs:
        if "://" in ref.ref or ref.ref.startswith("/"):
            return {"status": "error", "error": True,
                    "message": "representation refs must be opaque record ids, not URLs"}
    if not session_verified:
        if _session_exists is None:
            return {"status": "error", "error": True,
                    "message": "session verification is not configured"}
        if not await _session_exists(session_id):
            # Generic refusal: never reveals whether a foreign session exists.
            return {"status": "error", "error": True, "message": "not found"}

    title = _clip(title, MAX_TITLE)
    summary = _clip(summary, MAX_SUMMARY)
    resource_id = resource_id_for(founder_id, resource_type,
                                  spec.collection, canonical_id)
    link_id = link_id_for(founder_id, session_id, occurrence_key,
                          resource_id, relationship)
    terms, prefs = search_fields(title, summary, extra_search_text)

    resource_row = {
        "schema_version": SCHEMA_VERSION,
        "resource_id": resource_id,
        "founder_id": founder_id,
        "resource_type": resource_type,
        "canonical_ref": {"collection": spec.collection, "id": canonical_id},
        "producer": {"kind": producer_kind, "id": producer_id,
                     "output_key": producer_output_key},
        "origin": {"first_session_id": session_id, "run_id": run_id,
                   "journey_id": journey_id, "request_id": request_id,
                   "message_id": message_id},
        "title": title,
        "summary": summary,
        "status": status,
        "visibility": visibility,
        "content_hash": None,
        "search_terms": terms,
        "search_prefixes": prefs,
        "representation_refs": [
            {"kind": r.kind, "ref": r.ref}
            for r in representation_refs[:MAX_REPRESENTATION_REFS]],
    }
    link_row = {
        "schema_version": SCHEMA_VERSION,
        "link_id": link_id,
        "founder_id": founder_id,
        "session_id": session_id,
        "resource_id": resource_id,
        "resource_type": resource_type,
        "relationship": relationship,
        "occurrence_key": occurrence_key,
        "request_id": request_id,
        "message_id": message_id,
        "run_id": run_id,
        "journey_id": journey_id,
        "parent_resource_id": parent_resource_id,
        "title_snapshot": title,
        "summary_snapshot": _clip(summary, 240),
        "status_snapshot": status,
        "search_terms": terms,
        "search_prefixes": prefs,
    }

    try:
        result = await firestore.upsert_resource_and_link(resource_row, link_row)
    except Exception as exc:  # noqa: BLE001 — errors are data at this seam
        return {"status": "error", "error": True,
                "message": f"resource registration failed: {exc}"[:240]}

    if result.get("link_created"):
        # Advisory counters + audit are post-commit and best-effort.
        try:
            await firestore.bump_session_catalog_resources(
                session_id, resource_type)
        except Exception:  # noqa: BLE001
            pass
        try:
            await firestore.audit(
                "system:session_resources", "resource_link",
                f"resource_index/{resource_id}", "success",
                f"link={link_id} session={session_id} rel={relationship}")
        except Exception:  # noqa: BLE001
            pass

    return {"status": "success", "resource_id": resource_id,
            "link_id": link_id, "replayed": bool(result.get("replayed")),
            "tombstoned": bool(result.get("tombstoned"))}


async def update_resource_status(*, founder_id: str, resource_type: str,
                                 canonical_id: str,
                                 status: str = "", title: str = "",
                                 summary: str = "") -> dict[str, Any]:
    """Update a resource's CURRENT projection without a new occurrence — the
    matchmaker/state-transition path (docs/23 §6.1)."""
    from services import firestore

    spec = RESOURCE_REGISTRY.get(resource_type)
    if spec is None or not spec.enabled:
        return {"status": "error", "error": True,
                "message": f"unknown resource type: {resource_type!r}"}
    resource_id = resource_id_for(founder_id, resource_type,
                                  spec.collection, canonical_id)
    fields: dict[str, Any] = {}
    if status:
        fields["status"] = status
    if title:
        fields["title"] = _clip(title, MAX_TITLE)
    if summary:
        fields["summary"] = _clip(summary, MAX_SUMMARY)
    if title or summary:
        terms, prefs = search_fields(title, summary)
        fields["search_terms"] = terms
        fields["search_prefixes"] = prefs
    if not fields:
        return {"status": "success", "resource_id": resource_id,
                "updated": False}
    try:
        updated = await firestore.update_resource_projection(
            resource_id, fields)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": True,
                "message": f"projection update failed: {exc}"[:240]}
    return {"status": "success", "resource_id": resource_id,
            "updated": bool(updated)}


# ---------------------------------------------------------------------------
# Session catalog maintenance (docs/23 §6.3) — event-driven, no polling.
# Catalog failure is tolerated (repair marker), never a turn failure.
# ---------------------------------------------------------------------------

MAX_CATALOG_TITLE = 120
MAX_CATALOG_PREVIEW = 240


async def catalog_session_event(*, founder_id: str, session_id: str,
                                text: str = "", author: str = "user",
                                created: bool = False) -> dict[str, Any]:
    """Update the catalog for one conversation event (creation, accepted
    founder message, committed agent response, voice transcript persist)."""
    from services import firestore

    try:
        row = await firestore.get_session_catalog(session_id)
        fields: dict[str, Any] = {"founder_id": founder_id}
        display = _clip(text, MAX_CATALOG_PREVIEW)
        if row is None:
            fields["title"] = (_clip(text, MAX_CATALOG_TITLE)
                               if author == "user" else "")
            fields["preview"] = display
            fields["message_count"] = 0 if created and not text else 1
            terms, prefs = search_fields(text)
            fields["search_terms"] = terms
            fields["search_prefixes"] = prefs
        else:
            if text:
                fields["preview"] = display
                fields["message_count"] = int(row.get("message_count") or 0) + 1
                if not row.get("title") and author == "user":
                    fields["title"] = _clip(text, MAX_CATALOG_TITLE)
                # Rolling bounded search projection: merge new tokens.
                terms, _ = search_fields(text)
                merged: list[str] = list(row.get("search_terms") or [])
                for token in terms:
                    if token not in merged:
                        merged.append(token)
                merged = merged[-MAX_TERMS:]
                fields["search_terms"] = merged
                fields["search_prefixes"] = prefixes_for(merged)
        await firestore.upsert_session_catalog(session_id, fields)
        return {"status": "success"}
    except Exception as exc:  # noqa: BLE001 — catalog failure never fails a turn
        try:
            from services import firestore as _fs
            await _fs.audit("system:session_resources", "catalog_repair_needed",
                            f"session_catalog/{session_id}", "error",
                            str(exc)[:200])
        except Exception:  # noqa: BLE001
            pass
        return {"status": "error", "error": True,
                "message": f"catalog update failed: {exc}"[:240]}


# ---------------------------------------------------------------------------
# Search (docs/23 §7). Grouped by resource; keyset cursors bind to immutable
# scan order; ranking is within-page presentation only; the candidate cap is
# reported, never silently truncating.
# ---------------------------------------------------------------------------

MAX_LIMIT = 50
CANDIDATE_MULTIPLIER = 5
CANDIDATE_CAP = 250
MAX_OCCURRENCES = 25


def _rank(row: dict[str, Any], q_terms: list[str]) -> tuple:
    """Within-page ordering: exact title, title prefix, all-title-token,
    summary, then recency. Lower sorts first."""
    title = normalize_text(row.get("title") or "")
    title_tokens = set(tokenize(title))
    joined = " ".join(q_terms)
    if title == joined:
        tier = 0
    elif title.startswith(joined):
        tier = 1
    elif q_terms and all(t in title_tokens for t in q_terms):
        tier = 2
    elif any(t in normalize_text(row.get("summary") or "") for t in q_terms):
        tier = 3
    else:
        tier = 4
    recency = row.get("order_key") or ""
    return (tier, _invert(recency))


def _invert(value: str) -> str:
    """Sort a string descending inside an ascending tuple sort."""
    return "".join(chr(0x10FFFD - ord(ch)) if ord(ch) < 0x10FFFD else ch
                   for ch in value)


def _focus_for(resource_type: str, canonical_ref: dict[str, Any]
               ) -> dict[str, str]:
    spec = RESOURCE_REGISTRY.get(resource_type)
    return {"kind": spec.focus_kind if spec else "resource",
            "id": str((canonical_ref or {}).get("id") or "")}


async def search(*, founder_id: str, q: str = "",
                 types: list[str] | None = None,
                 session_id: str | None = None,
                 cursor: str | None = None,
                 limit: int = 30,
                 session_exists=None) -> dict[str, Any]:
    """Owner-scoped typed search over conversations and resources.

    Returns errors as data. Foreign or unknown sessions collapse to a generic
    not-found so search can never act as an existence oracle (docs/23 §9).
    """
    from services import firestore

    limit = max(1, min(int(limit or 30), MAX_LIMIT))
    requested = [t.strip() for t in (types or []) if t and t.strip()]
    for candidate in requested:
        if candidate not in SEARCHABLE_TYPES:
            return {"status": "error", "error": True,
                    "message": f"unknown result type: {candidate!r}"}
    if session_id:
        checker = session_exists or _session_exists
        if checker is None or not await checker(session_id):
            return {"status": "error", "error": True, "message": "not found"}

    normalized = normalize_query(q)
    q_terms = query_terms(normalized)
    if normalized and len("".join(q_terms)) < 2:
        return {"status": "error", "error": True,
                "message": "query needs at least two useful characters"}

    binding = filter_binding(normalized, requested, session_id)
    frontiers: dict[str, list[str] | None] = {}
    if cursor:
        decoded = decode_cursor(cursor, binding)
        if decoded["error"]:
            return {"status": "error", "error": True,
                    "message": decoded["message"]}
        frontiers = decoded["frontiers"]

    want_sessions = (not requested or "session" in requested) and not session_id
    resource_types = [t for t in requested if t != "session"]

    if not normalized:
        return await _recent(founder_id, limit, want_sessions, session_id,
                             resource_types)

    probe = choose_probe_prefix(q_terms)
    if not probe:
        return {"status": "success", "query": normalized, "results": [],
                "truncated": False, "next_cursor": None}

    # Scan budget per stream. We deliberately scan a bounded window and select
    # the page IN SCAN ORDER: selecting by rank would make the frontier
    # unresumable, because a rank-ordered page does not correspond to a
    # contiguous keyset range (docs/23 §7.2 steps 3-8).
    scan = min(max(limit * CANDIDATE_MULTIPLIER, limit + 1), CANDIDATE_CAP)
    truncated = False
    candidates: list[dict[str, Any]] = []
    next_frontiers: dict[str, list[str] | None] = dict(frontiers)

    def _carry(stream: str) -> None:
        next_frontiers.setdefault(stream, frontiers.get(stream))

    # --- conversations -----------------------------------------------------
    if want_sessions:
        rows = await firestore.search_session_catalog(
            founder_id, probe, limit=scan,
            start_after=_as_pair(frontiers.get("sessions")))
        truncated = truncated or len(rows) >= scan
        _carry("sessions")
        for row in rows:
            if not terms_match(row.get("search_terms") or [],
                               row.get("search_prefixes") or [], q_terms):
                continue
            candidates.append({
                "stream": "sessions", "kind": "session", "row": row,
                "key": (row.get("created_at", ""), row.get("session_id", "")),
            })

    # --- resources --------------------------------------------------------
    # Pagination is driven by the RESOURCE stream, which has exactly one row
    # per logical resource. Occurrence rows must not drive it: a resource
    # whose occurrences straddle a page boundary would then be emitted on two
    # pages. Occurrences are attached afterwards by bounded per-resource
    # lookup, which is also what makes occurrence_count correct rather than
    # "however many happened to fall inside the scan window".
    if session_id:
        # Session-scoped mode: the unit IS the occurrence, and one session
        # cannot produce cross-page duplicates of the same resource.
        for resource_type in (resource_types or [None]):
            stream = f"links:{resource_type or 'all'}"
            rows = await firestore.search_session_links(
                founder_id, probe, resource_type=resource_type,
                session_id=session_id, limit=scan,
                start_after=_as_pair(frontiers.get(stream)))
            truncated = truncated or len(rows) >= scan
            _carry(stream)
            for row in rows:
                if not terms_match(row.get("search_terms") or [],
                                   row.get("search_prefixes") or [], q_terms):
                    continue
                candidates.append({
                    "stream": stream, "kind": "link", "row": row,
                    "key": (row.get("occurred_at", ""), row.get("link_id", "")),
                })
    else:
        for resource_type in (resource_types or [None]):
            stream = f"resources:{resource_type or 'all'}"
            spec = RESOURCE_REGISTRY.get(resource_type or "")
            visibility = (spec.default_visibility if spec
                          else Visibility.PRIMARY)
            rows = await firestore.search_resources(
                founder_id, probe, visibility=visibility,
                resource_type=resource_type, limit=scan,
                start_after=_as_pair(frontiers.get(stream)))
            truncated = truncated or len(rows) >= scan
            _carry(stream)
            for row in rows:
                if not terms_match(row.get("search_terms") or [],
                                   row.get("search_prefixes") or [], q_terms):
                    continue
                candidates.append({
                    "stream": stream, "kind": "resource", "row": row,
                    "key": (row.get("created_at", ""),
                            row.get("resource_id", "")),
                })
    # Merge streams by their immutable keyset, newest first, stable ties.
    candidates.sort(key=lambda c: (c["key"], c["stream"]), reverse=True)

    # Select in scan order until the page is full of DISTINCT results; the
    # frontier then advances only over rows we actually consumed, so nothing
    # between pages can be skipped.
    grouped: dict[str, dict[str, Any]] = {}
    session_rows: dict[str, dict[str, Any]] = {}
    consumed: list[dict[str, Any]] = []

    def _distinct() -> int:
        return len(grouped) + len(session_rows)

    for candidate in candidates:
        row = candidate["row"]
        if candidate["kind"] == "session":
            key, container = row.get("session_id", ""), session_rows
        else:
            key, container = row.get("resource_id", ""), grouped
        if key not in container and _distinct() >= limit:
            break
        # A resource whose occurrences straddle the frontier must not be
        # emitted on two pages. Occurrences are consumed contiguously in scan
        # order, so stopping here leaves the rest for the next page, and the
        # dedupe set below keeps the resource off this one.
        consumed.append(candidate)
        if candidate["kind"] == "session":
            session_rows.setdefault(key, row)
        elif candidate["kind"] == "link":
            _accumulate(grouped, row)
        else:
            grouped.setdefault(key, {"resource": row, "occurrences": []})

    for candidate in consumed:
        next_frontiers[candidate["stream"]] = list(candidate["key"])
    has_more = truncated or len(consumed) < len(candidates)

    results: list[dict[str, Any]] = [
        _session_result(row) for row in session_rows.values()]
    for resource_id, bundle in grouped.items():
        row = bundle["resource"]
        if row is not None and not bundle["occurrences"]:
            # Resource-stream hit: attach its true occurrences (bounded).
            bundle["occurrences"] = await firestore.list_resource_links(
                founder_id, resource_id, limit=MAX_OCCURRENCES)
        if row is None:
            row = await firestore.get_resource(resource_id)
            if row is None:
                # Dangling link: hide from search, emit a repair signal.
                try:
                    await firestore.audit(
                        "system:session_resources", "dangling_link",
                        f"resource_index/{resource_id}", "error", "")
                except Exception:  # noqa: BLE001
                    pass
                continue
        results.append(_resource_result(row, bundle["occurrences"]))

    # Ranking is presentation order for the rows the scan already selected.
    results.sort(key=lambda r: _rank(r, q_terms))
    return {
        "status": "success", "query": normalized,
        "results": [_public(r) for r in results],
        "truncated": truncated,
        "next_cursor": (encode_cursor(binding, next_frontiers)
                        if has_more else None),
    }


def _as_pair(value) -> tuple[str, str] | None:
    if isinstance(value, list) and len(value) == 2:
        return (value[0], value[1])
    return None


def _accumulate(grouped: dict[str, dict[str, Any]], link: dict[str, Any]) -> None:
    bundle = grouped.setdefault(link["resource_id"],
                                {"resource": None, "occurrences": []})
    bundle["occurrences"].append(link)


def _session_result(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_id": row.get("session_id", ""),
        "result_type": "session",
        "resource_id": None,
        "canonical_ref": None,
        "title": row.get("title") or "New conversation",
        "subtitle": row.get("preview") or "",
        "status": row.get("status", "active"),
        "occurrence_count": int(row.get("resource_count") or 0),
        "latest_occurrence": None,
        "session_id": row.get("session_id", ""),
        "occurred_at": row.get("updated_at", ""),
        "order_key": row.get("created_at", ""),
        "focus": {"kind": "session", "id": row.get("session_id", "")},
        "summary": row.get("preview") or "",
    }


def _resource_result(row: dict[str, Any],
                     occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    occurrences = sorted(occurrences,
                         key=lambda o: (o.get("occurred_at", ""),
                                        o.get("link_id", "")), reverse=True)
    latest = occurrences[0] if occurrences else None
    return {
        "result_id": row.get("resource_id", ""),
        "result_type": row.get("resource_type", ""),
        "resource_id": row.get("resource_id", ""),
        "canonical_ref": row.get("canonical_ref") or {},
        "title": row.get("title", ""),
        "subtitle": row.get("summary", ""),
        "summary": row.get("summary", ""),
        "status": row.get("status", ""),
        "occurrence_count": len(occurrences),
        "latest_occurrence": ({
            "link_id": latest.get("link_id", ""),
            "relationship": latest.get("relationship", ""),
            "session_id": latest.get("session_id", ""),
            "occurred_at": latest.get("occurred_at", ""),
            "title_snapshot": latest.get("title_snapshot", ""),
        } if latest else None),
        "session_id": latest.get("session_id") if latest else None,
        "occurred_at": (latest.get("occurred_at") if latest
                        else row.get("updated_at", "")),
        "order_key": row.get("created_at", ""),
        "focus": _focus_for(row.get("resource_type", ""),
                            row.get("canonical_ref") or {}),
    }


_PUBLIC_FIELDS = ("result_id", "result_type", "resource_id", "canonical_ref",
                  "title", "subtitle", "status", "occurrence_count",
                  "latest_occurrence", "session_id", "occurred_at", "focus")


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """Drop internal ranking/scan fields from the API response."""
    return {k: row[k] for k in _PUBLIC_FIELDS if k in row}


async def _recent(founder_id: str, limit: int, want_sessions: bool,
                  session_id: str | None,
                  resource_types: list[str]) -> dict[str, Any]:
    """Blank state: recent conversations and primary resources, single page."""
    from services import firestore

    results: list[dict[str, Any]] = []
    if session_id:
        links = await firestore.list_session_links(
            founder_id, session_id, limit=limit)
        grouped: dict[str, dict[str, Any]] = {}
        for link in links:
            _accumulate(grouped, link)
        for resource_id, bundle in grouped.items():
            row = await firestore.get_resource(resource_id)
            if row:
                results.append(_resource_result(row, bundle["occurrences"]))
    else:
        if want_sessions:
            for row in await firestore.list_recent_session_catalog(
                    founder_id, limit=limit):
                results.append(_session_result(row))
        for row in await firestore.list_recent_resources(
                founder_id, limit=limit):
            if resource_types and row.get("resource_type") not in resource_types:
                continue
            results.append(_resource_result(row, []))
    results.sort(key=lambda r: _invert(r.get("occurred_at") or ""))
    return {"status": "success", "query": "",
            "results": [_public(r) for r in results[:limit]],
            "truncated": False, "next_cursor": None}


async def session_resources_for(founder_id: str, session_id: str, *,
                                limit: int = 100) -> dict[str, Any]:
    """One conversation's resource occurrences, newest first (docs/23 WI-6)."""
    from services import firestore

    links = await firestore.list_session_links(founder_id, session_id,
                                               limit=limit)
    out: list[dict[str, Any]] = []
    for link in links:
        row = await firestore.get_resource(link["resource_id"])
        if row is None:
            continue
        result = _resource_result(row, [link])
        out.append(_public(result))
    return {"status": "success", "session_id": session_id, "resources": out}
