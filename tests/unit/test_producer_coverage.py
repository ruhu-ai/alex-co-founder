"""Producer coverage (docs/23 §6.1, WI-4).

Adding a founder-visible producer without registering a session resource is a
failing test. The registry here is derived from the §6.1 matrix; each row is
either wired to `register_session_resource` or explicitly classified.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]

# Producer → the file that must call register_session_resource for it.
WIRED_PRODUCERS = {
    "discovery_request": "app/main.py",
    "discovered_opportunity": "app/main.py",
    "application_selection": "agents/co_founder/tools/pipeline.py",
    "document": "agents/co_founder/tools/documents.py",
    "upload": "app/main.py",
    "voice_note": "app/main.py",
    "browser_report": "services/browser_service.py",
    "evidence_report": "agents/co_founder/tools/drafting.py",
}

# Explicitly classified as internal/system, with the reason each carries no
# fabricated session link (docs/23 §6.1, invariant 4).
CLASSIFIED_NON_RESOURCES = {
    "founder_state": "proactive-delivery routing table",
    "document_versions": "monotonic counter",
    "source_state": "extraction dedupe pointer",
    "integrations": "connector config",
    "gmail_state": "scan cursor",
    "alex_mail_state": "scan cursor",
    "portal_registrations": "credential provenance",
    "oauth_states": "short-lived CSRF state",
    "audit": "append-only audit",
    "feedback": "parent-timeline child data",
    "approvals": "authorization records; never indexed",
    "mail_scan_followups": "system producer; Phase-1C inbox replaces it",
}


def test_every_wired_producer_registers_a_session_resource():
    missing = []
    for producer, relative in WIRED_PRODUCERS.items():
        source = (REPO / relative).read_text(encoding="utf-8")
        if "register_session_resource" not in source:
            missing.append(f"{producer} ({relative})")
    assert not missing, (
        "These founder-visible producers must call register_session_resource: "
        f"{missing}")


def test_document_production_takes_session_from_tool_context():
    source = (REPO / "agents/co_founder/tools/documents.py").read_text()
    # Session is derived from the invocation, never a model-supplied argument.
    assert 'getattr(getattr(tool_context, "session", None), "id", "")' in source
    assert "def produce_document(kind: str, title: str, spec: dict," in source
    assert "session_id" not in source.split("def produce_document")[1].split(
        '"""')[1]  # not an argument in the model-facing docstring


def test_voice_note_requires_a_session_at_the_api_boundary():
    source = (REPO / "app/main.py").read_text()
    voice = source.split('@app.post("/api/voice-note")')[1].split("@app.")[0]
    assert "session_id: str = Form(...)" in voice
    assert "_founder_session_exists(session_id)" in voice
    assert "create_voice_note_artifact" in voice


# Known remaining UI→worker shortcuts, out of scope for the docs/23 slice
# (mail connector admin buttons). They are recorded rather than tolerated
# silently: this list may shrink, never grow. Discovery is NOT on it — the
# founder-facing boundary landed in WI-3.
LEGACY_UI_TASK_CALLS = {"/tasks/gmail_scan", "/tasks/alex_mail_scan"}


def test_internal_task_routes_are_not_ui_authority_shortcuts():
    ui = (REPO / "app/static/index.html").read_text()
    task_calls = set(re.findall(r'["\'](/tasks/[a-z_]+)["\']', ui))
    new_offenders = task_calls - LEGACY_UI_TASK_CALLS
    assert not new_offenders, (
        f"UI must not call task workers directly: {sorted(new_offenders)}")
    assert "/tasks/discover" not in task_calls, (
        "discovery must go through POST /api/discovery-requests (docs/23 §6.2)")
    stale = LEGACY_UI_TASK_CALLS - task_calls
    assert not stale, (
        f"These were fixed — remove them from LEGACY_UI_TASK_CALLS: {sorted(stale)}")


def test_classified_non_resources_are_documented():
    spec = (REPO / "docs/23-session-resources-and-global-search.md").read_text()
    section = spec.split("Internal collections that are explicitly not")[1]
    for name in ("founder_state", "document_versions", "source_state",
                 "approvals", "feedback"):
        assert name in section, f"{name} must be classified in §6.1"


def test_all_registry_types_have_a_producer_or_are_disabled():
    from services import session_resources as sr

    producing = {
        sr.ResourceType.DISCOVERY_REQUEST, sr.ResourceType.OPPORTUNITY,
        sr.ResourceType.APPLICATION, sr.ResourceType.DOCUMENT,
        sr.ResourceType.ARTIFACT, sr.ResourceType.BROWSER_REPORT,
        sr.ResourceType.EVIDENCE_REPORT,
    }
    for resource_type, spec in sr.RESOURCE_REGISTRY.items():
        if spec.enabled:
            assert resource_type in producing, (
                f"{resource_type} is enabled but has no producer")
        else:
            assert resource_type not in producing
