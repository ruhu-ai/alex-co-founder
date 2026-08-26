# 13 — Deployment

Two Cloud Run services + Firestore + Cloud SQL + Pub/Sub + Scheduler + Cloud
Tasks + Secret Manager + GCS. Everything scale-to-zero / pay-per-use so the
project can stay up for judge testing (through ~Oct 1) at near-zero cost.

## Prereqs

- GCP project with billing; `$150` hackathon credits applied.
- CLIs: `gcloud`, `uv` (or pip), Docker (or use Cloud Build).
- `scripts/setup.sh` (idempotent): checks Python ≥ 3.11, creates `.venv`,
  installs requirements, prompts once for project id (validated with
  `gcloud projects describe`, remembered in `.env`), runs
  `gcloud auth application-default login` only if no creds, enables APIs
  (skips enabled), writes `.env`, makes one test Gemini call.

APIs to enable:
`run firestore sqladmin pubsub cloudscheduler cloudtasks secretmanager storage cloudbuild aiplatform cloudtrace logging`

`requirements.txt` (mirrors the reference repo's pyproject + our additions):

```
google-adk[eval]>=2.6
google-genai
google-cloud-aiplatform[agent-engines,evaluation]
google-cloud-firestore
google-cloud-logging
google-cloud-secret-manager
google-cloud-storage
google-cloud-sql-connector[asyncpg]   # or plain asyncpg
opentelemetry-instrumentation-google-genai
gcsfs
fastapi
uvicorn
sqlalchemy[asyncio]
aiosqlite
asyncpg           # REQUIRED for prod: ADK drives SQLAlchemy via asyncio, so the
                  # driver must be async — postgresql+asyncpg://, never postgresql://
greenlet
playwright
pydantic
pyyaml
httpx
```

Dev extras: `pytest pytest-asyncio nest-asyncio ruff`.

⚠️ Known trap (reference lab): `postgresql+asyncpg://`, not `postgresql://` —
ADK's SQLAlchemy is async; the sync driver "works until the first session write,
which in the cloud is at 3am with nobody watching." Same class of trap as
`aiosqlite` locally. The `host=/cloudsql/<proj>:<region>:<instance>` unix-socket
form is what Cloud Run mounts with `--add-cloudsql-instances`.

## Production gotchas (learned 2026-08-20, first deploy)

- **`/healthz` is intercepted by the Google Front End** — use `/health`
  (both services expose it; the edge owns `/healthz`).
- **Org policy `iam.allowedPolicyMemberDomains`** blocks `allUsers` on
  Workspace-org projects: grant yourself `roles/orgpolicy.policyAdmin` on the
  org, then set a project-level `allowAll` policy before public deploy.
- **Compute SA needs explicit roles** for the app: `datastore.user`,
  `secretmanager.secretAccessor` (never administrator), `aiplatform.user`, `storage.objectAdmin`,
  `cloudsql.client`, and `cloudbuild.builds.builder` (source deploys).
- **Cloud SQL tier**: new gcloud defaults to ENTERPRISE_PLUS edition;
  `db-f1-micro` needs `--edition=ENTERPRISE`.
- **gcloud flag renames (2026 CLI):** `--env-vars-file` (YAML, not dotenv —
  deploy.sh converts), `--push-auth-service-account`,
  `--push-auth-token-audience`.

## Local development

```bash
./scripts/setup.sh && source .venv/bin/activate
cp .env.example .env   # fill values
# terminal 1
uvicorn app.main:app --port 8090 --reload
# terminal 2
uvicorn mock_portal.main:app --port 8091
# optional dev inspector
adk web agents --port 8000 \
  --session_service_uri="sqlite+aiosqlite:///sessions.db" \
  --artifact_service_uri="file://./artifacts"
python scripts/seed_demo.py   # profile seeded under user / eval_founder / demo founder_id
                              # (see 02), demo opportunities, seeded rejection
```

## Cloud resources

| Resource | Choice | Notes |
|---|---|---|
| Firestore | native mode, `(default)` db | pipeline store |
| Cloud SQL | Postgres 16, `db-f1-micro`, private IP optional | ADK sessions in prod |
| GCS bucket | `gs://<project>-artifacts` | artifact service URI |
| Secret Manager | `mock-portal-creds`, `portal-webhook-token` | |
| Pub/Sub topics | `deadline-tick` | Discovery is founder-invoked through Cloud Tasks; **no discovery or distill topic** |
| Scheduler jobs | `deadline-scan-6h` (`0 */6 * * *`) | OIDC service account `scheduler-invoker@`; discovery is never scheduled. The Pub/Sub push fast-enqueues Cloud Tasks and uses a 600-second ack deadline to cover cold starts without duplicate delivery. |
| Cloud Tasks queue | `co-founder-events` | Durable HTTP dispatch for portal-event/agent wakes; OIDC-authenticated as `scheduler-invoker@`, max concurrency 1, max attempts 8. Browser expiry does not share this queue. The post-v1 expansion into general run-step/timer dispatch is specified in 21. |
| Cloud Tasks queue | `co-founder-browser-expiry` | Generation-safe `/tasks/browser_expire` dispatch only (22); OIDC-authenticated, max concurrency 4, max attempts 8 (pinned on every deploy so an existing queue cannot drift). Separating it prevents an agent wake or retry from delaying resource release. |
| Cloud Run ×2 | `co-founder`, `mock-portal` | `co-founder`: `--min-instances 0 --max-instances 1` (**single browser-owning instance**, 18), `--timeout=3600s`, 2 GiB for Playwright; Browser SSE rotates before 55 minutes and reconnects snapshot-first. `mock-portal`: `--min-instances 0 --max-instances 2`, 1 GiB / 1 CPU |

Reference commands (`scripts/deploy.sh` implements them idempotently):

```bash
# sessions database
gcloud sql instances create co-founder-sessions --database-version=POSTGRES_16 \
  --tier=db-f1-micro --region=$REGION
gcloud sql databases create adk_sessions --instance=co-founder-sessions
# prod SESSION_SERVICE_URI:
# postgresql+asyncpg://adk:<pw>@/adk_sessions?host=/cloudsql/<proj>:<region>:co-founder-sessions

gcloud tasks queues create co-founder-events --location="$REGION" \
  --max-concurrent-dispatches=1 --max-attempts=8
gcloud tasks queues create co-founder-browser-expiry --location="$REGION" \
  --max-concurrent-dispatches=10 --max-attempts=5

gcloud run deploy mock-portal --source ./mock_portal --region=$REGION \
  --allow-unauthenticated --min-instances 0

gcloud run deploy co-founder --source . --region=$REGION \
  --allow-unauthenticated --min-instances 0 --max-instances 1 --timeout=3600s --memory 2Gi \
  --add-cloudsql-instances <proj>:<region>:co-founder-sessions \
  --set-env-vars-from-file .env.prod   # contains PORTAL_SECRET_NAME=mock-portal-creds
# .env.prod must NOT set BROWSE_OPEN_WEB (or must set it false) — production
# browsing is fail-closed to the allowlist (18). scripts/deploy.sh asserts this
# and --max-instances 1 before deploying.

# Before any migration or Cloud Run rollout, deploy.sh installs every composite
# index in infra/firestore.indexes.json and verifies each is READY. It fails the
# deployment if a declared query shape is absent or still building:
python3 scripts/deploy_firestore_indexes.py --project "$GOOGLE_CLOUD_PROJECT"

# GCS lifecycle: browserframe_*.jpg objects expire after seven days; milestone
# PNG/page-text/form artifacts retain the application-artifact policy (02/22).
# scripts/deploy.sh applies and verifies a reviewed bucket lifecycle JSON with a
# matchesPrefix rule for the browser-frame object prefix.

# secrets are fetched BY NAME via the API at execution time (12), never injected
# as env values — grant the service account accessor instead:
gcloud secrets add-iam-policy-binding mock-portal-creds \
  --role=roles/secretmanager.secretAccessor \
  --member="serviceAccount:<run-sa>@<proj>.iam.gserviceaccount.com"

gcloud pubsub topics create deadline-tick
gcloud scheduler jobs create pubsub deadline-scan-6h --schedule="0 */6 * * *" \
  --topic=deadline-tick --message-body="{}"
# push subscription -> /webhooks/deadline with OIDC SA
# founder-invoked discovery -> Cloud Tasks -> /tasks/discover
```

Dockerfile notes: Playwright needs system Chromium — use
`mcr.microsoft.com/playwright/python:v1.47.0-jammy` (or run
`playwright install --with-deps chromium` in the build). The image also
installs `libreoffice-writer/-calc/-impress` (pinned apt — document previews
and PDF output, docs/15 §LibreOffice) and runs as the non-root `pwuser`.
Mock portal image is a plain slim Python image.

## Telemetry (matches the reference repo's `app_utils/telemetry.py`)

`app/app_utils/telemetry.py::setup_telemetry()` sets, at import time:

```
GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT   # metadata only, never prompts
OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT=jsonl
OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK=upload
OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH=gs://<logs-bucket>/completions
```

Plus `otel_to_cloud=True` on `get_fast_api_app` (07). Result: end-to-end
reasoning-chain traces in Cloud Trace with **no message content** — the
production-readiness proof for the video, with PII safety built in. Note
`ADK_TRIGGER_MAX_RETRIES` (default 3) in the README's idempotency section:
redelivery happens; derived idempotency keys are why it is safe.

## Verification checklist (post-deploy)

Before the first revision that reads canonical docs/24 data-source rows, run the
migration without flags and retain its `plan_hash`, target counts, parity field,
and rollback manifest. Only then run it with `--apply`. The apply path is
additive/idempotent, creates unverified legacy connections as `DEGRADED`, never
copies credential values, never synthesizes historical provider events, and
never deletes legacy records. A rollback keeps legacy reads enabled and removes
only the separately reviewed exact canonical paths from the report; the script
does not perform rollback deletion.

```bash
.venv/bin/python scripts/migrate_data_sources.py
.venv/bin/python scripts/migrate_data_sources.py --apply
```

- [ ] `GET https://co-founder-<hash>.run.app/healthz` → 200
- [ ] UI loads at the `.run.app` URL; chat round-trip works
- [ ] Mock portal reachable; full demo sequence runs in the cloud
- [ ] Founder sends `/discover` → Cloud Task runs once and opportunities appear in Firestore
- [ ] Kill/restart proof: Cloud Run revision swap mid-workflow → session resumes
- [ ] Vertex AI: model calls visible in console (video proof source)
- [ ] Cloud Trace shows end-to-end runs (screenshot for README)

## Cost guardrails (must stay near $0 after demo)

- All services `--min-instances 0`; agent sleeps between events by design.
  **Exception:** bump the agent service to `--min-instances 1` on recording days
  and through the judging window — cold-start + Chromium launch mid-video reads
  as "broken" (demo-day failure mode #1). Pre-warm with a `/healthz` call before
  rolling; still ~$0 at idle traffic.
- Cloud SQL `db-f1-micro` is the only always-on cost (~$9/mo — fits in credits);
  alternative if needed: stop the instance between demo days, sessions persist.
- Playwright runs are seconds-long; the deadline Scheduler fires 4 times/day.
- Budget alert at $20; teardown script: `gcloud run services delete ...`,
  `gcloud sql instances patch --no-activation-policy` etc. (list in README).

## Submission housekeeping (from the rules — do not skip)

- Project must remain available free of charge for judge testing until ~Oct 1:
  the scale-to-zero config above exists for exactly this reason.
- README.md spin-up instructions: prereqs → setup.sh → env → run local → run
  tests → deploy. A judge must be able to reproduce without guessing.
- Architecture diagram in repo (Gemini ↔ ADK agents ↔ Firestore/Cloud SQL ↔
  Pub/Sub/Scheduler ↔ browser tool ↔ mock portal).
- Video must show Google Cloud proof: Cloud Run dashboard, Vertex logs,
  `.run.app` URL.

## Acceptance checks

- [ ] Fresh clone → `./scripts/setup.sh` → local demo works in ≤ 15 min (time it).
- [ ] `./scripts/deploy.sh` from clean state → both services live, demo sequence passes in cloud.
- [ ] Production verification: deployed `co-founder` revision has
  `--max-instances 1`, `--timeout=3600s`, no `BROWSE_OPEN_WEB=true`, and passes
  `scripts/check_browser_invariants.py` (deploy/CI assert all four; 18/22).
- [ ] `co-founder-browser-expiry` exists separately from `co-founder-events`;
  expiry task delivery and portal-wake delivery cannot head-of-line block each
  other in an integration test.
- [ ] GCS lifecycle verification shows the seven-day browser-frame rule without
  applying that TTL to milestone PNG, page text, fill report, or form-map data.
- [ ] 48 h idle → next month's projected bill < $10 beyond credits.
