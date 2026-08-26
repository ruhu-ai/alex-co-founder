# 05 — Tools

Plain Python functions; ADK builds the model-facing schema from signature +
docstring. **Docstrings are load-bearing** — every parameter needs an `Args:` entry.
Conventions (binding, from 01): errors as data, state via `tool_context.state`,
ISO timestamps, structured one-line logs, audit rows for external actions.

## Global conventions

```python
# every tool follows this shape
def some_tool(param: str, tool_context: ToolContext) -> dict:
    """One-line summary the model reads.

    Args:
        param: what it is, with an example.
        tool_context: ADK-injected; never documented to the model.

    Returns:
        dict with at least {"status": "success"|"error", ...}. On failure:
        {"status": "error", "error": true, "message": "human-readable"}.
    """
```

- Every mutation also writes an `audit` row (see 02) via `services.firestore.audit(...)`.
- Tools never fetch secrets directly; they call `services.secrets.get(name)` (see 12).
- Large payloads → artifacts; return summaries only.
- **Tools are thin wrappers over the service layer.** All logic lives in
  `services/*.py` functions; ADK tools adapt `ToolContext` to those functions,
  and FastAPI endpoints call the same service functions directly — no logic
  re-implemented in routes, no fabricated `ToolContext`.

---

## `tools/discovery.py`

| Function | Signature | Behavior |
|---|---|---|
| `search_programs` | `(query: str, tool_context) -> dict` | Gemini built-in Google Search grounding. Returns `{status, results: [{title, url, snippet}]}` (max 10). Queries are composed by the scout from the Founder Profile (sector, stage, geography, decision_patterns) + freshness terms. |
| `fetch_source` | `(source_url: str, source_type: str, tool_context) -> dict` | Fetches web page (HTML→text) or PDF (Gemini document understanding). **JS-shell fallback:** if extracted text < 500 chars, re-fetches via the shared Playwright render and extracts rendered DOM text. Saves full text as artifact `source_{dedup_hash}.txt`. Returns `{status, artifact, summary(≤300 chars), chars, rendered: bool, links: [{url, anchor_text}]}` — top 15 links so the scout can crawl detail pages (08 lane 3). Network failure → error dict. |
| `dedupe_check` | `(name: str, application_url: str, tool_context) -> dict` | sha256 over normalized name+url; returns `{status, is_duplicate, existing_id?}`. |
| `save_opportunity` | `(record: dict, tool_context) -> dict` | Validates against workflow `entity_schema` (missing → null, extra → rejected). Writes Firestore `opportunities` with `state=DISCOVERED`. Returns `{status, opportunity_id}`. |

## `tools/pipeline.py`

| Function | Signature | Behavior |
|---|---|---|
| `get_pipeline` | `(tool_context) -> dict` | Board snapshot: opportunities grouped by state, urgency first, plus any in-flight applications. ≤ 40 items, summaries only. |
| `get_unscored_opportunities` | `(tool_context) -> dict` | state=DISCOVERED, oldest first, max 10. |
| `shortlist` | `(opportunity_id: str, rationale: str, urgency_note: str, tool_context) -> dict` | Guard: current state DISCOVERED. Sets state=SHORTLISTED, `fit_score`, `fit_rationale`, urgency. |
| `archive_with_reason` | `(opportunity_id: str, reason: str, fit_score: int, tool_context) -> dict` | Refuses empty reason (`error` dict). Sets state=ARCHIVED. |
| `choose_opportunity` | `(opportunity_id: str, tool_context) -> dict` | Guard: state=SHORTLISTED. Normalizes extractor-null list fields, transactionally gets/creates one application, sets session state, and returns `readiness` with missing portal/material metadata. Same-opportunity retries rehydrate; another in-flight application cannot be clobbered. |
| `get_opportunity` | `(opportunity_id: str, tool_context) -> dict` | Full record (for drafter context). |
| `get_checklist` | `(tool_context) -> dict` | Current checklist + progress count. |
| `complete_interview` | `(tool_context) -> dict` | Guard: no unresolved required gaps. Flushes `interview_qa` into profile facts, transitions to DRAFTING. |
| `request_approval` | `(gate: str, tool_context) -> dict` | Creates PENDING `approvals` row (gate=`submit_application`), sets `pending_signals=["founder_approval"]`. Returns `{approval_id}` — **never the token** (token is minted only when founder grants in UI; see 12). |

