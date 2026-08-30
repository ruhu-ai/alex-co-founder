"""Static UI lifecycle assertions for the in-app Browser observation plane."""

from pathlib import Path

HTML = (Path(__file__).parents[2] / "app/static/index.html").read_text()


def test_browser_uses_one_visibility_scoped_eventsource_without_refresh_poll():
    assert "new EventSource(`/api/v1/browser/events?session_id=" in HTML
    assert "if (browserEventSource && browserEventSession === context) return" in HTML
    assert "const context = contextSessionId();" in HTML
    assert 'document.visibilityState === "hidden"' in HTML
    assert "else closeBrowserEvents()" in HTML
    refresh_body = HTML.split("async function refresh() {", 1)[1].split("\n}", 1)[0]
    assert "loadBrowserState" not in refresh_body
    # No timer may drive a network read or a state refresh. Stated as the
    # invariant rather than a blanket ban on setInterval, because a purely
    # local render heartbeat (the docs/24 elapsed clock) reaches no server.
    import re

    for match in re.finditer(r"setInterval\(\s*([A-Za-z0-9_.]+)", HTML):
        callback = match.group(1)
        assert callback not in ("refresh", "loadBrowserState", "refreshWaiting",
                                "syncChat", "connectBrowserEvents"), callback
    for match in re.finditer(r"setInterval\(\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{(.*?)\}\s*,",
                             HTML, re.S):
        body = match.group(1)
        assert "api(" not in body and "fetch(" not in body
        assert "EventSource" not in body


def test_browser_surface_is_screenshot_only_and_monotonic():
    surface = HTML.split('<section id="browserSurface"', 1)[1].split("</section>", 1)[0]
    assert "<iframe" not in surface
    assert 'id="browserStage"' in surface
    assert "version < currentVersion" in HTML
    assert "Number(frame.seq || 0) < priorSeq" in HTML
    assert "browserFrames = browserFrames.slice(-12)" in HTML


def test_fill_stop_warns_and_sends_owned_run_id():
    assert "Unsaved portal entries may be discarded" in HTML
    assert "run_id: browserSnapshot.run_id" in HTML
    assert '$("browserStopBtn").hidden = !nonterminal' in HTML


def test_approval_ui_uses_workspace_scoped_receipted_v1_api():
    assert "/api/v1/approvals?session_id=" in HTML
    assert "/api/v1/approvals/${id}:decide" in HTML
    assert 'client_request_id: "approval_" + crypto.randomUUID()' in HTML
    assert "/api/approvals/${id}/resolve" not in HTML


def test_investor_outreach_ui_exposes_drafts_uncertainty_and_exact_send():
    assert 'id="investorRuns"' in HTML
    assert 'api("/api/v1/investor-outreach")' in HTML
    assert "/api/v1/outreach-drafts/${encodeURIComponent(draftId)}:request-approval" in HTML
    assert "/api/v1/outreach-drafts/${encodeURIComponent(draftId)}:send" in HTML
    assert "Send exact approved email" in HTML
    assert "Delivery uncertain · reconciling, do not resend" in HTML
    assert "/api/v1/runs/${encodeURIComponent(runId)}:${operation}" in HTML
    assert "/api/v1/investor-outreach/${encodeURIComponent(outreachId)}:retry" in HTML
    assert "Run closed · no action available" in HTML


def test_feedback_ui_uses_workspace_scoped_receipted_v1_api():
    assert 'api("/api/v1/feedback"' in HTML
    assert 'api("/api/feedback"' not in HTML


def test_founder_inbox_ui_uses_workspace_scoped_receipted_v1_api():
    assert "/api/v1/founder-inbox?status=" in HTML
    assert "/api/v1/founder-inbox/${encodeURIComponent(inboxId)}:resolve" in HTML
    assert "/api/v1/founder-inbox/${encodeURIComponent(inboxId)}:dismiss" in HTML
    assert "inbox_resolve_" in HTML and "inbox_dismiss_" in HTML


def test_connector_mutations_use_workspace_scoped_receipted_v1_apis():
    assert 'api("/api/v1/integrations")' in HTML
    assert "/api/v1/integrations/${encodeURIComponent(row.connection_id)}" in HTML
    assert 'api("/api/v1/integrations/drive/files"' in HTML
    assert 'api("/api/v1/integrations/alex-drive/files?limit=25")' in HTML
    assert 'api("/api/v1/integrations/gmail/label"' in HTML
    assert 'api("/api/v1/integrations/alex_mail:watch"' in HTML
    assert "client_request_id" in HTML


def test_connector_catalog_uses_the_cloud_safe_versioned_route():
    assert 'await api("/api/v1/connectors")' in HTML
    assert 'await api("/api/connectors")' not in HTML


def test_alex_drive_is_separate_and_generated_documents_target_it():
    assert 'id="connAlexDrive"' in HTML
    assert 'alex_drive: "connAlexDrive"' in HTML
    assert 'api("/api/v1/integrations/alex-drive/files?limit=25")' in HTML
    assert ':sync-alex-drive`' in HTML
    assert "Save to Alex's Drive" in HTML


def test_memory_panel_recovers_when_session_and_config_bootstrap_race():
    assert HTML.count('$("settings").dataset.open === "true"') >= 3
    assert "await loadMemoryPanel();" in HTML


def test_connector_list_is_grouped_by_account_ownership():
    assert 'const CONNECTOR_ACCOUNT_GROUPS = [' in HTML
    founder = HTML.index('{key: "founder", label: "Founder"')
    alex = HTML.index('{key: "alex", label: "Alex"')
    builtin = HTML.index('{key: "builtin", label: "Built-in tools"')
    assert founder < alex < builtin
    assert 'connector.account_group || LEGACY_CONNECTOR_ACCOUNT_GROUP' in HTML
    assert 'aria-label="${esc(group.label)} connectors"' in HTML
    assert 'group.key === "builtin"' in HTML


def test_chat_ui_uses_workspace_scoped_receipted_v1_messages():
    assert 'api("/api/v1/messages"' in HTML
    assert 'api("/wake"' not in HTML
    assert "voice_message_" in HTML


def test_browser_ui_uses_workspace_scoped_receipted_v1_apis():
    assert "/api/v1/browser/state?session_id=" in HTML
    assert 'api("/api/v1/browser:stop"' in HTML
    assert "browser_stop_" in HTML


def test_session_ui_uses_workspace_scoped_v1_resources():
    for path in ("/api/v1/sessions", "/api/v1/workspace-brief", "/api/v1/search"):
        assert path in HTML
    # The released M1 brief is the only waiting-area read. The legacy waits
    # projection can include pending signals and must not be a UI fallback.
    assert "/api/v1/waits" not in HTML
    assert "/api/v1/sessions/${encodeURIComponent(context)}/resources" in HTML
    assert "session_delete_" in HTML


def test_ingestion_ui_uses_workspace_scoped_receipted_v1_apis():
    assert 'appFetch("/api/v1/ingestions"' in HTML
    assert "/api/v1/ingestions/${encodeURIComponent(ref)}" in HTML
    assert "profile_decision_" in HTML
    assert "ingestion_upload_" in HTML
