#!/usr/bin/env bash
# Start the complete local stack with one canonical founder-app origin. OAuth
# callbacks, Cloud Tasks targets, and the mock portal all use AGENT_BASE_URL,
# so deriving it here prevents a command-line port and callback port from
# drifting apart.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# This is deliberately not configurable. Google registers an exact callback
# URI, and allowing an ad-hoc port here is how the connection flow drifted.
HOST="127.0.0.1"
PORT="8090"
PORTAL_PORT="8091"
START_PORTAL=true
RELOAD=false
VENV_DIR="${LOCAL_VENV_DIR:-.venv}"

usage() {
  cat <<'EOF'
Usage: ./scripts/run_local.sh [--reload] [--app-only]

  --reload    Restart both services when source files change (development only).
  --app-only  Start only the founder app; use when the portal runs elsewhere.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reload) RELOAD=true ;;
    --app-only) START_PORTAL=false ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ ! -x "$VENV_DIR/bin/uvicorn" ]]; then
  echo "Missing ${VENV_DIR}/bin/uvicorn. Run ./scripts/setup.sh first." >&2
  exit 2
fi
if ! "$VENV_DIR/bin/python" -c \
    'from cryptography.hazmat.backends.openssl.backend import backend; backend.openssl_version_text()' \
    >/dev/null 2>&1; then
  echo "The local cryptography/OpenSSL installation is incompatible." >&2
  echo "Repair pinned dependencies with: uv pip install --python .venv/bin/python -r requirements-dev.txt" >&2
  exit 2
fi
if ! command -v gcloud >/dev/null; then
  echo "Google Cloud CLI is required by the local app but is unavailable." >&2
  echo "Install gcloud, then run: gcloud auth application-default login" >&2
  exit 2
elif ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
  echo "Application Default Credentials are missing or expired." >&2
  echo "Run: gcloud auth application-default login" >&2
  echo "Then start the local stack again." >&2
  exit 2
fi

# Load non-secret local settings if present, then deliberately make the app
# origin authoritative for this process. An explicit file lets an isolated
# clean-main checkout reuse the developer's local settings without copying or
# mutating them. Do not edit either file at runtime.
LOCAL_ENV_FILE="${LOCAL_ENV_FILE:-.env}"
if [[ "$LOCAL_ENV_FILE" != /* ]]; then
  LOCAL_ENV_FILE="$ROOT/$LOCAL_ENV_FILE"
fi
export LOCAL_ENV_FILE
if [[ -f "$LOCAL_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$LOCAL_ENV_FILE"
  set +a
fi
export AGENT_BASE_URL="http://${HOST}:${PORT}"
export MOCK_PORTAL_URL="http://${HOST}:${PORTAL_PORT}"

port_must_be_free() {
  local port="$1"
  local service="$2"
  if command -v lsof >/dev/null && lsof -nP -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port ${port} is already in use; stop the existing ${service} first." >&2
    exit 2
  fi
}

port_must_be_free "$PORT" "founder app"
if [[ "$START_PORTAL" == true ]]; then
  port_must_be_free "$PORTAL_PORT" "mock portal"
fi

if [[ "$RELOAD" == true ]]; then
  echo "Hot reload enabled; active voice and OAuth requests may be interrupted by file changes."
fi

run_uvicorn() {
  if [[ "$RELOAD" == true ]]; then
    "$VENV_DIR/bin/uvicorn" "$@" --reload
  else
    "$VENV_DIR/bin/uvicorn" "$@"
  fi
}

APP_PID=""
PORTAL_PID=""
MAIL_SUBSCRIBER_PID=""

cleanup() {
  trap - EXIT INT TERM
  local pids=()
  [[ -n "$APP_PID" ]] && pids+=("$APP_PID")
  [[ -n "$PORTAL_PID" ]] && pids+=("$PORTAL_PID")
  [[ -n "$MAIL_SUBSCRIBER_PID" ]] && pids+=("$MAIL_SUBSCRIBER_PID")
  if [[ ${#pids[@]} -gt 0 ]]; then
    kill -TERM "${pids[@]}" >/dev/null 2>&1 || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

wait_for_health() {
  local name="$1"
  local url="$2"
  local pid="$3"
  local attempts="${LOCAL_STARTUP_HEALTH_ATTEMPTS:-120}"
  local attempt

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "${name} exited before becoming healthy." >&2
      wait "$pid" || true
      return 1
    fi
    if "$VENV_DIR/bin/python" - "$url" >/dev/null 2>&1 <<'PY'
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=0.5) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep 0.5
  done

  echo "${name} did not become healthy at ${url} within $(((attempts + 1) / 2)) seconds." >&2
  return 1
}

if [[ "$START_PORTAL" == true ]]; then
  run_uvicorn mock_portal.main:app --host "$HOST" --port "$PORTAL_PORT" &
  PORTAL_PID=$!
  wait_for_health "Mock portal" "${MOCK_PORTAL_URL}/healthz" "$PORTAL_PID"
fi

run_uvicorn app.main:app --host "$HOST" --port "$PORT" &
APP_PID=$!
wait_for_health "Founder app" "${AGENT_BASE_URL}/health" "$APP_PID"

if [[ -n "${ALEX_MAIL_LOCAL_SUBSCRIPTION:-}" ]]; then
  "$VENV_DIR/bin/python" scripts/alex_mail_local_subscriber.py &
  MAIL_SUBSCRIBER_PID=$!
  sleep 0.5
  if ! kill -0 "$MAIL_SUBSCRIBER_PID" >/dev/null 2>&1; then
    echo "Alex Mail local event subscriber failed to start." >&2
    wait "$MAIL_SUBSCRIBER_PID" || true
    exit 1
  fi
fi

echo
echo "Local stack is ready:"
echo "  Founder app: ${AGENT_BASE_URL}"
if [[ "$START_PORTAL" == true ]]; then
  echo "  Mock portal: ${MOCK_PORTAL_URL}"
fi
if [[ -n "$MAIL_SUBSCRIBER_PID" ]]; then
  echo "  Alex Mail: automatic Pub/Sub wake enabled"
fi
echo "Press Ctrl-C to stop."

# Bash 3.2 (still shipped by macOS) has no `wait -n`, so monitor both children
# and make either process exiting a failure of the complete local stack.
while kill -0 "$APP_PID" >/dev/null 2>&1; do
  if [[ -n "$PORTAL_PID" ]] && ! kill -0 "$PORTAL_PID" >/dev/null 2>&1; then
    echo "Mock portal stopped; shutting down the local stack." >&2
    exit 1
  fi
  if [[ -n "$MAIL_SUBSCRIBER_PID" ]] && ! kill -0 "$MAIL_SUBSCRIBER_PID" >/dev/null 2>&1; then
    echo "Alex Mail local event subscriber stopped; shutting down the local stack." >&2
    exit 1
  fi
  sleep 1
done

echo "Founder app stopped; shutting down the local stack." >&2
exit 1