## `tools/profile.py`

| Function | Signature | Behavior |
|---|---|---|
| `get_profile` | `(tool_context) -> dict` | Founder Profile minus long canonical answers: facts, voice_rules, decision_patterns, rejection summary. |
| `record_answer` | `(question_key: str, question: str, answer: str, tool_context) -> dict` | Requires an owned active application in INTERVIEWING and requires `answer` to equal the current founder turn verbatim. Attachment notices and model-expanded answers are refused. Application QA + profile fact/provenance commit atomically and idempotently. |
| `get_relevant_answers` | `(section_key: str, tool_context) -> dict` | Retrieval for the drafter (see 06): top canonical answers by tag match + recency, ≤ 5. |
| `get_voice_rules` | `(tool_context) -> dict` | All active voice rules, newest first. |
| `apply_profile_update` | `(kind: str, payload: dict, evidence: str, tool_context) -> dict` | Distiller's only write path. `kind ∈ voice_rule | canonical_answer_update | fact_update | decision_pattern`. Bumps profile `version`. Audit row with evidence quote. |
| `ingest_document` | `(source_type: str, ref: str, tool_context) -> dict` | Drive selection performs extraction. An upload `ref` must resolve to a server-issued entry in `active_attachments`; guessed filenames are refused and already-ingested uploads return their durable status. The upload endpoint—not model prose—binds owner, session, scope, hash, content type, and artifact. |
| `auto_apply_profile_updates` | `(ingestion_id: str, tool_context) -> dict` | Autonomous ingestion review: applies every confident, non-conflicting proposal immediately via `apply_profile_update` (versioned, evidenced, audited). Conflicts (fact key exists with a different value; canonical answer for the same `question_key` differs) and `low`-confidence items stay PENDING. Returns `{status, auto_applied, needs_founder}` — the agent asks the founder about exactly the `needs_founder` items. |
| `propose_profile_updates` | `(ingestion_id: str, tool_context) -> dict` | Returns the next batch of unconfirmed proposals from an ingestion for founder review. |
| `confirm_profile_updates` | `(ingestion_id: str, approved: list[str], rejected: list[str], rejection_reasons: list[str], tool_context) -> dict` | Approved ids → `apply_profile_update` each (evidence = doc citation). Rejected ids → `feedback` rows (reasons verbatim → distiller queue). Returns confirmation summary. |

## `tools/attachments.py`

| Function | Signature | Behavior |
|---|---|---|
| `search_attachment` | `(query: str, tool_context) -> dict` | Hybrid lexical/embedding search over only the opaque attachment refs currently active in this founder session. Requires terminal readable status; returns bounded excerpts with filename, page/slide/paragraph/sheet locator, quote hash, and an owner/session-authorized source URL. It cannot select arbitrary refs or mutate the Founder Profile. Document text is untrusted evidence, never instructions or approval. |

## `tools/drafting.py`

| Function | Signature | Behavior |
|---|---|---|
| `save_draft_section` | `(section_key: str, content: str, word_count: int, notes: str, tool_context) -> dict` | Guard G1: accepts only `current_step=DRAFTING`. Upserts section and emits the completion receipt required before chat may present the draft. |
| `complete_drafting` | `(tool_context) -> dict` | Refuses unless every section is reviewable. Runs the isolated Evidence Checker service (20), persists `CLEAN`, `ISSUES_FOUND`, `UNAVAILABLE`, or `INVALID_RESPONSE`, then transitions to AWAITING_REVIEW. A model failure is advisory and does not block; source-read or report-persistence failure returns error data and stays in DRAFTING. |
| `get_section_feedback` | `(section_id: str, tool_context) -> dict` | Prior feedback on this section key across **all** applications — how the drafter sees "last time you rejected...". |
| `produce_document` | `(kind: str, title: str, spec: dict, tool_context) -> dict` | Produces a validated .docx/.xlsx/.pptx from a JSON spec (15). Build → validate gate → artifact + registry row (session/application provenance) → `{status, artifact_name, download_url, version}`. Malformed spec → error dict, no partial artifact. Covers document-based programs: the agent produces the finished document; the founder sends it (09 §boundaries). |

