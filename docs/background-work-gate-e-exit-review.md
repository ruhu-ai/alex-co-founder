# Spec 40 Gate E exit review

**Recorded:** 2026-08-28
**Release branch:** `codex/spec40-gate-e-soak`
**Reviewed release commit:** `b93a52e63a48447d59835874bc10460bb5737353`
**Decision:** technically ready for a clean fast-forward; not merged into the
checked-out `main` worktree because that worktree has unrelated overlapping
uncommitted and untracked changes.

## Outcome

The release contains exactly one live-capable template,
`pilot.artifact_evidence_inventory@1`. It is a deterministic, Founder-only,
actor-private inventory of one already-authorized artifact. It performs no
model or provider call, external read, browser action, connector call,
approval, effect, memory operation, generic background dispatch, generic Runs
or SSE expansion, or proactive conversation delivery.

The recorded synthetic canary, pitch-deck run, and three-run Activity soak are
sufficient to close Gate E for this one template. The live soak exposed one
idempotent-replay admission-budget defect. Commit `5a23f0b` moved replay lookup
before capacity evaluation; the focused regression and the live counter probe
proved that a replay returns the original run without consuming a rate or
concurrency admission.

The final environment was returned to safe dormancy: shared and pilot flags
off, pilot kill switch on, queue empty and paused, no enabled pilot revision or
traffic tag, and a signed admission probe denied without creating a run. The
route-scoped identity, ready indexes, paused queue, actor-private evidence, and
content-free alert policies remain as disabled infrastructure.

## Verification accepted for exit

- full repository suite: `1159 passed, 2 skipped` after the replay fix;
- focused pilot route, dispatcher, executor, ordering, UI, monitoring, and
  policy suites passed;
- Ruff, shell syntax, Firestore index JSON, contrast, and release diff checks
  passed;
- three authoritative real-artifact runs reached `RUN_CREATED(1)`,
  `STEP_STARTED(2)`, and `RUN_SUCCEEDED(3)` exactly once and projected truthful
  `QUEUED`, `RUNNING`, and `SUCCEEDED` Activity states;
- crash, timeout, duplicate, cancellation, retry, deletion, kill-switch, and
  alert-delivery evidence is retained in
  `background-work-founder-artifact-pilot-evidence.md`; and
- no approval, action, delivery, wake, memory, provider, model, or external-read
  record was linked to the pilot runs.

## Merge disposition

`main` at `602b66d` is an ancestor of the reviewed release, so a clean target
can accept it as a fast-forward. The checked-out main worktree at
`/Users/ijidailassa/projects/all-things-ai` is not a clean target: it contains
many unrelated modifications and untracked files, including paths touched by
this release. This review does not stash, overwrite, commit, or reinterpret
that work. Merge remains an explicit repository-integration action after the
owner preserves or resolves those changes.

## Gate boundary

Gate E exit authorizes no standing execution and no Gate F behavior. The
offline Gate F packet in `background-work-gate-f-offline-qualification.md`
defines one candidate and its evidence requirements; it intentionally ships no
skill runtime, preparation template, model call, provider call, live flag,
route, queue, worker, deployment, or cloud mutation.
