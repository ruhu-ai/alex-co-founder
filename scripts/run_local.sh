#!/usr/bin/env bash
# Start the founder app with one canonical local origin.  OAuth callbacks,
# Cloud Tasks targets, and the mock portal all use AGENT_BASE_URL, so deriving
# it here prevents a command-line port and callback port from drifting apart.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# This is deliberately not configurable. Google registers an exact callback
# URI, and allowing an ad-hoc port here is how the connection flow drifted.
HOST="127.0.0.1"
PORT="8090"
if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "Missing .venv/bin/uvicorn. Run ./scripts/setup.sh first." >&2
  exit 2
fi
if ! command -v gcloud >/dev/null; then
  echo "Warning: Google Cloud CLI is unavailable; cloud-backed operations are disabled." >&2
elif ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
  echo "Warning: Application Default Credentials are missing or expired." >&2
  echo "Cloud-backed operations will be unavailable until you run:" >&2
  echo "  gcloud auth application-default login --no-launch-browser" >&2
fi
if command -v lsof >/dev/null && lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port ${PORT} is already in use. Stop the existing local app before starting Co-Founder." >&2
  exit 2
fi

# Load non-secret local settings if present, then deliberately make the app
# origin authoritative for this process. Do not edit .env at runtime.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
export AGENT_BASE_URL="http://${HOST}:${PORT}"

echo "Starting Co-Founder at ${AGENT_BASE_URL}"
exec .venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
