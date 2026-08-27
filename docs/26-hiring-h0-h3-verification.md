# 26 — Hiring H0–H3 implementation verification

## Scope and activation verdict

The engineering foundation in doc 25 phases H0–H3 is implemented for reviewed
synthetic fixtures only. It cannot process a live Gmail application and cannot
write email, Calendar, LinkedIn, or a public-verification destination. The
deployment must explicitly set both `HIRING_ENABLE_SYNTHETIC_DEMO=1` and an
allowlisted `HIRING_SYNTHETIC_FIXTURE_IDS` value before even a synthetic source
record can enter through HTTP. Every stored hiring record retains the canonical
`synthetic`/`fixture_id` marker and the reviewed `is_synthetic`/
`fixture_set_id` aliases. Qualified policy reviews remain pending in
`policies/hiring/h0-policy.json`, which blocks any production pilot.

## H0 evidence

- `policies/hiring/h0-policy.json` is the closed, code-read policy for
  non-scoring/non-ranking behavior, pending qualified reviews, notice,
  retention, authentication freshness, prohibited outputs, and disabled
  effects.
- `services/hiring_activation.py` rejects non-synthetic records and all H0–H3
  email/Calendar/LinkedIn/public-verification effects as errors-as-data.
- `tests/eval/hiring/h3_case_manifest.json` records human-reviewed synthetic
  baseline-parity, hard-negative, false-activation, abstention, injection,
  delayed-resume, and duplicate-wake cases.

## H1 evidence

- `services/workflow_runtime.py` persists run, step, attempt, wait, and ordered
  event records with optimistic versions, generation-fenced leases, idempotent
  wakes, restart recovery, pause/resume, recursive parent-child cancellation,
  month-scale durable timers, and no polling.
- `services/actor_identity.py` resolves current membership from a signed auth
  subject; the legacy founder token/`FOUNDER_ID` cannot authorize hiring.
- `services/workload_identity.py` verifies an exact route audience, issuer,
  expiry, verified service account, and per-route allowlist before parsing task
  identifiers. `services/task_queue.py::enqueue_hiring` mints that exact
  audience.
- `services/connector_credential_broker.py` resolves credentials per request
  from an opaque grant/action binding and has no credential cache.
  `services/google_oauth.py` no longer retains access-token-bearing credential
  objects in a process-global cache.
- `services/hiring_approval_service.py` binds approvals to workspace, actor,
  run, policy, exact action, and subject hash without chat-session equality.
- `services/resource_sensitivity.py` makes `HIRING_RESTRICTED` ineligible for
  Founder Profile, company knowledge, session resources, and general search.

## H2 evidence

- All H0–H3 collections are in the Firestore registry, lifecycle inventory,
  and index manifest.
- Role Contracts use closed Pydantic envelopes. Policy versions are immutable;
  diff/impact enumeration completes before activation; the current role pointer
  makes mismatched assessments immediately stale. Impact creation and exact
  policy activation recover across either side of their projection boundary.
- Candidate identity is separate from application/evidence/decision data and
  envelope-encrypted with one random DEK per application. The production
  wrapper uses Cloud KMS; fixture encryption remains synthetic-only.
- Redaction withholds protected, uncertain, conflicting, and prompt-injection
  blocks without echoing the content. The analyst sees only pseudonymous,
  role-scoped evidence.
- Analyst input/output is closed and versioned. Code rejects invocation,
  policy, criterion-order, citation-hash, scope, unknown-field, ranking,
  recommendation, and forbidden-output drift before persistence.

## H3 evidence

- The role mailbox binding cannot become active until a positive delivery
  probe and a forged-recipient-header negative probe both pass.
- Unfiltered synthetic mailbox history is represented by recoverable batch
  entries that contain no message body or identity. Every entry becomes
  receipted, ignored, or quarantined before an optimistic cursor compare-and-
  set. Retry heals crashes after the application receipt, after an entry, after
  batch completion, and after cursor commit.
- Candidate withdrawal uses the same recoverable prepared/committed projection
  boundary. Recent-auth export and exact-hash dry-run deletion have durable
  receipts, legal-hold refusal, explicit provider limitations, and audit proof.
- Archived/read mail is selectable; raw recipient headers, copied labels, and
  ambiguous routing are insufficient and produce a safe inbox record.
- The committed external event precedes restricted artifact/evidence creation.
  An Evidence Passport cannot prove its own cause.
- `app/static/hiring.html` renders ordinary backend projections, an unranked
  role/candidate board, cited Evidence Passport, causal timeline, and explicit
  human-decision control. It never advances state optimistically. The public
  notice is read-only and accepts no candidate data.
- Founder-opened publication URLs are handed to the main app's embedded,
  audited Browser panel. The hiring surface contains no external navigation or
  `window.open` path.
- `tests/eval/evalsets/hiring_gate_safety.json` supplies exact zero-tool ADK
  golden trajectories for long-delay/session-switch resume, gate pressure,
  downstream failure, and duplicate wakes.

## Deterministic release command

```bash
pytest -q \
  tests/unit/test_hiring_h0_h3.py \
  tests/unit/test_collection_registry.py \
  tests/unit/test_data_source_contracts.py \
  tests/unit/test_eval_contracts.py \
  tests/unit/test_auth.py
python scripts/check_contrast.py
```

This verification is an engineering gate only. It is not qualified legal,
employment, privacy, accessibility, destination-terms, or production-pilot
approval.
