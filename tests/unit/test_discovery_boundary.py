"""Founder-facing discovery boundary (docs/23 §6.2, WI-3).

POST /api/discovery-requests durably persists receipt + resource + session
link before dispatch; /tasks/discover is an internal worker that loads
authority from the receipt; completion delivery is origin-bound, never the
latest-session fallback.
"""

import pytest

pytestmark = pytest.mark.usefixtures("fake_store")


@pytest.fixture(scope="module")
def appmod():
    import os

    os.environ.pop("APP_AUTH_TOKEN", None)
    os.environ.pop("K_SERVICE", None)
    import app.main as m
    from services import discovery_service, voice_service

    discovery_service.set_search_fn(None)
    discovery_service.set_extract_fn(None)
    discovery_service.set_pdf_extract_fn(None)
    voice_service.set_transcribe_fn(None)
    return m


@pytest.fixture()
def client(appmod):
    from fastapi.testclient import TestClient

    return TestClient(appmod.app)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # app.main loads the developer's .env at import; keep the founder gate and
    # dispatch mode deterministic (same rule as test_app_server).
    monkeypatch.delenv("APP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("FIREBASE_WEB_API_KEY", raising=False)


@pytest.fixture()
def known_sessions(appmod, monkeypatch):
    """Sessions the founder owns; mutable so a test can delete one mid-flow."""
    sessions = {"s-origin", "s-latest"}

    async def _exists(session_id):
        return session_id in sessions

    async def _workspace_exists(_workspace_id, session_id):
        return session_id in sessions

    monkeypatch.setattr(appmod, "_founder_session_exists", _exists)
    monkeypatch.setattr(appmod, "_workspace_session_exists", _workspace_exists)
    return sessions


@pytest.fixture()
def quiet_worker(appmod, monkeypatch, fake_store):
    """Stub the sweep/score pipeline; capture notices and sweep calls."""
    calls = {"sweeps": [], "notices": []}

    async def _sweep(workflow, founder_id, context=None):
        calls["sweeps"].append((founder_id, context))
        return {"status": "success", "new": 1, "errors": [],
                "opportunity_ids": list(fake_store.opportunities),
                "executed_queries": [
                    {"query_id": "q1", "text": "grant programs lagos fintech",
                     "provider": "web_search", "status": "ok",
                     "result_count": 3}]}

    async def _unscored(limit=10, founder_id=""):
        return []

    async def _board(founder_id):
        return {"status": "success",
                "opportunities": {"SHORTLISTED": []}, "applications": []}

    async def _notify(notice, session_id=None, **_kwargs):
        calls["notices"].append((notice, session_id))

    monkeypatch.setattr(appmod.discovery_service, "run_sweep", _sweep)
    monkeypatch.setattr(appmod.firestore, "list_unscored_opportunities",
                        _unscored)
    monkeypatch.setattr(appmod.pipeline_service, "board", _board)
    monkeypatch.setattr(appmod, "_notify_founder", _notify)
    return calls


def _post(client, request_id="req_boundary1", session_id="s-origin",
          context="African AI accelerators"):
    return client.post("/api/discovery-requests", json={
        "session_id": session_id, "client_request_id": request_id,
        "context": context})


class TestAcceptance:
    def test_accept_persists_receipt_resource_and_link_before_work(
            self, client, fake_store, known_sessions, quiet_worker):
        response = _post(client)
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "accepted" and body["duplicate"] is False
        assert body["discovery_request_id"] and body["resource_id"]

        receipt = fake_store.discovery_requests[body["discovery_request_id"]]
        assert receipt["origin_session_id"] == "s-origin"
        assert receipt["display_query"] == "African AI accelerators"
        assert receipt["resource_id"] == body["resource_id"]

        resource = fake_store.resource_index[body["resource_id"]]
        assert resource["resource_type"] == "discovery_request"
        links = list(fake_store.session_resource_links.values())
        assert len(links) == 1
        assert links[0]["session_id"] == "s-origin"
        assert links[0]["relationship"] == "created"
        assert links[0]["occurrence_key"] == "req_boundary1"

    def test_duplicate_returns_same_ids_and_runs_once(
            self, client, fake_store, known_sessions, quiet_worker):
        first = _post(client).json()
        second = _post(client).json()
        assert second["duplicate"] is True
        assert second["discovery_request_id"] == first["discovery_request_id"]
        assert second["resource_id"] == first["resource_id"]
        assert len(quiet_worker["sweeps"]) == 1
        assert len(fake_store.session_resource_links) == 1

    def test_context_conflict_launches_nothing(
            self, client, fake_store, known_sessions, quiet_worker):
        _post(client, context="fintech grants")
        conflict = _post(client, context="something else entirely")
        assert conflict.status_code == 409
        assert len(quiet_worker["sweeps"]) == 1

    def test_unknown_session_is_generic_not_found(
            self, client, fake_store, known_sessions, quiet_worker):
        response = _post(client, session_id="s-foreign")
        assert response.status_code == 404
        assert response.json()["message"] == "not found"
        assert not fake_store.discovery_requests
        assert not fake_store.resource_index

    def test_invalid_request_id_rejected(self, client, known_sessions,
                                         quiet_worker):
        assert _post(client, request_id="x").status_code == 400


class TestReceiptLifecycle:
    async def test_worker_claim_preserves_acceptance_fields(
            self, fake_store, appmod, known_sessions, quiet_worker, client):
        body = _post(client, request_id="req_claimkeep").json()
        from services import firestore

        # Simulate a fresh worker claim (redelivery from another process).
        fake_store.discovery_requests[body["discovery_request_id"]]["status"] \
            = "ACCEPTED"
        claim = await firestore.claim_discovery_request(
            "req_claimkeep", appmod.FOUNDER_ID,
            fake_store.discovery_requests[
                body["discovery_request_id"]]["context_hash"])
        assert claim["claimed"] is True
        receipt = fake_store.discovery_requests[body["discovery_request_id"]]
        assert receipt["origin_session_id"] == "s-origin"
        assert receipt["display_query"] == "African AI accelerators"
        assert receipt["resource_id"] == body["resource_id"]


class TestOriginBoundDelivery:
    def test_completion_targets_origin_even_when_founder_moved_on(
            self, client, appmod, fake_store, known_sessions, quiet_worker):
        # The founder's "latest" session is s-latest; the request originated
        # in s-origin. The notice must land in s-origin.
        appmod._founder_session_id = "s-latest"
        _post(client)
        assert quiet_worker["notices"], "completion notice expected"
        assert all(target == "s-origin"
                   for _, target in quiet_worker["notices"])

    def test_missing_origin_gets_no_fabricated_wake(
            self, client, fake_store, known_sessions, quiet_worker):
        accepted = _post(client).json()
        assert accepted["status"] == "accepted"
        # Origin session disappears; redeliver the worker task.
        known_sessions.discard("s-origin")
        fake_store.discovery_requests[
            accepted["discovery_request_id"]]["status"] = "ACCEPTED"
        quiet_worker["notices"].clear()
        response = client.post("/tasks/discover", json={
            "discovery_request_id": accepted["discovery_request_id"]})
        assert response.status_code == 200
        assert quiet_worker["notices"] == []


class TestDiscoveredLinks:
    def test_opportunities_link_to_the_requesting_session(
            self, client, fake_store, known_sessions, quiet_worker):
        fake_store.opportunities["opp_1"] = {
            "id": "opp_1", "name": "Lagos AI Accelerator",
            "description": "Accelerator for African AI startups",
            "state": "DISCOVERED", "workspace_id": "founder",
            "founder_id": "founder"}
        body = _post(client).json()

        receipt = fake_store.discovery_requests[body["discovery_request_id"]]
        assert receipt["result_opportunity_ids"] == ["opp_1"]
        assert receipt["executed_queries"][0]["provider"] == "web_search"

        opp_links = [link for link in fake_store.session_resource_links.values()
                     if link["resource_type"] == "opportunity"]
        assert len(opp_links) == 1
        link = opp_links[0]
        assert link["relationship"] == "discovered"
        assert link["session_id"] == "s-origin"
        assert link["occurrence_key"] == \
            f"{body['discovery_request_id']}:opp_1"
        assert link["parent_resource_id"] == body["resource_id"]

        # Executed queries index into the parent request resource (§7.3).
        request_resource = fake_store.resource_index[body["resource_id"]]
        assert "lagos" in request_resource["search_terms"]
        assert request_resource["status"] == "COMPLETE"

    def test_worker_redelivery_duplicates_nothing(
            self, client, fake_store, known_sessions, quiet_worker):
        fake_store.opportunities["opp_1"] = {
            "id": "opp_1", "name": "Lagos AI Accelerator",
            "description": "x", "state": "DISCOVERED",
            "workspace_id": "founder", "founder_id": "founder"}
        body = _post(client).json()
        before = dict(fake_store.session_resource_links)
        # Redeliver: receipt is COMPLETE → duplicate, no second sweep.
        response = client.post("/tasks/discover", json={
            "discovery_request_id": body["discovery_request_id"]})
        assert response.status_code == 200
        assert response.json().get("duplicate") is True
        assert len(quiet_worker["sweeps"]) == 1
        assert fake_store.session_resource_links == before

    def test_second_request_same_opportunity_gets_second_occurrence(
            self, client, fake_store, known_sessions, quiet_worker):
        fake_store.opportunities["opp_1"] = {
            "id": "opp_1", "name": "Lagos AI Accelerator",
            "description": "x", "state": "DISCOVERED",
            "workspace_id": "founder", "founder_id": "founder"}
        _post(client, request_id="req_first", context="fintech")
        _post(client, request_id="req_second", context="fintech again",
              session_id="s-latest")
        opp_links = [link for link in fake_store.session_resource_links.values()
                     if link["resource_type"] == "opportunity"]
        assert len(opp_links) == 2
        assert {link["session_id"] for link in opp_links} == \
            {"s-origin", "s-latest"}
        opp_resources = [r for r in fake_store.resource_index.values()
                         if r["resource_type"] == "opportunity"]
        assert len(opp_resources) == 1
