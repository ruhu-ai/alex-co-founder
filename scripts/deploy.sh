#!/usr/bin/env bash
# Cloud deploy (docs/13). Idempotent. Requires: gcloud auth, billing, .env.prod.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; source .env; set +a
: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT in .env}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"

[[ -f .env.prod ]] || { echo "missing .env.prod (see docs/13)"; exit 1; }
grep -q "^BROWSE_OPEN_WEB=false" .env.prod \
  || { echo ".env.prod must set BROWSE_OPEN_WEB=false (production browsing is fail-closed, docs/18)"; exit 1; }
set -a; source .env.prod; set +a
: "${DB_PASSWORD:?set DB_PASSWORD in .env.prod}"

APP_URL="https://co-founder-$(gcloud run services describe co-founder --region="$REGION" --format='value(status.url)' 2>/dev/null | sed 's|https://co-founder-||' || echo "placeholder").run.app"
APP_URL="$(gcloud run services describe co-founder --region="$REGION" --format='value(status.url)' 2>/dev/null || echo '')"
MOCK_URL="$(gcloud run services describe mock-portal --region="$REGION" --format='value(status.url)' 2>/dev/null || echo '')"

echo "==> Firestore (native mode, idempotent)"
gcloud firestore databases describe --database="(default)" >/dev/null 2>&1 \
  || gcloud firestore databases create --location="$REGION"

echo "==> Secrets (idempotent)"
for SECRET in mock-portal-creds portal-webhook-token; do
  gcloud secrets describe "$SECRET" >/dev/null 2>&1 || gcloud secrets create "$SECRET" --replication-policy=automatic
done

echo "==> Cloud SQL (sessions) — create is slow; runs once"
gcloud sql instances describe co-founder-sessions >/dev/null 2>&1 \
  || gcloud sql instances create co-founder-sessions --database-version=POSTGRES_16 --tier=db-f1-micro --region="$REGION"
gcloud sql databases describe adk_sessions --instance=co-founder-sessions >/dev/null 2>&1 \
  || gcloud sql databases create adk_sessions --instance=co-founder-sessions
gcloud sql users describe adk --instance=co-founder-sessions >/dev/null 2>&1 \
  || gcloud sql users create adk --instance=co-founder-sessions --password="$DB_PASSWORD"

echo "==> Artifact bucket"
gcloud storage buckets describe "gs://${GOOGLE_CLOUD_PROJECT}-artifacts" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://${GOOGLE_CLOUD_PROJECT}-artifacts" --location="$REGION"

echo "==> Pub/Sub topics"
for TOPIC in discovery-tick deadline-tick; do
  gcloud pubsub topics describe "$TOPIC" >/dev/null 2>&1 || gcloud pubsub topics create "$TOPIC"
done

echo "==> Deploy mock-portal"
gcloud run deploy mock-portal --source ./mock_portal --region="$REGION" \
  --allow-unauthenticated --min-instances 0 --max-instances 2
MOCK_URL="$(gcloud run services describe mock-portal --region="$REGION" --format='value(status.url)')"
echo "    mock portal: $MOCK_URL"

echo "==> Deploy co-founder (single browser-owning instance, docs/13)"
sed -i '' "s|^MOCK_PORTAL_URL=.*|MOCK_PORTAL_URL=$MOCK_URL|" .env.prod
gcloud run deploy co-founder --source . --region="$REGION" \
  --allow-unauthenticated --min-instances 0 --max-instances 1 --memory 2Gi \
  --add-cloudsql-instances "${GOOGLE_CLOUD_PROJECT}:${REGION}:co-founder-sessions" \
  --set-env-vars-from-file .env.prod
APP_URL="$(gcloud run services describe co-founder --region="$REGION" --format='value(status.url)')"
echo "    app: $APP_URL"

echo "==> Wire URLs both ways (mock portal webhooks → app; app → mock portal)"
sed -i '' "s|^AGENT_BASE_URL=.*|AGENT_BASE_URL=$APP_URL|" .env.prod
gcloud run services update co-founder --region="$REGION" \
  --update-env-vars "AGENT_BASE_URL=$APP_URL,MOCK_PORTAL_URL=$MOCK_URL"
gcloud run services update mock-portal --region="$REGION" \
  --update-env-vars "AGENT_BASE_URL=$APP_URL"

echo "==> Scheduler jobs (idempotent)"
gcloud scheduler jobs describe discovery-daily --location="$REGION" >/dev/null 2>&1 \
  || gcloud scheduler jobs create pubsub discovery-daily --location="$REGION" \
       --schedule="0 7 * * *" --topic=discovery-tick --message-body="{}"
gcloud scheduler jobs describe deadline-scan-6h --location="$REGION" >/dev/null 2>&1 \
  || gcloud scheduler jobs create pubsub deadline-scan-6h --location="$REGION" \
       --schedule="0 */6 * * *" --topic=deadline-tick --message-body="{}"

echo "==> Pub/Sub push subscriptions → app (OIDC, idempotent)"
SA="scheduler-invoker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create scheduler-invoker --display-name="Scheduler → Cloud Run invoker"
gcloud run services add-iam-policy-binding co-founder --region="$REGION" \
  --member="serviceAccount:$SA" --role=roles/run.invoker >/dev/null
gcloud pubsub subscriptions describe discovery-tick-push >/dev/null 2>&1 \
  || gcloud pubsub subscriptions create discovery-tick-push --topic=discovery-tick \
       --push-endpoint="$APP_URL/tasks/discover" \
       --push-oidc-service-account-email="$SA" \
       --push-oidc-token-audience="$APP_URL"
gcloud pubsub subscriptions describe deadline-tick-push >/dev/null 2>&1 \
  || gcloud pubsub subscriptions create deadline-tick-push --topic=deadline-tick \
       --push-endpoint="$APP_URL/webhooks/deadline" \
       --push-oidc-service-account-email="$SA" \
       --push-oidc-token-audience="$APP_URL"

echo "==> Done. Verify: docs/13 §verification checklist."
echo "    APP:  $APP_URL"
echo "    MOCK: $MOCK_URL"
