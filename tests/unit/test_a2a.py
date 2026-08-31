"""A2A client contract tests against a provider-neutral in-process fixture."""

import httpx
import pytest
from fastapi import FastAPI, Request

from services import a2a_talk

pytestmark = pytest.mark.asyncio

fixture_app = FastAPI()


@fixture_app.get("/.well-known/agent.json")
async def agent_card():
    return {
        "name": "Fixture Program Office",
        "preferredTransport": "JSONRPC",
        "url": "http://fixture/a2a",
        "skills": [
            {"id": "requirements", "name": "Requirements"},
            {"id": "deadlines", "name": "Deadlines"},
            {"id": "status", "name": "Status"},
        ],
    }


@fixture_app.post("/a2a")
async def message_send(request: Request):
    payload = await request.json()
    if payload.get("method") != "message/send":
        return {"jsonrpc": "2.0", "id": payload.get("id"),
                "error": {"code": -32601, "message": "Method not found"}}
    text = " ".join(
        part.get("text", "")
        for part in payload.get("params", {}).get("message", {}).get("parts", [])
    ).lower()
    answer = (
        "Company name and founder profile are required."
        if "required" in text
        else "The deadline is 2026-09-30."
    )
    return {
        "jsonrpc": "2.0",
        "id": payload.get("id"),
        "result": {"role": "agent", "parts": [{"kind": "text", "text": answer}]},
    }


def _asgi_transport():
    return httpx.ASGITransport(app=fixture_app)


class TestAgentCard:
    async def test_card_is_a2a_shaped(self):
        async with httpx.AsyncClient(
                transport=_asgi_transport(), base_url="http://fixture") as client:
            resp = await client.get("/.well-known/agent.json")
        card = resp.json()
        assert card["name"] == "Fixture Program Office"
        assert card["preferredTransport"] == "JSONRPC"
        assert card["url"].endswith("/a2a")
        assert {s["id"] for s in card["skills"]} == {
            "requirements", "deadlines", "status"}

    async def test_message_send_requirements(self):
        rpc = {"jsonrpc": "2.0", "id": "t1", "method": "message/send",
               "params": {"message": {"role": "user", "kind": "message",
                                      "messageId": "m1",
                                      "parts": [{"kind": "text",
                                                 "text": "What is required to apply?"}]}}}
        async with httpx.AsyncClient(
                transport=_asgi_transport(), base_url="http://fixture") as client:
            resp = await client.post("/a2a", json=rpc)
        payload = resp.json()
        assert payload["id"] == "t1" and "result" in payload
        assert payload["result"]["role"] == "agent"
        assert "Company name" in payload["result"]["parts"][0]["text"]

    async def test_unknown_method_is_jsonrpc_error(self):
        rpc = {"jsonrpc": "2.0", "id": "t2", "method": "tasks/get", "params": {}}
        async with httpx.AsyncClient(
                transport=_asgi_transport(), base_url="http://fixture") as client:
            resp = await client.post("/a2a", json=rpc)
        assert resp.json()["error"]["code"] == -32601


class TestA2AClient:
    async def test_ask_agent_roundtrip(self, monkeypatch):
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda timeout: real_client(
                transport=_asgi_transport(), base_url="http://fixture", timeout=timeout))
        result = await a2a_talk.ask_agent("http://fixture", "When is the deadline?")
        assert result["status"] == "success"
        assert result["agent"] == "Fixture Program Office"
        assert "2026-09-30" in result["answer"]

    async def test_no_card_is_error_data(self, monkeypatch):
        class _Client:
            def __init__(self, **_kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def get(self, _url):
                raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: _Client())
        result = await a2a_talk.ask_agent("http://nothing.example", "hi")
        assert result["status"] == "error" and result["error"] is True
