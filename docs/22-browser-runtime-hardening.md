# 22 — Browser Runtime Hardening and In-App Observation

This document is the production-hardening contract for the browser agent in
18 and the portal form-filler in 09. It does not widen browser capabilities.
It makes browser ownership, projection, containment, and cleanup deterministic.

Where this document conflicts with an older browser-run lifecycle, observation,
artifact, popup, dialog, or stop-control statement in 02, 03, 09, 10, 13, 16,
or 18, **this document wins**.
The action allowlists, approval gates, content-trust rules, and network policy
remain owned by 09, 12, and 18.

## Outcome

The product has exactly two browser planes:

1. **Execution plane:** one application-owned, Playwright-managed Chromium
   process, always headless, with isolated contexts. It can never attach to or
   launch the founder's system browser.
2. **Observation plane:** an authenticated, read-only projection inside the
   app's Browser surface. It shows ordered viewport frames and run state. It is
   not an iframe of the remote website and cannot navigate independently.

The resulting invariant is precise:

> Browser work may be executed only in the application-owned headless Chromium.
> Every interactive or credentialed browser run may be observed only in the
> in-app Browser surface. Short-lived, read-only discovery rendering is the one
> noninteractive RunView exemption. No agent, UI link, setup script, OAuth
> fallback, or debug path may open an OS browser window.

Human OAuth consent is a separate first-party authentication flow. A provider
may redirect the current app tab when embedding is prohibited; that flow never
uses agent browser tools, never launches a new window, and never exposes OAuth
tokens to the agent browser. Concretely, `app/static/login.html` signs the
founder in with `signInWithRedirect` and consumes the credential on the way back
with `getRedirectResult` — never a popup sign-in call. "No new window" binds the
SDK that opens one for you exactly as it binds `window.open`.

## Reference patterns adopted and rejected

Reference repositories are read-only pattern-mining inputs. No dependency or
source code is copied into this project.

| Reference | Adopt | Explicitly reject |
|---|---|---|
| Cline `BrowserSession` / `BrowserSessionRow` | Ordered action-result frames, URL/action metadata, close-before-replace ownership | Debug/relaunch modes that spawn system Chrome; remote headed sessions |
| OpenHands Browser store | Push browser observations to the embedded surface while the agent turn is still running; reset on session change | Unrelated `window.open` application links |
| browser-use `BrowserSession` + watchdogs | Idempotent start, explicit stop/kill semantics, duplicate-handler protection, reset discipline, pre/post-navigation and new-page guards | Allow-all domain default; personal Chrome profiles; automatically accepting `confirm` or `beforeunload` dialogs |
| OpenClaw browser runtime | Isolated managed profile, session-owned tabs, idle/excess cleanup, launch circuit breaker, authenticated observation, SSRF recheck | Headed default, personal-profile attachment, externally managed CDP as a product mode, CAPTCHA solving |
| Strix | Bounded per-task resource ownership and proxy isolation | Container orchestration as a prerequisite for the v1 UI |
| Google new-hire-onboarding sample | In-app artifact preview only | `target="_blank"`, 1.2-second polling, and treating an artifact iframe as browser automation |

## Non-negotiable runtime invariants

These are code and test invariants, not configuration guidance.

1. `chromium.launch(headless=True)` is the only local launch form. There is no
   environment variable, request field, CLI flag, debug mode, executable-path
   override, persistent user-data directory, `connect_over_cdp`, or extension
   attach path in production code.
2. One Playwright driver and one Chromium process exist per server process.
   Concurrent cold-start callers share one launch attempt.
3. Every browser context is registered with the supervisor. Interactive
   `browse`/`fill` contexts belong to one durable `browser_runs/{run_id}` row
   and authenticated `(app_name, user_id, session_id)` owner. Portal bootstrap,
   sign-in, credential entry, and verification are foreground phases of the
   owning `fill` run, not invisible context kinds. Short-lived, read-only
   discovery render contexts receive an internal lease but no founder RunView.
4. A session has at most one foreground browser run across `browse` and `fill`.
   A fill supersedes an active research run. A research open during an active
   fill returns `browser_busy`; it never hides fill work behind another page.
5. Downloads are disabled in every context, including local-development
   credentialed contexts.
6. A run has exactly one projected page. New pages/popups cannot become hidden
   work. Research popups are closed and refused. Form-fill popups are closed
   and return `needs_human`; SSO handoff remains a founder blocker in v1.
7. JavaScript dialogs never auto-confirm. Alerts are dismissed and audited.
   `confirm`, `prompt`, and `beforeunload` are dismissed, freeze the current
   action, and return `needs_human`.
8. The founder can stop either a `browse` or `fill` run. Stop first reserves the
   durable `stopping` state, then closes the live context before committing the
   terminal state, and is idempotent.
