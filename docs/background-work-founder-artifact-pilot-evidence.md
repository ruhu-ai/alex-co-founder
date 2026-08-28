# Document 40 — founder artifact-analysis pilot evidence

**Recorded:** 2026-08-28
**Scope:** one default-off, founder-only, read-only live pilot
**Release state:** Gate E Activity soak completed in the existing project;
normal service deployed with every pilot gate off and kill switch on

This slice is a deliberately narrower controlled pilot than the generic Gate C
through Gate E progression in `40-durable-background-work.md`. It does not
approve those gates for any other template, actor, workspace, data source, or
delivery channel.

## Exact pilot capability

The only live template is `pilot.artifact_evidence_inventory@1`. An authenticated
FOUNDER may choose one already-ingested, session-bound artifact and ask
Alex to produce a deterministic evidence inventory in the background. The
worker reads only the authoritative artifact row and its current indexed
chunks. It records chunk, word, character, locator and citation-hash metadata;
it does not copy source text into the output.

The template is code-owned and closed. The server fixes the objective, plan,
step, capability, queue, worker route, completion contract, and negative
constraints. Inputs contain one 32-hex artifact identifier, never a model-
authored tool name, URL, query, connector, recipient, or provider identifier.
The execution profile has `NONE` authority for external reads, effects,
approvals, and memory writes, empty skill bindings, and zero model calls,
provider calls, and tokens.

Admission requires an interactive `FOUNDER`, exact workspace/session/artifact
ownership, a ready immutable artifact version and hash, an allowlisted
workspace, matching `Idempotency-Key`, and all enable flags. Limits are one
concurrent job per actor/template, three admissions per rolling hour, one step,
two retries, three attempts, 30 active seconds, 120 wall seconds, 5 MiB input,
100 indexed chunks, and 64 KiB output. Command receipt, outbox, run, plan,
initial event, immutable input manifest, and capacity reservation commit in one
atomic operation.

The dispatcher can create only the reviewed `analyze_artifact` step and sends
only `{workspace_id, run_id, step_id}` to the dedicated
`co-founder-background-pilot` Cloud Tasks queue. The private route requires its
exact audience and `background-pilot-worker@` identity. The executor rechecks
the full authority ceiling, claims a lease, records attempts, fences success
and failure by lease expiry and cancellation generation, retries only closed
safe errors, and atomically commits the immutable output, terminal event, and
capacity release. Duplicate admission, dispatch, task delivery, completion,
failure, and cancellation converge on their existing durable identity.

For local testing only, an explicitly enabled adapter asynchronously signs and
delivers that same opaque payload to the exact loopback worker route. It refuses
non-loopback origins, a changed audience, another route/queue, scheduled work,
a short or missing test secret, and all Cloud Run environments. It is not a
production substitute for Cloud Tasks.

## Founder-visible ordering and migration seam

The existing contextual Activity surface shows actor-private queued, running,
completed, failed, cancelling, and cancelled truth plus bounded output counts.
It also exposes the background action only beside eligible artifacts. The
funding-specific Runs page is unchanged and no generic run dashboard or SSE
stream was introduced.

Founder-visible captions are a closed projection of authoritative `run_events`
ordered by the run's atomic `next_event_sequence`. Event ids and sequence
deduplication prevent duplicate or out-of-order display. This is the safe
Document-40 migration seam while the Gate-B Cloud SQL conversation append
fence remains unproved. The pilot does **not** append proactive ADK transcript
messages, create `conversation_deliveries`, or enable
`BACKGROUND_CONVERSATION_DELIVERY_ENABLED`.

## Flags, rollback, and operations

Every enabling flag defaults off and the kill switch defaults on:

```text
BACKGROUND_JOB_ADMISSION_ENABLED=false
BACKGROUND_SPECIALIST_EXECUTION_ENABLED=false
BACKGROUND_CONVERSATION_DELIVERY_ENABLED=false
BACKGROUND_ARTIFACT_PILOT_ENABLED=false
BACKGROUND_ARTIFACT_PILOT_EXECUTION_ENABLED=false
BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH=true
BACKGROUND_ARTIFACT_PILOT_WORKSPACES=
TASKS_BACKGROUND_PILOT_SA=
BACKGROUND_PILOT_WORKLOAD_ALLOWLIST_JSON={}
```