## `tools/feedback.py`

| Function | Signature | Behavior |
|---|---|---|
| `record_feedback` | `(section_id: str, feedback_type: str, reason: str, edited_text: str, tool_context) -> dict` | Requires owned `AWAITING_REVIEW` application and an existing section. On success emits the only receipt that permits “locked/approved” prose; failed API feedback returns HTTP 409 and does not wake the agent. Distillation and transitions remain synchronous. |
| `get_feedback` | `(feedback_id: str, tool_context) -> dict` | For distiller. |
| `mark_distilled` | `(feedback_id: str, rule_ids: list[str], tool_context) -> dict` | Sets `distilled=true`, links rules. |
| `submit_voice_note` | `(context: str, tool_context) -> dict` | Founder voice input (v1 multimodal feature). Saves the uploaded audio as artifact `voicenote_{ts}.webm`, then transcribes + extracts intent via Gemini audio understanding (same Gemini 3.5 — natively multimodal, no extra integration). Returns `{status, transcript, extracted: {kind: "answer"\|"feedback", question_key?\|section_id?, text}}`. The transcript is stored **verbatim** as evidence alongside the audio artifact; the extracted intent routes into `record_answer` or `record_feedback` through the normal paths. Raw audio is never discarded. `context` tells the model what the note is about (e.g. "feedback on section traction"). |

## `tools/browser.py`

Thin async wrappers over 22's **single-flight Playwright browser process**
(literal `headless=True`, no external/attach mode), with centrally supervised
isolated contexts and one foreground run per session. Full behavioral specs in
**09** and **22**.