9. Firestore is the durable truth. Push events are hints. Reconnect always
   begins with a fresh state snapshot and never depends on replaying an
   in-memory queue.
10. Browser state reaches the UI by event push, never a periodic browser-state
    poll. Transport keepalives may preserve an already-open stream but perform
    no Firestore read, agent wake, model call, or browser action.

## Durable run state machine

22 extends the run status values in 18. The only valid transitions are:

```text
opening → active
opening → stopping
active  → blocked
active  → stopping
blocked → stopping
stopping → closed
any nonterminal → closed  (owner loss / launch failure only)
```

- `opening`: the durable row exists; no action is allowed yet. Founder Stop or
  owner shutdown moves it to `stopping`; launch failure with no live context may
  close it directly.
- `active`: the registered primary page and first frame both exist.
- `blocked`: the page remains readable/observable but actions are frozen.
- `stopping`: terminal ownership is reserved; no new action or read reservation
  may begin. Concurrent stop/close callers join the same per-run close lock.
- `closed`: no registered context exists. The last frame may remain visible.

The complete normal edge set is `opening → active`, `opening → stopping`,
`active → blocked`, `active → stopping`, `blocked → stopping`, and
`stopping → closed`. There is no generic `blocked → active` edge. Security
blocks (bot challenge or injection) require stop/reopen. A recoverable dialog or
founder-action blocker is recovered only by closing and reopening with a fresh
page signature; it never silently resumes an old action.

`close_reason` includes the 18 values plus `superseded_by_fill`, `expired`, and
`browser_crash`. Any attempted transition outside this graph is refused and
audited; terminal `closed` is immutable except for idempotent reads. Owner-loss
reconciliation may move any nonterminal state directly to `closed/restart` or
`closed/browser_crash` because no live context remains to stop.

## Target architecture

```text
POST /wake ──► ADK tool ──► browser_service (policy + durable run protocol)
                                  │
                                  ├──► BrowserRuntime
                                  │      ├─ single-flight headless Chromium
                                  │      ├─ ContextSupervisor
                                  │      ├─ popup/dialog/download watchdogs
                                  │      └─ process/context quotas + shutdown
                                  │
                                  ├──► Firestore BrowserRun/action/frame rows
                                  │         durable write happens first
                                  │
                                  └──► BrowserEventHub ──► SSE /api/browser/events
                                                              │
                                                              ▼
                                                   in-app Browser surface
                                                   latest frame + timeline
```

Implementation ownership:

| File | Responsibility |
|---|---|
| `services/browser_runtime.py` | `BrowserRuntime`, launch single-flight, circuit breaker, context supervisor, quotas, common context watchdog installation, shutdown |
| `services/browser_events.py` | Typed browser events, bounded per-session subscribers, snapshot-first SSE fan-out; no durable truth |
| `services/browser_service.py` | Existing URL/action/content policy, Firestore run/action/frame protocol, runtime calls, event publication after durable writes |
| `app/browser_routes.py` | Authenticated state, event-stream, stop endpoints; request identity is resolved server-side |
| `app/static/index.html` | EventSource lifecycle, monotonic reducer, Browser-only rendering, frame timeline, browse/fill Stop control |

`browser_runtime.py` must not import ADK, model code, or application workflow
state. `browser_events.py` must not import Playwright. This keeps process
ownership, product policy, and observation transport independently testable.

## Browser process lifecycle

### Single-flight launch

`BrowserRuntime.get_browser()` uses one process-wide `asyncio.Lock` and
double-checks health after acquiring it:

```python
async def get_browser(self) -> Browser:
    if self._healthy_browser():
        return self._browser
    async with self._launch_lock:
        if self._healthy_browser():
            return self._browser
        if self._breaker.is_open(now()):
            raise BrowserRuntimeUnavailable("launch circuit open")

        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=True)
        except asyncio.CancelledError:
            # Cancellation is a BaseException on supported Python versions.
            # Shield the bounded partial cleanup, then preserve cancellation.
            await asyncio.shield(close_partial(browser, playwright))
            raise
        except Exception:
            await close_partial(browser, playwright)
            self._breaker.record_failure(now())
            raise

        self._playwright = playwright
        self._browser = browser
        self._generation += 1
        self._breaker.record_success()
        generation = self._generation
        browser.on(
            "disconnected",
            lambda: self._schedule_disconnect(browser, generation),
        )
        return browser
```

Globals are assigned only after both Playwright and Chromium start. A failed or
cancelled launch closes partial resources with a bounded cleanup timeout; a
cancelled caller is never counted as a launch failure. Twenty concurrent cold-start calls
must result in one `async_playwright().start()` and one Chromium launch. The
runtime exception shown above is internal only; `browser_service` catches it
and returns the stable error-as-data contract before any tool/route boundary.

