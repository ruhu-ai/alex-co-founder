"""Browser service (docs/05, 09) — Playwright session, page signatures,
fills, screenshots, idempotent submit. One browser per process; a fresh
context per fill run. All failures return error dicts.
"""

# ruff: noqa: BLE001, S110, S112 -- caller-facing browser boundaries convert
# third-party/network failures to typed error data by design (docs/18).

from __future__ import annotations

import asyncio
import hashlib
import inspect as pyinspect
import ipaddress
import json
import os
import re
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from typing import Any, TypedDict
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from services import firestore, storage

_playwright = None
_browser = None


class ActionProposal(TypedDict):
    action: str
    target_key: str
    text: str | None


MAX_BROWSE_ACTIONS = 20
BROWSE_TIME_BOX_SECONDS = 90
MAX_PAGE_TEXT = 100_000
_CREDENTIAL_QUERY = re.compile(
    r"(?:^|[_-])(token|code|key|signature|session|auth|password)(?:$|[_-])",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?:api[_-]?key|access[_-]?token|password|bearer\s+[A-Za-z0-9._~-]{12,})",
    re.IGNORECASE,
)
_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+|any\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:system|assistant|developer)\s*:\s*", re.IGNORECASE),
    re.compile(
        r"<\|(?:system|assistant|developer|im_start|im_end)[^>]*\|>", re.IGNORECASE
    ),
    re.compile(
        r"(?:execute|run|call|invoke|send|post)\s+(?:this\s+)?(?:tool|command|request)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:[A-Za-z0-9+/]{80,}={0,2})"),
)

ReaderFn = Callable[[str, str, str], Awaitable[dict[str, Any]]]
ProposerFn = Callable[[str, str, bytes], Awaitable[dict[str, Any]]]
ResolverFn = Callable[[str, int], Any]
DialerFn = Callable[[str, int, str], Awaitable[tuple[Any, Any]]]

_reader_fn: ReaderFn | None = None
_proposer_fn: ProposerFn | None = None
_resolver_fn: ResolverFn | None = None
_dialer_fn: DialerFn | None = None
_browse_contexts: dict[str, dict[str, Any]] = {}
_browse_locks: dict[str, asyncio.Lock] = {}
_session_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
# Serialize submits sharing a derived idempotency key so two concurrent
# submit_form calls can never interleave clicks on the same page (docs/05, 09).
_submit_locks: dict[str, asyncio.Lock] = {}
_proxy = None
_public_proxy = None


def set_reader_fn(fn: ReaderFn | None) -> None:
    """Inject the isolated page reader (tests supply a deterministic function)."""
    global _reader_fn
    _reader_fn = fn


def set_proposer_fn(fn: ProposerFn | None) -> None:
    """Inject the isolated action proposer (tests supply deterministic actions)."""
    global _proposer_fn
    _proposer_fn = fn


def set_resolver(fn: ResolverFn | None) -> None:
    """Inject DNS resolution. Returned IPs are still checked by the SSRF guard."""
    global _resolver_fn
    _resolver_fn = fn


def set_proxy_dialer(fn: DialerFn | None) -> None:
    """Inject the validating proxy's transport for synthetic fixture servers."""
    global _dialer_fn
    _dialer_fn = fn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _error(
    code: str,
    message: str,
    *,
    reason: str | None = None,
    route: str | None = None,
    field: str | None = None,
    **extra: Any,
) -> dict:
    result: dict[str, Any] = {
        "status": "error",
        "error": True,
        "code": code,
        "message": message,
    }
    if reason:
        item = {"reason": reason}
        if route:
            item["route"] = route
        if field:
            item["field"] = field
        result["needs_human"] = [item]
    result.update(extra)
    return result


def _host_matches(host: str, pattern: str) -> bool:
    pattern = pattern.strip().lower().rstrip(".")
    if not pattern:
        return False
    if pattern.startswith("*."):
        apex = pattern[2:]
        return host == apex or host.endswith("." + apex)
    return fnmatch(host, pattern)


def check_domain_policy(url: str) -> str | None:
    """Return a domain-policy refusal message, or ``None`` when allowed."""
    try:
        host = (
            (urlsplit(url).hostname or "").encode("idna").decode().lower().rstrip(".")
        )
    except (UnicodeError, ValueError):
        return "invalid hostname"
    blocked = [
        x for x in os.environ.get("BROWSE_BLOCKED_DOMAINS", "").split(",") if x.strip()
    ]
    allowed = [
        x for x in os.environ.get("BROWSE_ALLOWED_DOMAINS", "").split(",") if x.strip()
    ]
    if any(_host_matches(host, pattern) for pattern in blocked):
        return f"domain is blocked: {host}"
    if os.environ.get("BROWSE_OPEN_WEB", "").lower() == "true":
        return None
    if not any(_host_matches(host, pattern) for pattern in allowed):
        return f"domain is not allowlisted: {host}"
    return None


