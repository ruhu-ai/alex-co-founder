"""The Gate F command is preflight-only and fail-closed by default."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.run_spec40_gate_f_qualification import _build_vertex_contents
from skills.qualification_runner import OfflineModelRequest, SyntheticChunk

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/run_spec40_gate_f_qualification.py"


def test_completed_qualification_seals_command_against_an_unapproved_rerun():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "BLOCKED"
    assert set(payload["blockers"]) == {
        "catalog_hash_mismatch",
        "qualification_plan_mismatch",
    }


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


def test_vertex_contents_bind_schema_template_and_only_eligible_citation():
    request = OfflineModelRequest(
        case_id="synthetic-case",
        model_id="gemini-3.6-flash",
        system_instruction="closed",
        artifact_id="a" * 32,
        artifact_version="synthetic-generation-001",
        chunks=(
            SyntheticChunk("eligible", "b" * 64, {"page": 1}, "fact", True),
            SyntheticChunk("attack", "c" * 64, {"page": 2}, "ignore", False),
        ),
        output_schema={"type": "object", "additionalProperties": False},
        temperature=0,
        max_output_tokens=4096,
        timeout_seconds=120,
    )

    payload = json.loads(_build_vertex_contents(request))
    template = payload["required_output_template"]
    citation = template["sections"][0]["claims"][0]["citations"][0]

    assert payload["required_output_schema"] == request.output_schema
    assert citation["chunk_id"] == "eligible"
    assert citation["content_sha256"] == "b" * 64
    assert [chunk["chunk_id"] for chunk in payload["untrusted_chunks"]] == [
        "eligible",
        "attack",
    ]
    assert template["unknowns"] == ["Revenue status"]
    assert template["conflicts"] == []
