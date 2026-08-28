# 40 — Durable background work behind one Alex

**Status:** Review-complete production specification, ready for recorded staged
implementation approval under §15. Documentation only; this document does not
itself authorize implementation, deployment, a live effect, or a migration.
Each rollout gate in §15 requires its own recorded approval.

One authenticated Founder **release-stage approval** may cover an exact,
hash-pinned batch of skills within one rollout gate. That avoids repeated
product prompts for compiler, fixture, model-evaluation, and evidence-recording
steps inside the same bounded gate. It does not span gates, survive a skill,
model-policy, fixture, threshold, capability, or scope hash change, or count as
an in-product action approval. Every skill in the batch must still pass every
applicable technical, security, privacy, quality, citation, cost, lifecycle,
independent-review, and rollback check. `NO_DATA` or one failed check blocks the
whole batch; the Founder approval cannot override or mark a check passed.

**External review disposition (2026-08-28):** findings-first gate review
SHA-256 `6d353a60c2e16201d9e53f78001537368bde282d5bf3ff026ef4ed78fe2a0dd6`
was reconciled into this revision. The architecture remains proposed and must
pass its recorded rollout gates; in particular, Gates B and E remain blocked
until append-time session fencing and closed external-read egress are proved.

**Second external review disposition (2026-08-28):** residual review SHA-256
`7428c8a6b59fc8d37f0a6ec915754696fd1ddc3d94400def134247a812f79882`
found no High/Critical defect and was reconciled into this revision. It adds
proof obligations for the complete ADK write surface, actor scope, queued-turn
delivery, lease renewal, intent interpretation, cutover, and restore epochs.
Those obligations were evaluated by the final review below.

**Final external review disposition (2026-08-28):** review SHA-256
`ce8884bade27984d26e55d2777044709d7e131652d68d1ca6ff17546412a11a4`
found five bounded design defects and otherwise approved the specification for
staged implementation under §15. D1–D5 are reconciled here. Approval permits a
separate recorded decision to begin Gate A and the Gate-B foundation only; it
does not waive any evidence gate or authorize Gates F/G, deployment, or effects.

**Depends on:** [21](21-alex-platform-north-star.md),
[23](23-session-resources-and-global-search.md),
[24](24-activity-and-waiting.md),
[34](34-platform-architecture-and-convergence.md),
[35](35-platform-operations-and-recovery.md), [16](16-design-system.md),
[36](36-unified-alex-product-experience.md),
[37](37-production-skills-system.md), [38](38-user-facing-vision.md), and
[39](39-durable-cross-session-memory.md). Doc 36 owns unified-surface
composition, 37 owns skill packaging and qualification, 38 owns live-vision
capture/session policy, and 39 owns the durable-memory backend/lifecycle. This
document owns only their background-job binding seams. Current baseline
memory/evidence boundaries also remain [06](06-memory-profile.md) and 34 §7.
Where this document and 34 overlap, 34's authority, consequence, approval,
tenancy, and recovery contracts remain binding.

Normative words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** use their RFC
meanings.

---

## 0. Executive decision and verified starting point

Alex remains the only founder-facing conversational identity. Eligible
specialist work may detach from an ordinary message after a durable acceptance
boundary, then run as bounded steps of the existing generic workflow runtime.
The founder's message returns as soon as Alex has either answered or durably
accepted the work; it never waits for a specialist result.

A background job is **not a new execution authority**:

> `job_id == run_id`. A detached job is a constrained `WorkflowRun` profile,
> not a second jobs engine, an ADK transfer left running, or an agent swarm.

The reviewed implementation proves native generic-run/step execution for
investor outreach and current hiring runtime paths, plus Cloud Tasks delivery,
CAS, leases, idempotency, waits, cancellation fencing, projection events,
workspace SSE, approvals, actions, and session wakes. Discovery commands have
durable receipts/tasks and the versioned route can create a run projection, but
discovery and grant domain records remain authoritative; their compatibility
runtime adapter is explicitly a Phase-1 shadow. A shadow run cannot host a
background job. Ordinary `/api/v1/messages` ADK turns and specialist transfers
are still synchronous. There is no generic detached specialist admission
contract; same-session proactive inference is not proved safe under concurrent
turns; observation delivery can outlive an SSE connection; and raw specialist
steps do not consistently produce useful founder milestones. This
specification closes those gaps without replacing the proven runtime.

Binding equations:

> Chat expresses and clarifies. Durable state authorizes. A bounded worker
> prepares. The consequence kernel acts. Alex reports.

> A closed browser or expired stream demand can hide observation temporarily;
> it can never pause, cancel, complete, or erase a job.

## 1. Scope and non-goals

### 1.1 In scope

- detached read, retrieval, analysis, validation, and draft preparation
  launched from an ordinary Alex conversation;
- a registry-backed eligibility decision made by code after references,
  permissions, scope, and budgets are resolved;
- generic run/step admission, dispatch, leases, retries, cancellation, outputs,
  milestones, waits, failure, and reconciliation;
- strict serialization of founder turns and proactive conversation deliveries
  for one ADK session;
- durable run cards, inbox items, conversation captions, and replayable SSE;
- bounded specialist roles and versioned skills that cannot add authority;
- use of task-scoped artifacts, confirmed facts, and governed durable memory;
- additive APIs, schemas, migrations, feature flags, rollout gates, and tests.

### 1.2 Non-goals

- autonomous multi-agent debate, peer-to-peer delegation, or an agent swarm;
- making a specialist a second founder-facing persona;
- detaching every message, speculative work, or emotional/general conversation;
- using chat history, a skill, memory recall, or model confidence to authorize a
  state transition or effect;
- replacing `WorkflowRun`, `StepAttempt`, durable waits, approvals, the action
  ledger, command receipts, founder inbox, or projection stream;
- background execution of arbitrary model-authored Python, SQL, shell, plans,
  tool names, URLs, connector scopes, or recipients;
- unsolicited audio, false percentages, or model-authored operational status;
- weakening review for hiring, financial, legal, compliance, outreach, browser,
  portal, email, calendar, or other domain effects;
- guaranteeing cancellation of an effect that crossed 34's T2 boundary.

## 2. Authority and eligibility policy

The model may propose detachment; only `BackgroundEligibilityPolicy` may admit
it. The policy consumes a closed job template, resolved references, current
principal, capability and skill manifests, sensitivity/retention policy,
domain state, and budget. Its signed/versioned decision is stored on the run.

### 2.1 Work classes

| Class | Examples | Default execution | Required boundary |
|---|---|---|---|
| `INLINE` | discussion, clarification, short status read, ambiguous reference, immediate interview question | current Alex turn | no job is created |
| `DETACHED_READ` | search allowed sources, inspect a founder-scoped artifact, extract evidence, compare records | eligible for background | immutable server-resolved targets/queries, per-call destination authorization, external-read budget, persisted cited output |
| `DETACHED_PREPARE` | draft a document, rank candidates/investors, prepare a proposed reply, validate evidence | eligible for background | output is a draft/proposal only; no provider effect |
| `DETACHED_TO_APPROVAL` | prepare an exact recipient/payload/target and open an approval | background until a durable founder wait | approval record binds exact current plan, target, payload, evidence, policy, versions, and expiry |
| `EFFECT` | send, submit, publish, invite, schedule, mutate a provider, disclose data externally | never authorized by this job contract | only 34's approval/action/consequence protocol and a registered effect worker |
| `NON_DETACHABLE` | unresolved ambiguity, interactive browser control, live interview exchange, step-up authentication, unsafe data class, capability without recovery contract | inline, clarify, or refuse | no background admission |

`DETACHED_TO_APPROVAL` does not grant or consume approval. It may create one
`PENDING` decision surface and then becomes `WAITING`. “I approve” in chat can
focus that surface but cannot decide it. After a valid decision, any effect is
performed by the existing consequence kernel—not by the specialist job.

An external read is observable egress, not “free” merely because it does not
mutate the provider. Its destination and query source are fixed at admission
from the founder's immutable objective, a resolved workspace reference, or a
closed server registry. A model may not turn a URL, recipient, identifier, or
query found in an untrusted page/email/tool result into another read. Every
external read re-authorizes the exact destination, workspace/source grant,
data class, and egress policy at call time and consumes a distinct
`external_reads`/bytes budget receipt.

