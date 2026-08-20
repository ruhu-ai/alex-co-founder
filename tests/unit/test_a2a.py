"""A2A protocol tests (docs/19 §P1.5): card shape + message/send contract."""

import httpx
import pytest

from mock_portal.main import app as mock_app
from services import a2a_talk

pytestmark = pytest.mark.asyncio


def _asgi_transport():
    return httpx.ASGITransport(app=mock_app)


class TestAgentCard:
    async def test_card_is_a2a_shaped(self):
        async with httpx.AsyncClient(transport=_asgi_transport(),
                                     base_url="http://mock") as client:
            resp = await client.get("/.well-known/agent.json")
        card = resp.json()
        assert card["name"] == "Mock Portal Program Office"
        assert card["preferredTransport"] == "JSONRPC"
        assert card["url"].endswith("/a2a")
        assert {s["id"] for s in card["skills"]} == {"requirements", "deadlines", "status"}

    async def test_message_send_requirements(self):
        rpc = {"jsonrpc": "2.0", "id": "t1", "method": "message/send",
               "params": {"message": {"role": "user", "kind": "message",
                                      "messageId": "m1",
                                      "parts": [{"kind": "text",
                                                 "text": "What is required to apply?"}]}}}
        async with httpx.AsyncClient(transport=_asgi_transport(),
                                     base_url="http://mock") as client:
            resp = await client.post("/a2a", json=rpc)
        payload = resp.json()
        assert payload["id"] == "t1" and "result" in payload
        assert payload["result"]["role"] == "agent"
        assert "Company name" in payload["result"]["parts"][0]["text"]

    async def test_unknown_method_is_jsonrpc_error(self):
        rpc = {"jsonrpc": "2.0", "id": "t2", "method": "tasks/get", "params": {}}
        async with httpx.AsyncClient(transport=_asgi_transport(),
                                     base_url="http://mock") as client:
            resp = await client.post("/a2a", json=rpc)
        assert resp.json()["error"]["code"] == -32601


class TestA2AClient:
    async def test_ask_agent_roundtrip(self, monkeypatch):
        real_client = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient",
                            lambda timeout: real_client(
                                transport=_asgi_transport(), base_url="http://mock",
                                timeout=timeout))
        result = await a2a_talk.ask_agent("http://mock", "When is the deadline?")
        assert result["status"] == "success"
        assert result["agent"] == "Mock Portal Program Office"
        assert "2026-09-30" in result["answer"]

    async def test_no_card_is_error_data(self, monkeypatch):
        class _Client:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def get(self, url): raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: _Client())
        result = await a2a_talk.ask_agent("http://nothing.example", "hi")
        assert result["status"] == "error" and result["error"] is True
