#!/usr/bin/env bash
# Cloud deploy (docs/13). Idempotent. Requires: gcloud auth, billing, .env.prod.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] \
  || { echo "missing $PYTHON (run scripts/setup.sh before deploy)"; exit 1; }
[[ -f .env ]] || { echo "missing .env (copy .env.example → .env, see docs/13)"; exit 1; }
set -a; source .env; set +a
: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT in .env}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"

[[ -f .env.prod ]] || { echo "missing .env.prod (see docs/13)"; exit 1; }
grep -q "^BROWSE_OPEN_WEB=false" .env.prod \
  || { echo ".env.prod must set BROWSE_OPEN_WEB=false (production browsing is fail-closed, docs/18)"; exit 1; }
set -a; source .env.prod; set +a
: "${DB_PASSWORD:?set DB_PASSWORD in .env.prod}"

"$PYTHON" scripts/check_browser_invariants.py


echo "==> Firestore (native mode, idempotent)"
gcloud firestore databases describe --database="(default)" >/dev/null 2>&1 \
  || gcloud firestore databases create --location="$REGION"
"$PYTHON" scripts/deploy_firestore_indexes.py --project "$GOOGLE_CLOUD_PROJECT"
"$PYTHON" scripts/deploy_firestore_ttl.py --project "$GOOGLE_CLOUD_PROJECT"
"$PYTHON" scripts/migrate_browser_runs.py

echo "==> Secrets (idempotent)"
for SECRET in mock-portal-creds portal-webhook-token app-auth-token; do
  gcloud secrets describe "$SECRET" >/dev/null 2>&1 || gcloud secrets create "$SECRET" --replication-policy=automatic
done
gcloud secrets versions list portal-webhook-token \
  --filter='state=ENABLED' --format='value(name)' | grep -q . \
  || { echo "portal-webhook-token has no enabled value; add one before deploying"; exit 1; }
# Founder app gate (app/auth.py): mint the token once, reuse forever.
gcloud secrets versions list app-auth-token \
  --filter='state=ENABLED' --format='value(name)' | grep -q . \
  || openssl rand -hex 24 | tr -d '\n' | gcloud secrets versions add app-auth-token --data-file=-

echo "==> Sensitive env → Secret Manager (never plaintext env vars, docs/12)"
# These keys are stripped from the env-vars file below and bound with
# --set-secrets instead. SESSION_SERVICE_URI carries the DB password inside
# the URL, so it is secret-managed too.
SECRET_ENV_KEYS=(DB_PASSWORD SESSION_SERVICE_URI GOOGLE_OAUTH_CLIENT_SECRET ALEX_MAIL_WEBHOOK_TOKEN APP_SESSION_SECRET HIRING_SYNTHETIC_ENCRYPTION_KEY HIRING_TEST_DISPATCH_SECRET)
RUNTIME_SECRET_KEYS=(GOOGLE_OAUTH_REFRESH_TOKEN ALEX_OAUTH_REFRESH_TOKEN)
# These names are bound to existing, canonical Secret Manager secrets below.
# They must never also appear in the generated plain env-vars file: Cloud Run
# rejects duplicate env/secret bindings, and a plaintext fallback would defeat
# the reviewed secret boundary.
FIXED_SECRET_BINDING_KEYS=(APP_AUTH_TOKEN PORTAL_WEBHOOK_TOKEN)
SET_SECRETS="PORTAL_WEBHOOK_TOKEN=portal-webhook-token:latest,APP_AUTH_TOKEN=app-auth-token:latest"
for KEY in "${SECRET_ENV_KEYS[@]}"; do
  VAL="${!KEY:-}"
  [[ -z "$VAL" ]] && continue
  SNAME="cf-$(echo "$KEY" | tr '[:upper:]_' '[:lower:]-')"
  gcloud secrets describe "$SNAME" >/dev/null 2>&1 \
    || gcloud secrets create "$SNAME" --replication-policy=automatic
  CURRENT="$(gcloud secrets versions access latest --secret "$SNAME" 2>/dev/null || true)"
  [[ "$CURRENT" == "$VAL" ]] || printf '%s' "$VAL" | gcloud secrets versions add "$SNAME" --data-file=-
  SET_SECRETS="$SET_SECRETS,$KEY=$SNAME:latest"