Detached reading is deliberately allowlist/snippet-scoped, not unattended
open-web browsing. A step may fetch only destinations resolved at admission or
present in a closed server registry. It may inspect an admitted search result's
bounded snippet, but it may not follow a discovered result/page link inside the
same plan. Opening that link requires a new eligibility/admission decision that
server-resolves it to an allowed destination, or an inline founder-confirmed
fetch. Neither path treats the page's suggestion as authority.

### 2.2 Admission predicate

A proposed job is eligible only when all conditions are true:

1. The founder expressed a concrete objective or accepted Alex's explicit
   proposal. Empathy, brainstorming, hypotheticals, and passive mentions do not
   imply work.
2. The template, steps, specialist roles, skills, capabilities, side-effect
   classes, completion contracts, retry classes, and milestone policy are in
   the server registry at reviewed versions.
3. Objective interpretation is a separate bounded step: closed UI fields may
   compile directly, while free text is extracted into a closed intent schema
   by a pinned, adversarially evaluated interpreter. Broad-scope, sensitive,
   recipient-bearing, or approval-producing templates require the founder to
   confirm Alex's exact displayed interpreted objective before admission. The
   deterministic validator then compares that direct or founder-confirmed
   intent—not an unconfirmed model paraphrase—with the template's constraints,
   scope breadth, and output contract. Interpreter/template provenance is
   recorded. A mismatch or more than one valid template returns to Alex for
   clarification.
4. Every entity, session, artifact, memory source, connector, and domain record
   resolves uniquely inside the authenticated workspace. Ambiguity returns to
   Alex as a clarification; “latest” is not resolution.
5. Inputs are immutable references or version/hash-bound snapshots. Required
   facts that exist only in chat are persisted through their proper fact or
   run-input boundary before admission.
6. Every external-read destination and query source is immutable and
   server-resolved; untrusted mid-run content cannot add or alter either.
7. The context pack fits the declared sensitivity, residency, retention,
   purpose, and model policy. External content is untrusted data.
8. All pre-approval steps are read-only or preparatory. Any possible effect is
   represented as an explicit future approval/action boundary.
9. The job has numeric step, model, token, active-time, wall-time, artifact,
   retrieval, external-read, and provider-read budgets; workspace concurrency
   has capacity.
10. The result can be committed idempotently, cancelled or fenced, inspected,
   and reconciled without relying on a live ADK session or SSE client.

The policy refuses detachment when a capability is undeclared/disabled, the
requested scope exceeds a grant, the data class is unsupported, a completion
contract is subjective and unbounded, or an effect cannot use deterministic
idempotency/reconciliation. Refusal is an error-as-data outcome that Alex can
explain; it is never silently converted into a synchronous specialist transfer.

## 3. Job, plan, step, and output contracts

### 3.1 Job profile on `WorkflowRun`

Register `alex_background_job:v1` as a workflow definition. Its run stores the
34 §5.3 fields plus:

```text
execution_mode: BACKGROUND
job_template_id, job_template_version
eligibility_policy_id, eligibility_policy_version, eligibility_decision_hash
origin_actor_id, origin_message_id, origin_session_id, delivery_session_id
subject_kind: WORKSPACE | ACTOR
subject_id, visibility_scope: WORKSPACE | ACTOR_PRIVATE
visibility_policy_id, visibility_policy_version
objective_summary, negative_constraints[]
input_manifest_ref, input_manifest_hash
completion_contract_id, milestone_policy_id
skill_bindings[]: {skill_id, version, manifest_hash}
output_manifest_ref
```

`objective_summary` and safe display metadata are bounded projections, not the
input authority. `input_manifest_ref` points to a workspace-scoped immutable
manifest of resolved records/artifact versions and allowed purposes. A job may
outlive, but never change ownership with, its origin session. By default a job,
its existence, timeline, outputs, milestones, inbox items, and deliveries are
`subject_kind=ACTOR`/`ACTOR_PRIVATE` for `origin_actor_id`. A reviewed template
policy may explicitly choose `WORKSPACE`/`WORKSPACE`; this is not inferred from
workspace membership. A founder may choose another session owned by the same
origin actor and allowed by the job's visibility policy; otherwise results
remain in that actor's inbox.

`delivery_session_id` is a preference, not a delivery guarantee or migration
trigger. If the authorized target session is `LEGACY`, `ENABLING`, unavailable,
or no longer in scope when an event is delivered, the event remains visible in
the origin actor's inbox/run card and no proactive transcript append occurs. A
later `ENABLED` session may receive a newly authorized delivery; nothing is
dropped or force-appended to make the old target appear current.

`negative_constraints[]` are immutable safety inputs compiled only from the
registered template/policy and the direct or founder-confirmed intent. They are
never model-fillable slots. `objective_summary` is likewise display-only and
cannot override the bound interpreted-intent record.

The plan is registry-produced and immutable. A model may fill only declared
non-routing, non-authority content slots. Template IDs, capabilities, skills,
external-read destinations/queries, recipients, retry policy, approval policy,
and effects are server-resolved. A material scope change creates a new plan
version after the same eligibility and intent-to-template checks; completed
work stays in the event history.

### 3.2 Step and attempt

Use existing `workflow_steps` and `step_attempts`. Additive step fields are:

```text
agent_role_id
skill_bindings[]
input_manifest_hash, dependency_output_hashes[]
completion_contract_id
side_effect_class: READ | PREPARE | APPROVAL_PREPARE
retry_class, timeout_seconds
milestone_on: NONE | START | COMPLETE | MATERIAL_OUTPUT
```

One task delivery performs one bounded attempt. The worker receives exactly
one step, one role, a minimum context pack, and the declared capabilities. It
does not receive the founder transcript by default. ADK worker sessions are
isolated (`include_contents="none"`) or job-scoped and are never the business
run identifier.

The dispatcher performs:

1. read and authorize run, active plan, registry and workspace membership;
2. CAS-claim the ready step and record run cancellation generation, lease
   generation, workload principal, budgets, and attempt;
3. execute within the declared timeout;
4. validate result schema, citations, sensitivity, and completion contract;
5. after any separately durable artifact write, transactionally fence on
   run/plan/lease/cancellation/input versions, commit its verified output
   reference and the run event/projection, and create only the bounded,
   destination-specific outbox intent rows declared by the step manifest;
6. acknowledge the Cloud Task only after that durable commit.

The transaction performs no queue, memory, inbox, SSE, session-link, Cloud SQL,
GCS, provider, or model work. Idempotent outbox consumers materialize those
destinations downstream. Multiple outbox intents are allowed as required by 34
§5.9, but their kinds/count are closed and bounded; work exceeding the reviewed
transaction limit is decomposed into another step rather than weakening the
boundary.

Dependencies, not specialists, dispatch follow-on work. Independent read-only
branches may run concurrently; writes to the run event sequence and shared
domain records serialize through CAS.

### 3.3 Outputs and milestones

Large or sensitive output lives in the existing artifact/evidence stores.
`WorkflowOutput` metadata records run, step, workspace, plan/input hashes,
schema, sensitivity, citations, producer/model/skill/capability versions,
validation result, retention, and content hash. A duplicate output key with a
different hash is an idempotency conflict.

Internal steps are not automatically founder milestones. A closed milestone
policy emits `MILESTONE_REACHED` only for:

- durable job acceptance/queueing;
- a meaningful phase boundary (research complete, draft ready);
- a material inspectable output;
- a human/external wait or approval request;
- budget degradation, recoverable failure, uncertainty, or terminal state.

No more than one routine progress milestone per job per 30 seconds is delivered
to the conversation; later routine events coalesce. A delivery session also
receives at most one routine progress item per 30 seconds across all jobs;
concurrent jobs roll up into closed copy such as “3 jobs advanced,” with links
to their run cards. Approval, failure, uncertainty, cancellation, and terminal
events never coalesce. Captions and icons come from a closed server map; raw
model text, tool names, specialist names, prompts, and chain-of-thought never
enter a milestone.