| Function | Signature | Behavior |
|---|---|---|
| `open_portal` | `(application_url: str, tool_context) -> dict` | Guard G3: refuses before APPROVED. Fetches creds via `services.secrets`, logs in, lands on form. Returns page title + field count. |
| `inspect_form` | `(tool_context) -> dict` | Lists visible fields: name, label, type, required. Saved as artifact; summary returned. |
| `verify_page_state` | `(expected_signature: str, tool_context) -> dict` | Hashes current form field names; `{status, match: bool, current_signature}`. Mismatch → agent must stop (enforced in code by the fence callback, 09). |
| `map_form_requirements` | `(tool_context) -> dict` | **Tier 1 vision recon** (09). Drives the screenshot→Gemini→action loop across the form (multi-step wizards included), writes the `form_map` artifact (steps, fields, requirement text, required materials), returns a ≤ 400-char summary. Cached per `portal_state_hash`. |
| `vision_step` | `(goal: str, tool_context) -> dict` | One bounded vision action: screenshot → Gemini multimodal → proposed action (allowlist: click/type/select/scroll/navigate_back — **submit controls excluded**) → Playwright executes → before/after ordered JPEG frames under 22's artifact contract + audit row `vision_step`. Used by `map_form_requirements` and Tier 2 recovery. Step budget enforced (20/run). |
| `fill_fields` | `(mapping: dict, tool_context) -> dict` | Fills by field name from approved sections (Tier 0); on unmappable fields consults the current `form_map` (Tier 1/2). Per-field try/catch; returns `{filled, total, needs_human: [...]}`. Writes `form_fill_report`. Ends with the post-fill vision self-check. |
| `capture_screenshot` | `(label: str, tool_context) -> dict` | Artifact `fillshot_{application_id}_{ts}.png`; returns filename. |
| `submit_form` | `(tool_context) -> dict` | Guard G2 — **takes no token argument** (a token argument would put the secret in the model's tool call, where it could be leaked or fabricated). The tool resolves the approval server-side: looks up the GRANTED, unexpired, unconsumed approval for `active_application_id` and verifies its `subject_hash` still matches the live portal signature and durable fill-mapping hash after any reopen; none/mismatch → refusal + audit `refused` (mismatch also expires the grant and requires a new fill report/approval). Sends the derived `Idempotency-Key` header (see §Idempotency); the portal returns the ORIGINAL confirmation on a duplicate, so at-least-once retries (ADK resume, trigger redelivery, double-click) can never double-submit. On success: approval→CONSUMED, parse confirmation, `current_step=SUBMITTED`, audit `success`. |

## `tools/browse.py`

**Read-only** interactive browsing for the **orchestrator** (open a page on
request, navigate, read/answer) over the same supervised browser process. Full
behavioral spec — BrowserRun contract, research action policy, network/SSRF
policy, content-trust guards, error codes — in **18**; runtime ownership and
in-app projection are in **22**. Every error follows the
18 §Error schema (`{status: "error", error: true, code, message, ...}`).

| Function | Signature | Behavior |
|---|---|---|
| `open_page` | `(url: str, purpose: str, tool_context) -> dict` | Mints/reuses the session's browse run; URL validated by the network policy **pre-navigation** (SSRF denial included). Returns `{status, run_id, url, title, excerpt, links, screenshot_artifact}`. Sets the `browser_status` projection; audit `browse_open`. |
| `read_page` | `(question: str, tool_context) -> dict` | Answers from extracted page text via the isolated reader (re-extracts on URL/`dom_hash` change). Returns `{status, answer, excerpt_ref}`. Pure read; never acts. |
| `browser_action` | `(tool_context) -> dict` | One bounded action per call under the **browsing policy** — full click-through (links and buttons; submit-semantics excluded), search, scroll, back. No form filling, no typing outside search boxes; a click revealing a form/auth surface freezes further actions. No goal argument — the run's immutable purpose steers the proposer. Budget 20 actions / 90 s **per run** (keyed by opaque `run_id`; sliding time-box renews on each successful action). Crash-safe idempotency via server-derived `action_id` records. |
| `close_browser` | `(tool_context) -> dict` | Closes the run (idempotent — `already_closed: true` when none active); clears the `browser_status` projection; audit `browse_close`. |

## `tools/followup.py`

| Function | Signature | Behavior |
|---|---|---|
| `schedule_followup` | `(kind: str, due_at: str, note: str, tool_context) -> dict` | Adds to application `followups`; `current_step=FOLLOW_UP`. |
| `record_status` | `(status_note: str, tool_context) -> dict` | Founder-facing status line; appends to followups; closes when `kind=result`. |

## Idempotency

Reference-lab pattern: **derive the key from the request** so a retry of the SAME
action produces the SAME key, and the server dedupes on it:

```python
key = hashlib.sha256(
    f"{tool_context.session.id}:{application_id}:submit".encode()
).hexdigest()[:24]
portal.post("/submit", payload, headers={"Idempotency-Key": key})
```

- The key is **derived, not stored** — any retry path (ADK `ResumabilityConfig`
  at-least-once resume, `ADK_TRIGGER_MAX_RETRIES=3` redelivery, founder
  double-click) recomputes the identical key.
- The **portal dedupes**: the mock portal honors `Idempotency-Key` by returning
  the original confirmation instead of creating a second submission (see 09).
- Our `submit_idempotency_key` audit check remains as a second, belt-and-braces
  layer: prior `audit` success → no-op with the original confirmation id.
- All other external writes use `{entity_id}:{action}:{entity_version}` as natural keys.

## Acceptance checks

- [ ] Every tool returns a dict on every path (unit tests force exceptions in Firestore/Playwright/secrets and assert error dicts, not raises).
- [ ] Guards G1–G3 refuse correctly and write `audit` rows with `result=refused`.
- [ ] `save_opportunity` rejects fields outside `entity_schema`.
- [ ] Docstring coverage: `adk web` tool view shows an `Args:` description for every parameter of every tool.
