# 01 — Architecture

## System overview

```
                        ┌────────────────────────────────────────────────────┐
                        │                 Cloud Run: co-founder          │
                        │                                                     │
   Founder ──HTTPS──►   │  FastAPI (ADK get_fast_api_app + custom routes)     │
                        │    ├─ /              static demo UI                 │
                        │    ├─ /wake          chat entry                     │
                        │    ├─ /webhooks/*    founder_reply, portal_event,   │
                        │    │                 deadline                       │
                        │    ├─ /tasks/*       discover, deadline_scan        │
                        │    └─ /api/*         pipeline, feedback, approvals  │
                        │                                                     │
                        │  Runner (ONE, built at startup)                     │
                        │    └─ App(co_founder)                          │
                        │        ├─ orchestrator (root agent)                 │
                        │        ├─ scout_agent                               │
                        │        ├─ matchmaker_agent                          │
                        │        ├─ interviewer_agent                         │
                        │        ├─ drafter_agent                             │
                        │        ├─ form_filler_agent ──► Playwright          │
                        │        └─ distiller_agent                           │
                        └──────┬───────────┬────────────┬───────────┬────────┘
                               │           │            │           │
              ┌────────────────▼─┐   ┌─────▼─────┐  ┌───▼────┐  ┌───▼─────────┐
              │ Cloud SQL (PG)   │   │ Firestore │  │  GCS   │  │ Secret Mgr  │
              │ ADK sessions     │   │ pipeline  │  │artifacts│  │ connector + │
              │                  │   │           │  │         │  │ action keys │
              └──────────────────┘   └───────────┘  └────────┘  └─────────────┘
                               ▲           ▲
        Cloud Scheduler ──► Pub/Sub ──────┘  (deadline-tick only)
        Founder command ──► Cloud Tasks ──► /tasks/discover
        Signed webhook ──► Cloud Tasks ──► /tasks/portal_wake (durable agent wake)
```

## Repository layout

Build exactly this tree at repo root:

```
co-founder/
├── agents/
│   └── co_founder/
│       ├── __init__.py            # exposes root_agent (and app) for adk web / CLI
│       ├── agent.py               # App wiring: root agent + sub_agents + compaction
│       ├── instructions.py        # all instruction templates (state-injected)
│       ├── state_schema.py        # state constants + checklist keys
│       ├── callbacks.py           # before_agent_callback: initialize state
│       ├── workflow.py            # WorkflowDefinition loader (YAML → dataclass)
│       ├── sub_agents/
│       │   ├── __init__.py
│       │   ├── scout.py
│       │   ├── matchmaker.py
│       │   ├── interviewer.py
│       │   ├── drafter.py
│       │   ├── form_filler.py
│       │   └── distiller.py
│       └── tools/
│           ├── __init__.py
│           ├── discovery.py       # fetch_source, save_opportunity, dedupe_check
│           ├── pipeline.py        # shortlist, archive, advance, get_pipeline
│           ├── profile.py         # get_profile, record_answer, apply_profile_update
│           ├── drafting.py        # save_draft_section, get_section_feedback
│           ├── feedback.py        # record_feedback (+ triggers distill)
│           ├── browser.py         # Playwright wrapper: open/inspect/fill/verify/submit
│           ├── browse.py          # read-only browsing: open/read/act/close (18)
│           └── followup.py        # schedule_followup, record_status
├── app/
│   ├── main.py                    # FastAPI: ADK app + custom routes, single Runner
│   ├── resume_handler.py          # hydrate session + run_async(state_delta=...)
│   ├── deps.py                    # shared singletons (firestore, secrets, runner)
│   └── static/
│       └── index.html             # demo UI (no framework)
├── services/
│   ├── firestore.py               # client + collection accessors
│   ├── memory.py                  # FirestoreMemoryService(BaseMemoryService)
│   ├── secrets.py                 # Secret Manager fetch (cached)
│   ├── browser_service.py         # shared Chromium, run registry, action/network policy (18)
│   ├── browser_runtime.py         # process/context ownership, watchdogs, quotas (22)
│   ├── browser_events.py          # snapshot-first in-app browser event projection (22)
│   └── storage.py                 # GCS artifact helpers
├── workflows/
│   └── grant_applications.yaml    # workflow instance #1 (declarative definition)
├── scripts/
│   ├── setup.sh                   # idempotent env setup
│   ├── deploy.sh                  # cloud deploy
│   └── seed_demo.py               # seed profile (under user / eval_founder / demo
│                                  # founder_id — see 02), demo opportunities, seeded rejection
├── tests/
│   ├── eval/
│   │   ├── evalsets/*.json
│   │   └── eval_config.json
│   └── integration/
│       └── test_adaptation_loop.py
├── docs/                          # these specifications
├── .env.example
├── requirements.txt
├── Dockerfile
└── README.md                      # spin-up instructions (submission requirement)
```

## Tech pins