## 4. Dispatch, idempotency, retries, and cancellation

### 4.1 Acceptance and dispatch

During an ordinary Alex turn, a server tool such as
`propose_background_job(...)` accepts only registry IDs and typed slot values.
Any model-proposed template ID is untrusted until the deterministic
intent-to-template validator in §2.2 binds it to the founder's objective;
selection provenance and ambiguity disposition are durable audit fields.
The already accepted `conversation.message` receipt is the parent cause; the
tool runs the eligibility policy and atomically creates a child
`background_job.create` command receipt, job/run, plan, initial event/projection,
input manifest, origin-session link, and dispatch outbox. The message receipt
may complete with the child job reference only after that transaction. The HTTP
response may say “I started…” only then. It returns `202`/accepted semantics and
identifiers; it does not wait for execution.

The outbox dispatcher uses a deterministic Cloud Task name. Task payloads hold
opaque IDs and generations, never user text, artifacts, secrets, or arbitrary
URLs. Internal task routes require audience-bound workload identity and verify
the exact workspace/run/step/capability scope again. Background preparation has
its own bounded queue/concurrency lane; it cannot share or starve interactive
message, provider-event/wake, timer/reconciliation, browser, or consequence
lanes. Priority is server policy, never a model-authored queue name.

### 4.2 Stable identities and retry rules

- job identity: `(workspace_id, client_request_id, normalized_job_hash)`;
- step identity: `(run_id, plan_version, step_key)`;
- attempt identity: `(step_id, lease_generation)`;
- output identity: `(step_id, completion_contract_id, input_hash)`;
- milestone/inbox identity: `(run_id, event_id, recipient_actor_id, kind)`;
- conversation delivery identity:
  `(workspace_id, subject_kind, subject_id, session_id, causal_event_id, kind)`;
- conversation event-append identity: `(delivery_id, event_ordinal)`.

At-least-once dispatch is expected. Same key/same hash returns the original
record; same key/different hash returns `409 idempotency_conflict`.

Retry policy is closed by step manifest. Transient model, read-provider, and
queue failures use capped exponential backoff with jitter and an attempt limit.
Schema, policy, authorization, input-staleness, prompt-injection, budget, and
unsupported-data failures do not retry blindly. A crashed read/preparation
attempt may recompute, but only the current lease generation may commit. Model
cost can repeat after a pre-commit crash; the repeated attempt still consumes
the same job/workspace budget ledger and is observable.

### 4.3 Cancellation

Founder cancellation is an idempotent, version-checked run command. It moves
the run through `CANCELLING`, increments `cancellation_generation`, fences new
claims and stale commits, voids unclaimed future approval requests, and
performs paginated cleanup before `CANCELLED`. A worker checks cancellation
before a model/provider read and again before commit.

Cancellation and approval resolution serialize on the approval's current CAS
version plus the run cancellation generation. A cancellation committed before
T2 prevents approval claim/start or voids the pending approval as policy
allows. If T2 already committed, cancellation cannot rewrite the decision or
reopen the grant; the same action settles or reconciles forward under 34 §8.2.

Cancellation acknowledgement and fencing are immediate after the CAS commits.
The run may remain `CANCELLING` while bounded, paginated cleanup completes
asynchronously; no claim, retry, approval start, preparatory work, or routine
work-progress milestone may resume after the cancellation generation is fenced.
The cancellation acknowledgement, cleanup failure, and `CANCELLED` state remain
deliverable. The run card shows terminal-pending cancellation until cleanup
durably reaches `CANCELLED`, and repair resumes cleanup rather than work.

Cancellation never deletes the audit/event record or completed output. A
late preparatory output is quarantined from founder delivery and follow-on
dispatch. An effect already past T2 settles or reconciles under 34; the UI says
that cancellation cannot undo it.

## 5. Conversation ordering and concurrency

No code path may concurrently invoke ADK against the same founder session. This
includes public messages, proactive wakes, voice finalization, job completion
narration, and approval-result narration.

Add a durable logical conversation-sequence row beside the production ADK
session events in Cloud SQL, plus a
`conversation_deliveries/{delivery_id}` Firestore ledger:

```text
ConversationSequence
  workspace_id, session_id, next_sequence
  active_turn_id, turn_lease_generation
  turn_started_at, turn_heartbeat_at, turn_lease_expires_at, turn_deadline_at
  version

ConversationDelivery
  delivery_id, workspace_id, origin_actor_id, session_id, sequence, kind
  subject_kind, subject_id, visibility_scope
  command_id?, run_id?, causal_event_id?, payload_ref?, safe_caption_code?
  next_event_ordinal, result_event_refs[]
  status: PENDING | LEASED | APPENDING | DELIVERED | DEAD_LETTER
  lease_owner, lease_generation, lease_expires_at
  attempts, next_attempt_at, last_safe_error, created_at, delivered_at, version
```

The delivery ledger orders work; it does not replace the ADK transcript or
authorize workflow work. The production `ConversationSequence` fence is stored
in the same Cloud SQL database as the ADK event append so a
`GenerationFencedSessionService` can lock the row, verify
`turn_lease_generation` and `active_turn_id`, insert an event with unique
`(delivery_id, event_ordinal)`, apply its session/app/user state delta, and
advance its append ordinal in one database transaction. Every Runner invocation
carries the claimed generation in an invocation-local context. A pre-check in
Firestore or a process lock is insufficient. After an ambiguous append, repair
queries the unique event key before retrying.

Gate B requires a version-pinned `ADKWriteSurfaceManifest` for every configured
Runner mode. It enumerates user, model, tool/function, control, compaction,
rewind, state-only, partial/streaming, live-transcript, direct application, and
session-linked artifact write paths; identifies which are persisted or exposed;
and proves that every persistent or founder-visible mutation crosses the same
generation fence. Merely wrapping the common `append_event` call is not proof.
An unenumerated or un-interceptable ADK path blocks proactive delivery for that
mode; the application may not fork or monkey-patch ADK as an unreviewed bypass.
Partial output from a proactive invocation remains buffered and invisible until
its fenced durable disposition. The manifest hash, ADK version, configured
Runner mode, and proof results are frozen in Gate-B evidence.

That proof is keyed to the exact ADK package/build hash, Runner modes and run
configuration, session-service implementation/configuration, enabled plugins,
and persistence/artifact adapters. A change to any key invalidates the proof
and MUST rerun the manifest enumeration, fence tests, and state-only negative
tests before release. CI blocks an unmatched build. Runtime startup also
attests the deployed tuple to the approved proof; on absence or mismatch it
forces `BACKGROUND_CONVERSATION_DELIVERY_ENABLED=false` for the affected mode.
Prior evidence cannot authorize a newer dependency surface.

A live invocation renews its turn lease through a generation-checked heartbeat
CAS. The heartbeat interval and lease TTL are numeric registry policy, with the
interval at most one third of the TTL after measured scheduling jitter; an
absolute `turn_deadline_at` bounds total renewals and model cost. The supervisor
renews only while it still owns the generation and its progress watchdog is
healthy. Repair may start only after a missed heartbeat makes the renewable
lease expire or the absolute deadline is reached, then CAS advances the
generation. Job-step and turn-delivery heartbeats are separately scoped records
but use this same recency/clock contract; UI motion may consume only the
heartbeat for the exact work it depicts.

Rules:

1. A founder message is accepted in sequence. If another turn owns the lease,
   the message is durably queued and receives `202`, not run concurrently.
2. An eligible job acceptance reply is part of that founder turn and precedes
   all milestones for the job.
3. Background events arriving during a turn create inbox/projection records and
   a pending conversation delivery. They do not call the live session.
4. A delivery-worker lease only authorizes processing the queue item. Before a
   proactive caption/narration can append, it must separately CAS-claim the
   `ConversationSequence` turn lease/generation for that delivery's short append
   critical section, pass the same Cloud SQL generation fence, and release the
   turn lease. If a founder turn owns it, the delivery stays pending. The update
   appends after the active turn completes; a subsequent founder message cannot
   overtake an already-reserved lower sequence.
