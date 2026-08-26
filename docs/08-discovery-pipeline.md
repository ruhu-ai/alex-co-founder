# 08 — Discovery Pipeline & Deadline Sentinel

The asynchronous discovery and deadline subsystem: founders invoke discovery
when they need it, while the deadline sentinel alone runs on a schedule. There
is no daily discovery job or polling loop.

## Discovery sweep flow

```
Founder UI or `/discover` command ──► durable Cloud Task
   └─► POST /tasks/discover (OIDC in Cloud Run; inline in local development)
        └─ system session (user_id="system", fresh session_id)
             ├─ LANE 1 (search): generate queries from Founder Profile
             │     → search_programs → result URLs → relevance filter
             │     → fetch_source each → extract → dedupe_check → save
             ├─ LANE 2 (configured): for each YAML source:
             │     fetch_source → artifact + summary → extract → dedupe → save
             ├─ LANE 3 (crawl): listing pages → link list → scout picks
             │     promising detail links (depth ≤ 2) → fetch each → extract
             └─ matchmaker: get_unscored_opportunities
                  → shortlist (≥70) or archive_with_reason (<70)
             └─ return sweep summary {found, new, shortlisted, archived}
```

- The discovery loop is **profile-driven**: queries are generated from the
  Founder Profile (sector, stage, geography, `decision_patterns` like
  "non-dilutive funding") plus freshness terms ("2026", "deadline"). The profile
  shapes what gets searched; what gets found then gets scored against the same
  profile. This closed loop is the answer to "how does it find programs that
  match *us*?"
- When the feature-flagged `/discover <context>` adapter supplies task-scoped
  prose, the same deterministic profile queries include that normalized,
  500-character-bounded context. The Gemini search prompt encloses the query in
  an explicit untrusted-data boundary. Context never mutates the Founder
  Profile, enters a system-session instruction, selects an opportunity, or
  broadens tool/attachment authority.
- **Fixture strategy (binding):** every configured source is snapshotted into
  `tests/fixtures/` on Day 1; CI and evals replay fixtures (deterministic),
  only the live demo fetches real URLs. The demo board is **pre-seeded** so the
  video never depends on live search — one live search call is shown on camera
  as the "it really roams the web" beat, but a failure there changes nothing.
- A sweep is idempotent: re-running on unchanged sources yields `{new: 0}`
  because `dedupe_check` catches everything.
- Sweep summary is written to `audit` with `actor="system:discovery"`.

## Discovery lanes (v1 scope)

| Lane | Input | Method |
|---|---|---|
| **1. Search** | profile-generated queries | Gemini built-in **Google Search grounding** (`GoogleSearchTool`) → result URLs → cheap relevance pass ("is this a funding program matching our sectors?") → full fetch+extract on passes only |
| **2. Configured** | `web_page` / `pdf` URLs from YAML | fetch → HTML→text (or Gemini document understanding for PDF) → artifact → extract records |
| **3. Crawl** | listing pages from lanes 1–2 | `fetch_source` returns top-N links with anchor text; scout picks promising detail pages (depth ≤ 2) → fetch each → extract |
| **4. Manual** | founder pastes text/URL in UI | `POST /wake` with "add this program: ..." → same scout path |

The additive `/discover` adapter also reaches these lanes, then the existing
system-session matchmaker and board. Natural-language discovery remains the
agent/scout route in v1. The two routes therefore promise **outcome-level**
equivalence (deduplicated opportunities, the same scoring guards, and the same
board), not identical compiled request envelopes. Compile-level equivalence is
still required by 21 Phase 2 and is not weakened by this adapter.

**Rendered-fetch fallback:** if a plain HTTP fetch returns a thin JS shell
(< 500 chars of text or a known SPA marker), re-fetch through the shared
Playwright browser (09) and extract from the rendered DOM text. This covers
JS-heavy portals (Gust, F6S) **without** any vision-loop browsing — the DOM is
still the source of truth.

