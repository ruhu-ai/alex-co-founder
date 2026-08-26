#!/usr/bin/env bash
# Deploy the agent to Vertex Agent Engine (docs/19 §P0.2). Idempotent.
#
# Trap this script exists to solve: the ADK packager uploads the agent folder
# inline and Agent Engine rejects payloads > 8 MB — our agents/ tree carries
# .adk/ eval history + __pycache__. Deploy from a clean staging copy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; source .env; set +a
: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT in .env}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"

# Reuse the one judging deployment instead of silently creating another
# billable/runtime resource on every invocation. An explicit ID wins; otherwise
# discover the unique engine with this display name.
ENGINE_ID="${AGENT_ENGINE_ID:-}"
if [[ -z "$ENGINE_ID" ]]; then
  ACCESS_TOKEN="$(gcloud auth print-access-token)"
  ENGINE_ID="$(curl -fsS \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    "https://${REGION}-aiplatform.googleapis.com/v1/projects/${GOOGLE_CLOUD_PROJECT}/locations/${REGION}/reasoningEngines" \
    | python3 -c '
import json, sys
matches = [item["name"].rsplit("/", 1)[-1]
           for item in json.load(sys.stdin).get("reasoningEngines", [])
           if item.get("displayName") == "co-founder"]
if len(matches) > 1:
    raise SystemExit("multiple co-founder Agent Engines found; set AGENT_ENGINE_ID")
print(matches[0] if matches else "")')"
fi

STAGE="$(mktemp -d)/stage"
mkdir -p "$STAGE"
rsync -a --exclude "__pycache__" --exclude ".adk" agents "$STAGE/"
rsync -a --exclude "__pycache__" services "$STAGE/"
rsync -a workflows "$STAGE/"
cp requirements.txt "$STAGE/"

cd "$STAGE"
DEPLOY_ARGS=(
  --project="$GOOGLE_CLOUD_PROJECT"
  --region="$REGION"
  --display_name="co-founder"
)
if [[ -n "$ENGINE_ID" ]]; then
  DEPLOY_ARGS+=(--agent_engine_id="$ENGINE_ID")
fi
"$ROOT/.venv/bin/adk" deploy agent_engine "${DEPLOY_ARGS[@]}" agents/co_founder

echo "==> Agent Engine deploy complete (see console: Vertex AI → Agent Engine)"
