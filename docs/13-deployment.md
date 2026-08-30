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
google-adk[eval]==2.8.0
google-genai==2.20.0
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
# For automatic local Alex Mail wakeups, use the deployed Gmail topic and the
# dedicated pull subscription. The launcher starts a streaming subscriber and
# acknowledges only after the local webhook succeeds.
# ALEX_MAIL_PUBSUB_TOPIC=projects/<project>/topics/alex-mail-events
# ALEX_MAIL_LOCAL_SUBSCRIPTION=projects/<project>/subscriptions/alex-mail-local-dev
# Isolated clean-main worktrees may reuse the saved project's environment:
# LOCAL_ENV_FILE=/path/to/project/.env LOCAL_VENV_DIR=/path/to/project/.venv ./scripts/run_local.sh
# The launcher exports the resolved LOCAL_ENV_FILE so an OAuth callback writes
# back to the same locked file even when the code runs from another checkout.
# terminal 1
./scripts/run_local.sh
# terminal 2
uvicorn mock_portal.main:app --port 8091
# optional dev inspector
adk web agents --port 8000 \
  --session_service_uri="sqlite+aiosqlite:///sessions.db" \
  --artifact_service_uri="file://./artifacts"
python scripts/seed_demo.py   # profile seeded under user / eval_founder / demo founder_id
                              # (see 02), demo opportunities, seeded rejection
```

Before enabling `/api/v1` mutations in a deployed workspace, explicitly
provision the sole interactive Founder. `WORKSPACE_FOUNDER_SUBJECT` is the
Firebase/OIDC `sub` claim from the same identity provider used by
`/auth/session`—never an email address and never the legacy founder token.
There is no browser workspace-pass/bootstrap route and no operator role or
authority flag.

```bash
WORKSPACE_FOUNDER_SUBJECT="..." WORKSPACE_ID="founder" \
  .venv/bin/python scripts/seed_workspace_founder.py
```

Re-running the command does not change an existing membership. Subsequent
activation or revocation changes use the versioned workspace-members API and
its audit receipt; the API has no role selector.

Only an active `FOUNDER` membership grants product access. Founder authority
still cannot bypass recent-auth requirements, CSRF, durable workflow state,
idempotency, exact-action approval, or consequence controls. Adding shared or
multi-person workspace admission requires a separately reviewed design; it is
not an environment switch.

This pre-production cut accepts no old membership role values. Inspect the
exact stale records first; the command is dry-run unless both execution flags
name the same workspace:

```bash
.venv/bin/python scripts/reset_legacy_workspace_memberships.py \
  --workspace-id founder
.venv/bin/python scripts/reset_legacy_workspace_memberships.py \
  --workspace-id founder --execute --confirm-workspace-id founder
```

The reset command recognizes only the retired closed set, never deletes a
`FOUNDER` or unknown-role row, writes one audit receipt per deletion, and does
not alter any other collection. Bootstrap the one-role workspace after reset.

## Cloud resources

| Resource | Choice | Notes |
|---|---|---|
| Firestore | native mode, `(default)` db | pipeline store |
| Cloud SQL | Postgres 16, `db-f1-micro`, private IP optional | ADK sessions in prod |
| GCS bucket | `gs://<project>-artifacts` | artifact service URI |
| Secret Manager | `mock-portal-creds`, `portal-webhook-token` | |
| Pub/Sub topics | `deadline-tick`, `alex-mail-events` | Gmail publishes content-free history notifications to `alex-mail-events`. Authenticated Cloud Run push and the dedicated local streaming-pull subscription consume the same idempotent event stream. Discovery is founder-invoked through Cloud Tasks; **no discovery or distill topic**. |
| Scheduler jobs | `deadline-scan-6h` (`0 */6 * * *`), legacy-named `command-outbox-recovery-1m` (`*/15 * * * *`), `alex-mail-watch-renew-daily` (`17 3 * * *`) | The first publishes the deadline tick. The second is a low-frequency crash-window safety net for the primary event-driven dispatch. The third renews the expiring unfiltered Gmail watch through the timers workload identity; it does not poll mail or run a model. Discovery is never scheduled. |
| Cloud Tasks queue | `co-founder-events` | Durable HTTP dispatch for portal-event/agent wakes; OIDC-authenticated as `scheduler-invoker@`, max concurrency 1, max attempts 5. Browser expiry does not share this queue. |
| Cloud Tasks queue | `co-founder-browser-expiry` | Generation-safe `/tasks/browser_expire` dispatch only (22); OIDC-authenticated, max concurrency 4, max attempts 3. Separating it prevents an agent wake or retry from delaying resource release. |
| Cloud Tasks queue | `co-founder-timers` | Generation-fenced workflow timer checkpoints only; max concurrency 8, max attempts 5. Long waits roll through bounded 28-day checkpoints and the durable wait remains authority. |
| Cloud Tasks queue | `co-founder-provider-events` | Verified connector events and durable founder wakes; route-scoped `provider-events-worker@` OIDC identity, max concurrency 8, max attempts 5. |
| Cloud Tasks queue | `co-founder-discovery-ingestion` | Crawling and document/image ingestion; route-scoped `discovery-ingestion-worker@` identity, max concurrency 4, max attempts 3. Each task has an 840-second deadline under its 900-second durable lease; its 90-second minimum redelivery backoff prevents a timed-out worker's still-live lease from consuming the next attempt. |
| Cloud Tasks queue | `co-founder-reconciliation` | Uncertain-effect reconciliation only; route-scoped `reconciliation-worker@` identity, max concurrency 4, max attempts 3. |
| Cloud Tasks queue | `co-founder-interactive` | Bounded founder-triggered reasoning/distillation; route-scoped `interactive-worker@` identity, max concurrency 4, max attempts 3. |
| Cloud Tasks queue | `co-founder-background-pilot` | Default-off founder artifact-analysis pilot only; route-scoped `background-pilot-worker@` identity, max concurrency 1, max attempts 3, zero provider/model/effect authority. |
| Cloud Tasks queue | `co-founder-background-skill-live-v1` | Default-off Founder grounded-artifact pilot only; route-scoped `background-skill-worker@` identity, max concurrency 1, max attempts 3, one bounded model call and no external/effect authority. |
| Cloud Run ×3 | `co-founder`, `co-founder-browser-worker`, `mock-portal` | `co-founder` scales independently (`0..10`) and reaches browser work only through the typed OIDC gateway. `co-founder-browser-worker` is private, `0..1`, concurrency 1, owns every Playwright object, and accepts only the API service identity. `mock-portal` is a non-authoritative demo provider (`0..2`). |