### Circuit breaker

- Three launch/disconnect failures inside 60 seconds open the circuit for 30
  seconds.
- While open, calls return an error-as-data result with code
  `browser_unavailable`; they do not attempt another launch.
- One caller performs the half-open trial. Success closes the circuit; failure
  reopens it.
- There is no background retry loop. A founder action, agent wake, or scheduled
  run event supplies the next demand.

### Disconnection and shutdown

The disconnect callback captures both the browser object and generation. It is
ignored unless both still match the installed runtime and shutdown intent is
false. This prevents an intentional close or stale callback from tripping the
breaker, detaching a newer generation, or closing runs owned by that generation.
An accepted unexpected Chromium disconnection atomically detaches the runtime
registry, marks owned durable runs `closed/browser_crash` best-effort, and
publishes one terminal event per run. Shutdown first sets shutdown intent,
stops accepting contexts, closes all
registered contexts concurrently with bounded timeouts, closes both validating
proxies, closes Chromium, stops Playwright, clears registries, and increments
the runtime generation. Every step is idempotent.

## Context supervisor and foreground arbitration

`ContextSupervisor` is the only component allowed to call
`browser.new_context()`. A registered lease contains:

```python
ContextLease = {
    "lease_id": str,
    "run_id": str | None,       # required for browse/fill, absent for render
    "kind": "browse" | "fill" | "render",
    "session_key": tuple[str, str, str] | None,
    "application_id": str | None, # required for fill
    "phase": str | None,          # fill: authenticating/verifying/filling/...
    "context": BrowserContext,
    "page": Page | None,          # absent during guarded context setup
    "browser_generation": int,
    "opened_at": str,
    "last_activity_at": str,
    "closing": bool,
}
```

Rules:

- `browse` and `fill` leases require a durable run before registration.
- A fill run exists and is projected before portal bootstrap begins. Login,
  credential entry, mailbox-code verification, filling, approval wait, and
  submission use phases on that same foreground run; they never create an
  unprojected `credentialed` or `verification` run.
- A `render` lease is short-lived discovery infrastructure, is globally
  bounded, and never appears in the founder panel.
- Binding v1 limits are four contexts globally, one foreground context per
  session, and one render context. They are code constants, not request or
  environment overrides; changing capacity requires a reviewed code change.
- Capacity refusal returns `capacity_exceeded`; callers do not spin or retry.
- Registering a fill closes an active browse as `superseded_by_fill` before
  creating the fill context.
- Opening research while fill is active returns `browser_busy` with the
  redacted active RunView.
- The form-filler's `_pages` map is removed. All lookup, close, replacement,
  and shutdown ownership goes through the supervisor by `run_id` and
  application metadata stored on the durable run.

Lease acquisition is two-stage: reserve quota and register a `page=None` lease,
create the context, install context guards, create/register the primary page,
then promote the durable run to `active` after its first frame commits. Any
failure or cancellation before promotion closes the partial context, releases
quota, and terminalizes the durable run. No page may exist before its lease and
context-wide guards exist.

### Proxy and interception matrix

The runtime exposes two validating proxies: public-read and portal-scoped.
Every context also installs Playwright interception. Deny rules win at both
layers; redirects and subresources are rechecked.

| Lease / phase | Proxy and destinations | Request methods and actions | RunView |
|---|---|---|---|
| `browse` | public-read; 18 allow/block lists, DNS pinning and SSRF denial | GET/HEAD only; 18 research action policy | required |
| `fill` — authenticating/verifying/filling | portal-scoped; only adapter-declared portal, identity-provider, and verification origins; DNS pinning and SSRF denial | adapter/action allowlist; credential POSTs are permitted only inside the audited fill phase; submit remains G2-only | required from before context creation |
| `fill` — submitting | same portal-scoped origins | only the deterministic, idempotent adapter submit request after G2 and staleness checks | required |
| `render` | public-read; configured discovery-source origins only, DNS pinning and SSRF denial | GET/HEAD only; no model-proposed actions | none; internal lease only |

Proxy selection is derived from the trusted lease kind and adapter, never a
request parameter. Credentials and verification codes are resolved at execution
time and are never added to RunView, frames, URLs, logs, or audit details.
Credential/verification frame capture applies a temporary screenshot-only
redaction style to password, OTP, token, and adapter-declared sensitive fields;
tests inspect decoded pixels and artifact metadata, not only logs. Phase and
progress remain visible while secret values do not.

## Common page-containment watchdogs

Every context is created with `accept_downloads=False` and receives watchdogs
before its first page is created.

### New pages and popups

