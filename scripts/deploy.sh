#!/usr/bin/env bash
# Cloud deploy (docs/13). Idempotent. Requires: gcloud auth, billing, setup.sh run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; source .env; set +a
: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT in .env}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"

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

echo "==> Artifact bucket"
gcloud storage buckets describe "gs://${GOOGLE_CLOUD_PROJECT}-artifacts" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://${GOOGLE_CLOUD_PROJECT}-artifacts" --location="$REGION"

echo "==> Pub/Sub topics"
for TOPIC in discovery-tick deadline-tick; do
  gcloud pubsub topics describe "$TOPIC" >/dev/null 2>&1 || gcloud pubsub topics create "$TOPIC"
done

echo "==> Deploy mock-portal"
gcloud run deploy mock-portal --source ./mock_portal --region="$REGION" \
  --allow-unauthenticated --min-instances 0

echo "==> Deploy co-founder"
gcloud run deploy co-founder --source . --region="$REGION" \
  --allow-unauthenticated --min-instances 0 --memory 2Gi \
  --add-cloudsql-instances "${GOOGLE_CLOUD_PROJECT}:${REGION}:co-founder-sessions" \
  --set-env-vars-from-file .env

echo "==> Scheduler jobs (idempotent)"
gcloud scheduler jobs describe discovery-daily --location="$REGION" >/dev/null 2>&1 \
  || gcloud scheduler jobs create pubsub discovery-daily --location="$REGION" \
       --schedule="0 7 * * *" --topic=discovery-tick --message-body="{}"
gcloud scheduler jobs describe deadline-scan-6h --location="$REGION" >/dev/null 2>&1 \
  || gcloud scheduler jobs create pubsub deadline-scan-6h --location="$REGION" \
       --schedule="0 */6 * * *" --topic=deadline-tick --message-body="{}"

echo "==> Done. Verify: docs/13 §verification checklist."
