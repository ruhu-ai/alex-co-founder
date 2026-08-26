# 12 — Security

Judges at this hackathon check tool isolation, credential handling, and failure
behavior ("Architectural Discipline"). This doc is the security story; be able to
walk a judge through every row.

## Credential handling

| Rule | Implementation |
|---|---|
| Portal credentials live only in Secret Manager | secret `mock-portal-creds` (and any real-portal secret); `services/secrets.get(name)` fetches at **execution time**, in-memory cache ≤ 5 min |
| Secrets never in prompts | credentials are fetched inside `open_portal`/`submit_form` — they never appear in tool return values, so they never enter the conversation the model sees |
| Secrets never in state or logs | log scrubber is a `logging.Filter` (in `services/`) installed on the logging path when the first secret is fetched; every value returned by `services/secrets.get` is registered with it, and the filter rewrites any registered secret value to `***` in a log record before it is emitted |
| Webhook authenticity | mock portal signs calls with `X-Portal-Token` shared secret; agent verifies before acting on `portal_event` |
| Company documents | uploaded docs live only in the founder's own GCS bucket. Extension/MIME are not trusted: PDF structure and bounded OOXML archives are validated before storage. Native parsers retain citations; legacy Office, encryption, traversal, links, duplicate members, expansion bombs, and malformed content fail closed. Contents are model data, never instructions/approval, and are not sent to the Evidence Checker. |
| Drive connector | OAuth `drive.readonly` only; founder selects specific files (no blanket indexing); refresh tokens minted in-app persist to Secret Manager on Cloud Run / to `.env` locally, never to state or logs |
| Gmail connector | OAuth `gmail.readonly` only; reads ONE founder-chosen label (default `grants`); everything outside that label is invisible to the agent — no sender, no subject; can never send/delete; processed ids persisted so rescans are idempotent |
| Calendar connector | OAuth `calendar.readonly` + `calendar.events`; reads (free/busy, events) ungated; **booking is approval-gated in code** (gate `book_meeting`: GRANTED, unexpired, single-use, server-resolved) with the event details shown to the founder before approval; edit/delete of existing events is never granted to the agent |
| Alex's mailbox (adr/001) | Separate Workspace account (`alex@ruhu.ai`), separate OAuth grant (`ALEX_OAUTH_REFRESH_TOKEN`); `gmail.readonly` + `gmail.send`. It is the agent's OWN mailbox, so unlike the founder's connector it is NOT privacy-narrowed — full search and full reads allowed. Send is gated in code by the approval service (GRANTED, unexpired, single-use, server-resolved); inbound mail is extraction-only, never followed as instructions; webhook token-checked; can never delete/modify mail |
| GitHub connector | Fine-grained PAT stored as a secret (`.env` locally / Secret Manager only on Cloud Run); validated against the API at connect time; no agent tools consume it yet (lands with the dev workflow) |
| Gemma Evidence Checker (20) | Calls managed Vertex MaaS using project ADC. Google Cloud does not use customer data for model training without permission, but abuse-monitoring retention and project-level in-memory caching can apply; zero retention requires the documented Google Cloud controls. This model processes in the `us` multi-region. Pack contents are allowlisted and bounded (draft text, relevant profile facts and canonical answers, interview answers, allowlisted programme fields); raw documents, connector payloads, browser content, chat history and secrets are never included. |
| Connector registry | Connectors are data (`services/connectors.py`), not UI code; the panel renders `GET /api/connectors`; adding one never bypasses the per-connector scope checks |

The Evidence Checker statements above follow Google's official
[Vertex MaaS model card](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/google/gemma-4-26b-a4b-it)
and [Google Cloud data-governance guidance](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/zero-data-retention).

## Founder sign-in (app-layer auth)

Cloud Run stays `--allow-unauthenticated` (webhooks/Pub/Sub need in), so the
app gates itself (`app/auth.py`). Two credentials are accepted; either makes
the request "the founder":

