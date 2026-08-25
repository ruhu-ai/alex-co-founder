"""Make ADK eval result JSON enforceable as a CI exit status."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PASSED = {1, "PASSED"}


def failed_case_ids(payload: dict) -> list[str]:
    """Return failed/malformed case ids; an empty result is itself a failure."""
    cases = payload.get("eval_case_results") or []
    if not cases:
        return ["<no eval case results>"]
    return [
        case.get("eval_id") or "<unknown eval case>"
        for case in cases
        if case.get("final_eval_status") not in PASSED
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    args = parser.parse_args()

    try:
        payload = json.loads(args.result.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ADK eval result is unreadable: {exc}")
        return 1

    failures = failed_case_ids(payload)
    if failures:
        print("ADK eval failures: " + ", ".join(failures))
        return 1
    print(f"ADK eval passed: {len(payload['eval_case_results'])} case(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