Clearing the kill switch alone enables nothing. Admission additionally needs
both admission flags and an exact workspace allowlist entry; execution needs
both execution flags and the same allowlist. The worker identity allowlist is
independent. Re-enabling or widening generic background work is not possible
through these flags.

The kill switch and execution rollback stop new claims without falsely failing
accepted work. Admission rollback stops new jobs. Accepted jobs remain visible
and recoverable in Activity. Cancellation remains available and generation-
fences a late worker. Safe structured metrics cover admission, denials,
dispatch, blocked execution, attempts and completion; SLO observations cover
start and completion. Audits cover denial, acceptance/dispatch disposition,
blocked execution, attempt outcome, completion, and cancellation. The
deployment definition adds the dedicated concurrency-one, max-attempts-three
queue and route-scoped invoker identity, but this work did not run deployment,
restart, or enablement commands.

## Focused verification

The complete repository unit run passed `1144` tests with `2` skipped. The
focused implementation/regression runs also passed. Together they cover
default-off and kill-switch behavior; role, workload, workspace, session,
actor, and tenant boundaries; malformed and URL-shaped inputs; authority and
manifest tampering; idempotency conflict, duplicate delivery and concurrent
admission; durable rate and concurrency limits; lease expiry, retry accounting,
cancellation races and late commits; ordered unique captions; actor-private
outputs; rollback visibility; no approvals, external actions, conversation
delivery, SSE, or memory path; route validation; UI truthfulness; collection
and lifecycle registration; plan/runtime regressions; and the bounded cloud
queue audit.

Touched-file Ruff, `git diff --check`, deployment shell syntax, Firestore index
JSON parsing, and the Document-16 contrast checker pass. The app regressions
emit pre-existing ADK/gRPC resource warnings while passing.

### FOUNDER policy and local end-to-end evidence

The pilot has one product role: `FOUNDER`. Authentication resolves one FOUNDER
identity; the pilot does not accept, display, or infer a separate administrator,
manager, observer, or multi-user authority. The workspace flag remains only a
single-tenant canary boundary and grants no role or capability.

After explicit founder authorization, the isolated local host created exactly
one ephemeral synthetic record set in `InMemoryDurableStore`:

```text
workspace_id  local_spec40_workspace
actor_id      local_spec40_founder
role          FOUNDER
session_id    local_spec40_session
artifact_id   40404040404040404040404040404040
artifact      SYNTHETIC_LOCAL_TEXT, READY, 154 bytes, 2 chunks
```

The two chunks are harmless authored fixture sentences containing no real user
or external data. The process loaded no main `.env`, cloud project,
credentials, Cloud SQL, Firestore, model, browser, or connector. The existing
port-`8090` app was not restarted or modified.

The separate loopback server on port `8092` passed health and authentication
checks and served the repository's normal product shell with only synthetic,
read-only companion projections. Activity showed the eligible synthetic
artifact and its contextual **Analyze safely in background** action; it did not
add a Runs page or generic background surface. A founder-cookie bootstrap was
required before the shell or pilot APIs would load. Reloading the shell retained
the three authoritative job rows and their durable output summaries without a
console error.

The first job was launched from that Activity action (`run_cfc000cc53700b38ff004d80d78c`)
and its authoritative projection and event timeline observed
`QUEUED -> RUNNING -> SUCCEEDED`. A second job
(`run_3d2167ecb6d5528ed5d6a25a1611`) received one explicitly armed synthetic
transient inspection failure. Its ordered captions showed queued, started,
temporary failure/retry, restarted, and completed; the durable attempt ledger
contained exactly one failed attempt before successful recovery. A third job
(`run_a73b1a0afe23745816f34782f39c`) observed
`QUEUED -> CANCELLING -> CANCELLED`, proving cancellation fencing.