- The supervisor tracks the one primary page established at registration.
- Any later `context.on("page")` page is covered by the already-installed
  context-wide network guard, closed immediately, and audited with only its
  redacted URL. It is never paused in an inspector, screenshotted, or adopted.
- Research returns `popup_blocked` for the causing action.
- Form fill returns `needs_human` with reason `popup_or_sso_required`.
- A popup is never silently adopted and never remains alive after the action.

### Dialogs

The `page.on("dialog")` handler records the type, message length, and message
SHA-256 only. Raw dialog text is untrusted page content and is never persisted:

| Dialog | Handling |
|---|---|
| `alert` | Dismiss, audit `browser_dialog`, continue only if the action otherwise succeeded |
| `confirm` | Dismiss, action becomes `UNCERTAIN`, freeze run, return `needs_human` |
| `prompt` | Dismiss, freeze run, return `needs_human`; never type supplied text |
| `beforeunload` | Dismiss, keep current page, return `needs_human` |

Event callbacks are scheduled through a tracked task set. Exceptions are
logged without secrets and converted into run policy errors; no unobserved
callback task is allowed. Each dialog handler captures the current `action_id`
and completes through an atomic once-gate shared with navigation/action
completion. A late dialog after Stop or generation replacement is dismissed but
cannot mutate the closed/new run or overwrite a recorded action result.

### Downloads

Playwright Python exposes download events on `Page`, not `BrowserContext`.
`accept_downloads=False` is the context-level guarantee; the supervisor installs
exactly one `page.on("download")` handler on every primary or newly observed page.
The handler correlates to the current `action_id` through the same once-gate,
cancels/deletes any temporary transfer best-effort, and records
`FAILED/policy_refused` once. Download paths and suggested filenames are never
returned to the model or UI. Popup pages are still guarded before immediate
closure, so a popup-triggered download cannot become hidden work.

## Durable run, frame, and lease contract

22 extends the BrowserRun in 18 with:

```python
{
    "application_id": str | None, # required for fill; durable recovery lookup
    "phase": str | None,          # foreground fill phase; never contains secrets
    "version": int,             # increments on every UI-visible mutation
    "frame_seq": int,           # increments once per persisted frame
    "browser_generation": int,  # process generation that owns the context
    "lease_generation": int,    # increments whenever expiry is renewed
    "expires_at": str,          # durable inactivity expiry
    "blocked_reason": str | None,
}
```

Frames live at `browser_runs/{run_id}/frames/{frame_seq}`:

```python
BrowserFrame = {
    "seq": int,
    "run_id": str,
    "run_version": int,
    "phase": "nav" | "before" | "after" | "blocked" | "closed",
    "url": str,                 # always redacted
    "title": str,
    "action": {"kind": str, "target": str} | None,
    "artifact": str | None,     # authenticated JPEG; blocked/closed may reference milestone PNG
    "created_at": str,
}
```

Artifact upload cannot be part of a Firestore transaction. Under the per-run
frame lock, read the durable counter, derive candidate `frame_seq + 1`, then
capture and upload that immutable, content-type-checked object first. The
following Firestore transaction verifies the counter is unchanged, creates the
immutable frame metadata, updates the run's latest artifact, and increments
`frame_seq` and `version`. A failed precondition leaves an undiscoverable orphan
and retries the whole capture once with a new candidate. Publish only after the
transaction commits. On capture/upload failure, the transaction still commits
frame metadata with `artifact=null`; the browser action completes. Any upload
whose transaction never commits is removed by the storage lifecycle. Frame
records are immutable.

Live frames are viewport captures, not full-page captures:
`browserframe_{run_id}_{frame_seq}.jpg`, 1280×720 JPEG, quality 75, maximum
500 KiB. If the cap is exceeded, quality is reduced once; failure returns frame
metadata without an artifact and does not fail the browser action. These JPEGs
are both the ordered step evidence and the Browser-surface preview. Durable
full-page PNGs are milestone artifacts only: `pageshot_{run_id}_{seq}_blocked.png`
and `pageshot_{run_id}_{seq}_final.png` (plus form-filler's fill/submit PNGs from
09). PNG is not captured before and after every live action.

Only `nav`, `after`, `blocked`, and `closed` frames appear in the default UI
timeline. `before` frames remain audit evidence.

Live JPEG objects expire after seven days. Milestone PNG, page-text, fill-report,
and form-map artifacts follow the application artifact lifecycle; audit metadata
remains after frame expiry and the UI renders an evidence-expired placeholder.
URLs stay redacted in retained metadata. Deployment configures and verifies both
storage lifecycle rules rather than relying on an undocumented bucket default.

## Event-driven in-app observation

### Endpoint

