# 12 — Security

Judges at this hackathon check tool isolation, credential handling, and failure
behavior ("Architectural Discipline"). This doc is the security story; be able to
walk a judge through every row.

## Credential handling

| Rule | Implementation |
|---|---|
| Portal credentials live only in Secret Manager | secret `mock-portal-creds` (and any real-portal secret); `services/secrets.get(name)` fetches at **execution time**, in-memory cache ≤ 5 min |
| Secrets never in prompts | credentials are fetched inside `open_portal`/`submit_form` — they never appear in tool return values, so they never enter the conversation the model sees |
| Secrets never in state or logs | log scrubber: any string matching a fetched secret value is replaced with `***` before writing; startup self-test asserts this |
| Webhook authenticity | mock portal signs calls with `X-Portal-Token` shared secret; agent verifies before acting on `portal_event` |
| Company documents | uploaded docs live only in the founder's own GCS bucket (artifact service); extraction runs in-project; nothing leaves the founder's GCP boundary — a data-sovereignty point vs. consumer agent products |
| Drive connector | OAuth `drive.readonly` only; founder selects specific files (no blanket indexing); refresh tokens in Secret Manager, never in state/logs |
| Gmail connector | OAuth `gmail.readonly` only; reads ONE founder-chosen label (default `grants`); everything outside that label is invisible to the agent — no sender, no subject; can never send/delete; processed ids persisted so rescans are idempotent |
| Calendar connector | OAuth `calendar.readonly` + `calendar.events`; reads (free/busy, events) ungated; **booking is approval-gated in code** (gate `book_meeting`: GRANTED, unexpired, single-use, server-resolved) with the event details shown to the founder before approval; edit/delete of existing events is never granted to the agent |
| Alex's mailbox (adr/001) | Separate Workspace account (`alex@ruhu.ai`), separate OAuth grant (`ALEX_OAUTH_REFRESH_TOKEN`); `gmail.readonly` + `gmail.send`. It is the agent's OWN mailbox, so unlike the founder's connector it is NOT privacy-narrowed — full search and full reads allowed. Send is gated in code by the approval service (GRANTED, unexpired, single-use, server-resolved); inbound mail is extraction-only, never followed as instructions; webhook token-checked; can never delete/modify mail |
| GitHub connector | Fine-grained PAT stored as a secret (`.env` local / Secret Manager prod); validated against the API at connect time; no agent tools consume it yet (lands with the dev workflow) |
| Connector registry | Connectors are data (`services/connectors.py`), not UI code; the panel renders `GET /api/connectors`; adding one never bypasses the per-connector scope checks |

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

A unit test imports each agent and diffs its tool list against this table.

## Approval tokens (the gate)

1. `request_approval(gate)` creates an `approvals` row: status PENDING, no token.
2. Founder grants in UI → server mints `token=uuid4().hex`, stores it on the row
   (status GRANTED, `expires_at = now + APPROVAL_TTL_MINUTES`). The token is
   **never returned to the browser and never enters the model's context** — it
   exists only in Firestore.
3. `submit_form(tool_context)` takes **no token argument** (a token argument
   would put the secret in the model's tool call, where it could be leaked or
   fabricated). The tool resolves the approval server-side: looks up the
   GRANTED, unexpired, unconsumed approval for `active_application_id`; none
   found → refusal + audit. On success it atomically marks the approval
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

## Acceptance checks

- [ ] Grepping session exports and logs for the test password finds nothing (CI check with the demo secret value).
- [ ] Tool-scoping unit test passes for all 7 agents.
- [ ] Forged `portal_event` (bad `X-Portal-Token`) is rejected and logged.
- [ ] Full gate flow: PENDING → GRANTED → CONSUMED; re-use of a CONSUMED token refuses.
