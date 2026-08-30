"""Static contracts for the reviewed unified Alex and Hiring experience."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
INDEX = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
HIRING = (ROOT / "app/static/hiring.html").read_text(encoding="utf-8")
OPEN_ROLE = (ROOT / "app/static/hiring-notice.html").read_text(encoding="utf-8")
PRODUCT_SHELL = (ROOT / "app/static/product-shell.css").read_text(encoding="utf-8")
VOICE_ORB_CSS = (ROOT / "app/static/alex-voice-orb.css").read_text(encoding="utf-8")
VOICE_ORB_JS = (ROOT / "app/static/alex-voice-orb.js").read_text(encoding="utf-8")


def squashed(value: str) -> str:
    return re.sub(r"\s+", "", value)


INDEX_SQUASHED = squashed(INDEX)
HIRING_SQUASHED = squashed(HIRING)


def test_theme_is_resolved_before_stylesheets_to_prevent_global_repaint():
    head = INDEX.split("<head>", 1)[1].split("</head>", 1)[0]
    bootstrap = 'localStorage.getItem("cofounder-theme")'
    stylesheet = '<link rel="stylesheet" href="/product-shell.css?v=20260829-header-left"/>'
    assert '<html lang="en" data-theme=' not in INDEX.split("<head>", 1)[0]
    assert bootstrap in head
    assert 't==="light"||t==="dark"' in head
    assert head.index(bootstrap) < head.index(stylesheet)


def test_alex_shell_is_conversation_first_with_stable_hiring_route():
    assert 'class="product-rail"' in INDEX
    assert 'padding: calc(var(--header-h) + var(--sp-3)) 6px var(--sp-2)' in INDEX
    assert 'data-destination-link="hiring"href="/hiring.html"' in INDEX_SQUASHED
    assert 'data-destination="alex" data-workspace="quiet"' in INDEX
    assert 'grid-template-columns: minmax(560px, 1fr) var(--workspace-w, 46px)' in INDEX
    assert '#chatlog {' in INDEX
    assert 'scrollbar-gutter: stable' in INDEX
    assert '#chatlog::-webkit-scrollbar{width:4px;}' in INDEX_SQUASHED
    assert 'scrollbar-color: transparent transparent' in INDEX
    assert '#chatlog[data-scrolling="true"]::-webkit-scrollbar-thumb{background:var(--field);}' in INDEX_SQUASHED
    assert 'width:min(calc(100%-2*var(--sp-4)),760px);align-self:center' in INDEX_SQUASHED
    assert 'width:min(calc(100%-2*var(--sp-4)),760px)' in INDEX_SQUASHED


def test_contextual_workspace_has_reviewed_vocabulary_and_tabs():
    tabs = INDEX.split("const REF_TABS = [", 1)[1].split("];", 1)[0]
    assert [label in tabs for label in ('label: "Work"', 'label: "Evidence"',
                                         'label: "Decisions"', 'label: "Activity"')] == [True] * 4
    assert "The form" not in tabs
    assert 'data-workspace="quiet"' in INDEX
    assert '--workspace-w: 46px' in INDEX
    assert 'min(var(--workspace-user-w,520px),min(48vw,600px,calc(100vw-624px)))' in INDEX_SQUASHED
    assert 'grid-template-areas: "workspace conversation"' in INDEX
    assert 'grid-template-columns: minmax(560px, 3fr) minmax(480px, 2fr)' in INDEX


def test_decisions_empty_state_is_quiet_and_pending_items_are_actionable():
    assert '.workspace-empty{display:none;margin-top:var(--sp-4)' in INDEX_SQUASHED
    assert '#pane-decisions>.workspace-empty>:not(b),#pane-decisions>button{display:none;}' in INDEX_SQUASHED
    assert 'Nothing needs your judgment' in INDEX
    assert 'function renderDecisionApprovals()' in INDEX
    assert 'data-approval-id=' in INDEX
    assert 'Review decision' in INDEX
    assert 'nextElementSibling.textContent = "Decline"' in INDEX
    assert 'renderDecisionApprovals();' in INDEX.split(
        'async function refreshGateBanner()', 1,
    )[1].split('function showApproval', 1)[0]


def test_browser_takes_over_workspace_and_conversation_survives_focus_stage():
    work = INDEX.split('id="pane-work"', 1)[1].split('id="pane-evidence"', 1)[0]
    left = INDEX.split('id="leftCell"', 1)[1].split('id="chat"', 1)[0]
    assert 'id="browserSurface"' not in work
    assert 'id="browserSurface"' not in left
    assert 'id="browserLauncher"' not in work
    header = INDEX.split('class="workspace-head"', 1)[1].split('</div>', 1)[0]
    assert 'id="browserLauncher"' in header and 'aria-label="Open Browser"' in header
    assert 'browser-launcher' not in INDEX
    assert 'body[data-browser-takeover="true"]:is(.workspace-head,#approvalSlot,#refTabs,.refzone)' in INDEX_SQUASHED
    assert 'function closeBrowserTakeover()' in INDEX
    assert 'restore' not in work.lower()  # restoration is workspace state, not a nested renderer
    assert 'min(var(--browser-user-w,40vw)' in INDEX_SQUASHED
    assert 'function toggleFocusStage()' in INDEX
    assert 'Return to conversation layout' in INDEX


def test_voice_cloud_is_lifecycle_gated_and_approval_is_orthogonal():
    assert 'cloud.dataset.approval = liveUi.approval ? "pending" : "none"' in INDEX
    assert '.alex-cloud[data-approval="pending"]::after' in VOICE_ORB_CSS
    assert 'liveUi.speaking){state="SPEAKING"' in INDEX_SQUASHED
    assert 'id="captionToggle"' in INDEX
    assert 'id="alexCloudCanvas"' in INDEX
    assert 'id="alexLive"aria-label="Alexvoicestatus"hidden' in INDEX_SQUASHED
    assert 'Math.min(2, devicePixelRatio || 1)' in INDEX
    assert re.search(r'(?:1000|1e3)\s*/\s*30', INDEX)
    assert 'alex-orb' not in INDEX
    assert 'href="#i-sparkle"' not in INDEX and 'ico("sparkle"' not in INDEX


def test_session_work_is_one_collapsed_inline_disclosure_with_wrapping_work():
    assert 'id="contextBtn"' in INDEX and 'aria-controls="sessionWork"' in INDEX
    assert 'class="conversation-context-row"' in INDEX
    assert 'title="Work in this session"' in INDEX
    assert 'onclick="toggleSessionWork()"' in INDEX
    assert 'grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr))' in INDEX
    assert '.session-work-list { display: flex' not in INDEX
    chat = INDEX.split('id="chat"', 1)[1].split('id="review"', 1)[0]
    assert 'id="sessionWork"' in chat
    assert 'id="sessionWork"' not in INDEX.split('id="pane-work"', 1)[1].split('id="pane-evidence"', 1)[0]
    assert 'contextSummary' not in INDEX
    assert '.context-control.badge,.session-work-head.badge' in INDEX_SQUASHED
    assert 'color: var(--ink-1)' in INDEX
    assert '.context-control[aria-expanded="true"]' in INDEX
    assert 'border-radius:var(--r-full);background:var(--surface-1);color:var(--accent-ink)' in INDEX_SQUASHED


def test_documents_are_canonical_in_work_and_join_exact_assistant_events():
    assert 'id="workDocuments"' in INDEX
    assert 'data-invocation-id' in INDEX
    assert 'occurrence.occurrence_key' in INDEX
    assert 'turn.appendChild(reference)' in INDEX
    assert '$("chatlog").appendChild(div)' not in INDEX[
        INDEX.index('function renderSessionDocCards()'):INDEX.index('// provenance navigation')]


def test_navigation_has_one_search_and_one_new_session_without_raw_header_id():
    assert INDEX.count('onclick="openSessions()"') == 1
    assert 'id="sessionsBtn"' not in INDEX
    assert INDEX.count('onclick="newSession') == 1
    assert 'class="rail-link rail-new-session"' in INDEX and '<span>New</span>' in INDEX
    assert 'background:var(--new-session-bg);color:var(--new-session-ink)' in INDEX_SQUASHED
    assert '--new-session-bg:#2f5bd0' in INDEX_SQUASHED.lower()
    assert '--new-session-ink:#ffffff' in INDEX_SQUASHED.lower()
    rail = INDEX.split('<nav class="product-rail"', 1)[1].split('</nav>', 1)[0]
    assert rail.index('rail-new-session') < rail.index('data-destination-link="alex"')
    order = [rail.index(f'data-destination-link="{name}"')
             for name in ("alex", "search", "runs", "hiring", "decisions", "activity")]
    assert order == sorted(order)
    assert 'rail-mark' not in rail and 'title="Co-Founder"' not in rail
    assert INDEX.index('class="brand-home rail-brand"') < INDEX.index(
        '<nav class="product-rail"')
    assert 'inset: 0 auto auto 0' in INDEX
    header = INDEX.split('<header>', 1)[1].split('</header>', 1)[0]
    assert 'id="contextTitle">Alex</div>' in header
    assert 'body>header>:is(.brand-home,.context-title){display:none;}' in INDEX_SQUASHED
    assert '#i-sparkle' not in rail
    assert '$("sessionTag").textContent = "session " +' not in INDEX
    assert '$("setSession").textContent = sessionId ||' not in INDEX


def test_brand_mark_uses_the_same_reserved_top_left_slot_in_product_shells():
    for html in (INDEX, HIRING):
        assert html.count('class="brand-home rail-brand"') == 1
        assert html.index('class="brand-home rail-brand"') < html.index(
            '<nav class="product-rail"')
        brand = html.split('class="brand-home rail-brand"', 1)[1].split(
            '</a>', 1)[0]
        assert 'href="#brand-mark"' in brand

    hiring_header = HIRING.split('<header class="top">', 1)[1].split(
        '</header>', 1)[0]
    assert 'class="brand-home"' not in hiring_header
    assert 'padding: calc(var(--header-h) + var(--sp-3)) 6px var(--sp-2)' in HIRING
    assert 'color: var(--accent)' in HIRING
    assert 'z-index: 33' in HIRING
    assert 'justify-content:center' in HIRING_SQUASHED
    assert '.rail-brand,.rail-spacer' not in HIRING_SQUASHED
    assert 'padding-left:calc(56px+var(--sp-2))' in HIRING_SQUASHED


def test_account_menu_does_not_duplicate_primary_hiring_navigation():
    rail = INDEX.split('<nav class="product-rail"', 1)[1].split('</nav>', 1)[0]
    account_menu = INDEX.split('id="acctMenu"', 1)[1].split(
        'id="inboxBtn"', 1)[0]
    assert 'data-destination-link="hiring"' in rail
    assert 'Hiring operations' not in account_menu
    assert "location.href='/hiring.html'" not in account_menu


def test_account_menu_is_consistent_and_left_aligned_in_every_authenticated_header():
    index_header = INDEX.split("<header>", 1)[1].split("</header>", 1)[0]
    hiring_header = HIRING.split('<header class="top">', 1)[1].split(
        "</header>", 1)[0]
    for header in (index_header, hiring_header):
        assert 'id="acctWrap" class="account-wrap" data-menu-align="left"' in header
        assert 'id="acctMenu"' in header and "account-menu" in header
        assert header.index('id="acctWrap"') < header.index('class="spacer"')
        for label in ("Profile", "Connectors", "Settings", "Log out"):
            assert label in header
    assert 'href="/?open=profile"' in hiring_header
    assert 'href="/?open=connectors"' in hiring_header
    assert 'href="/?open=settings"' in hiring_header
    assert 'launch.get("open") === "profile"' in INDEX
    assert 'launch.get("open") === "connectors"' in INDEX
    assert '.account-wrap[data-menu-align="left"] .account-menu' in PRODUCT_SHELL


def test_main_mail_and_account_controls_precede_header_context():
    header = INDEX.split("<header>", 1)[1].split("</header>", 1)[0]
    assert header.index('id="acctWrap"') < header.index('id="inboxBtn"')
    assert header.index('id="inboxBtn"') < header.index('id="contextTitle"')


def test_settings_workspace_omits_internal_workflow_configuration():
    settings = INDEX.split('id="settings"', 1)[1].split(
        'id="visionConsent"', 1)[0]
    assert "Loaded from the server config" not in settings
    assert "workflows/grant_applications.yaml" not in settings
    assert "<b>Workflow</b>" not in settings
    assert 'id="setWorkflow"' not in INDEX


def test_runs_is_a_global_cross_operation_page_with_separate_boundaries():
    board = INDEX.split('id="board"', 1)[1].split('id="splitHandle"', 1)[0]
    assert 'id="boardTitle">Runs</h2>' in board
    assert "Funding, Hiring, and skill-backed work" in board
    for operation in ("hiring", "funding", "skills"):
        assert f'data-run-filter="{operation}"' in board
    assert 'id="globalRuns"' in board
    assert 'id="fundingDetails"' in board
    assert 'id="futureOperations"' in board
    assert "Planned—not active" in board
    assert 'api("/api/v1/runs")' in INDEX
    assert 'api(`/api/v1/pipeline?session_id=${encodeURIComponent(context)}`)' in INDEX
    assert 'api("/api/v1/investor-outreach")' in INDEX
    assert '"All operations"' in INDEX
    assert '$("sessionTag").hidden = current === "runs"' in INDEX
    assert 'document.body.dataset.pane = destination === "runs" ? "board" : "chat"' in INDEX
    assert "Candidate identity, evidence, and decisions stay inside Hiring" in INDEX
    assert 'href="/hiring.html">Open in Hiring</a>' in INDEX
    assert "run-candidate" not in INDEX


def test_runs_dashboard_stays_bounded_and_opens_paginated_history():
    assert 'id="recentRuns"' in INDEX
    assert 'id="runHistory"' in INDEX
    assert 'id="runHistorySearch"' in INDEX
    assert 'id="runHistoryOperation"' in INDEX
    assert 'id="runHistoryStatus"' in INDEX
    assert 'const RUN_DASHBOARD_LIMIT = 12' in INDEX
    assert 'const RUN_RECENT_LIMIT = 6' in INDEX
    assert 'const RUN_HISTORY_PAGE_SIZE = 25' in INDEX
    assert 'groupCompletedRunTitles(completed)' in INDEX
    assert 'Review ${options.groupCount} runs' in INDEX
    assert 'current.slice(0, RUN_DASHBOARD_LIMIT)' in INDEX
    assert 'recentGroups.slice(0, RUN_RECENT_LIMIT)' in INDEX
    assert 'id="runHistoryMore"' in INDEX
    assert 'rows.slice(0, runHistoryVisibleCount)' in INDEX
    assert 'function loadMoreRunHistory()' in INDEX
    assert 'Open run history (${filtered.length})' in INDEX
    assert 'RUN_ATTENTION_STATUSES' in INDEX
    assert 'prefers-reduced-motion: reduce' in INDEX
    assert "Candidate identity, evidence, and decisions stay inside Hiring" in INDEX


def test_growing_lists_use_load_more_and_stale_while_refreshing_contract():
    assert "function visibleListRows(" in INDEX
    assert "function loadMoreFooter(" in INDEX
    for key in (
        "investor-outreach", "funding-applications", "session-work",
        "session-activity", "work-documents", "evidence-attachments",
    ):
        assert f'"{key}"' in INDEX
    assert 'Refresh failed — showing the last data Alex sent.' in INDEX
    assert 'document.body.dataset.refreshing = "true"' in INDEX
    assert "captureScrollState()" in INDEX
    assert "restoreScrollState(scrollState)" in INDEX
    assert "rolesVisibleCount = 12" in HIRING
    assert "candidateVisibleCount = 25" in HIRING
    assert "function loadMoreRoles()" in HIRING
    assert "function loadMoreCandidates()" in HIRING
    assert "Refresh failed. The last roles remain visible." in HIRING
    assert "Active roles could not load" in HIRING
    assert "Retry loading roles" in HIRING
    assert '"?include_demo=true"' in HIRING
    assert '{ timeoutMs: 12000 }' in HIRING
    assert "restoreHiringScroll(scrollState)" in HIRING


def test_rail_and_composer_use_unambiguous_generated_icons():
    rail = INDEX.split('<nav class="product-rail"', 1)[1].split('</nav>', 1)[0]
    for destination, icon in (
        ("alex", "chat"), ("search", "search"), ("runs", "board"),
        ("hiring", "hiring"), ("decisions", "shield"),
        ("activity", "clock"),
    ):
        item = rail.split(f'data-destination-link="{destination}"', 1)[1]
        end = min(index for index in (item.find("</button>"), item.find("</a>"))
                  if index >= 0)
        item = item[:end]
        assert f'href="#i-{icon}"' in item
    new_item = rail.split("rail-new-session", 1)[1].split("</button>", 1)[0]
    settings_item = rail.split('class="rail-link rail-utility"', 1)[1].split(
        "</button>", 1)[0]
    assert 'href="#i-plus"' in new_item
    assert 'href="#i-gear"' in settings_item
    camera = INDEX.split('id="cameraBtn"', 1)[1].split("</button>", 1)[0]
    display = INDEX.split('id="displayBtn"', 1)[1].split("</button>", 1)[0]
    assert 'href="#i-camera"' in camera
    assert 'href="#i-screen-share"' in display


def test_mobile_destinations_are_fixed_and_more_contains_activity_and_search():
    for label in (">Alex</span>", ">Runs</span>", ">Hiring</span>",
                  ">Decisions</span>", ">More</span>"):
        assert label in INDEX
    menu = INDEX.split('id="mobileMore"', 1)[1].split("</div>", 1)[0]
    assert "Activity" in menu and "Search" in menu
    assert 'width:42px;height:42px;bottom:auto;background:transparent' in INDEX_SQUASHED


def test_hiring_is_a_dedicated_shared_shell_with_scoped_candidate_work():
    assert 'class="hiring-shell"' in HIRING
    assert 'href="/hiring.html"aria-current="page"' in HIRING_SQUASHED
    assert 'href="/product-shell.css?v=20260829-header-left"' in HIRING
    assert 'data-view="roles"' in HIRING and 'data-workspace="quiet"' in HIRING
    assert '<h2 id="roleIndexTitle">Active roles</h2>' in HIRING
    assert 'class="role-card"' in HIRING
    for tab in ("work", "evidence", "decisions", "activity"):
        assert f'data-hiring-tab="{tab}"' in HIRING
        assert f'id="hiring-pane-{tab}"' in HIRING
    assert "Candidate workspace" in HIRING
    assert "Founder view" in HIRING
    assert 'id="candidateIdentityScope"' in HIRING
    assert "hiring_candidate=" in HIRING
    assert "/candidate-conversations/answer" not in HIRING
    assert "/applications/${encodeURIComponent(candidateId)}/conversations" not in HIRING
    assert "candidate-pane-evidence" in HIRING
    assert "selectCandidateTab" in HIRING
    assert '.candidate-section-nav .btn[aria-selected="true"]' in HIRING
    assert "background: var(--surface-1);" in HIRING
    assert "box-shadow: var(--e-1);" in HIRING
    assert "Open CV" in HIRING
    assert "/api/hiring/applications/${encodeURIComponent(candidateId)}/resume" in HIRING
    assert 'Cache-Control' not in HIRING  # transport owns the no-store header
    assert "activeHiringCandidateContext" in INDEX
    assert "hiring_candidate_id" in INDEX
    assert "/conversation-context`" in INDEX
    assert "Candidate communication &amp; interview" in HIRING
    assert "exact applicant thread and confirmed availability only" in HIRING
    assert "One Founder approval confirms the candidate" in HIRING
    assert "/coordination/contact" in HIRING
    assert "/coordination/interview" in HIRING
    assert "Approve Alex to coordinate this interview" in HIRING
    assert "Copy the Founder on Alex’s applicant emails" in HIRING
    assert "SYNTHETIC FIXTURE · BOUNDED INTERNAL DEMO" not in HIRING
    assert "Synthetic fixture · bounded internal demo" not in HIRING
    assert "Founder-controlled drafts" not in HIRING
    assert "Synthetic sandbox active" not in HIRING
    assert "Read-only test role" in HIRING
    assert "innerWidth>=900||candidateId" not in HIRING_SQUASHED
    assert "alex-orb" not in HIRING[HIRING.index("<body>"):]
    assert 'class="candidate-route"' in HIRING
    assert 'function closeCandidate()' in HIRING


def test_hiring_home_is_roles_first_without_setup_or_demo_controls():
    assert 'class="role-index" aria-labelledby="roleIndexTitle"' in HIRING
    assert ".role-index {" in HIRING and "width: 100%;" in HIRING
    assert 'class="role-index-empty" role="status"' in HIRING
    assert "No active roles" in HIRING
    assert "Hiring roles you create with Alex will appear here" in HIRING
    assert '>Go to Alex</a>' in HIRING
    for removed in (
        "Start a hiring run with Alex",
        "Controlled demo lane",
        'id="hiringCommand"',
        'id="beginInternalDemo"',
        "async function startHiringRun()",
        "/api/hiring/internal-demo/runs",
    ):
        assert removed not in HIRING


def test_founder_hiring_package_is_readable_and_exact_approval_is_primary():
    assert "Founder-controlled drafts" not in HIRING
    assert "FOUNDER DRAFT · INTERNAL REVIEW" not in HIRING
    assert "DRAFT ROLE · INTERNAL REVIEW" in HIRING
    assert "Job description draft" in HIRING
    for label in (
        "Role purpose / overview", "Responsibilities",
            "Required qualifications", "Preferred qualifications",
            "Relevant experience", "Success outcomes", "Hiring process",
        "Location", "Employment type", "Application instructions",
        "Save edits as draft", "Review public job page", "Hiring scorecard",
        "Interview plan",
    ):
        assert label in HIRING
    assert "Discuss with Alex" in HIRING
    assert HIRING.count("Discuss with Alex</a>") == 2
    assert 'id="hiringAlexDock"' not in HIRING
    assert "Applications are not open yet" in HIRING
    for editable in (
            "purpose", "responsibilities", "required_qualifications",
            "preferred_qualifications", "relevant_experience", "location",
            "work_arrangement", "employment_type", "hiring_process",
            "application_instructions"):
        assert f'name="{editable}"' in HIRING
    assert "decision-actions" in HIRING
    assert "min-height: 42px" in HIRING
    assert 'class="btn" data-variant="primary" id="approvePolicy"' in HIRING
    assert "Approve exact role package for internal use" in HIRING
    assert "It does not publish, email, source, rank, decide, or contact anyone" in HIRING
    assert 'e.errorCode === "step_up_required"' in HIRING
    assert "Sign in again" in HIRING
    assert "/login.html?fresh=1&next=" in HIRING
    assert "policyApprovalRequestId(role, policy)" in HIRING
    assert "sessionStorage.getItem(key)" in HIRING
    assert "actions.append(review, approve)" in HIRING
    assert "decision.append(actions)" in HIRING
    assert "Candidate intake inbox" in HIRING
    assert "present, missing, or unclear" in HIRING
    assert "/candidate-conversations/answer" not in HIRING
    assert "/applications/${encodeURIComponent(candidateId)}/conversations" not in HIRING
    assert "complete criterion map, citations, unknowns, contradictions" in HIRING
    assert "fixed email quota" not in HIRING
    assert "No active synthetic sandbox is scoped to this role" not in HIRING
    assert "Only the Founder can explicitly change a phase or decline" in HIRING


def test_candidate_open_role_is_receipt_gated_and_contains_no_internal_controls():
    assert "Open role · Co-Founder" in OPEN_ROLE
    assert "Job description not live" in OPEN_ROLE
    assert "manual publication receipt exists" in OPEN_ROLE
    for label in (
            "Overview", "Responsibilities", "Requirements", "Working model",
            "Employment", "Apply for this role", "Full name", "Email address",
            "Message", "(optional)", "CV or resume", "Submit application"):
        assert label in OPEN_ROLE
    form_start = OPEN_ROLE.index('<form id="applicationForm"')
    form_end = OPEN_ROLE.index("</form>", form_start)
    application_form = OPEN_ROLE[form_start:form_end]
    assert application_form.count("<button") == 1
    assert ('id="applicantName" name="applicant_name" type="text" '
            'autocomplete="name" maxlength="160" required') in application_form
    assert 'id="coverNote"' in application_form
    assert 'id="coverNote" name="cover_note" maxlength="4000" required' not in application_form
    assert OPEN_ROLE.index('<div class="job-description"') < form_start
    assert 'id="applicationForm"' not in HIRING
    assert "View public job page" in HIRING
    assert "Copy application link" in HIRING
    assert "PUBLISHED ROLE · APPLICATION INTAKE" in HIRING
    assert "No applications have been received for this role yet." in HIRING
    assert '"Evidence"' in HIRING
    assert '"Waiting for Alex"' in HIRING
    assert '"Ready"' in HIRING
    assert '"Alex is preparing evidence"' in HIRING
    assert '"View evidence"' in HIRING
    assert "Refresh status" in HIRING
    assert 'id="mapEvidenceButton"' not in HIRING
    assert "/api/hiring/applications/${encodeURIComponent(id)}/assess" not in HIRING
    assert "Alex owns this preparation" in HIRING
    assert 'id="applicationLinkStatus" role="status" aria-live="polite"' in HIRING
    assert "/api/public/hiring/roles/" in OPEN_ROLE
    assert "/applications" in OPEN_ROLE
    assert 'accept=".pdf,.docx,application/pdf' in OPEN_ROLE
    assert "Resume files must be 5 MB or smaller" in OPEN_ROLE
    assert "it is never inferred from your CV" in OPEN_ROLE
    assert "This address is dedicated to this role" in OPEN_ROLE
    assert "Not specified" not in OPEN_ROLE
    assert "Founder preview" in OPEN_ROLE
    assert 'role="status" aria-live="polite"' in OPEN_ROLE
    for forbidden in (
            "Hiring scorecard", "Interview plan", "Approve exact",
            "candidate assessment", "policy hash", "provider credential"):
        assert forbidden not in OPEN_ROLE
    assert "Alex · synthetic Hiring Run" not in HIRING


def test_unified_ui_does_not_add_a_client_authority_path():
    assert "/api/v1/approvals/${id}:decide" in INDEX
    assert 'client_request_id: "approval_" + crypto.randomUUID()' in INDEX
    assert "Chat/voice" not in INDEX  # no client prose parser is introduced
    assert "/api/hiring/applications/${encodeURIComponent(id)}/decisions" in HIRING
    assert "expected_application_version:version" in HIRING_SQUASHED
