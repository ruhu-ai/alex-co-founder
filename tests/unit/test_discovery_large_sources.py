"""Large public sources degrade to bounded research instead of dead-ending."""

from __future__ import annotations

import httpx
import pytest

from services import browser_service, discovery_service, storage


class _Stream:
    def __init__(self, body: bytes, *, declared: int):
        self.status_code = 200
        self.headers = {"content-length": str(declared),
                        "content-type": "text/html"}
        self.request = httpx.Request("GET", "https://example.org/program")
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        yield self._body


class _Client:
    def __init__(self, stream):
        self._stream = stream

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, *_args, **_kwargs):
        return self._stream


@pytest.mark.asyncio
async def test_html_fetch_streams_a_bounded_prefix_instead_of_failing(monkeypatch):
    stream = _Stream(b"<html><body>Program details</body></html>" * 20,
                     declared=20_000)
    monkeypatch.setattr(
        discovery_service.httpx, "AsyncClient", lambda **_kwargs: _Client(stream))
    monkeypatch.setattr(
        browser_service, "public_proxy_url", _async_value(None))
    monkeypatch.setattr(
        browser_service, "validate_public_url", _async_value(None))

    response, final_url, truncated = await discovery_service._safe_fetch(
        "https://example.org/program", 128, truncate=True)

    assert final_url == "https://example.org/program"
    assert truncated is True
    assert len(response.content) == 128


@pytest.mark.asyncio
async def test_binary_fetch_still_rejects_a_corrupt_partial_document(monkeypatch):
    stream = _Stream(b"%PDF-body", declared=51_000_000)
    monkeypatch.setattr(
        discovery_service.httpx, "AsyncClient", lambda **_kwargs: _Client(stream))
    monkeypatch.setattr(
        browser_service, "public_proxy_url", _async_value(None))
    monkeypatch.setattr(
        browser_service, "validate_public_url", _async_value(None))

    with pytest.raises(ValueError, match="source exceeds"):
        await discovery_service._safe_fetch(
            "https://example.org/program.pdf", 50_000_000, truncate=False)


@pytest.mark.asyncio
async def test_large_html_uses_bounded_browser_text_and_remains_usable(monkeypatch):
    async def _fetch(*_args, **_kwargs):
        return (
            httpx.Response(
                200,
                content=b"<html><body>partial</body></html>",
                request=httpx.Request("GET", "https://example.org/program"),
            ),
            "https://example.org/program",
            True,
        )

    async def _render(_url):
        return "Official program details and application requirements."

    saved: dict[str, str] = {}
    monkeypatch.setattr(discovery_service, "_safe_fetch", _fetch)
    monkeypatch.setattr("services.browser_gateway.render_text", _render)
    monkeypatch.setattr(storage, "save_text", saved.__setitem__)

    result = await discovery_service.fetch_source(
        "https://example.org/program", "web_page", "source.txt")

    assert result["status"] == "success"
    assert result["source_truncated"] is True
    assert result["rendered"] is True
    assert result["research_note"].startswith("This large source")
    assert saved["source.txt"] == (
        "Official program details and application requirements.")


def _async_value(value):
    async def _result(*_args, **_kwargs):
        return value

    return _result
