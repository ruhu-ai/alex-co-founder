# Spec 40 Gate F controlled cloud canary evidence

**Status:** `PASSED_CONTROLLED_SYNTHETIC_CANARY`  
**Date:** 2026-08-29  
**Machine-readable evidence:**
`skills/evidence/spec40-gate-f-cloud-canary-20260829.json`

The single qualified `pilot.artifact_grounded_brief@1` template completed its
controlled synthetic cloud canary in `co-founder-506001`. It used one synthetic
authenticated `FOUNDER`, session, and harmless two-chunk artifact; one private
Cloud Run service; one route-only worker identity; one concurrency-one Cloud
Tasks queue; and the pre-existing verified Google alert email channel. The
normal product service was not modified by this canary.

The worker could read only the selected immutable artifact, make one tool-less
`gemini-3.6-flash` call, validate the closed grounded-draft schema and exact
citations, and atomically persist an actor-private `DRAFT`. It had no web,
browser, connector, approval, effect, memory, conversation delivery, generic
Runs/SSE, hiring, or arbitrary tool/URL surface.

## Live results

- The exact replay reused one command and run and correctly remained
  `DISPATCHED`; no second task or job was created.
- The successful run progressed uniquely and in order through
  `QUEUED(1) -> RUNNING(2) -> SUCCEEDED(3)`, used one attempt, and persisted one
  actor-private draft with two citations.
- A deliberately invalid synthetic locator window failed closed through three
  bounded attempts with ordered recoverable progress and terminal failure. It
  created no output and was completely cleaned before the next window.
- An in-flight evidence delay was cancelled through
  `QUEUED(1) -> RUNNING(2) -> CANCELLING(3) -> CANCELLED(4)`. The worker commit
  was fenced and no output was created.
- The kill switch paused admission with HTTP 403
  `background_skill_killed` before final cleanup.
- Approvals, external actions, conversation/wake deliveries, memory items, and
  memory-write receipts remained empty throughout.
- The exact content-free 5xx policy opened and closed alert
  `projects/co-founder-506001/alerts/0.obzpb1xl87ea` through the enabled
  `Spec 40 Canary Failures` email channel.

The canary found and closed four bounded defects: Python 3.10 `StrEnum`
compatibility in the pinned Cloud Run base image; duplicate dispatch response
projection; numeric locator enforcement in the synthetic fixture; and the need
for two private HTTP request slots so cancellation can run alongside the one
queue worker. Queue concurrency, dispatch rate, and instance count remained
one.

## Rollback and cleanup proof

The queue was paused and the Gate F kill switch activated before cleanup. The
canary cleanup removed every synthetic source, manifest, draft, membership,
control, command/outbox, capacity record, plan, run, step, attempt, event, and
audit. An independent workspace-scoped Firestore re-read returned zero records
in every enumerated collection.

The private service, queue, two identities, project/resource IAM, temporary
secret, two alert policies, four container digests, and four Cloud Run source
archives were deleted. Independent inventory returned zero for every category.
Only immutable Cloud Build/Monitoring history and the pre-existing verified
email channel remain as audit evidence.

Repository flags remain omitted/default-off and the kill switch defaults on.
This packet qualifies the closed synthetic Gate F canary. It does not claim
that the concurrently changing normal `co-founder` service has integrated this
work, and it enables no Gate G approval wait or Gate H broad expansion.