| Path | Mechanism | Notes |
|---|---|---|
| Access key (ops/e2e) | `APP_AUTH_TOKEN` via `/?key=` bootstrap → HttpOnly cookie, or Bearer/`X-App-Key` | unchanged; what scripts and curl use |
| Email sign-in (founder) | `/login.html` → Firebase Auth email/password → ID token POSTed to `/auth/session` → server verifies against Google certs (`google-auth`, no Admin SDK), then mints its own signed HttpOnly cookie (HMAC, 14 d) | client never becomes trusted: the server re-verifies the ID token and re-checks policy |
| Google sign-in (founder) | current-tab `/auth/google/start` → Google web-server authorization-code flow with state + PKCE + OIDC nonce → `/auth/google/callback` exchanges the one-time code, verifies the ID token, and mints the same signed cookie | no popup, refresh-token persistence, or cross-origin Firebase redirect storage; verified email + allowlist remain mandatory |

Policy enforced in `/auth/session`, all server-side:

- **`email_verified` required** — otherwise anyone could register an
  allowlisted address with their own password and impersonate the founder
  before the real owner signs up.
- **`ALLOWED_LOGIN_EMAILS` allowlist** — the set of people allowed to act as
  the founder (single-tenant: every login maps to `FOUNDER_ID`). Empty
  allowlist: production rejects all logins (fail closed), local dev accepts
  any verified account.
- **Cookie is signed, not a lookup** — `APP_SESSION_SECRET` (falls back to
  `APP_AUTH_TOKEN`) HMACs `{email, name, exp}`; tamper or expiry ⇒ 401. No
  server-side session table to leak or scale.
- Unauthorized **browser navigations** redirect to `/login.html`; API calls
  get a bare 401. `/auth/*` and `/login.html` are the only new exempt routes.
- The Firebase **web API key is a public identifier**, not a secret; the gate
  is the server-side verification + allowlist.

## Tool scoping matrix (enforce in code, assert in tests)

| Capability | orch. | scout | match. | interv. | drafter | form-filler | distiller |
|---|---|---|---|---|---|---|---|
| read pipeline | ✓ | | ✓ | ✓ | ✓ | | |
| write opportunities | | ✓ | ✓ | | | | |
| write profile facts | | | | ✓ | | | ✓ (via apply_profile_update) |
| draft sections | | | | | ✓ | | |
| browser / portal | | | | | | ✓ | |
| general browsing (`browse.*`, 18) | ✓ | | | | | | |
| request approval | | | | | | ✓ | |
| submit (token-gated) | | | | | | ✓ | |
| record feedback | ✓ | | | | | | |
| write applications | ✓ | | | ✓ | ✓ | ✓ | |
| transition application state | ✓ | | | ✓ | ✓ | ✓ | |
| search active attachments | ✓ | | | ✓ | ✓ | | |

A unit test imports each agent and diffs its tool list against this table.

## Browse policy (18) — enforced in code, not prompts

| Surface | Policy |
|---|---|
| Network | Browse contexts allow GET/HEAD only via request interception (covers redirects, JS nav, iframes, subresources); SSRF denial of loopback/private/link-local/reserved/metadata IPs incl. decimal/hex IPv4 forms; fail-closed unless `BROWSE_OPEN_WEB=true`; deny-list wins |
| Content | Isolated reader/proposer invocations (no tools, no conversation contents, untrusted-content delimiters, strict JSON out); suspected injection **suspends actions** (`injection_suspected` + `needs_human`), never just annotates |
| Action | Click-through browsing (links **and** buttons; submit-semantics excluded, form fields never clicked — search boxes excepted — downloads/popups blocked); a click revealing a form/auth/payment surface freezes further actions; indexed-element + `dom_hash` revalidation before execution; idempotent `action_id`s; bot-challenge detector freezes runs |
| Secrets | No credentials ever enter browse contexts; portal credentials stay on the form-filler path (above) |
| Runtime | One single-flight Playwright-owned Chromium process with literal `headless=True`; no headed, external launcher, personal profile, persistent context, CDP attach, or configurable executable path. One foreground run per session; all contexts share popup/dialog/download watchdogs and central ownership (22) |
| Observation | Authenticated, snapshot-first event projection renders screenshot artifacts only in the in-app Browser surface; remote pages are never iframed; monotonic versions reject stale frames. Human OAuth current-tab redirect is a separate first-party auth boundary |
| Runs | Budget keyed by opaque `run_id` (20 actions / 90 s against durable `deadline_at`); durable inactivity lease with generation-safe expiry; Firestore `browser_runs` is the single source of truth; crash-safe action ledger (PREPARED→…→UNCERTAIN); founder stop for browse/fill audited as `founder:<id>` |

