"""Browser service (docs/05, 09) — Playwright session, page signatures,
fills, screenshots, idempotent submit. One browser per process; a fresh
context per fill run. All failures return error dicts.
"""

# ruff: noqa: BLE001, S110, S112 -- caller-facing browser boundaries convert
# third-party/network failures to typed error data by design (docs/18).

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect as pyinspect
import ipaddress
import json
import logging
import os
import re
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from typing import Any, TypedDict
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from services import browser_expiry, browser_metrics, firestore, storage
from services.browser_events import hub as browser_event_hub
from services.browser_runtime import (
    BrowserCapacityExceeded,
    BrowserForegroundBusy,
    BrowserRuntimeUnavailable,
    ContextLease,
    PolicyEventContext,
)
from services.browser_runtime import (
    runtime as browser_runtime,
)


class ActionProposal(TypedDict):
    action: str
    target_key: str
    text: str | None


MAX_BROWSE_ACTIONS = 20
BROWSE_TIME_BOX_SECONDS = 90
BROWSE_EXPIRY_SECONDS = 5 * 60
FILL_EXPIRY_SECONDS = 30 * 60

# Identity of THIS process's browser ownership. Stamped on every run it opens
# so startup reconciliation can only terminalize runs it actually owned.
# `browser_generation` cannot serve this role: it is a per-process counter that
# starts at 0 in every instance. During a rolling deploy two revisions overlap
# (--max-instances is per revision), and an unfenced sweep would close the other
# live instance's runs while their credentialed contexts keep executing.
INSTANCE_ID = f"{os.environ.get('K_REVISION', 'local')}:{uuid.uuid4().hex[:12]}"
MAX_PAGE_TEXT = 100_000
_CREDENTIAL_QUERY = re.compile(
    # `otp`/`sessionid` carry the same authority as the delimited spellings, and
    # `email` is PII that should not sit in a persisted/displayed URL.
    r"(?:^|[_-])(token|code|key|signature|session|auth|password|otp|email)(?:$|[_-])"
    r"|^(?:otp|sessionid|apikey)$",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"(?:api[_-]?key|access[_-]?token|password|bearer\s+[A-Za-z0-9._~-]{12,})",
    re.IGNORECASE,
)
# Every field class whose rendered value would be a credential, a one-time
# secret, or founder PII. Screenshots are durable evidence, so this errs wide:
# masking a harmless field costs nothing, exposing an OTP costs everything.
# Extend via PORTAL_SENSITIVE_SELECTORS for adapter-declared portal fields.
_SENSITIVE_SELECTORS = (
    "input[type=password]",
    "input[type=email]",
    "input[autocomplete*=one-time-code]",
    "input[autocomplete*=current-password]",
    "input[autocomplete*=new-password]",
    "input[autocomplete*=username]",
    "input[name*=token i]",
    "input[name*=code i]",
    "input[name*=otp i]",
    "input[name*=passcode i]",
    "input[name*=secret i]",
    "input[name*=password i]",
    "input[name*=email i]",
    "input[name*=username i]",
    "input[id*=otp i]",
    "input[id*=password i]",
    "textarea[name*=token i]",
    "textarea[name*=code i]",
    "textarea[name*=otp i]",
    "textarea[name*=passcode i]",
    "textarea[name*=secret i]",
    "textarea[name*=password i]",
    "textarea[name*=email i]",
    "textarea[name*=username i]",
    "textarea[autocomplete*=one-time-code]",
    "textarea[autocomplete*=current-password]",
    "textarea[autocomplete*=new-password]",
    "textarea[autocomplete*=username]",
    "[contenteditable][data-sensitive]",
    "[contenteditable][autocomplete*=one-time-code]",
    "[contenteditable][autocomplete*=current-password]",
    "[contenteditable][autocomplete*=new-password]",
    "[contenteditable][autocomplete*=username]",
    "[contenteditable][aria-label*=token i]",
    "[contenteditable][aria-label*=code i]",
    "[contenteditable][aria-label*=otp i]",
    "[contenteditable][aria-label*=secret i]",
    "[contenteditable][aria-label*=password i]",
    "[contenteditable][aria-label*=email i]",
    "[contenteditable][aria-label*=username i]",
)


def _sensitive_screenshot_style() -> str:
    """Build the masking stylesheet, including adapter-declared selectors."""
    extra = [s.strip() for s in
             os.environ.get("PORTAL_SENSITIVE_SELECTORS", "").split(",") if s.strip()]
    selectors = ",".join((*_SENSITIVE_SELECTORS, *extra))
    return (f"{selectors}{{color:transparent!important;"
            "background:#777!important;text-shadow:none!important;"
            "caret-color:transparent!important}")


# Import-time snapshot kept for callers/tests that reference the constant.
_SENSITIVE_SCREENSHOT_STYLE = _sensitive_screenshot_style()
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

class _RunState(dict[str, Any]):
    """Policy/extraction state; page/context ownership stays in the supervisor."""

    def __init__(self, run_id: str, **values: Any) -> None:
        super().__init__(run_id=run_id, **values)
        self.run_id = run_id

    def __getitem__(self, key: str) -> Any:
        if key in {"page", "context"}:
            lease = browser_runtime.lease_for_run(self.run_id)
            if lease is None:
                raise KeyError(key)
            return getattr(lease, key)
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in {"page", "context"}:
            lease = browser_runtime.lease_for_run(self.run_id)
            return getattr(lease, key, default) if lease is not None else default
        return super().get(key, default)


_browse_contexts: dict[str, _RunState] = {}
_browse_locks: dict[str, asyncio.Lock] = {}
_close_locks: dict[str, asyncio.Lock] = {}
_frame_locks: dict[str, asyncio.Lock] = {}
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
        result["reason"] = reason
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
    """Compatibility accessor; process ownership lives in BrowserRuntime."""
    return await browser_runtime.get_browser()


async def _popup_blocked(lease: ContextLease, page: Any) -> None:
    if not lease.run_id:
        return
    await firestore.audit(
        "system:browser_runtime",
        "browser_popup",
        f"browser_runs/{lease.run_id}",
        "refused",
        json.dumps({"url": redact_url(getattr(page, "url", ""))}),
    )
    state = _browse_contexts.get(lease.run_id)
    if state is not None and browser_runtime.is_current_lease(lease):
        state["policy_error"] = _error(
            "popup_blocked" if lease.kind == "browse" else "policy_refused",
            "a popup or SSO handoff was blocked",
            reason="popup_or_sso_required",
        )
    await _freeze_lease_for_policy(lease, "popup_or_sso_required")


async def _freeze_lease_for_policy(
    lease: ContextLease, reason: str, *, mark_actions: bool = True
) -> None:
    """Move a consequential watchdog refusal through the legal state graph."""
    if not lease.run_id or not browser_runtime.is_current_lease(lease):
        return
    run = await firestore.get_browser_run(lease.run_id)
    if not run or run.get("status") in {"blocked", "stopping", "closed"}:
        return
    if run.get("status") == "opening":
        await capture_frame(lease.run_id, "blocked")
        await _transition_run(
            lease.run_id,
            "active",
            current_url=redact_url(getattr(lease.page, "url", "")),
            title=await lease.page.title() if lease.page else "",
        )
    if mark_actions:
        await firestore.mark_prepared_browser_actions_uncertain(lease.run_id, reason)
    await _transition_run(
        lease.run_id, "blocked", blocked_reason=reason
    )


async def _dialog_blocked(
    lease: ContextLease,
    dialog: Any,
    event: PolicyEventContext | None = None,
) -> None:
    dialog_type = str(getattr(dialog, "type", "unknown"))
    message = str(getattr(dialog, "message", ""))
    with contextlib.suppress(Exception):
        await dialog.dismiss()
    if not lease.run_id:
        return
    await firestore.audit(
        "system:browser_runtime",
        "browser_dialog",
        f"browser_runs/{lease.run_id}",
        "refused" if dialog_type != "alert" else "success",
        json.dumps(
            {
                "type": dialog_type,
                "message_length": len(message),
                "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
            }
        ),
    )
    if dialog_type != "alert":
        state = _browse_contexts.get(lease.run_id)
        if state is not None and browser_runtime.is_current_lease(lease):
            state["policy_error"] = _error(
                "dialog_blocked",
                f"{dialog_type} dialog was dismissed; action outcome is uncertain",
                reason="A consequential browser dialog requires the founder",
            )
        # If SUCCEEDED already won the in-memory once-gate, this is a late
        # dialog: still freeze the page for the next action, but never rewrite
        # the completed action's ledger result. Otherwise the dialog won and
        # PREPARED is terminalized UNCERTAIN exactly once.
        mark_actions = event is None or event.action_id is None or event.claimed
        await _freeze_lease_for_policy(
            lease, f"dialog:{dialog_type}", mark_actions=mark_actions)


