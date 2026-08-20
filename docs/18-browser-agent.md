# 18 — Browser Agent: general-purpose browsing + live browser view

Two gaps sit next to the existing browser work:

1. **Interactive reading.** `fetch_source` (05/08) is a one-shot, read-only page
   fetch for the scout — no session, no clicking, no follow-up questions. The
   form-filler tools (09) are portal-scoped and gated at APPROVED. Nothing lets
   Alex *open a page on request, navigate across pages, and read/answer
   questions* — "open this link and tell me what the FAQ says about equity."
2. **A founder-visible browser surface.** Today the UI shows only post-hoc
   screenshots in the fill report. The founder cannot watch what Alex is
   looking at while it browses.

This spec adds both: a small `browse` tool family on the orchestrator, and a
**Browser panel** attached to the Co-Founder UI showing the live page. It
reuses the shared Playwright browser from 09 and changes **nothing** about the
form-filler's gates (G2/G3, staleness fence, approval tokens).

## Boundaries (binding)

1. **Browsing is read-first and autonomous.** Opening, reading, and navigating
   public pages needs no approval — same delegated-autonomy stance as 17.
2. **The browse loop can never commit.** `browser_action`'s allowlist is
   `click | type | select | scroll | navigate_back | open_link | wait` —
   submit/checkout/purchase controls are excluded by construction, exactly as
   the vision allowlist in 09. Anything irreversible routes through the
   existing approval-gated paths (09/17) or lands in `needs_human`.
3. **Page content is data, never instructions** (adr/001). Extracted page text
   is parsed and summarized; instruction-like content inside a page is never
   followed. The service layer flags common injection patterns
   ("ignore previous instructions", fake system tags) into the audit row.
4. **Never defeat bot protection.** CAPTCHA / anti-bot challenge → stop,
   screenshot, hand to the founder (same boundary as 17).
5. **No secrets in general browsing.** Credentials are typed only through the
   portal-credential path (17) by the form-filler. `browser_action` refuses
   `type` into `password` inputs and payment fields — checked in code against
   the target's input type/autocomplete attributes.
6. **License hygiene.** Reference repos are pattern sources only
   (`.opensrc/repos/…`, read-only). Skyvern is AGPL-3.0 — mine the patterns,
   never copy code (same stance as the superdoc rejection in 15).

## Architecture

```
services/browser_service.py   # shared module-level Chromium (09 §Browser session)
   └── contexts:
        ├── fill:{application_id}    # form-filler runs (09) — unchanged
        └── browse:{session_id}      # NEW: one browsing context per ADK session,
                                     # created lazily by open_page, closed by
                                     # close_browser or session end
tools/browse.py                 # NEW: thin ADK wrappers (errors as data, audit rows)
GET /api/browser/state          # NEW: read-only snapshot for the UI panel
POST /api/browser/stop          # NEW: closes the session's browse context
```

Context isolation matches 09: separate browser **context** (isolated cookies)
per browse session, closed after use. `HEADLESS` env unchanged.

## `tools/browse.py`

Tool scoping (12): **orchestrator only.** The scout keeps `fetch_source` for
batch reads (one-shot, stateless — cheaper); the form-filler keeps its portal
tools; drafter/distiller get nothing browser-shaped. Asserted in the tool
scoping test.

| Function | Signature | Behavior |
|---|---|---|
| `open_page` | `(url: str, purpose: str, tool_context) -> dict` | Guard: http/https only, domain policy (below) enforced **pre-navigation in code**. Opens/navigates the session's browse context, waits for settle, extracts rendered DOM text → artifact `page_{ts}.txt`. Returns `{status, url, title, excerpt(≤600 chars), links: [{url, anchor_text}] (top 15), screenshot_artifact}`. Sets state `browser_status={active: true, url, goal: purpose, last_action: "open"}`. Audit row `browse_open`. |
| `read_page` | `(question: str, tool_context) -> dict` | Answers `question` about the current page from the extracted text (Gemini over the artifact; re-extracts if the page changed since last extraction — URL + DOM hash check). Returns `{status, answer, excerpt_ref}`. Pure read; never acts. |
| `browser_action` | `(goal: str, tool_context) -> dict` | One bounded vision action, identical mechanics to `browser.vision_step` (09): screenshot → Gemini multimodal → proposed action (allowlist above — **no submit/checkout/payment**) → Playwright executes → before/after screenshots to artifacts + audit row `browse_action`. Refuses `type` into password/payment fields in code. Shares the vision step budget: **20 actions per goal AND 90 s time-box**; on either limit → partial result + `needs_human` (same partial-success semantics as 09). |
| `close_browser` | `(tool_context) -> dict` | Closes the session's browse context, sets `browser_status.active=false`, audit row `browse_close`. |