| Choice | Value | Notes |
|---|---|---|
| Language | Python ≥ 3.11 | enforced in setup.sh |
| Agent framework | `google-adk==2.8.0` | `DatabaseSessionService` is the canonical transcript; `EventsCompactionConfig` bounds text history, while Gemini Live uses `RunConfig.session_resumption` plus context-window compression. Durable `state_delta` remains the workflow-resumption authority (see 03). |
| Model | env `ADK_MODEL`, default `gemini-3.6-flash` | rules require Gemini 3.5+; one env var, per-agent override allowed. `REASONING_MODEL` (orchestrator + drafter) also defaults to `gemini-3.6-flash` |
| Model access | Vertex AI with Application Default Credentials | never API keys — the Gemma Evidence Checker (20) calls managed Vertex MaaS under the project's Google Cloud credentials and data-governance controls |
| Server | FastAPI + uvicorn via `google.adk.cli.fast_api.get_fast_api_app` | custom routes added on top |
| Sessions (local) | SQLite: `sqlite+aiosqlite:///sessions.db` | |
| Sessions (prod) | Cloud SQL Postgres via `DatabaseSessionService` | same API, swap URI |
| Pipeline store | Firestore (native mode) | see 02 |
| Memory service | custom `FirestoreMemoryService` | see 06 |
| Artifacts | local `file://` dev, `gs://` bucket prod | ADK artifact service URI |
| Durable task dispatch | Cloud Tasks queues `co-founder-events`, `co-founder-browser-expiry` | `co-founder-events` carries portal-event wakes that must outlive the webhook request; generation-safe browser expiry is isolated on its own queue so resource cleanup cannot head-of-line block behind an agent wake (22). 21 extends the event primitive into bounded workflow-step/timer dispatch. |
| Browser automation | Playwright-managed Chromium; one single-flight process, literal `headless=True`, no headed/attach/system-profile mode | see 09, 18, 22 |
| Deploy | Cloud Run ×2 (public app, private browser worker), scale-to-zero | see 13 |

## Environment variables (`.env.example` must list all)

| Var | Example | Purpose |
|---|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` | `True` | force Vertex, no API keys |
| `GOOGLE_CLOUD_PROJECT` | `my-project` | |
| `GOOGLE_CLOUD_REGION` | `us-central1` | |
| `GOOGLE_CLOUD_LOCATION` | `global` | Vertex location |
| `ADK_MODEL` | `gemini-3.6-flash` | default dialogue/step model; role-specific seams may override it |
| `REASONING_MODEL` | `gemini-3.6-flash` | orchestrator + drafter seam; currently the same measured model as `ADK_MODEL` |
| `LITE_MODEL` | `gemini-3.5-flash-lite` | high-volume extraction/classification tier |
| `SESSION_SERVICE_URI` | `sqlite+aiosqlite:///sessions.db` | prod: Cloud SQL asyncpg URI |
| `ARTIFACT_SERVICE_URI` | `file://./artifacts` | prod: `gs://<bucket>` |
| `LIBREOFFICE_CONVERSION_ENABLED` | `false` on native macOS; `true` in Docker | prevents the macOS GUI app bundle from being launched by the local server; production uses the pinned headless Linux package |
| `FIRESTORE_DATABASE` | `(default)` | pipeline store |
| `WORKFLOW_FILE` | `workflows/grant_applications.yaml` | active workflow definition |
| `AGENT_BASE_URL` | `http://127.0.0.1:8090` | canonical callback and task origin |
| `TASKS_INVOKER_SA` | `scheduler-invoker@project.iam.gserviceaccount.com` | OIDC identity on authenticated Cloud Tasks HTTP delivery |
| `DISCOVER_COMMAND_ENABLED` | `false` | pre-Phase-0 `/discover` compatibility adapter; production remains off until its tests and full eval gate pass |
| Browser execution | always headless | Portal pages are shown only in the in-app Browser panel |
| `BROWSE_OPEN_WEB` | _(unset)_ | dev-only: allow non-allowlisted public hosts (18); production must leave unset/false — fail-closed |
| `BROWSE_ALLOWED_DOMAINS` | _(empty)_ | comma-separated host patterns; with `BROWSE_OPEN_WEB` unset, empty = deny-all (18) |
| `PORTAL_ALLOWED_HOSTS` | _(empty)_ | adapter-declared identity-provider/verification origins a credential-typing context may reach besides the portal itself (`*.` prefixes match subdomains, 22 §per-kind matrix). Everything else is refused, so a hostile portal page cannot beacon a typed credential to a third-party host |
| `BROWSE_BLOCKED_DOMAINS` | _(empty)_ | deny wins over the allowlist (18) |
| `APPROVAL_TTL_MINUTES` | `30` | approval token lifetime |
| `GEMMA_EVIDENCE_CHECK_ENABLED` | `false` | enable 20 only after its rollout eval passes |
| `GEMMA_EVIDENCE_BACKEND` | `vertex` | only `vertex` is accepted; any other value is refused (20) |
| `GEMMA_EVIDENCE_VERTEX_MODEL` | `gemma-4-26b-a4b-it-maas` | Vertex MaaS id — distinct from the Gemini API's (20) |
| `GEMMA_EVIDENCE_LEASE_SECONDS` | `90` | execution-lease bound; a `PREPARED` row older than this is reclaimable (20) |