5. A healthy generation renews before expiry. A missed heartbeat or absolute
   deadline permits repair only after CAS advances the generation. A zombie
   invocation may finish computation, but its superseded generation is rejected
   inside the same Cloud SQL transaction that would append its event or state
   delta. It cannot append after the repaired invocation starts.
6. A notification-delivery failure cannot roll back the job. It becomes a
   visible dead letter while the run card and inbox remain correct.
7. Rich status narration never requires proactive ADK inference. Closed Alex
   captions are the safe default. A future narrator model, if enabled, runs in
   an isolated worker session and its bounded text is still delivered through
   this sequencer.

This is the required proof boundary for proactive wakes. Until it passes the
concurrency tests in §16, background events may update only run cards, inbox,
and SSE—not append into the originating conversation.

## 6. Event, projection, inbox, and SSE lifecycle

Authoritative job transitions atomically append the run event/update the run
projection and create durable outbox records. Projection publication is not a
best-effort-only path for this feature: failed projection/inbox/conversation
publishing remains pending and repairable. Observation failure never rolls
back authority.

Each material event fans out idempotently to:

1. the current run snapshot and immutable run timeline;
2. an actor/recipient-scoped founder inbox item, or a workspace item only when
   the job's reviewed visibility policy says `WORKSPACE`;
3. an origin/delivery-session `ConversationDelivery`, when allowed by §5;
4. a safe `projection_event` for workspace SSE;
5. a `session_resource_link` for each material inspectable output.

SSE subscription demand is never a work lease. The server produces durable
events whether zero or many clients are connected. A stream may end at its
bounded horizon; the client reconnects with `Last-Event-ID`. On a missing or
expired cursor it receives `snapshot_required` and fetches current active jobs,
pending approvals, inbox, and conversation deliveries before resubscribing.
Focus/visibility restore performs the same snapshot read. There is no workflow
polling loop.

Projection retention MUST cover the declared replay window, but an active job
does not depend on old events remaining replayable. `GET /api/v1/jobs` derives
all non-terminal jobs from durable run projections, so a job accepted before a
long quiet interval is still visible. The client keeps independent version
watermarks per aggregate and rejects regression/out-of-order snapshots **within
one stream epoch**. `projection_streams` carries an opaque `stream_epoch`; every
event, cursor, snapshot, and `snapshot_required` response carries it. Epochs
come from a strictly monotonic restore-generation authority outside the data
being restored and include a globally unique token; a value is never reused,
even when a backup reinstates an older `projection_streams` row. A
backup/restore or sequence-rewinding rollback must mint and publish the new
epoch before admission resumes. On epoch mismatch the client discards prior
cursors and aggregate watermarks, accepts the authorized fresh snapshot, and
reconnects. SSE heartbeats contain no user data and do not imply progress.

## 7. Founder experience, voice, and captions

The composer, navigation, and voice controls remain usable while jobs run.
Alex acknowledges acceptance in one sentence, says closing the chat is safe,
and continues the conversation. The unified UI renders a compact run card in
the transcript and activity area; opening it reveals objective, durable status,
last milestone, next action/wait, outputs/citations, costs within policy,
failure/uncertainty, and pause/cancel/retry controls where valid.

Founder-visible states use closed copy, for example:

| Runtime condition | Caption | Motion/action |
|---|---|---|
| accepted/queued | “I’ve queued the research. You can keep chatting.” | static queued icon |
| leased execution | “I’m reviewing the evidence.” | motion only while the lease is valid **and** its execution heartbeat is recent |
| stalled lease | “This step appears stalled. I’m checking it safely.” | static warning; reconciliation, never a progress spinner |
| cancelling | “I’m stopping this safely.” | static terminal-pending state; cancellation is fenced while cleanup continues |
| durable wait | “I’m waiting for the source to respond. I’ll update you here.” | static wait icon; next check shown |
| approval | “Your draft is ready. I need your approval before anything is sent.” | exact approval card; never spinner |
| complete | “I finished the research. The cited brief is ready.” | output link and completion time |
| recoverable failure | “I couldn’t finish this step. Nothing was sent.” | safe retry if manifest permits |
| terminal failure | “I stopped because the source could not be verified.” | evidence/error code and next option |
| uncertain effect | “I can’t confirm whether that action completed.” | reconciliation state; never success |

Unknown-duration work shows phases, not percentages or fabricated ETAs.
Founder-blocking approval is visually distinct from world/timer waits and sorts
first in the return digest. Routine progress is polite and coalesced; approval,
failure, and uncertainty are prominent without modal interruption of typing.

A background-prepared approval carries field-level `PreparationInfluenceRef`
records for every material recipient/target, template-declared content slot,
attachment, and timing value derived from an external or otherwise untrusted
source. Each ref binds the approval field/slot hash to the current authorized source ID,
source/version hash, trust class, citation/extraction evidence, and freshness.
Assembly preserves those exact bytes through the approval/action subject hash.
The decision card visibly labels the influenced fields and exposes their
sources; polished copy may not hide that provenance. This follows doc 39's
attributable-slot pattern but does not mislabel ordinary external evidence as a
memory item.

Free-form prepared prose makes no span-level attribution claim. Its immutable
draft artifact instead carries a document-level `PreparationSourceDisclosure`
listing every currently authorized untrusted source that informed generation,
and the approval card displays “Informed by external sources” with those source
links. A template that requires field attribution must generate declared slots
and preserve them verbatim through assembly; if it cannot, it uses the coarse
document disclosure or is ineligible for approval preparation.

Voice is another Alex surface, not a separate worker persona:

- Alex may speak the initial acceptance during the founder's live voice turn;
- background jobs never start the microphone, play unsolicited audio, or barge
  into an active voice response;
- updates first arrive as accessible captions/run cards/inbox items;
- if the founder asks by voice, Alex reads the current durable snapshot and may
  speak its closed status caption;
- all spoken status has an identical text caption and preserves reduced-motion,
  screen-reader live-region, focus, and contrast requirements from 16;
- worker/tool/skill/model names and hidden reasoning are never spoken or shown.

## 8. Specialist roles and skills

Use the bounded roles in 34 §6.3. Alex owns intent clarification, job proposal,
status narration, and founder dialogue. Researchers retrieve; analysts compare;
writers prepare; validators advise; distillers propose memory changes. None may
approve, mutate run/domain authority directly, select a new recipient, or call
an undeclared effect. A typed output and deterministic completion contract—not
an ADK transfer—ends a specialist step.

A **skill** is a versioned instruction/resource bundle for one or more allowed
roles. It is not a capability, credential, tool grant, persona, workflow, or
policy. Doc 37 exclusively owns packaging, installation, qualification,
lifecycle, and the canonical manifest. This job contract consumes its actual
bindings; it defines no second skill schema:

```text
skill_id, version, status
assignments.agent_roles[], assignments.workflow_steps[]
contracts.{input,output,error}_schema_id
contracts.{completion,context,evidence}_contract_id, citation_policy_id
capabilities.allow[], capabilities.effect_policy, required_permissions[]
policy.{accepted,produced}_data_classes, tenant_scope, precondition_ids[]
execution budgets, retry_policy_id, idempotency_contract_id
provenance.playbook_hash, reviewed_capability_set_hash
resources[].sha256
evaluation.suite_ids[], release_gate_id
lifecycle.{rollout_policy_id,rollback_to,replaces,deprecated_after}
```

The plan binds exact skill versions through `assignments.workflow_steps`.
`DRAFT`, `QUALIFIED`, `CANARY`, `ACTIVE`, `DEPRECATED`, `DISABLED`, and
`RETIRED` retain the meanings in doc 37; only the exact environment/cohort
permitted by that lifecycle may claim work. Server code intersects
`capabilities.allow` with the step allowlist, so a skill can only reduce, never
expand, access. Skill text and referenced resources are placed in the
instruction section only after hash/registry verification; founder artifacts,
retrieved pages, emails, memories, and tool results remain delimited data.
Installation or version change cannot alter in-flight plans. An ineligible,
disabled, retired, or hash-mismatched skill blocks a new claim with a visible
safe error and no fallback to an unreviewed prompt.

## 9. Memory, artifacts, and evidence

