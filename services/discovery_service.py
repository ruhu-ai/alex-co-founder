"""Discovery service (docs/08) — fetch, search, extract, deadline sentinel.

Model-backed steps (search grounding, record extraction) are injectable so
tests and offline dev run without credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin

import httpx

from services import firestore, pipeline_service, storage

SearchFn = Callable[[str], list[dict[str, str]]]
ExtractFn = Callable[[str, dict], list[dict[str, Any]]]
PdfExtractFn = Callable[[bytes, dict], list[dict[str, Any]]]
_search_fn: SearchFn | None = None
_extract_fn: ExtractFn | None = None
_pdf_extract_fn: PdfExtractFn | None = None
MAX_HTML_FETCH_BYTES = 2_000_000
# Gemini document understanding accepts PDFs up to 50 MB. Keep the transport
# bound aligned with that real provider boundary instead of the former 10 MB
# application-only restriction.
MAX_PDF_FETCH_BYTES = 50_000_000


def set_search_fn(fn: SearchFn) -> None:
    global _search_fn
    _search_fn = fn


def set_extract_fn(fn: ExtractFn) -> None:
    global _extract_fn
    _extract_fn = fn


def set_pdf_extract_fn(fn: PdfExtractFn) -> None:
    global _pdf_extract_fn
    _pdf_extract_fn = fn


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


async def _safe_fetch(
    url: str, max_bytes: int, *, truncate: bool = False,
) -> tuple[httpx.Response, str, bool]:
    """Fetch through the DNS-pinning proxy with a bounded streaming read.

    HTML callers may request a bounded prefix rather than failing an otherwise
    useful research task merely because a page ships a large application
    bundle. Binary callers still fail closed because truncating a PDF would
    create a corrupt artifact and misleading extraction result.
    """
    from services import browser_service

    current = url
    proxy = await browser_service.public_proxy_url()
    async with httpx.AsyncClient(
            follow_redirects=False, timeout=30, proxy=proxy, trust_env=False) as client:
        for _ in range(6):
            refusal = await browser_service.validate_public_url(current)
            if refusal:
                raise ValueError(f"unsafe source URL: {refusal}")
            async with client.stream("GET", current) as streamed:
                if streamed.status_code in (301, 302, 303, 307, 308):
                    location = streamed.headers.get("location")
                    if not location:
                        raise ValueError("redirect response had no Location header")
                    current = urljoin(current, location)
                    continue
                streamed.raise_for_status()
                declared = int(streamed.headers.get("content-length", "0") or 0)
                if declared > max_bytes and not truncate:
                    raise ValueError(f"source exceeds {max_bytes} byte limit")
                chunks: list[bytes] = []
                size = 0
                truncated = declared > max_bytes
                async for chunk in streamed.aiter_bytes():
                    remaining = max_bytes - size
                    if len(chunk) > remaining:
                        if truncate and remaining > 0:
                            chunks.append(chunk[:remaining])
                            size += remaining
                            truncated = True
                            break
                        raise ValueError(f"source exceeds {max_bytes} byte limit")
                    chunks.append(chunk)
                    size += len(chunk)
                    if size == max_bytes and truncate:
                        truncated = True
                        break
                response = httpx.Response(
                    streamed.status_code, headers=streamed.headers,
                    content=b"".join(chunks), request=streamed.request)
                return response, current, truncated
    raise ValueError("too many redirects")


async def fetch_source(source_url: str, source_type: str, artifact_name: str) -> dict:
    """Fetch → text (+ links). JS-shell fallback: <500 chars → Playwright render."""
    try:
        is_pdf = source_type == "pdf"
        resp, final_url, source_truncated = await _safe_fetch(
            source_url,
            MAX_PDF_FETCH_BYTES if is_pdf else MAX_HTML_FETCH_BYTES,
            truncate=not is_pdf,
        )
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"fetch failed: {exc}"}

    rendered = False
    if is_pdf:
        # The bytes are the source of truth: extract_records() detects the
        # .pdf sibling and feeds document understanding the real document.
        pdf_artifact = artifact_name.replace(".txt", ".pdf")
        storage.save_bytes(pdf_artifact, resp.content)
        text, links = (f"[PDF source: {len(resp.content)} bytes stored as "
                       f"{pdf_artifact}; extract_records reads the PDF itself]"), []
    else:
        text, links = _parse_html(resp.text, final_url)
        if source_truncated or len(text) < 500:
            # Large HTML and JS shells are rendered in the already isolated,
            # SSRF-guarded browser worker. The browser loads the page normally
            # while returning at most MAX_PAGE_TEXT visible characters, so a
            # multi-megabyte bundle no longer blocks useful research.
            from services import browser_gateway as browser_service

            rendered_text = await browser_service.render_text(final_url)
            if rendered_text:
                text, rendered = rendered_text, True

    storage.save_text(artifact_name, text)
    return {
        "status": "success",
        "artifact": artifact_name,
        "summary": text[:300],
        "chars": len(text),
        "rendered": rendered,
        "source_truncated": source_truncated,
        "source_bytes_read": len(resp.content),
        "research_note": (
            "This large source was converted to bounded visible text. Use its "
            "links or another official source if a requested detail is absent."
            if source_truncated else None
        ),
        "links": links,
    }


async def search_programs(query: str, max_results: int = 10) -> dict:
    """Search lane (docs/08). Backend verified by the Day-1 spike; the fallback
    is a plain search HTTP call behind the same contract."""
    if _search_fn is None:
        return {"status": "error", "error": True,
                "message": "search backend not configured (needs ADC; see docs/verification-notes.md)"}
    try:
        results = (await asyncio.to_thread(_search_fn, query))[:max_results]
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"search failed: {exc}"}
    return {"status": "success", "results": results}


async def extract_records(text_artifact: str, entity_schema: dict) -> dict:
    """Structured extraction from a source artifact into entity_schema records.

    PDF sources: fetch_source() stores the raw document as a .pdf sibling of
    the text artifact — when it exists, extraction runs document understanding
    over the actual PDF bytes, never over the placeholder text."""
    pdf_artifact = text_artifact.replace(".txt", ".pdf")
    if text_artifact != pdf_artifact and storage.exists(pdf_artifact):
        if _pdf_extract_fn is None:
            return {"status": "error", "error": True,
                    "message": "PDF extraction backend not configured (needs ADC)"}
        try:
            records = await asyncio.to_thread(
                _pdf_extract_fn, storage.read_bytes(pdf_artifact), entity_schema)
        except Exception as exc:
            return {"status": "error", "error": True,
                    "message": f"pdf extraction failed: {exc}"}
        for record in records:
            record.setdefault("source_type", "pdf")
        return {"status": "success", "records": records, "source": "pdf"}
    if _extract_fn is None:
        return {"status": "error", "error": True,
                "message": "extraction backend not configured (needs ADC)"}
    try:
        records = await asyncio.to_thread(
            _extract_fn, storage.read_text(text_artifact), entity_schema)
    except Exception as exc:
        return {"status": "error", "error": True, "message": f"extraction failed: {exc}"}
    return {"status": "success", "records": records}


async def save_opportunity_record(record: dict, entity_schema: dict,
                                  founder_id: str = "") -> dict:
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
    # The extraction contract uses null for unknown fields. Normalize declared
    # list fields before persistence so every downstream consumer sees one
    # stable shape; legacy null records are also defended in pipeline_service.
    for key, declared_type in entity_schema.items():
        if str(declared_type).startswith("list["):
            full[key] = pipeline_service.normalize_string_list(full.get(key))
    full["raw_excerpt"] = record.get("raw_excerpt", "")[:2000]
    full["source_type"] = record.get("source_type", "web_page")
    full["workspace_id"] = founder_id or None
    full["founder_id"] = founder_id or None
    full["dedup_hash"] = pipeline_service.dedup_hash(full.get("name") or "", full.get("application_url") or "")
    opportunity_id = await firestore.create_opportunity(full)
    return {"status": "success", "opportunity_id": opportunity_id, "dedup_hash": full["dedup_hash"]}


_RELEVANCE_WORDS = ("grant", "fund", "program", "programme", "accelerator",
                    "incubator", "fellowship", "award", "prize", "capital")


_DISCOVERY_CONTEXT_MAX = 500


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS_RE = re.compile(r"\d{6,}")


def scrub_query_text(text: str) -> str:
    """Sensitivity filter for persisted query text (docs/23 §5.5).

    Executed queries are generated from Founder Profile facts, so anything
    identifying beyond sector/stage/geography prose is scrubbed before the
    text becomes durable/searchable: email addresses and long digit runs
    (phone numbers, registration ids). Bounded at 300 chars.
    """
    text = _EMAIL_RE.sub("[email]", text or "")
    text = _LONG_DIGITS_RE.sub("[number]", text)
    return text[:300]


def normalize_discovery_context(context: str | None) -> str:
    """Normalize untrusted prose constraints without treating them as policy."""
    if not context:
        return ""
    printable = "".join(
        " " if ch.isspace() else ch for ch in str(context) if ch.isprintable() or ch.isspace())
    return re.sub(r"\s+", " ", printable).strip()[:_DISCOVERY_CONTEXT_MAX]


def _queries_from_profile(profile: dict, context: str | None = None,
                          limit: int = 3) -> list[str]:
    """Lane 1 queries from profile facts plus optional task-scoped prose.

    The prose is data, not an instruction channel. The model-backed search
    adapter applies the corresponding trust-boundary prompt before searching.
    """
    facts = profile.get("facts", {})
    sector = facts.get("sector", "technology")
    geo = facts.get("geography", "")
    stage = facts.get("stage", "early-stage")
    who = f"{stage} {sector} startups {geo}".replace("  ", " ").strip()
    queries = [
        f"grant programs for {who} applications open",
        f"accelerators incubators funding {who} apply",
        f"non-dilutive funding awards {sector} founders {geo}".strip(),
    ]
    scoped = normalize_discovery_context(context)
    if scoped:
        queries = [f"{query}; founder-supplied constraints: {scoped}"
                   for query in queries]
    return queries[:limit]


def _relevant(item: dict) -> bool:
    """Cheap relevance pass (docs/08): keyword filter before spending a fetch."""
    text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
    return any(w in text for w in _RELEVANCE_WORDS)


async def _ingest_url(url: str, source_type: str, workflow, summary: dict,
                      founder_id: str = "") -> None:
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
    if (_pdf_extract_fn if source_type == "pdf" else _extract_fn) is None:
        return
    # unchanged-skip hashes what extraction actually consumes: the PDF bytes
    # for pdf sources (the .txt is just a placeholder), the text otherwise
    content = (storage.read_bytes(artifact.replace(".txt", ".pdf"))
               if source_type == "pdf" else storage.read_text(artifact).encode())
    content_hash = hashlib.sha256(content).hexdigest()[:16]
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
                pipeline_service.dedup_hash(
                    record.get("name"), record.get("application_url")),
                founder_id)
            saved = await save_opportunity_record(
                record, workflow.entity_schema, founder_id)
            if saved["status"] == "success":
                summary["saved"] += 1
                if existing is None:
                    summary["new"] += 1
                # Request → opportunity membership for the receipt and the
                # session-link registration (docs/23 §5.5): bounded first 100,
                # deduplicated finds included — a re-found opportunity is still
                # an occurrence of THIS request.
                ids = summary.setdefault("opportunity_ids", [])
                oid = saved.get("opportunity_id", "")
                if oid and oid not in ids and len(ids) < 100:
                    ids.append(oid)
            else:
                summary["errors"].append({"url": url, "error": saved.get("message", "")[:200]})
        except Exception as exc:  # errors as data: skip the record, not the sweep
            summary["errors"].append({"url": url, "error": f"record skipped: {exc}"[:200]})


async def run_sweep(workflow, founder_id: str | None = None,
                    context: str | None = None) -> dict:
    """Full discovery sweep (docs/08): configured lanes plus the profile-driven
    search lane (so scheduled sweeps are autonomous — the scout agent uses the
    same search_programs contract in chat). Fetches snapshot to artifacts even
    when extraction is offline — the sweep never fails because one lane is
    down."""
    summary = {"fetched": 0, "extracted": 0, "saved": 0, "new": 0, "unchanged": 0,
               "errors": [], "opportunity_ids": [], "executed_queries": []}
    for source in workflow.sources:
        if source["type"] not in ("web_page", "pdf"):
            continue
        url = source.get("url", "")
        if not url or url == "TBD-Day-1":
            continue
        await _ingest_url(
            url, source["type"], workflow, summary, founder_id or "")

    search_sources = [s for s in workflow.sources if s["type"] == "search"]
    if search_sources and founder_id and _search_fn is not None:
        max_results = search_sources[0].get("max_results_per_query", 10)
        profile = await firestore.get_profile(founder_id)
        seen_urls: set[str] = set()
        for index, query in enumerate(
                _queries_from_profile(profile, context=context)):
            results = await search_programs(query, max_results=max_results)
            # Durable, bounded, provider-neutral query record (docs/23 §5.5).
            # Text passes the sensitivity scrub BEFORE persistence — profile-
            # derived queries can embed founder facts.
            if len(summary["executed_queries"]) < 12:
                summary["executed_queries"].append({
                    "query_id": f"q{index + 1}",
                    "text": scrub_query_text(query),
                    "provider": "web_search",
                    "status": ("ok" if results["status"] == "success"
                               else "error"),
                    "result_count": len(results.get("results", []) or []),
                })
            if results["status"] != "success":
                summary["errors"].append(
                    {"lane": "search", "query": query, "error": results.get("message", "")[:200]})
                continue
            for item in results["results"]:
                url = item.get("url", "")
                if not url or url in seen_urls or not _relevant(item):
                    continue
                seen_urls.add(url)
                await _ingest_url(
                    url, "web_page", workflow, summary, founder_id or "")

    # Audit the real outcome, not an unconditional "success": every lane can
    # error while the sweep still returns. success = clean; partial = some
    # progress with errors; error = errors and nothing achieved.
    if not summary["errors"]:
        outcome = "success"
    elif summary["fetched"] or summary["extracted"] or summary["saved"]:
        outcome = "partial"
    else:
        outcome = "error"
    await firestore.audit("system:discovery", "sweep", "opportunities", outcome,
                          f"fetched={summary['fetched']} new={summary['new']} "
                          f"errors={len(summary['errors'])}")
    return {"status": "success", **summary}



async def deadline_scan(founder_id: str = "") -> dict:
    """Deadline sentinel (docs/08): recompute urgency on ALL open items.

    Pages through the whole opportunities collection with a created_at cursor —
    a fixed limit silently skipped every opportunity past the first page, so a
    grant closing tomorrow could go un-nudged simply because 200 newer records
    existed. (created_at ties can nudge the page boundary; acceptable at
    demo-scale and still far better than a hard cap.)
    """
    _PAGE = 200
    scanned = 0
    critical = []
    cursor: str | None = None
    while True:
        batch = await firestore.list_opportunities(
            limit=_PAGE, start_after=cursor, founder_id=founder_id)
        if not batch:
            break
        for opp in batch:
            if opp.get("state") != "SHORTLISTED":
                continue
            urgency = pipeline_service.compute_urgency(
                opp.get("deadline"), opp.get("required_materials", []))
            previous = (opp.get("urgency") or {}).get("tier")
            if urgency != opp.get("urgency"):
                await firestore.set_opportunity_state(
                    opp["id"], opp["state"], founder_id=founder_id,
                    urgency=urgency)
            if urgency["tier"] == "CRITICAL" and previous != "CRITICAL":
                critical.append(opp["id"])
            scanned += 1
        if len(batch) < _PAGE:
            break
        cursor = batch[-1].get("created_at")
    return {"status": "success", "scanned": scanned, "newly_critical": critical}