done
# Runtime-managed connector values are fetched by their canonical name and are
# deliberately not Cloud Run env bindings. They are created only by the in-app
# Connect flow, so a later deployment cannot resurrect a disconnected token.

echo "==> Compute SA roles (idempotent — learned the hard way, docs/13 §gotchas)"
COMPUTE_SA="$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
for ROLE in datastore.user secretmanager.secretAccessor aiplatform.user storage.objectAdmin cloudsql.client cloudbuild.builds.builder cloudtasks.enqueuer; do
  gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
    --member="serviceAccount:$COMPUTE_SA" --role="roles/$ROLE" --format="none" >/dev/null
done
# Reconcile the legacy broad grant as well as adding the intended role. Merely
# adding secretAccessor leaves an existing secretmanager.admin binding intact,
# which gives the application permission to change or delete every secret in
# the project. The runtime only reads named secret values.
if gcloud projects get-iam-policy "$GOOGLE_CLOUD_PROJECT" \
    --flatten='bindings[].members' \
    --filter="bindings.role=roles/secretmanager.admin AND bindings.members=serviceAccount:$COMPUTE_SA" \
    --format='value(bindings.role)' | grep -qx 'roles/secretmanager.admin'; then
  gcloud projects remove-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
    --member="serviceAccount:$COMPUTE_SA" \
    --role='roles/secretmanager.admin' \
    --all --quiet --format='none' >/dev/null
fi

echo "==> Cloud Tasks queue"
gcloud tasks queues describe co-founder-events --location="$REGION" >/dev/null 2>&1 \
  || gcloud tasks queues create co-founder-events --location="$REGION" \
       --max-concurrent-dispatches=1 --max-attempts=5
gcloud tasks queues describe co-founder-browser-expiry --location="$REGION" >/dev/null 2>&1 \
  || gcloud tasks queues create co-founder-browser-expiry --location="$REGION" \
       --max-concurrent-dispatches=4 --max-attempts=3
gcloud tasks queues describe co-founder-timers --location="$REGION" >/dev/null 2>&1 \
  || gcloud tasks queues create co-founder-timers --location="$REGION" \
       --max-concurrent-dispatches=8 --max-attempts=5
gcloud tasks queues describe co-founder-provider-events --location="$REGION" >/dev/null 2>&1 \
  || gcloud tasks queues create co-founder-provider-events --location="$REGION" \
       --max-concurrent-dispatches=8 --max-attempts=5
gcloud tasks queues describe co-founder-discovery-ingestion --location="$REGION" >/dev/null 2>&1 \
  || gcloud tasks queues create co-founder-discovery-ingestion --location="$REGION" \
       --max-concurrent-dispatches=4 --max-attempts=3
gcloud tasks queues describe co-founder-reconciliation --location="$REGION" >/dev/null 2>&1 \
  || gcloud tasks queues create co-founder-reconciliation --location="$REGION" \
       --max-concurrent-dispatches=4 --max-attempts=3
gcloud tasks queues describe co-founder-interactive --location="$REGION" >/dev/null 2>&1 \
  || gcloud tasks queues create co-founder-interactive --location="$REGION" \
       --max-concurrent-dispatches=4 --max-attempts=3
gcloud tasks queues describe co-founder-background-pilot --location="$REGION" >/dev/null 2>&1 \
  || gcloud tasks queues create co-founder-background-pilot --location="$REGION" \
       --max-concurrent-dispatches=1 --max-attempts=3
# `describe || create` cannot correct drift on an existing queue, so pin the
# reviewed limits on every deploy (idempotent, and cheap).
gcloud tasks queues update co-founder-events --location="$REGION" \
  --max-concurrent-dispatches=1 --max-attempts=5 \
  --min-backoff=5s --max-backoff=60s --max-doublings=4 --max-retry-duration=1800s >/dev/null
gcloud tasks queues update co-founder-browser-expiry --location="$REGION" \
  --max-concurrent-dispatches=4 --max-attempts=3 \
  --min-backoff=5s --max-backoff=60s --max-doublings=4 --max-retry-duration=1800s >/dev/null
