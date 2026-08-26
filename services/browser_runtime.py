"""Single-owner Playwright runtime and context supervisor (docs/22).

This module is the only production location allowed to launch Chromium or call
``Browser.new_context``.  It deliberately knows nothing about ADK, workflow
state, Firestore, or model policy.  Callers provide trusted lease metadata,
context options, request interception, and narrow event callbacks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from services import browser_metrics

LOGGER = logging.getLogger(__name__)

LeaseKind = Literal["browse", "fill", "render"]
SessionKey = tuple[str, str, str]
AsyncCallback = Callable[..., Awaitable[None]]

GLOBAL_CONTEXT_LIMIT = 4
RENDER_CONTEXT_LIMIT = 1
LAUNCH_TIMEOUT_SECONDS = 20
CONTEXT_TIMEOUT_SECONDS = 10
PAGE_TIMEOUT_SECONDS = 10
CLOSE_TIMEOUT_SECONDS = 5
SHUTDOWN_TIMEOUT_SECONDS = 2.5


class BrowserRuntimeUnavailable(RuntimeError):
    """Internal runtime failure; caller-facing services convert it to data."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class BrowserCapacityExceeded(RuntimeError):
    """The reviewed global/per-kind context capacity is exhausted."""


class BrowserForegroundBusy(RuntimeError):
    """A session already owns a foreground context."""


@dataclass(slots=True)
class ContextLease:
    lease_id: str
    run_id: str | None
    kind: LeaseKind
    session_key: SessionKey | None
    application_id: str | None
    phase: str | None
    context: Any | None
    page: Any | None
    browser_generation: int
    opened_at: float
    last_activity_at: float
    closing: bool = False
    # Bumped SYNCHRONOUSLY inside the Playwright event callback, before the
    # async policy handler is even scheduled. An action captures this value
    # before executing and compares it after: any dialog/download that fired
    # during the action makes its outcome ambiguous, even if the policy
    # handler has not finished writing its refusal yet.
    policy_event_seq: int = 0
    # In-memory half of the action once-gate. Playwright callbacks run on the
    # same event loop, so claiming this field is synchronous and establishes a
    # linearization point before any Firestore await. The durable ledger then
    # uses its own PREPARED -> terminal transaction as the second half.
    active_action_id: str | None = None
    action_completion: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyEventContext:
    """Action identity and once-gate result captured at event-delivery time."""

    action_id: str | None
    outcome: str | None
    claimed: bool


class _LaunchBreaker:
    """Three failures in 60 s open for 30 s; one half-open caller probes."""

    def __init__(self) -> None:
        self.failures: list[float] = []
        self.open_until = 0.0
        self.half_open = False

    def refusal(self, now: float) -> str | None:
        if now < self.open_until:
            return "circuit_open"
        if self.open_until and self.half_open:
            return "circuit_open"
        if self.open_until:
            self.half_open = True
        return None

    def success(self) -> None:
        self.failures.clear()
        self.open_until = 0.0
        self.half_open = False

    def failure(self, now: float) -> None:
        self.failures = [stamp for stamp in self.failures if now - stamp <= 60]
        self.failures.append(now)
        if len(self.failures) >= 3:
            self.open_until = now + 30
        self.half_open = False


