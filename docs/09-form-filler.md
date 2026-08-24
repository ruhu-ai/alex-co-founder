# 09 — Form-Filler & Mock Portal

The action proof. The form-filler navigates a real application portal and
pre-fills from approved answers; the mock portal guarantees a reliable demo.
**The mock portal is the primary demo path; a real portal is the stretch goal.**

## Safety model (implements 03 guards + 12)

1. **G3:** `open_portal` refuses before the application reaches APPROVED.
2. **Approval gate:** after filling, the agent stops at AWAITING_SUBMIT_APPROVAL.
   Submission requires a single-use token minted only by the founder's UI grant.
3. **Staleness guard — in CODE, not in the prompt (corrected per reference lab
   Task 7):** the form_filler_agent is constructed with
   `before_tool_callback=verify_before_action`. Before any `fill_fields` or
   `submit_form` call, the callback re-hashes the live form's field signature and
   compares it to the inspected signature. Mismatch → the callback **returns a
   dict, which short-circuits the tool** — the fill/submit never executes, and
   the model receives `{"status": "error", "stale": true, "believed": ...,
   "current": ...}`. "Ask for it in the prompt and the model complies most of the
   time. Ask for it in code and it complies every time." The `verify_page_state`
   tool remains for on-demand checks; the callback is the guarantee.
4. **Idempotency:** derived `Idempotency-Key` header (05 §Idempotency); the
   portal returns the original confirmation on duplicates.
5. **Partial success is a first-class outcome:** "filled 14/16; these 2 need you"
   is a good result, not a failure.
6. **ToS:** automated pre-fill only on portals that permit it; the mock portal
   exists precisely so the demo never depends on a third party's consent.

## PortalAdapter interface (`tools/browser.py`)

```python
class PortalAdapter(Protocol):
    async def login(self, page, creds: dict) -> None: ...
    async def form_signature(self, page) -> str: ...        # hash of field names
    async def field_mapping(self, application: dict) -> dict: ...  # field name -> value
    async def submit(self, page) -> str: ...                # returns confirmation_id
```

v1 ships `MockPortalAdapter` (full) + optionally `RealPortalAdapter` (stretch).
Adapters live behind the interface; the agent tools call the active adapter only.

## Vision browser agent (tiered architecture — vision is first-class)

Real program applications are online forms — often multi-step wizards with
fields hidden behind "Next" clicks, conditional logic, and JS rendering
(Submittable, F6S, SurveyMonkey Apply, custom React portals). Reading *what a
form requires* on an arbitrary portal is where a vision browser agent earns its
keep: it looks at the page and understands it like a human, without per-portal
code. So vision is a first-class component — wrapped in the same guards as
everything else.

**Model:** Gemini 3.5 multimodal / computer-use capability — screenshots go in,
structured actions come out. Same mandatory stack; no third-party browser agent,
no separate vision model to integrate.

```
TIER 0 — DOM fast path (known portals: mock + adapted real portals)
         Playwright selectors + field signatures. Default. Seconds per form.

TIER 1 — VISION RECONNAISSANCE (map_form_requirements)
         For ANY portal, known or not. The vision loop navigates the form
         like a human (screenshot → Gemini → action → Playwright executes →
         repeat), traverses multi-step wizards, reads requirement/instruction
         text, and emits a form_map artifact:
           {steps: [{step, fields: [{label, field_type, required,
             maps_to_section_key|null}], page_requirements: [str]}],
            required_materials: [str], notes: str}
         This is how the agent discovers "what details are needed to complete
         the application" on a portal it has never seen — and it feeds the
         Interviewer's gap analysis (requirements vs. Founder Profile).

TIER 2 — VISION RECOVERY & FILL (escalation only)
         Engaged when: the staleness fence reports a signature mismatch
         (portal changed), a field defeats DOM mapping (custom widget), or
         the page has no usable DOM. Tier 1 re-maps the form, then the fill
         proceeds under the same constraints.
```

