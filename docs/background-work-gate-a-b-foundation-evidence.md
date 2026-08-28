# Document 40 — Gate A/B foundation evidence

**Recorded:** 2026-08-28
**Scope:** dark, additive foundation only
**Release state:** no background-work gate is approved or enabled

This note records the first implementation slice of
`40-durable-background-work.md`. It is evidence for a foundation, not evidence
that Gate A or Gate B has passed. No generic background-work creation route,
ADK turn detachment, specialist execution, provider call, external read,
memory access, approval creation/resolution, consequence execution, proactive
conversation append, or actor-private SSE publication is wired.

## Implemented foundation

- `alex_background_job:v1` is an additive workflow kind backed by the existing
  authoritative workflow run, plan, event, step, attempt, cancellation,
  idempotency, CAS, lease, and generation-fence mechanisms.
- One code-owned `foundation.detached_prepare@1` template exists. Its production
  registry entry is disabled. Test/evaluation enablement must be injected
  explicitly and still requires service admission.
- Admission accepts only an interactive workspace member, a bounded objective,
  immutable registered internal references, and registered founder constraint
  codes. The immutable run profile requires actor-private visibility and the
  full closed set of negative constraints from Document 40.
- The runtime rejects background runs with missing, unknown, changed, or
  hash-mismatched profile fields. The Gate-A/B budget is exactly one
  deterministic step and zero model calls, provider calls, and tokens.
- Command receipt, command outbox, workflow run, plan, initial run event, and
  immutable input manifest are accepted in one create-only atomic mutation.
  Reusing an idempotency identity for different work is a conflict.
- The separate dark dispatcher can materialize only
  `validate_contract -> background.contract.validate`, a `NO_EFFECT`
  capability. It is not connected to the public dispatcher, Cloud Tasks, an
  application route, or an executor.
- Dispatch rechecks that specialist, approval, effect, external-read, and
  memory-write authority remain `NONE`/false. Authority widening fails before
  a step is created.
- Background run/step/command records default to `ACTOR_PRIVATE`. Generic run
  point reads and run controls enforce the originating actor; the only allowed
  generic control for this foundation is cancellation. Actor-private run and
  command changes are suppressed from the current workspace-wide SSE stream.
- The projection normalizer accepts only closed event kinds, caption codes,
  safe error codes, and exact approval identifiers. It returns observation-only
  metadata and never model-authored status text.
- `conversation_deliveries` is registered in the Firestore and data-lifecycle
  inventories. A delivery ledger, sequence-port contract, turn leases,
  generations, bounded attempts, dead-letter disposition, and fenced append
  receipt contract exist without any transcript append implementation.
- The production sequence port is deliberately unavailable. The current ADK
  write-surface manifest is explicitly `UNPROVED`; missing or drifted proof
  prevents claim. The in-memory sequence port exists only for deterministic
  race tests.
- Background step completion and conversation delivery completion reject an
  expired lease. Cancellation and generation changes fence late commits.
- `.env.example` records background admission, specialist execution, and
  proactive delivery as false. Admission and delivery fail closed in their dark
  services; no production caller imports either service, and specialist
  execution has no enabling code path.

## Verification

The following checks passed in the main checkout:

```text
pytest background/conversation + command/runtime regressions
35 passed

pytest plan/collection/lifecycle/projection/session/app regressions
93 passed

ruff on all touched Python files
All checks passed

py_compile on the touched production modules
passed
```

The tests cover disabled-by-default admission, non-interactive refusal,
atomic acceptance, duplicate replay, changed-input conflict, unsafe input and
constraint rejection, effect-capability refusal, authority-tamper refusal,
no-effect dispatch replay, actor-private isolation, cancellation fencing,
closed retry/projection contracts, direct-runtime bypass refusal, unavailable
serializer fallback, ADK proof refusal, delivery reservation ordering,
delivery identity replay, turn-lease conflict/reclaim, stale-generation append
refusal, and fenced-receipt completion.

The broader app suite emitted pre-existing ADK/gRPC resource warnings while
passing; no assertion failed.

The full repository unit/integration run reached `1260 passed, 2 skipped` and
one unrelated failure in `test_search_ui_contract.py`: the concurrently edited
unified UI no longer contains the older exact `title="Sessions (⌘K)"` string.
Neither that test nor `app/static/index.html` is part of this slice. Full-tree
Ruff likewise found one unrelated import-order issue in the concurrently added
`test_unified_product_ui_contract.py`; touched-file Ruff remains green. These
pre-existing/concurrent UI issues were left unchanged.

## Exact residual gates

### Gate A remains closed

- Complete and freeze the inventory/classification of every current specialist,
  transfer, tool, live, direct-append, and workflow path.
- Freeze golden API/event/hash vectors and prove no undeclared capability can
  enter an enabled template.
- Add versioned registry governance and release evidence for any template beyond
  the inert foundation template.

### Gate B remains closed

- Implement the Cloud SQL `ConversationSequence` schema, migration frontier,
  `LEGACY -> ENABLING -> ENABLED` cutover, and rollback/reconciliation commands.
- Implement `GenerationFencedSessionService` so the sequence-generation check,
  ADK event insert, state delta, artifact linkage, and event ordinal commit in
  one Cloud SQL transaction.
- Enumerate and prove every configured ADK persistent/founder-visible write
  surface for the exact package/build, Runner modes, plugins, session service,
  and persistence adapters. Bind the approved proof hash to CI, deploy, and
  runtime startup attestation.
- Put typed messages, voice finalization, proactive wakes, approval narration,
  direct appends, compaction/rewind, partial/live persistence, state-only
  deltas, and session-linked artifact writes behind the same session turn
  lease. Prove heartbeat renewal, absolute deadlines, expired-zombie fencing,
  ambiguous-append reconciliation, and no dual-path cutover.
- Add the durable delivery/outbox query indexes, retention, backup/restore,
  repair worker, workload identity, rate/cost limits, metrics, alerts, and
  operator runbook. The current delivery ledger has no worker or route.
- Add actor-scoped inbox/run-card/SSE snapshot and reconnect delivery, stream
  epoch rollback semantics, quiet-horizon recovery, and accessibility/voice
  evidence. Private jobs are currently hidden from workspace SSE rather than
  published through an unsafe stream.
- Run load, chaos, deletion, restore, cross-tenant, concurrent
  message/wake/voice, and full append-path adversarial evidence. The in-memory
  sequence tests do not qualify the production adapter.

### Later gates remain closed

- Gate C: shadow eligibility corpus and predeclared classification thresholds.
- Gate D: synthetic specialist execution with stubbed providers.
- Gate E: canary detached reads, egress allowlists, injection/exfiltration
  tests, and separate read/byte budgets.
- Gate F: real preparation outputs and exact Document-37 skill bindings.
- Gate G: background approval preparation and cancel-versus-approve proof.
- Gate H: production expansion, non-empty SLO windows, security/privacy review,
  load/chaos evidence, and rollback authorization.

Document 39 boundaries are unchanged: the foundation reads or writes no
durable memory, binds no memory skill, and introduces no automatic, managed,
multi-person, or cross-session memory behavior.

## Release decision

The slice is suitable to keep staged in main with all entry points dark. It is
not sufficient to enable background admission, specialist execution,
conversation delivery, SSE, external reads, approvals, effects, skills, or
memory. Advancing any such flag or route requires the corresponding residual
gate and a separate approval.
