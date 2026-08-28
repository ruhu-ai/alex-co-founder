#!/usr/bin/env bash
# Deterministic local Phase 7 gate. Production recovery evidence is separate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTEST="$ROOT/.venv/bin/pytest"
RUFF="$ROOT/.venv/bin/ruff"
[[ -x "$PYTEST" && -x "$RUFF" ]] \
  || { echo "missing locked .venv; run scripts/setup.sh"; exit 1; }

"$RUFF" check services/platform_operations.py scripts/platform_ops.py \
  scripts/phase7_cloud_audit.py scripts/configure_phase7_monitoring.py \
  tests/unit/test_phase7_operations.py tests/unit/test_phase7_monitoring.py
"$PYTEST" -q \
  tests/unit/test_command_service.py \
  tests/unit/test_workflow_runtime_platform.py \
  tests/unit/test_consequence_service.py \
  tests/unit/test_workflow_timer_service.py \
  tests/unit/test_projection_stream.py \
  tests/unit/test_phase7_operations.py \
  tests/unit/test_phase7_monitoring.py \
  tests/unit/test_phase7_cloud_audit.py \
  tests/unit/test_collection_registry.py
python -m compileall -q services scripts
git diff --check
echo "Phase 7 local implementation gate passed."
echo "Production completion still requires docs/35 recovery/load/chaos evidence."
