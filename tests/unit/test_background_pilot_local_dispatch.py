from __future__ import annotations

import base64
import hashlib
import hmac
import json

from services import task_queue


class _Thread:
    def __init__(self, *, target, name, daemon):
        assert name.startswith("background-pilot-")
        assert daemon is True
        self.target = target

    def start(self):
        self.target()


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_local_dispatch_is_loopback_route_scoped_and_signed(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("BACKGROUND_PILOT_ALLOW_TEST_DISPATCH", "1")
    monkeypatch.setenv(
        "BACKGROUND_PILOT_TEST_DISPATCH_SECRET", "s" * 32)
    monkeypatch.setenv("AGENT_BASE_URL", "http://127.0.0.1:8092")
    monkeypatch.setattr(task_queue.threading, "Thread", _Thread)
    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout))
        return _Response()

    monkeypatch.setattr(task_queue.urllib.request, "urlopen", open_request)
    result = task_queue.enqueue(
        "/tasks/background-artifact-pilot",
        {"workspace_id": "workspace-pilot", "run_id": "run-001",
         "step_id": "step-001"}, "delivery-001",
        queue_name="co-founder-background-pilot",
        audience="http://127.0.0.1:8092/tasks/background-artifact-pilot")

    assert result == {"status": "success", "local_test_delivery": True}
    assert len(requests) == 1
    request, timeout = requests[0]
    assert timeout == 30
    assert request.full_url == (
        "http://127.0.0.1:8092/tasks/background-artifact-pilot")
    encoded = request.headers["X-background-pilot-test-principal"]
    signature = request.headers["X-background-pilot-test-signature"]
    assert signature == hmac.new(
        b"s" * 32, encoded.encode(), hashlib.sha256).hexdigest()
    claims = json.loads(base64.urlsafe_b64decode(
        encoded + "=" * (-len(encoded) % 4)))
    assert claims["audience"] == request.full_url
    assert claims["service_account"] == "local-background-pilot-worker"


def test_local_gate_f_dispatch_is_exactly_scoped_and_signed(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("BACKGROUND_PILOT_ALLOW_TEST_DISPATCH", "1")
    monkeypatch.setenv("BACKGROUND_PILOT_TEST_DISPATCH_SECRET", "s" * 32)
    monkeypatch.setenv("AGENT_BASE_URL", "http://127.0.0.1:8092")
    monkeypatch.setattr(task_queue.threading, "Thread", _Thread)
    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout))
        return _Response()

    monkeypatch.setattr(task_queue.urllib.request, "urlopen", open_request)
    path = "/tasks/background-artifact-grounded-brief"
    result = task_queue.enqueue(
        path, {"workspace_id": "workspace-pilot", "run_id": "run-001",
               "step_id": "step-001"}, "delivery-gate-f-001",
        queue_name="co-founder-background-skill-gate-f",
        audience=f"http://127.0.0.1:8092{path}")

    assert result == {"status": "success", "local_test_delivery": True}
    request, timeout = requests[0]
    assert timeout == 30
    assert request.full_url == f"http://127.0.0.1:8092{path}"
    encoded = request.headers["X-background-pilot-test-principal"]
    claims = json.loads(base64.urlsafe_b64decode(
        encoded + "=" * (-len(encoded) % 4)))
    assert claims["service_account"] == "local-background-skill-worker"
    assert claims["audience"] == request.full_url


def test_local_dispatch_refuses_non_loopback_and_other_routes(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("BACKGROUND_PILOT_ALLOW_TEST_DISPATCH", "1")
    monkeypatch.setenv(
        "BACKGROUND_PILOT_TEST_DISPATCH_SECRET", "s" * 32)
    monkeypatch.setenv("AGENT_BASE_URL", "https://attacker.invalid")

    refused = task_queue.enqueue(
        "/tasks/background-artifact-pilot", {}, "delivery-002",
        queue_name="co-founder-background-pilot",
        audience="https://attacker.invalid/tasks/background-artifact-pilot")
    unrelated = task_queue._enqueue_local_background_pilot(
        "/tasks/discover", {}, "delivery-003",
        queue_name="co-founder-discovery-ingestion",
        audience="https://attacker.invalid/tasks/discover", schedule_at=None)

    assert refused["error_code"] == "local_dispatch_target_denied"
    assert unrelated is None