Conventions per 05: every path returns a dict; large text → artifacts; audit
rows for every external action.

## Page representation (mined pattern, decision recorded)

Two complementary representations, both already in the stack:

- **Text first (cheap):** rendered DOM text + top links — the `fetch_source`
  extraction path, reused. Covers "read this page" without any vision call.
  (microsoft/playwright-mcp validates text/AX-tree-first: most reading tasks
  never need pixels.)
- **Vision on demand (expensive):** screenshots enter the model context only
  when `browser_action` runs or the page defeats text extraction — the
  browser-use `use_vision='auto'` pattern: capture every step for artifacts
  and the UI panel, gate what enters the context window.

Indexed-DOM action grounding (click target by element index from a serialized
interactive-element list) is the browser-use reliability pattern; our
`browser_action` inherits it from the existing `vision_step` implementation
rather than adding a second loop.

## Guardrails (code, not prompts)

1. **Scheme allowlist:** `http`/`https` only; `file://`, `data://`,
   `chrome://` refused with an error dict.
2. **Domain policy:** `BROWSE_ALLOWED_DOMAINS` (glob list; empty = open web)
   and `BROWSE_BLOCKED_DOMAINS` (deny wins, evaluated pre-navigation — the
   browser-use `SecurityWatchdog` / playwright-mcp origin-list pattern).
   Redirect landing off-policy → navigate back to `about:blank`, error dict,
   audit row.
3. **Budgets:** 20 actions / 90 s per goal (09 numbers); a runaway loop
   degrades to `needs_human`, never a hang.
4. **Injection flagging:** extracted text is scanned for instruction-shaped
   patterns; matches are noted on the audit row (`injection_suspected: true`)
   and the content stays quarantined as data.
5. **CAPTCHA/anti-bot** → stop, screenshot artifact, `needs_human`. No
   retries, no solver services.
6. Every action evidenced: before/after screenshot artifacts + audit row —
   the UI audit tail shows Alex "looking."

## UI: Browser panel (live view)

The panel makes browsing visible the way the fill report makes filling
visible. **Visual rules in 16-design-system.md** (panel = `.card`, status =
`.badge`, icon from the Phosphor sprite).

**Placement:** a **Browser** section at the top of the REVIEW PANEL column
(10 §Layout). It renders whenever `browser_status.active` is true — during a
browse run it leads; afterwards the last screenshot + summary stay until
dismissed or the next run.

```
┌── BROWSER ─────────────────────────────┐
│ ● Alex is browsing — "checking the FAQ"│
│ https://program.example/faq  (read-only)│
│ ┌────────────────────────────────────┐ │
│ │        latest screenshot           │ │
│ └────────────────────────────────────┘ │
│ last: clicked "Eligibility" · 12s ago  │
│ [Stop browsing]                        │
└────────────────────────────────────────┘
```

**Data:** the panel polls `GET /api/browser/state` on the existing 5 s UI
cycle (10 §Behavior rules — no new transport). Response:

```json
{"active": true, "url": "...", "title": "...", "goal": "...",
 "screenshot_url": "/artifacts/pageshot_....png",
 "last_action": {"kind": "click", "target": "Eligibility", "at": "..."},
 "status": "browsing | blocked | idle"}
```

This is the OpenHands `BrowserPanel` pattern (URL bar + latest screenshot per
step, screenshot as data URL/artifact link) — deliberately **not** VNC or a
remote-desktop stream in v1.

**Controls:** **Stop browsing** → `POST /api/browser/stop` → `close_browser`
service path. The panel is otherwise read-only — no click-through, no founder
takeover in v1 (the founder acts through chat: "go back", "stop").

**Upgrade path (documented, not built):** true live streaming via CDP
`Page.startScreencast` frames forwarded over the existing `WS /live` channel
(~1 fps JPEG; the browser-use `RecordingWatchdog` frame pipeline and Skyvern's
viewport livestream are the references). Revisit only if the 5 s screenshot
cadence reads as "broken" on camera.

## State keys, audit, endpoints