gcloud tasks queues update co-founder-timers --location="$REGION" \
  --max-concurrent-dispatches=8 --max-attempts=5 \
  --min-backoff=5s --max-backoff=60s --max-doublings=4 --max-retry-duration=1800s >/dev/null
gcloud tasks queues update co-founder-provider-events --location="$REGION" \
  --max-concurrent-dispatches=8 --max-attempts=5 \
  --min-backoff=5s --max-backoff=60s --max-doublings=4 --max-retry-duration=1800s >/dev/null
gcloud tasks queues update co-founder-discovery-ingestion --location="$REGION" \
  --max-concurrent-dispatches=4 --max-attempts=3 \
  --min-backoff=90s --max-backoff=300s --max-doublings=2 --max-retry-duration=1800s >/dev/null
gcloud tasks queues update co-founder-reconciliation --location="$REGION" \
  --max-concurrent-dispatches=4 --max-attempts=3 \
  --min-backoff=5s --max-backoff=60s --max-doublings=4 --max-retry-duration=1800s >/dev/null
gcloud tasks queues update co-founder-interactive --location="$REGION" \
  --max-concurrent-dispatches=4 --max-attempts=3 \
  --min-backoff=5s --max-backoff=60s --max-doublings=4 --max-retry-duration=1800s >/dev/null
gcloud tasks queues update co-founder-background-pilot --location="$REGION" \
  --max-concurrent-dispatches=1 --max-attempts=3 \
  --min-backoff=5s --max-backoff=60s --max-doublings=2 --max-retry-duration=600s >/dev/null

echo "==> Cloud SQL (sessions) — create is slow; runs once"
gcloud sql instances describe co-founder-sessions >/dev/null 2>&1 \
  || gcloud sql instances create co-founder-sessions --database-version=POSTGRES_16 \
       --edition=ENTERPRISE --tier=db-f1-micro --region="$REGION"
gcloud sql databases describe adk_sessions --instance=co-founder-sessions >/dev/null 2>&1 \
  || gcloud sql databases create adk_sessions --instance=co-founder-sessions
gcloud sql users describe adk --instance=co-founder-sessions >/dev/null 2>&1 \
  || gcloud sql users create adk --instance=co-founder-sessions --password="$DB_PASSWORD"

echo "==> Artifact bucket"
gcloud storage buckets describe "gs://${GOOGLE_CLOUD_PROJECT}-artifacts" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://${GOOGLE_CLOUD_PROJECT}-artifacts" --location="$REGION"
gcloud storage buckets update "gs://${GOOGLE_CLOUD_PROJECT}-artifacts" \
  --lifecycle-file=infra/gcs-browser-lifecycle.json >/dev/null
# The lifecycle prefix is bucket-root-relative. If ARTIFACT_SERVICE_URI ever
# gains a path component, frames are written as "<path>/browserframe_…", the
# rule matches nothing, and frames accumulate forever — while a grep for the
# rule text would still pass. Assert the URI is exactly the bucket root.
[[ "${ARTIFACT_SERVICE_URI:-}" == "gs://${GOOGLE_CLOUD_PROJECT}-artifacts" ]] \
  || { echo "ARTIFACT_SERVICE_URI must be gs://${GOOGLE_CLOUD_PROJECT}-artifacts (no path prefix) or the browser-frame lifecycle rule cannot match"; exit 1; }
# Verify the applied rule's SEMANTICS (age + action + prefix), not just that
# the word appears somewhere in the config we wrote a line earlier.
gcloud storage buckets describe "gs://${GOOGLE_CLOUD_PROJECT}-artifacts" \
  --format=json | "$PYTHON" -c '
import json,sys
rules=(json.load(sys.stdin).get("lifecycle_config") or {}).get("rule") or []
ok=any(r.get("action",{}).get("type")=="Delete"
       and r.get("condition",{}).get("age")==7
       and "browserframe_" in (r.get("condition",{}).get("matchesPrefix") or [])
       for r in rules)
sys.exit(0 if ok else 1)' \
  || { echo "browser frame lifecycle verification failed (need Delete @ age 7 on browserframe_)"; exit 1; }

