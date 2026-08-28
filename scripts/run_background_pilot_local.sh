#!/usr/bin/env bash
# Run the one off-cloud Spec-40 synthetic pilot. Ctrl-C deletes all state.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="127.0.0.1"
PORT="8092"
UVICORN_BIN="${UVICORN_BIN:-.venv/bin/uvicorn}"
founder_key="${BACKGROUND_PILOT_LOCAL_FOUNDER_KEY:-}"
worker_secret="${BACKGROUND_PILOT_TEST_DISPATCH_SECRET:-}"

if [[ ${#founder_key} -lt 32 ]]; then
  echo "Set BACKGROUND_PILOT_LOCAL_FOUNDER_KEY to at least 32 characters." >&2
  exit 2
fi
if [[ ${#worker_secret} -lt 32 ]]; then
  echo "Set BACKGROUND_PILOT_TEST_DISPATCH_SECRET to at least 32 characters." >&2
  exit 2
fi
if [[ ! -x "$UVICORN_BIN" ]]; then
  echo "uvicorn not found at $UVICORN_BIN; set UVICORN_BIN explicitly." >&2
  exit 2
fi
if command -v lsof >/dev/null \
    && lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use." >&2
  exit 2
fi

# Do not inherit cloud authority into this synthetic host.
unset K_SERVICE GOOGLE_CLOUD_PROJECT GOOGLE_APPLICATION_CREDENTIALS \
  SESSION_SERVICE_URI

export AGENT_BASE_URL="http://${HOST}:${PORT}"
export BACKGROUND_JOB_ADMISSION_ENABLED=true
export BACKGROUND_SPECIALIST_EXECUTION_ENABLED=true
export BACKGROUND_CONVERSATION_DELIVERY_ENABLED=false
export BACKGROUND_ARTIFACT_PILOT_ENABLED=true
export BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED=true
export BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH=false
export BACKGROUND_ARTIFACT_PILOT_WORKSPACES=local_spec40_workspace
export BACKGROUND_PILOT_ALLOW_TEST_DISPATCH=1
export BACKGROUND_PILOT_LOCAL_DISPATCH_DELAY_MS=200
export BACKGROUND_PILOT_LOCAL_INSPECTION_DELAY_MS=3000

# Broader background, effect, retrieval, hiring, and memory paths stay off.
export SESSION_SERIALIZER_ENABLED=false
export BACKGROUND_JOB_SSE_ENABLED=false
export BACKGROUND_SKILLS_ENABLED=false
export BROWSE_OPEN_WEB=false
export DISCOVER_COMMAND_ENABLED=false
export DURABLE_BRIEF_M1_ENABLED=false
export HIRING_ENABLE_INTERNAL_CONTROLLED_DEMO=false
export HIRING_ENABLE_SYNTHETIC_DEMO=false
export INVESTOR_OUTREACH_ENABLED=false
export PERSISTENT_MEMORY_BACKEND=disabled

exec "$UVICORN_BIN" services.background_pilot_local_app:app \
  --host "$HOST" --port "$PORT"