**Guardrails — binding on every tier, no exceptions:**

0. **Shared execution primitive:** vision actions execute through
   `services/browser_service.execute_action(page, proposal, policy="form_fill")`
   (18 §Action model) — indexed-snapshot proposals, element + `dom_hash`
   revalidation immediately before execution, idempotent `action_id`s. The
   `form_fill` policy keeps the wider allowlist below; general browsing uses
   the narrower `research` policy.

1. **The vision loop can never submit.** Its action allowlist is
   `click | type | select | scroll | navigate_back` — the final submit control
   is excluded by construction. Submission happens ONLY via the deterministic,
   approval-token-gated `submit_form` (G2). The vision agent proposes; the
   gated path disposes.
2. **Step budget AND time-box:** max 20 vision actions per run **and 90 seconds
   per recon run** — on either limit, return the partial `form_map` +
   `needs_human` (same partial-success semantics as DOM fills). The demo shows
   the cached artifact + a truncated live segment, never a full silent wait
   (a pause on camera reads as "broken").
3. **Every vision action is evidenced:** screenshot before/after saved as
   artifacts + an `audit` row per action (`action=vision_step`, detail =
   proposed action + target). The UI audit tail shows the agent "looking."
4. **Staleness fence and derived idempotency keys wrap the vision path exactly
   as the DOM path** — recovery re-maps first, never guesses.
5. **Cost/latency containment:** Tier 0 is always tried first on known portals;
   vision runs are cached per `portal_state_hash` (a recon map is reused while
   the signature is unchanged).

**Post-fill self-check (both tiers):** after filling, the final screenshot +
intended mapping go to Gemini multimodal — "does the page look correctly
filled?" Discrepancies land in `needs_human`.

**PDF guideline parsing (Scout):** unchanged, see 08.

**Demo beat ( upgraded):** run the mock portal in `?v=2` mode (fields renamed)
→ DOM fence stops the fill → Tier 1 vision recon re-maps the form live, names
what changed → fill completes. One unedited minute that proves staleness
handling AND vision capability.

File-upload fields: if the founder has a matching attachment artifact in storage
(e.g. deck PDF, see 02), `fill_fields` uploads it from the artifact; otherwise
the field lands in `needs_human` with reason "file upload — choose the file."

## Application surfaces & boundaries (what "applying" covers)

Programs accept applications three ways; v1 covers each deliberately:

| Surface | v1 behavior |
|---|---|
| **Online portal** | Form-filler pre-fills; founder approves; agent submits. (This spec.) |
| **Document (PDF/DOCX emailed in)** | `generate_application_pack` (05) produces the finished PDF from approved answers; **founder sends it**. No agent-sent email in v1 (deliverability/ToS complexity — same reason the brief defers investor outreach). |
| **Chat/email correspondence** | Follow-up tracking + drafted replies in our UI; founder sends. Roadmap item, noted in the write-up. |

## Browser session

- One module-level Playwright Chromium instance per server process; a new
  browser **context** per fill run (isolated cookies), closed after.
- **Every fill run is recorded in Firestore `browser_runs` with `kind="fill"`**
  (18 §BrowserRun contract): created at `open_portal` (`status=active`),
  updated with `current_url` / `screenshot_artifact` / `last_action` through
  the run, and closed (`agent_close` | `error`) when the run ends — this is
  what lets the UI Browser panel (18) watch fills live with the same payload
  shape as browse runs.
- Playwright is always headless; development and Cloud Run both mirror progress in the in-app Browser panel.
- Every fill run: screenshots after fill and after submit → artifacts.

## Fill report (`applications.form_fill_report`)

```json
{"filled": 14, "total": 16,
 "needs_human": [{"field": "deck_upload", "reason": "file upload"},
                 {"field": "referral_source", "reason": "not in approved answers"}],
 "portal_state_hash": "sha256:...",
 "screenshot_artifact": "fillshot_app123_20260825T1012Z.png",
 "ran_at": "..."}
```

