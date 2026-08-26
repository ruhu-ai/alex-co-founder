"""Golden vectors for the docs/23 shared contracts (WI-1).

Pinned literals: a change to normalization, tokenization, prefixes, IDs, or
the cursor codec that alters these bytes is a projection-format change and
needs a deliberate version bump, not a silent drift.
"""

from __future__ import annotations

from services import session_resources as sr


class TestNormalization:
    def test_nfkc_casefold_collapse(self):
        assert sr.normalize_text("Ｆｕｌｌｗｉｄｔｈ ＡＩ") == "fullwidth ai"

    def test_control_and_format_chars_stripped_but_whitespace_survives(self):
        # ​ is Cf (stripped); the tab is Cc but is a separator.
        assert sr.normalize_text("  Café​  ÉCOLE\t42 ") == "café école 42"

    def test_empty(self):
        assert sr.normalize_text("") == ""
        assert sr.normalize_query(None or "") == ""


class TestTokenize:
    def test_letters_numbers_across_scripts(self):
        assert sr.tokenize("Africa AI-Accelerator 2026, Lagos!") == [
            "africa", "ai", "accelerator", "2026", "lagos"]

    def test_single_chars_dropped_and_dedup(self):
        assert sr.tokenize("a b ab ab") == ["ab"]

    def test_token_length_caps(self):
        long = "x" * (sr.TOKEN_MAX + 1)
        assert sr.tokenize(long) == []
        assert sr.tokenize("x" * sr.TOKEN_MAX) == ["x" * sr.TOKEN_MAX]

    def test_max_terms(self):
        text = " ".join(f"tok{i:03d}" for i in range(200))
        assert len(sr.tokenize(text)) == sr.MAX_TERMS


class TestPrefixes:
    def test_prefix_ladder(self):
        assert sr.prefixes_for(["africa"]) == [
            "af", "afr", "afri", "afric", "africa"]

    def test_opaque_tokens_get_the_whole_term_but_no_prefix_ladder(self):
        """Exact-id lookup must work: the indexed query is a single
        array_contains over search_prefixes, so an opaque token that is
        absent from that field is unfindable in production."""
        terms = sr.tokenize("req_7f3a9b2c4d5e Lagos")
        assert terms == ["req", "7f3a9b2c4d5e", "lagos"]
        assert sr.prefixes_for(terms) == [
            "re", "req", "7f3a9b2c4d5e", "la", "lag", "lago", "lagos"]

    def test_opaque_token_is_findable_end_to_end(self):
        """The probe chosen for an opaque query must be present in the
        prefixes the write path indexed."""
        _, prefixes = sr.search_fields("Request 7f3a9b2c4d5e")
        probe = sr.choose_probe_prefix(sr.query_terms("7f3a9b2c4d5e"))
        assert probe in prefixes

    def test_prefix_cap(self):
        # Distinct two-char starts so prefixes do not deduplicate away:
        # 40 tokens x 15 prefix lengths >> 256.
        terms = [f"{chr(97 + i // 26)}{chr(97 + i % 26)}longsuffixwords"
                 for i in range(40)]
        assert len(sr.prefixes_for(terms)) == sr.MAX_PREFIXES

    def test_prefix_max_length(self):
        [*prefs] = sr.prefixes_for(["internationalization"])
        assert max(len(p) for p in prefs) == sr.PREFIX_MAX


class TestProbeSelection:
    def test_longest_prefix_wins(self):
        assert sr.choose_probe_prefix(["ai", "accelerator", "africa"]) == \
            "accelerator"

    def test_tie_breaks_lexicographically(self):
        assert sr.choose_probe_prefix(["banana", "apricot"]) == "apricot"

    def test_opaque_only_query_probes_whole_term(self):
        assert sr.choose_probe_prefix(["7f3a9b2c4d5e"]) == "7f3a9b2c4d5e"

    def test_no_usable_terms(self):
        assert sr.choose_probe_prefix([]) is None


class TestDeterministicIds:
    def test_resource_id_golden(self):
        assert sr.resource_id_for(
            "founder", "opportunity", "opportunities", "opp_42",
        ) == "r_1c92819e9fec3069bd768c98c7ba4967"

    def test_link_id_golden(self):
        assert sr.link_id_for(
            "founder", "s_abc", "req_789:opp_42", "r_x", "discovered",
        ) == "l_a361f928a56ff780162ecdb91deef06d6913b065"

    def test_any_component_changes_the_id(self):
        base = sr.link_id_for("f", "s", "k", "r", "created")
        assert sr.link_id_for("f", "s", "k", "r", "continued") != base
        assert sr.link_id_for("f", "s2", "k", "r", "created") != base
        assert sr.link_id_for("f", "s", "k2", "r", "created") != base


class TestCursor:
    BINDING = sr.filter_binding("africa accelerator", ["opportunity"], None)

    def test_round_trip(self):
        cursor = sr.encode_cursor(self.BINDING, {
            "links": ["2026-08-25T12:00:00+00:00", "l_abc"],
            "resources": None,
        })
        decoded = sr.decode_cursor(cursor, self.BINDING)
        assert decoded["error"] is False
        assert decoded["frontiers"]["links"] == [
            "2026-08-25T12:00:00+00:00", "l_abc"]
        assert decoded["frontiers"]["resources"] is None

    def test_filter_mismatch_rejected(self):
        cursor = sr.encode_cursor(self.BINDING, {"links": None})
        other = sr.filter_binding("africa accelerator", ["document"], None)
        assert sr.decode_cursor(cursor, other)["error"] is True

    def test_malformed_rejected(self):
        for bad in ("", "!!!", "bm90anNvbg=="):
            assert sr.decode_cursor(bad, self.BINDING)["error"] is True

    def test_binding_normalizes_query_and_types(self):
        a = sr.filter_binding("  Africa   ACCELERATOR ", ["b", "a", "a"], None)
        b = sr.filter_binding("africa accelerator", ["a", "b"], None)
        assert a == b


class TestTermsMatch:
    def test_all_terms_must_match(self):
        terms, prefixes = sr.search_fields("Africa AI Accelerator")
        assert sr.terms_match(terms, prefixes, ["africa", "accel"])
        assert not sr.terms_match(terms, prefixes, ["africa", "berlin"])

    def test_prefix_of_indexed_term_matches(self):
        terms, prefixes = sr.search_fields("Meridian Pre-Seed Grant")
        assert sr.terms_match(terms, prefixes, ["merid"])


class TestRegistry:
    def test_closed_registry_types(self):
        assert set(sr.SEARCHABLE_TYPES) == {
            "discovery_request", "opportunity", "application", "document",
            "artifact", "browser_report", "evidence_report", "session"}

    def test_disabled_types_have_no_collection(self):
        assert not sr.RESOURCE_REGISTRY[sr.ResourceType.LEAD].enabled
        assert not sr.RESOURCE_REGISTRY[sr.ResourceType.WORKFLOW_RUN].enabled
