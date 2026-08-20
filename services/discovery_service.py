"""Discovery service (docs/08) — fetch, search, extract, deadline sentinel.

Model-backed steps (search grounding, record extraction) are injectable so
tests and offline dev run without credentials.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin

import httpx

from services import firestore, pipeline_service, storage

SearchFn = Callable[[str], list[dict[str, str]]]
ExtractFn = Callable[[str, dict], list[dict[str, Any]]]
_search_fn: SearchFn | None = None
_extract_fn: ExtractFn | None = None


def set_search_fn(fn: SearchFn) -> None:
    global _search_fn
    _search_fn = fn


def set_extract_fn(fn: ExtractFn) -> None:
    global _extract_fn
    _extract_fn = fn


class _TextAndLinks(HTMLParser):
    """Minimal HTML→text + anchor extraction (no heavyweight deps)."""

    def __init__(self, base_url: str):
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._base = base_url
        self._href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "a":
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        self.parts.append(text)
        if self._href:
            self.links.append({"url": urljoin(self._base, self._href),
                               "anchor_text": text[:120]})


def _parse_html(html: str, base_url: str) -> tuple[str, list[dict[str, str]]]:
    parser = _TextAndLinks(base_url)
    parser.feed(html)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(parser.parts))
    return text, parser.links[:15]


async def fetch_source(source_url: str, source_type: str, artifact_name: str) -> dict:
    """Fetch → text (+ links). JS-shell fallback: <500 chars → Playwright render."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(source_url)
            resp.raise_for_status()
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"fetch failed: {exc}"}

    rendered = False
    if source_type == "pdf":
        text, links = f"[PDF fetched: {len(resp.content)} bytes — parsed by Gemini document understanding at extraction]", []
        storage.save_bytes(artifact_name.replace(".txt", ".pdf"), resp.content)
    else:
        text, links = _parse_html(resp.text, source_url)
        if len(text) < 500:  # JS shell — render through Playwright (docs/08)
            from services import browser_service

            rendered_text = await browser_service.render_text(source_url)
            if rendered_text:
                text, links, rendered = rendered_text, _parse_html(rendered_text, source_url)[1], True

    storage.save_text(artifact_name, text)
    return {
        "status": "success",
        "artifact": artifact_name,
        "summary": text[:300],
        "chars": len(text),
        "rendered": rendered,
        "links": links,
    }


async def search_programs(query: str, max_results: int = 10) -> dict:
    """Search lane (docs/08). Backend verified by the Day-1 spike; the fallback
    is a plain search HTTP call behind the same contract."""
    if _search_fn is None:
        return {"status": "error", "error": True,
                "message": "search backend not configured (needs ADC; see docs/verification-notes.md)"}
    try:
        results = _search_fn(query)[:max_results]
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"search failed: {exc}"}
    return {"status": "success", "results": results}


async def extract_records(text_artifact: str, entity_schema: dict) -> dict:
    """Structured extraction from a source artifact into entity_schema records."""
    if _extract_fn is None:
        return {"status": "error", "error": True,
                "message": "extraction backend not configured (needs ADC)"}
    try:
        records = _extract_fn(storage.read_text(text_artifact), entity_schema)
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"extraction failed: {exc}"}
    return {"status": "success", "records": records}


async def save_opportunity_record(record: dict, entity_schema: dict) -> dict:
    """Validate against the workflow entity_schema: missing → None, extra → rejected.
    A record with no name is junk extraction — rejected, never saved."""
    if not record.get("name"):
        return {"status": "error", "error": True,
                "message": "record rejected: no name (incomplete extraction)"}
    extra = set(record) - set(entity_schema) - {"raw_excerpt", "source_type"}
    if extra:
        return {"status": "error", "error": True,
                "message": f"record has fields outside entity_schema: {sorted(extra)}"}
    full = {key: record.get(key) for key in entity_schema}
    full["raw_excerpt"] = record.get("raw_excerpt", "")[:2000]
    full["source_type"] = record.get("source_type", "web_page")
    full["dedup_hash"] = pipeline_service.dedup_hash(full.get("name") or "", full.get("application_url") or "")
    opportunity_id = await firestore.create_opportunity(full)
    return {"status": "success", "opportunity_id": opportunity_id, "dedup_hash": full["dedup_hash"]}


_RELEVANCE_WORDS = ("grant", "fund", "program", "programme", "accelerator",
                    "incubator", "fellowship", "award", "prize", "capital")


