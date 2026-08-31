# 09 — Form filling and real provider boundaries

Co-Founder can prepare approved application data for a real provider through
either browser automation or a provider's A2A endpoint. The repository does
not bundle or deploy a demonstration provider. Deterministic provider fixtures
belong in tests and never become runtime authority.

## Safety contract

1. Durable workflow, application, answer, and approval records authorize every
   operation. Chat history never does.
2. Browser access is allowed only after the Founder explicitly activates the
   application and the portal host passes the G3 policy check.
3. Provider credentials are bound to an exact host and fetched by secret name
   at execution time. No default username or password exists.
4. Form filling is preparation, not submission. The agent must stop before the
   irreversible submit action.
5. Submission requires one fresh, exact, server-resolved approval token bound
   to the application, provider, form hash, and intended action.
6. Every provider action is idempotent and audited. An uncertain provider
   result blocks blind retry and requires reconciliation.
7. If a provider disallows automation, changes its form, presents an unsafe
   redirect, or cannot be mapped confidently, the operation fails closed.

## Provider adapters

The generic browser adapter supports real HTTPS application portals that pass
the SSRF and provider-policy checks. A provider may instead expose a compatible
A2A endpoint; the same durable application and approval constraints apply.

Provider-specific business logic does not belong in the core workflow. An
adapter may translate between a provider's field names and the canonical form
map, but it cannot weaken approvals, invent answers, or authorize submission.

## Browser tiers

### Tier 0 — deterministic DOM mapping

The browser worker inspects labels, names, ARIA metadata, input types, and
option values. It maps only fields it can ground to approved durable answers.
Unknown or ambiguous fields remain unresolved for Founder review.

### Tier 1 — bounded visual reconciliation

When DOM metadata is insufficient, the worker may use a screenshot and the
vision model to propose a field mapping. The proposal is validated against the
live DOM and existing approved answers before use. Visual inference never
authorizes submission.

## Durable browser session

Each active application can bind one provider host, encrypted credential
reference, browser-session reference, and current form fingerprint. Restart
recovery may reuse those exact records. If credentials or a valid session are
missing, recovery returns `portal_credentials_unavailable`; it never falls
back to built-in demo credentials.

## Fill report

Before submission the Founder sees an inspectable report containing:

- provider and exact HTTPS host;
- form fingerprint and capture time;
- fields filled from approved answers;
- missing, ambiguous, or rejected fields;
- uploaded artifact references;
- policy or provider warnings; and
- the exact action awaiting approval.

Changing an answer, artifact, provider form, or application version makes the
report stale. A stale report cannot be submitted.

## Submission and reconciliation

`submit_form()` verifies the current application state, form fingerprint,
fresh approval, provider binding, and idempotency key before the browser clicks
submit. It records a PREPARED action before the provider call and commits the
provider confirmation only after a definite result.

If the browser or provider times out after the action may have committed, the
result becomes UNCERTAIN. Reconciliation must read a provider confirmation or
durable event before the workflow can continue; automatic resubmission is
forbidden.

## Local and automated verification

Tests use in-process provider-neutral FastAPI/A2A fixtures and deterministic
browser pages. They may simulate login, dynamic fields, upload, confirmation,
duplicate submission, and uncertain responses without shipping a public demo
service or credentials.

## Acceptance checks

- [ ] A real-provider host must pass G3 policy and SSRF checks before access.
- [ ] No runtime path contains built-in provider credentials or a bundled demo
      provider URL.
- [ ] DOM mapping fills only fields grounded in approved durable answers.
- [ ] Ambiguous and unsupported fields are reported, not guessed.
- [ ] Submission without a fresh, exact approval is rejected and audited.
- [ ] Reusing an idempotency key returns the original definite result.
- [ ] An uncertain result blocks retry until read-only reconciliation.
- [ ] Deterministic test fixtures exercise browser and A2A contracts without
      being deployed as product services.
