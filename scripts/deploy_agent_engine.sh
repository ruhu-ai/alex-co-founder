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

STAGE="$(mktemp -d)/stage"
mkdir -p "$STAGE"
rsync -a --exclude "__pycache__" --exclude ".adk" agents "$STAGE/"
rsync -a --exclude "__pycache__" services "$STAGE/"
rsync -a workflows "$STAGE/"
cp requirements.txt "$STAGE/"

cd "$STAGE"
"$ROOT/.venv/bin/adk" deploy agent_engine \
  --project="$GOOGLE_CLOUD_PROJECT" --region="$REGION" \
  --display_name="co-founder" agents/co_founder

echo "==> Agent Engine deploy complete (see console: Vertex AI → Agent Engine)"