The two successful durable outputs each contained 2 chunk citations, 29 counted
words, locator/hash metadata, and the same deterministic content hash
`sha256:cc7a526a4961d16d065f95dd69874001fb8edf51b387b8ff46c3dce7cd456a92`;
output inspection proved no content/text field. Approvals, external actions,
conversation deliveries, wakes, memory items, and memory-write receipts all
remained empty. An unsigned worker returned 401, a URL/tool-name injection
returned 422, and a generic background route returned 404.

To repeat, set two distinct local-only secrets of at least 32 characters and
run `scripts/run_background_pilot_local.sh`; in another terminal, export the
same founder key and run `scripts/exercise_background_pilot_local.py`. The
normal shell is available only on that loopback host after bootstrapping its
HttpOnly local founder cookie with the same key. Restart the harness before
repeating because its deterministic scripted identities intentionally collapse
replays. In Alex, the exact founder action is: **Activity -> Analyze safely in
background** beside the eligible artifact. The row then shows authoritative
queued/running/terminal status and a Cancel action while non-terminal.

Rollback is immediate: set `BACKGROUND_ARTIFACT_PILOT_KILL_SWITCH=true`, then
set both pilot-specific enable flags false and both shared background
admission/execution flags false; keep conversation delivery false and stop the
port-`8092` process. Stopping the synthetic host deletes its membership,
session, source chunks, runs, events, audits, and outputs from process memory.

## Exact residual gates before broader rollout

1. Deploy and independently verify the new Firestore composite index, Cloud
   Tasks queue, route-scoped identity/audience, IAM, metrics, alerts, SLO
   dashboards, backup/restore behavior, and operator kill/repair runbook in a
   non-production environment; then run crash/timeout/duplicate/cancel chaos
   against the real queue and Firestore transaction adapter.
2. Obtain a recorded founder/internal-workspace canary approval with the exact
   release digest, workspace boundary, enablement window, rollback operator, and
   empty-to-nonempty SLO observation criteria. No flag is approved by this
   implementation evidence.
3. Complete the full Gate-A inventory, registry governance and frozen golden
   vectors. This slice proves only one closed capability path.
4. Complete Gate-C labelled shadow eligibility thresholds and corpus evidence
   before any ordinary-message auto-selection. This pilot is an explicit
   contextual artifact action, not autonomous intent-to-template selection.
5. Complete Gate-B Cloud SQL sequence migration, generation-fenced ADK append,
   write-surface attestation, delivery worker/reconciliation, actor-private
   inbox/SSE recovery, and concurrency/voice/accessibility evidence before any
   proactive transcript or stream delivery.
6. Complete Gate-D real synthetic-environment and Gate-E canary evidence before
   adding a second template or any external retrieval. External reads require
   a separate authorized data-source contract, egress allowlist, injection and
   exfiltration tests, and per-call/read/byte budgets; none exists here.
7. Pass load, deletion, retention, restore, privacy/security review, and
   non-empty error-budget windows before multi-user or broader production use.
8. Document 39 memory, skills, drafting/preparation, approvals, consequences,
   browser actions, connectors, email, calendar, hiring evidence, autonomous
   detachment, generic Runs, and generic SSE remain separately gated and
   disabled.

## Non-production canary attempt: fail-closed blocker

On 2026-08-28 the Founder authorized the next non-production canary. No cloud
mutation was made because the environment preconditions failed before resource
inspection:

- the only local gcloud configuration is `default`, selecting
  `co-founder-506001` as `ijidai@ruhu.ai`;
- repository verification evidence identifies that project as the existing
  public deployment, including its production Cloud Run revisions, so it cannot
  be repurposed as a canary;
- no separately named or configured dev, staging, test, recovery, or canary GCP
  project is present in repository or gcloud configuration metadata;
- the selected account cannot currently refresh its token non-interactively,
  and the read-only Phase-7 cloud audit therefore returned `ready: false` with
  only an authentication error; and
- the canary candidate is an intentionally uncommitted worktree, while the
  platform runbook requires cloud evidence against one immutable reviewed
  revision.

