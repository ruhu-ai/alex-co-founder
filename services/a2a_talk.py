"""A2A client (docs/19 §P1.5): discover an A2A agent by its card and send a
message — the real protocol: agent card at /.well-known/agent.json + JSON-RPC
`message/send`. Errors as data; httpx is the transport (injectable in tests).
"""

from __future__ import annotations

import uuid

import httpx


def _err(message: str) -> dict:
    return {"status": "error", "error": True, "message": message}


async def ask_agent(base_url: str, question: str, timeout: float = 20) -> dict:
    """Discover the A2A agent at base_url and ask it a text question.

    Returns {status, agent, answer, card} or an error dict. The card is
    fetched first — if the host is not an A2A agent, that's the answer too."""
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            card_resp = await client.get(f"{base}/.well-known/agent.json")
            card_resp.raise_for_status()
            card = card_resp.json()
            if not card.get("name"):
                return _err(f"invalid agent card from {base}")
        except Exception as exc:
            return _err(f"no A2A agent card at {base}: {exc}"[:200])
        rpc = {
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/send",
            "params": {"message": {
                "role": "user", "kind": "message", "messageId": uuid.uuid4().hex,
                "parts": [{"kind": "text", "text": question}]}},
        }
        try:
            resp = await client.post(card.get("url") or f"{base}/a2a", json=rpc)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            return _err(f"A2A message/send failed: {exc}"[:200])
    if "error" in payload:
        return _err(f"A2A error {payload['error'].get('code')}: "
                    f"{payload['error'].get('message')}")
    result = payload.get("result") or {}
    texts = [p.get("text", "") for p in result.get("parts", []) if "text" in p]
    if not texts:
        return _err("A2A reply contained no text parts")
    return {"status": "success", "agent": card.get("name", base),
            "answer": " ".join(texts).strip(), "card": card}
