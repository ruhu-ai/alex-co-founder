"""Activity/waiting vocabulary contracts (docs/24 §6, §12 WI-1).

The copy maps are the founder-visible surface of everything the agent does, so
they are pinned: a tool name leaking through, a fabricated date, or a wait kind
without a reader are all failures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services import activity as act

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)


class TestStepVocabulary:
    def test_tense_follows_persistence(self):
        assert act.describe_step("tool:search_programs", running=True).verb \
            == "Searching the web"
        assert act.describe_step("tool:search_programs", running=False).verb \
            == "Searched the web"

    def test_every_mapped_signal_has_both_tenses(self):
        for signal in act._STEPS:
            run = act.describe_step(signal, running=True)
            done = act.describe_step(signal, running=False)
            assert run.verb and done.verb
            assert run.verb != done.verb, signal
            assert run.state == "running" and done.state == "done"

    def test_unmapped_signal_never_leaks_a_tool_name(self):
        step = act.describe_step("tool:some_internal_thing", running=True)
        assert step.verb == act.GENERIC_RUNNING
        assert "some_internal_thing" not in step.verb
        done = act.describe_step("agent:secret_agent", running=False)
        assert done.verb == act.GENERIC_DONE

    def test_no_copy_contains_a_tool_agent_or_model_name(self):
        banned = ("agent", "tool", "gemini", "adk", "_agent", "llm", "model")
        for run, done, _ in act._STEPS.values():
            for text in (run, done):
                low = text.lower()
                assert not any(b in low for b in banned), text
        for title in act._WAIT_TITLES.values():
            low = title.lower()
            assert not any(b in low for b in banned), title


class TestStepObject:
    def test_url_reduces_to_bare_host(self):
        step = act.describe_step("tool:fetch_source", running=True,
                                 args={"url": "https://www.meridian.org/apply?x=1"})
        assert step.object == "www.meridian.org"

    def test_userinfo_and_port_are_stripped(self):
        assert act._host("https://user:pw@meridian.org:8443/x") == "meridian.org"

    def test_non_url_objects_pass_through_bounded(self):
        step = act.describe_step("tool:save_draft_section", running=False,
                                 args={"section_key": "  describe   traction "})
        assert step.object == "describe traction"

    def test_object_is_bounded(self):
        step = act.describe_step("tool:save_draft_section", running=True,
                                 args={"section_key": "x" * 500})
        assert len(step.object) <= act.MAX_OBJECT

    def test_steps_that_take_no_object_never_get_one(self):
        step = act.describe_step("tool:search_programs", running=True,
                                 args={"url": "https://leak.example"})
        assert step.object == ""

    def test_missing_or_junk_args_are_safe(self):
        for args in (None, {}, {"url": ""}, {"other": 1}):
            assert act.describe_step("tool:fetch_source", running=True,
                                     args=args).object == ""


class TestWaitRegistry:
    def test_every_kind_has_a_reader_and_a_blocked_on(self):
        kinds = {v for k, v in vars(act.WaitKind).items()
                 if not k.startswith("_") and isinstance(v, str)}
        assert kinds == set(act.WAIT_READERS)
        assert kinds == set(act.BLOCKED_ON)
        assert kinds == set(act._WAIT_TITLES)

    def test_founder_and_world_split_matches_the_design(self):
        founder = {k for k, v in act.BLOCKED_ON.items()
                   if v == act.BlockedOn.FOUNDER}
        assert act.WaitKind.FOUNDER_APPROVAL in founder
        assert act.WaitKind.FOUNDER_FEEDBACK in founder
        assert act.WaitKind.ACTION_UNCERTAIN in founder
        assert act.BLOCKED_ON[act.WaitKind.PORTAL_CONFIRMATION] == "world"


class TestNextCheckPromise:
    def test_a_date_is_never_invented(self):
        text = act.next_check_action(act.WaitKind.PORTAL_CONFIRMATION, None)
        assert act.NO_DATE_FALLBACK in text
        assert "None" not in text

    def test_open_waits_say_nothing_is_running(self):
        text = act.next_check_action(
            act.WaitKind.PORTAL_CONFIRMATION,
            (NOW + timedelta(days=4)).isoformat())
        assert act.NOTHING_RUNNING in text
        assert "chase" in text

    def test_approval_promise_states_the_consequence(self):
        text = act.next_check_action(act.WaitKind.FOUNDER_APPROVAL,
                                     (NOW + timedelta(hours=4)).isoformat())
        assert "fresh fill" in text

    def test_uncertain_never_promises_a_retry(self):
        text = act.next_check_action(act.WaitKind.ACTION_UNCERTAIN, None)
        assert "won't resubmit blind" in text

    def test_promise_is_self_bounding(self):
        """A hostile programme name cannot blow the promise line out."""
        for kind in act.WAIT_READERS:
            text = act.next_check_action(kind, NOW.isoformat(), subject="x" * 500)
            assert len(text) <= act.MAX_PROMISE, kind


class TestTimeTreatment:
    def test_human_dates_read_like_a_person_wrote_them(self):
        assert act._human_date((NOW + timedelta(minutes=30)).isoformat(),
                               now=NOW) == "in 30 minutes"
        assert act._human_date((NOW + timedelta(hours=4)).isoformat(),
                               now=NOW) == "in 4 hours"
        assert act._human_date((NOW + timedelta(days=3)).isoformat(),
                               now=NOW) == "Saturday"
        assert act._human_date((NOW + timedelta(days=10)).isoformat(),
                               now=NOW) == "5 Sep"

    def test_past_dates_and_junk_degrade_safely(self):
        assert act._human_date((NOW - timedelta(days=1)).isoformat(),
                               now=NOW) == "already"
        for bad in (None, "", "not-a-date", "2026-13-45"):
            assert act._human_date(bad, now=NOW) == ""

    def test_urgency_comes_from_real_expiry(self):
        assert act.urgency_for(None) == "none"
        assert act.urgency_for((NOW - timedelta(minutes=1)).isoformat(),
                               now=NOW) == "expired"
        assert act.urgency_for((NOW + timedelta(hours=2)).isoformat(),
                               now=NOW) == "critical"
        assert act.urgency_for((NOW + timedelta(days=2)).isoformat(),
                               now=NOW) == "soon"
        assert act.urgency_for((NOW + timedelta(days=20)).isoformat(),
                               now=NOW) == "none"

    def test_since_is_clamped_both_ways(self):
        future = act.clamp_since((NOW + timedelta(days=5)).isoformat(), now=NOW)
        assert future == NOW
        ancient = act.clamp_since((NOW - timedelta(days=400)).isoformat(), now=NOW)
        assert ancient == NOW - timedelta(days=act.MAX_SINCE_DAYS)
        assert act.clamp_since(None, now=NOW) == NOW - timedelta(days=act.MAX_SINCE_DAYS)


class TestOrdering:
    def test_blocked_on_you_always_sorts_first(self):
        world = act.build_wait(act.WaitKind.PORTAL_CONFIRMATION,
                               since="2026-08-01T00:00:00+00:00", now=NOW)
        founder = act.build_wait(act.WaitKind.FOUNDER_FEEDBACK,
                                 since="2026-08-25T00:00:00+00:00", now=NOW)
        assert act.sort_waits([world, founder])[0] is founder

    def test_urgency_orders_within_blocked_on_you(self):
        soon = act.build_wait(act.WaitKind.FOUNDER_APPROVAL, since="a",
                              next_check=(NOW + timedelta(days=2)).isoformat(),
                              now=NOW)
        critical = act.build_wait(act.WaitKind.FOUNDER_APPROVAL, since="b",
                                  next_check=(NOW + timedelta(hours=1)).isoformat(),
                                  now=NOW)
        assert act.sort_waits([soon, critical])[0] is critical


class TestWaitView:
    def test_payload_omits_provenance_and_bounds_strings(self):
        view = act.build_wait(act.WaitKind.DEADLINE_TICK, since=NOW.isoformat(),
                              next_check=(NOW + timedelta(days=40)).isoformat(),
                              subject="Meridian", now=NOW)
        payload = view.as_dict()
        assert "source" not in payload
        assert len(payload["title"]) <= act.MAX_TITLE
        assert len(payload["next_check_action"]) <= act.MAX_PROMISE
        assert "Meridian decides" in payload["next_check_action"]
