# 07 — Server, Webhooks & Resume

FastAPI app that serves the agent over HTTP, the demo UI, webhook endpoints for
external events, and task endpoints for Scheduler/Pub/Sub. Built once Runner,
durable sessions, event-driven resume via `state_delta`.

## Startup wiring (`app/main.py`)

Follows the reference repo (`new-hire-onboarding/app/fast_api_app.py`) exactly:
**two entry surfaces, one shared session store.**

```python
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.adk.sessions.database_session_service import DatabaseSessionService
from app.app_utils.telemetry import setup_telemetry

setup_telemetry()                                    # see 13; OTEL → Cloud Trace
session_service_uri = os.environ["SESSION_SERVICE_URI"]

# Surface 1: founder chat — ADK's app (its internal Runner shares the same URI)
app: FastAPI = get_fast_api_app(
    agents_dir="agents", web=False,                 # adk web is dev-only, never in prod
    session_service_uri=session_service_uri,
    artifact_service_uri=os.environ["ARTIFACT_SERVICE_URI"],
    otel_to_cloud=True,
)

# Surface 2: webhooks/tasks — dedicated Runner over the SAME session store
db_session_service = DatabaseSessionService(db_url=session_service_uri)
webhook_runner = Runner(app=agent_app,               # the App object, not agent=
                        session_service=db_session_service)
resume_handler = ResumeHandler(runner=webhook_runner)

# Surface 3: the distiller — its own tiny App (the Runner(app=...) rule holds
# everywhere), invoked INLINE from the feedback path via the service layer
distill_app = App(name="co_founder_distill", root_agent=distiller_agent)
distill_runner = Runner(app=distill_app, session_service=db_session_service)
```

Rules (corrected against the reference implementation):
- The invariant is **one shared session store**, not one Runner. Chat and webhook
  surfaces each get a startup-built Runner pointing at the same
  `DatabaseSessionService`. A Runner built **per request** = amnesia; that is the
  only forbidden shape.
- Pass the `App` object to `Runner(app=...)`, not `agent=` + `app_name=`.
- Why custom wake routes and not ADK's built-in `trigger_sources=["pubsub"]` for
  resume traffic: the built-in trigger **mints a brand-new session per message**
  (`session_id = uuid4()`), which would start a duplicate workflow instead of
  resuming the parked one. Our `/webhooks/*` and approval/feedback routes look up
  the founder's existing session. Founder-invoked discovery uses a separate
  system scoring session and reports into the originating founder session.
- Log at startup which agent, session store, memory store, and artifact store
  each surface is wired to.

## Routes

