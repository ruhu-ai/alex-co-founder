# 17 — Real Portal Access: registration + sign-in

The form-filler can navigate, map, fill, and submit on approved portals (09).
This spec adds the authentication layer for **registering and signing in on
real program portals**, with the Gmail connector closing the
email-verification loop.

## Boundaries (binding)

1. **Alex acts under its own identity.** Accounts are created as
   alex@ruhu.ai — the agent's own delegated identity, not the founder's.
   Registration is **approval-gated** (gate `create_portal_account`:
   GRANTED, unexpired, single-use, server-resolved — the same token flow as
   12) because account creation is an irreversible external act; sign-in to
   an existing account is **ungated** (delegated autonomy). Every account
   creation is **audited** — "where does Alex have accounts?" is always
   answerable. The submission gate (docs/09, 12) is unaffected and stays.
2. **Email+password and SSO both allowed.** SSO ("Continue with Google") runs
   through Alex's own Google identity (alex@ruhu.ai OAuth) only when it remains
   in the projected primary page and adapter-declared origins. A popup handoff
   is closed and becomes `needs_human` under 22. No improvisation with the
   founder's personal accounts.
3. **Never defeat bot protection.** CAPTCHA / anti-bot challenge → stop,
   screenshot, hand to the founder. This boundary stays: bypassing CAPTCHAs
   violates portal ToS and anti-abuse norms, and endangers every relationship
   the agent is building. Alex identifies itself honestly if a form asks.
4. **Credentials live in Secret Manager.** Not a restriction — pure hygiene:
   per-portal password generated at registration, stored as
   `portal-cred-{host}`, fetched by name at execution. Never in session state,
   prompts, logs, or artifacts.
5. **Authentication is foreground browser work.** Registration, sign-in,
   credential entry, and verification are phases of the application-linked
   `kind="fill"` BrowserRun from 22. The RunView exists before context creation
   and remains visible in the in-app Browser surface; no separate invisible
   credentialed/verification context or external OS browser is permitted.

## The email verification loop

Registration usually gates on "click the link we emailed you." The Gmail
connector (label-scoped, read-only) closes it:

```
register_account(portal)
  → approval gate (founder grants in UI; gate create_portal_account)
  → fill signup form (founder identity from profile, generated password)
  → arm verification wait: pending_signals += ["portal_verification"], agent
    goes dormant (principle 3 — no polling)
  → Gmail push notification (Pub/Sub watch on the watched label) wakes the
    session via state_delta; a scheduled verification_timeout event (15 min)
    is the backstop wake
  → extract verification link/code → open link / enter code
  → store credential in Secret Manager → audit → continue to map_form_requirements
```

## New pieces

- `gmail_adapter.check_verification(query)` — event-driven, never a poll loop
  (dormancy, principle 3): a Gmail push watch on the watched label (Pub/Sub)
  wakes the session when mail arrives; the adapter then reads the matching
  unread message and returns the parsed body/link/code. A scheduled
  `verification_timeout` event (15 min) is the backstop wake → `needs_human`.
  Never reads outside the label.
- `services/portal_accounts.py` — credential lifecycle: generate (secrets
  module), store (Secret Manager), fetch by host. Errors as data.
- Form-filler tools: `register_account(portal_url)` — **approval-gated**
  (`create_portal_account`); and `sign_in(portal_url)` — ungated (Alex's own
  delegated identity), every sign-in **audited**; both behind the existing
  staleness fence; every step emits the ordered JPEG frames from 22, with PNG
  reserved for blocked/final milestones.
- Provider-neutral in-process fixtures simulate signup and verification email
  so the contract remains testable offline without shipping a demo service.

## After auth

The existing pipeline is unchanged: `map_form_requirements` (DOM + vision
recon) → gap analysis → fill from approved answers → staleness fence →
approval-gated submit. Real portals simply become reachable.

## Acceptance checks

- [ ] every registration writes an audit row (actor `agent:form_filler`, target host)
- [ ] `register_account` without a GRANTED `create_portal_account` approval refuses with an error dict + audit `refused`; `sign_in` needs no approval but is always audited
- [ ] provider-neutral integration loop: register → verification email → verified → signed in → form mapped
- [ ] password never appears in state, logs, chat, or artifacts (grep test)
- [ ] CAPTCHA/SSO-via-founder-account → clean blocker report, no retry cleverness
- [ ] verification wait creates no polling loop: wake arrives via Gmail push or the scheduled `verification_timeout` event; timeout → `needs_human` error-as-data
- [ ] registration/sign-in/verification share one application-linked foreground
  fill RunView; the Browser surface shows non-secret phase progress and no path
  opens an external browser window
- [ ] unit tests for credential store + email parsing; eval case for the flow
