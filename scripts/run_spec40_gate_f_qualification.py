"""Run the frozen Spec 40 Gate F evaluation only after every gate passes."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from skills.qualification_runner import (  # noqa: E402
    GateFQualificationRunner,
    OfflineModelRequest,
)

LOCAL_OUTPUT_ROOT = (REPO / ".codex/spec40-gate-f").resolve()


class VertexQualificationAdapter:
    """Exact project-scoped Vertex adapter with no tool or grounding surface."""

    def __init__(self, *, project_id: str, location: str) -> None:
        self.client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
        )

    def generate(self, request: OfflineModelRequest) -> dict:
        contents = json.dumps(
            {
                "artifact_id": request.artifact_id,
                "artifact_version": request.artifact_version,
                "chunks": [asdict(chunk) for chunk in request.chunks],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        response = self.client.models.generate_content(
            model=request.model_id,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=request.system_instruction,
                temperature=request.temperature,
                max_output_tokens=request.max_output_tokens,
                response_mime_type="application/json",
                response_json_schema=request.output_schema,
                tools=[],
                http_options=types.HttpOptions(
                    timeout=request.timeout_seconds * 1000,
                ),
            ),
        )
        payload = json.loads(response.text or "")
        if not isinstance(payload, dict):
            raise ValueError("provider_response_not_object")
        return payload


def _safe_output_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = REPO / path
    resolved = path.resolve()
    if resolved != LOCAL_OUTPUT_ROOT and LOCAL_OUTPUT_ROOT not in resolved.parents:
        raise argparse.ArgumentTypeError(
            "qualification output must stay under .codex/spec40-gate-f"
        )
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform bounded Vertex calls; default is preflight-only",
    )
    parser.add_argument(
        "--output",
        type=_safe_output_path,
        default=LOCAL_OUTPUT_ROOT / "qualification-result.json",
    )
    args = parser.parse_args()

    runner = GateFQualificationRunner(REPO)
    now = datetime.now(timezone.utc)
    blockers = runner.authorization_blockers(now=now)
    if blockers:
        print(json.dumps({"status": "BLOCKED", "blockers": blockers}))
        return 2
    if not args.execute:
        print(json.dumps({"status": "READY", "model_calls": 0}))
        return 0

    provider = json.loads(
        (REPO / "skills/approvals/spec40-gate-f-provider-preflight.json").read_text()
    )
    policy = json.loads((REPO / "skills/model_policies/spec40-gate-f-offline-v1.json").read_text())
    if provider["project_id"] != provider["quota_project_id"]:
        print(json.dumps({"status": "BLOCKED", "blockers": ["quota_project_drift"]}))
        return 2
    adapter = VertexQualificationAdapter(
        project_id=provider["project_id"],
        location=policy["location"],
    )
    result = runner.run(adapter, now=now)
    payload = asdict(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": result.status,
                "model_calls": result.model_calls,
                "output": str(args.output.relative_to(REPO)),
            }
        )
    )
    return 0 if result.status == "AWAITING_OUTPUT_REVIEW" else 1


if __name__ == "__main__":
    sys.exit(main())
