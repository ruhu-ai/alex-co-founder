# Spec 40 Gate F synthetic runtime evidence

**Recorded:** 2026-08-29
**Status:** `PASSED_SYNTHETIC_RUNTIME`
**Live route:** absent

The first qualified Gate F skill now has a bounded synthetic runtime on the
existing durable workflow authority. It accepts exactly one server-resolved,
immutable artifact; exposes exactly the selected-evidence reader and private
draft writer; permits one tool-less `gemini-3.6-flash` call; validates the
closed output and every citation; and atomically commits one actor-private
`DRAFT` artifact with the terminal run event.

No app or worker route imports this runtime yet. Repository defaults keep
admission and execution false, the kill switch true, and the workspace
allowlist empty. The harness cannot reach web, browser, connectors, approvals,
effects, memory, proactive conversation delivery, generic Runs, or SSE.

## Verified behavior

- one accepted command, input manifest, workflow run, plan, step, attempt,
  output, and terminal event remain one durable authority chain;
- exact Founder/workspace/session/artifact checks fail before durable
  admission, and cross-tenant reads collapse to denial;
- duplicate admission and delivery do not repeat a model call or draft write;
- retryable pre-model failures recover within the lease/retry budgets, while a
  model or output-validation failure is terminal and content-free;
- cancellation fences an in-flight read before the model call and prevents a
  stale draft commit;
- the kill switch blocks accepted work without deleting or falsely completing
  it;
- malicious open-schema output and closed-schema URL/action text are rejected;
- Activity reuses the actor-private contextual surface and shows ordered
  queued/running/completed states plus the private draft label; and
- audits and metrics contain only identifiers, status, attempt, and safe error
  codes.

The focused Gate F and related suite passed 165 tests. The complete repository
suite passed 1,275 tests with 2 skipped. Compiler verification, Ruff, and diff
checks passed. The content-free machine record is
`skills/evidence/spec40-gate-f-synthetic-runtime-20260829.json`.

## Technical rollout boundary

Doc 40 now advances release stages on recorded technical checks without a
repeated Founder release prompt. This checkpoint therefore permits work on the
default-off canary route, queue identity, and exact model adapter. It does not
itself register a route, enable a flag, deploy, create cloud resources, or
authorize any external effect. The next checkpoint must prove authenticated
route scoping, worker audience/identity, model-adapter parity, live synthetic
recovery, monitoring, kill-switch rollback, and cleanup before expansion.