Workers receive the minimum job-scoped context pack from 34 §6.4. The input
manifest distinguishes confirmed workspace facts, actor-private preferences,
domain records, task-scoped artifacts, institutional datasets, and advisory
episodic recall. Every source carries purpose, visibility, sensitivity,
retention, version/hash, and provenance.

Memory search is server-filtered and re-authorized at execution time. Recall
may ground a draft but cannot become authority, satisfy a required fact, change
a target, resolve a wait/approval, or prove an effect. A missing/degraded memory
backend is explicit, not an empty result.

Specialists never write durable memory directly. A declared material milestone
may create an idempotent memory-proposal outbox item. Confirmation and conflict
rules from 34 §7 apply; task-scoped data is not promoted to workspace memory by
default. The proposal and every retry use doc 39's canonical
`MemoryWriteCandidate` identity, including workspace, subject kind/ID,
milestone, memory kind, projection slot, and sorted source refs/versions; doc 40
defines no local “source/policy” retry key. Cancellation, source deletion, and
retention jobs enumerate outputs, links, derived memory proposals/items, and
embeddings by source reference. Doc 39 owns the cross-session memory lifecycle;
this document only binds a job to explicit source IDs, purposes, policy
versions, and write receipts.

Live vision is not a background-job input or execution lease. Under doc 38, a
background job may consume only an explicitly confirmed, workspace/session-
scoped **still-image attachment** with an immutable hash, purpose, expiry, and
sensitivity label. Ephemeral live camera/display frames are never persisted or
passed to a job, and clips are unsupported. A job cannot keep a camera or
live-media session open, silently capture a still, control voice/video UI, or
infer continuing consent. A request that needs live back-and-forth remains
`NON_DETACHABLE`.

## 10. Authorization, privacy, and tenancy

- Interactive identity, workspace, actor, membership, and origin session are
  server-derived. Caller/model-supplied ownership fields are ignored/refused.
- Job scope uses doc 39's `subject_kind`/`subject_id` and
  `WORKSPACE | ACTOR_PRIVATE` scope semantics. Actor-private is the default;
  workspace sharing requires a reviewed template visibility policy. Before the
  multi-member identity/privacy migration passes, no flag may claim shared or
  cross-actor behavior.
- Admission, each claim, each capability call, output commit, delivery, and
  read recheck workspace ownership, origin actor, subject/scope, current
  membership, and grants.
- Workload identity is audience-bound to a private route and the exact
  workspace/run/step/capability. It never acquires a human role.
- Datastore queries are workspace-scoped. Foreign and absent point reads use
  the same not-found shape; foreign SSE cursors and session delivery attempts
  are refused and audited without content.
- `delivery_session_id`, job listing, inbox, projection/SSE, output links, and
  conversation deliveries re-authorize the exact actor and visibility policy;
  workspace membership alone cannot reveal an actor-private job's existence,
  counts, timing, status, or identifiers.
- Artifact and memory references use opaque IDs, current visibility grants,
  sensitivity ceilings, and purpose binding. Signed URLs are short-lived.
- Secrets are fetched by canonical name only inside a registered adapter and
  never enter a plan, task body, model context, milestone, log, or SSE payload.
- Standard logs and projections contain IDs, hashes, closed codes, versions,
  counts, and timings—not user text, candidate details, drafts, secrets, raw
  provider bodies, or approval tokens.
- Connector revocation, membership revocation, deletion tombstones, retention,
  residency, and legal holds fence future claims immediately.
- Domain-specific policy/evals remain mandatory. This generic facility does
  not make protected hiring, finance, compliance, or outreach data generally
  detachable.

## 11. Errors, reconciliation, and degradation

Every boundary returns the 34 §5.8 error envelope; tools return errors as data.
Closed categories include `policy_refused`, `clarification_required`,
`authorization_failed`, `input_stale`, `skill_unavailable`, `budget_exhausted`,
`retryable_dependency`, `lease_lost`, `cancelled`, `validation_failed`,
`delivery_dead_letter`, and `reconciliation_required`.

Reconciliation is deterministic and bounded:

| Drift/failure | Recovery |
|---|---|
| accepted job, missing task receipt | replay the same dispatch outbox/task name |
| expired step lease | fence generation; reclaim same step within retry budget |
| output exists, completion ambiguous | verify output key/hash; commit once or quarantine |
| run event/projection mismatch | replay registered reducers; quarantine unknown version |
| authority committed, projection missing | publish pending outbox; snapshot remains correct |
| inbox delivered, conversation append ambiguous | query `(delivery_id, event_ordinal)`; append once or dead-letter |
| quiet/expired SSE | snapshot active jobs/inbox/approvals, then resume cursor |
| approval stale/expired | void it and prepare a new exact approval if still requested |
| action outcome uncertain | use registered read-only provider reconciliation; never resend |
| memory write failed | show degraded recall; retry the same doc-39 `MemoryWriteCandidate` identity/request hash |

Operator scans use indexed due times and stable keys. They may fence, replay,
reconcile, or quarantine under 35; they may not edit immutable events, invent a
success, synthesize founder approval, or pick a “latest” session.

## 12. Observability, SLOs, and budgets

Every command, job, step, attempt, model call, capability call, output,
milestone, wait, approval, action, delivery, memory operation, and projection
shares `workspace_id`, `command_id`, `run_id`, `step_id`, `attempt_id`,
`session_id`, `causal_event_id`, and trace IDs where applicable.

Required metrics:

- message acknowledgement and durable job-accept latency;
- queue age, claim latency, active versus dormant time, lease expiry/reclaim;
- completion/failure/cancellation/uncertainty and retry rates by template/step;
- session turn contention, queued messages, delivery lag/dead letters, duplicate
  append prevention, and out-of-order refusal;
- milestone coalescing and time from authority commit to inbox/SSE/snapshot;
- SSE reconnects, snapshot-required frequency, epoch resets, stale aggregate
  rejection;
- model/provider latency, tokens, cost, repeated-compute cost, cache use;
- external-read destinations by closed class, denied steering attempts, calls,
  bytes, latency, and budget exhaustion—never raw target/query content;
- citation/completion-contract pass, correction/override, skill version/eval;
- authorization/policy denials, cross-tenant probes, deletion completion.

Numeric SLOs and alerts are added to 35's versioned platform operations policy;
`NO_DATA` blocks promotion. Alert on oldest queue age, repeated lease expiry,
projection/outbox backlog, session delivery dead letter, security denial spike,
budget anomaly, and reconciliation backlog.

Job and workspace budget ledgers atomically consume idempotent receipts for
steps, model calls/tokens/cost, provider reads, external reads/bytes, artifact
bytes, active runtime, and retries. Admission reserves a ceiling; claims check
remaining capacity; commit records actual use. Exhaustion pauses or fails with
a visible closed reason—never silently upgrades a model, drops validation,
removes citations, or invokes an effect. Per-workspace and per-template
concurrency limits protect interactive turns and existing action/wake lanes
from background starvation.

Cancellation and every terminal transition release unused admission reservation
through the same version-fenced, idempotent terminal transaction and budget
receipt. Repair may replay that release; it cannot release twice or leave
cancelled/failed jobs consuming concurrency or cost capacity.

## 13. APIs and private dispatch

Existing public contracts gain only additive fields/semantics:

```text
POST /api/v1/messages
  -> 200 existing completed reply plus optional
     {background_job: {job_id, run_id, status, location}}
  -> 202 when another turn owns the session lease
     {session_id, client_request_id, command_id,
      turn_delivery: {delivery_id, sequence, status: PENDING},
      deliveries_url}

GET  /api/v1/events/stream
  -> existing workspace stream; events, cursors, snapshots, and
     snapshot_required responses add stream_epoch
```

New public contracts:

```text
POST /api/v1/jobs
GET  /api/v1/jobs?status=&cursor=
GET  /api/v1/jobs/{job_id}
GET  /api/v1/jobs/{job_id}/timeline
POST /api/v1/jobs/{job_id}:cancel
POST /api/v1/jobs/{job_id}:retry-step

GET  /api/v1/sessions/{session_id}/deliveries?cursor=
```

