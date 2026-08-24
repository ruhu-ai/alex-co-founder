#!/usr/bin/env bash
# Co-Founder — idempotent environment setup (docs/01, docs/13).
# Safe to run any number of times: every step checks before it acts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> [1/7] Python >= 3.11"
python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    sys.exit("Python 3.11+ required; found " + sys.version.split()[0])
print("    python", sys.version.split()[0])
PY

echo "==> [2/7] Virtualenv + dependencies"
if [ ! -d .venv ]; then
  uv venv .venv
fi
uv pip install --python .venv/bin/python -r requirements-dev.txt

echo "==> [3/7] Google Cloud project"
ENV_FILE=.env
touch "$ENV_FILE"
CURRENT_PROJECT="$(grep -E '^GOOGLE_CLOUD_PROJECT=' "$ENV_FILE" | cut -d= -f2 || true)"
if [ -z "${CURRENT_PROJECT}" ] || [ "${CURRENT_PROJECT}" = "your-project-id" ]; then
  DEFAULT_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
  read -r -p "Google Cloud project id [${DEFAULT_PROJECT}]: " INPUT_PROJECT
  PROJECT_ID="${INPUT_PROJECT:-$DEFAULT_PROJECT}"
  gcloud projects describe "$PROJECT_ID" >/dev/null   # fails loudly if wrong
  if grep -q '^GOOGLE_CLOUD_PROJECT=' "$ENV_FILE"; then
    sed -i '' "s|^GOOGLE_CLOUD_PROJECT=.*|GOOGLE_CLOUD_PROJECT=$PROJECT_ID|" "$ENV_FILE"
  else
    echo "GOOGLE_CLOUD_PROJECT=$PROJECT_ID" >> "$ENV_FILE"
  fi
else
  PROJECT_ID="$CURRENT_PROJECT"
fi
gcloud config set project "$PROJECT_ID" >/dev/null
echo "    project: $PROJECT_ID"

echo "==> [4/7] Application Default Credentials"
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
  echo "    no ADC found — starting login flow (browser)..."
  gcloud auth application-default login
else
  echo "    ADC present"
fi

echo "==> [5/7] Enable APIs (skips already-enabled)"
APIS="run firestore sqladmin pubsub cloudscheduler secretmanager storage cloudbuild aiplatform cloudtrace logging"
for API in $APIS; do
  if gcloud services list --enabled --filter="name:${API}.googleapis.com" --format="value(name)" 2>/dev/null | grep -q .; then
    echo "    $API already enabled"
  else
    gcloud services enable "${API}.googleapis.com" >/dev/null
    echo "    $API enabled"
  fi
done

echo "==> [6/7] .env values"
grep -q '^GOOGLE_GENAI_USE_VERTEXAI=' "$ENV_FILE" || echo "GOOGLE_GENAI_USE_VERTEXAI=True" >> "$ENV_FILE"
grep -q '^GOOGLE_CLOUD_REGION=' "$ENV_FILE"      || echo "GOOGLE_CLOUD_REGION=us-central1" >> "$ENV_FILE"
grep -q '^GOOGLE_CLOUD_LOCATION=' "$ENV_FILE"    || echo "GOOGLE_CLOUD_LOCATION=global" >> "$ENV_FILE"
grep -q '^ADK_MODEL=' "$ENV_FILE"                || echo "ADK_MODEL=gemini-3.5-flash" >> "$ENV_FILE"
grep -q '^SESSION_SERVICE_URI=' "$ENV_FILE"      || echo "SESSION_SERVICE_URI=sqlite+aiosqlite:///sessions.db" >> "$ENV_FILE"
grep -q '^ARTIFACT_SERVICE_URI=' "$ENV_FILE"     || echo "ARTIFACT_SERVICE_URI=file://./artifacts" >> "$ENV_FILE"
grep -q '^WORKFLOW_FILE=' "$ENV_FILE"            || echo "WORKFLOW_FILE=workflows/grant_applications.yaml" >> "$ENV_FILE"
grep -q '^AGENT_BASE_URL=' "$ENV_FILE"           || echo "AGENT_BASE_URL=http://127.0.0.1:8090" >> "$ENV_FILE"
grep -q '^MOCK_PORTAL_URL=' "$ENV_FILE"          || echo "MOCK_PORTAL_URL=http://127.0.0.1:8091" >> "$ENV_FILE"
grep -q '^APPROVAL_TTL_MINUTES=' "$ENV_FILE"     || echo "APPROVAL_TTL_MINUTES=30" >> "$ENV_FILE"
echo "    .env complete"

echo "==> [7/7] Model check (one real Gemini call)"
set -a; source "$ENV_FILE"; set +a
.venv/bin/python - <<'PY' || echo "    WARN: model check failed — verify credentials/project/model id before Day 2"
import os
from google import genai
client = genai.Client(
    vertexai=True,
    project=os.environ["GOOGLE_CLOUD_PROJECT"],
    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
)
r = client.models.generate_content(
    model=os.environ.get("ADK_MODEL", "gemini-3.5-flash"),
    contents="Reply with the single word: ok",
)
print("    model check:", r.text.strip()[:20])
PY

echo
echo "Setup complete. Next:"
echo "  source .venv/bin/activate"
echo "  adk web agents --port 8000 --session_service_uri=\"sqlite+aiosqlite:///sessions.db\" --artifact_service_uri=\"file://./artifacts\""
