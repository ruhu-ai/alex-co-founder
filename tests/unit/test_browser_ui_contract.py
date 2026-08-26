"""Static UI lifecycle assertions for the in-app Browser observation plane."""

from pathlib import Path

HTML = (Path(__file__).parents[2] / "app/static/index.html").read_text()


def test_browser_uses_one_visibility_scoped_eventsource_without_refresh_poll():
    assert "new EventSource(`/api/browser/events?session_id=" in HTML
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
