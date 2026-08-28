"""Private OIDC-authenticated RPC surface for the Playwright trust zone."""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services import browser_service

router = APIRouter()

_OPERATIONS = frozenset({
    "get_browser_state", "stop_browser", "open_run", "read_current",
    "propose_and_act", "close_run", "current_run_for_session",
    "browser_status_projection", "reconcile_session", "reconcile_all_runs",
    "shutdown", "expire_run", "register", "verify_registration_link",
    "open_and_login", "inspect", "fill", "screenshot", "submit",
    "set_fill_phase", "update_fill_run", "render_text", "execute_action",
    "fill_session_for_application", "set_fill_signature",
})
_PAGE_OPERATIONS = frozenset({
    "page_fill", "page_click", "page_screenshot", "page_eval_on_selector",
})


class BrowserWorkerCall(BaseModel):
    operation: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)


async def _authorized(request: Request) -> bool:
    if os.environ.get("BROWSER_WORKER_ROLE") != "1":
        return False
    if not os.environ.get("K_SERVICE"):
        return os.environ.get("BROWSER_WORKER_ALLOW_LOCAL") == "1"
    header = request.headers.get("Authorization", "")
    expected = os.environ.get("BROWSER_CALLER_SA", "")
    audience = (os.environ.get("BROWSER_WORKER_URL", "").rstrip("/")
                or f"https://{request.url.hostname}")
    if not header.startswith("Bearer ") or not expected:
        return False
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2.id_token import verify_oauth2_token

        claims = await asyncio.to_thread(
            verify_oauth2_token, header.removeprefix("Bearer "),
            GoogleRequest(), audience=audience)
        return (claims.get("email_verified") is True
                and claims.get("email") == expected)
    except Exception:
        return False


def _error(code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"status": "error", "error": True,
                         "error_code": code, "message": message},
                        status_code=status_code)


def _page(handle: dict[str, Any]):
    application_id = str(handle.get("application_id") or "")
    run_id = str(handle.get("run_id") or "")
    session = browser_service.fill_session_for_application(application_id)
    if not session or (run_id and session.get("run_id") != run_id):
        return None
    return session.get("page")


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) >= {"application_id", "run_id"} and set(value) <= {
                "application_id", "run_id", "url"}:
            return _page(value)
        return {str(key): _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _serialize(value: Any, *, application_id: str = "", run_id: str = "") -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode()}
    if isinstance(value, dict):
        output = {}
        effective_run_id = str(value.get("run_id") or run_id)
        for key, item in value.items():
            if key == "context":
                continue
            if key == "page":
                output[key] = {
                    "__remote_page__": True,
                    "application_id": application_id,
                    "run_id": effective_run_id,
                    "url": str(getattr(item, "url", "")),
                }
            else:
                output[key] = _serialize(
                    item, application_id=application_id,
                    run_id=effective_run_id)
        return output
    if isinstance(value, (list, tuple)):
        return [_serialize(item, application_id=application_id, run_id=run_id)
                for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)[:500]


@router.post("/internal/browser/v1/execute")
async def execute_browser_call(request: Request, payload: BrowserWorkerCall):
    if not await _authorized(request):
        return _error("workload_unauthorized", "Browser workload is unauthorized.",
                      status_code=401)
    if int(request.headers.get("content-length") or 0) > 262_144:
        return _error("browser_request_too_large", "Browser request is too large.",
                      status_code=413)
    operation = payload.operation
    params = payload.params
    try:
        if operation in _PAGE_OPERATIONS:
            page = _page(dict(params.get("page") or {}))
            if page is None:
                return _error("browser_handle_stale", "Browser page handle is stale.",
                              status_code=409)
            selector = str(params.get("selector") or "")
            if len(selector) > 1000:
                return _error("browser_selector_invalid",
                              "Browser selector is too large.")
            if operation == "page_fill":
                result = await page.fill(
                    selector,
                    str(params.get("value") or ""),
                    **dict(params.get("kwargs") or {}))
            elif operation == "page_click":
                result = await page.click(
                    selector,
                    **dict(params.get("kwargs") or {}))
            elif operation == "page_screenshot":
                result = await page.screenshot(**dict(params.get("kwargs") or {}))
            else:
                expression = str(params.get("expression") or "")
                if len(expression) > 2000:
                    return _error("browser_expression_invalid",
                                  "Browser expression is too large.")
                result = await page.eval_on_selector(
                    selector, expression,
                    *list(params.get("args") or []))
            return _serialize(result)
        if operation not in _OPERATIONS:
            return _error("browser_operation_unregistered",
                          "Browser operation is not registered.")
        args = _decode(list(params.get("args") or []))
        kwargs = _decode(dict(params.get("kwargs") or {}))
        if any(item is None for item in args):
            return _error("browser_handle_stale", "Browser page handle is stale.",
                          status_code=409)
        if operation == "fill" and len(args) >= 3:
            # Upload artifacts are mirrored to GCS by the API service. Resolve
            # an exact basename into this worker's private local cache rather
            # than accepting an API-container filesystem path.
            from services import storage

            attachments = dict(args[2] or {})
            args[2] = {
                str(field): storage.download_if_missing(
                    os.path.basename(str(source)))
                for field, source in attachments.items()
            }
        fn = getattr(browser_service, operation)
        result = await fn(*args, **kwargs) if asyncio.iscoroutinefunction(fn) else fn(
            *args, **kwargs)
        application_id = str(
            kwargs.get("application_id")
            or (args[0] if operation == "fill_session_for_application" and args
                else ""))
        return _serialize(result, application_id=application_id)
    except Exception as exc:
        return _error("browser_worker_failure",
                      f"Browser worker operation failed: {exc}"[:240],
                      status_code=503)