| Method & path | Purpose | Body → Response |
|---|---|---|
| `GET /` | serve `app/static/index.html` | UI |
| `POST /wake` | founder chat entry | `{message, session_id?, client_request_id?}` → agent text parts as list. Creates session if missing. With `DISCOVER_COMMAND_ENABLED=true`, exact `/discover` is parsed before model invocation. |
| `POST /session/new` | fresh session id | `{founder_id}` → `{session_id}` |
| `POST /webhooks/founder_reply` | async founder answer (e.g. from email reply later; v1: UI posts here) | `WebhookPayload` |
| `POST /webhooks/portal_event` | mock/real portal events (submission confirmed, form changed) | `PortalEvent` |
| `POST /webhooks/deadline` | Pub/Sub push: validate message ID, durably enqueue one deadline task, then fast-ack | Pub/Sub envelope |
| `POST /api/discovery-requests` | founder-facing discovery boundary (23 §6.2) | `{session_id, client_request_id, context?}` → `202 {status, request_id, discovery_request_id, resource_id, session_id, duplicate}`; durably persists receipt + resource + session link, then dispatches IDs only |
| `POST /tasks/discover` | **internal** discovery worker (Cloud Tasks delivery); never scheduled, never a UI shortcut | `{discovery_request_id, founder_id}`; the worker loads context/authority from the receipt. Legacy `{context?, client_request_id?, session_id?}` remains accepted for in-flight tasks |
| `GET /api/search` | typed global search over conversations and resources (23 §7) | `?q&types&session_id&cursor&limit` → grouped-by-resource results |
| `GET /api/sessions/{session_id}/resources` | one conversation's resource occurrences (23) | → bounded occurrence list |
| `DELETE /api/sessions/{session_id}` | explicitly confirmed session deletion (23 §9) | `{confirm:true}` → deletes the transcript, tombstones its links, purges exclusive session-retained files, and reports retained/shared items plus cleanup errors |
| `POST /tasks/deadline_scan` | Cloud Tasks worker: recompute urgency on all open items and nudge for newly critical items | `{message_id?}` or empty for an authenticated manual run |
| `POST /tasks/distill` | run the distiller on one feedback record | `{feedback_id}` → runs `distill_runner` (below) |
| `POST /tasks/browser_expire` | generation-safe browser inactivity expiry (22), Cloud Tasks/OIDC only | `{run_id, lease_generation}` → close only when owner/generation/deadline still match; stale delivery is a success no-op |
| `GET /api/pipeline` | UI board data | grouped opportunities + in-flight applications |
| `POST /api/feedback` | UI review controls | `{session_id, section_id, type, reason?, edited_text?}` → runs `record_feedback` path, resumes session |
| `POST /api/approvals/{approval_id}/resolve` | founder grants/denies at the gate | `{decision: "grant"|"deny"}` → mints/consumes token flow (see 12), resumes session |
| `POST /api/voice-note` | voice-note upload (multipart) | stores artifact, transcribes via the service-layer function, forwards extracted intent through the normal resume path (tool `submit_voice_note` wraps the same service function) |
| `GET /api/applications/{id}?session_id=` | session-bound application detail for UI (drafts, fill report, audit) | unknown session or another founder's application → 404 |
| `GET /api/artifacts/{name}/preview` | inline artifact for the UI (browser pageshots, fill screenshots — 18) | founder/session-checked; streams with correct MIME |
| `GET /api/artifacts/{name}/download` | document downloads (15) | `Content-Disposition: attachment`, correct OOXML MIME |
| `GET /api/browser/state?session_id=` | Browser panel snapshot (18) | → `get_browser_state` dict; unknown/non-founder session → 404 |
| `GET /api/browser/events?session_id=` | snapshot-first in-app browser observation stream (22) | authenticated `text/event-stream`; durable snapshot first, monotonic run/frame events after; unknown/non-founder session → 404 |
| `POST /api/browser/stop` | founder stops the foreground browse or fill run (18/22) | `{session_id, run_id?}`, requires `Content-Type: application/json` → idempotent `{status, run_id?, kind?, already_closed?}`; audited as `founder:<id>` |

```python
class WebhookPayload(BaseModel):
    user_id: str
    session_id: str
    note: str = ""           # short wake notice

class PortalEvent(BaseModel):
    user_id: str
    session_id: str
    application_id: str
    kind: str                # submission_confirmed | form_changed | result_posted
    confirmation_id: str = ""
    detail: str = ""
```

## Resume handler (`app/resume_handler.py`)

Pattern from the reference architecture — hydrate, transition, wake, in that order:

```python
class ResumeHandler:
    def __init__(self, runner: Runner): self.runner = runner

    async def wake(self, *, user_id: str, session_id: str,
                   notice: str, state_delta: dict) -> None:
        """Hydrate persisted session, apply transition BEFORE inference, resume."""
        async for event in self.runner.run_async(
            user_id=user_id, session_id=session_id,
            new_message=types.Content(
                role="user",
                parts=[types.Part.from_text(text=notice)]),
            state_delta=state_delta,
        ):
            logger.info(json.dumps({"severity": "INFO",
                "message": f"wake event: {event}",
                "session_id": session_id}))
```

Wake-notice wording: short, factual, system-flavored —
`"Resume: portal confirmed submission C-1042."`, `"Resume: founder approved the
submission gate."` Never fabricate user intent.

Event → state_delta mapping (implements 03 §dormancy):

| Route | notice | state_delta |
|---|---|---|
| `portal_event` kind=submission_confirmed | `"Resume: portal confirmed submission {confirmation_id}."` | `{"current_step": "FOLLOW_UP", "pending_signals": []}` |
| `portal_event` kind=result_posted | `"Resume: program posted a result."` | `{"current_step": "CLOSED", "pending_signals": []}` |
| `approvals.resolve` decision=grant | `"Resume: founder approved submission."` | `{"pending_signals": []}` (step stays AWAITING_SUBMIT_APPROVAL until submit succeeds) |
| `api/feedback` | `"Resume: founder reviewed a section."` | `{"pending_signals": []}` |
| `tasks/deadline_scan` | `"Resume: deadline tick; refresh urgency."` | `{}` (no forced transition) |

## Task endpoints