echo "==> Pub/Sub topics"
for TOPIC in deadline-tick; do
  gcloud pubsub topics describe "$TOPIC" >/dev/null 2>&1 || gcloud pubsub topics create "$TOPIC"
done

echo "==> Deploy mock-portal"
gcloud run deploy mock-portal --source ./mock_portal --region="$REGION" \
  --allow-unauthenticated --min-instances 0 --max-instances 2 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT" \
  --set-secrets="PORTAL_WEBHOOK_TOKEN=portal-webhook-token:latest"
MOCK_URL="$(gcloud run services describe mock-portal --region="$REGION" --format='value(status.url)')"
echo "    mock portal: $MOCK_URL"

echo "==> Prepare isolated service identities"
# scheduler-invoker SA is created below (idempotent) but the OIDC caller
# check in app/main.py needs its email at boot, so pin it here too.
SA="scheduler-invoker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
PROVIDER_EVENTS_SA="provider-events-worker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
DISCOVERY_INGESTION_SA="discovery-ingestion-worker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
TIMERS_SA="timers-worker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
BROWSER_SA="browser-worker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
RECONCILIATION_SA="reconciliation-worker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
INTERACTIVE_SA="interactive-worker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
BACKGROUND_PILOT_SA="background-pilot-worker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
for worker_name in provider-events-worker discovery-ingestion-worker timers-worker browser-worker reconciliation-worker interactive-worker background-pilot-worker; do
  worker_sa="${worker_name}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
  gcloud iam service-accounts describe "$worker_sa" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$worker_name" --display-name="Co-Founder ${worker_name}"
done
for browser_role in roles/datastore.user roles/storage.objectAdmin roles/secretmanager.secretAccessor roles/cloudtasks.enqueuer roles/aiplatform.user roles/logging.logWriter roles/cloudsql.client; do
  gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
    --member="serviceAccount:$BROWSER_SA" --role="$browser_role" \
    --format=none >/dev/null
done
# Env file: secret keys stripped (bound via --set-secrets), duplicate keys
# deduped keep-last, values JSON-quoted so ", ', and : survive YAML. The
# freshly-deployed mock-portal URL is threaded in via env var (like the
# invoker SA) rather than rewritten into .env.prod — no in-place mutation of
# the file, and no BSD-vs-GNU `sed -i` portability trap.
EXCLUDE_KEYS="${SECRET_ENV_KEYS[*]} ${RUNTIME_SECRET_KEYS[*]} ${FIXED_SECRET_BINDING_KEYS[*]} BROWSER_WORKER_URL BROWSER_WORKER_ROLE BROWSER_CALLER_SA" TASKS_INVOKER_SA="$SA" \
  TASKS_PROVIDER_EVENTS_SA="$PROVIDER_EVENTS_SA" TASKS_DISCOVERY_INGESTION_SA="$DISCOVERY_INGESTION_SA" \
  TASKS_TIMERS_SA="$TIMERS_SA" TASKS_BROWSER_SA="$BROWSER_SA" \
  TASKS_RECONCILIATION_SA="$RECONCILIATION_SA" TASKS_INTERACTIVE_SA="$INTERACTIVE_SA" \
  TASKS_BACKGROUND_PILOT_SA="$BACKGROUND_PILOT_SA" \
  MOCK_PORTAL_URL="$MOCK_URL" GOOGLE_CLOUD_REGION="$REGION" \
  "$PYTHON" - <<'PY' > /tmp/co_founder_env.yaml
import json, os, re
exclude = set(os.environ["EXCLUDE_KEYS"].split())
vals = {}
for line in open(".env.prod"):
    m = re.match(r"^([A-Z_]+)=(.*)$", line.rstrip("\n"))
    if m and m.group(1) not in exclude:
        vals[m.group(1)] = m.group(2)          # duplicate keys: last one wins
vals["TASKS_INVOKER_SA"] = os.environ["TASKS_INVOKER_SA"]
for key in ("TASKS_PROVIDER_EVENTS_SA", "TASKS_DISCOVERY_INGESTION_SA",
            "TASKS_TIMERS_SA", "TASKS_BROWSER_SA",
            "TASKS_RECONCILIATION_SA", "TASKS_INTERACTIVE_SA",
            "TASKS_BACKGROUND_PILOT_SA"):
    vals[key] = os.environ[key]
