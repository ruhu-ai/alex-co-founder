#!/usr/bin/env python3
"""Exercise success, retry/recovery, visibility, and cancellation fencing."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get(
    "BACKGROUND_PILOT_LOCAL_URL", "http://127.0.0.1:8092").rstrip("/")
FOUNDER_KEY = os.environ.get("BACKGROUND_PILOT_LOCAL_FOUNDER_KEY", "")
EXISTING_COMPLETED_RUN_ID = os.environ.get(
    "BACKGROUND_PILOT_EXISTING_COMPLETED_RUN_ID", "")
SESSION_ID = "local_spec40_session"
ARTIFACT_ID = "40" * 16


def _request(path: str, *, method: str = "GET", body: dict | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, dict]:
    payload = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=payload, method=method,
        headers={"X-Local-Founder-Key": FOUNDER_KEY,
                 **({"Content-Type": "application/json"} if body else {}),
                 **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def _start(client_request_id: str) -> dict:
    status, result = _request(
        "/api/v1/background-pilot/artifact-analysis", method="POST",
        headers={"Idempotency-Key": client_request_id}, body={
            "session_id": SESSION_ID, "artifact_id": ARTIFACT_ID,
            "client_request_id": client_request_id})
    if status != 202:
        raise RuntimeError(f"admission failed safely: {status} {result}")
    return result


def _job(run_id: str) -> dict:
    status, result = _request(f"/api/v1/background-pilot/jobs/{run_id}")
    if status != 200:
        raise RuntimeError(f"job read failed: {status} {result}")
    return result["job"]


def _timeline(run_id: str) -> list[dict]:
    status, result = _request(
        f"/api/v1/background-pilot/jobs/{run_id}/timeline")
    if status != 200:
        raise RuntimeError(f"timeline read failed: {status} {result}")
    return result["messages"]


def _wait_terminal(run_id: str) -> tuple[dict, list[str]]:
    statuses = [_job(run_id)["runtime_status"]]
    deadline = time.monotonic() + 10
    job = _job(run_id)
    while job["runtime_status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        if time.monotonic() >= deadline:
            raise RuntimeError(f"terminal status timed out: {run_id}")
        status = str(job["runtime_status"])
        if status != statuses[-1]:
            statuses.append(status)
        time.sleep(0.025)
        job = _job(run_id)
    if job["runtime_status"] != statuses[-1]:
        statuses.append(str(job["runtime_status"]))
    return job, statuses


def main() -> int:
    if len(FOUNDER_KEY) < 32:
        raise RuntimeError("BACKGROUND_PILOT_LOCAL_FOUNDER_KEY is required")

    completed = ({"run_id": EXISTING_COMPLETED_RUN_ID}
                 if EXISTING_COMPLETED_RUN_ID
                 else _start("local-spec40-complete-001"))
    if EXISTING_COMPLETED_RUN_ID:
        completed_job = _job(EXISTING_COMPLETED_RUN_ID)
        completed_timeline = _timeline(EXISTING_COMPLETED_RUN_ID)
        completed_statuses = []
        for row in completed_timeline:
            status = str(row["runtime_status"])
            if not completed_statuses or completed_statuses[-1] != status:
                completed_statuses.append(status)
    else:
        completed_job, completed_statuses = _wait_terminal(
            str(completed["run_id"]))
        completed_timeline = _timeline(str(completed["run_id"]))
    if completed_job["runtime_status"] != "SUCCEEDED":
        raise RuntimeError(f"pilot failed: {completed_job}")

    arm_status, arm_result = _request("/local-test/retry-next", method="POST")
    if arm_status != 200:
        raise RuntimeError(f"retry injection could not be armed: {arm_result}")
    recovered = _start("local-spec40-recovery-001")
    recovered_job, recovered_statuses = _wait_terminal(str(recovered["run_id"]))
    recovered_timeline = _timeline(str(recovered["run_id"]))
    if recovered_job["runtime_status"] != "SUCCEEDED":
        raise RuntimeError(f"retry did not recover safely: {recovered_job}")
    if not any(row.get("caption") ==
               "I hit a temporary problem and will retry safely."
               for row in recovered_timeline):
        raise RuntimeError("authoritative retry progress was not visible")

    cancelling = _start("local-spec40-cancel-001")
    cancelling_job = _job(str(cancelling["run_id"]))
    cancel_key = "local-spec40-cancel-command-001"
    cancel_status, cancel_result = _request(
        f"/api/v1/background-pilot/jobs/{cancelling['run_id']}:cancel",
        method="POST", headers={
            "Idempotency-Key": cancel_key,
            "If-Match": str(cancelling_job["version"])},
        body={"client_request_id": cancel_key,
              "reason": "Synthetic cancellation safety check"})
    if cancel_status != 200:
        raise RuntimeError(f"cancellation failed: {cancel_status} {cancel_result}")
    time.sleep(0.4)
    cancelled_job = _job(str(cancelling["run_id"]))
    cancelled_timeline = _timeline(str(cancelling["run_id"]))

    jobs_status, durable_jobs = _request(
        f"/api/v1/background-pilot/jobs?session_id={SESSION_ID}")
    if jobs_status != 200 or len(durable_jobs.get("jobs", [])) != 3:
        raise RuntimeError(f"durable Activity list is incomplete: {durable_jobs}")

    state_status, state = _request("/local-test/state")
    if (state_status != 200 or not state.get("output_content_free")
            or any(state.get("forbidden_collection_counts", {}).values())):
        raise RuntimeError(f"safety state invalid: {state_status} {state}")
    if cancelled_job["runtime_status"] != "CANCELLED":
        raise RuntimeError(f"cancellation did not fence the run: {cancelled_job}")
    if (state.get("injected_retry_failures") != 1
            or state.get("failed_attempt_count") != 1
            or state.get("retry_failure_armed")):
        raise RuntimeError(f"retry accounting is invalid: {state}")

    print(json.dumps({
        "status": "success",
        "synthetic_state": {
            key: state[key] for key in (
                "workspace_id", "actor_id", "role", "session_id",
                "artifact_id", "source_kind", "source_bytes", "source_chunks")},
        "completed_run": {
            "run_id": completed["run_id"],
            "observed_activity_statuses": completed_statuses,
            "timeline_statuses": [row["runtime_status"]
                                  for row in completed_timeline],
            "output": completed_job["output"],
            "output_content_free": state["output_content_free"],
        },
        "recovered_run": {
            "run_id": recovered["run_id"],
            "observed_activity_statuses": recovered_statuses,
            "timeline_statuses": [row["runtime_status"]
                                  for row in recovered_timeline],
            "timeline_captions": [row["caption"]
                                  for row in recovered_timeline],
            "output": recovered_job["output"],
            "failed_attempts": state["failed_attempt_count"],
            "injected_retry_failures": state["injected_retry_failures"],
        },
        "cancelled_run": {
            "run_id": cancelling["run_id"],
            "runtime_status": cancelled_job["runtime_status"],
            "timeline_statuses": [row["runtime_status"]
                                  for row in cancelled_timeline],
        },
        "durable_activity": {
            "job_count": len(durable_jobs["jobs"]),
            "runtime_statuses": sorted(
                job["runtime_status"] for job in durable_jobs["jobs"]),
            "attempt_count": state["attempt_count"],
            "output_count": state["output_count"],
        },
        "forbidden_collection_counts": state["forbidden_collection_counts"],
        "active_flags": state["flags"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
