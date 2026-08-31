"""Static contracts for honest, non-blocking attachment intake."""

from pathlib import Path

HTML = (Path(__file__).parents[2] / "app/static/index.html").read_text()


def test_conversation_scope_is_default_and_profile_is_explicit():
    select = HTML.split('id="attachmentScope"', 1)[1].split("</select>", 1)[0]
    assert 'value="reference_only"' in select
    assert "selected" in select
    assert "Use in conversation" in select
    assert '<option value="profile">Add to Founder Profile</option>' in select
    assert ('accept=".pdf,.docx,.pptx,.xlsx,.txt,.csv,.jpg,.jpeg,.png,.webp"'
            in HTML)
    assert 'fd.append("scope", scope)' in HTML


def test_processing_does_not_block_chat_or_claim_early_success():
    assert ("watchAttachment(j.attachment_ref, f.name, scope, d, "
            "attachmentSessionId, composerItem)" in HTML)
    assert 'You can keep chatting while I process it.' in HTML
    assert 'pendingUploads||!this.value.trim()' not in HTML
    watch = HTML.split("async function watchAttachment", 1)[1].split(
        "// ---------- view-only preview", 1)[0]
    for terminal in ("READY", "NEEDS_FOUNDER", "CONFIRMED", "NO_TEXT", "UNSUPPORTED", "FAILED"):
        assert terminal in watch
    assert "I did not read" in watch


def test_composer_grows_with_bottom_actions_and_converts_large_prose():
    assert "function upgradeComposer()" in HTML
    assert 'document.createElement("textarea")' in HTML
    assert 'actions.className = "composer-actions"' in HTML
    assert 'left.className = "composer-actions-left"' in HTML
    assert 'right.className = "composer-actions-right"' in HTML
    assert "COMPOSER_INPUT_MAX_HEIGHT = 118" in HTML
    assert "COMPOSER_PASTE_ATTACHMENT_THRESHOLD = 8_000" in HTML
    assert 'new File([text], "Pasted context.txt", { type: "text/plain" })' in HTML
    assert 'uploadAttachmentFile(file)' in HTML
    assert 'combined.trimStart().startsWith("/")' in HTML
    assert 'id = "composerAttachments"' in HTML
    assert 'title="Remove from this message"' in HTML
    assert ".pillbar textarea:focus-visible" in HTML
    assert "box-shadow: none" in HTML.split(
        ".pillbar textarea:focus-visible", 1
    )[1].split("}", 1)[0]


def test_composer_attachment_chip_tracks_upload_and_message_lifecycle():
    upload = HTML.split("async function uploadAttachmentFile", 1)[1].split(
        "async function watchAttachment", 1)[0]
    assert 'status: "Uploading…"' in upload
    assert 'composerItem.status = "Attached to this message"' in upload
    assert "composerAttachmentItems.push(composerItem)" in upload
    send = HTML.split("async function send()", 1)[1].split(
        "// ---------- board", 1)[0]
    assert "clearComposerAttachments()" in send


def test_internal_citations_download_authorized_source_without_browser_handoff():
    markdown = HTML.split("function md(src)", 1)[1].split("// ---------- chat", 1)[0]
    assert 'decoded.startsWith("/")' in markdown
    assert 'href="${url}" download' in markdown
    assert 'data-browser-url' in markdown  # external HTTP links retain the guarded Browser path


def test_profile_conflicts_have_a_founder_review_surface():
    assert "async function renderProfileReview" in HTML
    assert 'data-profile-action="approve"' in HTML
    assert 'data-profile-action="reject"' in HTML
    assert "UNCONFIRMED_EVIDENCE" in HTML
    assert "source unavailable" in HTML