`GET /api/browser/events?session_id=<id>` returns `text/event-stream`.

SSE is deliberate: browser observation is server-to-client only, automatic
reconnect is useful, and browser control must continue through audited HTTP/
ADK paths rather than sharing a bidirectional socket. The existing Gemini Live
WebSocket remains voice-only.

- Existing app authentication resolves the founder. `user_id` and `app_name`
  never come from query parameters.
- Unknown or other-founder sessions return 404 before streaming begins.
- Response headers include `Cache-Control: no-store`, `X-Accel-Buffering: no`,
  and the existing same-origin security headers.
- The first event is always `browser.snapshot`, obtained from Firestore.
- Later event types are `browser.started`, `browser.frame`,
  `browser.status`, and `browser.closed`.
- Event IDs are `<run_id>:<version>`. The UI compares versions per run and
  drops duplicates or older events.
- Reconnect does not replay an in-memory queue. It receives a new durable
  snapshot, so a process restart or dropped event self-heals.
- A transport comment may be sent every 25 seconds while connected. It performs
  no database/model/browser work and is not a product polling mechanism.
- The Cloud Run request timeout is explicitly configured longer than the client
  stream horizon. The server ends each stream before that horizon; EventSource
  reconnects and receives a new durable snapshot. Stream rotation is transport
  maintenance, not a browser-state poll.

The endpoint subscribes its bounded queue **before** reading the durable
snapshot, emits that snapshot, then drains queued events through the version
reducer. This closes the snapshot/subscribe race; duplicate events are harmless.

Each subscriber queue is bounded to 16 items. On overflow the hub drops queued
intermediate events and enqueues one `browser.resync` marker. The client then
calls `GET /api/browser/state` once and resumes streaming. Slow clients can
therefore lose animation frames, never final state.

Subscription is an async context manager with `try/finally` removal on normal
completion, cancellation, disconnect, logout, and generator error. A founder may
hold at most three browser streams across tabs; a fourth returns 429. Hub tests
assert subscriber count returns to zero after each exit path and that queues do
not retain RunViews after unsubscribe.

### Publication points

Publish only after these durable transitions:

1. run created (`opening`);
2. first navigation/frame committed (`active`);
3. every action after-frame committed;
4. action/run blocked;
5. founder stop requested (`stopping`);
6. context closed and run terminal;
7. restart/crash/expiry reconciliation.

No model token stream or untrusted page text is sent through this channel.

## Browser surface behavior

The Browser surface is an observation/control surface, not a webview:

- It renders only authenticated screenshot artifacts. Arbitrary remote pages
  are never placed in an iframe.
- The URL input composes an agent request through `/wake`; it never assigns
  `location`, creates an anchor navigation, or calls browser APIs directly.
- Chat Markdown links remain `data-browser-url` controls routed through the
  same policy path.
- The app opens one EventSource while the authenticated page is visible and
  closes it on logout, session switch, or `visibilityState=hidden`.
- A `browser.started` or active snapshot automatically switches the left
  surface to Browser. Browser frames render nowhere else in the application.
- UI state is reduced by `(run_id, version, frame_seq)`. Older frames cannot
  overwrite newer ones, even when image requests finish out of order.
- The stage shows `opening`, `live`, `blocked`, `stopping`, or `closed`, plus
  redacted URL, goal, non-secret fill phase, last action, and a compact
  Cline-style frame timeline.
- Stop is shown for every nonterminal `browse` and `fill` run. Stopping a fill first asks
  the founder to confirm that unsaved portal entries may be discarded. Stop
  never submits or changes application workflow state.
- The last frame remains visible after close, labelled as retained evidence.
- Pipeline/application refresh remains activity-driven. There is no browser
  `setInterval` and no five-second browser-state poll.

In every RunView, `active` means nonterminal
(`opening|active|blocked|stopping`); `status` carries the precise state. UI and
tools must not infer action permission from the compatibility boolean.

## Stop, expiry, and cleanup

`POST /api/browser/stop` accepts `{session_id, run_id?}`. The server resolves
identity, verifies the run belongs to the session, and closes the foreground
run when `run_id` is omitted. It works for both kinds and returns:

```json
{"status":"success","run_id":"...","kind":"fill","already_closed":false}
```

Close ordering is fixed:

1. under the per-run close lock, mark the lease `closing`, commit
   `status=stopping` + `version++`, and publish that committed status;
2. cancel the in-flight action task with a bound. If its PREPARED ledger row has
   no committed result, atomically mark it `UNCERTAIN/stopped`; it is never
   replayed automatically;
3. when the page is healthy, capture/upload the one `final` milestone PNG with
   a five-second bound; capture failure is recorded but never blocks cleanup;
