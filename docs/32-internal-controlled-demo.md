# 32 — Internal Controlled Demo Lane

**Status: implemented but disabled by default; requires its separate review and
local provider proof before recording.** This is not H4S and is not H4. It exists solely to record a bounded product demo
using Ruhu-controlled operational accounts. It must never be called a sandbox,
a hiring pilot, or evidence that live hiring is enabled.

## Purpose

The demo may show a real email and a real Calendar invitation between accounts
controlled by the founder. Its audience sees actual provider receipts while
H4S remains the isolated test-account engineering environment.

## Closed scope

The only permitted identities are the deployment-pinned accounts:

| Role | Address | Permitted use |
|---|---|---|
| Founder recipient | `ijidai@ruhu.ai` | Receive a bounded internal-demo recap or invitation |
| Alex sender | `alex@ruhu.ai` | Send a bounded internal-demo recap; own the demo Calendar event |

No aliases, display-name matching, free-text address, normal connector
selection, forwarding address, group, or attendee is accepted. The provider
subject for each account is pinned in deployment configuration and verified
immediately before an action. The deployment may not point either subject at a
different address without a new policy version and review.

The lane permits only:

1. one founder-initiated import of the predeclared synthetic FDE application
   from `ijidai.lassa@gmail.com` to `alex@ruhu.ai`, with an exact subject,
   body marker, attachment filename, and deployment-pinned SHA-256; the
   import reads no other message, attachment, or mailbox history;
2. creation of the synthetic candidate case and Evidence Passport for that
   exact fixture email, under the pre-approved synthetic Ruhu FDE role only;
3. a static, fixture-marked product recap from Alex to Ijidai;
4. a static, fixture-marked Calendar demonstration invite with Alex and Ijidai;
5. an exact-approved update or cancellation of that same demo event; and
6. provider receipt and reconciliation display.

It does **not** permit a general Gmail search or Gmail watch, reply processing,
arbitrary candidate data, arbitrary résumés, generic job applications,
interview attendance, Browser automation, Drive access, public
recipients, or a generic agent send/calendar tool. The single synthetic PDF
fixture is an exception only after its sender, recipient, subject, body marker,
filename, and SHA-256 all match the deployment policy; content is treated as
untrusted data and never as instructions.

## Separate authority and data boundary

Every record is marked `internal_demo=true`, `fixture_id`,
`demo_namespace=internal_demo_ruhu`, and `demo_run_id`. It uses a separate
policy, approval domain, action kinds, ledger context, UI badge, audit action,
and reset receipt. H4S actions cannot reconcile here and these actions cannot
reconcile through H4S or generic connector code.

The exact action contains an immutable template version, canonical rendered
content hash, recipients/attendees, action kind, provider subject hashes,
timezone, and policy id. The founder must grant a single-use server-bound
approval after viewing the exact text/event. The assistant/chat/voice surface
can only open the review UI; it cannot approve, execute, or choose recipients.

## Gates

All must be true before a provider call:

```text
HIRING_ENABLE_INTERNAL_CONTROLLED_DEMO == 1
AND internal-controlled-demo policy == INTERNAL_DEMO_ONLY
AND deployment-pinned account subject + address exactly match
AND fixture-marked demo run is ACTIVE and inside its expiry window
AND action template is one of the closed allowlist
AND exact internal-demo approval is GRANTED and unconsumed
AND ledger row is PREPARED and leased
```

The feature is disabled by default. It does not alter H0, H4S, qualified-review
status, or live-hiring activation. Kill switch, expiry, ambiguous provider
result, and any recipient/account mismatch fail closed before a provider call.

## Recording/reset

The demo recording must show the **LIVE INTERNAL DEMO** badge before an action,
the exact approval review, the resulting Gmail/Calendar receipt, and the
post-run reset receipt. Reset can revoke the demo run and cancel only events
whose provider event id and private marker are owned by that run. It cannot
delete Gmail messages and must say so on-screen.

## Acceptance checks

- [ ] Both ordinary account identities are exact deployment pins; no browser,
      model, request, or normal connection projection can select an account.
- [ ] Static demo templates cannot include candidate data or arbitrary text.
- [ ] Every email/event/update/cancel is exact-approved, idempotent, audited,
      receipt-backed, and reconciled when uncertain.
- [ ] Any address, subject, account, fixture, expiry, approval, or action drift
      refuses before the provider call.
- [ ] H4S stays test-account-only and H0/H4 live hiring remains disabled.
- [ ] The UI never describes internal-demo effects as sandbox or hiring work.