**Search-backend fallback:** `GoogleSearchTool` compatibility with Vertex AI +
gemini-3.5-flash is **unverified by both reference implementations** — it is
verified by a 15-minute spike on Day 1 (14). If it fails, the fallback is a
plain search HTTP API behind the identical `search_programs` signature; the
lane contract does not change and nothing downstream notices.

Newsletter/email ingestion is future scope (mention in write-up as roadmap).
Deliberately out of scope: vision-driven "computer use" web browsing — program
information is public HTML; the lanes above cover it faster, cheaper, and
deterministically. A vision fallback can plug into `fetch_source` later if a
target ever defeats both HTTP and rendered DOM.

The messy-unstructured-data judging criterion is satisfied here: **web pages,
PDF guideline docs, and portal forms parsed into structured `Opportunity`
records with `raw_excerpt` citations.** Call this out in the Devpost write-up.

## Deadline sentinel flow

```
Cloud Scheduler (every 6h) ──► Pub/Sub topic: deadline-tick
   └─► POST /webhooks/deadline (fast durable enqueue; message-id dedupe)
        └─► Cloud Tasks → POST /tasks/deadline_scan
        └─ recompute urgency for all SHORTLISTED opportunities
           and FOLLOW_UP applications:
             days_left = deadline - today
             tier = OVERDUE | CRITICAL(≤3) | URGENT(≤14) | NORMAL(>14) | ROLLING(null)
        └─ for items crossing into CRITICAL: wake the founder's active
           session (if any) with notice "Resume: {name} closes in {n} days."
```

Urgency note format (matchmaker + sentinel both use it):
`"closes in 9 days, needs 2 essays — start now"` (deadline + required_materials
workload estimate).

## Task and scheduler wiring

| Work | Trigger | Target |
|---|---|---|
| Discovery | founder action only | Cloud Task → `/tasks/discover` |
| `deadline-scan-6h` | `0 */6 * * *` | Pub/Sub `deadline-tick` |

The deadline push subscription points at the Cloud Run deadline route with an
OIDC service account. Manual discovery is dispatched through Cloud Tasks with a
founder request ID. Commands are in 13-deployment. Local development runs
discovery directly: `curl -X POST localhost:8090/tasks/discover`.

Task routes return success only after request-bound work completes. Pub/Sub's
deadline webhook does no scan or model work: it durably enqueues one Cloud Task
using the message ID and acknowledges immediately. A worker crash therefore
causes bounded Cloud Tasks redelivery instead of a Pub/Sub push storm. Delivery
is at-least-once, so discovery receipts and deadline task-name deduplication are
mandatory.

## Failure behavior

- Source fetch fails → scout records `{"error": true}` in the sweep summary and
  continues with other sources. One dead source never fails a sweep.
- Extraction yields zero records from a previously-productive source → warn-level
  log + audit note (possible page redesign; feeds the form/page-staleness story).
- Matchmaker errors on one opportunity → that opportunity stays DISCOVERED;
  others proceed.

## Acceptance checks

- [ ] `curl -X POST localhost:8090/tasks/discover -H 'Content-Type: application/json' -d '{}'` with seeded YAML sources creates ≥ 1 SHORTLISTED and ≥ 1 ARCHIVED opportunity (seed data includes one obvious non-fit), each ARCHIVED with a specific reason.
- [ ] **Search lane:** with a seeded profile (sector + geography), a sweep discovers ≥ 1 real program NOT present in the configured sources, with a `raw_excerpt` citation — accepted against live search OR a replayed fixture (the video shows one live call; the board is pre-seeded).
- [ ] **Crawl lane:** from a seeded listing page containing 3 detail links, the scout fetches and extracts all 3 detail pages (depth ≤ 2 respected).
- [ ] **Rendered fallback:** a JS-shell test page yields extracted text only after the Playwright render path (log shows the fallback fired).
- [ ] Second sweep: `{new: 0}`.
- [ ] `deadline_scan` on a seeded opportunity with deadline in 2 days sets `urgency.tier=CRITICAL` and wakes a dormant session (log shows `state_delta` resume).
- [ ] `raw_excerpt` present on every extracted record (judge-visible citation).