def _queries_from_profile(profile: dict, limit: int = 3) -> list[str]:
    """Lane 1 query generation (docs/08): deterministic, from profile facts."""
    facts = profile.get("facts", {})
    sector = facts.get("sector", "technology")
    geo = facts.get("geography", "")
    stage = facts.get("stage", "early-stage")
    who = f"{stage} {sector} startups {geo}".replace("  ", " ").strip()
    return [
        f"grant programs for {who} applications open",
        f"accelerators incubators funding {who} apply",
        f"non-dilutive funding awards {sector} founders {geo}".strip(),
    ][:limit]


def _relevant(item: dict) -> bool:
    """Cheap relevance pass (docs/08): keyword filter before spending a fetch."""
    text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
    return any(w in text for w in _RELEVANCE_WORDS)


async def _ingest_url(url: str, source_type: str, workflow, summary: dict) -> None:
    """Fetch one source → extract records → save each (deduped). One bad record
    or fetch never fails the sweep — errors land in summary['errors'].
    Extraction is skipped when the source content is unchanged since the last
    sweep — the model is never paid twice for identical bytes."""
    artifact = f"source_{pipeline_service.dedup_hash(url, source_type)[:12]}.txt"
    fetched = await fetch_source(url, source_type, artifact)
    if fetched["status"] != "success":
        summary["errors"].append({"url": url, "error": fetched.get("message", "")[:200]})
        return
    summary["fetched"] += 1
    if _extract_fn is None:
        return
    content_hash = hashlib.sha256(storage.read_text(artifact).encode()).hexdigest()[:16]
    if await firestore.get_source_hash(url) == content_hash:
        summary["unchanged"] += 1
        return
    extracted = await extract_records(artifact, workflow.entity_schema)
    if extracted["status"] != "success":
        summary["errors"].append({"url": url, "error": extracted.get("message", "")[:200]})
        return
    await firestore.set_source_hash(url, content_hash)
    summary["extracted"] += 1
    for record in extracted["records"]:
        try:
            existing = await firestore.find_opportunity_by_hash(
                pipeline_service.dedup_hash(record.get("name"), record.get("application_url")))
            saved = await save_opportunity_record(record, workflow.entity_schema)
            if saved["status"] == "success":
                summary["saved"] += 1
                if existing is None:
                    summary["new"] += 1
            else:
                summary["errors"].append({"url": url, "error": saved.get("message", "")[:200]})
        except Exception as exc:  # errors as data: skip the record, not the sweep
            summary["errors"].append({"url": url, "error": f"record skipped: {exc}"[:200]})


async def run_sweep(workflow, founder_id: str | None = None) -> dict:
    """Full discovery sweep (docs/08): configured lanes plus the profile-driven
    search lane (so scheduled sweeps are autonomous — the scout agent uses the
    same search_programs contract in chat). Fetches snapshot to artifacts even
    when extraction is offline — the sweep never fails because one lane is
    down."""
    summary = {"fetched": 0, "extracted": 0, "saved": 0, "new": 0, "unchanged": 0,
               "errors": []}
    for source in workflow.sources:
        if source["type"] not in ("web_page", "pdf"):
            continue
        url = source.get("url", "")
        if not url or url == "TBD-Day-1":
            continue
        await _ingest_url(url, source["type"], workflow, summary)

    search_sources = [s for s in workflow.sources if s["type"] == "search"]
    if search_sources and founder_id and _search_fn is not None:
        max_results = search_sources[0].get("max_results_per_query", 10)
        profile = await firestore.get_profile(founder_id)
        seen_urls: set[str] = set()
        for query in _queries_from_profile(profile):
            results = await search_programs(query, max_results=max_results)
            if results["status"] != "success":
                summary["errors"].append(
                    {"lane": "search", "query": query, "error": results.get("message", "")[:200]})
                continue
            for item in results["results"]:
                url = item.get("url", "")
                if not url or url in seen_urls or not _relevant(item):
                    continue
                seen_urls.add(url)
                await _ingest_url(url, "web_page", workflow, summary)

    await firestore.audit("system:discovery", "sweep", "opportunities", "success",
                          f"fetched={summary['fetched']} new={summary['new']}")
    return {"status": "success", **summary}



async def deadline_scan() -> dict:
    """Deadline sentinel (docs/08): recompute urgency on all open items."""
    scanned = 0
    critical = []
    for opp in await firestore.list_opportunities(limit=200):
        if opp.get("state") != "SHORTLISTED":
            continue
        urgency = pipeline_service.compute_urgency(opp.get("deadline"), opp.get("required_materials", []))
        previous = (opp.get("urgency") or {}).get("tier")
        if urgency != opp.get("urgency"):
            await firestore.set_opportunity_state(opp["id"], opp["state"], urgency=urgency)
        if urgency["tier"] == "CRITICAL" and previous != "CRITICAL":
            critical.append(opp["id"])
        scanned += 1
    return {"status": "success", "scanned": scanned, "newly_critical": critical}