def _legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse browser-compatible decimal/hex/octal IPv4 spellings."""
    try:
        if host.lower().startswith("0x"):
            return ipaddress.IPv4Address(int(host, 16))
        if host.isdigit():
            base = 8 if len(host) > 1 and host.startswith("0") else 10
            return ipaddress.IPv4Address(int(host, base))
        parts = host.split(".")
        if not 1 <= len(parts) <= 4:
            return None
        nums = []
        for part in parts:
            base = (
                16
                if part.lower().startswith("0x")
                else 8
                if len(part) > 1 and part.startswith("0")
                else 10
            )
            nums.append(int(part, base))
        if len(nums) == 4 and all(0 <= n <= 255 for n in nums):
            return ipaddress.IPv4Address(bytes(nums))
    except (ValueError, OverflowError):
        return None
    return None


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return _legacy_ipv4(host)


def _unsafe_ip(value: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    try:
        ip = (
            value
            if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address))
            else ipaddress.ip_address(value)
        )
    except ValueError:
        return True
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _resolve_sync(host: str, port: int) -> list[str]:
    literal = _parse_ip(host)
    if literal is not None:
        return [str(literal)]
    if _resolver_fn is not None:
        result = _resolver_fn(host, port)
        if pyinspect.isawaitable(result):
            raise RuntimeError(
                "the synchronous URL validator requires a synchronous resolver"
            )
        return [str(item) for item in result]
    return sorted(
        {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    )


async def _resolve(host: str, port: int) -> list[str]:
    literal = _parse_ip(host)
    if literal is not None:
        return [str(literal)]
    if _resolver_fn is not None:
        result = _resolver_fn(host, port)
        if pyinspect.isawaitable(result):
            result = await result
        return [str(item) for item in result]
    return await asyncio.to_thread(_resolve_sync, host, port)


def _url_parts(url: str) -> tuple[Any, str] | tuple[None, str]:
    try:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in ("http", "https"):
            return None, "only http and https URLs are allowed"
        if not parts.hostname:
            return None, "URL must include a hostname"
        if parts.username is not None or parts.password is not None:
            return None, "credential-bearing URLs are not allowed"
        host = parts.hostname.encode("idna").decode().lower().rstrip(".")
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        netloc = f"[{host}]" if ":" in host else host
        if port != (443 if parts.scheme.lower() == "https" else 80):
            netloc += f":{port}"
        canonical = parts._replace(scheme=parts.scheme.lower(), netloc=netloc)
        return canonical, ""
    except (UnicodeError, ValueError):
        return None, "invalid URL"


def _credential_param(url: str) -> bool:
    try:
        return any(
            _CREDENTIAL_QUERY.search(key)
            for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)
        )
    except ValueError:
        return True


def redact_url(url: str) -> str:
    """Redact sensitive query values before persistence, display, log, or audit.

    Fragments carrying query-shaped credentials (OAuth implicit flow puts
    ``access_token`` after ``#``) are redacted the same way.
    """
    try:
        parts = urlsplit(url)
        query = [
            (key, "***" if _CREDENTIAL_QUERY.search(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        fragment = parts.fragment
        if "=" in fragment:
            fragment = urlencode(
                [
                    (key, "***" if _CREDENTIAL_QUERY.search(key) else value)
                    for key, value in parse_qsl(fragment, keep_blank_values=True)
                ]
            )
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), fragment)
        )
    except ValueError:
        return "[invalid-url]"


def _validate_url_detail(
    url: str, *, resolve: bool = True, enforce_domain_policy: bool = True
) -> tuple[str | None, str | None, str]:
    parts, message = _url_parts(url)
    if parts is None:
        code = "credential_url" if "credential-bearing" in message else "policy_refused"
        return code, message, ""
    canonical = urlunsplit(parts)
    if _credential_param(canonical):
        return (
            "credential_url",
            "credential-bearing query parameters are not allowed",
            canonical,
        )
    if enforce_domain_policy:
        policy = check_domain_policy(canonical)
        if policy:
            return "policy_refused", policy, canonical
    host = parts.hostname or ""
    literal = _parse_ip(host)
    if literal is not None and _unsafe_ip(literal):
        return "ssrf_blocked", f"private or reserved address refused: {host}", canonical
    if resolve:
        try:
            ips = _resolve_sync(
                host, parts.port or (443 if parts.scheme == "https" else 80)
            )
        except (OSError, RuntimeError) as exc:
            return "page_unavailable", f"hostname resolution failed: {exc}", canonical
        if not ips or any(_unsafe_ip(ip) for ip in ips):
            return (
                "ssrf_blocked",
                f"hostname resolves to a private or reserved address: {host}",
                canonical,
            )
    return None, None, canonical


def validate_url(url: str) -> str | None:
    """Canonicalize and enforce scheme, credentials, domain, DNS, and SSRF policy."""
    _code, message, _canonical = _validate_url_detail(url)
    return message


async def _validate_url_async(
    url: str, *, enforce_domain_policy: bool = True,
) -> tuple[str | None, str | None, str, list[str]]:
    code, message, canonical = _validate_url_detail(
        url, resolve=False, enforce_domain_policy=enforce_domain_policy)
    if code:
        return code, message, canonical, []
    parts = urlsplit(canonical)
    try:
        ips = await _resolve(
            parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)
        )
    except (OSError, RuntimeError) as exc:
        return "page_unavailable", f"hostname resolution failed: {exc}", canonical, []
    if not ips or any(_unsafe_ip(ip) for ip in ips):
        return (
            "ssrf_blocked",
            "hostname resolves to a private or reserved address",
            canonical,
            ips,
        )
    return None, None, canonical, ips


async def validate_public_url(url: str) -> str | None:
    """Validate an arbitrary discovery URL without the interactive allowlist.

    Discovery is intentionally open-web, but still rejects credentials, unsafe
    schemes, and every private/reserved DNS result.
    """
    _code, message, _canonical, _ips = await _validate_url_async(
        url, enforce_domain_policy=False)
    return message


class _ValidatingProxy:
    """Tiny HTTP/CONNECT proxy that validates DNS and dials the validated IP."""

    def __init__(self, *, enforce_domain_policy: bool = True) -> None:
        self.server = None
        self.port = 0
        self.connections: set[Any] = set()
        self.enforce_domain_policy = enforce_domain_policy

    async def start(self) -> int:
        if self.server is None:
            self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
            self.port = int(self.server.sockets[0].getsockname()[1])
        return self.port

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            self.port = 0
        for writer in tuple(self.connections):
            writer.close()
        if self.connections:
            await asyncio.gather(
                *(writer.wait_closed() for writer in tuple(self.connections)),
                return_exceptions=True,
            )
        self.connections.clear()

    async def _dial(self, host: str, port: int, ip: str):
        if _dialer_fn is not None:
            return await _dialer_fn(host, port, ip)
        return await asyncio.open_connection(ip, port)

    async def _handle(self, client_r, client_w) -> None:
        upstream_w = None
        self.connections.add(client_w)
        try:
            header = await asyncio.wait_for(client_r.readuntil(b"\r\n\r\n"), timeout=15)
            first, rest = header.split(b"\r\n", 1)
            method_b, target_b, version_b = first.split(b" ", 2)
            method, target = method_b.decode().upper(), target_b.decode()
            if method == "CONNECT":
                host, _, raw_port = target.rpartition(":")
                port = int(raw_port or "443")
                url = f"https://{host}:{port}/"
            else:
                if method not in ("GET", "HEAD"):
                    raise PermissionError("unsafe method")
                url = target
                parsed = urlsplit(url)
                host = parsed.hostname or ""
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
            code, _message, canonical, ips = await _validate_url_async(
                url, enforce_domain_policy=self.enforce_domain_policy)
            if code:
                raise PermissionError(code)
            upstream_r, upstream_w = await self._dial(host, port, ips[0])
            self.connections.add(upstream_w)
            if method == "CONNECT":
                client_w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_w.drain()
            else:
                parsed = urlsplit(canonical)
                origin_target = urlunsplit(
                    ("", "", parsed.path or "/", parsed.query, "")
                )
                upstream_w.write(
                    b" ".join((method_b, origin_target.encode(), version_b))
                    + b"\r\n"
                    + rest
                )
                await upstream_w.drain()

            async def pipe(reader, writer):
                try:
                    while data := await reader.read(65536):
                        writer.write(data)
                        await writer.drain()
                except (ConnectionError, asyncio.CancelledError):
                    pass

            tasks = {
                asyncio.create_task(pipe(client_r, upstream_w)),
                asyncio.create_task(pipe(upstream_r, client_w)),
            }
            _done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        except Exception:
            if not client_w.is_closing():
                client_w.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                try:
                    await client_w.drain()
                except ConnectionError:
                    pass
        finally:
            if upstream_w is not None:
                upstream_w.close()
                self.connections.discard(upstream_w)
            client_w.close()
            self.connections.discard(client_w)


async def _get_proxy(*, public: bool = False) -> _ValidatingProxy:
    global _proxy, _public_proxy
    if public:
        if _public_proxy is None:
            _public_proxy = _ValidatingProxy(enforce_domain_policy=False)
        await _public_proxy.start()
        return _public_proxy
    if _proxy is None:
        _proxy = _ValidatingProxy()
    await _proxy.start()
    return _proxy


async def public_proxy_url() -> str:
    """Return the DNS-pinning SSRF proxy used by open-web discovery fetches."""
    proxy = await _get_proxy(public=True)
    return f"http://127.0.0.1:{proxy.port}"


async def get_browser():
    global _playwright, _browser
    if _browser is None:
        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=os.environ.get("HEADLESS", "true").lower() == "true"
        )
    return _browser


async def new_context():
    browser = await get_browser()
    return await browser.new_context()


def page_signature(field_names: list[str]) -> str:
    """The staleness signature: a hash of the form's field names (docs/09)."""
    return (
        "sha256:"
        + hashlib.sha256("|".join(sorted(field_names)).encode()).hexdigest()[:16]
    )


async def render_text(url: str) -> str | None:
    """SSRF-guarded JS-shell fallback for the scout (docs/08)."""
    if await validate_public_url(url):
        return None
    browser = await get_browser()
    proxy = await _get_proxy(public=True)
    context = await browser.new_context(
        accept_downloads=False,
        proxy={"server": f"http://127.0.0.1:{proxy.port}"},
    )
    try:
        page = await context.new_page()
        runtime = {"run_id": "discovery-render", "policy_error": None}

        async def guard(route, request):
            await _request_guard(
                route, request, runtime, enforce_domain_policy=False)

        await context.route("**/*", guard)
        await page.goto(url, timeout=30000, wait_until="networkidle")
        if runtime.get("policy_error"):
            return None
        return (await page.inner_text("body"))[:MAX_PAGE_TEXT]
    except Exception:
        return None
    finally:
        await context.close()


async def inspect(page) -> dict:
    """Read the form's fields from the DOM (Tier 0 source of truth)."""
    fields = await page.eval_on_selector_all(
        "input, textarea, select",
        """els => els.map(e => ({
            name: e.name || e.id || "",
            label: (e.labels && e.labels[0] ? e.labels[0].innerText : "") || e.placeholder || "",
            type: e.tagName.toLowerCase() === "select" ? "select" : (e.type || e.tagName.toLowerCase()),
            required: e.required
        }))""",
    )
    fields = [
        f
        for f in fields
        if f["name"] and f["type"] not in ("hidden", "submit", "button")
    ]
    return {
        "status": "success",
        "fields": fields,
        "signature": page_signature([f["name"] for f in fields]),
    }


async def fill(page, mapping: dict[str, str], attachments: dict[str, str]) -> dict:
    """Fill by field name. Per-field try/catch — partial success is first-class."""
    filled, needs_human = [], []
    for name, value in mapping.items():
        try:
            field_type = await page.eval_on_selector(
                f"[name='{name}']",
                "e => e.tagName.toLowerCase() === 'select' ? 'select' : (e.type || e.tagName.toLowerCase())",
            )
            if field_type == "file":
                if name in attachments:
                    await page.set_input_files(f"[name='{name}']", attachments[name])
                    filled.append(name)
                else:
                    needs_human.append(
                        {"field": name, "reason": "file upload — choose the file"}
                    )
            elif field_type == "select":
                await page.select_option(f"[name='{name}']", label=value)
                filled.append(name)
            elif field_type in ("checkbox", "radio"):
                needs_human.append(
                    {"field": name, "reason": f"{field_type} needs founder judgment"}
                )
            else:
                await page.fill(f"[name='{name}']", value)
                filled.append(name)
        except Exception:
            needs_human.append({"field": name, "reason": "not found or not fillable"})
    return {
        "status": "success",
        "filled": len(filled),
        "filled_fields": filled,
        "needs_human": needs_human,
    }


async def screenshot(page, path: str) -> str:
    await page.screenshot(path=path, full_page=True)
    return path


async def _scoped_confirmation(page) -> str | None:
    """Read the portal's dedicated confirmation element, if the current page is
    a receipt. The mock portal renders the reference inside ``.receipt .id``
    (mock_portal/main.py::_receipt) — anchoring on that element, rather than
    scanning the whole body, is what keeps an ordinary hyphenated token in the
    page text ("ISO-8601", "REF-2026") from reading as a false submit success."""
    try:
        element = await page.query_selector(
            ".receipt .id, [data-confirmation-id], .confirmation-id")
        if element is None:
            return None
        text = (await element.inner_text()).strip()
        return text or None
    except Exception:
        return None


async def _read_submit_confirmation(page) -> dict:
    """Decide submit success STRICTLY, from an explicit confirmation signal.

    Order: the portal's dedicated confirmation element, then an explicit
    "Confirmation reference: X" line, then an id token that appears in an
    explicit confirmation-labeled context. Never a greedy body-wide id match."""
    cid = await _scoped_confirmation(page)
    if cid:
        return {"status": "success", "confirmation_id": cid[:120]}
    try:
        body = await page.inner_text("body")
    except Exception as exc:
        return {"status": "error", "error": True,
                "message": f"could not read result page: {exc}"[:180]}
    for line in body.splitlines():
        lowered = line.lower()
        if ("confirmation" in lowered or "reference" in lowered) and ":" in line:
            value = line.split(":", 1)[1].strip()
            if value:
                return {"status": "success", "confirmation_id": value[:120]}
    # Last resort: an id-shaped token, but ONLY within a confirmation-labeled
    # context (id must follow the word confirmation/reference within ~40 chars),
    # so standards tokens elsewhere on the page can never be mistaken for a receipt.
    match = re.search(
        r"(?i)(?:confirmation|reference)[^\n]{0,40}?\b([A-Z]{2,4}-[0-9A-Za-z]{4,})\b",
        body)
    if match:
        return {"status": "success", "confirmation_id": match.group(1)}
    return {
        "status": "error",
        "error": True,
        "message": "submitted but no confirmation id found on result page",
    }


async def submit(page, idempotency_key: str,
                 routing: dict[str, str] | None = None) -> dict:
    """Click submit with the derived Idempotency-Key. The portal dedupes
    (docs/05, 09) — a retry returns the ORIGINAL confirmation."""
    headers = {"Idempotency-Key": idempotency_key}
    if routing:
        headers.update({
            "X-CoFounder-Session-Id": routing.get("session_id", ""),
            "X-CoFounder-Application-Id": routing.get("application_id", ""),
            "X-CoFounder-User-Id": routing.get("user_id", ""),
        })
    await page.set_extra_http_headers(headers)
    lock = _submit_locks.setdefault(idempotency_key, asyncio.Lock())
    try:
        async with lock:
            # Already on a receipt (a prior click for this key landed, or a
            # concurrent caller just submitted): return that confirmation instead
            # of clicking again. The derived key means this is the SAME
            # submission. Guard against a form page that merely carries a
            # confirmation-shaped element: only trust the short-circuit once the
            # submit control is gone (i.e. we have actually left the form).
            submit_selector = "button[type='submit'], input[type='submit']"
            prior = await _scoped_confirmation(page)
            if prior and await page.query_selector(submit_selector) is None:
                return {"status": "success", "confirmation_id": prior[:120]}
            try:
                await page.click(submit_selector)
            except Exception as exc:
                # The click never fired — nothing was submitted; safe to retry.
                return {"status": "error", "error": True,
                        "message": f"submit click failed: {exc}"}
            # The click fired: the POST may be in flight. A networkidle timeout
            # here does NOT mean the submission failed — reconcile from the
            # page's own confirmation signal rather than re-clicking (a blind
            # re-click, and the old greedy regex, are what caused false success
            # / double-submit).
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            # Give a slow receipt time to render before concluding failure, so a
            # successful-but-slow POST is reconciled here rather than reported as
            # a failure the caller has to retry.
            try:
                await page.wait_for_selector(
                    ".receipt .id, [data-confirmation-id], .confirmation-id",
                    timeout=10000)
            except Exception:
                pass
            return await _read_submit_confirmation(page)
    finally:
        # Drop our lock entry once nothing else holds or awaits it, so the map
        # can't grow without bound. Only our own instance, and only when free —
        # a concurrent holder/waiter keeps its reference and a fresh setdefault
        # would otherwise hand a new caller a different lock (reopening the race).
        if (_submit_locks.get(idempotency_key) is lock
                and not lock.locked()
                and not getattr(lock, "_waiters", None)):
            _submit_locks.pop(idempotency_key, None)


async def _validate_portal_target(url: str) -> dict | None:
    """SSRF fence for the CREDENTIALED paths (register/open_and_login).

    These contexts type portal passwords into whatever page they land on, and
    their URLs originate from model-extracted opportunity records — untrusted.
    The read-only browse stack routes through the validating proxy; these
    interactive POST-ing contexts validate the target up front instead:
    scheme/credential checks plus DNS resolution with the private/reserved
    tables. Loopback is exempt in local dev only (the mock portal)."""
    parts, message = _url_parts(url)
    if parts is None:
        return _error("policy_refused", message)
    host = (parts.hostname or "").lower()
    if not os.environ.get("K_SERVICE") and host in ("127.0.0.1", "localhost"):
        return None
    refusal = await validate_public_url(url)
    if refusal:
        return _error("ssrf_blocked", refusal)
    return None


async def register(portal_url: str, email: str, password: str) -> dict:
    """Open a portal's signup page and create an account (docs/17).

    Heuristic, email+password only: finds the email + password fields on a
    signup/register page and submits. SSO-only or bot-challenged pages return
    blockers as data — the agent never improvises around them."""
    refusal = await _validate_portal_target(portal_url)
    if refusal:
        return refusal
    context = await new_context()
    try:
        page = await context.new_page()
        base = portal_url.rstrip("/")
        found = False
        for candidate in (
            f"{base}/signup",
            f"{base}/register",
            f"{base}/auth/signup",
            base,
        ):
            try:
                await page.goto(candidate, timeout=15000)
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                continue
            if await page.query_selector("input[type='password']"):
                found = True
                break
        body = (await page.inner_text("body"))[:800] if page.url else ""
        lowered = body.lower()
        if any(s in lowered for s in ("captcha", "verify you are human", "cloudflare")):
            await context.close()
            return {
                "status": "blocked",
                "error": True,
                "message": "bot protection detected on signup — screenshot and hand "
                "to the founder; Alex never solves CAPTCHAs (docs/17)",
            }
        if not found:
            await context.close()
            return {
                "status": "blocked",
                "error": True,
                "message": "no email+password signup form found (possibly SSO-only) — "
                "reported as a blocker",
            }
        # Host pin (mirrors open_and_login): page.goto follows redirects, so an
        # SSO bounce or open redirect could land the signup form on a different
        # origin. Never type the generated portal password/email cross-origin.
        expected_host = (urlsplit(portal_url).hostname or "").lower()
        landed_host = (urlsplit(page.url).hostname or "").lower()
        if landed_host != expected_host:
            await context.close()
            return {"status": "blocked", "error": True,
                    "message": f"signup page is on {landed_host!r}, not the portal "
                               f"{expected_host!r} — credentials withheld; hand this "
                               "portal to the founder"}
        email_sel = "input[type='email'], [name='email'], [name='username']"
        await page.fill(email_sel, email)
        pw_fields = await page.query_selector_all("input[type='password']")
        await pw_fields[0].fill(password)
        if len(pw_fields) > 1:  # confirm-password field
            await pw_fields[1].fill(password)
        await page.click("button[type='submit'], input[type='submit']")
        await page.wait_for_load_state("networkidle", timeout=15000)
        body = (await page.inner_text("body"))[:800]
        return {"status": "success", "context": context, "page": page, "body": body}
    except Exception as exc:
        await context.close()
        return {
            "status": "error",
            "error": True,
            "message": f"registration failed: {exc}",
        }


async def verify_registration_link(url: str, expected_host: str) -> dict:
    """Open one emailed verification link, constrained to the approved host."""
    parts, message = _url_parts(url)
    if parts is None:
        return _error("policy_refused", message)
    if parts.netloc.lower() != expected_host.lower():
        return _error(
            "policy_refused",
            "verification link host does not match the approved portal",
        )
    host = parts.hostname or ""
    ips = await _resolve(
        host, parts.port or (443 if parts.scheme == "https" else 80))
    local_dev = not os.environ.get("K_SERVICE") and host in ("127.0.0.1", "localhost")
    if (not local_dev) and (not ips or any(_unsafe_ip(ip) for ip in ips)):
        return _error("ssrf_blocked", "verification host resolves to a private address")
    context = await new_context()
    try:
        page = await context.new_page()

        async def same_origin_get_only(route, request):
            request_parts, _ = _url_parts(request.url)
            if (request.method.upper() not in ("GET", "HEAD")
                    or request_parts is None
                    or request_parts.netloc.lower() != expected_host.lower()):
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        await context.route("**/*", same_origin_get_only)
        await page.goto(url, timeout=30000, wait_until="networkidle")
        body = (await page.inner_text("body"))[:800]
        if any(word in body.lower() for word in ("not valid", "expired", "error")):
            return _error("verification_failed", "portal rejected the verification link")
        return {"status": "success", "verified": True}
    except Exception as exc:
        return _error("verification_failed", f"verification failed: {exc}"[:300])
    finally:
        await context.close()


async def open_and_login(portal_url: str, username: str, password: str) -> dict:
    """Open the portal and log in. Returns the live page on success."""
    refusal = await _validate_portal_target(portal_url)
    if refusal:
        return refusal
    expected_host = (urlsplit(portal_url).hostname or "").lower()
    context = await new_context()
    try:
        page = await context.new_page()
        await page.goto(portal_url, timeout=30000)
        if not await page.query_selector("input[type='password']"):
            # landing page isn't the login page — try the conventional path
            await page.goto(portal_url.rstrip("/") + "/login", timeout=15000)
        if await page.query_selector("input[type='password']"):
            # Host pin: redirects (SSO bounce, open redirect) must never end
            # with the portal credential typed into a different origin.
            landed_host = (urlsplit(page.url).hostname or "").lower()
            if landed_host != expected_host:
                await context.close()
                return {"status": "blocked", "error": True,
                        "message": f"login page is on {landed_host!r}, not the "
                                   f"portal {expected_host!r} — credentials "
                                   "withheld; hand this portal to the founder"}
            await page.fill("[name='username'], [name='email']", username)
            await page.fill("[name='password']", password)
            await page.click("button[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.goto(
                portal_url, timeout=30000
            )  # login redirects home; go to the form
            await page.wait_for_load_state("networkidle", timeout=15000)
        return {
            "status": "success",
            "context": context,
            "page": page,
            "title": await page.title(),
        }
    except Exception as exc:
        await context.close()
        return {
            "status": "error",
            "error": True,
            "message": f"open/login failed: {exc}",
        }


# ---------------------------------------------------------------------------
# General-purpose read-only browser runs (docs/18)
# ---------------------------------------------------------------------------


async def _audit_refusal(url: str, code: str, message: str, run_id: str = "") -> None:
    try:
        host = urlsplit(url).hostname or "unknown"
        await firestore.audit(
            "agent:co_founder",
            "browse_action" if run_id else "browse_open",
            host,
            "refused",
            json.dumps({"code": code, "message": message[:180]}),
        )
    except Exception:
        # Audit availability must not turn a policy refusal into an exception.
        pass


async def _request_guard(route, request, runtime: dict[str, Any],
                         *, enforce_domain_policy: bool = True) -> None:
    method = request.method.upper()
    post_data = request.post_data
    if method not in ("GET", "HEAD") or post_data:
        runtime["policy_error"] = _error(
            "policy_refused", "browse requests are GET/HEAD only"
        )
        await _audit_refusal(
            request.url, "policy_refused", "unsafe method/body", runtime["run_id"]
        )
        await route.abort("blockedbyclient")
        return
    code, message, _canonical, _ips = await _validate_url_async(
        request.url, enforce_domain_policy=enforce_domain_policy)
    if code:
        runtime["policy_error"] = _error(code, message or "request refused")
        await _audit_refusal(
            request.url, code, message or "request refused", runtime["run_id"]
        )
        await route.abort("blockedbyclient")
        return
    await route.continue_()


async def _new_browse_context(run_id: str) -> dict[str, Any]:
    browser = await get_browser()
    proxy = await _get_proxy()
    context = await browser.new_context(
        accept_downloads=False,
        proxy={"server": f"http://127.0.0.1:{proxy.port}"},
    )
    runtime: dict[str, Any] = {
        "run_id": run_id,
        "context": context,
        "page": None,
        "dom_hash": "",
        "text": "",
        "links": [],
        "artifact": None,
        "injection_suspected": False,
        "policy_error": None,
        "bot_captured": False,
    }

    async def guard(route, request):
        await _request_guard(route, request, runtime)

    await context.route("**/*", guard)

    async def close_popup(page):
        if runtime.get("page") is not None and page is not runtime.get("page"):
            await page.close()

    context.on("page", close_popup)
    page = await context.new_page()
    runtime["page"] = page

    async def cancel_download(download):
        await download.cancel()

    page.on("download", cancel_download)
    _browse_contexts[run_id] = runtime
    return runtime


async def extract_page_text(page) -> dict:
    """Extract bounded visible text, ordered links, title, and a deterministic hash."""
    raw = await page.evaluate(
        """() => {
          const copy = document.body ? document.body.cloneNode(true) : document.createElement('body');
          copy.querySelectorAll('script,style,noscript,template,svg').forEach(e => e.remove());
          const text = (copy.innerText || copy.textContent || '').replace(/\\s+/g, ' ').trim();
          const links = [...document.querySelectorAll('a[href]')].slice(0, 100).map(a => ({
            text: (a.innerText || a.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim(),
            href: a.href
          }));
          return {text, links, title: document.title || ''};
        }"""
    )
    text = str(raw.get("text", ""))[:MAX_PAGE_TEXT]
    links = []
    for item in raw.get("links", []):
        href = urljoin(page.url, str(item.get("href", "")))
        if href.startswith(("http://", "https://")):
            links.append(
                {"text": str(item.get("text", ""))[:200], "href": redact_url(href)}
            )
        if len(links) >= 15:
            break
    digest_input = text + "\n" + "\n".join(item["href"] for item in links)
    return {
        "text": text,
        "links": links,
        "dom_hash": hashlib.sha256(digest_input.encode()).hexdigest(),
        "title": str(raw.get("title", ""))[:300],
    }


def scan_injection(text: str) -> bool:
    """Detect instruction-shaped content; reads remain allowed, actions suspend."""
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


async def classify_page(page) -> str:
    """Deterministically classify article/search/form/auth/portal surfaces."""
    facts = await page.evaluate(
        """() => ({
          passwords: document.querySelectorAll('input[type=password]').length,
          namedFields: [...document.querySelectorAll('input,textarea,select')]
            .filter(e => (e.name || e.id) && !['hidden','submit','button'].includes(e.type)).length,
          oauth: [...document.querySelectorAll('button,a')].some(e =>
            /sign in with|continue with|log in with|oauth/i.test(e.innerText || e.getAttribute('aria-label') || '')),
          searches: document.querySelectorAll('input[type=search],[role=searchbox],form[role=search]').length,
          forms: document.querySelectorAll('form').length
        })"""
    )
    path = urlsplit(page.url).path.lower()
    if facts.get("passwords") or facts.get("oauth"):
        return "auth"
    portal_path = re.search(
        r"/(apply|application|signup|register|login|portal)(?:/|$)", path
    )
    if facts.get("namedFields", 0) >= 3:
        return "portal" if portal_path else "form"
    if portal_path and facts.get("forms"):
        return "portal"
    if facts.get("searches"):
        return "search"
    return "article"


async def detect_bot_challenge(page) -> dict | None:
    """Return challenge evidence for CAPTCHA/Cloudflare/human-verification pages."""
    evidence = await page.evaluate(
        """() => {
          const markup = document.documentElement ? document.documentElement.innerHTML : '';
          const headings = [...document.querySelectorAll('h1,h2,h3,[role=heading]')]
            .map(e => e.innerText || '').join(' ');
          const title = document.title || '';
          const captcha = !!document.querySelector(
            'iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="turnstile"],'
            + 'script[src*="recaptcha"],script[src*="hcaptcha"],script[src*="turnstile"],'
            + '#cf-challenge-running,.cf-challenge,[data-sitekey]');
          const wording = /verify (you are|that you are) human|checking your browser|attention required/i
            .test(title + ' ' + headings + ' ' + markup.slice(0, 20000));
          return {captcha, wording, title};
        }"""
    )
    return evidence if evidence.get("captcha") or evidence.get("wording") else None


async def save_pageshot(run_id: str, seq: int, tag: str) -> str:
    if tag not in {"before", "after", "nav", "blocked"}:
        raise ValueError("invalid pageshot tag")
    runtime = _browse_contexts.get(run_id)
    if not runtime:
        raise RuntimeError("browser context is unavailable")
    artifact = f"pageshot_{run_id}_{seq}_{tag}.png"
    shot = await runtime["page"].screenshot(full_page=True)
    # storage.save_bytes mirrors to GCS synchronously (blocking) — offload it.
    await asyncio.to_thread(storage.save_bytes, artifact, shot)
    runtime["artifact"] = artifact
    await firestore.update_browser_run(run_id, screenshot_artifact=artifact)
    return artifact


async def _extract_and_store(run_id: str, seq: int) -> dict:
    runtime = _browse_contexts[run_id]
    extracted = await extract_page_text(runtime["page"])
    artifact = f"page_{run_id}_{seq}.txt"
    # storage.save_text mirrors to GCS synchronously (blocking) — offload it.
    await asyncio.to_thread(storage.save_text, artifact, extracted["text"])
    runtime.update(extracted)
    runtime["text_artifact"] = artifact
    runtime["injection_suspected"] = scan_injection(extracted["text"])
    await firestore.update_browser_run(
        run_id,
        current_url=redact_url(runtime["page"].url),
        title=extracted["title"],
    )
    return {**extracted, "artifact": artifact}


async def _freeze_for_bot(run_id: str, seq: int) -> dict:
    runtime = _browse_contexts[run_id]
    artifact = runtime.get("artifact")
    if not runtime.get("bot_captured"):
        artifact = await save_pageshot(run_id, seq, "blocked")
        runtime["bot_captured"] = True
        await firestore.audit(
            "agent:co_founder",
            "bot_challenge",
            f"browser_runs/{run_id}",
            "refused",
            json.dumps({"url": redact_url(runtime["page"].url)}),
        )
    await firestore.update_browser_run(
        run_id, status="blocked", close_reason="bot_challenge"
    )
    return _error(
        "bot_challenge",
        "bot protection detected; the run is frozen",
        reason="Bot protection requires the founder",
        screenshot_artifact=artifact,
    )


async def open_run(session_key: dict, url: str, purpose: str) -> dict:
    """Serialize open/supersede for the v1 single browser-owning process."""
    identity = (
        str(session_key.get("app_name") or "co_founder"),
        str(session_key.get("user_id") or ""),
        str(session_key.get("session_id") or ""),
    )
    lock = _session_locks.setdefault(identity, asyncio.Lock())
    async with lock:
        return await _open_run(session_key, url, purpose)


async def _open_run(session_key: dict, url: str, purpose: str) -> dict:
    """Open or reuse a durable, isolated, read-only browser run."""
    purpose = " ".join(str(purpose).split())[:200]
    if not purpose:
        return _error("policy_refused", "purpose is required")
    code, message, canonical, _ips = await _validate_url_async(url)
    if code:
        await _audit_refusal(url, code, message or "URL refused")
        return _error(code, message or "URL refused")

    app_name = str(session_key.get("app_name") or "co_founder")
    user_id = str(session_key.get("user_id") or "")
    session_id = str(session_key.get("session_id") or "")
    if not user_id or not session_id:
        return _error("no_active_run", "a user and session are required")
    active = await current_run_for_session(
        {"app_name": app_name, "user_id": user_id, "session_id": session_id}, "browse"
    )
    if active and active.get("status") == "blocked":
        if active.get("goal") == purpose:
            return _error(
                "bot_challenge",
                "the current browser run is frozen by bot protection; close it first",
                reason="Bot protection requires the founder",
            )
        await close_run(active["run_id"], "superseded", "agent:co_founder")
        active = None
    if active and active.get("goal") != purpose:
        await close_run(active["run_id"], "superseded", "agent:co_founder")
        active = None
    if active and active["run_id"] not in _browse_contexts:
        await firestore.update_browser_run(
            active["run_id"], status="closed", close_reason="restart"
        )
        active = None

    run_id = active["run_id"] if active else uuid.uuid4().hex
    if not active:
        now = _now()
        await firestore.create_browser_run(
            {
                "run_id": run_id,
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id,
                "kind": "browse",
                "goal": purpose,
                "status": "active",
                "close_reason": None,
                "current_url": None,
                "title": None,
                "last_action": None,
                "screenshot_artifact": None,
                "action_count": 0,
                "started_at": now.isoformat(),
                "deadline_at": (
                    now + timedelta(seconds=BROWSE_TIME_BOX_SECONDS)
                ).isoformat(),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        )
    try:
        runtime = _browse_contexts.get(run_id) or await _new_browse_context(run_id)
        runtime["policy_error"] = None
        response = await runtime["page"].goto(
            canonical, wait_until="domcontentloaded", timeout=30_000
        )
        await runtime["page"].wait_for_timeout(
            min(2000, int(os.environ.get("BROWSE_SETTLE_MS", "800")))
        )
        if runtime.get("policy_error"):
            result = runtime["policy_error"]
            await close_run(run_id, "error", "agent:co_founder")
            return result
        if response is not None and response.status >= 400:
            await firestore.audit(
                "agent:co_founder",
                "browse_open",
                urlsplit(canonical).hostname or "",
                "error",
                json.dumps({"url": redact_url(canonical), "status": response.status}),
            )
            await close_run(run_id, "error", "agent:co_founder")
            return _error("page_unavailable", f"page returned HTTP {response.status}")
        challenge = await detect_bot_challenge(runtime["page"])
        if challenge:
            return await _freeze_for_bot(
                run_id, int(active.get("action_count", 0)) if active else 0
            )
        extracted = await _extract_and_store(
            run_id, int(active.get("action_count", 0)) if active else 0
        )
        shot = await save_pageshot(
            run_id, int(active.get("action_count", 0)) if active else 0, "nav"
        )
        await firestore.audit(
            "agent:co_founder",
            "browse_open",
            urlsplit(canonical).hostname or "",
            "success",
            json.dumps(
                {
                    "url": redact_url(runtime["page"].url),
                    "injection_suspected": runtime["injection_suspected"],
                }
            ),
        )
        return {
            "status": "success",
            "run_id": run_id,
            "url": redact_url(runtime["page"].url),
            "title": extracted["title"],
            "excerpt": extracted["text"][:600],
            "links": extracted["links"],
            "screenshot_artifact": shot,
        }
    except Exception as exc:
        await close_run(run_id, "error", "agent:co_founder")
        code = "timeout" if "Timeout" in type(exc).__name__ else "browser_unavailable"
        return _error(code, f"browser open failed: {str(exc)[:240]}")


async def read_current(run_id: str, question: str) -> dict:
    """Answer a question from the current page through the isolated reader."""
    run = await firestore.get_browser_run(run_id)
    runtime = _browse_contexts.get(run_id)
    if not run or run.get("status") not in ("active", "blocked") or not runtime:
        return _error("no_active_run", "there is no active browser page")
    try:
        current = await extract_page_text(runtime["page"])
        if current["dom_hash"] != runtime.get("dom_hash") or redact_url(
            runtime["page"].url
        ) != run.get("current_url"):
            current = await _extract_and_store(run_id, int(run.get("action_count", 0)))
        else:
            current["artifact"] = runtime["text_artifact"]
        suspected = scan_injection(current["text"])
        runtime["injection_suspected"] = suspected
        if _reader_fn is None:
            return _error("model_error", "isolated browser reader is not configured")
        answer = await _reader_fn(run["goal"], str(question)[:500], current["text"])
        start = max(0, int(answer.get("excerpt_start", 0)))
        end = min(len(current["text"]), int(answer.get("excerpt_end", start)))
        if end < start:
            start, end = 0, min(len(current["text"]), 600)
        if suspected:
            await firestore.audit(
                "agent:co_founder",
                "browse_action",
                f"browser_runs/{run_id}",
                "success",
                json.dumps(
                    {"url": run.get("current_url"), "injection_suspected": True}
                ),
            )
        return {
            "status": "success",
            "answer": str(answer.get("answer", ""))[:4000],
            "excerpt_ref": {
                "artifact": current["artifact"],
                "start": start,
                "end": end,
            },
            "injection_suspected": suspected,
        }
    except Exception as exc:
        return _error("model_error", f"page reading failed: {str(exc)[:240]}")


async def _interactive_snapshot(page) -> dict:
    """Index visible interactive DOM nodes and return a stable-key snapshot."""
    items = await page.evaluate(
        """() => {
          let n = 0;
          const selector = 'a[href],button,summary,input,textarea,select,[role=button],'
            + '[role=searchbox],[aria-expanded],[data-browser-scroll]';
          return [...document.querySelectorAll(selector)].filter(e => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden';
          }).slice(0, 200).map(e => {
            const key = `e${n++}`;
            e.setAttribute('data-cf-browser-key', key);
            return {
              key, tag: e.tagName.toLowerCase(), role: e.getAttribute('role') || '',
              label: (e.getAttribute('aria-label') || e.innerText || e.placeholder || e.name || '')
                .replace(/\\s+/g, ' ').trim().slice(0, 180),
              href: e.href || '', type: e.type || '', name: e.name || '',
              placeholder: e.placeholder || '', target: e.target || '',
              download: e.hasAttribute('download'), expanded: e.getAttribute('aria-expanded'),
              formMethod: e.form ? (e.form.method || 'get').toLowerCase() : '',
              submit: e.matches('button[type=submit],input[type=submit]')
                || /^(submit|apply|register|sign up|log in|send|save|confirm)$/i
                  .test((e.innerText || e.value || '').trim())
            };
          });
        }"""
    )
    extracted = await extract_page_text(page)
    lines = [
        f"[{item['key']}]<{item['tag']} role={item['role']!r} "
        f"aria-label={item['label']!r} href={redact_url(item['href']) if item['href'] else ''!r}/>"
        for item in items
    ]
    return {
        "items": {item["key"]: item for item in items},
        "text": "\n".join(lines),
        "dom_hash": extracted["dom_hash"],
    }


def _validate_research_proposal(
    proposal: dict, snapshot: dict
) -> tuple[str | None, dict | None]:
    action = str(proposal.get("action", ""))
    allowed = {"click", "search", "scroll", "navigate_back", "wait"}
    if action not in allowed:
        return "action is outside the browsing allowlist", None
    target_key = str(proposal.get("target_key", ""))
    target = snapshot["items"].get(target_key) if target_key else None
    if action in {"click", "search"} and target is None:
        return "target key is missing or stale", None
    if action == "click":
        # Full click-through navigation (links AND buttons). What stays closed
        # in code: submit-semantics controls, form fields, downloads, popups,
        # and any URL that fails the network policy. A click that reveals a
        # form/auth surface freezes further actions (classify_page, below).
        if target.get("submit"):
            return (
                "submit-semantics controls are never clicked while browsing — "
                "this would commit something; report what it would do instead",
                None,
            )
        if target.get("tag") in {"input", "textarea", "select"}:
            return (
                "form fields are not clicked while browsing — search boxes "
                "take the search action; anything else is the founder's",
                None,
            )
        if target.get("download") or target.get("target") == "_blank":
            return "downloads and popup links are not allowed", None
        href = target.get("href", "")
        if href:
            if not href.startswith(("http://", "https://")):
                return "link target is not a safe http/https URL", None
            code, message, _canonical = _validate_url_detail(href)
            if code:
                return message or "link URL refused", None
    elif action == "search":
        recognized = (
            target.get("type") == "search"
            or target.get("role") == "searchbox"
            or re.search(
                r"search",
                " ".join((target.get("name", ""), target.get("placeholder", ""))),
                re.IGNORECASE,
            )
        )
        text = str(proposal.get("text") or "")
        if not recognized or target.get("tag") not in {"input", "textarea"}:
            return "search requires a recognized search input", None
        if not text or len(text) > 200 or _SECRET_TEXT.search(text):
            return "search text is empty, too long, or resembles a secret", None
        if target.get("formMethod") not in ("", "get"):
            return "search form must use GET", None
    return None, target


async def execute_action(
    page, proposal: ActionProposal, policy: str, run_id: str, invocation_id: str
) -> dict:
    """Execute one validated action under the research or form-fill policy."""
    action = proposal["action"]
    key = proposal.get("target_key", "")
    selector = f'[data-cf-browser-key="{key}"]' if key else ""
    if policy == "research":
        if action == "click":
            # A real click — links navigate, buttons run their JS. Anything
            # state-changing is stopped downstream: the request guard aborts
            # non-GET/HEAD, and submit controls never reach this point.
            await page.click(selector, timeout=5_000)
        elif action == "scroll":
            if key:
                await page.eval_on_selector(
                    selector, "e => e.scrollBy(0, Math.max(400, e.clientHeight * .8))"
                )
            else:
                await page.mouse.wheel(0, 700)
        elif action == "navigate_back":
            await page.go_back(wait_until="domcontentloaded", timeout=30_000)
        elif action == "search":
            await page.fill(selector, proposal.get("text") or "")
            await page.press(selector, "Enter")
        elif action == "wait":
            seconds = min(5.0, max(0.0, float(proposal.get("text") or 1)))
            await page.wait_for_timeout(int(seconds * 1000))
        else:
            return _error("policy_refused", f"unsupported research action: {action}")
    elif policy == "form_fill":
        # The caller must index a target and the shared primitive still blocks
        # submit semantics. Credentials remain owned by the portal path.
        if action not in {"click", "type", "select", "scroll", "navigate_back"}:
            return _error("policy_refused", f"unsupported form-fill action: {action}")
        if action == "click":
            submit = await page.eval_on_selector(
                selector,
                "e => e.matches('button[type=submit],input[type=submit]') || "
                "/^(submit|send|apply|confirm)$/i.test((e.innerText || e.value || '').trim())",
            )
            if submit:
                return _error(
                    "policy_refused", "submit controls are excluded from form recon"
                )
            await page.click(selector, timeout=5_000)
        elif action == "type":
            await page.fill(selector, proposal.get("text") or "")
        elif action == "select":
            await page.select_option(selector, label=proposal.get("text") or "")
        elif action == "scroll":
            await page.mouse.wheel(0, 700)
        elif action == "navigate_back":
            await page.go_back(timeout=30_000)
    else:
        return _error("policy_refused", f"unknown browser policy: {policy}")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=8_000)
    except Exception:
        pass
    return {
        "status": "success",
        "action": action,
        "target_key": key,
        "invocation_id": invocation_id,
    }


async def record_action_budget(run_id: str) -> dict:
    """Atomically reserve an action. The twentieth runs; the twenty-first refuses."""
    return await firestore.reserve_browser_action(
        run_id, _now_iso(), MAX_BROWSE_ACTIONS
    )


async def propose_and_act(run_id: str, invocation_id: str) -> dict:
    """Propose, validate, reserve, ledger, execute, and persist one research action."""
    lock = _browse_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        run = await firestore.get_browser_run(run_id)
        runtime = _browse_contexts.get(run_id)
        if not run or run.get("status") == "closed" or not runtime:
            return _error("no_active_run", "there is no active browser run")
        if run.get("status") == "blocked":
            return _error(
                "bot_challenge",
                "the browser run is frozen by bot protection",
                reason="Bot protection requires the founder",
            )
        action_id = hashlib.sha256(f"{run_id}:{invocation_id}".encode()).hexdigest()
        existing = await firestore.get_browser_action(run_id, action_id)
        if existing:
            if existing.get("status") in {"SUCCEEDED", "FAILED", "UNCERTAIN"}:
                return existing.get("result_ref") or _error(
                    "policy_refused", "the prior action has no recorded result"
                )
            await firestore.update_browser_action(
                run_id,
                action_id,
                status="UNCERTAIN",
                result_ref=_error(
                    "policy_refused",
                    "a prior action may have executed; not replaying",
                    reason="Action outcome is uncertain after restart",
                ),
            )
            return _error(
                "policy_refused",
                "a prior action may have executed; not replaying",
                reason="Action outcome is uncertain after restart",
            )
        try:
            if await detect_bot_challenge(runtime["page"]):
                return await _freeze_for_bot(run_id, int(run.get("action_count", 0)))
            extracted = await _extract_and_store(
                run_id, int(run.get("action_count", 0))
            )
            if runtime["injection_suspected"]:
                await firestore.audit(
                    "agent:co_founder",
                    "browse_action",
                    f"browser_runs/{run_id}",
                    "refused",
                    json.dumps(
                        {"url": run.get("current_url"), "injection_suspected": True}
                    ),
                )
                return _error(
                    "injection_suspected",
                    "page content looks like instructions; actions are suspended",
                    reason="Page content may be attempting prompt injection",
                )
            page_class = await classify_page(runtime["page"])
            if page_class in {"auth", "form", "portal"}:
                route = "form_filler" if page_class in {"form", "portal"} else None
                await firestore.audit(
                    "agent:co_founder",
                    "browse_action",
                    f"browser_runs/{run_id}",
                    "refused",
                    json.dumps(
                        {"url": run.get("current_url"), "page_class": page_class}
                    ),
                )
                return _error(
                    "policy_refused",
                    f"{page_class} pages are outside general browsing",
                    reason=f"This is a {page_class} page",
                    route=route,
                )
            if _proposer_fn is None:
                return _error(
                    "model_error", "isolated browser proposer is not configured"
                )
            snapshot = await _interactive_snapshot(runtime["page"])
            shot = await runtime["page"].screenshot(full_page=True)
            proposal = await _proposer_fn(run["goal"], snapshot["text"], shot)
            # _validate_research_proposal runs synchronous DNS (getaddrinfo) for
            # link targets — offload it so the event loop is never blocked.
            refusal, target = await asyncio.to_thread(
                _validate_research_proposal, proposal, snapshot)
            if refusal:
                await firestore.audit(
                    "agent:co_founder",
                    "browse_action",
                    f"browser_runs/{run_id}",
                    "refused",
                    json.dumps(
                        {"url": run.get("current_url"), "reason": refusal[:180]}
                    ),
                )
                return _error("policy_refused", refusal)
            budget = await record_action_budget(run_id)
            if budget["exceeded"]:
                # Refuse the action but keep the run open: the founder may
                # still be reading the page. Only close_run ends a run.
                return _error(
                    "budget_exceeded",
                    "browser action budget exceeded; the page stays open for "
                    "reading — close the browser or start a new run to act more",
                    reason=budget.get("reason"),
                    budget_reason=budget.get("reason"),
                )
            seq = int(budget["count"])
            prepared = await firestore.prepare_browser_action(
                run_id,
                action_id,
                {
                    "seq": seq,
                    "proposal": dict(proposal),
                    "result_ref": None,
                },
            )
            if not prepared.get("created"):
                return _error(
                    "policy_refused",
                    "action identity was already prepared",
                    reason="Action outcome is uncertain",
                )
            await save_pageshot(run_id, seq, "before")
            fresh = await extract_page_text(runtime["page"])
            if fresh["dom_hash"] != snapshot["dom_hash"]:
                result = _error(
                    "stale_page", "the page changed before the action could execute"
                )
                await firestore.update_browser_action(
                    run_id, action_id, status="FAILED", result_ref=result
                )
                return result
            runtime["policy_error"] = None
            result = await execute_action(
                runtime["page"], proposal, "research", run_id, invocation_id
            )
            if result.get("error"):
                await firestore.update_browser_action(
                    run_id, action_id, status="FAILED", result_ref=result
                )
                return result
            await runtime["page"].wait_for_timeout(
                min(2000, int(os.environ.get("BROWSE_SETTLE_MS", "800")))
            )
            if runtime.get("policy_error"):
                result = runtime["policy_error"]
                await firestore.update_browser_action(
                    run_id, action_id, status="FAILED", result_ref=result
                )
                return result
            if await detect_bot_challenge(runtime["page"]):
                result = await _freeze_for_bot(run_id, seq)
                await firestore.update_browser_action(
                    run_id, action_id, status="FAILED", result_ref=result
                )
                return result
            extracted = await _extract_and_store(run_id, seq)
            after = await save_pageshot(run_id, seq, "after")
            label = (
                (target or {}).get("label")
                or proposal.get("target_key")
                or proposal["action"]
            )
            last_action = {
                "seq": seq,
                "kind": proposal["action"],
                "target": str(label)[:180],
                "at": _now_iso(),
            }
            await firestore.update_browser_run(run_id, last_action=last_action)
            # Sliding time-box (18): the 90 s budget bounds action *activity*,
            # not the founder's reading time — every successful action renews it.
            await firestore.update_browser_run(
                run_id,
                deadline_at=(
                    _now() + timedelta(seconds=BROWSE_TIME_BOX_SECONDS)
                ).isoformat(),
            )
            final = {
                "status": "success",
                "action": {"kind": proposal["action"], "target": str(label)[:180]},
                "url": redact_url(runtime["page"].url),
                "excerpt": extracted["text"][:600],
                "screenshot_artifact": after,
            }
            await firestore.update_browser_action(
                run_id, action_id, status="SUCCEEDED", result_ref=final
            )
            await firestore.audit(
                "agent:co_founder",
                "browse_action",
                f"browser_runs/{run_id}",
                "success",
                json.dumps(
                    {"url": redact_url(runtime["page"].url), "action_id": action_id}
                ),
            )
            return final
        except Exception as exc:
            result = _error(
                "timeout" if "Timeout" in type(exc).__name__ else "browser_unavailable",
                f"browser action failed: {str(exc)[:240]}",
            )
            if "action_id" in locals() and await firestore.get_browser_action(
                run_id, action_id
            ):
                await firestore.update_browser_action(
                    run_id, action_id, status="FAILED", result_ref=result
                )
            return result


async def close_run(run_id: str, reason: str, actor: str) -> dict:
    """Idempotently close a browser context and its durable run record."""
    run = await firestore.get_browser_run(run_id)
    # Close the live context FIRST, unconditionally — even when the durable
    # record already reads "closed". Otherwise a run whose Firestore row was
    # marked closed elsewhere (a reconcile tick, a supersede) but whose context
    # is still registered here would leak an open browser window forever.
    runtime = _browse_contexts.pop(run_id, None)
    _browse_locks.pop(run_id, None)  # bounded: per-run lock dies with the run
    if runtime:
        try:
            await runtime["context"].close()
        except Exception:
            pass
    if not run or run.get("status") == "closed":
        return {"status": "success", "already_closed": True}
    await firestore.update_browser_run(run_id, status="closed", close_reason=reason)
    action = "browse_stop" if reason == "founder_stop" else "browse_close"
    await firestore.audit(
        actor,
        action,
        f"browser_runs/{run_id}",
        "success",
        json.dumps({"url": run.get("current_url"), "reason": reason}),
    )
    return {"status": "success", "already_closed": False}


def _run_view(run: dict | None) -> dict | None:
    if not run:
        return None
    artifact = run.get("screenshot_artifact")
    return {
        "active": run.get("status") == "active",
        "kind": run.get("kind"),
        "run_id": run.get("run_id"),
        "url": run.get("current_url"),
        "title": run.get("title"),
        "goal": run.get("goal"),
        "screenshot_url": f"/api/artifacts/{artifact}/preview" if artifact else None,
        "last_action": run.get("last_action"),
        "status": run.get("status"),
    }


async def get_browser_state(session_key: dict) -> dict:
    """Return the latest browse and fill RunViews for the Founder panel."""
    rows = await firestore.list_browser_runs(
        str(session_key.get("app_name") or "co_founder"),
        str(session_key.get("user_id") or ""),
        str(session_key.get("session_id") or ""),
    )
    browse = next((row for row in rows if row.get("kind") == "browse"), None)
    fill_run = next((row for row in rows if row.get("kind") == "fill"), None)
    return {
        "status": "success",
        "browse": _run_view(browse),
        "fill": _run_view(fill_run),
    }


async def active_run_for_session(
    session_key: dict, kind: str = "browse"
) -> dict | None:
    return await firestore.find_active_browser_run(
        str(session_key.get("app_name") or "co_founder"),
        str(session_key.get("user_id") or ""),
        str(session_key.get("session_id") or ""),
        kind,
    )


async def current_run_for_session(
    session_key: dict, kind: str = "browse"
) -> dict | None:
    """Return the newest actionable run, including a bot-blocked run."""
    rows = await firestore.list_browser_runs(
        str(session_key.get("app_name") or "co_founder"),
        str(session_key.get("user_id") or ""),
        str(session_key.get("session_id") or ""),
        kind,
    )
    return next(
        (row for row in rows if row.get("status") in {"active", "blocked"}), None
    )


async def stop_browser(session_key: dict, actor: str) -> dict:
    """Founder endpoint service: idempotently stop this session's browse run."""
    active = await current_run_for_session(session_key, "browse")
    if not active:
        return {"status": "success", "already_closed": True}
    return await close_run(active["run_id"], "founder_stop", actor)


async def register_fill_run(session_key: dict, context, page, goal: str) -> dict:
    """Register an approval-gated form-filler context for the shared live panel."""
    active = await active_run_for_session(session_key, "fill")
    if active:
        await close_run(active["run_id"], "superseded", "agent:form_filler")
    run_id = uuid.uuid4().hex
    now = _now()
    await firestore.create_browser_run(
        {
            "run_id": run_id,
            "app_name": session_key.get("app_name") or "co_founder",
            "user_id": session_key.get("user_id") or "",
            "session_id": session_key.get("session_id") or "",
            "kind": "fill",
            "goal": " ".join(goal.split())[:200],
            "status": "active",
            "close_reason": None,
            "current_url": redact_url(page.url),
            "title": await page.title(),
            "last_action": None,
            "screenshot_artifact": None,
            "action_count": 0,
            "started_at": now.isoformat(),
            "deadline_at": (now + timedelta(minutes=30)).isoformat(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    )
    _browse_contexts[run_id] = {
        "run_id": run_id,
        "context": context,
        "page": page,
        "dom_hash": "",
        "text": "",
        "links": [],
        "artifact": None,
        "injection_suspected": False,
        "policy_error": None,
        "bot_captured": False,
    }
    artifact = await save_pageshot(run_id, 0, "nav")
    return {"status": "success", "run_id": run_id, "screenshot_artifact": artifact}


async def update_fill_run(
    run_id: str, kind: str, target: str, screenshot_artifact: str | None = None
) -> None:
    """Refresh the fill RunView after a guarded portal action."""
    runtime = _browse_contexts.get(run_id)
    if not runtime:
        return
    run = await firestore.get_browser_run(run_id)
    seq = int(run.get("action_count", 0)) + 1 if run else 1
    last_action = {"seq": seq, "kind": kind, "target": target[:180], "at": _now_iso()}
    fields: dict[str, Any] = {
        "action_count": seq,
        "last_action": last_action,
        "current_url": redact_url(runtime["page"].url),
        "title": await runtime["page"].title(),
    }
    if screenshot_artifact:
        fields["screenshot_artifact"] = screenshot_artifact
    await firestore.update_browser_run(run_id, **fields)


async def browser_status_projection(session_key: dict) -> dict:
    run = await current_run_for_session(session_key, "browse")
    if not run:
        run = await current_run_for_session(session_key, "fill")
    if not run:
        return {
            "active": False,
            "kind": None,
            "run_id": None,
            "url": None,
            "goal": None,
            "last_action": None,
        }
    return {
        "active": run.get("status") == "active",
        "kind": run.get("kind"),
        "run_id": run.get("run_id"),
        "url": run.get("current_url"),
        "goal": run.get("goal"),
        "last_action": run.get("last_action"),
    }


async def reconcile_session(session_key: dict) -> dict:
    """Close durable active runs whose in-process contexts disappeared."""
    rows = await firestore.list_browser_runs(
        str(session_key.get("app_name") or "co_founder"),
        str(session_key.get("user_id") or ""),
        str(session_key.get("session_id") or ""),
    )
    for row in rows:
        if row.get("status") == "active" and row["run_id"] not in _browse_contexts:
            await firestore.update_browser_run(
                row["run_id"], status="closed", close_reason="restart"
            )
    return await browser_status_projection(session_key)


async def reconcile_all_runs() -> None:
    for run in await firestore.list_active_browser_runs():
        if run["run_id"] not in _browse_contexts:
            await firestore.update_browser_run(
                run["run_id"], status="closed", close_reason="restart"
            )


async def shutdown() -> None:
    """Best-effort shutdown; durable restart reconciliation covers interruptions."""
    global _browser, _playwright, _proxy
    for runtime in list(_browse_contexts.values()):
        try:
            await runtime["context"].close()
        except Exception:
            pass
    _browse_contexts.clear()
    if _proxy is not None:
        await _proxy.close()
        _proxy = None
    if _browser is not None:
        try:
            await asyncio.wait_for(_browser.close(), timeout=5)
        except Exception:
            pass
        finally:
            _browser = None
    if _playwright is not None:
        try:
            await asyncio.wait_for(_playwright.stop(), timeout=5)
        except Exception:
            pass
        finally:
            _playwright = None