The prepared continuation is narrow and explicit. Supply a separately approved
non-production project id (with environment label, billing, region, data
residency, and deletion owner), an authenticated gcloud configuration scoped to
that project, and an enabled verified monitoring notification channel. Freeze
the reviewed pilot as an immutable revision. Before applying anything, rerun the
read-only cloud audit and prove the project contains no real user data. Then
provision only the `co-founder-background-pilot` queue (concurrency 1, attempts
3), route-scoped `background-pilot-worker@` identity and exact Cloud Run
invoker/audience binding, required Firestore indexes, content-free monitoring,
and a FOUNDER-only synthetic workspace/session/artifact. Deploy with repository
defaults still off, then enable only the two shared gates plus the two pilot
gates, exact synthetic workspace allowlist, exact worker allowlist, and clear
the pilot kill switch for the recorded test window. All provider, web,
connector, effect, approval, memory, hiring, SSE, and conversation-delivery
flags remain off.

The canary exit sequence is: admission/outbox redelivery, duplicate delivery,
lease-expiry crash recovery, bounded timeout/retry, cancellation-generation
fencing, actor/workspace privacy reads, content-free output/audit/metrics,
queue-depth and error alerts, then kill-switch rollback with accepted work still
visible. Any failed denial, sequence gap, duplicate terminal output, forbidden
collection write, missing alert, or non-empty real-user dataset aborts the
canary and leaves admission/execution disabled.

The Founder subsequently authorized a synthetic, removable canary in the
existing project because the app is not yet in real use. Authentication was
rechecked before any resource read or mutation. `gcloud auth
print-access-token` still failed during non-interactive token refresh and
requested `gcloud auth login`; therefore no queue, IAM binding, index,
monitoring policy, Cloud Run configuration, synthetic identity/session/
artifact, job, or audit record was created or changed. Canary execution and
cleanup remain pending until the Founder completes scoped gcloud
reauthentication for `ijidai@ruhu.ai` and the selected project is reverified as
`co-founder-506001`.

After reauthentication, the scoped checks succeeded for account
`ijidai@ruhu.ai` and exact project `co-founder-506001`; billing is enabled and
Cloud Run, Cloud Tasks, Firestore, Monitoring, IAM, and Cloud Build APIs are
enabled. Read-only inventory confirmed that the pilot queue, worker identity,
route-scoped Run binding, pilot service configuration, and required
actor-private workflow-run index do not yet exist. The project has no Cloud
Monitoring notification channels (`gcloud beta monitoring channels list`
returned an empty result). The binding Phase-7 runbook refuses to install alert
policies without an existing enabled, verified destination, so the canary again
stopped before mutation. No queue, identity, IAM, index, revision, flag,
synthetic record, task, or monitoring policy was created or changed. A Founder
must provide or authorize creation of an enabled verified notification channel
before the controlled cloud canary can proceed.

The Founder selected an existing PagerDuty service named “canary failure” but
did not provide its routing key through a reliable secret channel. Voice or chat
transcription is never accepted for that credential. The prepared local handoff
is `.env.canary.local`, which is already covered by the repository's `.env.*`
ignore rule. The file must be a regular mode-`0600` file containing exactly one
`PAGERDUTY_ROUTING_KEY=<routing-key>` assignment. The helper
`scripts/configure_spec40_pagerduty_channel.py` accepts no credential argument,
checks the exact account/project, refuses an unignored or broadly readable
file, removes the key from the child environment, sends the Cloud Monitoring
channel body over stdin, and emits only non-secret metadata. It is dry-run by
default and no PagerDuty, secret-store, Monitoring, or canary state was created
while preparing this path.

The Founder later replaced that PagerDuty plan with the Google Cloud registered
alert email. Read-only inventory found the enabled channel
`projects/co-founder-506001/notificationChannels/11488372512344482253`, display
name `Spec 40 Canary Failures`, type `email`, for the authenticated Co-Founder
account address. The PagerDuty credential file and helper are not used for the
canary. Monitoring policy configuration must bind to this exact channel. The
monitoring preflight was also updated for the current gcloud command
`monitoring uptime list-configs` and now includes the closed
`co-founder-background-pilot` queue at its 100-task safety ceiling.