async def _download_blocked(
    lease: ContextLease,
    _download: Any,
    event: PolicyEventContext | None = None,
) -> None:
    if not lease.run_id:
        return
    state = _browse_contexts.get(lease.run_id)
    if state is not None and browser_runtime.is_current_lease(lease):
        state["policy_error"] = _error(
            "policy_refused", "downloads are disabled by browser policy"
        )
    await firestore.audit(
        "system:browser_runtime",
        "browser_download",
        f"browser_runs/{lease.run_id}",
        "refused",
        "download disabled",
    )


def _normalize_host(host: str) -> str:
    """Lower-case, strip the root-zone trailing dot, and punycode IDNs.

    Without this, `Portal.example.` and the unicode spelling of an IDN both
    miss an ASCII allowlist entry that should have matched (or, worse, a
    lookalike passes a naive comparison).
    """
    value = (host or "").strip().lower().rstrip(".")
    if not value:
        return ""
    try:
        return value.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return value


class _HostAllowlist:
    """Exact hosts and explicit `*.suffix` wildcards, kept distinct.

    Treating every entry as a suffix wildcard (the earlier bug) silently
    authorized every subdomain: allowing `portal.example` also allowed
    `collector.portal.example`, which is exactly the dangling/attacker-owned
    host a hostile portal page would beacon a typed credential to.
    """

    __slots__ = ("exact", "wildcards")

    def __init__(self, exact: set[str] | None = None,
                 wildcards: set[str] | None = None) -> None:
        self.exact = {_normalize_host(h) for h in (exact or set()) if h}
        self.wildcards = {_normalize_host(h) for h in (wildcards or set()) if h}

    def allows(self, host: str) -> bool:
        value = _normalize_host(host)
        if not value:
            return False
        if value in self.exact:
            return True
        # A wildcard authorizes strict subdomains AND its own apex.
        return any(value == item or value.endswith("." + item)
                   for item in self.wildcards)

    def __bool__(self) -> bool:
        return bool(self.exact or self.wildcards)


def _host_allowed(host: str, allowed: "_HostAllowlist") -> bool:
    return allowed.allows(host)


def _credentialed_allowed_hosts(expected_host: str) -> _HostAllowlist:
    """Origins a credential-typing context may reach (docs/22 per-kind matrix).

    The portal itself (EXACT — a portal host never implicitly authorizes its
    subdomains), plus adapter-declared identity-provider/verification origins
    from PORTAL_ALLOWED_HOSTS. An entry is exact unless it is written with a
    leading `*.`, which declares a subdomain wildcard. Config-declared, never
    page-derived: letting the page nominate its own allowed origins would
    defeat the fence.
    """
    exact = {expected_host} if expected_host else set()
    wildcards: set[str] = set()
    for item in os.environ.get("PORTAL_ALLOWED_HOSTS", "").split(","):
        entry = item.strip().lower()
        if not entry:
            continue
        if entry.startswith("*."):
            wildcards.add(entry[2:])
        else:
            exact.add(entry)
    if not os.environ.get("K_SERVICE"):
        exact.update({"127.0.0.1", "localhost"})
    return _HostAllowlist(exact, wildcards)


def _credentialed_request_refused(
    method: str,
    request_host: str,
    expected_host: str,
    allowed_hosts: _HostAllowlist,
) -> bool:
    """Return whether interception must deny one credentialed-context request.

    Configured identity/verification origins may receive read-only requests and
    top-level GET/HEAD redirects. Cross-origin writes remain forbidden: portal
    credentials are never posted to an IdP merely because its hostname was
    allowlisted. The explicit host allowlist still governs every subresource,
    beacon, and navigation.
    """
    verb = method.upper()
    host = _normalize_host(request_host)
    portal = _normalize_host(expected_host)
    unsafe_method = verb not in {"GET", "HEAD", "POST", "OPTIONS"}
    cross_origin_write = verb in {"POST", "OPTIONS"} and host != portal
    return unsafe_method or cross_origin_write or not allowed_hosts.allows(host)


async def _credentialed_context(
    target_url: str,
    *,
    run_id: str,
    session_key: dict[str, str],
    application_id: str,
    phase: str,
) -> ContextLease:
    """Browser context for the CREDENTIALED paths (register / open_and_login /
    verify link).

    These type portal passwords into whatever page they land on, so the
    connection must reach the IP that was validated — not one a second DNS
    lookup returns. `_validate_portal_target` checks the URL up front, but a
    direct context re-resolves at navigation time, leaving a DNS-rebind / TOCTOU
    window. Route public targets through the DNS-validating proxy, which
    resolves once and dials the validated IP (over HTTPS the CONNECT tunnel
    carries the login POST). Explicit loopback contract fixtures in local dev
    keep a direct context because the public proxy correctly refuses them."""
    host = (urlsplit(target_url).hostname or "").lower()
    options: dict[str, Any] = {}
    if os.environ.get("K_SERVICE") or host not in ("127.0.0.1", "localhost"):
        proxy = await _get_proxy(public=True)
        options["proxy"] = {"server": f"http://127.0.0.1:{proxy.port}"}
    identity = (
        str(session_key.get("app_name") or "co_founder"),
        str(session_key.get("user_id") or ""),
        str(session_key.get("session_id") or ""),
    )
    expected_host = (urlsplit(target_url).hostname or "").lower()

    allowed_hosts = _credentialed_allowed_hosts(expected_host)

    async def portal_guard(route, request):
        parts, _message = _url_parts(request.url)
        method = request.method.upper()
        request_host = (parts.hostname or "").lower() if parts else ""
        unsafe = parts is None or _credentialed_request_refused(
            method, request_host, expected_host, allowed_hosts)
        # docs/22 per-kind matrix: credentialed contexts reach only the portal
        # and adapter-declared identity/verification origins. Without this, a
        # hostile portal page could read the password Alex just typed and
        # exfiltrate it with a plain cross-origin GET beacon (<img src=...>),
        # which the method/navigation checks above all permit.
        if unsafe:
            await route.abort("blockedbyclient")
            return
        if not (
            not os.environ.get("K_SERVICE")
            and request_host in {"127.0.0.1", "localhost"}
        ):
            refusal = await validate_public_url(request.url)
            if refusal:
                await route.abort("blockedbyclient")
                return
        await route.continue_()

    return await browser_runtime.acquire_context(
        kind="fill",
        run_id=run_id,
        session_key=identity,
        application_id=application_id,
        phase=phase,
        context_options=options,
        route_handler=portal_guard,
        on_popup=_popup_blocked,
        on_dialog=_dialog_blocked,
        on_download=_download_blocked,
    )


def page_signature(fields: list[dict[str, Any]] | list[str]) -> str:
    """The staleness/portal-state signature (docs/09, 22).

    Covers name AND type, label, and required-state. Hashing names alone let a
    portal change a field's MEANING — a text box becoming a file upload, an
    optional field becoming mandatory, a relabelled question — without moving
    the signature, so the staleness fence passed and, worse, a founder's submit
    approval bound to that signature still matched a materially different form.

    Accepts a plain name list for legacy callers; those keep the weaker,
    name-only signature by construction.
    """
    parts: list[str] = []
    for field in fields:
        if isinstance(field, str):
            parts.append(field)
            continue
        parts.append("\x1f".join((
            str(field.get("name", "")),
            str(field.get("type", "")),
            " ".join(str(field.get("label", "")).split()).lower(),
            "req" if field.get("required") else "opt",
        )))
    return (
        "sha256:"
        + hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()[:16]
    )


def _page_policy_error(page: Any) -> dict[str, Any] | None:
    for state in _browse_contexts.values():
        if state.get("page") is page and state.get("policy_error"):
            return state["policy_error"]
    return None


