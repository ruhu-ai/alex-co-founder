"""Top-left session-picker UI contract (docs/23 §8, WI-7).

Static contracts over app/static/index.html, matching the established style
of test_browser_ui_contract / test_attachment_ui_contract. These lock the
behaviours the review required proof of and that no server test can see.
"""

from __future__ import annotations

import pathlib

UI = (pathlib.Path(__file__).resolve().parents[2]
      / "app/static/index.html").read_text(encoding="utf-8")


class TestSurface:
    def test_button_and_dialog_are_sessions_only(self):
        assert 'title="Sessions (⌘K)"' in UI
        assert 'aria-label="Find a session"' in UI
        assert '<div class="conn-title">Sessions</div>' in UI

    def test_dialog_explains_the_session_workbench_preview(self):
        assert "Choose a session to preview everything linked to it." in UI
        assert 'placeholder="Search sessions"' in UI

    def test_uses_the_server_search_endpoint(self):
        assert '"/api/search?"' in UI
        assert 'params.set("types", "session")' in UI
        # The old client-side substring filter over previews is gone.
        assert "s.preview || \"\").toLowerCase().includes(q)" not in UI

    def test_resource_filters_and_groups_are_removed(self):
        assert 'id="searchChips"' not in UI
        assert "data-search-type" not in UI
        assert "SEARCH_GROUPS" not in UI


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
        assert "No sessions yet — start one" in UI
        assert "No session matches that search." in UI


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

    def test_touch_targets_remain_on_session_rows(self):
        assert "min-height: 44px" in UI


class TestNoRemoteContent:
    def test_results_render_no_third_party_content(self):
        block = UI.split("function resultRow")[1].split("\nfunction ")[0]
        assert "<img" not in block
        assert "http" not in block
        assert "esc(" in block  # every interpolation is escaped


class TestSessionSelection:
    """The picker changes session context; work is focused from its Work strip."""

    def test_current_session_results_focus_in_place(self):
        block = UI.split("function openSearchResult")[1].split("\n}")[0]
        assert "const here = contextSessionId();" in block
        assert "row.session_id !== here" in block

    def test_selection_only_opens_the_session(self):
        block = UI.split("function openSearchResult")[1].split("\n}")[0]
        assert "await viewSession(" in block
        assert "focusResource(row)" not in block


class TestSessionWorkbenchContext:
    def test_read_through_never_reassigns_the_active_session(self):
        block = UI.split("async function viewSession")[1].split("\n}")[0]
        assert "viewingSession = sid" in block
        assert "sessionId = sid" not in block
        assert "contextSessionId()" in block

    def test_every_workbench_read_uses_the_visible_session_context(self):
        assert '/api/pipeline?session_id=${encodeURIComponent(context)}' in UI
        assert '/api/sessions/${encodeURIComponent(context)}/resources?limit=2000' in UI
        assert '/api/documents?session_id=${encodeURIComponent(context)}' in UI
        assert '/api/browser/state?session_id=${encodeURIComponent(session)}' in UI
        assert '/api/waiting?session_id=${encodeURIComponent(context)}' in UI

    def test_session_outputs_are_visible_and_focusable(self):
        assert 'id="sessionWork"' in UI
        assert 'data-session-resource="${index}"' in UI
        assert "renderSessionWork();" in UI
        assert "focusResource(row);" in UI

    def test_read_through_blocks_mutating_actions(self):
        for fn in ("send", "runSweep", "browseTo", "stopBrowsing", "feedback",
                   "uploadDoc", "syncDrive", "ingestDrive", "toggleVoice",
                   "showApproval", "resolveApproval"):
            block = UI.split(f"function {fn}(")[1].split("\n}")[0]
            assert "requireActiveContext(" in block, fn
        assert 'function setReadThroughControls()' in UI
        assert 'id="resumeViewedBtn"' in UI

    def test_documents_and_attachments_are_session_scoped(self):
        assert 'sessionResources.filter(row => row.result_type === "artifact")' in UI
        assert "/source?session_id=${encodeURIComponent(contextSessionId())}" in UI