## Controlled existing-project synthetic canary

On 2026-08-28 the Founder explicitly approved a removable, test-only canary in
the existing project. The authenticated account was `ijidai@ruhu.ai`; the exact
project was `co-founder-506001` (`co-founder`, project number `624375313190`),
billing was enabled, and the enabled notification channel was the Google Cloud
registered email channel `Spec 40 Canary Failures`. The normal `co-founder`
service remained on revision `co-founder-00027-79d` throughout.

The canary used a separate private Cloud Run service named
`co-founder-spec40-canary`, concurrency one and maximum one instance. It loaded
`services.background_pilot_canary_app` rather than the product app, so no ADK
runner, model, web/browser, provider, connector, approval, effect, memory,
conversation-delivery, generic Runs/SSE, hiring, or user-selected route was
present. Its only product capability was
`pilot.artifact_evidence_inventory@1`. A route-scoped
`background-pilot-worker@co-founder-506001.iam.gserviceaccount.com` identity,
the concurrency-one/max-attempts-three `co-founder-background-pilot` queue, a
temporary secret-backed synthetic Founder credential, exact task audience, one
actor-private workflow-run index, and two content-free email alert policies
were created for the test window. Repository defaults remained off; only the
private canary revision had the four narrow pilot gates enabled, the exact
synthetic workspace/worker allowlists, and a clear kill switch.

The durable fixture was exactly:

```text
workspace_id  spec40_canary_workspace_20260828
actor_id      spec40_canary_founder_20260828
role          FOUNDER
session_id    spec40_canary_session_20260828
artifact_id   41414141414141414141414141414141
artifact      SYNTHETIC_CANARY_TEXT, READY, 153 bytes, 2 chunks
```

No real user data or external input was used. Live observations proved:

- durable command/outbox admission and real Cloud Tasks dispatch; an immediate
  replay returned the same command/run with `duplicate: true`;
- strict `QUEUED -> RUNNING -> SUCCEEDED` event ordering;
- injected process-failure and timeout attempts each returned 503, persisted a
  failed attempt, retried safely, and recovered in order to `SUCCEEDED`;
- the three-admissions-per-hour server limit rejected a fourth job with 409;
- cancellation fenced a retry and produced `QUEUED -> RUNNING -> recoverable
  failure -> CANCELLING -> CANCELLED`, with no output artifact;
- successful outputs were deterministic and content-free; approvals, external
  actions, conversation/wake delivery, memory items, and memory-write receipts
  remained empty;
- missing Founder authorization returned 401, a tampered artifact returned 404,
  and an unregistered generic background route returned 404;
- a synthetic 503 probe produced two matching Monitoring time series with 13
  points while both temporary policies were bound to the verified email; and
- enabling the kill switch denied admission with 403 and
  `background_pilot_killed` before final cleanup.

The canary exposed one deployment seam: the newest-event Activity projection
requires both ascending timeline and descending `run_events(run_id, sequence)`
indexes. The first read failed closed with 500; the descending index was added
to `infra/firestore.indexes.json`, deployed to `READY`, and cancellation was
repeated successfully from a clean synthetic window.

Cleanup was executed and independently re-read. The exact synthetic workspace
had zero remaining records across artifacts/chunks, membership, session
control, command/outbox, capacity, plans/runs/steps/attempts/events, audits, SLO
observations, and every forbidden collection. The private service, queue,
worker identity, project IAM grants, temporary secret, both temporary alert
policies, and both canary-created indexes were deleted. Final inventory returned
all those resources absent, zero canary IAM bindings, and zero synthetic
workspace records. The exact canary container digest and Cloud Run source
archive were also deleted; Cloud Build retains only its immutable completed
build metadata as audit evidence. The pre-existing verified email channel
remains enabled; the normal app remains revision `co-founder-00027-79d` with
all pilot flags off.

## Controlled real pitch-deck pilot

The Founder next authorized one genuine founder-owned pitch deck. Metadata-only
preflight found two byte-identical PDF uploads in the same session. Both were
1,503,104 bytes, generation `04e7a87b743486aed032`, and had 14 current evidence
chunks. The pilot deterministically selected the most recent record:

```text
workspace_id  founder
actor_id      founder_86239040360df20f59d8e6178ac7
role          FOUNDER
session_id    s-8b65b761c2cd44cf15284970e581befe
artifact_id   8f034b5e5f6d1bd6dacfe1acefccd379
content_type  application/pdf
status        READY
size_bytes    1503104
chunks        14
```

The normal service remained pilot-off at `co-founder-00027-79d`. A separate
private `co-founder-spec40-real-pilot` service loaded the exact-artifact host in
`services/background_pilot_real_app.py`. Deployment configuration bound the
service to the one workspace, real FOUNDER actor, session, and artifact. The
host had no seed/cleanup/failure controls, interactive docs, model, provider,
web/browser, connector, approval, effect, memory, conversation delivery, or
generic route. An optional server-side artifact authorizer was added to the
shared route registration so a valid artifact from the same session still
failed unless it was the exact configured deck.

The previously deleted queue name was still under Cloud Tasks' deletion
tombstone, so the dispatcher gained one additional code-owned name,
`co-founder-background-pilot-real`; every other configured queue is rejected.
The real queue used concurrency one, dispatch rate one, and three attempts. A
temporary route worker, exact audience/allowlist, secret-backed Founder control,
and two content-free email policies completed the execution envelope. Both
actor-private workflow and newest-event indexes reached `READY` before
admission.

The live request created command `command_7538c7ba7633920facdf3ff2dc5c` and run
`run_8008372ce4ef81088bc2fd86f912`. It dispatched through the real queue and
completed in one attempt. An immediate identical replay returned
`duplicate: true` and the same command/run. Founder-visible events were unique
and ordered `RUN_CREATED(1) -> STEP_STARTED(2) -> RUN_SUCCEEDED(3)`.

The retained actor-private output is
`artifact_be2bdc8343e8b93819eb235cd90d`. It contains a deterministic inventory
of 14 cited sections and 1,090 words with content hash
`sha256:bce19852d21b0abc9cc08800fea735f8f6c5fccfadc1ae0132cb601cf5ec6091`.
Schema inspection proved it contains no content, text, raw text, source text,
prompt, or transcript field. The source artifact remained `READY` with the same
hash, generation, and 14 chunks. No approval, external action, conversation
delivery, wake, memory item, or memory-write receipt linked to the run.

Rollback was proved before teardown: the private service kill switch returned
403 `background_pilot_killed` for a new request and created no second run. The
private service, queue, worker identity and project IAM, temporary secret, both
temporary alert policies, container digest, and source archive were deleted.
Only immutable Cloud Build audit metadata remains. The completed run, ordered
events/audits, content-free output, unchanged source deck, and the two durable
visibility indexes remain. Final inventory found exactly one real Founder
background run and no standing execution resource.

## Controlled Gate E Activity soak

The Founder then approved the next narrow rollout stage on the normal product
topology. Repository verification passed before deployment: `1158` tests passed
with `2` skipped, the focused pilot suite passed, and Ruff, shell syntax,
Firestore index JSON, diff hygiene, and the Document-16 contrast check were
green. The reviewed implementation was frozen at `37048fc`; the tombstone-safe
queue delta was frozen at `63af518`; and the replay-budget remediation described
below was frozen at `5a23f0b` with `1159` tests passing and `2` skipped.

The normal `co-founder` service first received the immutable image with all
pilot variables absent/off. A no-traffic tagged revision passed `/health` and a
signed FOUNDER read with `enabled: false` before normal traffic moved. The two
required Firestore indexes were `READY`. Because the two earlier queue names
were still under Cloud Tasks deletion tombstones, the dispatcher added exactly
one further code-owned name, `co-founder-background-pilot-gate-e`; arbitrary
configured queues remain refused. That queue used concurrency one, dispatch
rate one, and three attempts. The route-scoped
`background-pilot-worker@co-founder-506001.iam.gserviceaccount.com` identity,
exact OIDC audience/allowlist, and two content-free policies were provisioned.
The policies remain bound only to the pre-existing enabled Google email channel
`Spec 40 Canary Failures`:

- `Spec 40 Gate E queue stalled` alerts when the exact queue remains non-empty
  for five minutes; and
- `Spec 40 Gate E service 5xx` alerts on Cloud Run 5xx responses.

The two eligible PDFs were healthy but referred to a conversation that no
longer existed in Cloud SQL, so the normal Activity/session gate correctly
returned 404. A metadata-only consistency repair created empty normal Founder
session `s-534555d5582fb374f8f4790b0c614b05`, changed only those two artifacts'
session binding, registered both as selected Activity resources, and wrote
content-free audits. Source bytes, objects, hashes, generation, size, READY
status, and 14-chunk indexes did not change.

The three-admissions-per-hour window comprised the retained exact pitch-deck
run plus two normal Activity admissions:

```text
run_8008372ce4ef81088bc2fd86f912  SUCCEEDED  output artifact_be2bdc8343e8b93819eb235cd90d
run_1ee88e98416ebc2878961d018a6e  SUCCEEDED  output artifact_7824d9a607438c843ac32c453afd
run_208853ef9cd74e2e9f013da698c7  SUCCEEDED  output artifact_7c4454c8c6809bc001ef53c997cd
```

Every run had one complete attempt and the unique ordered sequence
`RUN_CREATED(1) -> STEP_STARTED(2) -> RUN_SUCCEEDED(3)`. The actor-private
Activity projection returned the same `QUEUED -> RUNNING -> SUCCEEDED` order.
Each output contained 14 citations and 1,090 counted words, no content/text/raw
text/source text/prompt/transcript/routing fields, and no linked approval,
external action, conversation or wake delivery, memory item, or memory-write
receipt. The two source records remained byte-identical at 1,503,104 bytes,
SHA-256 `4b9e92b6c975c3a731059d7d7a9dc09f6f3c4e3e270e1c22067d94cc7091cc17`,
generation `04e7a87b743486aed032`, READY, and 14 chunks. Crash, retry,
cancellation, synthetic deletion/cleanup, and verified email delivery remain
covered by the immediately preceding controlled synthetic canary; they were not
re-induced against the real deck.

The live soak found one bounded defect: a valid duplicate returned the same run
but reached rate evaluation before the command point-read, so the replay could
consume an hourly admission slot. No duplicate run, output, event, or effect was
created. Admission was stopped, and `CommandService.replay` now validates and
returns an existing idempotent receipt before any capacity check. New focused
tests prove a replay at the full hourly counter succeeds without changing the
counter. The fixed image passed the full suite and a seconds-long live
regression at `window_admissions=3`: the existing key returned 202,
`duplicate: true`, and `run_208853ef9cd74e2e9f013da698c7`; a fresh key still
returned 409 `background_rate_limited`; job count remained three and the
counter remained `3 -> 3`.

Final rollback is authoritative. Revision `co-founder-00035-vvd` serves 100%
of traffic with shared and pilot admission/execution flags false, conversation
delivery false, and kill switch true. A signed post-rollback request returned
403 `background_pilot_killed` and created no fourth run. The exact Gate E queue
is empty and `PAUSED`; both zero-traffic enabled revisions and all enabled
traffic tags were deleted. The route identity, READY indexes, paused queue, and
the two content-free email policies remain as disabled Gate E infrastructure.
No model/provider, web/browser, connector, approval/effect, memory, generic
Runs/SSE, hiring automation, multi-user access, or proactive conversation
delivery was added or enabled by this stage.

## Decision

The synthetic canary, exact real pitch-deck pilot, and three-run Gate E Activity
window completed with one replay-budget defect found, fixed, fully retested, and
verified live. This evidence authorizes no standing execution and no broader
background work. The repository and serving revision remain safe-by-default:
pilot flags are off, the kill switch is on, the queue is paused, and no enabled
pilot revision or traffic tag remains. Actor-private runs, audits, outputs,
Activity links, monitoring, route identity, and required indexes are
intentionally durable. Gate F and every broader capability remain blocked
pending a new exact authorization and the residual gates above.
