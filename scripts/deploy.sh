#!/usr/bin/env bash
# Cloud deploy (docs/13). Idempotent. Requires: gcloud auth, billing, .env.prod.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] || { echo "missing .env (copy .env.example → .env, see docs/13)"; exit 1; }
set -a; source .env; set +a
: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT in .env}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"

[[ -f .env.prod ]] || { echo "missing .env.prod (see docs/13)"; exit 1; }
grep -q "^BROWSE_OPEN_WEB=false" .env.prod \
  || { echo ".env.prod must set BROWSE_OPEN_WEB=false (production browsing is fail-closed, docs/18)"; exit 1; }
set -a; source .env.prod; set +a
: "${DB_PASSWORD:?set DB_PASSWORD in .env.prod}"

python3 scripts/check_browser_invariants.py


echo "==> Firestore (native mode, idempotent)"
gcloud firestore databases describe --database="(default)" >/dev/null 2>&1 \
  || gcloud firestore databases create --location="$REGION"
python3 scripts/deploy_firestore_indexes.py --project "$GOOGLE_CLOUD_PROJECT"
python3 scripts/migrate_browser_runs.py

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
SECRET_ENV_KEYS=(DB_PASSWORD SESSION_SERVICE_URI GOOGLE_OAUTH_CLIENT_SECRET ALEX_MAIL_WEBHOOK_TOKEN)
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

echo "==> Cloud Tasks queue"
gcloud tasks queues describe co-founder-events --location="$REGION" >/dev/null 2>&1 \
  || gcloud tasks queues create co-founder-events --location="$REGION" \
       --max-concurrent-dispatches=1 --max-attempts=8
gcloud tasks queues describe co-founder-browser-expiry --location="$REGION" >/dev/null 2>&1 \
  || gcloud tasks queues create co-founder-browser-expiry --location="$REGION" \
       --max-concurrent-dispatches=4 --max-attempts=8
# `describe || create` cannot correct drift on an existing queue, so pin the
# reviewed limits on every deploy (idempotent, and cheap).
gcloud tasks queues update co-founder-events --location="$REGION" \
  --max-concurrent-dispatches=1 --max-attempts=8 >/dev/null
gcloud tasks queues update co-founder-browser-expiry --location="$REGION" \
  --max-concurrent-dispatches=4 --max-attempts=8 >/dev/null

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
  --format=json | python3 -c '
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

echo "==> Deploy co-founder (single browser-owning instance, docs/13)"
# scheduler-invoker SA is created below (idempotent) but the OIDC caller
# check in app/main.py needs its email at boot, so pin it here too.
SA="scheduler-invoker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
# Env file: secret keys stripped (bound via --set-secrets), duplicate keys
# deduped keep-last, values JSON-quoted so ", ', and : survive YAML. The
# freshly-deployed mock-portal URL is threaded in via env var (like the
# invoker SA) rather than rewritten into .env.prod — no in-place mutation of
# the file, and no BSD-vs-GNU `sed -i` portability trap.
EXCLUDE_KEYS="${SECRET_ENV_KEYS[*]} ${RUNTIME_SECRET_KEYS[*]} ${FIXED_SECRET_BINDING_KEYS[*]}" TASKS_INVOKER_SA="$SA" \
  MOCK_PORTAL_URL="$MOCK_URL" GOOGLE_CLOUD_REGION="$REGION" \
  python3 - <<'PY' > /tmp/co_founder_env.yaml
import json, os, re
exclude = set(os.environ["EXCLUDE_KEYS"].split())
vals = {}
for line in open(".env.prod"):
    m = re.match(r"^([A-Z_]+)=(.*)$", line.rstrip("\n"))
    if m and m.group(1) not in exclude:
        vals[m.group(1)] = m.group(2)          # duplicate keys: last one wins
vals["TASKS_INVOKER_SA"] = os.environ["TASKS_INVOKER_SA"]
vals["MOCK_PORTAL_URL"] = os.environ["MOCK_PORTAL_URL"]  # override with live URL
vals["GOOGLE_CLOUD_REGION"] = os.environ["GOOGLE_CLOUD_REGION"]
for key, val in vals.items():
    print(f"{key}: {json.dumps(val)}")
PY
# --allow-unauthenticated stays: the mock portal webhook and Pub/Sub push have
# no platform identity; the app-layer founder gate (app/auth.py) is the fence.
# Request-bound workers and durable Cloud Tasks allow true scale-to-zero — the
# ack-then-background pattern that would have needed CPU-always-allocated was
# redesigned away, so that throttling flag is intentionally left off (enforced
# by tests/unit/test_findings_hardening.py) to keep scale-to-zero economics.
gcloud run deploy co-founder --source . --region="$REGION" \
  --allow-unauthenticated --min-instances 0 --max-instances 1 --cpu-throttling \
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
gcloud run services update mock-portal --region="$REGION" \
  --update-env-vars "AGENT_BASE_URL=$APP_URL,MOCK_PORTAL_PUBLIC_URL=$MOCK_URL"

echo "==> Deadline scheduler job (idempotent)"
gcloud scheduler jobs describe deadline-scan-6h --location="$REGION" >/dev/null 2>&1 \
  || gcloud scheduler jobs create pubsub deadline-scan-6h --location="$REGION" \
       --schedule="0 */6 * * *" --topic=deadline-tick --message-body="{}"

echo "==> Pub/Sub push subscriptions → app (OIDC, idempotent)"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create scheduler-invoker --display-name="Scheduler → Cloud Run invoker"
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
