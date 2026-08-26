"""register_session_resource / catalog service tests (docs/23 §6, §12 WI-2)."""

from __future__ import annotations

import pytest

from services import session_resources as sr

FOUNDER = "founder"


@pytest.fixture
def wired(fake_store):
    async def _exists(session_id):
        return session_id.startswith("s-")

    sr.configure(session_exists=_exists)
    yield fake_store
    sr.configure(session_exists=None)


async def _register(session_id="s-a", occurrence="occ-1",
                    relationship=sr.Relationship.DISCOVERED,
                    canonical_id="opp_42", resource_type=sr.ResourceType.OPPORTUNITY,
                    **kwargs):
    return await sr.register_session_resource(
        founder_id=FOUNDER, session_id=session_id,
        resource_type=resource_type, canonical_id=canonical_id,
        relationship=relationship, occurrence_key=occurrence,
        producer_kind="discovery_request", producer_id="dr_1",
        producer_output_key="entity",
        title="Africa AI Accelerator", summary="Accelerator for AI startups",
        status="DISCOVERED", **kwargs)


class TestIdempotency:
    async def test_same_occurrence_replays_one_link(self, wired):
        first = await _register()
        second = await _register()
        assert first["status"] == "success" and second["status"] == "success"
        assert second["replayed"] is True
        assert first["link_id"] == second["link_id"]
        assert len(wired.session_resource_links) == 1
        assert len(wired.resource_index) == 1

    async def test_two_sessions_one_resource_two_links(self, wired):
        a = await _register(session_id="s-a", occurrence="req_a:opp_42")
        b = await _register(session_id="s-b", occurrence="req_b:opp_42")
        assert a["resource_id"] == b["resource_id"]
        assert a["link_id"] != b["link_id"]
        assert len(wired.resource_index) == 1
        assert len(wired.session_resource_links) == 2

    async def test_intentional_repeat_requests_stay_distinct(self, wired):
        one = await _register(resource_type=sr.ResourceType.DISCOVERY_REQUEST,
                              canonical_id="dr_1", occurrence="req_1",
                              relationship=sr.Relationship.CREATED)
        two = await _register(resource_type=sr.ResourceType.DISCOVERY_REQUEST,
                              canonical_id="dr_2", occurrence="req_2",
                              relationship=sr.Relationship.CREATED)
        assert one["resource_id"] != two["resource_id"]
        assert len(wired.resource_index) == 2


class TestValidation:
    async def test_foreign_session_reveals_nothing_and_creates_nothing(self, wired):
        result = await _register(session_id="x-foreign")
        assert result["error"] is True
        assert result["message"] == "not found"
        assert not wired.resource_index and not wired.session_resource_links

    async def test_reserved_relationship_refused(self, wired):
        result = await _register(relationship=sr.Relationship.REFERENCED)
        assert result["error"] is True
        assert "no producer" in result["message"]

    async def test_unknown_and_disabled_types_refused(self, wired):
        bad = await _register(resource_type="not_a_type")
        assert bad["error"] is True
        lead = await _register(resource_type=sr.ResourceType.LEAD)
        assert lead["error"] is True

    async def test_url_representation_ref_refused(self, wired):
        result = await _register(representation_refs=(
            sr.RepresentationRef(kind="file",
                                 ref="/api/ingest/x/source?session_id=s-a"),))
        assert result["error"] is True
        assert "opaque" in result["message"]

    async def test_unwired_session_check_fails_closed(self, fake_store):
        sr.configure(session_exists=None)
        result = await _register()
        assert result["error"] is True
        assert "not configured" in result["message"]


class TestProjection:
    async def test_status_update_creates_no_link(self, wired):
        await _register()
        result = await sr.update_resource_status(
            founder_id=FOUNDER, resource_type=sr.ResourceType.OPPORTUNITY,
            canonical_id="opp_42", status="SHORTLISTED")
        assert result["updated"] is True
        assert len(wired.session_resource_links) == 1
        row = next(iter(wired.resource_index.values()))
        assert row["status"] == "SHORTLISTED"

    async def test_status_update_keeps_link_snapshot(self, wired):
        await _register()
        await sr.update_resource_status(
            founder_id=FOUNDER, resource_type=sr.ResourceType.OPPORTUNITY,
            canonical_id="opp_42", status="SHORTLISTED")
        link = next(iter(wired.session_resource_links.values()))
        assert link["status_snapshot"] == "DISCOVERED"


class TestTombstones:
    async def test_tombstoned_link_is_final(self, wired):
        from services import firestore

        first = await _register()
        assert await firestore.tombstone_session_links(FOUNDER, "s-a") == 1
        replay = await _register()
        assert replay["replayed"] is True and replay["tombstoned"] is True
        link = wired.session_resource_links[first["link_id"]]
        assert link["deleted_at"]

    async def test_search_excludes_tombstoned_links(self, wired):
        from services import firestore

        await _register()
        rows = await firestore.search_session_links(FOUNDER, "africa")
        assert len(rows) == 1
        await firestore.tombstone_session_links(FOUNDER, "s-a")
        assert await firestore.search_session_links(FOUNDER, "africa") == []


class TestCatalog:
    async def test_first_founder_message_titles_the_session(self, wired):
        await sr.catalog_session_event(
            founder_id=FOUNDER, session_id="s-a",
            text="Find grants for a health startup", author="user")
        row = wired.session_catalog["s-a"]
        assert row["title"] == "Find grants for a health startup"
        assert row["message_count"] == 1

    async def test_rolling_terms_from_later_messages(self, wired):
        await sr.catalog_session_event(founder_id=FOUNDER, session_id="s-a",
                                       text="first message", author="user")
        await sr.catalog_session_event(founder_id=FOUNDER, session_id="s-a",
                                       text="Lagos accelerator deadline",
                                       author="agent")
        row = wired.session_catalog["s-a"]
        assert "lagos" in row["search_terms"]
        assert row["message_count"] == 2
        assert row["title"] == "first message"

    async def test_counter_bump_after_link(self, wired):
        await sr.catalog_session_event(founder_id=FOUNDER, session_id="s-a",
                                       text="hello", author="user")
        await _register()
        row = wired.session_catalog["s-a"]
        assert row["resource_count"] == 1
        assert row["resource_types"] == ["opportunity"]

    async def test_catalog_failure_is_data_not_raise(self, wired, monkeypatch):
        async def _boom(*a, **k):
            raise RuntimeError("firestore down")

        monkeypatch.setattr("services.firestore.upsert_session_catalog", _boom)
        result = await sr.catalog_session_event(
            founder_id=FOUNDER, session_id="s-a", text="x", author="user")
        assert result["error"] is True
