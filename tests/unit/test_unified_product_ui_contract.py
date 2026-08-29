"""Static contracts for the reviewed unified Alex and Hiring experience."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
INDEX = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
HIRING = (ROOT / "app/static/hiring.html").read_text(encoding="utf-8")
OPEN_ROLE = (ROOT / "app/static/hiring-notice.html").read_text(encoding="utf-8")
VOICE_ORB_CSS = (ROOT / "app/static/alex-voice-orb.css").read_text(encoding="utf-8")
VOICE_ORB_JS = (ROOT / "app/static/alex-voice-orb.js").read_text(encoding="utf-8")


def squashed(value: str) -> str:
    return re.sub(r"\s+", "", value)


INDEX_SQUASHED = squashed(INDEX)
HIRING_SQUASHED = squashed(HIRING)


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
    assert 'Keep the header-aligned rail slot intentionally blank' in INDEX
    assert '#i-sparkle' not in rail
    assert '$("sessionTag").textContent = "session " +' not in INDEX
    assert '$("setSession").textContent = sessionId ||' not in INDEX


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
    assert 'href="/product-shell.css"' in HIRING
    assert 'data-view="roles"' in HIRING and 'data-workspace="quiet"' in HIRING
    assert 'Start a hiring run with Alex' in HIRING
    assert 'class="role-card"' in HIRING
    for tab in ("work", "evidence", "decisions", "activity"):
        assert f'data-hiring-tab="{tab}"' in HIRING
        assert f'id="hiring-pane-{tab}"' in HIRING
    assert "Candidate workspace" in HIRING
    assert "identity hidden" in HIRING
    assert "/applications/${encodeURIComponent(candidateId)}/conversations" in HIRING
    assert "cannot score, rank, advise a hiring outcome, commit a decision, or perform an external action" in HIRING
    assert "Synthetic fixture · bounded internal demo" in HIRING
    assert "innerWidth>=900||candidateId" not in HIRING_SQUASHED
    assert "alex-orb" not in HIRING[HIRING.index("<body>"):]
    assert 'class="candidate-route"' in HIRING
    assert 'function closeCandidate()' in HIRING


def test_hiring_role_creation_uses_the_authenticated_v1_command_boundary():
    start = HIRING.split("async function startHiringRun()", 1)[1].split(
        "async function recordManualPublication", 1)[0]
    assert 'api("/api/v1/messages"' in start
    assert '"X-CSRF-Token": csrfToken' in start
    assert "csrfToken = await csrf()" in start
    assert 'api("/wake"' not in start


def test_founder_hiring_package_is_readable_and_exact_approval_is_primary():
    assert "Founder-controlled drafts" in HIRING
    assert "FOUNDER DRAFT · INTERNAL REVIEW" in HIRING
    assert "Generated job description" in HIRING
    for label in (
        "Role purpose / overview", "Responsibilities",
            "Must-have qualifications", "Preferred qualifications",
            "Relevant experience", "Success outcomes", "Hiring process",
        "Location and work arrangement", "Employment type",
        "Exact candidate-facing job-post copy", "Hiring scorecard",
        "Interview plan",
    ):
        assert label in HIRING
    assert "Discuss this role with Alex" in HIRING
    assert "Applications not open yet" in HIRING or "Not open yet" in HIRING
    assert "decision-actions" in HIRING
    assert "min-height: 42px" in HIRING
    assert 'class="job-post-copy" tabindex="0"' in HIRING
    assert 'class="btn" data-variant="primary" id="approvePolicy"' in HIRING
    assert "Approve exact role package for internal use" in HIRING
    assert "it does not publish, email, source, rank, decide, or contact anyone" in HIRING
    assert "actions.append(review, approve)" in HIRING
    assert "decision.append(actions)" in HIRING
    assert "Candidate intake inbox" in HIRING
    assert "present, missing, or unclear" in HIRING
    assert "/candidate-conversations/answer" in HIRING
    assert "/applications/${encodeURIComponent(candidateId)}/conversations" in HIRING
    assert "No active synthetic sandbox is scoped to this role" not in HIRING
    assert "Only the Founder can explicitly change a phase or decline" in HIRING


def test_candidate_open_role_is_receipt_gated_and_contains_no_internal_controls():
    assert "Open role · Co-Founder" in OPEN_ROLE
    assert "Job description not live" in OPEN_ROLE
    assert "manual publication receipt exists" in OPEN_ROLE
    for label in (
            "Overview", "Responsibilities", "Requirements", "Working model",
            "Employment", "Apply for this role", "Name", "Email address",
            "Message or cover note", "CV or resume", "Privacy consent"):
        assert label in OPEN_ROLE
    assert "/api/public/hiring/roles/" in OPEN_ROLE
    assert "/applications" in OPEN_ROLE
    assert 'accept=".pdf,.docx,application/pdf' in OPEN_ROLE
    assert "Resume files must be 5 MB or smaller" in OPEN_ROLE
    assert "it is never inferred from your resume" in OPEN_ROLE
    assert "This address is dedicated to this role" in OPEN_ROLE
    assert 'role="status" aria-live="polite"' in OPEN_ROLE
    for forbidden in (
            "Hiring scorecard", "Interview plan", "Approve exact",
            "candidate assessment", "policy hash", "provider credential"):
        assert forbidden not in OPEN_ROLE
    assert "if (manual && role.synthetic)" in HIRING
    assert "if (role.synthetic) loadH4S(id)" in HIRING


def test_unified_ui_does_not_add_a_client_authority_path():
    assert "/api/v1/approvals/${id}:decide" in INDEX
    assert 'client_request_id: "approval_" + crypto.randomUUID()' in INDEX
    assert "Chat/voice" not in INDEX  # no client prose parser is introduced
    assert "/api/hiring/applications/${encodeURIComponent(id)}/decisions" in HIRING
    assert "expected_application_version:version" in HIRING_SQUASHED
