"""The waiting adapter and GET /api/waiting (docs/24 §4, §7.1, §12 WI-4).

The seam is the point: everything above it is written against WaitView, so
these tests assert reader independence, purity, and the Phase-1 swap.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from services import activity as act
from services import waiting

FOUNDER = "founder"
NOW = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)
ISO = NOW.isoformat()


@pytest.fixture
def wired(fake_store, monkeypatch):
    state = {"pending_signals": [], "active_application_id": "app_1"}

    async def _read(session_id):
        return dict(state) if session_id == "s-a" else None

    waiting.configure(session_state_reader=_read)
    yield fake_store, state
    waiting.configure(session_state_reader=None)


def _approval(store, *, expires_in_hours=4, session="s-a"):
    aid = "ap_1"
    store.approvals[aid] = {
        "id": aid, "application_id": "app_1", "gate": "submit_application",
        "status": "PENDING", "founder_id": FOUNDER, "session_id": session,
        "created_at": ISO,
        "expires_at": (NOW + timedelta(hours=expires_in_hours)).isoformat(),
    }
    return aid


class TestReaders:
    async def test_pending_signals_become_waits(self, wired):
        _, state = wired
        state["pending_signals"] = ["founder_feedback", "portal_confirmation"]
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        kinds = [w.wait_kind for w in result["waits"]]
        assert "founder_feedback" in kinds and "portal_confirmation" in kinds
        assert result["partial"] is False

    async def test_unknown_signals_are_ignored_not_guessed(self, wired):
        _, state = wired
        state["pending_signals"] = ["something_new", 42, None]
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        assert result["waits"] == []

    async def test_approval_carries_real_expiry_and_urgency(self, wired):
        store, _ = wired
        _approval(store, expires_in_hours=2)
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        approval = [w for w in result["waits"]
                    if w.wait_kind == "founder_approval"][0]
        assert approval.blocked_on == "founder"
        assert approval.urgency == "critical"
        assert "fresh fill" in approval.next_check_action

    async def test_followups_become_timer_waits_with_the_programme_name(self, wired):
        store, _ = wired
        store.opportunities["opp_1"] = {"id": "opp_1", "name": "Meridian",
                                        "state": "SHORTLISTED",
                                        "workspace_id": FOUNDER,
                                        "founder_id": FOUNDER}
        store.applications["app_1"] = {
            "id": "app_1", "founder_id": FOUNDER, "opportunity_id": "opp_1",
            "state": "FOLLOW_UP", "created_at": ISO, "updated_at": ISO,
            "followups": [{"kind": "decision", "status": "PENDING",
                           "due_at": (NOW + timedelta(days=10)).isoformat()}],
        }
        result = await waiting.list_waits(FOUNDER, now=NOW)
        timer = [w for w in result["waits"] if w.wait_kind == "deadline_tick"][0]
        assert timer.blocked_on == "timer"
        assert "Meridian decides" in timer.next_check_action

    async def test_completed_followups_are_not_waits(self, wired):
        store, _ = wired
        store.applications["app_1"] = {
            "id": "app_1", "founder_id": FOUNDER, "opportunity_id": "",
            "state": "FOLLOW_UP", "created_at": ISO, "updated_at": ISO,
            "followups": [{"kind": "decision", "status": "DONE",
                           "due_at": ISO}],
        }
        result = await waiting.list_waits(FOUNDER, now=NOW)
        assert [w for w in result["waits"] if w.wait_kind == "deadline_tick"] == []

    async def test_in_flight_discovery_is_a_world_wait(self, wired):
        store, _ = wired
        store.discovery_requests["dr_1"] = {
            "founder_id": FOUNDER, "status": "RUNNING", "created_at": ISO,
            "origin_session_id": "s-a", "request_id": "req_1"}
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        run = [w for w in result["waits"] if w.wait_kind == "discovery_running"][0]
        assert run.blocked_on == "world"
        assert "closing this chat is safe" in run.next_check_action

    async def test_completed_discovery_is_not_a_wait(self, wired):
        store, _ = wired
        store.discovery_requests["dr_1"] = {
            "founder_id": FOUNDER, "status": "COMPLETE", "created_at": ISO,
            "origin_session_id": "s-a", "request_id": "req_1"}
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        assert result["waits"] == []

    async def test_durable_portal_verification_survives_missing_session_state(
            self, wired):
        store, _ = wired
        store.portal_registrations["example.org"] = {
            "host": "example.org", "founder_id": FOUNDER,
            "session_id": "s-a", "application_id": "app_1",
            "status": "PENDING", "updated_at": ISO,
        }
        waiting.configure(session_state_reader=None)
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        assert any(wait.wait_kind == "portal_confirmation"
                   for wait in result["waits"])

    async def test_uncertain_action_is_a_founder_visible_wait(self, wired):
        store, _ = wired
        store.external_actions["act_1"] = {
            "action_id": "act_1", "founder_id": FOUNDER,
            "session_id": "s-a", "status": "UNCERTAIN",
            "created_at": ISO, "updated_at": ISO,
        }
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        wait = next(item for item in result["waits"]
                    if item.wait_kind == "action_uncertain")
        assert wait.focus == {"kind": "external_action", "id": "act_1"}
        assert "won't resubmit blind" in wait.next_check_action


class TestReaderIndependence:
    async def test_one_failing_reader_degrades_only_itself(self, wired,
                                                           monkeypatch):
        store, _ = wired
        _approval(store)

        async def _boom(founder_id):
            raise RuntimeError("firestore down")

        monkeypatch.setattr("services.firestore.list_inflight_applications", _boom)
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        assert result["partial"] is True
        assert result["failed_readers"] == ["followup"]
        # The approval reader still produced its wait.
        assert any(w.wait_kind == "founder_approval" for w in result["waits"])

    async def test_unreadable_session_state_omits_rather_than_guesses(self, wired):
        store, _ = wired
        _approval(store)
        # s-b has no state; the pending_signals reader must contribute nothing.
        result = await waiting.list_waits(FOUNDER, session_id="s-b", now=NOW)
        assert result["partial"] is False
        assert all(w.source != "pending_signals" for w in result["waits"])

    async def test_unconfigured_session_reader_is_safe(self, fake_store):
        waiting.configure(session_state_reader=None)
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        assert result["waits"] == [] and result["partial"] is False


class TestPurityAndOrdering:
    async def test_identical_inputs_give_identical_output(self, wired):
        store, state = wired
        state["pending_signals"] = ["portal_confirmation"]
        _approval(store)
        first = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        second = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        assert [w.as_dict() for w in first["waits"]] == \
               [w.as_dict() for w in second["waits"]]

    async def test_blocked_on_you_sorts_above_the_world(self, wired):
        store, state = wired
        state["pending_signals"] = ["portal_confirmation"]
        _approval(store)
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        assert result["waits"][0].blocked_on == "founder"

    async def test_result_is_bounded(self, wired):
        store, _ = wired
        for i in range(30):
            store.discovery_requests[f"dr_{i}"] = {
                "founder_id": FOUNDER, "status": "RUNNING",
                "created_at": ISO, "origin_session_id": "s-a",
                "request_id": f"req_{i}"}
        result = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)
        assert len(result["waits"]) <= act.MAX_WAITS


class TestPhaseOneSwap:
    """docs/24 §12: swapping readers for a stub `waits` reader must produce
    the same WaitView[] — proving the seam holds before Phase 1B exists."""

    async def test_a_stub_waits_reader_produces_equivalent_views(self, wired,
                                                                 monkeypatch):
        store, _ = wired
        _approval(store, expires_in_hours=2)
        before = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)

        async def _stub_waits_reader(founder_id, session_id, now):
            # What 21's `waits` collection would yield for the same state.
            return [act.build_wait(
                act.WaitKind.FOUNDER_APPROVAL, since=ISO,
                next_check=(NOW + timedelta(hours=2)).isoformat(),
                focus={"kind": "approval", "id": "ap_1"},
                source="wait", now=now)]

        monkeypatch.setattr(waiting, "_READERS", (("wait", _stub_waits_reader),))
        after = await waiting.list_waits(FOUNDER, session_id="s-a", now=NOW)

        # Identical above the seam; only `source` (never rendered) differs.
        assert [w.as_dict() for w in before["waits"]] == \
               [w.as_dict() for w in after["waits"]]
        assert before["waits"][0].source == "approval"
        assert after["waits"][0].source == "wait"


class TestChangedSince:
    async def test_only_things_after_since_are_reported(self, wired):
        store, _ = wired
        store.session_resource_links["l_old"] = {
            "founder_id": FOUNDER, "session_id": "s-a", "resource_id": "r_1",
            "resource_type": "document", "link_id": "l_old",
            "title_snapshot": "Old pack",
            "occurred_at": (NOW - timedelta(days=5)).isoformat()}
        store.session_resource_links["l_new"] = {
            "founder_id": FOUNDER, "session_id": "s-a", "resource_id": "r_2",
            "resource_type": "document", "link_id": "l_new",
            "title_snapshot": "New pack",
            "occurred_at": (NOW - timedelta(hours=2)).isoformat()}
        changed = await waiting.changed_since(
            FOUNDER, NOW - timedelta(days=1), session_id="s-a")
        assert [c["title"] for c in changed] == ["New pack"]

    async def test_focus_is_a_closed_enum_never_a_url(self, wired):
        store, _ = wired
        store.session_resource_links["l_1"] = {
            "founder_id": FOUNDER, "session_id": "s-a", "resource_id": "r_1",
            "resource_type": "opportunity", "link_id": "l_1",
            "title_snapshot": "Lagos", "canonical_ref": {"id": "opp_1"},
            "occurred_at": NOW.isoformat()}
        changed = await waiting.changed_since(
            FOUNDER, NOW - timedelta(days=1), session_id="s-a")
        assert changed[0]["focus"] == {"kind": "opportunity", "id": "opp_1"}
        assert "http" not in repr(changed)

    async def test_read_failure_is_data_not_a_raise(self, wired, monkeypatch):
        async def _boom(*a, **k):
            raise RuntimeError("down")
        monkeypatch.setattr("services.firestore.list_session_links", _boom)
        assert await waiting.changed_since(
            FOUNDER, NOW - timedelta(days=1), session_id="s-a") == []


class TestEndpoint:
    @pytest.fixture(scope="class")
    def appmod(self):
        os.environ.pop("APP_AUTH_TOKEN", None)
        os.environ.pop("K_SERVICE", None)
        import app.main as m
        return m

    @pytest.fixture()
    def client(self, appmod, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.delenv("APP_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        return TestClient(appmod.app)

    def test_foreign_session_is_generic_not_found(self, client, appmod,
                                                  monkeypatch, fake_store):
        async def _exists(sid):
            return False
        monkeypatch.setattr(appmod, "_founder_session_exists", _exists)
        r = client.get("/api/waiting?session_id=s-foreign")
        assert r.status_code == 404
        assert r.json() == {"error": "not found"}

    def test_payload_shape_and_no_secrets(self, client, appmod, monkeypatch,
                                          fake_store):
        async def _exists(sid):
            return True
        monkeypatch.setattr(appmod, "_founder_session_exists", _exists)
        fake_store.approvals["ap_1"] = {
            "id": "ap_1", "application_id": "app_1", "status": "PENDING",
            "gate": "submit_application", "founder_id": "founder",
            "session_id": "s-a", "created_at": ISO,
            "token": "SUPERSECRETTOKEN", "subject_hash": "sha256:leak",
            "details": {"body": "confidential email body"},
            "expires_at": (datetime.now(timezone.utc)
                           + timedelta(hours=3)).isoformat()}
        body = client.get("/api/waiting?session_id=s-a").json()
        assert body["status"] == "success"
        assert body["blocked_on_you"] >= 1
        blob = repr(body)
        for secret in ("SUPERSECRETTOKEN", "sha256:leak", "confidential"):
            assert secret not in blob