## Approval tokens (the gate)

1. `request_approval(gate)` creates an `approvals` row: status PENDING, no token.
   Gates: `submit_application` (09), `create_portal_account` (17),
   `book_meeting` (§Credential handling, Calendar). A submission approval also
   stores `subject_hash = hash(application_id, portal_state_hash, mapping_hash)`
   from the authoritative fill report.
2. Founder grants in UI → server mints `token=uuid4().hex`, stores it on the row
   (status GRANTED, `expires_at = now + APPROVAL_TTL_MINUTES`). The token is
   **never returned to the browser and never enters the model's context** — it
   exists only in Firestore.
3. `submit_form(tool_context)` takes **no token argument** (a token argument
   would put the secret in the model's tool call, where it could be leaked or
   fabricated). The tool resolves the approval server-side: looks up the
   GRANTED, unexpired, unconsumed approval for `active_application_id`, then
   recomputes and compares `subject_hash` after the live staleness check; none or
   mismatch → refusal + audit. A mismatch expires the grant and requires a new
   fill report and approval. On success it atomically marks the approval
   CONSUMED. Judge answer to "could the model fabricate or leak the token?":
   it never sees one — there is nothing to fabricate or leak.
4. Deny → status DENIED; the agent must receive a new explicit grant to proceed.
5. Any validation failure → refusal + `audit` row `result=refused`.

## Audit trail (append-only)

Every external action writes an `audit` row (schema in 02): actor, action,
idempotency key, target, result, detail (≤ 500 chars, no PII beyond refs, no
secrets). State transitions from the table in 03 are also logged
(`action=state_transition`). The UI audit tail makes this judge-visible.

## PII & data minimization

- Founder PII lives only in `profiles` and `applications` docs; logs carry ids,
  not names/emails.
- Submission materials state the data sources used (program pages, PDFs, mock
  portal) — all public or self-hosted; no scraped personal data.
- Repo contains `.env.example` only; real `.env` is gitignored (verify in CI:
  `git ls-files | grep -x .env` must be empty).

## Failure behavior (be ready to demo these answers)

| Failure | Behavior |
|---|---|
| Portal page changed | staleness guard stops the run; report names changed fields; nothing guessed |
| Token expired | refusal; founder re-approves; no silent retry |
| Double-submit attempt | idempotency no-op with original confirmation id |
| Firestore down | tools return error dicts; agent tells founder and pauses; nothing half-written (writes are single-doc or batched) |
| Container killed mid-step | session resumes from Cloud SQL; state machine re-orients from `current_step` |
| Container killed during document extraction | Cloud Tasks redelivers; an expired lease is reclaimed; deterministic chunk ids and a generation pointer prevent a partial index from becoming visible |
| Unreadable/scanned/unsafe attachment | terminal `NO_TEXT`, `UNSUPPORTED`, or `FAILED`; no model call, profile mutation, summary, or optimistic UI success |

## Acceptance checks

- [ ] Grepping session exports and logs for the test password finds nothing (CI check with the demo secret value).
- [ ] Tool-scoping unit test passes for all 7 agents.
- [ ] Forged `portal_event` (bad `X-Portal-Token`) is rejected and logged.
- [ ] Full gate flow: PENDING → GRANTED → CONSUMED; re-use of a CONSUMED token refuses.