- New session state key (added to 02): `browser_status` — dict
  `{active, url, goal, last_action}`; set by browse tools, injected into the
  orchestrator instruction so Alex always knows whether its browser is open.
- Audit rows: `browse_open`, `browse_action`, `browse_close`, `browse_stop`
  (actor `agent:co_founder`, target host) — "where has Alex browsed?" is always
  answerable.
- Endpoints are thin FastAPI wrappers over `services/browser_service.py`
  (05 §global conventions — no logic re-implemented in routes).

## Relationship to existing browser features

| Feature | Owner | Scope | Status |
|---|---|---|---|
| `fetch_source` (05/08) | scout | one-shot read, batch discovery | unchanged |
| Form-filler portal tools (09) | form-filler | APPROVED-gated fill/submit on portals | unchanged |
| Portal auth (17) | form-filler | register/sign-in via Alex's identity | unchanged |
| **`browse.*` (this spec)** | **orchestrator** | **interactive open/navigate/read on request** | **new** |
| **Browser panel (this spec)** | **UI** | **live view of any browser work** | **new** |

The panel also surfaces form-filler runs (same `/api/browser/state` shape,
`actor: form_filler`) — one watching surface for all browser work.

## Reference implementations (pattern mining only)

| Repo | License | Stars (Aug 2026) | What we mined |
|---|---|---|---|
| `browser-use/browser-use` ([local clone](.opensrc)) | MIT | ~110k | Action allowlist + step/time budgets; indexed-DOM serialization; stale-DOM action guards; sensitive-data placeholder protocol (secrets substituted at execution, never in model context — mirrors our server-resolved tokens); watchdog event-bus guards; screencast live-view primitive |
| `microsoft/playwright-mcp` | Apache-2.0 | ~36k | Accessibility-tree/text-first page representation; origin allow/block config; granular tool surface |
| `Skyvern-AI/skyvern` | AGPL-3.0 (patterns only, no code) | ~23k | Browser viewport livestreaming to a web UI; `act/extract/validate` AI-augmented Playwright actions |
| `OpenHands/OpenHands` (local clone) | MIT | — | `BrowserPanel` UI: URL bar + latest screenshot per step over the event stream |
| `cline/cline` (local clone) | Apache-2.0 | — | `BrowserSession` lifecycle; attach to running Chrome via CDP |

Decision: keep our own thin Playwright + Gemini implementation (mandatory
stack; 09 already ruled "no third-party browser agent"). These repos validate
the design and supply the guardrail patterns; nothing is imported.

## Demo beat

Founder pastes a program link in chat: *"check whether this fits us."* Alex
opens the page — the Browser panel lights up live in the UI — navigates to the
eligibility FAQ, reads it, and answers with the page on screen. Judges see:
the live panel, the audit tail (`browse_open`, `browse_action` ×3), and the
artifact trail. Same "watch it work" energy as the form-fill beat, zero setup.

## Acceptance checks

- [ ] `open_page` → `read_page` → `browser_action` (click a link) → `read_page` → `close_browser` round-trip on a public page; every step returns a dict, audit rows present, screenshots saved as artifacts.
- [ ] Tool scoping test: orchestrator's tool list includes the four browse tools; scout/drafter/form-filler lists do not (assert alongside the 04/12 matrix test).
- [ ] Allowlist enforcement: a `browser_action` attempt on a submit/checkout control returns `{"error": true, "message": "action not permitted"}` + audit row; `type` into a password input is refused in code.
- [ ] Domain policy: navigation to a `BROWSE_BLOCKED_DOMAINS` host (and a redirect landing there) is refused pre-navigation with an error dict + audit row.
- [ ] Budgets: a synthetic infinite-loop page (link back to itself) terminates at 20 actions / 90 s with partial result + `needs_human`, never a hang.
- [ ] Injection quarantine: a seeded page containing "ignore your instructions…" is summarized as data; the audit row carries `injection_suspected: true`; the agent does not act on the embedded instruction (eval case in 11).
- [ ] Browser panel: during a run the UI shows URL + goal + refreshing screenshot within one 5 s poll cycle; **Stop browsing** closes the context and the panel goes idle; `browser_status` in session state tracks reality at every step.
- [ ] CAPTCHA page → screenshot + `needs_human`, no retry cleverness.
- [ ] `GET /api/browser/state` and `POST /api/browser/stop` contain no logic beyond service calls (code review).
