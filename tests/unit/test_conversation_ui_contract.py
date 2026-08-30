"""Long conversation history remains bounded in the redesigned UI."""

from pathlib import Path

HTML = Path("app/static/index.html").read_text(encoding="utf-8")


def test_history_ui_pages_without_dropping_current_session_authority():
    assert "Load older messages" in HTML
    assert "page.next_before" in HTML
    assert "chatPageMessages" in HTML
    assert "const sid = contextSessionId();" in HTML
    assert "before=${chatNextBefore}" in HTML


def test_history_page_preserves_event_metadata_for_document_provenance():
    assert "addMsg(message.role, message.text, message)" in HTML
