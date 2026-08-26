"""GET /api/search (docs/23 §7, WI-6).

The cases that mattered in review: grouped result unit, keyset pagination
stability under projection mutation, explicit truncation, cursor binding,
and the absence of a cross-founder / cross-session existence oracle.
"""

from __future__ import annotations

import pytest

from services import session_resources as sr

FOUNDER = "founder"
OTHER = "other-founder"


@pytest.fixture
def wired(fake_store):
    async def _exists(session_id):
        return session_id.startswith("s-")

    sr.configure(session_exists=_exists)
    yield fake_store
    sr.configure(session_exists=None)


async def _register(session="s-a", occurrence="occ-1", founder=FOUNDER,
                    resource_type=sr.ResourceType.OPPORTUNITY,
                    canonical_id="opp_42", title="Africa AI Accelerator",
                    summary="Accelerator for African AI startups",
                    relationship=sr.Relationship.DISCOVERED, status="DISCOVERED"):
    return await sr.register_session_resource(
        founder_id=founder, session_id=session, resource_type=resource_type,
        canonical_id=canonical_id, relationship=relationship,
        occurrence_key=occurrence, producer_kind="test", producer_id="t",
        producer_output_key="entity", title=title, summary=summary,
        status=status, session_verified=True)


async def _search(**kwargs):
    kwargs.setdefault("founder_id", FOUNDER)

    async def _exists(session_id):
        return session_id.startswith("s-")

    kwargs.setdefault("session_exists", _exists)
    return await sr.search(**kwargs)


class TestResultUnit:
    async def test_one_opportunity_two_sessions_is_one_grouped_row(self, wired):
        await _register(session="s-a", occurrence="req_a:opp_42")
        await _register(session="s-b", occurrence="req_b:opp_42")
        result = await _search(q="africa")
        rows = [r for r in result["results"] if r["result_type"] == "opportunity"]
        assert len(rows) == 1
        assert rows[0]["occurrence_count"] == 2
        assert rows[0]["latest_occurrence"]["session_id"] in {"s-a", "s-b"}
        assert rows[0]["focus"] == {"kind": "opportunity", "id": "opp_42"}

    async def test_session_filter_scopes_to_that_occurrence(self, wired):
        await _register(session="s-a", occurrence="req_a:opp_42")
        await _register(session="s-b", occurrence="req_b:opp_42")
        result = await _search(q="africa", session_id="s-a")
        rows = [r for r in result["results"] if r["result_type"] == "opportunity"]
        assert len(rows) == 1
        assert rows[0]["latest_occurrence"]["session_id"] == "s-a"
        assert rows[0]["occurrence_count"] == 1

    async def test_unlinked_resource_is_a_first_class_hit(self, wired):
        """legacy_unlinked / deleted-origin / system work stays findable."""
        from services import firestore

        await _register(session="s-a", occurrence="occ")
        await firestore.tombstone_session_links(FOUNDER, "s-a")
        result = await _search(q="africa")
        rows = [r for r in result["results"] if r["result_type"] == "opportunity"]
        assert len(rows) == 1
        assert rows[0]["session_id"] is None
        assert rows[0]["occurrence_count"] == 0

    async def test_two_repeated_searches_stay_two_resources(self, wired):
        await _register(resource_type=sr.ResourceType.DISCOVERY_REQUEST,
                        canonical_id="dr_1", occurrence="req_1",
                        relationship=sr.Relationship.CREATED,
                        title="Lagos accelerators", summary="")
        await _register(resource_type=sr.ResourceType.DISCOVERY_REQUEST,
                        canonical_id="dr_2", occurrence="req_2",
                        relationship=sr.Relationship.CREATED,
                        title="Lagos accelerators", summary="")
        result = await _search(q="lagos")
        rows = [r for r in result["results"]
                if r["result_type"] == "discovery_request"]
        assert len(rows) == 2

    async def test_conversations_are_searchable(self, wired):
        await sr.catalog_session_event(
            founder_id=FOUNDER, session_id="s-a",
            text="Find grants for a health startup", author="user")
        result = await _search(q="health")
        rows = [r for r in result["results"] if r["result_type"] == "session"]
        assert len(rows) == 1
        assert rows[0]["focus"] == {"kind": "session", "id": "s-a"}