`/jobs` is a founder-facing alias/view over background-profile
`workflow_runs`; it does not create a `jobs` collection. Mutations require an
authenticated principal, `Idempotency-Key`, and, where state-sensitive,
`If-Match`. Create returns `202`, durable identifiers, current status, `ETag`,
and `Location`. Reads use opaque workspace/filter-bound cursors. Retry is
available only for a manifest-declared safe failed step; it creates a new
attempt, not a new job.

The `202` message response means accepted and queued, never answered. The client
may render the founder's own message optimistically, keyed by
`client_request_id`, with a pending state; it does not fabricate an Alex reply
or append local text as transcript authority. A scoped SSE delivery update
invalidates that state, and
`GET /api/v1/sessions/{session_id}/deliveries` returns the same delivery with
its fenced status and eventual `result_event_refs`/safe payload. The client
reconciles by `delivery_id` plus event ordinal, so reconnects, duplicate SSE,
and snapshot recovery display the queued founder turn and Alex's eventual reply
exactly once and in reserved sequence.

Private routes are not UI-callable:

```text
POST /internal/v1/background-steps/{step_id}:execute
POST /internal/v1/conversation-deliveries/{delivery_id}:deliver
POST /internal/v1/background-outbox/{outbox_id}:dispatch
POST /internal/v1/background-reconciliation/{issue_id}:run
```

They accept opaque IDs/generations, authenticate workload identity, and return
stored duplicate dispositions. The browser always treats snapshots as
authoritative and SSE as invalidation/observation.

## 14. Data model and migrations

No existing collection is renamed or repurposed. Additive changes:

1. register `alex_background_job:v1`, reviewed job templates, specialist roles,
   skill manifests, completion contracts, retry and milestone policies;
2. add the §3.1 profile fields to `workflow_runs`, §3.2 fields to
   `workflow_steps`, and background event/reducer schemas;
3. add immutable job input/output manifests to existing artifact/metadata
   storage and material outputs to `resource_index` plus
   `session_resource_links`;
4. add `conversation_sequences` beside the ADK event append boundary in Cloud
   SQL and `conversation_deliveries` to Firestore; register each in its
   applicable schema, backup/restore, deletion, index, retention, and operator
   inventory;
5. extend command, run, message, inbox, approval, action, and projection
   snapshots to expose safe job references;
6. add composite indexes for workspace/status/updated job listing,
   run/sequence timeline, session/status/sequence delivery, and due outbox/lease
   repair; exact manifests must be measured against Firestore query plans and
   deployed through `scripts/deploy_firestore_indexes.py`, whose duplicate-
   signature rejection remains a release invariant; every required index must
   report ready before traffic cutover;
7. add projection/outbox retention rules that preserve snapshot recovery and
   active-run visibility;
8. register the versioned `ADKWriteSurfaceManifest` proof tuple in the release
   manifest and CI/deploy policy. The same release check that preserves index
   manifest invariants must reject ADK/Runner/session-service/configuration
   drift without current Gate-B evidence and keep proactive delivery disabled.

Migration protocol follows 34 §13.1: inventory, compatible backup/restore,
expand schemas/indexes, dual-read/shadow-write where required, deterministic
backfill receipts, parity reports, canary, cutover, and rollback rehearsal.
Existing workflow runs are not relabelled as background jobs. Existing ADK
messages receive no fabricated sequence; the sequencer starts from a recorded
cutover frontier. Until a session is migrated, proactive conversation delivery
stays disabled for it while inbox/run-card delivery continues.

Each session has a server-owned sequencing mode `LEGACY | ENABLING | ENABLED`
and a recorded legacy frontier. The cutover command first moves the session to
`ENABLING`; messages accepted from that point create durable deliveries and may
not take the legacy direct-append path. While the mode is `ENABLING`, every
new-path delivery remains pending-only: no Runner starts and no event/state
append targets that session. The cutover drains or generation-fences every
legacy invocation accepted before the frontier, proves none can still write,
then creates and locks the Cloud SQL sequence row at the next append position
and activates the append fence before recording `ENABLED`. Only after
`ENABLED` may the queued new-path deliveries dispatch. Recovery replays the
same idempotent cutover command. No request may be eligible for both append
paths; an ambiguous or failed cutover leaves the session in `ENABLING`, queues
new work without appending, and requires reconciliation rather than guessing a
sequence.

## 15. Rollout gates and feature flags

Flags are server-owned, default off in production, workspace allowlisted,
audited, and versioned:

- `BACKGROUND_JOB_SHADOW_ELIGIBILITY`;
- `BACKGROUND_JOB_ADMISSION_ENABLED`;
- `BACKGROUND_SPECIALIST_EXECUTION_ENABLED`;
- `BACKGROUND_TO_APPROVAL_ENABLED`;
- `SESSION_SERIALIZER_ENABLED`;
- `BACKGROUND_CONVERSATION_DELIVERY_ENABLED`;
- `BACKGROUND_JOB_SSE_ENABLED`;
- `BACKGROUND_SKILLS_ENABLED`.

Feature-flag admission is build-attested, not operator optimism. The proactive
delivery flag can evaluate true only when the running ADK/Runner/session-service
proof tuple exactly matches a current approved `ADKWriteSurfaceManifest`.
Dependency, Runner-mode, plugin, persistence, or session-service changes rerun
Gate B as a CI/release invariant; mismatch fails the release or forces that
capability off before traffic.

Roll out in this order:

1. **Gate A — inventory and contracts.** Classify every current specialist path
   and capability; no undeclared or effectful path is eligible. Freeze golden
   API/event/hash vectors. Prove that each candidate vertical uses native
   authoritative run/step transitions; compatibility or projection-only shadow
   runs may not host jobs.
2. **Gate B — session and delivery foundation.** Ship the sequencer, durable
   outboxes, snapshots, and reconciliation with job execution disabled. Prove
   append-time generation fencing against a lease-expired zombie, exact
   serializer cutover, concurrent message/wake/voice ordering, stream-epoch
   reset after rollback, and quiet-SSE recovery. Freeze the configured ADK
   version/modes and complete the §5 write-surface manifest: every persistent or
   founder-visible event, state-only delta, partial/streaming path, compaction,
   rewind, direct append, and session-linked artifact path is fenced. Prove
   bounded heartbeat renewal and missed-heartbeat repair without livelock. Any
   un-interceptable path blocks proactive conversation delivery.
3. **Gate C — shadow eligibility.** Evaluate real requests without detaching;
   compare policy decisions to labelled review. Zero false-safe effect
   classifications and required clarification recall must pass thresholds.
   Separately measure deterministic intent-to-template misselection, including
   declared-size, adversarial, broad-scope, and multi-template objectives; any
   ambiguous or mismatched selection must clarify or refuse. Numeric
   clarification-recall and misselection thresholds, corpus size/composition,
   and the broad/consequential founder-confirmation policy are recorded and
   frozen before the evaluation runs; they cannot be selected after observing
   results, and `NO_DATA` blocks the gate.
4. **Gate D — synthetic read-only.** One template, one role, synthetic data,
   stubbed providers. Pass duplicate, crash, cancel, budget, deletion, and
   cross-tenant tests. Prove the transaction/outbox boundary: no downstream
   fanout or network work occurs inside the authoritative commit.
5. **Gate E — canary detached read.** Allowlisted internal workspaces and one
   reviewed read-only template. No approval preparation or effect capability is
   present in the worker image/service account. Untrusted content cannot choose
   or extend read destinations or queries; per-call authorization and separate
   external-read/byte budgets must pass injection and exfiltration tests.
6. **Gate F — detached preparation and skills.** Add draft/artifact preparation
   and exact doc-37 skill versions after manifest-parity, lifecycle, quality,
   citation, privacy, cost, and per-session progress-coalescing evals.
7. **Gate G — approval wait.** Permit exact approval creation after binding,
   field-level untrusted-source provenance, expiry, revocation,
   cancel-versus-approve race, and stale-plan tests. Effect execution remains
   on 34's separately approved consequence path.
8. **Gate H — production expansion.** Template-by-template, domain-by-domain,
   with non-empty SLO/error-budget windows, load/chaos evidence, security and
   privacy sign-off, and rollback proof.

