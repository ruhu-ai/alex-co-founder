"""Top-left global search UI contract (docs/23 §8, WI-7).

Static contracts over app/static/index.html, matching the established style
of test_browser_ui_contract / test_attachment_ui_contract. These lock the
behaviours the review required proof of and that no server test can see.
"""

from __future__ import annotations

import pathlib

UI = (pathlib.Path(__file__).resolve().parents[2]
      / "app/static/index.html").read_text(encoding="utf-8")


class TestSurface:
    def test_button_is_search_not_search_sessions(self):
        assert 'title="Search (⌘K)"' in UI
        assert 'aria-label="Search conversations and Alex&#39;s work"' in UI
        assert "Search sessions" not in UI

    def test_dialog_copy_does_not_claim_sessions_own_work(self):
        assert "Search conversations and Alex&#39;s work." in UI
        # The old copy asserted drafts/approvals belong to a session.
        assert "Approvals and drafts stay with their session" not in UI

    def test_uses_the_server_search_endpoint(self):
        assert '"/api/search?"' in UI
        # The old client-side substring filter over previews is gone.
        assert "s.preview || \"\").toLowerCase().includes(q)" not in UI

    def test_groups_cover_every_founder_visible_type(self):
        for kind in ("session", "discovery_request", "opportunity",
                     "application", "document", "artifact",
                     "browser_report", "evidence_report"):
            assert f'"{kind}"' in UI


class TestBehaviour:
    def test_stale_responses_cannot_replace_newer_results(self):
        assert "searchGeneration" in UI
        assert "if (generation !== searchGeneration) return;" in UI

    def test_typing_is_debounced(self):
        assert "setTimeout(() => runSearch({ reset: true }), 200)" in UI

    def test_pagination_is_user_driven_not_polled(self):
        assert "data-search-more" in UI
        assert "Show more" in UI
        # No timer re-runs the search.
        assert "setInterval(runSearch" not in UI

    def test_opening_a_result_is_read_through_and_resume_is_explicit(self):
        assert "function openSearchResult" in UI
        # Read-through parks the real session; it never writes localStorage.
        block = UI.split("function openSearchResult")[1].split("\n}")[0]
        assert "localStorage" not in block
        assert "viewSession(" in block
        assert "data-session-resume" in UI

    def test_opening_a_previous_search_never_relaunches_it(self):
        block = UI.split("function focusResource")[1].split("\n}")[0]
        assert "/api/discovery-requests" not in block
        assert "doesn't run it again" in block

    def test_focus_targets_are_closed_enum_not_urls(self):
        block = UI.split("function focusResource")[1].split("\n}")[0]
        assert "focus.kind" in block
        assert "location.href" not in block
        assert "eval(" not in block
        assert "innerHTML = row" not in block

    def test_error_state_preserves_the_current_result_set(self):
        assert "Showing the last results." in UI

    def test_truncation_is_surfaced_to_the_founder(self):
        assert "narrow your search to see more" in UI

    def test_blank_and_empty_states_exist(self):
        assert "Nothing yet — say something to your co-founder" in UI
        assert "Nothing matches." in UI

    def test_deleted_origin_renders_a_tombstone(self):
        assert "Origin conversation unavailable" in UI


class TestVoiceGuards:
    """All three switch paths, not just resume (docs/23 §8.2)."""

    def _guarded(self, fn_name):
        block = UI.split(f"function {fn_name}(")[1].split("\n}")[0]
        return "if (voice)" in block

    def test_resume_is_guarded(self):
        assert self._guarded("resumeSession")

    def test_read_through_is_guarded(self):
        assert self._guarded("viewSession")

    def test_new_session_is_guarded(self):
        assert self._guarded("newSession")


class TestAccessibility:
    def test_listbox_semantics(self):
        assert 'role="listbox"' in UI
        assert 'role="option"' in UI
        assert 'role="combobox"' in UI
        assert 'aria-selected="${index === searchActive}"' in UI

    def test_keyboard_contract(self):
        assert '(e.metaKey || e.ctrlKey) && (e.key === "k"' in UI
        assert 'e.key === "ArrowDown"' in UI
        assert 'e.key === "ArrowUp"' in UI
        assert "moveSearchActive" in UI

    def test_live_region_announces_status(self):
        assert 'aria-live="polite"' in UI

    def test_selection_is_not_colour_only(self):
        # The active row also carries a text marker.
        assert '.connrow[aria-selected="true"] .grow b::before' in UI

    def test_touch_targets_and_tokens(self):
        assert "min-height: 44px" in UI
        chips = UI.split(".chiprow {")[1].split("}")[0]
        assert "var(--sp-" in chips
        assert "#" not in chips  # no raw hex colours


class TestNoRemoteContent:
    def test_results_render_no_third_party_content(self):
        block = UI.split("function resultRow")[1].split("\nfunction ")[0]
        assert "<img" not in block
        assert "http" not in block
        assert "esc(" in block  # every interpolation is escaped


class TestSameSessionFocus:
    """A result already in the open conversation focuses in place; it must
    not route through viewSession, which refuses with a toast (docs/23 §8.2)."""

    def test_current_session_results_focus_in_place(self):
        block = UI.split("function openSearchResult")[1].split("\n}")[0]
        assert "const here = viewingSession || sessionId;" in block
        assert "row.session_id !== here" in block

    def test_transcript_swap_is_awaited_before_focus(self):
        block = UI.split("function openSearchResult")[1].split("\n}")[0]
        assert "await viewSession(" in block
        assert block.index("await viewSession(") < block.index("focusResource(row)")