class TestPagination:
    async def test_mutation_between_pages_neither_skips_nor_duplicates(self, wired):
        for i in range(6):
            await _register(occurrence=f"occ-{i}", canonical_id=f"opp_{i}",
                            title=f"Accelerator Programme {i}")
        first = await _search(q="accelerator", limit=3)
        assert first["next_cursor"]
        seen = [r["resource_id"] for r in first["results"]]

        # Mutate a returned row's status/updated_at: cursors bind to immutable
        # scan keys, so the walk must be unaffected.
        await sr.update_resource_status(
            founder_id=FOUNDER, resource_type=sr.ResourceType.OPPORTUNITY,
            canonical_id="opp_0", status="SHORTLISTED")

        second = await _search(q="accelerator", limit=3,
                               cursor=first["next_cursor"])
        page2 = [r["resource_id"] for r in second["results"]]
        assert not (set(seen) & set(page2)), "cursor walk duplicated a row"
        assert len(set(seen) | set(page2)) == 6, "cursor walk skipped a row"

    async def test_cursor_is_filter_bound(self, wired):
        await _register()
        first = await _search(q="africa", limit=1)
        if first["next_cursor"]:
            bad = await _search(q="africa", types=["document"], limit=1,
                                cursor=first["next_cursor"])
            assert bad["error"] is True
            assert "filters" in bad["message"]

    async def test_malformed_cursor_is_data_not_crash(self, wired):
        result = await _search(q="africa", cursor="!!!not-a-cursor")
        assert result["error"] is True

    async def test_truncation_is_explicit(self, wired, monkeypatch):
        monkeypatch.setattr(sr, "CANDIDATE_CAP", 3)
        monkeypatch.setattr(sr, "CANDIDATE_MULTIPLIER", 1)
        for i in range(8):
            await _register(occurrence=f"occ-{i}", canonical_id=f"opp_{i}",
                            title=f"Accelerator Programme {i}")
        result = await _search(q="accelerator", limit=2)
        assert result["truncated"] is True
        assert result["next_cursor"]


class TestAuthorizationOracle:
    async def test_foreign_founder_rows_never_surface(self, wired):
        await _register(founder=OTHER, occurrence="occ-x",
                        title="Africa AI Accelerator")
        result = await _search(q="africa")
        assert result["results"] == []

    async def test_foreign_session_filter_is_generic_not_found(self, wired):
        await _register()
        result = await _search(q="africa", session_id="x-foreign")
        assert result["error"] is True
        assert result["message"] == "not found"

    async def test_unknown_type_is_rejected(self, wired):
        result = await _search(q="africa", types=["approvals"])
        assert result["error"] is True

    async def test_no_approval_or_secret_fields_reach_results(self, wired):
        await _register()
        result = await _search(q="africa")
        blob = repr(result)
        for forbidden in ("token", "subject_hash", "details", "mapping",
                          "http://", "https://"):
            assert forbidden not in blob


class TestQueryHandling:
    async def test_blank_query_returns_recent_work(self, wired):
        await _register()
        await sr.catalog_session_event(founder_id=FOUNDER, session_id="s-a",
                                       text="hello there", author="user")
        result = await _search(q="")
        assert result["results"]
        assert result["next_cursor"] is None  # blank state does not paginate
        work = next(r for r in result["results"]
                    if r["result_type"] == "opportunity")
        assert work["session_id"] == "s-a"
        assert work["occurrence_count"] == 1

    async def test_single_character_query_refused(self, wired):
        result = await _search(q="a")
        assert result["error"] is True

    async def test_unicode_normalization_matches(self, wired):
        await _register(title="Café Accelerator")
        result = await _search(q="CAFÉ")
        assert [r["title"] for r in result["results"]] == ["Café Accelerator"]

    async def test_every_query_term_must_match(self, wired):
        await _register(title="Africa AI Accelerator")
        assert (await _search(q="africa accelerator"))["results"]
        assert not (await _search(q="africa berlin"))["results"]

    async def test_type_filter(self, wired):
        await _register()
        await _register(resource_type=sr.ResourceType.DOCUMENT,
                        canonical_id="app1:pack", occurrence="doc-1",
                        relationship=sr.Relationship.PRODUCED,
                        title="Africa Application Pack", summary="DOCX v1")
        docs = await _search(q="africa", types=["document"])
        assert {r["result_type"] for r in docs["results"]} == {"document"}


class TestSessionResourcesEndpoint:
    async def test_lists_one_conversations_outputs(self, wired):
        await _register(session="s-a", occurrence="occ-1")
        await _register(session="s-b", occurrence="occ-2",
                        canonical_id="opp_99", title="Other")
        result = await sr.session_resources_for(FOUNDER, "s-a")
        assert [r["canonical_ref"]["id"] for r in result["resources"]] == ["opp_42"]

    async def test_bounded_list_reports_truncation_instead_of_silent_omission(self, wired):
        for index in range(3):
            await _register(session="s-a", occurrence=f"occ-{index}",
                            canonical_id=f"opp_{index}", title=f"Opportunity {index}")
        result = await sr.session_resources_for(FOUNDER, "s-a", limit=2)
        assert len(result["resources"]) == 2
        assert result["truncated"] is True

    async def test_workbench_context_pages_until_every_resource_is_loaded(self, wired):
        for index in range(3):
            await _register(session="s-a", occurrence=f"page-{index}",
                            canonical_id=f"opp_page_{index}",
                            title=f"Paged opportunity {index}")
        result = await sr.all_session_resources_for(
            FOUNDER, "s-a", page_size=1, max_items=10)
        assert {r["canonical_ref"]["id"] for r in result["resources"]} == {
            "opp_page_0", "opp_page_1", "opp_page_2"}
        assert result["truncated"] is False