Reference commands (`scripts/deploy.sh` implements them idempotently):

```bash
# sessions database
gcloud sql instances create co-founder-sessions --database-version=POSTGRES_16 \
  --tier=db-f1-micro --region=$REGION
gcloud sql databases create adk_sessions --instance=co-founder-sessions
# prod SESSION_SERVICE_URI:
# postgresql+asyncpg://adk:<pw>@/adk_sessions?host=/cloudsql/<proj>:<region>:co-founder-sessions

gcloud tasks queues create co-founder-events --location="$REGION" \
  --max-concurrent-dispatches=1 --max-attempts=5
gcloud tasks queues create co-founder-browser-expiry --location="$REGION" \
  --max-concurrent-dispatches=4 --max-attempts=3
gcloud tasks queues create co-founder-timers --location="$REGION" \
  --max-concurrent-dispatches=8 --max-attempts=5

gcloud run deploy mock-portal --source ./mock_portal --region=$REGION \
  --allow-unauthenticated --min-instances 0

gcloud run deploy co-founder-browser-worker --source . --region=$REGION \
  --no-allow-unauthenticated --min-instances 0 --max-instances 1 \
  --concurrency=1 --service-account=browser-worker@<proj>.iam.gserviceaccount.com \
  --timeout=3600s --memory=2Gi

gcloud run deploy co-founder --source . --region=$REGION \
  --allow-unauthenticated --min-instances 0 --max-instances 10 --timeout=3600s --memory 2Gi \
  --add-cloudsql-instances <proj>:<region>:co-founder-sessions \
  --set-env-vars-from-file .env.prod   # contains PORTAL_SECRET_NAME=mock-portal-creds
# .env.prod must NOT set BROWSE_OPEN_WEB (or must set it false) — production
# browsing is fail-closed to the allowlist (18). scripts/deploy.sh asserts this
# on either service. The isolated worker, not the public API, owns the
# single-instance/concurrency invariant.

# Before any migration or Cloud Run rollout, deploy.sh installs every composite
# index in infra/firestore.indexes.json and verifies each is READY. It fails the
# deployment if a declared query shape is absent or still building:
python3 scripts/deploy_firestore_indexes.py --project "$GOOGLE_CLOUD_PROJECT"
python3 scripts/deploy_firestore_ttl.py --project "$GOOGLE_CLOUD_PROJECT"

# Alex vision release flags are independent and disabled by default:
# ALEX_STILL_IMAGE_ENABLED, ALEX_LIVE_VISION_ENABLED,
# ALEX_LIVE_CAMERA_ENABLED, ALEX_LIVE_DISPLAY_ENABLED. Do not enable a Live
# source unless the master Live flag and that source flag are both true. The
# TTL command above is a prerequisite for any internal visual rollout.

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

Phase 7 recovery configuration and proof are intentionally separate from the
normal deploy. Run `scripts/configure_phase7_reliability.sh` only after reviewing
its dry run, audit with `scripts/phase7_cloud_audit.py`, and execute the isolated
restore/load/chaos process in [35](35-platform-operations-and-recovery.md).
Enabling backups is not evidence that restore works.

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
- [ ] `./scripts/deploy.sh` from clean state → all three services live, demo sequence passes in cloud.
- [ ] Production verification: `co-founder-browser-worker` is private,
  `--max-instances 1`, concurrency 1, runs as `browser-worker@`, and accepts an
  audience-bound call from the API identity while rejecting another service
  account. `co-founder` scales above one with `BROWSER_WORKER_URL` set. Neither
  revision has `BROWSE_OPEN_WEB=true`; `scripts/check_browser_invariants.py`
  passes (18/22/34).
- [ ] `co-founder-browser-expiry` and `co-founder-timers` exist separately from
  `co-founder-events`; expiry, timer checkpoint, and portal-wake delivery cannot
  head-of-line block each other in an integration test.
- [ ] GCS lifecycle verification shows the seven-day browser-frame rule without
  applying that TTL to milestone PNG, page text, fill report, or form-map data.
- [ ] 48 h idle → next month's projected bill < $10 beyond credits.