vals["MOCK_PORTAL_URL"] = os.environ["MOCK_PORTAL_URL"]  # override with live URL
vals["GOOGLE_CLOUD_REGION"] = os.environ["GOOGLE_CLOUD_REGION"]
vals["HIRING_WORKLOAD_ALLOWLIST_JSON"] = json.dumps({
    "/tasks/hiring/process_mailbox_batch": [os.environ["TASKS_INVOKER_SA"]],
}, separators=(",", ":"))
vals["BACKGROUND_PILOT_WORKLOAD_ALLOWLIST_JSON"] = json.dumps({
    "/tasks/background-artifact-pilot": [
        os.environ["TASKS_BACKGROUND_PILOT_SA"]],
}, separators=(",", ":"))
for key, val in vals.items():
    print(f"{key}: {json.dumps(val)}")
PY
cp /tmp/co_founder_env.yaml /tmp/co_founder_browser_env.yaml
cat >> /tmp/co_founder_browser_env.yaml <<EOF
BROWSER_WORKER_ROLE: "1"
BROWSER_CALLER_SA: "$COMPUTE_SA"
EOF
echo "==> Deploy isolated browser worker"
gcloud run deploy co-founder-browser-worker --source . --region="$REGION" \
  --no-allow-unauthenticated --min-instances 0 --max-instances 1 \
  --concurrency=1 --cpu-throttling --memory 2Gi --timeout=3600s \
  --service-account="$BROWSER_SA" \
  --add-cloudsql-instances "${GOOGLE_CLOUD_PROJECT}:${REGION}:co-founder-sessions" \
  --set-secrets="$SET_SECRETS" \
  --env-vars-file /tmp/co_founder_browser_env.yaml
BROWSER_URL="$(gcloud run services describe co-founder-browser-worker --region="$REGION" --format='value(status.url)')"
rm -f /tmp/co_founder_browser_env.yaml
gcloud run services add-iam-policy-binding co-founder-browser-worker --region="$REGION" \
  --member="serviceAccount:$COMPUTE_SA" --role=roles/run.invoker >/dev/null
cat >> /tmp/co_founder_env.yaml <<EOF
BROWSER_WORKER_URL: "$BROWSER_URL"
EOF
echo "    browser worker: $BROWSER_URL"
# --allow-unauthenticated stays: the mock portal webhook and Pub/Sub push have
# no platform identity; the app-layer founder gate (app/auth.py) is the fence.
# Request-bound workers and durable Cloud Tasks allow true scale-to-zero — the
# ack-then-background pattern that would have needed CPU-always-allocated was
# redesigned away, so that throttling flag is intentionally left off (enforced
# by tests/unit/test_findings_hardening.py) to keep scale-to-zero economics.
echo "==> Deploy co-founder modular monolith (Playwright remote)"
gcloud run deploy co-founder --source . --region="$REGION" \
  --allow-unauthenticated --min-instances 0 --max-instances 10 --cpu-throttling \
  --memory 2Gi --timeout=3600s \
  --add-cloudsql-instances "${GOOGLE_CLOUD_PROJECT}:${REGION}:co-founder-sessions" \
  --set-secrets="$SET_SECRETS" \
  --env-vars-file /tmp/co_founder_env.yaml
rm -f /tmp/co_founder_env.yaml
APP_URL="$(gcloud run services describe co-founder --region="$REGION" --format='value(status.url)')"
echo "    app: $APP_URL"

echo "==> Wire URLs both ways (mock portal webhooks → app; app → mock portal)"
# AGENT_BASE_URL is applied to the live service below; no need to rewrite it
# into .env.prod (that in-place edit was BSD-only `sed -i ''` and a surprising
# side effect on a tracked-ish config file — the deployed env is the source
# of truth here).
gcloud run services update co-founder --region="$REGION" \
  --update-env-vars "AGENT_BASE_URL=$APP_URL,MOCK_PORTAL_URL=$MOCK_URL"