4. close context with a five-second bound;
5. if context close times out, mark the generation unavailable and immediately
   tear down that Chromium generation; reconcile its other leases as
   `closed/browser_crash`. A timed-out context is never detached and allowed to
   keep executing behind the UI. The requested Stop still reaches a durable
   terminal state;
6. remove registry and release quota;
7. commit `status=closed`, reason, `version++`, and an immutable closed-frame
   record that references the last artifact rather than capturing a dead page;
8. audit;
9. publish the committed terminal event.

### Fill Stop and restart recovery

Stop never changes application workflow state. Recovery is derived from the
durable application and BrowserRun, never an in-memory `Page`:

- A stop during `FORM_FILLING` leaves the application in `FORM_FILLING`. The
  next fill creates a new run linked by `application_id`, reopens and remaps the
  portal, reapplies the durable approved field mapping idempotently, writes a new
  fill report, and only then transitions to `AWAITING_SUBMIT_APPROVAL` and
  requests approval.
- A stop while `AWAITING_SUBMIT_APPROVAL` leaves that state unchanged. Reopen
  must reconstruct from the application, refill idempotently, and compare the
  live signature and intended mapping with the approved fill report. Only an
  unchanged portal state may use an existing GRANTED, unexpired, unconsumed
  approval. Any material signature/mapping change expires the old approval,
  writes a new fill report, and requires fresh founder approval.
- `portal_state_hash` in `applications.form_fill_report` is authoritative.
  BrowserRun may project the observed hash for diagnostics but may not become a
  second mutable source of truth.

Inactivity cleanup is event-driven and durable. Creating or successfully using
a run increments `lease_generation`, sets `expires_at`, and schedules one Cloud
Task carrying `{run_id, lease_generation}`. `/tasks/browser_expire` closes only
when the generation still matches and the durable expiry has passed. Renewed
runs make older tasks harmless no-ops. Local development uses one cancellable
timer per lease as a convenience, never a polling loop.

Cloud task names include the run and generation. Delivery uses the dedicated
OIDC-authenticated `co-founder-browser-expiry` queue so browser leases cannot
sit behind portal-event/agent-wake work. The handler trusts neither
session nor owner data from the body. It loads ownership and expiry from
Firestore; a stale generation returns HTTP 200 with `already_renewed:true`.

Default inactivity is 30 minutes for fill and 5 minutes for browse after the
last successful action. The 90-second action budget in 18 remains separate: it
bounds autonomous activity, while expiry bounds resource retention.

### Browser and renderer failure bounds

Every Playwright operation has a named deadline: launch 20 s, context/page
creation 10 s each, navigation 30 s, action 10 s, screenshot 5 s, and close 5 s.
No caller may replace these bounds with an unbounded timeout. A timeout returns
stable error-as-data code `timeout`, marks an ambiguous PREPARED action
`UNCERTAIN`, and closes or blocks the affected run according to whether page
health can be proven. An unexpected browser disconnect closes every nonterminal
owned run as `browser_crash`; stale callbacks cannot affect a new generation.

Read-only discovery renderer failures close the internal render lease and return
the caller's existing fetch/render error-as-data result. They never create a
Browser RunView, never retry in a loop, and never consume foreground capacity
after their deadline.

## Static external-launch guard

CI runs `scripts/check_browser_invariants.py` across every production root
(`agents`, `app`, `services`, `scripts`, `mock_portal`) for Python, JavaScript
(`.js/.jsx/.mjs/.cjs`), TypeScript, component templates (`.vue/.svelte`),
HTML (`.html/.htm`), and shell (`.sh/.bash/.zsh/.ps1`). Build artefacts
(`node_modules`, `__pycache__`, `.venv`) are skipped by name; everything else
is scanned unless it is named in the short `SKIP_PATHS` list of specific
known-safe paths (`tests/fixtures`, `app/static/vendor`). A directory that
merely happens to be called `vendor` or `fixtures` inside a production root is
**not** skipped. It fails on:

- Python `webbrowser.open*` reached by any binding — `import webbrowser as wb`,
  `from webbrowser import open as show`, `getattr(webbrowser, "open")`,
  `importlib.import_module("webbrowser")`, `__import__("webbrowser")`;
- process launches whose command is `open`, `xdg-open`, `start`, or
  `Start-Process` — literal argv, argv bound to a nearby list literal,
  `shell=True` command strings, `os.system`, `os.exec*`/`os.spawn*`, and
  `asyncio.create_subprocess_exec/shell`;
- JavaScript `window.open` including `window["open"]`, `window["op"+"en"]`,
  `globalThis.open`, and `const w = window; w.open(...)`; Electron
  `shell.openExternal`;
