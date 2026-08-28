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

- `cryptography` pinned to `48.0.1` in requirements.txt: 49.0.0 removed the
  x86_64 macOS wheel required by Rosetta-based local Python, and a source-linked
  build crashed during Google ID-token certificate verification.
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
- `app/main.py`: 13 routes — chat, webhooks (token-verified), tasks (durable
  request-bound acknowledgement),
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

## Data-source reliability slice (docs/24, 2026-08-26)

- WI-0 was traced independently before cutover; its temporary Drive-id gate was
  superseded in WI-3 by the stronger owner-scoped ACTIVE `source_grant_id`
  authority, shared by HTTP and model-tool ingestion.
- WI-1/2 added closed contracts, five canonical collections, registry-driven
  export/deletion, durable status projections, explicit Drive grants, and honest
  shared-account revocation behavior.
- WI-3/4/5 unified source registration, added orphan reconciliation, deterministic
  provider-event receipts/correlation, and the reduced founder inbox. There is
  no active-session fallback and no heuristic exact match.
- WI-6 puts email, Calendar, and produced-document Drive export behind product
  receipts. Expired PREPARED leases become UNCERTAIN; provider-specific
  reconciliation is required before another effect.
- WI-7 labels retrieval as unconfirmed evidence, projects profile verification
  levels and supersession history, blocks uncited/unsupported auto-apply, and
  provides an owner/session-scoped founder conflict-review surface.
- WI-8 migration is dry-run-first and additive. Deterministic unit/eval cases
  cover duplicates, crash/uncertainty, ownership, poisoning, privacy, parity,
  and rollback planning. No live provider is required for these checks.
- Final local/emulated acceptance: **873 passed** (`tests/unit` +
  `tests/integration`, excluding the documented environmental LibreOffice
  suite), Ruff clean, collection registry 4/4, browser invariants and contrast
  checks green. In-app QA passed at 1280×800 and 390×844: no page-level
  horizontal overflow, reduced inbox is an accessible dialog, Escape restores
  trigger focus, and the mobile drawer stays within 390 px.
- Production cutover completed on 2026-08-26. All 14 declared Firestore
  composite indexes reached READY before migration or Cloud Run deployment.
  The additive migration dry-run and apply shared plan hash
  `44cdc7cf32b49a3e21f85cc8a9f2d397727267da3afa1aaa037840d4614f2987`,
  read no credential values, created no synthetic events, and reported
  `dual_read_parity=true`; the existing dataset required no canonical-row
  creation.
- Cloud Run revision `co-founder-00025-z8f` receives 100% of traffic with
  min-instances 0, max-instances 1, CPU throttling, and in-app-only browsing.
  Both task queues are bounded and the only Scheduler job in any enabled region
  is `deadline-scan-6h`; no discovery schedule, topic, or subscription remains.
  Public health, login, unauthenticated redirect, access-key session, founder
  inbox, pipeline, Firebase email/Google provider configuration, OAuth redirect
  domains, artifact lifecycle, and latest-revision error logs were verified.

## Fixtures

`tests/fixtures/startup_google_programs.html` (149 KB) and
`tony_elumelu_programme.html` (261 KB) snapshotted 2026-08-18 — content
verified real (25 TEF mentions, 101 program/accelerator mentions). The Day-1
guidelines PDF remains `TBD-Day-1` in the workflow YAML — pick and snapshot
when the PDF link is chosen.

## Conversational discovery adapter verification (2026-08-25)

- Pre-Phase-0 `/discover` adapter implemented behind
  `DISCOVER_COMMAND_ENABLED`; production default remains off.
- Transactional founder/opportunity selection uniqueness, legacy-record reuse,
  opaque request IDs, durable worker receipts, transcript persistence,
  prose-only context, manual-button empty-body compatibility, and empty-result
  conversational closure are covered by deterministic tests.
- Full deterministic suite: **390 passed**.
- Static validation: `ruff check .` and `git diff --check` passed.
- All six live ADK eval sets passed against Vertex, with persisted result JSON
  checked by `scripts/check_adk_eval_result.py`: document output 1/1; strict
  safety gates 9/9; idle resume 1/1; persona identity 3/3 (**14/14 cases**).
- The document eval originally named an external programme (`Madica`) absent
  from its supplied state, so the agent correctly refused the unresolved
  entity and the outcome metric failed. The case now asks for the **current
  approved application**, which is the entity its `session_input` actually
  identifies; the successful `produce_document` result and download URL remain
  mandatory.

## Platform convergence Phase 2A local verification (2026-08-28)

- Interactive, workload, and seeded principals now converge on
  `ActorPrincipal`; deployed worker routes accept workload OIDC only, while
  seeded identities are local-only.
- Public product mutations have `/api/v1` command receipts with normalized
  request hashes, deterministic in-flight duplicate responses, and conflict on
  changed reuse. Deployed legacy product mutations return `410`; local
  compatibility remains available for deterministic fixtures.
- Connector credentials are workspace-scoped opaque secret references. The
  legacy global-token migration deliberately reads and copies no credential
  value: it atomically marks affected connections `REAUTH_REQUIRED` and records
  a receipt so verified OAuth consent establishes the workspace grant.
- The tenancy manifest now includes opportunity, application, ingestion,
  artifact, feedback, evidence, approval/action/event, inbox, connection,
  source-grant, wake, and portal-receipt records. Ownerless historical rows
  require an explicit operator workspace override, which is recorded in the
  per-row migration receipt; an override conflicting with a durable owner is
  refused.
