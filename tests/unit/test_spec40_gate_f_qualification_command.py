"""The Gate F command is preflight-only and fail-closed by default."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/run_spec40_gate_f_qualification.py"


def test_real_command_is_blocked_without_independent_review_and_calls_no_model():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert '"status": "BLOCKED"' in result.stdout
    assert "independent_review_complete" in result.stdout
    assert "READY" not in result.stdout


def test_output_path_cannot_escape_ignored_local_review_directory():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", "outside.json"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "must stay under .codex/spec40-gate-f" in result.stderr
