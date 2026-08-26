"""Turn trace collection (docs/24 §7.2, §12 WI-3)."""

from __future__ import annotations

from types import SimpleNamespace

from services import activity as act


def _call(name, **args):
    return SimpleNamespace(function_call=SimpleNamespace(name=name, args=args),
                           text=None)


def _event(author="co_founder", parts=()):
    return SimpleNamespace(author=author,
                           content=SimpleNamespace(parts=list(parts)))


class TestCollection:
    def test_tool_calls_become_founder_voice_steps(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        c.observe(_event(parts=[_call("search_programs", query="grants")]))
        c.observe(_event(parts=[_call("fetch_source",
                                      url="https://meridian.org/apply")]))
        verbs = [s["verb"] for s in c.as_payload()]
        assert verbs == ["Searched the web", "Read"]
        assert c.as_payload()[1]["object"] == "meridian.org"

    def test_sub_agent_handoff_is_a_step(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        c.observe(_event(author="scout_agent"))
        assert [s["verb"] for s in c.as_payload()] == ["Looked for programmes"]

    def test_the_same_agent_is_announced_once(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        for _ in range(4):
            c.observe(_event(author="scout_agent"))
        assert len(c.as_payload()) == 1

    def test_unmapped_agents_stay_silent_rather_than_generic(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        c.observe(_event(author="some_internal_worker"))
        assert c.as_payload() == []

    def test_unmapped_tools_degrade_without_leaking_the_name(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        c.observe(_event(parts=[_call("internal_secret_tool", x=1)]))
        payload = c.as_payload()
        assert payload[0]["verb"] == act.GENERIC_DONE
        assert "internal_secret_tool" not in repr(payload)

    def test_immediate_repeats_collapse(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        for _ in range(3):
            c.observe(_event(parts=[_call("fetch_source",
                                          url="https://meridian.org/a")]))
        assert len(c.as_payload()) == 1

    def test_distinct_objects_do_not_collapse(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        c.observe(_event(parts=[_call("fetch_source", url="https://a.org/x")]))
        c.observe(_event(parts=[_call("fetch_source", url="https://b.org/y")]))
        assert [s["object"] for s in c.as_payload()] == ["a.org", "b.org"]

    def test_step_count_is_bounded(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        for i in range(200):
            c.observe(_event(parts=[_call("fetch_source",
                                          url=f"https://h{i}.org/x")]))
        assert len(c.as_payload()) == act.MAX_STEPS
        assert c.truncated is True


class TestNeverRaises:
    def test_malformed_events_are_ignored(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        for bad in (None, object(), SimpleNamespace(),
                    SimpleNamespace(author=None, content=None),
                    SimpleNamespace(author="x", content=SimpleNamespace(parts=None)),
                    SimpleNamespace(author="x",
                                    content=SimpleNamespace(parts=[object()]))):
            c.observe(bad)          # must not raise
        assert isinstance(c.as_payload(), list)

    def test_a_hostile_argument_cannot_escape_bounds(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        c.observe(_event(parts=[_call("save_draft_section",
                                      section_key="<script>x</script>" * 50)]))
        obj = c.as_payload()[0]["object"]
        assert len(obj) <= act.MAX_OBJECT

    def test_user_turns_are_not_steps(self):
        c = act.TraceCollector(root_agent_name="co_founder")
        c.observe(_event(author="user"))
        assert c.as_payload() == []
