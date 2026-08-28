"""Typed gateway to the isolated Playwright worker.

Local development keeps the in-process implementation. In Cloud Run the API
sets ``BROWSER_WORKER_URL`` and every stateful Playwright operation crosses an
audience-bound OIDC call. Only opaque run/application handles cross the trust
boundary; Playwright objects and credentials never leave the worker.
"""

from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass
from typing import Any

from services import browser_service as _local


def _remote() -> bool:
    return (bool(os.environ.get("BROWSER_WORKER_URL"))
            and os.environ.get("BROWSER_WORKER_ROLE") != "1")


def _url() -> str:
    return os.environ.get("BROWSER_WORKER_URL", "").rstrip("/")


def _token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token

    return fetch_id_token(Request(), _url())


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"__bytes__"}:
            return base64.b64decode(value["__bytes__"])
        if value.get("__remote_page__"):
            return RemotePage(
                application_id=str(value.get("application_id") or ""),
                run_id=str(value.get("run_id") or ""),
                url=str(value.get("url") or ""))
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _encode(value: Any) -> Any:
    if isinstance(value, RemotePage):
        return {"application_id": value.application_id,
                "run_id": value.run_id, "url": value.url}
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


async def _call(operation: str, **params: Any) -> Any:
    if not _remote():
        raise RuntimeError("remote browser gateway is not configured")
    import httpx

    token = await asyncio.to_thread(_token)
    try:
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            response = await client.post(
                f"{_url()}/internal/browser/v1/execute",
                headers={"Authorization": f"Bearer {token}"},
                json={"operation": operation, "params": _encode(params)})
        response.raise_for_status()
        return _decode(response.json())
    except Exception as exc:
        return {"status": "error", "error": True,
                "error_code": "browser_worker_unavailable",
                "message": f"Browser worker unavailable: {exc}"[:240]}


def _call_sync(operation: str, **params: Any) -> Any:
    if not _remote():
        raise RuntimeError("remote browser gateway is not configured")
    import httpx

    try:
        response = httpx.post(
            f"{_url()}/internal/browser/v1/execute",
            headers={"Authorization": f"Bearer {_token()}"},
            json={"operation": operation, "params": _encode(params)},
            timeout=120)
        response.raise_for_status()
        return _decode(response.json())
    except Exception:
        return None


@dataclass
class RemotePage:
    application_id: str
    run_id: str
    url: str = ""

    async def fill(self, selector: str, value: str, **kwargs: Any) -> Any:
        return await _call("page_fill", page=self, selector=selector,
                           value=value, kwargs=kwargs)

    async def click(self, selector: str, **kwargs: Any) -> Any:
        return await _call("page_click", page=self, selector=selector,
                           kwargs=kwargs)

    async def screenshot(self, **kwargs: Any) -> bytes:
        result = await _call("page_screenshot", page=self, kwargs=kwargs)
        return result if isinstance(result, bytes) else b""

    async def eval_on_selector(self, selector: str, expression: str,
                               *args: Any) -> Any:
        return await _call("page_eval_on_selector", page=self,
                           selector=selector, expression=expression,
                           args=list(args))


async def _invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    if not _remote():
        return await getattr(_local, name)(*args, **kwargs)
    return await _call(name, args=list(args), kwargs=kwargs)


async def get_browser_state(session_key): return await _invoke("get_browser_state", session_key)
async def stop_browser(session_key, actor, run_id=None): return await _invoke("stop_browser", session_key, actor, run_id)
async def open_run(session_key, url, purpose): return await _invoke("open_run", session_key, url, purpose)
async def read_current(run_id, question): return await _invoke("read_current", run_id, question)
async def propose_and_act(run_id, invocation_id): return await _invoke("propose_and_act", run_id, invocation_id)
async def close_run(run_id, reason, actor, **kwargs): return await _invoke("close_run", run_id, reason, actor, **kwargs)
async def current_run_for_session(session_key, kind="browse"): return await _invoke("current_run_for_session", session_key, kind)
async def browser_status_projection(session_key): return await _invoke("browser_status_projection", session_key)
async def reconcile_session(session_key): return await _invoke("reconcile_session", session_key)
async def reconcile_all_runs():
    # Worker startup owns process-local lease reconciliation. An API instance
    # starting must never race that ownership merely because it uses the
    # gateway.
    return None if _remote() else await _local.reconcile_all_runs()


async def shutdown():
    # Cloud Run may retire any one of many API instances. Forwarding that
    # lifecycle signal would tear down the singleton worker's live contexts.
    return None if _remote() else await _local.shutdown()
async def expire_run(run_id, lease_generation): return await _invoke("expire_run", run_id, lease_generation)
async def register(*args, **kwargs): return await _invoke("register", *args, **kwargs)
async def verify_registration_link(*args, **kwargs): return await _invoke("verify_registration_link", *args, **kwargs)
async def open_and_login(*args, **kwargs): return await _invoke("open_and_login", *args, **kwargs)
async def inspect(page): return await _invoke("inspect", page)
async def fill(page, mapping, attachments): return await _invoke("fill", page, mapping, attachments)
async def screenshot(page, path): return await _invoke("screenshot", page, path)
async def submit(page, idempotency_key, **kwargs): return await _invoke("submit", page, idempotency_key, **kwargs)
async def set_fill_phase(application_id, phase): return await _invoke("set_fill_phase", application_id, phase)
async def update_fill_run(*args, **kwargs): return await _invoke("update_fill_run", *args, **kwargs)
async def render_text(url): return await _invoke("render_text", url)
async def execute_action(*args, **kwargs): return await _invoke("execute_action", *args, **kwargs)


def fill_session_for_application(application_id: str):
    if not _remote():
        return _local.fill_session_for_application(application_id)
    return _call_sync("fill_session_for_application",
                      args=[application_id], kwargs={})


def set_fill_signature(application_id: str, signature: str | None) -> bool:
    if not _remote():
        return _local.set_fill_signature(application_id, signature)
    return bool(_call_sync("set_fill_signature", args=[application_id, signature],
                           kwargs={}))


# Pure policy functions stay in the API process and do not own Playwright.
scan_injection = _local.scan_injection
validate_public_url = _local.validate_public_url
public_proxy_url = _local.public_proxy_url
_url_parts = _local._url_parts
_validate_url_async = _local._validate_url_async
_validate_portal_target = _local._validate_portal_target