class TestCursorWalkExhaustive:
    """The property the review demanded proof of: a full cursor walk visits
    every match exactly once, under mutation and across merged streams."""

    async def test_full_walk_visits_every_match_once(self, wired):
        for i in range(11):
            await _register(occurrence=f"occ-{i}", canonical_id=f"opp_{i}",
                            title=f"Accelerator Programme {i:02d}")
        await sr.catalog_session_event(
            founder_id=FOUNDER, session_id="s-a",
            text="accelerator planning notes", author="user")

        seen: list[str] = []
        cursor = None
        for _ in range(20):  # generous bound; walk must terminate sooner
            page = await _search(q="accelerator", limit=3, cursor=cursor)
            assert page.get("error") is not True
            seen.extend(r["result_id"] for r in page["results"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert cursor is None, "walk did not terminate"
        assert len(seen) == len(set(seen)), "a row was visited twice"
        # 11 opportunities + 1 conversation.
        assert len(seen) == 12

    async def test_walk_survives_mutation_of_already_seen_rows(self, wired):
        for i in range(7):
            await _register(occurrence=f"occ-{i}", canonical_id=f"opp_{i}",
                            title=f"Accelerator Programme {i:02d}")
        seen: list[str] = []
        cursor = None
        while True:
            page = await _search(q="accelerator", limit=2, cursor=cursor)
            seen.extend(r["result_id"] for r in page["results"])
            # Mutate every row returned so far between each page.
            for i in range(7):
                await sr.update_resource_status(
                    founder_id=FOUNDER,
                    resource_type=sr.ResourceType.OPPORTUNITY,
                    canonical_id=f"opp_{i}", status="SHORTLISTED")
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert len(seen) == len(set(seen)) == 7


class TestReviewRegressions:
    """Bugs found by self-review of this slice; each one shipped broken and
    was caught before merge. They stay as permanent regressions."""

    async def _reg(self, session, occ, cid, title,
                   rtype=sr.ResourceType.OPPORTUNITY,
                   rel=sr.Relationship.DISCOVERED):
        return await sr.register_session_resource(
            founder_id=FOUNDER, session_id=session, resource_type=rtype,
            canonical_id=cid, relationship=rel, occurrence_key=occ,
            producer_kind="t", producer_id="t", producer_output_key="entity",
            title=title, summary="", status="X", session_verified=True)

    async def test_resource_straddling_a_page_boundary_is_not_duplicated(self, wired):
        """Occurrence rows must not drive pagination: a resource with many
        scattered occurrences was previously emitted on two pages."""
        for i in range(12):
            await self._reg(f"s-{i}", f"occR-{i}", "opp_R",
                            "Accelerator Programme R")
            await self._reg(f"s-{i}", f"occO-{i}", f"opp_{i}",
                            f"Accelerator Programme {i}")
        seen, cursor = [], None
        for _ in range(40):
            page = await _search(q="accelerator", limit=2, cursor=cursor)
            seen.extend(r["result_id"] for r in page["results"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert len(seen) == len(set(seen)), "a resource appeared on two pages"
        assert len(set(seen)) == 13

    async def test_occurrence_count_is_the_true_total(self, wired):
        """Counts come from the resource's own links, not from whatever
        happened to fall inside the scan window."""
        for i in range(12):
            await self._reg(f"s-{i}", f"occR-{i}", "opp_R",
                            "Accelerator Programme R")
        page = await _search(q="accelerator", limit=5)
        row = [r for r in page["results"]
               if r["result_type"] == "opportunity"][0]
        assert row["occurrence_count"] == 12

    async def test_supporting_resources_need_an_explicit_type_filter(self, wired):
        """Global search defaults to primary (docs/23 §5.1); browser and
        evidence reports previously leaked in as top-level hits."""
        await self._reg("s-a", "k1", "run1", "Meridian portal browse",
                        rtype=sr.ResourceType.BROWSER_REPORT,
                        rel=sr.Relationship.PRODUCED)
        assert (await _search(q="meridian"))["results"] == []
        explicit = await _search(q="meridian", types=["browser_report"])
        assert [r["result_type"] for r in explicit["results"]] == ["browser_report"]