- auth SDKs that open the window internally, so the source never says
  `window.open` — Firebase `signInWithPopup`, `linkWithPopup` and
  `reauthenticateWithPopup`; MSAL `loginPopup` and `acquireTokenPopup`; Google
  Identity Services `initTokenClient`/`initCodeClient` configured with
  `ux_mode: "popup"`. Renamed imports (`import { signInWithPopup as gp }`),
  destructuring renames (`const { loginPopup: lp } = msal`) and value aliases
  (`const gp = signInWithPopup`) are resolved per file and their call sites are
  flagged too. `signInWithRedirect`/`getRedirectResult` are the sanctioned shape
  and stay quiet;
- HTML/JSX `target="_blank"` in every spelling — unquoted, single-quoted,
  `target={"_blank"}`, `target: "_blank"`, `setAttribute("target","_blank")`,
  and `formtarget`;
- shell openers anywhere on a line (indented, `$(open …)`, backticks, `exec`,
  `nohup`, after `;`/`&&`/`|`), not only at column zero;
- Playwright `headless=False` (with or without spaces), non-literal `headless`,
  `headless` smuggled through `**kwargs`, `launch` captured as an alias or via
  `getattr`, `launch_persistent_context`, `connect_over_cdp`, or
  user-data-directory use;
- executable-path/system-profile launch configuration, including keys supplied
  through `**{...}` / `**kwargs` dict unpacking;
- `new_context` outside `services/browser_runtime.py`, whether called directly,
  captured as an attribute, or fetched with `getattr`.

It also counts literal Chromium launch sites across **all** production roots
and fails unless there is exactly one and it lives in
`services/browser_runtime.py`.

The check parses Python with `ast` — resolving import aliases per file so a
renamed import is not a bypass — and uses bounded syntax-aware patterns for
web/shell sources. A plain substring assertion remains as defense in depth but
is not the only guard. Any narrowly required exception must be an explicit
entry in the module-level `ALLOWLIST`, keyed by `(path, rule id)` and carrying
an owner and rationale; an entry suppresses exactly that rule at exactly that
path. A false positive is fixed by narrowing the rule, not by allowlisting.
The allowlist is empty and a unit test asserts it stays that way.

## Error and observability contract

New error codes are stable data returned at the tool/service boundary:

| Code | Meaning |
|---|---|
| `browser_unavailable` | Browser cannot serve the request; required non-secret `reason` is `launch_failed` \| `circuit_open` \| `disconnected` so operations and UI do not collapse distinct failures |
| `browser_busy` | A fill owns the session foreground |
| `capacity_exceeded` | Global context quota is full |
| `popup_blocked` | Research action attempted to create another page |
| `dialog_blocked` | A consequential JavaScript dialog was dismissed |
| `run_expired` | Durable inactivity lease expired |

Structured metrics contain no URL query, page text, credentials, or screenshot
bytes:

- browser launches, launch failures, circuit state and process generation;
- active contexts by kind, quota refusals, orphan reconciliation;
- frame capture/write latency, dropped event count, subscriber count;
- popup/dialog/download refusals;
- stop and expiry close latency.

## Implementation order

1. **Runtime ownership:** add `BrowserRuntime`, launch/proxy locks, partial
   cleanup, process generation, circuit breaker, context supervisor, and
   central shutdown. Migrate all direct `new_context()` calls.
2. **Containment parity:** install common popup/dialog/download watchdogs for
   browse, every credentialed/verification phase of foreground fill, and render
   contexts. Remove the form-filler `_pages` registry.
3. **Durable projection:** add run `version`/`frame_seq`/lease fields and frame
   rows; publish only after durable commits.
4. **Live transport:** add event hub and snapshot-first SSE endpoint; replace
   browser polling with the monotonic UI reducer and timeline.
5. **Control and cleanup:** extend Stop to fill, implement foreground
   arbitration, quotas, durable expiry tasks, crash reconciliation.
6. **Invariant enforcement:** add the static checker, concurrency/transport/
   containment tests, metrics, and deployment verification.

Each work item lands with its tests. Do not mix policy expansion or new browser
actions into this hardening change.

## Acceptance checks

### Process and ownership

- [ ] Twenty concurrent cold-start `get_browser()` calls start Playwright and
  Chromium exactly once and all receive the same browser generation.
- [ ] A cancelled/failed launch leaves no assigned global and closes partial
  resources; three failures open the circuit and calls during the open interval
  cause zero launches.
- [ ] All context creation flows through `ContextSupervisor`; global and
  per-session quota tests return data errors without retry loops. The two-stage
  lease test proves context guards exist before the first page.
- [ ] Starting fill closes research as `superseded_by_fill`; starting research
  while fill is active returns `browser_busy` and creates no context.
