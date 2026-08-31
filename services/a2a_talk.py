"""A2A client (docs/19 §P1.5): discover an A2A agent by its card and send a
message — the real protocol: agent card at /.well-known/agent.json + JSON-RPC
`message/send`. Errors as data; httpx is the transport (injectable in tests).

Both the model-supplied ``base_url`` and the card-supplied POST URL are run
through the same SSRF/public-URL policy the rest of the codebase uses
(services/browser_service): non-http(s) schemes, credential URLs, and every
private/reserved/loopback/link-local/metadata address are refused before a
request is made. Loopback is exempt only in local dev (no ``K_SERVICE``) so
provider-neutral in-process and local contract fixtures remain testable.
"""

from __future__ import annotations

import os
import uuid

import httpx

from services import browser_service


def _err(message: str) -> dict:
    return {"status": "error", "error": True, "message": message}


async def _refuse_unsafe(url: str) -> str | None:
    """Return an SSRF/policy refusal message for ``url``, or ``None`` if allowed.

    Reuses browser_service's validation helpers. A hostname that simply fails to
    resolve (``page_unavailable``) is not an SSRF hit — nothing can be dialled —
    so the request is allowed to fail naturally at the HTTP layer; only
    positively unsafe targets (private/reserved IPs, credential URLs, bad
    schemes) are refused here.
    """
    parts, message = browser_service._url_parts(url)
    if parts is None:
        return message or "invalid URL"
    host = (parts.hostname or "").lower()
    if not os.environ.get("K_SERVICE") and host in ("127.0.0.1", "localhost", "::1"):
        return None  # local dev: an explicit local A2A contract fixture
    code, msg, _canonical, _ips = await browser_service._validate_url_async(
        url, enforce_domain_policy=False)
    if code in ("ssrf_blocked", "policy_refused", "credential_url"):
        return msg or "target URL refused by SSRF policy"
    return None


async def ask_agent(base_url: str, question: str, timeout: float = 20) -> dict:
    """Discover the A2A agent at base_url and ask it a text question.

    Returns {status, agent, answer, card} or an error dict. The card is
    fetched first — if the host is not an A2A agent, that's the answer too."""
    base = base_url.rstrip("/")
    refusal = await _refuse_unsafe(base)
    if refusal:
        return _err(f"A2A base URL refused: {refusal}"[:200])
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            card_resp = await client.get(f"{base}/.well-known/agent.json")
            card_resp.raise_for_status()
            card = card_resp.json()
            if not card.get("name"):
                return _err(f"invalid agent card from {base}")
        except Exception as exc:
            return _err(f"no A2A agent card at {base}: {exc}"[:200])
        post_url = card.get("url") or f"{base}/a2a"
        # The POST URL comes from the fetched (untrusted) card — validate it
        # against the same policy before sending anything to it.
        refusal = await _refuse_unsafe(post_url)
        if refusal:
            return _err(f"A2A endpoint URL refused: {refusal}"[:200])
        rpc = {
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/send",
            "params": {"message": {
                "role": "user", "kind": "message", "messageId": uuid.uuid4().hex,
                "parts": [{"kind": "text", "text": question}]}},
        }
        try:
            resp = await client.post(post_url, json=rpc)
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