class BrowserRuntime:
    """Own one Playwright driver/browser and all supervised context leases."""

    def __init__(self) -> None:
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._generation = 0
        self._launch_lock = asyncio.Lock()
        self._lease_lock = asyncio.Lock()
        self._breaker = _LaunchBreaker()
        self._leases: dict[str, ContextLease] = {}
        self._run_leases: dict[str, str] = {}
        self._application_leases: dict[str, str] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._shutdown_intent = False
        self._disconnect_callback: AsyncCallback | None = None

    @property
    def generation(self) -> int:
        return self._generation

    def configure_disconnect_callback(self, callback: AsyncCallback | None) -> None:
        """Install the service-owned durable reconciliation callback."""
        self._disconnect_callback = callback

    def _healthy_browser(self) -> bool:
        browser = self._browser
        if browser is None:
            return False
        probe = getattr(browser, "is_connected", None)
        try:
            return bool(probe()) if callable(probe) else True
        except Exception:  # pragma: no cover - defensive third-party boundary
            return False

    async def get_browser(self) -> Any:
        """Return the process browser, sharing one cancellation-safe launch."""
        if self._shutdown_intent:
            raise BrowserRuntimeUnavailable(
                "browser runtime is shutting down", reason="disconnected"
            )
        if self._healthy_browser():
            return self._browser
        async with self._launch_lock:
            if self._shutdown_intent:
                raise BrowserRuntimeUnavailable(
                    "browser runtime is shutting down", reason="disconnected"
                )
            if self._healthy_browser():
                return self._browser
            reason = self._breaker.refusal(time.monotonic())
            if reason:
                raise BrowserRuntimeUnavailable(
                    "browser launch circuit is open", reason=reason
                )

            playwright = None
            browser = None
            try:
                from playwright.async_api import async_playwright

                playwright = await asyncio.wait_for(
                    async_playwright().start(), timeout=LAUNCH_TIMEOUT_SECONDS
                )
                browser = await asyncio.wait_for(
                    playwright.chromium.launch(headless=True),
                    timeout=LAUNCH_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                await self._shielded_partial_close(browser, playwright)
                raise
            except Exception as exc:
                await self._close_partial(browser, playwright)
                self._breaker.failure(time.monotonic())
                browser_metrics.record("browser_launch_failure", reason="launch_failed")
                raise BrowserRuntimeUnavailable(
                    f"browser launch failed: {exc}", reason="launch_failed"
                ) from exc

            self._playwright = playwright
            self._browser = browser
            self._generation += 1
            generation = self._generation
            self._shutdown_intent = False
            self._breaker.success()
            browser_metrics.record("browser_launch", generation=generation)

            def disconnected() -> None:
                self._spawn(self._handle_disconnect(browser, generation))

            browser.on("disconnected", disconnected)
            return browser

    async def _shielded_partial_close(self, browser: Any, playwright: Any) -> None:
        task = asyncio.create_task(self._close_partial(browser, playwright))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Keep the bounded cleanup tracked even after a second cancellation.
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            raise

    @staticmethod
    async def _close_partial(browser: Any, playwright: Any) -> None:
        if browser is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(browser.close(), CLOSE_TIMEOUT_SECONDS)
        if playwright is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(playwright.stop(), CLOSE_TIMEOUT_SECONDS)

    def _spawn(self, awaitable: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        # docs/22: "no unobserved callback task". Retrieving the result without
        # logging silenced watchdog failures (a raising dialog/popup handler
        # left no trace at all); log it instead of swallowing.
        try:
            task.result()
        except Exception:  # noqa: BLE001 — a watchdog failure is data, not a crash
            LOGGER.exception("browser runtime callback task failed")

    async def _handle_disconnect(self, browser: Any, generation: int) -> None:
        """Detach only the browser/generation captured by this callback."""
        if (
            self._shutdown_intent
            or browser is not self._browser
            or generation != self._generation
        ):
            return
        self._breaker.failure(time.monotonic())
        browser_metrics.record("browser_disconnect", generation=generation)
        leases = await self._detach_generation(generation)
        playwright = self._playwright
        self._browser = None
        self._playwright = None
        await self._close_partial(None, playwright)
        if self._disconnect_callback is not None and leases:
            try:
                await self._disconnect_callback(leases)
            except Exception:
                LOGGER.exception("browser disconnect reconciliation failed")

    async def _detach_generation(self, generation: int) -> list[ContextLease]:
        async with self._lease_lock:
            leases = [
                lease
                for lease in self._leases.values()
                if lease.browser_generation == generation
            ]
            for lease in leases:
                self._remove_lease_unlocked(lease)
            return leases

    async def acquire_context(
        self,
        *,
        kind: LeaseKind,
        run_id: str | None,
        session_key: SessionKey | None,
        application_id: str | None = None,
        phase: str | None = None,
        context_options: dict[str, Any] | None = None,
        route_handler: AsyncCallback | None = None,
        on_popup: AsyncCallback | None = None,
        on_dialog: AsyncCallback | None = None,
        on_download: AsyncCallback | None = None,
    ) -> ContextLease:
        """Reserve capacity, install guards, create one primary page, promote."""
        if kind in {"browse", "fill"} and (not run_id or not session_key):
            raise ValueError("foreground leases require run_id and session_key")
        if kind == "fill" and not application_id:
            raise ValueError("fill leases require application_id")

        browser = await self.get_browser()
        now = time.monotonic()
        lease = ContextLease(
            lease_id=uuid.uuid4().hex,
            run_id=run_id,
            kind=kind,
            session_key=session_key,
            application_id=application_id,
            phase=phase,
            context=None,
            page=None,
            browser_generation=self._generation,
            opened_at=now,
            last_activity_at=now,
        )
        async with self._lease_lock:
            if len(self._leases) >= GLOBAL_CONTEXT_LIMIT:
                browser_metrics.record("browser_quota_refusal", kind=kind)
                raise BrowserCapacityExceeded("global browser capacity is full")
            if kind == "render" and sum(
                item.kind == "render" for item in self._leases.values()
            ) >= RENDER_CONTEXT_LIMIT:
                browser_metrics.record("browser_quota_refusal", kind=kind)
                raise BrowserCapacityExceeded("render browser capacity is full")
            if session_key and any(
                item.session_key == session_key and item.kind in {"browse", "fill"}
                for item in self._leases.values()
            ):
                browser_metrics.record("browser_foreground_busy", kind=kind)
                raise BrowserForegroundBusy("session already owns a foreground run")
            self._leases[lease.lease_id] = lease
            if run_id:
                self._run_leases[run_id] = lease.lease_id
            if application_id:
                self._application_leases[application_id] = lease.lease_id

        try:
            options = {"accept_downloads": False, **(context_options or {})}
            # Hard invariant: callers cannot turn downloads back on.
            options["accept_downloads"] = False
            options.setdefault("viewport", {"width": 1280, "height": 720})
            context = await asyncio.wait_for(
                browser.new_context(**options), timeout=CONTEXT_TIMEOUT_SECONDS
            )
            lease.context = context
            primary_ready = asyncio.Event()

            async def page_seen(page: Any) -> None:
                if lease.page is None:
                    lease.page = page
                    self._install_page_guards(
                        lease,
                        page,
                        on_dialog=on_dialog,
                        on_download=on_download,
                    )
                    primary_ready.set()
                    return
                if page is lease.page:
                    return
                self._install_page_guards(
                    lease,
                    page,
                    on_dialog=on_dialog,
                    on_download=on_download,
                )
                try:
                    if on_popup is not None:
                        await on_popup(lease, page)
                finally:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(page.close(), CLOSE_TIMEOUT_SECONDS)

            def page_event(page: Any) -> None:
                self._spawn(page_seen(page))

            context.on("page", page_event)
            if route_handler is not None:
                await context.route("**/*", route_handler)
            page = await asyncio.wait_for(
                context.new_page(), timeout=PAGE_TIMEOUT_SECONDS
            )
            if lease.page is None:
                lease.page = page
                self._install_page_guards(
                    lease, page, on_dialog=on_dialog, on_download=on_download
                )
                primary_ready.set()
            await asyncio.wait_for(primary_ready.wait(), timeout=PAGE_TIMEOUT_SECONDS)
            browser_metrics.record("browser_context_open", kind=kind)
            return lease
        except BaseException:
            await self._close_failed_lease(lease)
            raise

    def _install_page_guards(
        self,
        lease: ContextLease,
        page: Any,
        *,
        on_dialog: AsyncCallback | None,
        on_download: AsyncCallback | None,
    ) -> None:
        async def dialog_seen(dialog: Any, event: PolicyEventContext) -> None:
            # Dismissal is unconditional. A policy callback that raises (a
            # Firestore outage inside the audit/freeze write) must never leave
            # the modal up: an undismissed dialog blocks the page indefinitely
            # and the run would keep looking healthy. Playwright rejects the
            # dismiss when the page already handled the dialog itself — that
            # race is benign and stays suppressed.
            try:
                if on_dialog is not None:
                    await on_dialog(lease, dialog, event)
            finally:
                with contextlib.suppress(Exception):
                    await dialog.dismiss()

        async def download_seen(download: Any, event: PolicyEventContext) -> None:
            try:
                if on_download is not None:
                    await on_download(lease, download, event)
            finally:
                with contextlib.suppress(Exception):
                    await download.cancel()
                with contextlib.suppress(Exception):
                    await download.delete()

        def _mark_and_spawn(coro_factory, value, outcome: str | None):
            # Synchronous marker first: the async handler may not run before the
            # in-flight action finishes, and an action that completed while a
            # consequential dialog/download fired must never report SUCCEEDED.
            lease.policy_event_seq += 1
            action_id = lease.active_action_id
            claimed = False
            if action_id and outcome and lease.action_completion is None:
                lease.action_completion = outcome
                claimed = True
            self._spawn(coro_factory(
                value,
                PolicyEventContext(
                    action_id=action_id, outcome=outcome, claimed=claimed),
            ))

        page.on(
            "dialog",
            lambda value: _mark_and_spawn(
                dialog_seen,
                value,
                None if str(getattr(value, "type", "unknown")) == "alert"
                else "UNCERTAIN",
            ),
        )
        page.on(
            "download",
            lambda value: _mark_and_spawn(download_seen, value, "FAILED"),
        )

    async def _close_failed_lease(self, lease: ContextLease) -> None:
        """Release a lease whose setup failed, proving the context is dead.

        Suppressing a close timeout here and dropping the lease anyway would
        detach a context that may still be running — the same "no detached page
        continues network activity" hazard the normal close path recycles the
        generation for. Failed acquisition gets the identical treatment.
        """
        lease.closing = True
        close_failed = False
        if lease.context is not None:
            try:
                await asyncio.wait_for(lease.context.close(), CLOSE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                close_failed = True
            except Exception:
                close_failed = True
        async with self._lease_lock:
            self._remove_lease_unlocked(lease)
        if close_failed:
            await self.recycle_generation(lease.browser_generation)

    def _remove_lease_unlocked(self, lease: ContextLease) -> None:
        self._leases.pop(lease.lease_id, None)
        if lease.run_id:
            self._run_leases.pop(lease.run_id, None)
        if lease.application_id:
            self._application_leases.pop(lease.application_id, None)

    def lease_for_run(self, run_id: str) -> ContextLease | None:
        lease_id = self._run_leases.get(run_id)
        return self._leases.get(lease_id) if lease_id else None

    def lease_for_application(self, application_id: str) -> ContextLease | None:
        lease_id = self._application_leases.get(application_id)
        return self._leases.get(lease_id) if lease_id else None

    def leases(self) -> tuple[ContextLease, ...]:
        return tuple(self._leases.values())

    def is_current_lease(self, lease: ContextLease) -> bool:
        """True only while this exact generation/lease remains registered."""
        return (
            self._leases.get(lease.lease_id) is lease
            and lease.browser_generation == self._generation
            and not lease.closing
        )

    async def set_phase(self, run_id: str, phase: str) -> None:
        lease = self.lease_for_run(run_id)
        if lease:
            lease.phase = phase
            lease.last_activity_at = time.monotonic()

    def begin_action(self, run_id: str, action_id: str) -> bool:
        """Open the synchronous half of the per-action completion gate."""
        lease = self.lease_for_run(run_id)
        if lease is None or lease.closing or lease.active_action_id is not None:
            return False
        lease.active_action_id = action_id
        lease.action_completion = None
        return True

    def claim_action_completion(self, run_id: str, action_id: str,
                                outcome: str) -> tuple[bool, str | None]:
        """Claim exactly one in-memory terminal outcome without awaiting."""
        lease = self.lease_for_run(run_id)
        if lease is None or lease.active_action_id != action_id:
            return False, None
        if lease.action_completion is None:
            lease.action_completion = outcome
            return True, outcome
        return False, lease.action_completion

    def action_completion(self, run_id: str, action_id: str) -> str | None:
        lease = self.lease_for_run(run_id)
        if lease is None or lease.active_action_id != action_id:
            return None
        return lease.action_completion

    def finish_action(self, run_id: str, action_id: str) -> None:
        lease = self.lease_for_run(run_id)
        if lease is not None and lease.active_action_id == action_id:
            lease.active_action_id = None
            lease.action_completion = None

    async def close_lease(self, run_id: str) -> bool:
        """Close/remove one lease; recycle generation on a context-close hang."""
        lease = self.lease_for_run(run_id)
        if lease is None:
            return False
        return await self.close_context_lease(lease)

    async def close_context_lease(self, lease: ContextLease) -> bool:
        """Close a foreground or internal lease by its supervised identity."""
        if lease.lease_id not in self._leases:
            return False
        lease.closing = True
        close_failed = False
        if lease.context is not None:
            try:
                await asyncio.wait_for(
                    lease.context.close(), timeout=CLOSE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                close_failed = True
            except Exception:
                close_failed = True
        async with self._lease_lock:
            self._remove_lease_unlocked(lease)
        if close_failed:
            await self.recycle_generation(lease.browser_generation)
        return True

    async def recycle_generation(self, generation: int) -> list[ContextLease]:
        """Kill a suspect generation; no detached context may keep running."""
        if generation != self._generation:
            return []
        browser, playwright = self._browser, self._playwright
        prior_shutdown_intent = self._shutdown_intent
        self._shutdown_intent = True
        sibling_leases = await self._detach_generation(generation)
        self._browser = None
        self._playwright = None
        self._generation += 1
        await self._close_partial(browser, playwright)
        self._shutdown_intent = prior_shutdown_intent
        if self._disconnect_callback is not None and sibling_leases:
            await self._disconnect_callback(sibling_leases)
        return sibling_leases

    async def shutdown(
        self, timeout_seconds: float = SHUTDOWN_TIMEOUT_SECONDS
    ) -> None:
        """Detach ownership immediately, then reap resources under one deadline.

        Per-resource five-second waits compose badly during process shutdown:
        contexts, browser, and Playwright could consume fifteen seconds after
        Uvicorn had already spent its request-drain allowance. Registry state is
        cleared before the first await and all physical cleanup shares one
        process-exit budget.
        """
        self._shutdown_intent = True
        leases = list(self._leases.values())
        browser, playwright = self._browser, self._playwright
        tasks = list(self._tasks)
        self._leases.clear()
        self._run_leases.clear()
        self._application_leases.clear()
        self._browser = None
        self._playwright = None
        self._tasks.clear()
        self._generation += 1
        for lease in leases:
            lease.closing = True
        for task in tasks:
            task.cancel()

        async def _cleanup() -> None:
            if leases:
                await asyncio.gather(
                    *(lease.context.close() for lease in leases
                      if lease.context is not None),
                    return_exceptions=True,
                )
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._close_partial(browser, playwright)

        try:
            await asyncio.wait_for(
                _cleanup(), timeout=max(0.1, float(timeout_seconds)))
        except asyncio.TimeoutError:
            LOGGER.warning("browser runtime shutdown exceeded %.1f s", timeout_seconds)
        finally:
            # Tasks spawned by a final Playwright callback during teardown are
            # not allowed to outlive shutdown either.
            for task in list(self._tasks):
                task.cancel()
            self._tasks.clear()
            self._shutdown_intent = False


ContextSupervisor = BrowserRuntime


runtime = BrowserRuntime()
