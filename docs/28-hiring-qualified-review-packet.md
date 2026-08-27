# 28 — Ruhu hiring qualified-review packet

**Status: pending external qualified sign-off.** This packet is not legal,
employment, privacy, accessibility, records-management, or destination-terms
advice. It is the evidence and decision record a qualified reviewer needs
before any live candidate data or H4 effect can be considered.

## Product and deployment boundary under review

Ruhu’s **Forward Deployment Engineer** workflow is limited to H0–H3 synthetic
fixtures. It is non-scoring and non-ranking. Human founders make every
employment decision. Live candidate intake, Gmail/Calendar writes, LinkedIn
automation, automated public verification, and H4–H7 are disabled in code.

## Reviewer inputs

- [Hiring operations contract](25-hiring-operations.md), especially sections
  7, 9–10, 16, 19–21.
- [H0–H3 engineering verification](26-hiring-h0-h3-verification.md).
- [Ruhu FDE walkthrough](27-ruhu-fde-walkthrough.md).
- `policies/hiring/h0-policy.json`, the deployment gate that keeps production
  disabled while these review fields remain `PENDING`.

## Required decisions

| Review owner | Decision required before any pilot | Evidence to assess | Status |
|---|---|---|---|
| Employment/HR counsel | Applicable jurisdictions; permitted criteria/reason codes; notice, accommodation, adverse-action, retention, audit, and human-review requirements | Role Contract, prohibited criteria, Evidence Passport, human decision/withdrawal flows | PENDING |
| Privacy counsel/DPO | Lawful basis, notices, processor roles, international transfer, data-subject export/deletion workflow, legal holds, retention/deletion schedule, and incident obligations | Identity vault, restricted scope, export/deletion/hold evidence | PENDING |
| Accessibility specialist | WCAG target and testing; accommodation contact; keyboard/screen-reader review of founder and public-notice surfaces | Hiring cockpit, notice page, contrast results | PENDING |
| Records/retention owner | Approved retention periods, legal-hold process, deletion exceptions, audit retention, and restoration/recovery policy | Data-rights service and durable receipts | PENDING |
| Destination-terms owner | Whether any public verification or future official provider API use is permitted; explicit ban/conditions for LinkedIn automation | Manual publication receipt and Browser containment evidence | PENDING |
| Security owner | Authentication freshness, workspace authorization, task identity, credential broker, incident response, and kill-switch operation | H1 security tests and deployment configuration | PENDING |

## Required reviewer outputs

Each reviewer must provide a dated, attributable record containing:

1. scope and jurisdiction(s) reviewed;
2. accepted/rejected decisions and operational constraints;
3. required policy identifiers and retention periods;
4. unresolved risks and pilot blockers;
5. expiry/re-review date; and
6. the named accountable Ruhu owner.

Engineering may then encode only the accepted constraints in a new, versioned
policy. A reviewer statement, email, or chat message alone cannot enable an
effect; the deployment gate, Role Contract, connector policy, and exact action
approval must all still pass.

## H4 decision boundary

Do **not** authorize H4 from this packet by default. A separate H4 engineering
goal and review are required for:

- approved candidate outreach and causal reply correlation;
- Calendar availability/invite action receipts and reconciliation;
- DSN/automatic-reply handling; and
- interview artifacts and scorecard permissions.

No reviewed sign-off may be interpreted as permission for auto-decline,
ranking, browser automation, unsupervised outreach, or any other effect outside
the explicit approved scope.