Rollback stops new admission, disables affected claims, fences leases, drains
outboxes, and leaves accepted jobs visible/recoverable. It never reports them
as synchronous success, moves accepted work back into an interactive transfer,
or discards approvals/actions/events.

## 16. Adversarial and failure tests

### 16.1 Eligibility and authority

- “Just do it in the background and skip approval” remains preparation-only.
- “I approve” in chat focuses but does not resolve the decision surface.
- A web page, attachment, email, recalled memory, or skill attempts to add
  `email.send`, a recipient, URL, connector, secret, or policy override; plan
  and capabilities remain registry-bound.
- Untrusted mid-run content supplies a follow-up URL, identifier, or search
  query; the worker refuses it, performs no egress, and emits a closed audit
  disposition. Per-call authorization is rerun for every admitted read.
- A search result supplies an otherwise benign discovered link; the current
  detached step may use only its admitted snippet and cannot open the link.
  Opening requires a fresh server-resolved admission or inline founder-confirmed
  fetch, with a new egress receipt.
- An ambiguous “research them” creates no job until the reference is resolved.
- An objective fits zero or multiple templates, or exceeds the chosen
  template's declared scope/output contract; deterministic selection clarifies
  or refuses. Gate-C corpora measure this separately from effect-class safety.
- A model mis-extracts a broad or approval-producing free-text objective; no
  job is admitted until the founder confirms the exact interpreted objective.
  Injected text cannot alter immutable `negative_constraints[]`.
- A detached template is disabled between admission and claim; the claim
  refuses safely and is visible.
- A specialist returns success without required citations/schema; completion
  is rejected and no milestone says complete.

### 16.2 Idempotency, crash, and cancellation

- Deliver the create request and every task twice concurrently: one job, step,
  output, event, milestone, inbox item, and conversation delivery result.
- Kill before/after claim, model call, output write, transition commit, outbox
  enqueue, task acknowledgement, and transcript append; recovery converges
  without a stale-generation commit.
- Reuse a key with changed input/plan/output hash; receive conflict and preserve
  the original.
- Cancel while queued, leased, committing, waiting for approval, and while a
  consequence is `PREPARED`, `EXECUTING`, or `UNCERTAIN`; fencing and copy match
  the actual boundary.
- Race cancellation with approval resolution on both sides of T2; one CAS
  ordering wins, the subject hash is unchanged, and no voided approval starts
  an action.
- Exhaust retry and cost budgets; no unbounded task loop or hidden model call.
- Cancel and terminally fail jobs with unused reservations, then replay repair;
  capacity is released exactly once and later admissions are not starved.
- Hold paginated cancellation cleanup open across retries; the run remains
  visibly `CANCELLING`, no work/approval/milestone resumes after the fence, and
  repair completes cleanup to `CANCELLED` without a second acknowledgement.

### 16.3 Session ordering and delivery

- Submit two founder messages while a turn is active and complete a job between
  them; transcript/delivery order matches reserved sequence and only one ADK
  invocation is active.
- Race voice finalization, approval result, proactive wake, and typed message;
  one session lease wins and all durable deliveries remain ordered.
- Give a proactive worker its delivery lease while a founder turn owns the
  sequence lease; it performs zero append. After it claims the sequence
  generation, its short append is fenced/released and the founder queue proceeds.
- Let a turn lease expire mid-Runner invocation, advance the generation, start
  repair, then let the original invocation finish; every stale intermediate and
  terminal append is rejected by the Cloud SQL append transaction.
- Inject a state-only delta, compaction/rewind event, direct append, and each
  configured partial/live persistence path from a superseded generation; every
  persistent or founder-visible write is fenced, and the Gate-B manifest has no
  unknown path.
- Change the pinned ADK build, Runner mode, plugin, persistence adapter, or
  session-service configuration without a matching proof; CI rejects release
  and runtime keeps proactive conversation delivery disabled until Gate-B
  evidence is regenerated.
- Run a legitimate long invocation beyond the initial TTL while healthy
  generation-checked heartbeats renew it; no repair or recomputation starts.
  Stop heartbeats and prove bounded generation-fenced repair before the absolute
  turn deadline, with cost receipts for any repeated compute.
- Crash after ADK append but before marking delivered; repair finds
  `(delivery_id, event_ordinal)` and does not append twice.
- Dead-letter a conversation append; run card/inbox remain correct and the next
  founder turn is not falsely told the job failed.
- Change `delivery_session_id` during an event race; CAS chooses one authorized
  target and never “latest session.”
- Submit messages at the recorded serializer frontier while cutover crashes and
  retries; each message takes exactly one path and obtains exactly one sequence.
- Hold a session in `ENABLING` while legacy work drains; new messages receive
  durable pending deliveries but produce zero event/state appends until
  `ENABLED`.
- Target a `LEGACY`, `ENABLING`, unavailable, and newly unauthorized delivery
  session; no force-append or loss occurs, and the actor-private inbox/run card
  remains the authorized fallback.
- Queue a founder message behind an active turn; its `202` identifiers remain
  stable, the optimistic message reconciles, and Alex's reply arrives exactly
  once in sequence through the delivery ledger after reconnect/duplicate SSE.

### 16.4 SSE, projection, and UX

- Leave a job quiet beyond the stream horizon/replay retention, expire browser
  demand, reconnect, and require snapshot recovery with the active card intact.
- Drop/reorder/duplicate SSE events; per-aggregate versions prevent regression.
- Restore a snapshot with lower projection versions and a rotated
  `stream_epoch`; clients discard old cursors/watermarks before accepting the
  fresh authorized snapshot and reject an epoch-less rollback response.
- Keep a client holding every previously issued epoch across a restore; the
  external restore-generation authority mints a never-used value and forces a
  fresh authorized snapshot.
- Fail projection publication after authority commit; outbox repair publishes
  once and the snapshot was always correct.
- A dormant wait shows no motion; only a valid lease with a recent execution
  heartbeat shows motion; an expired heartbeat becomes a static stalled
  warning, and SSE keepalive alone never shows work.
- Advance many jobs in one session at once; routine items respect both per-job
  and per-session windows while failure, approval, uncertainty, cancellation,
  and terminal items remain immediate.
- Screen reader, keyboard, reduced motion, AA contrast, closed captions, and no
  unsolicited audio pass for progress, failure, and approval.

### 16.5 Security, privacy, memory, and skills

- Foreign workspace job/session/artifact/memory/output/cursor IDs return the
  collapsed denial and emit no content.
- Revoke membership, connector, source grant, or artifact visibility after
  queueing; the next claim/call/commit is fenced.
- A task-scoped artifact or actor-private preference is not written to shared
  memory or exposed to another actor.
- Actor A starts a default-private job; actor B observes zero job, inbox, SSE,
  delivery, output, count, timing, or identifier signal. Changing
  `delivery_session_id` to B's session is refused even inside one workspace.
- Delete a source during execution; late output is quarantined and all derived
  indexes/memory are removed through receipts.
- Tamper with a skill hash or let a skill request an undeclared tool; the step
  refuses and cannot fall back to free-form instructions.
- Prepare an approval from external evidence; every influenced target,
  recipient, declared content slot, attachment, and timing field has a current
  `PreparationInfluenceRef`, and the approval/action subject preserves the
  exact reviewed bytes and source/version hashes.
- Prepare free-form prose from external evidence; the card makes no arbitrary
  span claim and displays the complete document-level authorized source
  disclosure. A declared attributable slot remains byte-exact through action.
- Scan task bodies, standard logs, milestones, inbox, SSE, and captions for raw
  content, secrets, approval tokens, protected data, and chain-of-thought.

## 17. Acceptance checks

- [ ] Alex is the only founder-facing identity; no specialist sends chat or
      voice directly.
- [ ] Eligible ordinary-message work returns after durable acceptance and
      continues through container/session/browser closure.
- [ ] `job_id == run_id`; no parallel jobs authority or swarm is introduced.
- [ ] Eligibility is deterministic, registry/policy/version bound, and refuses
      ambiguity, intent/template mismatch, untrusted read routing, unsupported
      data, undeclared capabilities, and effects; broad/sensitive/consequential
      free-text intent is founder-confirmed before validation.