gcloud run services update co-founder-browser-worker --region="$REGION" \
  --update-env-vars "AGENT_BASE_URL=$APP_URL,BROWSER_WORKER_URL=$BROWSER_URL,MOCK_PORTAL_URL=$MOCK_URL"
gcloud run services update mock-portal --region="$REGION" \
  --update-env-vars "AGENT_BASE_URL=$APP_URL,MOCK_PORTAL_PUBLIC_URL=$MOCK_URL"

echo "==> Deadline scheduler job (idempotent)"
gcloud scheduler jobs describe deadline-scan-6h --location="$REGION" >/dev/null 2>&1 \
  || gcloud scheduler jobs create pubsub deadline-scan-6h --location="$REGION" \
       --schedule="0 */6 * * *" --topic=deadline-tick --message-body="{}"

echo "==> Pub/Sub push subscriptions → app (OIDC, idempotent)"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create scheduler-invoker --display-name="Scheduler → Cloud Run invoker"
for worker_name in provider-events-worker discovery-ingestion-worker timers-worker browser-worker reconciliation-worker interactive-worker background-pilot-worker; do
  worker_sa="${worker_name}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
  gcloud iam service-accounts add-iam-policy-binding "$worker_sa" \
    --member="serviceAccount:$COMPUTE_SA" --role="roles/iam.serviceAccountUser" \
    --format=none >/dev/null
  gcloud run services add-iam-policy-binding co-founder --region="$REGION" \
    --member="serviceAccount:$worker_sa" --role=roles/run.invoker >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member="serviceAccount:$COMPUTE_SA" --role="roles/iam.serviceAccountUser" \
  --format=none >/dev/null
gcloud run services add-iam-policy-binding co-founder --region="$REGION" \
  --member="serviceAccount:$SA" --role=roles/run.invoker >/dev/null
# Pub/Sub's service agent must be able to mint OIDC tokens AS the invoker SA,
# or push deliveries arrive tokenless and the app's caller check rejects them.
PROJECT_NUMBER="$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" --format='value(projectNumber)')"
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator --format=none >/dev/null
# The scheduler service agent mints the audience-bound token as the timers
# worker. The app then verifies both token audience and exact caller email.
gcloud iam service-accounts add-iam-policy-binding "$TIMERS_SA" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator --format=none >/dev/null
echo "==> Command outbox recovery scheduler (idempotent, low-frequency safety net)"
if gcloud scheduler jobs describe command-outbox-recovery-1m --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http command-outbox-recovery-1m --location="$REGION" \
    --schedule="*/15 * * * *" --uri="$APP_URL/tasks/dispatch_command_outbox" \
    --http-method=POST --oidc-service-account-email="$TIMERS_SA" \
    --oidc-token-audience="$APP_URL" --headers="Content-Type=application/json" \
    --message-body='{}'
else
  gcloud scheduler jobs create http command-outbox-recovery-1m --location="$REGION" \
    --schedule="*/15 * * * *" --uri="$APP_URL/tasks/dispatch_command_outbox" \
    --http-method=POST --oidc-service-account-email="$TIMERS_SA" \
    --oidc-token-audience="$APP_URL" --headers="Content-Type=application/json" \
    --message-body='{}'
fi
gcloud pubsub subscriptions describe deadline-tick-push >/dev/null 2>&1 \
  || gcloud pubsub subscriptions create deadline-tick-push --topic=deadline-tick \
       --push-endpoint="$APP_URL/webhooks/deadline" \
       --push-auth-service-account="$SA" \
       --push-auth-token-audience="$APP_URL" \
       --ack-deadline=600
# Correct drift on existing subscriptions. The webhook only performs a durable
# enqueue, but 600 seconds also covers a cold start without duplicate delivery.
gcloud pubsub subscriptions update deadline-tick-push --ack-deadline=600 >/dev/null

echo "==> Done. Verify: docs/13 §verification checklist."
echo "    APP:  $APP_URL"
echo "    MOCK: $MOCK_URL"
echo "    Founder access (sets the auth cookie once):"
echo "    $APP_URL/?key=\$(gcloud secrets versions access latest --secret app-auth-token)"