`POST /tasks/discover` accepts direct founder-triggered work or an authenticated
Cloud Task from the `/discover` adapter; it is never a Pub/Sub or Scheduler
target. `POST /tasks/deadline_scan` handles the scheduled deadline wake. Discovery
uses a **system-owned** scoring session (`user_id="system"`); dormancy remains for
founder workflows. `POST /tasks/distill`
is a **direct-HTTP admin/retry route only** — the interactive distill path is
synchronous (inline, awaited, from `/api/feedback` via the service layer), so
the money-shot draft never races a queue. No distill Pub/Sub topic exists.

**Durable acknowledgement rule:** Cloud Task workers acknowledge only after
request-bound work completes, so a crash produces a non-2xx response and bounded
redelivery. Pub/Sub webhooks never run long work: deadline and portal events first
enqueue an authenticated Cloud Task, keyed by the upstream event ID, and
acknowledge only after that durable enqueue.
Redelivery is at-least-once: every task remains idempotent (a durable request
receipt plus `dedupe_check` for discovery; distiller skips already-`distilled` feedback).

**Competition `/discover` adapter:** when its flag is enabled, `/wake` recognizes
only the exact leading token `/discover` with optional prose. It appends the
founder turn and static acknowledgment to the ADK session without invoking the
chat Runner, then dispatches `/tasks/discover` durably on Cloud Run (inline and
awaited in local development). Unknown slash commands are static refusals.
Context is normalized and capped at 500 characters; `@attachment` references
are refused because task-scoped attachment authority is a Phase-2 capability.
The chat client mints an opaque `client_request_id` per submission and reuses it
only for transport retry. Both the conversational `/discover` adapter and the
public compatibility endpoint converge on the same durable request service
(`_accept_discovery_request` → `_dispatch_discovery`); the completion notice
targets the receipt's `origin_session_id`, never the most recently touched
session (23 §6.2). Worker receipts enforce retry idempotency; message
content is never used as an identity. Typed text during a live voice call still
travels over the Live websocket and cannot launch this adapter.

**`someone_is_there()` principle:** whether a human is present is a property of
the REQUEST, not the process. Chat routes (`/wake`) have a person; task/webhook
routes do not. Agents running in system sessions must **never** stop to ask a
question — they act within their scope (score, archive, distill) or record a note
for the founder. A clock can answer a clock; only a person can answer a question.

**Distiller surface:** the distiller runs through its own startup-built runner
(`distill_runner = Runner(app=distill_app, session_service=db_session_service)`,
a tiny second `App` wrapping the distiller — the `Runner(app=...)` rule holds
everywhere), in a system-owned session with a fresh `session_id` per run.
Primary invocation is **inline from the feedback service function** (awaited);
`POST /tasks/distill` exists only for admin/retry. Because the agent is
`include_contents="none"`, its entire input is the feedback JSON carried in the
run message (`"Distill feedback {feedback_id}: {type, original, edited_text,
reason}"`); `output_key="distillation"` lands its result in state, and its
`apply_profile_update` calls do the durable writes. Skips records already marked
`distilled=true` (idempotent).

## Static UI

Mount `app/static` at `/`. The page uses `/wake`, `/session/new`,
`/api/discovery-requests` for the discovery button, and `/api/*`. It never
calls a `/tasks/*` worker route directly (23 §6.2). No build step, no
framework (see 10).

## CORS & ops

- `allow_origins=["*"]` in dev; restrict to the Cloud Run URL in prod.
- Structured JSON logs, one line per request + one per tool call (from 01).
- `/healthz` returns 200 when Runner + Firestore are reachable (Cloud Run health).

## Local run

```bash
# terminal 1: agent server
./scripts/run_local.sh
# terminal 2: mock portal (see 09)
uvicorn mock_portal.main:app --port 8091
# dev chat surface (separate, never deployed)
adk web agents --port 8000 \
  --session_service_uri="sqlite+aiosqlite:///sessions.db" \
  --artifact_service_uri="file://./artifacts"
```

## Acceptance checks

- [ ] `curl -X POST localhost:8090/wake -H 'Content-Type: application/json' -d '{"message":"hi"}'` returns agent text; second call with same `session_id` continues the conversation after a server restart.
- [ ] Kill server mid-AWAITING_REVIEW → restart → `POST /api/feedback` → session resumes with drafts/checklist intact (log shows `state_delta` applied before inference).
- [ ] `POST /webhooks/portal_event` with a fake confirmation moves the application to FOLLOW_UP and clears `pending_signals`.
- [ ] `web=False` in prod: `/` serves our UI, not `adk web`.
- [ ] Runner singleton: startup log shows one Runner construction across 20 requests.
