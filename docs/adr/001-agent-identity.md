# ADR 001 — Agent Identity: Persona, Email, and Social Presence

**Status:** Accepted; **v2 mailbox implemented 2026-08-20** (`services/alex_mailbox.py`,
`/webhooks/alex_mail`, tools `check_alex_inbox` / `send_alex_email`)
**Date:** 2026-08-20
**Context:** Ruhu, Inc. (org + venture), domain `ruhu.ai`

## Decision

Co-Founder (the app) runs an agent with a **named persona, Alex**, that holds a
**functional identity inside the venture** — a role mailbox on the venture's
domain — and **no personal identity anywhere** (no LinkedIn, no social profiles).

| Layer | Decision |
|---|---|
| Product name | **Co-Founder** (app / Devpost submission name) |
| Agent persona | **Alex** — a per-venture config value, default "Alex", never hardcoded in core modules (injected into instructions like `{current_step}`) |
| Email | `alex@ruhu.ai` — Google Workspace role mailbox on the **venture's** domain; `cofounder@ruhu.ai` as alias. Founder retains full access; it is a company asset the agent operates |
| Signature | Always discloses: "Alex — AI co-founder, Ruhu". Transparency is a feature, never impersonate a human |
| LinkedIn / social | **None, in any version.** Bot accounts violate LinkedIn's UA outright; outreach trust depends on the founder's real identity. Future social workflows: agent drafts, founder posts from their own account |

## Design rule (the durable invariant)

> **Functional identity, yes — personal identity, no.**
> The agent may operate company functions (role mailboxes, shared inboxes) the
> way a human co-founder would. It never claims personhood.

And the scaling corollary:

> **Mailboxes live on the venture's domain, never the vendor's.**
> Multi-tenant future = one role mailbox per customer venture
> (`alex@their-venture.com`), provisioned at onboarding. Same code, per-venture
> config — persona and mailbox belong in workflow/venture config, not core code.

## Why (alternatives considered)

- **Agent-owned consumer account (e.g. a bot Gmail):** gray-zone ToS, no PII
  separation story, weaker than a Workspace role mailbox on our own domain.
  Rejected.
- **Founder's personal mailbox only (status quo v1):** works, keeps PII scoped
  via `gmail.readonly` + one label, but leaves `FOLLOW_UP` dependent on founder
  forwarding and gives the agent no channel of its own for correspondence.
  Kept for v1; superseded by the role mailbox in v2.
- **Agent LinkedIn:** explicit ToS prohibition + destroys the trust that makes
  outreach work + zero v1 use case. Rejected permanently.

## v1 vs v2 split

**v1 (hackathon, unchanged):** founder-account Gmail connector,
`gmail.readonly`, one watched label. No agent-sent email
(`docs/09-form-filler.md` §boundaries). Document-surface programs get the
rendered application pack; the founder sends it.

**v2 (role mailbox) — implemented:**
- Inbound: Gmail API `watch` on `alex@ruhu.ai` → Pub/Sub → `POST /webhooks/alex_mail`
  → history-based fetch → classified events → follow-ups attached to matching
  inflight applications → the founder's session is woken and Alex reports.
  This closes the `FOLLOW_UP` loop (confirmations, interview invites, results)
  with real signals instead of founder forwarding. Manual fallback:
  `POST /tasks/alex_mail_scan` and the pane's Scan-now button.
- **Extraction-only:** email bodies are untrusted input, treated exactly like
  web pages — parsed to structured events, never followed as instructions.
- Outbound: `send_email` rides the same approval gate as submission
  (`approval_service`, gate `send_email`) — a GRANTED, unexpired, unconsumed
  approval resolved server-side; consuming it is the idempotency key.
- Deliverability: SPF/DKIM/DMARC configured day one; correspondence volume
  only, never bulk. The agent mailbox never replaces the founder's address as
  *applicant contact* on application forms — applicant identity is contractual.
- **Manual prerequisites (not code):** create the `alex` Workspace user in the
  ruhu.ai admin console; create the Pub/Sub topic + push subscription to
  `/webhooks/alex_mail`; set `ALEX_MAIL_PUBSUB_TOPIC` / `ALEX_MAIL_WEBHOOK_TOKEN`;
  connect via Connectors → Alex's Mailbox, signing in **as alex@ruhu.ai**.
  Gmail watch expires after 7 days — re-arm on the deadline tick.

## Consequences

- Demo narrative gains a beat: "watch the program's email land in Alex's inbox
  and the board update itself" (v2; v1 demo narrates the founder-label version).
- Seed data and demo assets should tell one coherent story: venture = Ruhu,
  Inc., persona = Alex, mailbox = alex@ruhu.ai.
- Prompt-injection surface grows when the agent reads its own inbox; the
  extraction-only rule and tool scoping (12-security) extend to mail parsing.