async def render_text(url: str) -> str | None:
    """SSRF-guarded JS-shell fallback for the scout (docs/08)."""
    if await validate_public_url(url):
        return None
    proxy = await _get_proxy(public=True)
    lease: ContextLease | None = None
    policy_state = {"run_id": "discovery-render", "policy_error": None}
    # docs/22 per-kind matrix: render is public-read but scoped to the source
    # being rendered. Subresources may load from that host (and its subdomains);
    # anything else is refused, so a hostile discovery page cannot use the
    # renderer as a general-purpose fetcher.
    render_host = (urlsplit(url).hostname or "").lower()
    # Wildcard on the source's own domain: a read-only render legitimately
    # pulls scripts/styles from sibling subdomains, and this context types no
    # credentials, so the risk profile differs from the credentialed fence.
    # Everything off that domain is still refused.
    render_hosts = _HostAllowlist(wildcards={render_host} if render_host else set())

    async def guard(route, request):
        request_parts, _message = _url_parts(request.url)
        request_host = (request_parts.hostname or "").lower() if request_parts else ""
        if not _host_allowed(request_host, render_hosts):
            await route.abort("blockedbyclient")
            return
        await _request_guard(
            route, request, policy_state, enforce_domain_policy=False
        )

    try:
        lease = await browser_runtime.acquire_context(
            kind="render",
            run_id=None,
            session_key=None,
            context_options={"proxy": {"server": f"http://127.0.0.1:{proxy.port}"}},
            route_handler=guard,
        )
        await lease.page.goto(url, timeout=30000, wait_until="networkidle")
        if policy_state.get("policy_error"):
            return None
        return (await lease.page.inner_text("body"))[:MAX_PAGE_TEXT]
    except Exception:
        return None
    finally:
        if lease is not None:
            await browser_runtime.close_context_lease(lease)


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
        "signature": page_signature(fields),
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
                    await page.set_input_files(
                        f"[name='{name}']", attachments[name], timeout=10_000
                    )
                    filled.append(name)
                else:
                    needs_human.append(
                        {"field": name, "reason": "file upload — choose the file"}
                    )
            elif field_type == "select":
                await page.select_option(
                    f"[name='{name}']", label=value, timeout=10_000
                )
                filled.append(name)
            elif field_type in ("checkbox", "radio"):
                needs_human.append(
                    {"field": name, "reason": f"{field_type} needs founder judgment"}
                )
            else:
                await page.fill(f"[name='{name}']", value, timeout=10_000)
                filled.append(name)
        except Exception:
            needs_human.append({"field": name, "reason": "not found or not fillable"})
        policy_error = _page_policy_error(page)
        if policy_error:
            return policy_error
    return {
        "status": "success",
        "filled": len(filled),
        "filled_fields": filled,
        "needs_human": needs_human,
    }


async def screenshot(page, path: str) -> str:
    """Full-page evidence capture with sensitive fields masked, mirrored durably.

    Fill/recon shots land on the application-artifact lifecycle (not the 7-day
    frame expiry) and become the RunView preview, so they must carry the same
    redaction as live frames — an adapter-declared sensitive field on a portal
    form would otherwise be captured in the clear.

    The bytes go through `storage.save_bytes`, which mirrors to GCS. Writing
    Playwright's output straight to a local path left the durable fill report
    referencing an artifact that vanished with the instance's filesystem.
    """
    shot = await asyncio.wait_for(
        page.screenshot(full_page=True, style=_sensitive_screenshot_style()),
        timeout=5,
    )
    name = os.path.basename(path)
    await asyncio.to_thread(storage.save_bytes, name, shot)
    return path