## Mock portal (`mock_portal/`) — full spec

A standalone FastAPI service, deployed as its own Cloud Run service. It simulates
a realistic program application portal and **calls the agent back** via webhook.

**Routes:**

| Route | Behavior |
|---|---|
| `GET /` | landing page listing 2 open programs |
| `GET /login` + `POST /login` | session cookie auth; creds = `mock-portal-creds` secret (`demo-founder` / `demo-pass-2026`) |
| `GET /apply/{program_id}` | the application form: 16 fields (text, textarea, select, date, **file upload**, **one dynamic select** whose options load after a region pick) |
| `POST /apply/{program_id}/save` | accepts partial field payload; returns `{saved: n}` |
| `POST /apply/{program_id}/submit` | validates required fields; honors the `Idempotency-Key` header — a repeated key returns the ORIGINAL `{"confirmation_id": "MP-1042"}` instead of creating a second submission; fires `POST {AGENT_BASE_URL}/webhooks/portal_event` with `kind=submission_confirmed` |
| `GET /admin/reset` | wipes submissions (demo repeatability) |
| `GET /admin/ping-agent` | **pre-flight check:** fires a signed test `portal_event` at `{AGENT_BASE_URL}/webhooks/portal_event` and verifies a 200 — run before every recording (catches wrong `AGENT_BASE_URL` / token mismatch before the camera does) |
| `GET /healthz` | |

**Deliberate friction (so the demo is honest):**
- The file-upload field cannot be filled by the agent → lands in `needs_human`.
- The dynamic select requires a two-step interaction → shows real browser work.
- A `?v=2` query flag changes two field names → exercises the staleness guard
  (demo beat: "the portal changed; the agent stopped instead of guessing").

**Webhook signing:** mock portal includes header `X-Portal-Token` matching a
shared secret; the agent verifies it (cheap stand-in for real signature schemes).

## Demo sequence (implements brief §7 beat 5)

1. Application at APPROVED → founder clicks **Fill form** in UI.
2. `open_portal` → login via Secret Manager creds → `inspect_form` →
   `verify_page_state` → `fill_fields` → report + screenshot appear in UI.
3. Agent: "Filled 14 of 16. The deck upload and one answer only you can give
   are marked for you. Approve submission when ready."
4. Founder clicks **Approve submission** → token minted → agent `submit_form`
   → confirmation `MP-1042` → mock portal webhook fires → application moves to
   FOLLOW_UP live on screen.
5. Judges see: browser logs, fill report, Firestore updates, webhook arrival,
   state transition — unedited.

## Acceptance checks

- [ ] End-to-end against the mock portal twice in a row: second `submit_form` returns "Already submitted" with the original confirmation id (idempotency).
- [ ] Running with `?v=2` portal: fence stops the fill, **Tier 1 vision recon re-maps the form**, the report names the changed fields, fill then completes — **no** fields were guessed.
- [ ] **Vision recon coverage:** `map_form_requirements` on the mock portal produces a `form_map` artifact covering all 16 fields across steps (including the dynamic select and the file upload) plus the page's stated requirements; artifact is inspectable in the console.
- [ ] **Vision cannot submit:** the `vision_step` allowlist provably excludes submit controls (attempt returns `{"error": true, "message": "action not permitted"}` + audit row); submission occurs only through `submit_form` with a valid token.
- [ ] Step budget: a recon loop capped at 20 actions; exhaustion yields partial `form_map` + `needs_human`, never a hang.
- [ ] Every vision action has before/after screenshot artifacts + an `audit` `vision_step` row.
- [ ] `submit_form` with an expired/denied/consumed token refuses and writes `audit` `result=refused`.
- [ ] Screenshots saved as artifacts on both fill and submit.
- [ ] `GET /admin/reset` restores a clean demo in < 5 s.
