# 29 — Independent H0–H3 implementation review

**Verdict: ACCEPT FOR SYNTHETIC DEMONSTRATION ONLY.** The implemented H0–H3
foundation conforms to the safety boundaries reviewed here after the corrective
change below. It is not authorized for a pilot, live candidate data, live
connector input, external browser effects, or H4.

## Scope and method

This review traced the H0–H3 contract in
[doc 25](25-hiring-operations.md) through its route guards, durable state,
domain services, browser hand-off, and deterministic tests. It deliberately
focused on the failure paths most likely to turn a hiring demo into an unsafe
system: identity isolation, deletion, mailbox recovery, approval binding, and
browser containment.

## Findings and disposition

| Area | Result | Evidence and disposition |
|---|---|---|
| Identity isolation | PASS | Request identity is resolved from a signed membership; hiring identity, application identity, and restricted evidence use separate scopes. Analyst products use pseudonymous, role-scoped inputs, and restricted hiring data is excluded from general knowledge/search resources. |
| Deletion and data rights | PASS — controlled | Export and deletion dry-run require recent authentication, bind an exact inventory hash, record provider limitations, and refuse under legal hold. The demo exercises a dry-run only; no deletion is executed. |
| Mailbox recovery | PASS — synthetic only | Binding activation requires both positive-delivery and forged-header-negative probes. Recoverable batches persist a manifest and entries before cursor advancement, so restart/retry does not silently omit or replay mail. Real mailbox connectors remain disabled. |
| Approval binding | PASS | Durable approvals bind workspace, actor, run, policy version, exact action, and subject hash. A changed role/policy or action cannot consume an old approval. No approval itself enables a disabled external effect. |
| Browser containment | PASS | Publication links are handed to the app-owned embedded Browser route; the hiring UI has no external navigation or `window.open` path. This supports founder-controlled, audited manual publication only—not browser automation. |
| Synthetic fixture provenance | FIXED | Review found that writes carried the older `synthetic`/`fixture_id` names but not the explicit H0–H3 aliases expected by the reviewed fixture contract. The durable store now normalizes every synthetic write with `is_synthetic=true` and `fixture_set_id=<fixture_id>`; a regression test covers create/put/CAS paths. |

## Verification

The repository interpreter was repaired with the pinned Firestore client:

```text
google-cloud-firestore 2.28.1
```

Verification completed as follows:

- `python -m pytest -q --ignore=tests/unit/test_browser_redaction.py` — **898
  passed, 2 skipped**.
- `tests/unit/test_browser_redaction.py` — **10 passed** in
  `mcr.microsoft.com/playwright/python:v1.62.0-jammy`, the exact Playwright
  base pinned by the Dockerfile. This separate execution was necessary because
  the developer desktop retained orphaned Playwright headless-shell processes
  that prevented browser shutdown; it is not a code skip or a relaxation of
  the redaction assertion.
- H0–H3, containment, compile, contrast, and diff checks — **61 passed** plus
  `scripts/check_contrast.py` and `git diff --check` clean.

Use the following baseline command in a healthy local process environment:

```bash
python -m pip install 'google-cloud-firestore==2.28.1'
python -m pytest -q
python scripts/check_contrast.py
```

The container result closes the desktop browser-runtime gap while keeping the
pixel-level redaction test mandatory.

## Explicit non-authorization

Qualified employment, privacy, accessibility, records-retention, destination
terms, and security reviews remain externally pending in
[doc 28](28-hiring-qualified-review-packet.md). Until the accepted constraints
are encoded and independently re-reviewed, all live hiring inputs and effects
remain disabled.

H4 is **not authorized**. It requires a separate reviewed implementation goal
covering approved outreach, reply correlation, scheduling, Calendar/email
receipts, reconciliation, and its own operational/legal review. It must never
be enabled as an incremental configuration change to H0–H3.
