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

## Normal-service Founder-only live pilot

**Status:** `LIVE_BOUNDED_FOUNDER_PILOT`
**Date:** 2026-08-29
**Exact deployed source:** `f522a72bb5a6ced7872a31343ac0ec8051510581`
**Cloud Build:** `bb521f7b-c102-47c6-bad0-31327089c799`
**Immutable image:**
`sha256:fb7cefe003dc9e67a94fc8a263c77f16c179b364e6714db5ce3f94d46c103aac`

The normal service now exposes only the closed
`pilot.artifact_grounded_brief@1` lane to the authenticated `FOUNDER` in
workspace `founder`. The source fix projects a normal structural ingestion
locator such as `{"section":"document"}` to its stable one-based chunk page
for the already-qualified closed citation schema. It does not widen model
input, output, tool, URL, connector, memory, approval, or effect authority.

The canonical service is 100% on
`co-founder-spec40-main-f522-live`. Same-image rollback revision
`co-founder-spec40-main-f522-ready` keeps Spec 40 off and both artifact kill
switches on. `co-founder-spec40-main-f522-m2off` additionally disables the
Spec 39 M2 entry gate. The live queue is limited to one dispatch per second and
one concurrent dispatch. The pre-live killed arm returned HTTP 403
`background_skill_killed` for a valid owned artifact and created zero new runs.

Exact-image verification reproduced the deployed source hash and read the
previously failing harmless artifact as one chunk with locator `{"page":1}`;
the probe emitted no content and called no model, provider, or effect path.
The bounded live retry was admitted once with a new idempotency key and
progressed uniquely through `QUEUED(1) -> RUNNING(2) -> SUCCEEDED(3)`. It
created one actor-private `DRAFT` with two citations, used one model/provider
call and zero retries, and created zero approvals, external actions, action
outbox rows, conversation deliveries, or memory items.

The canonical redesigned interface was verified in a real authenticated
browser. It showed the complete Product navigation (Alex, Search, Runs,
Hiring, Decisions, Activity, and Settings), made no unreleased workspace-brief
request, and displayed the authoritative completed status and private-draft
citation count in contextual Activity. The funding-specific Runs surface was
not repurposed as generic background-work authority.

After receipts were captured, the exact synthetic session, source file, input
manifests, private draft, failed and successful test runs, plans, steps,
attempts, events, commands, and outbox rows were removed. Re-reads returned 404
or absent for every enumerated id; the append-only content-free cleanup audit
was retained. Final service checks found zero ERROR or safety signals, and the
three content-free Spec 40 alert policies were enabled with a verified channel.

The final live envelope deliberately keeps the older inventory pilot off and
killed, conversation delivery off, open web off, and every connector, browser,
approval, external effect, memory-write, hiring, generic route/template,
multi-user, generic Runs, and SSE expansion outside this pilot. Broader Spec 40
rollout still requires the remaining gates in `40-durable-background-work.md`,
including a second-template review, external-read authority and egress proof,
the full proactive conversation serializer/delivery proof, multi-user privacy,
and broader load/restore/error-budget evidence. None is implied by this bounded
Founder pilot.