- [ ] Shutdown and unexpected disconnect leave zero live leases and reconcile
  every owned durable run exactly once; a stale/intentional disconnect callback
  cannot detach a newer generation or trip the circuit breaker.
- [ ] A context-close timeout tears down its browser generation and reconciles
  sibling leases; no detached page continues network activity.
- [ ] Startup reconciliation is instance-fenced: it terminalizes only runs
  stamped with this process's `owner_instance`, or a foreign run whose durable
  lease has already lapsed. Two overlapping revisions (rolling deploy) never
  close each other's live runs.
- [ ] A policy callback that raises (dialog/download) still dismisses or cancels,
  and the failure is logged rather than silently swallowed.
- [ ] A run closed underneath an in-flight open releases its supervisor lease,
  and the open returns an error instead of a live page behind a closed run.
- [ ] Every UI-visible field change (`last_action`, `current_url`/`title`,
  `screenshot_artifact`) increments `version` and publishes; the durable row is
  never permanently newer than the last published version.
- [ ] Only the terminal closed-frame may commit while `stopping`; a surviving
  action task cannot append a frame behind the closed evidence.
- [ ] An expiry close re-verifies `lease_generation` inside the close lock, so a
  renewal that lands mid-flight aborts the close.
- [ ] A terminal ledger row (`UNCERTAIN`) cannot be rewritten by a late task.
- [ ] The backfill migration is transactional: a concurrent frame commit cannot
  cause `version`/`frame_seq` to regress, and frame documents are created (never
  overwritten).
- [ ] A credentialed context reaches only the portal and adapter-declared
  identity/verification origins; a cross-origin GET beacon from a hostile portal
  page is refused.

### Containment

- [ ] Browse, every credentialed/verification phase of fill, and render contexts
  all set `accept_downloads=False` before page creation. Credentialed work is
  visible in its owning fill RunView from before context creation.
- [ ] Credential/OTP fixture values do not appear in decoded frame pixels,
  artifact metadata, RunView, logs, or audits; non-secret phase progress remains
  visible.
- [ ] Popup fixtures for browse and fill leave exactly one page and return the
  specified refusal/blocker; no hidden page executes a request after closure.
- [ ] `confirm`, `prompt`, and `beforeunload` fixtures are never accepted;
  actions freeze and require a human. Alert dismissal is audited.
- [ ] Download fixtures create no file and return `policy_refused`.
- [ ] Founder Stop closes every nonterminal browse and fill idempotently; stopping fill performs
  no submit and no workflow transition.

### Projection and UI

- [ ] While `/wake` is still pending, a fixture browser action produces a frame
  event that appears in the Browser surface without a poll or second wake.
- [ ] Every event references committed Firestore state; forced event loss and
  process restart recover from the next snapshot. Forced upload failure commits
  `artifact=null`; forced transaction failure publishes nothing and leaves only
  a lifecycle-cleaned orphan object.
- [ ] Duplicate and out-of-order events/images cannot replace a newer frame.
- [ ] Subscriber overflow produces one resync and bounded memory; final state
  remains correct.
- [ ] EventSource closes on hidden tab/session change/logout and reconnects with
  one snapshot when visible. Cancellation/error paths remove subscribers and
  the configured stream horizon reconnects before the Cloud Run request timeout.
- [ ] Browser screenshots render only in the Browser surface; arbitrary remote
  content is never iframed.

### Expiry, security, and regression

- [ ] A stale expiry task is a no-op after lease renewal; the matching task
  closes the context and records `closed/expired` once. Expiry runs on the
  dedicated queue and cannot be blocked by agent-wake tasks.
- [ ] `check_browser_invariants.py` passes the repository and fails synthetic
  fixtures for every forbidden external-launch construct.
- [ ] Production code contains one literal `headless=True` launch site and no
  headed/configurable/attach/system-profile path.
- [ ] Chat links, URL-bar requests, and OAuth setup preserve the no-new-window
  invariant; human OAuth current-tab redirect is covered as the explicit
  first-party-auth boundary.
- [ ] Existing 09/12/18 approval, SSRF, injection, bot-challenge, staleness,
  action-budget, and idempotency suites remain green.
- [ ] Restart reconciliation terminalizes orphaned `opening`, `active`,
  `blocked`, and `stopping` runs. Stop during an action leaves its ledger row
  `UNCERTAIN/stopped` and never replays it.
- [ ] Stop/reopen during `FORM_FILLING` refills before requesting approval;
  stop/reopen from `AWAITING_SUBMIT_APPROVAL` can consume an existing grant only
  after the portal hash and intended mapping are unchanged.
- [ ] GCS lifecycle expires `browserframe_*.jpg` after seven days while milestone
  evidence follows the application-artifact lifecycle.