- Agent tools no longer default durable scope to a process-global founder.
  Invocation/session identity supplies workspace scope, and missing authority
  fails closed before durable reads or effects.
- Adversarial tests cover foreign application, approval, opportunity, provider
  binding, connector credential, and missing-workspace worker cases. The full
  deterministic unit suite passed locally: **1,055 passed, 2 skipped** before
  the final tool-scope tightening; focused post-tightening suites remained
  green. Cloud index readiness, backup/restore, migration dry-run/apply, and
  staging identity tests remain deployment gates rather than local claims.

## Platform convergence Phases 0–7 implementation verification (2026-08-28)

- Phases 0, 1, 2A, 3, and 2B now share the generic transactional runtime,
  workspace principal, command/event receipts, consequence protocol, wake
  delivery, timer checkpoints, queue identities, and replayable SSE projection.
- Phase 6 adds the reviewed `investor_outreach:v1` vertical: source-cited
  research, deterministic ranking, immutable recipient-bound drafts, exact
  same-founder approval, one provider send receipt, provider-thread reply
  correlation, evidence-bounded meeting brief, cold-start recovery, run
  controls, and founder-visible uncertainty. Disabling it leaves grants and the
  runtime healthy.
- Phase 4A adds immutable per-key workspace facts and actor-private preference
  facts, compatibility dual-write/read cutover, source lineage, untruncated
  supersession, blob-aware deletion, durable deletion jobs, manifests, and
  restore tombstones. Phase 4B adds a server-owned Firestore keyword fallback
  implementing the portable memory adapter, ADK runner binding, pre-ranking
  tenant/scope/sensitivity filters, untrusted-source delimiters, explicit
  backend failure, source/workspace deletion, and an offline release metric.
  Persistent memory remains disabled by default until the deployed evaluation
  and privacy/deletion review pass.
- Phase 5 validates exact code-reviewed templates against versioned capability
  descriptors and budgets, limits agent context to the role/step intersection,
  fences disabled capabilities, pauses dependent unexecuted runs, and requires
  reapproval when replanning changes an effect subject. Dynamic/model-authored
  plan composition remains deliberately disabled.
- Phase 7 code supplies numeric SLO/error-budget contracts, content-free stuck
  run/wait/action/approval/delivery inspection, operator inbox records,
  priority/budget load shedding, recovery-drill receipts with RPO/RTO and
  deletion/action/checksum proof, and versioned governance reports.
- Run-creating public commands now close transaction boundary T8: command
  receipt, dispatch outbox, immutable plan, initial run event/projection, and
  discovery/investor domain root commit together. A closed dispatcher uses a
  deterministic task name, records `DISPATCHED` only after enqueue, and a
  one-minute timers-lane recovery job heals process death after commit.
- The browser trust zone is extracted in code and deployment configuration.
  `co-founder` uses a typed audience-bound OIDC gateway and scales independently;
  private `co-founder-browser-worker` alone owns Playwright objects at
  concurrency one. Worker notifications enter the durable workspace projection
  stream, so cross-process UI recovery does not depend on an in-memory event
  hub. Local development intentionally retains the same-process adapter.
- Local release evidence after these changes: **1,105 passed, 2 skipped** for
  the complete pytest suite; Ruff, `git diff --check`, JavaScript syntax,
  contrast, browser invariants, deployment shell syntax, Firestore-index JSON,
  index/deployment contracts, and collection lifecycle coverage passed.
- A live strict ADK judge run reached the remote model call but returned no
  result for more than two minutes and was cancelled cleanly. This is recorded
  as an external evaluation limitation, not an architecture or unit-test
  failure. Staging queue/load chaos, browser-worker identity/traffic
  verification, migration dry-run/apply, backup restore, regional recovery, and measured
  RPO/RTO remain required deployment evidence; local code must not be described
  as proving those production properties.

## Phase 7 operations hardening (2026-08-28)

- Verified checkpoint `46073de` contains the Phase 0–7 convergence baseline.
- Phase 7 operations no longer stop at 1,000 records: the durable-store query
  contract supports document-ID pagination and stuck run/wait/action/approval/
  delivery scans exhaust every page.
- Added measured, idempotent SLO observations and fail-closed error-budget
  reports; closed queue depth/age capacity policy; atomic workspace/model/
  provider cost-budget receipts; durable canary promotion/rollback decisions;
  strict recovery, migration and chaos evidence schemas; and governance reports
  that include policy, plan, capability, model, memory, rollout and budget
  versions.
- Added content-free operator and cloud-audit CLIs, an explicit dry-run/apply
  reliability configurator, and the binding Phase 7 runbook in docs/35.
- `scripts/check_phase7.sh`: **49 passed**. Complete deterministic suite:
  **1,111 passed, 2 skipped**. Ruff, shell syntax, JSON validation and
  `git diff --check` passed.
- The read-only cloud audit was attempted against `co-founder-506001` and
  failed closed before reading resources because the configured gcloud account
  and ADC both require interactive reauthentication. No cloud state changed.
  Firestore/SQL/GCS recovery configuration, monitoring policies, staging load/
  chaos, migration rollback, cross-region restore and measured RPO/RTO remain
  external evidence gates; they are not represented as completed by local
  tests.