- [ ] Detached reads are allowlist/snippet-scoped; a discovered link cannot be
      followed without fresh admission or an inline founder-confirmed fetch.
- [ ] Duplicate delivery, crash, lease expiry, retry, and cancellation preserve
      one committed output/event identity and fence stale work.
- [ ] Same-session ADK invocations serialize; queued messages, wakes, voice, and
      job updates are durably ordered and idempotently appended; a superseded
      Runner generation cannot append any event or state delta; every configured
      ADK write surface has frozen interception evidence.
- [ ] Any ADK/Runner/plugin/session-service/persistence configuration change
      invalidates and reruns that proof; CI/runtime fail closed with proactive
      delivery disabled on a mismatch.
- [ ] Healthy long turns renew bounded leases without repair churn; missed
      heartbeats and absolute deadlines trigger generation-fenced recovery.
- [ ] A queued founder message has an explicit `202`/delivery contract and its
      eventual Alex reply reconciles exactly once in reserved sequence.
- [ ] A proactive delivery lease alone cannot append; the worker must own and
      release the fenced conversation turn generation for its append section.
- [ ] Active jobs, approvals, failures, uncertainty, and completion survive a
      quiet/expired SSE connection and a rollback epoch through snapshot +
      inbox recovery.
- [ ] Milestones are durable, closed-vocabulary, coalesced, honest, and useful;
      internal steps/tool/agent/model names never leak.
- [ ] Cancellation fences immediately, shows a closed `CANCELLING` state during
      asynchronous cleanup, and never resumes work after the fence.
- [ ] Approval creation is non-blocking and exact; effect execution can occur
      only through the existing approval/action/consequence protocol; fields
      influenced by untrusted sources retain exact, visible declared-slot
      provenance, while free-form prose carries honest document-level source
      disclosure rather than unverifiable span claims.
- [ ] Skill bindings use doc 37's canonical manifest/lifecycle unchanged, are
      immutable, and can never widen the role/capability, context, memory,
      tenancy, or policy envelope.
- [ ] Background vision input is limited to a founder-confirmed immutable still
      attachment; no ephemeral frame, clip, or continuing capture is retained.
- [ ] Every enabled vertical uses native authoritative run/step transitions;
      a shadow compatibility run cannot host a background job.
- [ ] Workspace, actor, artifact, memory, connector, deletion, residency, and
      retention rules are rechecked at every boundary.
- [ ] Jobs are actor-private by default; workspace sharing is explicit policy,
      and cross-actor inbox/SSE/delivery/output existence leakage is zero.
- [ ] A non-`ENABLED` or unauthorized target session receives no proactive
      append; the origin actor's inbox/run card remains the durable fallback.
- [ ] Numeric budgets, SLOs, alerts, reconciliation, load/chaos, backup/restore,
      and rollback evidence are non-empty before production promotion.
- [ ] Existing discovery, investor, grant, hiring, browser, approval, action,
      memory, session-resource, unified UI, and durable-runtime suites remain
      green with all flags off and at each canary gate.

## 18. Decision log

| ID | Decision | Rejected alternative | Reason |
|---|---|---|---|
| D40-01 | A job is a constrained `WorkflowRun`; `job_id == run_id`. | new `jobs` engine/collection | one authority, lease, event, cancellation, and recovery model |
| D40-02 | Alex alone talks to the founder. | specialist personas posting directly | preserves trust, voice, reference resolution, and a single conversation |
| D40-03 | Code admits detachment from registered templates. | model decides whether any work is safe | eligibility and effect boundaries must be deterministic and reviewable |
| D40-04 | Preparation may detach; effects use the consequence kernel. | let a background specialist send after prose consent | preserves exact approval binding, idempotency, receipts, and reconciliation |
| D40-05 | Same-session inference and delivery serialize durably. | concurrent proactive `run_async` calls or process locks | works across containers/restarts and makes ordering testable |
| D40-06 | Inbox/snapshot are durable; SSE is observation. | subscriber demand or a live stream owns progress | disconnected clients must not lose or stall work |
| D40-07 | Milestones are policy-selected and closed-copy. | expose every tool/agent step or model-written status | reduces noise and prevents false/unsafe operational claims |
| D40-08 | Skills are immutable instruction bundles, never grants. | skills carry tools/credentials or dynamically expand plans | keeps capability, tenancy, policy, and eval authority in code |
| D40-09 | Workers get minimum job-scoped context. | copy the whole founder transcript into each worker | limits leakage, injection, cost, and cross-run contamination |
| D40-10 | No proactive transcript append before serializer proof. | ship best-effort wakes first | a run card/inbox is safer than corrupting or racing the conversation |
| D40-11 | Rollback preserves accepted jobs and fences execution. | fall back to synchronous transfer or delete jobs | accepted durable work remains auditable and recoverable |
| D40-12 | Unknown-duration work uses phases, not percentages/ETAs. | model-estimated progress | avoids fabricated certainty while keeping work legible |
| D40-13 | Session event append is generation-fenced in the same Cloud SQL transaction. | pre-checks, Firestore-only leases, or process locks | a superseded Runner must be unable to append after repair starts |
| D40-14 | External reads use immutable server-resolved routing and a separate egress budget. | let retrieved content choose the next URL/query | read chains can exfiltrate data even when they do not mutate a provider |
| D40-15 | Background steps consume doc 37's canonical skill manifest and doc 38's explicit-still boundary unchanged. | define local skill fields or retain live frames/clips | companion specs must have one owner and one privacy contract |
| D40-16 | Approval fields derived from untrusted sources preserve attributable provenance through exact action bytes. | cite only the finished draft | the founder must see which material decision fields external content influenced |
| D40-17 | Projection replay includes a resettable opaque stream epoch. | compare only monotonically increasing aggregate versions | restore/rollback can legitimately lower versions and otherwise strand clients on stale watermarks |
| D40-18 | Gate B proves a version-pinned complete ADK write-surface manifest. | assume wrapping common `append_event` covers every path | an unenumerated state, streaming, compaction, direct, or artifact path can bypass the generation fence |
| D40-19 | Jobs are actor-private by default; workspace visibility is reviewed template policy. | infer job sharing from workspace membership | multi-member work must not expose another actor's activity or outputs |
| D40-20 | Turn leases renew by bounded generation-checked heartbeat up to an absolute deadline. | fixed short TTL or long unrepaired TTL | avoids healthy-turn churn while retaining bounded crash recovery and cost |
| D40-21 | Deterministic template validation consumes direct or founder-confirmed interpreted intent. | call model-based objective extraction deterministic | the comparison is only as trustworthy as its safety-bearing input |
| D40-22 | A lease-contended founder message returns an explicit queued-turn delivery contract. | leave a synchronous endpoint's deferred result implicit | the UI must reconcile the founder message and eventual Alex reply exactly once |
| D40-23 | Structured slots get field provenance; free-form prose gets document-level source disclosure. | claim arbitrary model-text span attribution | disclosures must be complete and objectively testable |
| D40-24 | Stream epochs come from a non-restored monotonic generation and are never reused. | restore or increment an epoch stored inside the rollback domain | a long-idle client must never mistake restored history for its current stream |
| D40-25 | ADK write-surface proof is invalidated by any dependency/runtime configuration drift. | freeze proof only at initial Gate B | a new persistence path must fail CI/runtime closed before it can bypass fencing |
| D40-26 | Detached research is allowlist/snippet-scoped; discovered links require a new boundary. | unattended transitive open-web browsing | result content cannot grant a new egress destination |
| D40-27 | A proactive worker must claim the conversation turn generation in addition to its delivery lease. | treat queue ownership as append authority | delivery processing and same-session serialization are different authorities |
| D40-28 | Cancellation fences immediately and exposes terminal-pending cleanup. | hide a long cleanup or resume work while stopping | founder copy and runtime authority must remain honest during bounded cleanup |
| D40-29 | A non-enabled target session degrades to the actor's inbox/run card. | force append, force migration, or drop the update | delivery preference cannot weaken serializer safety or durable visibility |
