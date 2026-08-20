# 17 — Real Portal Access: registration + sign-in

The form-filler can navigate, map, fill, and submit on portals (09) — but only
on the mock portal, because real portals sit behind auth. This spec adds the
auth layer: **registering and signing in on real program portals**, with the
Gmail connector closing the email-verification loop. Un-cuts 14 §cut-list #1.

## Boundaries (binding)

1. **Alex acts under its own identity.** Accounts are created as
   alex@ruhu.ai — the agent's own delegated identity, not the founder's.
   Registration is **ungated** (delegated autonomy), but every account
   creation is **audited** — "where does Alex have accounts?" is always
   answerable. The submission gate (docs/09, 12) is unaffected and stays.
2. **Email+password and SSO both allowed.** SSO ("Continue with Google") runs
   through Alex's own Google identity (alex@ruhu.ai OAuth). No improvisation
   with the founder's personal accounts.
3. **Never defeat bot protection.** CAPTCHA / anti-bot challenge → stop,
   screenshot, hand to the founder. This boundary stays: bypassing CAPTCHAs
   violates portal ToS and anti-abuse norms, and endangers every relationship
   the agent is building. Alex identifies itself honestly if a form asks.
4. **Credentials live in Secret Manager.** Not a restriction — pure hygiene:
   per-portal password generated at registration, stored as
   `portal-cred-{host}`, fetched by name at execution. Never in session state,
   prompts, logs, or artifacts.

## The email verification loop

Registration usually gates on "click the link we emailed you." The Gmail
connector (label-scoped, read-only) closes it:

```
register_account(portal)
  → approval gate (founder grants in UI)
  → fill signup form (founder identity from profile, generated password)
  → poll watched Gmail label: wait_for_email(from=portal domain, timeout=120s)
  → extract verification link/code → open link / enter code
  → store credential in Secret Manager → audit → continue to map_form_requirements
```

## New pieces

- `gmail_adapter.wait_for_email(query, timeout_s)` — polls the watched label
  until a matching unread message arrives; returns parsed body/link/code or a
  timeout error-dict. Never reads outside the label.
- `services/portal_accounts.py` — credential lifecycle: generate (secrets
  module), store (Secret Manager), fetch by host. Errors as data.
- Form-filler tools: `register_account(portal_url)` and `sign_in(portal_url)` —
  ungated (Alex's own delegated identity), every registration **audited**;
  both behind the existing staleness fence; every step screenshotted.
- Mock portal gains a signup + verification-email simulation page so the whole
  flow is testable end-to-end offline.

## After auth

The existing pipeline is unchanged: `map_form_requirements` (DOM + vision
recon) → gap analysis → fill from approved answers → staleness fence →
approval-gated submit. Real portals simply become reachable.

## Acceptance checks

- [ ] every registration writes an audit row (actor `agent:form_filler`, target host)
- [ ] full loop vs the mock portal's signup page: register → verification email → verified → signed in → form mapped
- [ ] password never appears in state, logs, chat, or artifacts (grep test)
- [ ] CAPTCHA/SSO-via-founder-account → clean blocker report, no retry cleverness
- [ ] `wait_for_email` timeout returns error-as-data; no infinite poll
- [ ] unit tests for credential store + email parsing; eval case for the flow