async def _scoped_confirmation(page) -> str | None:
    """Read a portal's dedicated confirmation element, if the page is a receipt.

    Anchoring on an explicit receipt element, rather than scanning the whole
    body, keeps ordinary hyphenated tokens ("ISO-8601", "REF-2026") from being
    misread as submit confirmations.
    """
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
                await page.click(submit_selector, timeout=10_000)
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
            policy_error = _page_policy_error(page)
            if policy_error:
                return policy_error
            # Give a slow receipt time to render before concluding failure, so a
            # successful-but-slow POST is reconciled here rather than reported as
            # a failure the caller has to retry.
            try:
                await page.wait_for_selector(
                    ".receipt .id, [data-confirmation-id], .confirmation-id",
                    timeout=10000)
            except Exception:
                pass
            policy_error = _page_policy_error(page)
            if policy_error:
                return policy_error
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
    tables. Loopback is exempt only for explicit local development fixtures."""
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


async def _start_fill_context(
    portal_url: str,
    *,
    session_key: dict[str, str],
    application_id: str,
    goal: str,
    phase: str,
) -> tuple[str, _RunState, ContextLease]:
    """Create/project a durable fill run before credentialed page creation."""
    app_name = str(session_key.get("app_name") or "co_founder")
    user_id = str(session_key.get("user_id") or "")
    session_id = str(session_key.get("session_id") or "")
    if not user_id or not session_id or not application_id:
        raise ValueError("fill browser work requires session and application identity")
    identity = {"app_name": app_name, "user_id": user_id, "session_id": session_id}
    browse = await current_run_for_session(identity, "browse")
    if browse:
        await close_run(
            browse["run_id"], "superseded_by_fill", "agent:form_filler"
        )
    prior_fill = await current_run_for_session(identity, "fill")
    if prior_fill:
        await close_run(prior_fill["run_id"], "superseded", "agent:form_filler")

    run_id = uuid.uuid4().hex
    now = _now()
    await firestore.create_browser_run(
        {
            "run_id": run_id,
            "app_name": app_name,
            "user_id": user_id,
            "session_id": session_id,
            "kind": "fill",
            "application_id": application_id,
            "phase": phase,
            "goal": " ".join(goal.split())[:200],
            "status": "opening",
            "close_reason": None,
            "current_url": redact_url(portal_url),
            "title": None,
            "last_action": None,
            "screenshot_artifact": None,
            "action_count": 0,
            "started_at": now.isoformat(),
            "deadline_at": (now + timedelta(minutes=30)).isoformat(),
            "version": 1,
            "frame_seq": 0,
            "browser_generation": browser_runtime.generation,
            "owner_instance": INSTANCE_ID,
            "lease_generation": 1,
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
            "blocked_reason": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    )
    await _register_browser_resource(
        founder_id=user_id, session_id=session_id, run_id=run_id, kind="fill",
        goal=goal, application_id=application_id)
    created = await firestore.get_browser_run(run_id)
    if created:
        await _publish_browser_event(
            created, "browser.started", run=_run_view(created), version=1
        )
        scheduled = await _schedule_run_expiry(created)
        if scheduled.get("status") != "success":
            # Fail closed: a fill context that no durable task can reclaim would
            # hold credentialed browser ownership until this instance restarts.
            await close_run(run_id, "error", "system:browser")
            raise BrowserRuntimeUnavailable(
                "durable browser expiry could not be scheduled",
                reason="expiry_unscheduled",
            )
    state = _RunState(
        run_id,
        dom_hash="",
        text="",
        links=[],
        artifact=None,
        injection_suspected=False,
        policy_error=None,
        bot_captured=False,
        signature=None,
    )
    _browse_contexts[run_id] = state
    try:
        lease = await _credentialed_context(
            portal_url,
            run_id=run_id,
            session_key=identity,
            application_id=application_id,
            phase=phase,
        )
        await firestore.update_browser_run(
            run_id, browser_generation=lease.browser_generation
        )
        return run_id, state, lease
    except BaseException:
        _browse_contexts.pop(run_id, None)
        await _transition_run(
            run_id, "closed", "browser.closed", owner_loss=True,
            close_reason="error"
        )
        raise


async def _activate_fill_run(run_id: str, phase: str) -> str | None:
    state = _browse_contexts.get(run_id)
    if state is None:
        return None
    artifact = await save_pageshot(run_id, 0, "nav")
    await browser_runtime.set_phase(run_id, phase)
    promoted = await _transition_run(
        run_id, "active",
        phase=phase,
        current_url=redact_url(state["page"].url),
        title=await state["page"].title(),
    )
    if not promoted.get("ok"):
        # Closed underneath the bootstrap: release the context rather than
        # returning a live portal page attached to a terminal run.
        await close_run(run_id, "superseded", "system:browser")
        return None
    await renew_run_expiry(run_id)
    return artifact


async def register(
    portal_url: str,
    email: str,
    password: str,
    *,
    session_key: dict[str, str],
    application_id: str,
) -> dict:
    """Open a portal's signup page and create an account (docs/17).

    Heuristic, email+password only: finds the email + password fields on a
    signup/register page and submits. SSO-only or bot-challenged pages return
    blockers as data — the agent never improvises around them."""
    refusal = await _validate_portal_target(portal_url)
    if refusal:
        return refusal
    try:
        run_id, _state, lease = await _start_fill_context(
            portal_url,
            session_key=session_key,
            application_id=application_id,
            goal=f"register portal account for application {application_id}",
            phase="authenticating",
        )
    except BrowserCapacityExceeded as exc:
        return _error("capacity_exceeded", str(exc))
    except BrowserForegroundBusy as exc:
        return _error("browser_busy", str(exc))
    except BrowserRuntimeUnavailable as exc:
        return _error(
            "browser_unavailable",
            "browser runtime unavailable",
            reason=exc.reason,
        )
    except Exception as exc:
        return _error(
            "browser_unavailable", f"browser setup failed: {exc}"[:300],
            reason="launch_failed"
        )
    context, page = lease.context, lease.page
    try:
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
            await close_run(run_id, "bot_challenge", "agent:form_filler")
            return {
                "status": "blocked",
                "error": True,
                "message": "bot protection detected on signup — screenshot and hand "
                "to the founder; Alex never solves CAPTCHAs (docs/17)",
            }
        if not found:
            await close_run(run_id, "error", "agent:form_filler")
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
            await close_run(run_id, "error", "agent:form_filler")
            return {"status": "blocked", "error": True,
                    "message": f"signup page is on {landed_host!r}, not the portal "
                               f"{expected_host!r} — credentials withheld; hand this "
                               "portal to the founder"}
        email_sel = "input[type='email'], [name='email'], [name='username']"
        await page.fill(email_sel, email, timeout=10_000)
        pw_fields = await page.query_selector_all("input[type='password']")
        await pw_fields[0].fill(password, timeout=10_000)
        if len(pw_fields) > 1:  # confirm-password field
            await pw_fields[1].fill(password, timeout=10_000)
        await page.click(
            "button[type='submit'], input[type='submit']", timeout=10_000
        )
        await page.wait_for_load_state("networkidle", timeout=15000)
        policy_error = _page_policy_error(page)
        if policy_error:
            return policy_error
        body = (await page.inner_text("body"))[:800]
        artifact = await _activate_fill_run(run_id, "verifying")
        return {
            "status": "success",
            "run_id": run_id,
            "context": context,
            "page": page,
            "body": body,
            "screenshot_artifact": artifact,
        }
    except Exception as exc:
        await close_run(run_id, "error", "agent:form_filler")
        return {
            "status": "error",
            "error": True,
            "message": f"registration failed: {exc}",
        }


async def verify_registration_link(
    url: str,
    expected_host: str,
    *,
    session_key: dict[str, str],
    application_id: str,
) -> dict:
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
    try:
        run_id, _state, lease = await _start_fill_context(
            url,
            session_key=session_key,
            application_id=application_id,
            goal=f"verify portal account for application {application_id}",
            phase="verifying",
        )
    except BrowserCapacityExceeded as exc:
        return _error("capacity_exceeded", str(exc))
    except BrowserForegroundBusy as exc:
        return _error("browser_busy", str(exc))
    except BrowserRuntimeUnavailable as exc:
        return _error(
            "browser_unavailable", "browser runtime unavailable",
            reason=exc.reason,
        )
    context, page = lease.context, lease.page
    try:
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
        await _activate_fill_run(run_id, "verifying")
        body = (await page.inner_text("body"))[:800]
        if any(word in body.lower() for word in ("not valid", "expired", "error")):
            return _error("verification_failed", "portal rejected the verification link")
        return {"status": "success", "verified": True}
    except Exception as exc:
        return _error("verification_failed", f"verification failed: {exc}"[:300])
    finally:
        await close_run(run_id, "agent_close", "agent:form_filler")


async def open_and_login(
    portal_url: str,
    username: str,
    password: str,
    *,
    session_key: dict[str, str],
    application_id: str,
) -> dict:
    """Open the portal and log in. Returns the live page on success."""
    refusal = await _validate_portal_target(portal_url)
    if refusal:
        return refusal
    expected_host = (urlsplit(portal_url).hostname or "").lower()
    try:
        run_id, _state, lease = await _start_fill_context(
            portal_url,
            session_key=session_key,
            application_id=application_id,
            goal=f"fill application {application_id}",
            phase="authenticating",
        )
    except BrowserCapacityExceeded as exc:
        return _error("capacity_exceeded", str(exc))
    except BrowserForegroundBusy as exc:
        return _error("browser_busy", str(exc))
    except BrowserRuntimeUnavailable as exc:
        return _error(
            "browser_unavailable", "browser runtime unavailable",
            reason=exc.reason,
        )
    except Exception as exc:
        return _error(
            "browser_unavailable", f"browser setup failed: {exc}"[:300],
            reason="launch_failed"
        )
    context, page = lease.context, lease.page
    try:
        await page.goto(portal_url, timeout=30000)
        if not await page.query_selector("input[type='password']"):
            # landing page isn't the login page — try the conventional path
            await page.goto(portal_url.rstrip("/") + "/login", timeout=15000)
        if await page.query_selector("input[type='password']"):
            # Host pin: redirects (SSO bounce, open redirect) must never end
            # with the portal credential typed into a different origin.
            landed_host = (urlsplit(page.url).hostname or "").lower()
            if landed_host != expected_host:
                await close_run(run_id, "error", "agent:form_filler")
                return {"status": "blocked", "error": True,
                        "message": f"login page is on {landed_host!r}, not the "
                                   f"portal {expected_host!r} — credentials "
                                   "withheld; hand this portal to the founder"}
            await page.fill(
                "[name='username'], [name='email']", username, timeout=10_000
            )
            await page.fill("[name='password']", password, timeout=10_000)
            await page.click("button[type='submit']", timeout=10_000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.goto(
                portal_url, timeout=30000
            )  # login redirects home; go to the form
            await page.wait_for_load_state("networkidle", timeout=15000)
        policy_error = _page_policy_error(page)
        if policy_error:
            return policy_error
        artifact = await _activate_fill_run(run_id, "filling")
        return {
            "status": "success",
            "run_id": run_id,
            "context": context,
            "page": page,
            "title": await page.title(),
            "screenshot_artifact": artifact,
        }
    except Exception as exc:
        await close_run(run_id, "error", "agent:form_filler")
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


async def _new_browse_context(
    run_id: str, session_key: dict[str, str]
) -> dict[str, Any]:
    proxy = await _get_proxy()
    runtime = _RunState(
        run_id,
        dom_hash="",
        text="",
        links=[],
        artifact=None,
        injection_suspected=False,
        policy_error=None,
        bot_captured=False,
    )
    _browse_contexts[run_id] = runtime

    async def guard(route, request):
        await _request_guard(route, request, runtime)

    identity = (
        str(session_key.get("app_name") or "co_founder"),
        str(session_key.get("user_id") or ""),
        str(session_key.get("session_id") or ""),
    )
    try:
        await browser_runtime.acquire_context(
            kind="browse",
            run_id=run_id,
            session_key=identity,
            context_options={"proxy": {"server": f"http://127.0.0.1:{proxy.port}"}},
            route_handler=guard,
            on_popup=_popup_blocked,
            on_dialog=_dialog_blocked,
            on_download=_download_blocked,
        )
        return runtime
    except BaseException:
        _browse_contexts.pop(run_id, None)
        raise


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


def _event_session_key(run: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(run.get("app_name") or "co_founder"),
        str(run.get("user_id") or ""),
        str(run.get("session_id") or ""),
    )


async def _publish_browser_event(
    record: dict[str, Any], event_type: str, **payload: Any
) -> None:
    event = {
        "type": event_type,
        "run_id": record.get("run_id"),
        "version": int(payload.get("version", record.get("version", 0))),
        **payload,
    }
    await browser_event_hub.publish(
        _event_session_key(record),
        event,
    )
    # The worker and public API are separate processes in production. Publish
    # a content-free durable notification so the workspace SSE can trigger an
    # authoritative browser snapshot; the process-local hub remains the fast
    # path for local development and is never required for correctness.
    if os.environ.get("K_SERVICE"):
        from services.projection_stream import publish_best_effort

        frame = payload.get("frame") or {}
        await publish_best_effort(
            workspace_id=str(record.get("user_id") or ""),
            projection_type="browser",
            aggregate_id=str(record.get("run_id") or ""),
            aggregate_version=int(event["version"]),
            run_id=str(record.get("run_id") or ""),
            safe_payload={"event_type": event_type,
                          "status": str(record.get("status") or "")[:40],
                          "frame_seq": int(frame.get("seq") or 0)},
            idempotency_key=(f"browser:{record.get('run_id')}:{event_type}:"
                             f"{event['version']}:{int(frame.get('seq') or 0)}"))


async def _mutate_run_view(run_id: str, **fields: Any) -> dict[str, Any]:
    """Commit a UI-visible field change (version++) and publish the new view.

    docs/22 requires `version` to increment on every UI-visible mutation. These
    fields previously used the non-bumping accessor, so the durable row could
    stay permanently newer than the last published version — the final
    `last_action` of a run was never delivered live at all.
    """
    result = await firestore.mutate_browser_run_view(run_id, **fields)
    if result.get("ok"):
        await _publish_browser_event(
            result,
            "browser.status",
            run=_run_view(result),
            status=result.get("status"),
            version=int(result.get("version", 0)),
        )
    return result


async def _transition_run(
    run_id: str,
    status: str,
    event_type: str = "browser.status",
    *,
    owner_loss: bool = False,
    **fields: Any,
) -> dict[str, Any]:
    """Commit a legal lifecycle edge before emitting its ephemeral hint."""
    result = await firestore.transition_browser_run(
        run_id, status, owner_loss=owner_loss, **fields
    )
    if result.get("ok") and not result.get("idempotent"):
        await _publish_browser_event(
            result,
            event_type,
            run=_run_view(result),
            status=result.get("status"),
            version=int(result.get("version", 0)),
        )
    elif not result.get("ok"):
        # Only genuinely ILLEGAL edges are audited as refused. An idempotent
        # no-op (active→active on a reused run) is normal and was polluting the
        # exact signal this row exists to make searchable.
        await firestore.audit(
            "system:browser",
            "browser_transition",
            f"browser_runs/{run_id}",
            "refused",
            json.dumps({"from": result.get("from"), "to": status}),
        )
    return result


async def _schedule_run_expiry(run: dict[str, Any]) -> dict[str, Any]:
    """Schedule durable expiry; the caller MUST honour a failure.

    Ignoring an enqueue error let a run go live with no durable expiry task.
    Startup reconciliation is not periodic and skips foreign runs whose lease
    has not lapsed, so such a run could hold a credentialed context until the
    next restart of its own instance — effectively forever on a warm service.
    """
    expires_at = str(run.get("expires_at") or "")
    if not expires_at:
        return {"status": "error", "error": True, "message": "run has no expiry"}
    result = await browser_expiry.schedule(
        str(run["run_id"]),
        int(run.get("lease_generation", 1)),
        expires_at,
        expire_run,
    )
    if isinstance(result, dict) and (
            result.get("error") or result.get("status") == "error"):
        browser_metrics.record("browser_expiry_schedule_failure")
        return result
    return {"status": "success"}


async def renew_run_expiry(run_id: str) -> dict[str, Any] | None:
    """Renew retention only after successful browser use; old tasks no-op."""
    run = await firestore.get_browser_run(run_id)
    if not run or run.get("status") not in {"opening", "active", "blocked"}:
        return None
    seconds = FILL_EXPIRY_SECONDS if run.get("kind") == "fill" else BROWSE_EXPIRY_SECONDS
    expires_at = (_now() + timedelta(seconds=seconds)).isoformat()
    # Enqueue BEFORE publishing the new generation. If enqueue fails, durable
    # state remains on the old generation and its existing Cloud Task remains
    # effective. Publishing first and rolling back creates an ABA hazard: an
    # ambiguously-created generation N+1 task could later match a reused N+1.
    prior_generation = int(run.get("lease_generation", 0))
    generation = prior_generation + 1
    refreshed = {**run, "lease_generation": generation, "expires_at": expires_at}
    scheduled = await _schedule_run_expiry(refreshed)
    if scheduled.get("status") != "success":
        await firestore.audit(
            "system:browser",
            "browser_expiry_renew",
            f"browser_runs/{run_id}",
            "error",
            "new expiry task enqueue failed; previous durable lease remains current",
        )
        return {**run, "expiry_renewed": False, "expiry_schedule_error": True}
    # CAS after enqueue. A concurrent winner makes this task stale (or shares
    # the same generation); expire_run also checks durable expires_at before
    # closing, so an earlier duplicate cannot shorten a newer lease.
    renewed = await firestore.renew_browser_lease(
        run_id,
        expires_at,
        expected_generation=prior_generation,
        lease_generation=generation,
    )
    if not renewed.get("ok"):
        return await firestore.get_browser_run(run_id)
    return {**refreshed, "expiry_renewed": True}


async def expire_run(run_id: str, lease_generation: int) -> dict:
    """Cloud Task/local timer handler; body identity is never trusted."""
    run = await firestore.get_browser_run(run_id)
    if not run or run.get("status") == "closed":
        return {"status": "success", "already_closed": True}
    if int(run.get("lease_generation", 0)) != int(lease_generation):
        return {"status": "success", "already_renewed": True}
    try:
        expires = datetime.fromisoformat(
            str(run.get("expires_at")).replace("Z", "+00:00"))
    except ValueError:  # unparseable/absent expiry is not a licence to close
        return {"status": "success", "not_due": True, "unparsed_expiry": True}
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires > _now():
        return {"status": "success", "not_due": True}
    # Re-verify the generation INSIDE the close lock: an action that renewed
    # between the checks above and the close would otherwise have its live,
    # just-renewed run closed as expired.
    result = await close_run(
        run_id, "expired", "system:browser_expiry",
        require_lease_generation=int(lease_generation),
    )
    if result.get("already_renewed"):
        return {"status": "success", "already_renewed": True}
    browser_metrics.record("browser_expiry_close", status=result.get("status"))
    result["error_code"] = "run_expired"
    return result


async def capture_frame(
    run_id: str,
    phase: str,
    action: dict[str, str] | None = None,
    *,
    artifact_override: str | None = None,
) -> str | None:
    """Upload a bounded JPEG (or reference a milestone), commit, then publish."""
    if phase not in {"nav", "before", "after", "blocked", "closed"}:
        raise ValueError("invalid browser frame phase")
    state = _browse_contexts.get(run_id)
    if state is None:
        raise RuntimeError("browser context is unavailable")
    lock = _frame_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        for _attempt in range(2):
            run = await firestore.get_browser_run(run_id)
            if not run:
                return None
            candidate = int(run.get("frame_seq", 0)) + 1
            artifact = f"browserframe_{run_id}_{candidate}.jpg"
            uploaded: str | None = artifact_override
            try:
                shot = None if artifact_override else await asyncio.wait_for(
                    state["page"].screenshot(
                        type="jpeg", quality=75, full_page=False,
                        style=_sensitive_screenshot_style(),
                    ),
                    timeout=5,
                )
                if shot is not None and len(shot) > 500 * 1024:
                    shot = await asyncio.wait_for(
                        state["page"].screenshot(
                            type="jpeg", quality=50, full_page=False,
                            style=_sensitive_screenshot_style(),
                        ),
                        timeout=5,
                    )
                if shot is not None and len(shot) <= 500 * 1024:
                    await asyncio.to_thread(storage.save_bytes, artifact, shot)
                    uploaded = artifact
            except Exception:
                uploaded = None
            try:
                frame_title = await asyncio.wait_for(state["page"].title(), timeout=5)
                frame_url = redact_url(getattr(state["page"], "url", ""))
            except Exception:
                frame_title = ""
                frame_url = str(run.get("current_url") or "")
            frame = await firestore.commit_browser_frame(
                run_id,
                candidate,
                {
                    "phase": phase,
                    "url": frame_url,
                    "title": frame_title,
                    "action": action,
                },
                uploaded,
            )
            if frame.get("conflict"):
                continue
            if frame.get("committed"):
                browser_metrics.record(
                    "browser_frame_commit", frame_seq=candidate, status=phase
                )
                state["artifact"] = uploaded
                refreshed = await firestore.get_browser_run(run_id) or run
                await _publish_browser_event(
                    refreshed, "browser.frame", frame=frame, version=frame["run_version"]
                )
                return uploaded
            return None
        return None


async def save_milestone_pageshot(run_id: str, seq: int, tag: str) -> str | None:
    """Persist a full-page PNG only for blocked/final evidence milestones."""
    if tag not in {"blocked", "final"}:
        raise ValueError("invalid milestone pageshot tag")
    state = _browse_contexts.get(run_id)
    if state is None:
        return None
    artifact = f"pageshot_{run_id}_{seq}_{tag}.png"
    try:
        shot = await asyncio.wait_for(
            state["page"].screenshot(
                full_page=True, style=_sensitive_screenshot_style()
            ),
            timeout=5,
        )
        await asyncio.to_thread(storage.save_bytes, artifact, shot)
        state["artifact"] = artifact
        await _mutate_run_view(run_id, screenshot_artifact=artifact)
        return artifact
    except Exception:
        return None


async def save_pageshot(run_id: str, seq: int, tag: str) -> str | None:
    """Compatibility adapter while callers migrate to frames/milestones."""
    if tag == "blocked":
        artifact = await save_milestone_pageshot(run_id, seq, "blocked")
        await capture_frame(run_id, "blocked", artifact_override=artifact)
        return artifact
    if tag in {"before", "after", "nav"}:
        return await capture_frame(run_id, tag)
    raise ValueError("invalid pageshot tag")


async def _extract_and_store(run_id: str, seq: int) -> dict:
    runtime = _browse_contexts[run_id]
    extracted = await extract_page_text(runtime["page"])
    artifact = f"page_{run_id}_{seq}.txt"
    # storage.save_text mirrors to GCS synchronously (blocking) — offload it.
    await asyncio.to_thread(storage.save_text, artifact, extracted["text"])
    runtime.update(extracted)
    runtime["text_artifact"] = artifact
    runtime["injection_suspected"] = scan_injection(extracted["text"])
    await _mutate_run_view(
        run_id,
        current_url=redact_url(runtime["page"].url),
        title=extracted["title"],
    )
    return {**extracted, "artifact": artifact}


async def _freeze_for_bot(run_id: str, seq: int) -> dict:
    runtime = _browse_contexts[run_id]
    run = await firestore.get_browser_run(run_id)
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
    if run and run.get("status") == "opening":
        await _transition_run(
            run_id, "active",
            current_url=redact_url(runtime["page"].url),
            title=await runtime["page"].title(),
        )
    # blocked is nonterminal: blocked_reason explains the freeze. Writing
    # close_reason here made a live run look closed in the RunView.
    await _transition_run(run_id, "blocked", blocked_reason="bot_challenge")
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
    fill_owner = await current_run_for_session(
        {"app_name": app_name, "user_id": user_id, "session_id": session_id},
        "fill",
    )
    if fill_owner:
        return _error(
            "browser_busy",
            "a form-fill run owns this session's Browser surface",
            active_run=_run_view(fill_owner),
        )
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
        await _transition_run(
            active["run_id"], "closed", "browser.closed", owner_loss=True,
            close_reason="restart"
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
                "status": "opening",
                "close_reason": None,
                "current_url": None,
                "title": None,
                "last_action": None,
                "screenshot_artifact": None,
                "action_count": 0,
                "version": 1,
                "frame_seq": 0,
                "browser_generation": browser_runtime.generation,
                "owner_instance": INSTANCE_ID,
                "lease_generation": 1,
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
                "blocked_reason": None,
                "started_at": now.isoformat(),
                "deadline_at": (
                    now + timedelta(seconds=BROWSE_TIME_BOX_SECONDS)
                ).isoformat(),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        )
        await _register_browser_resource(
            founder_id=user_id, session_id=session_id, run_id=run_id,
            kind="browse", goal=purpose)
        created = await firestore.get_browser_run(run_id)
        if created:
            await _publish_browser_event(
                created, "browser.started", run=_run_view(created), version=1
            )
            scheduled = await _schedule_run_expiry(created)
            if scheduled.get("status") != "success":
                await close_run(run_id, "error", "agent:co_founder")
                return _error(
                    "browser_unavailable",
                    "durable browser expiry could not be scheduled",
                    reason="expiry_unscheduled",
                )
    try:
        runtime = _browse_contexts.get(run_id) or await _new_browse_context(
            run_id,
            {"app_name": app_name, "user_id": user_id, "session_id": session_id},
        )
        lease = browser_runtime.lease_for_run(run_id)
        if lease:
            await firestore.update_browser_run(
                run_id, browser_generation=lease.browser_generation
            )
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
        promoted = await _transition_run(run_id, "active")
        if not promoted.get("ok"):
            # The run was closed underneath this open (founder stop, expiry, or
            # owner-loss reconciliation). Returning success here would hand back
            # a live page behind a closed run and leak its foreground lease.
            await close_run(run_id, "superseded", "system:browser")
            return _error("browser_unavailable",
                          "browser run was closed before it became active",
                          reason="disconnected")
        await renew_run_expiry(run_id)
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
    except BrowserForegroundBusy:
        await close_run(run_id, "error", "agent:co_founder")
        return _error("browser_busy", "the session already owns browser work")
    except BrowserCapacityExceeded:
        await close_run(run_id, "error", "agent:co_founder")
        return _error("capacity_exceeded", "browser context capacity is full")
    except BrowserRuntimeUnavailable as exc:
        await close_run(run_id, "error", "agent:co_founder")
        return _error(
            "browser_unavailable",
            "browser runtime is unavailable",
            reason=exc.reason,
        )
    except Exception as exc:
        await close_run(run_id, "error", "agent:co_founder")
        code = "timeout" if "Timeout" in type(exc).__name__ else "browser_unavailable"
        return _error(
            code, f"browser open failed: {str(exc)[:240]}",
            reason="disconnected" if code == "browser_unavailable" else None,
        )


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


def _policy_action_result(
    outcome: str, policy_error: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    """Translate the once-gate winner into one durable/returned result."""
    if outcome == "FAILED":
        return "FAILED", policy_error or _error(
            "policy_refused", "a browser download was blocked during this action"
        )
    return "UNCERTAIN", policy_error or _error(
        "dialog_blocked",
        "a consequential browser dialog interrupted this action; its outcome is uncertain",
        reason="A consequential browser event requires the founder",
    )


async def _commit_action_result(
    run_id: str, action_id: str, status: str, result: dict[str, Any]
) -> dict[str, Any]:
    """CAS PREPARED to one terminal result and return the actual winner."""
    committed = await firestore.update_browser_action(
        run_id, action_id, status=status, result_ref=result
    )
    if isinstance(committed, dict) and not committed.get("updated", True):
        winner = committed.get("status")
        if winner in {"SUCCEEDED", "FAILED", "UNCERTAIN"}:
            return committed.get("result_ref") or _error(
                "policy_refused",
                f"browser action completed as {winner.lower()} without result data",
            )
    return result


async def propose_and_act(run_id: str, invocation_id: str) -> dict:
    """Propose, validate, reserve, ledger, execute, and persist one research action."""
    lock = _browse_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        run = await firestore.get_browser_run(run_id)
        runtime = _browse_contexts.get(run_id)
        if not run or not runtime:
            return _error("no_active_run", "there is no active browser run")
        if run.get("status") == "blocked":
            return _error(
                "bot_challenge",
                "the browser run is frozen by bot protection",
                reason="Bot protection requires the founder",
            )
        if run.get("status") != "active":
            return _error(
                "no_active_run",
                f"browser run is {run.get('status')}; actions require active",
            )
        runtime["action_task"] = asyncio.current_task()
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
            shot = await asyncio.wait_for(
                runtime["page"].screenshot(full_page=True), timeout=5
            )
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
            if not browser_runtime.begin_action(run_id, action_id):
                result = _error(
                    "policy_refused",
                    "browser action could not acquire its completion gate",
                    reason="Action outcome is uncertain",
                )
                await _commit_action_result(
                    run_id, action_id, "UNCERTAIN", result)
                return result
            await save_pageshot(run_id, seq, "before")
            fresh = await extract_page_text(runtime["page"])
            if fresh["dom_hash"] != snapshot["dom_hash"]:
                result = _error(
                    "stale_page", "the page changed before the action could execute"
                )
                browser_runtime.claim_action_completion(
                    run_id, action_id, "FAILED")
                return await _commit_action_result(
                    run_id, action_id, "FAILED", result)
            runtime["policy_error"] = None
            result = await execute_action(
                runtime["page"], proposal, "research", run_id, invocation_id
            )
            if result.get("error"):
                _claimed, winner = browser_runtime.claim_action_completion(
                    run_id, action_id, "FAILED")
                if winner in {"FAILED", "UNCERTAIN"}:
                    status, result = _policy_action_result(
                        winner, runtime.get("policy_error") or result)
                else:
                    status = "FAILED"
                return await _commit_action_result(
                    run_id, action_id, status, result)
            await runtime["page"].wait_for_timeout(
                min(2000, int(os.environ.get("BROWSE_SETTLE_MS", "800")))
            )
            completion = browser_runtime.action_completion(run_id, action_id)
            if completion in {"FAILED", "UNCERTAIN"}:
                status, result = _policy_action_result(
                    completion, runtime.get("policy_error"))
                return await _commit_action_result(
                    run_id, action_id, status, result)
            if runtime.get("policy_error"):
                # Popup and navigation guards are not Page dialog/download
                # events, but still complete through the same gate.
                _claimed, winner = browser_runtime.claim_action_completion(
                    run_id, action_id, "FAILED")
                status, result = _policy_action_result(
                    winner or "FAILED", runtime["policy_error"])
                return await _commit_action_result(
                    run_id, action_id, status, result)
            if await detect_bot_challenge(runtime["page"]):
                result = await _freeze_for_bot(run_id, seq)
                browser_runtime.claim_action_completion(
                    run_id, action_id, "FAILED")
                return await _commit_action_result(
                    run_id, action_id, "FAILED", result)
            extracted = await _extract_and_store(run_id, seq)
            after = await save_pageshot(run_id, seq, "after")
            # Linearization point: no await between checking the winner and
            # claiming success. A Playwright event delivered before this line
            # wins FAILED/UNCERTAIN; one delivered after cannot rewrite success.
            _claimed, winner = browser_runtime.claim_action_completion(
                run_id, action_id, "SUCCEEDED")
            if winner != "SUCCEEDED":
                status, result = _policy_action_result(
                    winner or "UNCERTAIN", runtime.get("policy_error"))
                return await _commit_action_result(
                    run_id, action_id, status, result)
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
            # One bumping mutation: last_action is UI-visible, and the sliding
            # time-box (18 — the 90 s budget bounds action *activity*, not the
            # founder's reading time) rides along in the same commit.
            await _mutate_run_view(
                run_id,
                last_action=last_action,
                deadline_at=(
                    _now() + timedelta(seconds=BROWSE_TIME_BOX_SECONDS)
                ).isoformat(),
            )
            await renew_run_expiry(run_id)
            final = {
                "status": "success",
                "action": {"kind": proposal["action"], "target": str(label)[:180]},
                "url": redact_url(runtime["page"].url),
                "excerpt": extracted["text"][:600],
                "screenshot_artifact": after,
            }
            actual = await _commit_action_result(
                run_id, action_id, "SUCCEEDED", final)
            if actual is not final:
                return actual
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
            code = "timeout" if "Timeout" in type(exc).__name__ else "browser_unavailable"
            result = _error(
                code, f"browser action failed: {str(exc)[:240]}",
                reason="disconnected" if code == "browser_unavailable" else None,
            )
            if "action_id" in locals() and await firestore.get_browser_action(
                run_id, action_id
            ):
                _claimed, winner = browser_runtime.claim_action_completion(
                    run_id, action_id, "FAILED")
                status, result = _policy_action_result(
                    winner or "FAILED", runtime.get("policy_error") or result)
                return await _commit_action_result(
                    run_id, action_id, status, result)
            return result
        finally:
            browser_runtime.finish_action(run_id, action_id)
            if runtime.get("action_task") is asyncio.current_task():
                runtime.pop("action_task", None)


async def _commit_closed_frame(
    run_id: str, run: dict[str, Any], artifact: str | None
) -> dict[str, Any] | None:
    """Commit terminal metadata using retained evidence, never a dead page."""
    lock = _frame_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        for _attempt in range(2):
            current = await firestore.get_browser_run(run_id)
            if not current:
                return None
            candidate = int(current.get("frame_seq", 0)) + 1
            frame = await firestore.commit_browser_frame(
                run_id,
                candidate,
                {
                    "phase": "closed",
                    "url": current.get("current_url") or run.get("current_url") or "",
                    "title": current.get("title") or run.get("title") or "",
                    "action": None,
                },
                artifact or current.get("screenshot_artifact"),
                closing=True,   # the one frame allowed to land during stopping
            )
            if frame.get("conflict"):
                continue
            return frame if frame.get("committed") else None
    return None


async def close_run(run_id: str, reason: str, actor: str,
                    *, require_lease_generation: int | None = None) -> dict:
    """Reserve stopping, bound cleanup, terminalize, audit, then publish.

    `require_lease_generation` re-verifies the durable lease generation INSIDE
    the close lock (expiry): a renewal that landed after the caller's check
    must abort the close rather than terminate a live run.
    """
    lock = _close_locks.setdefault(run_id, asyncio.Lock())
    try:
        async with lock:
            run = await firestore.get_browser_run(run_id)
            lease = browser_runtime.lease_for_run(run_id)
            if not run:
                if lease:
                    await browser_runtime.close_lease(run_id)
                _browse_contexts.pop(run_id, None)
                return {"status": "success", "already_closed": True}
            if (require_lease_generation is not None
                    and int(run.get("lease_generation", 0)) != require_lease_generation):
                return {"status": "success", "already_renewed": True}
            if run.get("status") == "closed":
                browser_expiry.cancel(run_id)
                # A durable close can race an open that already registered a
                # lease. Releasing it here keeps the supervisor from holding a
                # foreground slot for a dead run, which would wedge every later
                # open in this session as browser_busy until process death.
                if lease:
                    await browser_runtime.close_lease(run_id)
                _browse_contexts.pop(run_id, None)
                return {"status": "success", "already_closed": True}
            if lease:
                lease.closing = True
            if run.get("status") != "stopping":
                transition = await _transition_run(run_id, "stopping")
                if not transition.get("ok"):
                    return _error(
                        "browser_unavailable", "browser run could not reserve stopping",
                        reason="disconnected",
                    )
                run = transition

            state = _browse_contexts.get(run_id)
            action_task = state.get("action_task") if state else None
            current_task = asyncio.current_task()
            if action_task and action_task is not current_task and not action_task.done():
                action_task.cancel()
                with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(action_task), timeout=2)
            await firestore.mark_prepared_browser_actions_uncertain(run_id, "stopped")

            final_artifact = None
            if state and state.get("page") is not None:
                seq = int(run.get("action_count", 0))
                final_artifact = await save_milestone_pageshot(run_id, seq, "final")
            if lease:
                await browser_runtime.close_lease(run_id)
            closed_frame = await _commit_closed_frame(run_id, run, final_artifact)
            if closed_frame:
                projected = await firestore.get_browser_run(run_id) or run
                await _publish_browser_event(
                    projected, "browser.frame", frame=closed_frame,
                    version=closed_frame.get("run_version", projected.get("version", 0))
                )
            terminal = await _transition_run(
                run_id, "closed", "browser.closed", close_reason=reason
            )
            browser_expiry.cancel(run_id)
            _browse_contexts.pop(run_id, None)
            _browse_locks.pop(run_id, None)
            _frame_locks.pop(run_id, None)
            action = "browse_stop" if reason == "founder_stop" else "browse_close"
            await firestore.audit(
                actor,
                action,
                f"browser_runs/{run_id}",
                "success",
                json.dumps({"url": run.get("current_url"), "reason": reason}),
            )
            browser_metrics.record("browser_stop", kind=run.get("kind"), reason=reason)
            return {
                "status": "success",
                "run_id": run_id,
                "kind": run.get("kind"),
                "already_closed": False,
                "version": terminal.get("version"),
                "frame_seq": (closed_frame or {}).get("seq"),
            }
    finally:
        if not lock.locked() and not getattr(lock, "_waiters", None):
            _close_locks.pop(run_id, None)


def _run_view(run: dict | None) -> dict | None:
    if not run:
        return None
    artifact = run.get("screenshot_artifact")
    status = str(run.get("status") or "closed")
    return {
        "active": status in {"opening", "active", "blocked", "stopping"},
        "kind": run.get("kind"),
        "run_id": run.get("run_id"),
        "application_id": run.get("application_id"),
        "phase": run.get("phase"),
        "url": run.get("current_url"),
        "title": run.get("title"),
        "goal": run.get("goal"),
        "screenshot_url": (
            f"/api/v1/artifacts/{artifact}/preview?session_id={run.get('session_id', '')}"
            if artifact else None),
        "screenshot_artifact": artifact,
        "last_action": run.get("last_action"),
        "status": status,
        "version": int(run.get("version", 0)),
        "frame_seq": int(run.get("frame_seq", 0)),
        "blocked_reason": run.get("blocked_reason"),
        "close_reason": run.get("close_reason"),
        "expires_at": run.get("expires_at"),
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


async def _register_browser_resource(*, founder_id: str, session_id: str,
                                     run_id: str, kind: str, goal: str,
                                     application_id: str = "") -> None:
    """Make a browser run findable from its conversation (docs/23 §6.1).

    Supporting visibility: browser evidence surfaces under its parent work,
    not as a primary search hit. Registration never fails a run — the browser
    is the founder-visible product here, the projection is navigation.
    """
    if not session_id or not founder_id:
        return
    try:
        from services import session_resources as sr

        await sr.register_session_resource(
            founder_id=founder_id, session_id=session_id,
            resource_type=sr.ResourceType.BROWSER_REPORT,
            canonical_id=run_id,
            relationship=sr.Relationship.PRODUCED,
            occurrence_key=f"browser_run:{run_id}",
            producer_kind="service", producer_id=f"browser:{kind}",
            producer_output_key="run",
            title=(goal or f"Browser {kind}")[:200],
            summary=f"Browser {kind} run",
            status="opening",
            visibility=sr.Visibility.SUPPORTING,
            parent_resource_id=(
                sr.resource_id_for(founder_id, sr.ResourceType.APPLICATION,
                                   "applications", application_id)
                if application_id else None),
            session_verified=True)
    except Exception:  # noqa: BLE001 — provenance never breaks browser work
        # This module has no module-level logger; referencing a bare `logger`
        # here would raise NameError *inside* the handler that exists to make
        # this failure harmless.
        import logging

        logging.getLogger(__name__).exception(
            "browser resource registration failed")

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
        (
            row
            for row in rows
            if row.get("status") in {"opening", "active", "blocked", "stopping"}
        ),
        None,
    )


async def stop_browser(
    session_key: dict, actor: str, run_id: str | None = None
) -> dict:
    """Idempotently stop an owned foreground browse or fill run."""
    rows = await firestore.list_browser_runs(
        str(session_key.get("app_name") or "co_founder"),
        str(session_key.get("user_id") or ""),
        str(session_key.get("session_id") or ""),
    )
    if run_id:
        selected = next((row for row in rows if row.get("run_id") == run_id), None)
        if selected is None:
            return _error("not_found", "browser run does not belong to this session")
    else:
        selected = next(
            (
                row
                for row in rows
                if row.get("status") in {"opening", "active", "blocked", "stopping"}
            ),
            None,
        )
    if not selected or selected.get("status") == "closed":
        return {"status": "success", "already_closed": True}
    return await close_run(selected["run_id"], "founder_stop", actor)


def fill_session_for_application(application_id: str) -> dict[str, Any] | None:
    """Return a transient view of a supervisor-owned fill lease.

    This is intentionally a lookup, not a second page registry.  The runtime's
    application index remains the sole owner used for replacement and cleanup.
    """
    lease = browser_runtime.lease_for_application(application_id)
    if lease is None or lease.closing or lease.kind != "fill" or lease.page is None:
        return None
    state = _browse_contexts.get(lease.run_id or "")
    if state is None:
        return None
    return {
        "run_id": lease.run_id,
        "page": lease.page,
        "context": lease.context,
        "signature": state.get("signature"),
        "phase": lease.phase,
    }


def set_fill_signature(application_id: str, signature: str | None) -> bool:
    """Attach a non-secret portal signature to the supervisor-owned run state."""
    lease = browser_runtime.lease_for_application(application_id)
    state = _browse_contexts.get(lease.run_id or "") if lease else None
    if state is None:
        return False
    state["signature"] = signature
    return True


async def set_fill_phase(application_id: str, phase: str) -> dict[str, Any]:
    """Project non-secret portal progress from the owning supervisor lease."""
    if phase not in {
        "authenticating", "verifying", "filling", "awaiting_approval", "submitting"
    }:
        return _error("policy_refused", "invalid fill phase")
    lease = browser_runtime.lease_for_application(application_id)
    if lease is None or not lease.run_id:
        return _error("no_active_run", "no open portal")
    await browser_runtime.set_phase(lease.run_id, phase)
    result = await firestore.mutate_browser_run_view(lease.run_id, phase=phase)
    if result.get("ok"):
        await _publish_browser_event(
            result, "browser.status", run=_run_view(result),
            status=result.get("status"), version=result.get("version", 0)
        )
    return {"status": "success", "run_id": lease.run_id, "phase": phase}


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
    changed = await firestore.mutate_browser_run_view(run_id, **fields)
    if changed.get("ok"):
        await _publish_browser_event(
            changed, "browser.status", run=_run_view(changed),
            status=changed.get("status"), version=changed.get("version", 0)
        )
    await capture_frame(
        run_id, "after", {"kind": kind, "target": target[:180]}
    )
    await renew_run_expiry(run_id)


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
    return _run_view(run) or {}


def _reclaimable_orphan(row: dict[str, Any], now: datetime) -> bool:
    """True only when no other process can still be driving this run.

    Three ways to prove it:

    1. It is stamped by THIS process and we hold no lease for it.
    2. It is stamped by this process's own Cloud Run revision. The service runs
       `--max-instances 1`, so one revision has at most one instance: a
       different process id under the same revision is a dead predecessor
       (this is what keeps "kill the server mid-run → restart → closed/restart"
       immediate, per docs/18).
    3. Its durable lease has lapsed. A live owner renews expiry on every
       successful action, so an expired lease means the owner is gone or long
       idle. This is the only proof available for a run belonging to ANOTHER
       revision, which during a rolling deploy may still be mid-fill.

    A pre-lease legacy row (no owner stamp and no expiry at all) predates this
    protocol; the migration backfills expiry for anything current, so such a row
    cannot belong to a live owner.
    """
    owner = str(row.get("owner_instance") or "")
    if owner == INSTANCE_ID:
        return True
    if owner and owner.split(":", 1)[0] == INSTANCE_ID.split(":", 1)[0]:
        return True
    raw_expiry = row.get("expires_at")
    if not owner and not raw_expiry:
        return True
    try:
        expires = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False   # a stamped owner with unreadable expiry is never assumed dead
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= now


async def reconcile_session(session_key: dict) -> dict:
    """Terminalize a nonterminal run whose supervised lease disappeared.

    Runs before every agent turn (callbacks.initialize_session_state), so it is
    the hottest reconciliation path — and it must honour the same instance fence
    as startup. Without it, an agent request landing on a NEW revision during a
    rolling deploy would close the OLD revision's live fill: its credentialed
    page keeps executing while the founder's panel reports `closed`.
    """
    rows = await firestore.list_browser_runs(
        str(session_key.get("app_name") or "co_founder"),
        str(session_key.get("user_id") or ""),
        str(session_key.get("session_id") or ""),
    )
    now = _now()
    for row in rows:
        if (
            row.get("status") in {"opening", "active", "blocked", "stopping"}
            and browser_runtime.lease_for_run(row["run_id"]) is None
            and _reclaimable_orphan(row, now)
        ):
            await _transition_run(
                row["run_id"], "closed", "browser.closed", owner_loss=True,
                close_reason="restart"
            )
    return await browser_status_projection(session_key)


async def reconcile_all_runs() -> None:
    """Terminalize runs this process owned but no longer holds a lease for.

    Scoped to INSTANCE_ID: a run stamped by another instance may still have a
    live context behind it (rolling deploy, overlapping revisions), and closing
    it here would leave credentialed work executing with the observation plane
    reporting `closed`. Those runs are reconciled by their own owner's restart,
    or by their durable expiry lease if that owner never comes back.
    """
    now = _now()
    for run in await firestore.list_nonterminal_browser_runs():
        run_id = run["run_id"]
        if browser_runtime.lease_for_run(run_id) is not None:
            continue  # this process still owns a live context for it
        if not _reclaimable_orphan(run, now):
            continue  # another instance may still be driving it
        await firestore.mark_prepared_browser_actions_uncertain(run_id, "restart")
        await _transition_run(
            run_id, "closed", "browser.closed", owner_loss=True,
            close_reason="restart"
        )


async def _reconcile_lost_leases(leases: list[ContextLease]) -> None:
    """Unexpected generation loss terminalizes each durable owner once."""
    for lease in leases:
        if not lease.run_id:
            continue
        run = await firestore.get_browser_run(lease.run_id)
        if not run or run.get("status") == "closed":
            continue
        _browse_contexts.pop(lease.run_id, None)
        _browse_locks.pop(lease.run_id, None)
        _frame_locks.pop(lease.run_id, None)
        browser_expiry.cancel(lease.run_id)
        await firestore.mark_prepared_browser_actions_uncertain(
            lease.run_id, "browser_crash"
        )
        await _transition_run(
            lease.run_id, "closed", "browser.closed", owner_loss=True,
            close_reason="browser_crash"
        )


SHUTDOWN_RUN_BUDGET_SECONDS = 2.0
SHUTDOWN_TOTAL_BUDGET_SECONDS = 5.5


async def _terminalize_for_shutdown(run_id: str) -> None:
    """Record the truth, then release — inside a per-run budget.

    Ordering matters: the durable `closed` write is what the founder's panel
    and the next instance read, so it happens FIRST and gets the budget. The
    context close is best-effort after that; `browser_runtime.shutdown()` and
    process exit reap anything still open.
    """
    async def _work() -> None:
        await firestore.mark_prepared_browser_actions_uncertain(run_id, "restart")
        await _transition_run(
            run_id, "closed", "browser.closed", owner_loss=True,
            close_reason="restart",
        )
        browser_expiry.cancel(run_id)
        _browse_contexts.pop(run_id, None)
        _browse_locks.pop(run_id, None)
        _frame_locks.pop(run_id, None)

    try:
        await asyncio.wait_for(_work(), timeout=SHUTDOWN_RUN_BUDGET_SECONDS)
    except Exception:  # noqa: BLE001 — best effort; restart reconciliation covers it
        import logging

        logging.getLogger(__name__).warning(
            "shutdown could not terminalize browser run %s", run_id)


async def shutdown() -> None:
    """Terminalize owned runs, then release process resources.

    docs/22 requires shutdown to reconcile every owned durable run. Closing
    contexts alone left runs `active` in Firestore until some later instance
    started — with --min-instances 0 the founder could watch a "live" run with
    no browser behind it for an unbounded time. Best-effort: a SIGKILL still
    falls back to expiry-fenced restart reconciliation.
    """
    global _proxy, _public_proxy
    # Shutdown-specific protocol, deliberately NOT close_run(): that path can
    # spend 2 s cancelling an action + 5 s on final evidence + 5 s closing the
    # context, PER RUN, sequentially — far past the platform's grace window.
    # Here ownership is terminalized first (durable truth is what the founder
    # sees), nonessential final screenshots are skipped, and contexts close
    # concurrently under one bounded budget. Anything that misses the budget is
    # still covered by expiry-fenced restart reconciliation.
    async def _shutdown_work() -> None:
        await asyncio.gather(
            *(_terminalize_for_shutdown(lease.run_id)
              for lease in browser_runtime.leases() if lease.run_id),
            return_exceptions=True,
        )
        await browser_expiry.shutdown()
        await browser_runtime.shutdown()
        proxies = [proxy for proxy in (_proxy, _public_proxy) if proxy is not None]
        if proxies:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(
                        *(proxy.close() for proxy in proxies),
                        return_exceptions=True,
                    ),
                    timeout=0.5,
                )
        await browser_event_hub.clear()

    try:
        await asyncio.wait_for(
            _shutdown_work(), timeout=SHUTDOWN_TOTAL_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        logging.getLogger(__name__).warning(
            "browser service shutdown exceeded %.1f s",
            SHUTDOWN_TOTAL_BUDGET_SECONDS,
        )
    finally:
        _browse_contexts.clear()
        _browse_locks.clear()
        _frame_locks.clear()
        _proxy = None
        _public_proxy = None


browser_runtime.configure_disconnect_callback(_reconcile_lost_leases)
