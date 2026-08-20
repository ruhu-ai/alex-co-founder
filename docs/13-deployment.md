# 13 — Deployment

Two Cloud Run services + Firestore + Cloud SQL + Pub/Sub + Scheduler + Secret
Manager + GCS. Everything scale-to-zero / pay-per-use so the project can stay up
for judge testing (through ~Oct 1) at near-zero cost.

## Prereqs

- GCP project with billing; `$150` hackathon credits applied.
- CLIs: `gcloud`, `uv` (or pip), Docker (or use Cloud Build).
- `scripts/setup.sh` (idempotent): checks Python ≥ 3.11, creates `.venv`,
  installs requirements, prompts once for project id (validated with
  `gcloud projects describe`, remembered in `.env`), runs
  `gcloud auth application-default login` only if no creds, enables APIs
  (skips enabled), writes `.env`, makes one test Gemini call.

APIs to enable:
`run firestore sqladmin pubsub cloudscheduler secretmanager storage cloudbuild aiplatform cloudtrace logging`

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
| Pub/Sub topics | `discovery-tick`, `deadline-tick` | **no distill topic** — the distiller runs inline on the interactive path (07) by design |
| Scheduler jobs | `discovery-daily` (`0 7 * * *`), `deadline-scan-6h` (`0 */6 * * *`) | OIDC service account `scheduler-invoker@` |
| Cloud Run ×2 | `co-founder`, `mock-portal` | `co-founder`: `--min-instances 0 --max-instances 1` (**single browser-owning instance**, 18), 2 GiB for Playwright; `mock-portal`: `--min-instances 0 --max-instances 2`, 1 GiB / 1 CPU |

Reference commands (`scripts/deploy.sh` implements them idempotently):

```bash
# sessions database
gcloud sql instances create co-founder-sessions --database-version=POSTGRES_16 \
  --tier=db-f1-micro --region=$REGION
gcloud sql databases create adk_sessions --instance=co-founder-sessions
# prod SESSION_SERVICE_URI:
# postgresql+asyncpg://adk:<pw>@/adk_sessions?host=/cloudsql/<proj>:<region>:co-founder-sessions

gcloud run deploy mock-portal --source ./mock_portal --region=$REGION \
  --allow-unauthenticated --min-instances 0

gcloud run deploy co-founder --source . --region=$REGION \
  --allow-unauthenticated --min-instances 0 --max-instances 1 --memory 2Gi \
  --add-cloudsql-instances <proj>:<region>:co-founder-sessions \
  --set-env-vars-from-file .env.prod   # contains PORTAL_SECRET_NAME=mock-portal-creds
# .env.prod must NOT set BROWSE_OPEN_WEB (or must set it false) — production
# browsing is fail-closed to the allowlist (18). scripts/deploy.sh asserts this
# and --max-instances 1 before deploying.

# secrets are fetched BY NAME via the API at execution time (12), never injected
# as env values — grant the service account accessor instead:
gcloud secrets add-iam-policy-binding mock-portal-creds \
  --role=roles/secretmanager.secretAccessor \
  --member="serviceAccount:<run-sa>@<proj>.iam.gserviceaccount.com"

gcloud pubsub topics create discovery-tick
gcloud pubsub topics create deadline-tick
gcloud scheduler jobs create pubsub discovery-daily --schedule="0 7 * * *" \
  --topic=discovery-tick --message-body="{}"
gcloud scheduler jobs create pubsub deadline-scan-6h --schedule="0 */6 * * *" \
  --topic=deadline-tick --message-body="{}"
# push subscriptions -> /tasks/discover and /tasks/deadline_scan with OIDC SA
```

Dockerfile notes: Playwright needs system Chromium — use
`mcr.microsoft.com/playwright/python:v1.47.0-jammy` (or run
`playwright install --with-deps chromium` in the build). Mock portal image is a
plain slim Python image.

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

- [ ] `GET https://co-founder-<hash>.run.app/healthz` → 200
- [ ] UI loads at the `.run.app` URL; chat round-trip works
- [ ] Mock portal reachable; full demo sequence runs in the cloud
- [ ] `gcloud scheduler jobs run discovery-daily` → opportunities appear in Firestore console
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
- Playwright runs are seconds-long; Scheduler fires 1+4 times/day.
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
- [ ] Production verification: deployed `co-founder` revision has `--max-instances 1` and no `BROWSE_OPEN_WEB=true` (deploy script asserts both; 18).
- [ ] 48 h idle → next month's projected bill < $10 beyond credits.
