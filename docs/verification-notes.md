# Verification notes — ADK 2.7.1 (installed 2026-08-18)

Day-2 first-hour verify pass, run early on Day 1. Environment: macOS (x86_64),
Python 3.11.9, `google-adk==2.7.1`.

## Verified against the installed SDK

| Item | Result |
|---|---|
| `google.adk` version | **2.7.1** (spec pinned ≥2.6 ✓) |
| `Runner.run_async` signature | `(self, user_id, session_id, invocation_id, new_message, state_delta, run_config, yield_user_message)` — **`state_delta` present ✓** (top open question, closed) |
| `App` | `google.adk.apps.App` ✓ |
| `EventsCompactionConfig` | `google.adk.apps.app.EventsCompactionConfig` ✓ (emits `[EXPERIMENTAL]` warning at construction — expected per docs/04) |
| `Gemini` model class | `google.adk.models.Gemini` ✓ |
| `ToolContext` | `google.adk.tools.ToolContext` ✓ |
| `CallbackContext` | `google.adk.agents.callback_context.CallbackContext` ✓ |
| `Agent` fields | `include_contents` ✓, `output_key` ✓, `before_tool_callback` ✓ |
| `ResumabilityConfig` | importable from `google.adk.apps` — **unused by design** (docs/03 deviation) |
| `GoogleSearchTool` | importable (`google.adk.tools.google_search_tool`) ✓ — **live grounded query still pending ADC login** |

## Day-1 acceptance results

- `load_workflow("workflows/grant_applications.yaml")` → populated dataclass ✓
- Malformed workflow YAML fails loudly, naming missing keys ✓
- `from agents.co_founder.agent import app` → App `co_founder`, root
  agent + 5 sub-agents in transfer graph (scout, matchmaker, interviewer,
  drafter, form_filler), distiller standalone with `include_contents="none"` /
  `output_key="distillation"`, form-filler fence callback wired ✓
- `.env.example` covers every var in docs/01 ✓

## Environment deviations recorded

- `cryptography` pinned `<50` in requirements.txt: 50.0.0 builds from source
  (Rust toolchain absent); 49.0.0 ships a wheel.
- **Application Default Credentials not yet present** on this machine. Required
  user action: `gcloud auth application-default login`. Blocks: the model
  check in setup.sh (7/7), the live GoogleSearchTool spike, and any Gemini call.

## Day-1 spike result (search lane)

`scripts/spike_search.py` first attempt 2026-08-18: blocked on ADC.
**Re-run 2026-08-19 against project `co-founder-506001`: PASS** — grounded
search works on Vertex AI + gemini-3.5-flash, returning real, current programs
(Delaware EDGE Grant et al.). No fallback needed; Lane 1 ships on
`GoogleSearchTool`.

## Day-2 progress (2026-08-18, creds-free slice)

- `tests/unit/test_state_machine.py`: **19/19 pass** — full transition table,
  safety gates G1–G3 (no drafting from INTERVIEWING, SUBMITTED only from
  AWAITING_SUBMIT_APPROVAL, FORM_FILLING only from APPROVED), no backward
  leaps, CLOSED terminal.
- `app/main.py` two-surface wiring verified: chat app + webhook Runner over one
  `DatabaseSessionService`; `/wake`, `/session/new`, `/healthz` live; healthz 200.
- **Gotcha found:** ADK 2.7 rejects relative `file://` artifact URIs
  ("must reference the local filesystem"). Fixed with absolute-path
  normalization in `app/main.py`; `.env.example` updated.
- **Warning noted (not blocking):** ADK suggests `context_cache_config` on the
  App for multi-agent transfer prompt caching. Candidate perf hardening for
  Day 9–10; not in specs — flag for review before adding.
- Kill/restart live resume test + skeleton Cloud Run/Cloud SQL deploy: **gated
  on ADC login** (model calls + billing APIs).

## Implementation progress (2026-08-18/19, single push)

**Code complete through the Day 9 scope — all creds-free verification green:**

- Service layer: `storage`, `profile_service` (retrieval ranking, ingestion
  propose→confirm gate), `pipeline_service` (urgency tiers, guarded
  transitions, derived idempotency keys), `approval_service` (server-side gate,
  token never leaves Firestore), `feedback_service` (verbatim capture +
  **synchronous** distillation hook), `distill_service` (inline runner),
  `discovery_service` (fetch + JS-shell fallback, schema validation, sweep,
  deadline sentinel), `browser_service` (Playwright), `recon_service` (vision
  loop: allowlist, 20-step budget + 90s time-box, per-step evidence),
  `voice_service`.
- All 24 tools implemented (thin wrappers; G1/G2/G3 in code).
- `app/main.py`: 13 routes — chat, webhooks (token-verified), tasks (fast-ack),
  api (pipeline, applications, feedback, approvals resolve, voice-note, ingest).
- `mock_portal/`: 16-field form, deliberate friction (file upload, dynamic
  select), `?v=2` renamed-fields mode, idempotent submit honoring
  `Idempotency-Key`, signed webhook, `/admin/reset`, `/admin/ping-agent`.
- UI `app/static/index.html`: board, chat, review controls (reject requires a
  reason), approval modal, audit tail, mic + upload buttons.
- Evals: 4 golden sets in the verified reference envelope + eval_config.
- Tests: **38/38 pass** (state machine, urgency, derived keys, approval
  lifecycle, schema validation, ingestion gate, retrieval ranking, vision
  allowlist, adaptation loop integration, approve-all → APPROVED + key minted).
- `scripts/e2e_portal_check.py`: **ALL GREEN** against the live mock portal —
  login, 16-field inspect, 14-field fill, idempotent retry (same confirmation
  `MP-ED75`), staleness signature change on `?v=2`. No model creds needed.
- `scripts/deploy.sh` (idempotent full deploy), `scripts/seed_demo.py`
  (3-user-id seeding + money-shot voice rule), CI workflow.

**Bugs found by testing (fixed):** relative `file://` artifact URIs rejected by
ADK 2.7; login landing-page re-navigation; `select-one` vs `select` field-type
detection; HTML5 `required` blocking founder-owned fields; sync-vs-async mock
mismatches in tests.

**Still gated on `gcloud auth application-default login`:** live Gemini calls
(distiller agent, extraction, search grounding, voice transcription, vision
recon model fn), the adk web graph check, golden evals, Cloud deploy.

## Final layer (2026-08-19)

- `services/gemini_backends.py` — all five production backends written and
  wired at startup (`search_fn`, `extract_fn`, `doc_extract_fn`,
  `recon_model_fn`, `transcribe_fn`), with tolerant JSON parsing. The genai
  client authenticates lazily, so the app boots with or without ADC; without
  creds the service layer returns its documented error dicts.
- `context_cache_config` wired on the App (per-agent prompt caching on
  transfer); `get_section_feedback` implemented. **38/38 tests green.**
- Submission assets: `docs/architecture-diagram.md` (Mermaid),
  `docs/demo-script.md`, `docs/submission-draft.md`.

**Implementation is now code-complete end to end.** Remaining items are all
runtime actions: ADC login → live verification (evals, adk web graph, resume
proof) → `deploy.sh` → video → submission.

## Fixtures

`tests/fixtures/startup_google_programs.html` (149 KB) and
`tony_elumelu_programme.html` (261 KB) snapshotted 2026-08-18 — content
verified real (25 TEF mentions, 101 program/accelerator mentions). The Day-1
guidelines PDF remains `TBD-Day-1` in the workflow YAML — pick and snapshot
when the PDF link is chosen.