## Workflow engine (build the loader, one instance)

Core code is domain-generic. Domain specifics live in `workflows/grant_applications.yaml`.

**Naming rule (enforce):** core modules use `Opportunity`, `Application`, `PipelineStore`,
`DraftSection`, `ChecklistItem`, `ApprovalGate`. The strings "grant", "accelerator"
appear only in the YAML, seed data, and UI copy.

```yaml
# workflows/grant_applications.yaml
workflow_id: grant_applications
display_name: "Funding & Program Applications"
entity_schema:                      # what the Scout extracts
  name: str
  source_url: str
  award: str
  deadline: date|null
  eligibility: list[str]
  application_url: str
  required_materials: list[str]
  description: str
states: [DISCOVERED, SHORTLISTED, ARCHIVED]
application_states: [INTERVIEWING, DRAFTING, AWAITING_REVIEW, APPROVED,
                     FORM_FILLING, AWAITING_SUBMIT_APPROVAL, SUBMITTED,
                     FOLLOW_UP, CLOSED]
sources:                            # discovery lanes (see 08)
  - type: search                    # Lane 1: profile-driven Google Search grounding
    queries_from_profile: true
    max_results_per_query: 10
  - type: web_page                  # Lane 2: REAL program listings (finalize + verify Day 1)
    url: "https://startup.google.com/programs/"
  - type: web_page
    url: "https://www.tonyelumelufoundation.org/entrepreneurship-programme"
  - type: pdf                       # one REAL guidelines PDF — pick and verify Day 1
    url: "TBD-Day-1"
# tests/fixtures/ holds snapshots of every configured source: CI and evals
# replay fixtures (deterministic); only the live demo fetches real URLs.
fit_criteria:                       # matchmaker inputs
  fields: [stage, sector, geography, award_size]
approval_policy:
  submit_requires: founder_approval_token
  token_ttl_minutes: 30
```

`workflow.py` exposes `load_workflow(path) -> WorkflowDefinition` (frozen dataclass).
Agents receive workflow values via state/instruction injection; they never read YAML
directly. Acceptance: swapping the YAML for a second workflow file requires zero code
changes to load it (only instance #1 is built, but the loader must be real).

## Conventions (binding)

- **Errors as data:** every tool returns `dict`; on failure `{"error": true, "message": ...}`.
  Never let an exception cross the tool boundary into the model.
- **State access:** tools read/write session state only via `tool_context.state`.
  Prefix discipline per ADK: `temp:` (invocation only), plain (session),
  `user:` (cross-session per user), `app:` (cross-user).
- **Timestamps:** ISO-8601 UTC strings everywhere.
- **IDs:** `uuid4().hex` for entities; idempotency keys are separate (see 05).
- **Logging:** one structured JSON line per tool call: tool name, args hash, result
  status, latency_ms. No PII, no secrets (see 12).
- **One Runner per surface:** built once at startup, reused across requests (07). Never per-request.
- **Agents-dir hygiene:** every folder under `agents/` must be a valid agent
  package or `adk web` fails to load the entire list (reference-lab warning).
  Never move `services/`, `app/`, or stray `memory/` /
  `artifacts/` folders under `agents/`.

## Data flows (narratives — implement these end-to-end)

**Discovery sweep (founder-invoked):** `/discover` command → durable
Cloud Task → `POST /tasks/discover` → scout fetches each configured source → extracts `Opportunity`
records → dedupe → Firestore `opportunities` (state DISCOVERED) → matchmaker scores →
SHORTLISTED or ARCHIVED(with reason) → founder sees board next visit. There is no
daily discovery schedule or polling loop.

**Application flow (interactive):** founder picks a SHORTLISTED opportunity in UI →
session `current_step=INTERVIEWING` → interviewer asks gap questions →
`DRAFTING` → drafter produces sections → `AWAITING_REVIEW` → founder approves/edits/
rejects per section (feedback → distiller → profile update) → all approved →
`APPROVED` → founder clicks "Fill form" → `FORM_FILLING` → form-filler pre-fills
portal → fill report → `AWAITING_SUBMIT_APPROVAL` → founder approves in UI →
single-use token → `SUBMITTED` → confirmation → `FOLLOW_UP`.

**Wake-up flow (dormancy):** external event (portal webhook, deadline tick, founder
reply) → webhook route → `resume_handler` hydrates persisted session →
`runner.run_async(..., state_delta={...})` → state transition applied before next
inference → agent continues with full context. No chat-history replay.

## Acceptance checks

- [ ] Repo tree matches this document.
- [ ] `python -c "from agents.co_founder.agent import app"` succeeds.
- [ ] `adk web agents --port 8000` shows `co_founder` with 5 sub-agents in the graph view (scout, matchmaker, interviewer, drafter, form_filler; the distiller runs standalone via `/tasks/distill` — see 04/07).
- [ ] `load_workflow("workflows/grant_applications.yaml")` returns a populated dataclass; malformed YAML fails loudly with the offending key.
- [ ] `.env.example` contains every var in the table.
