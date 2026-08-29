# 39 — Durable, secure cross-session memory for Alex

**Status:** approved specification for the bounded M1/M2 implementation scope
below; M3–M6 remain gated proposals.

**Initial implementation authorization boundary (approved 2026-08-28):**

1. **M1 only:** implement the deterministic “Since you were away” brief from
   registered authorized durable projections, with the existing interim
   `pending_signals` wait input replaced or omitted as explicitly partial.
2. **M2 only:** implement synthetic, user-controlled optional memory shared
   inside one single-Founder workspace, using the existing controlled portable
   backend. Writes require an explicit remember/correct/pin/forget/delete command
   or explicit Founder confirmation of a source-linked synthetic verified
   durable workflow outcome; private sessions perform no optional memory I/O;
   recall is advisory and shows the simple “Informed by saved context”
   disclosure.

This authorizes a separately reviewed implementation task within that boundary;
this documentation task changes no application code. It authorizes no automatic
feedback/run-outcome extraction or write, transcript/session ingestion, actor-private or
multi-member memory, managed external memory service, `CONFIDENTIAL` managed
egress, approval-artifact influence, connector activation, external effect,
production rollout, or M3–M6 work. Those require later explicit approval after
their gates pass.

**Numbering integration record:** the documentation-only integration on
2026-08-28 retains
[35 — Platform operations and recovery](35-platform-operations-and-recovery.md),
then assigns [36 — Unified Alex product experience](36-unified-alex-product-experience.md),
[37 — Production skills system](37-production-skills-system.md),
[38 — User-facing vision](38-user-facing-vision.md), this memory specification
as 39, and [40 — Durable background work](40-durable-background-work.md). The
approved main vision snapshot `151ef35...` became doc 38; the older review-only
`3897a896...` vision snapshot remains historical and was not merged. This
resolves the earlier duplicate doc 35 without changing either version's
decision content.

This specification owns the cross-session memory product and architecture
contract. It does not duplicate the general product shell, live-media protocol,
skills system, or workflow architecture. Those remain owned by
[21 — Alex Platform North Star](21-alex-platform-north-star.md),
[24 — Activity and waiting](24-activity-and-waiting.md),
[34 — Platform architecture and convergence](34-platform-architecture-and-convergence.md),
[36 — Unified Alex product experience](36-unified-alex-product-experience.md),
[37 — Production skills system](37-production-skills-system.md), and
[38 — User-facing vision](38-user-facing-vision.md).

---

## 1. Executive decision

Alex should gain **optional, scoped, inspectable cross-session memory**, but it
must not become an omniscient transcript reader.

Four kinds of continuity stay separate:

1. **Durable workspace facts** describe the company and confirmed shared rules.
2. **Workflow/run state** describes what work exists, what happened, and what is
   waiting or authorized.
3. **Session-local state** supports one conversation's reference resolution and
   user experience.
4. **Optional cross-session memory** recalls sourced preferences, decisions, and
   bounded outcome summaries that are useful later but are not authoritative.

The founder-facing “Since you were away” brief is assembled directly from
current durable records. It does not query semantic memory and never summarizes
all chat transcripts. A generic new chat therefore becomes useful without being
granted universal access to historical conversation or restricted domain data.
It is the proposed product name and expansion of the existing **return digest**,
not a claim that an unrelated brief feature already exists.

Raw chat transcripts remain session history under the session lifecycle. They
are never automatic cross-session memory, never backfilled into memory, and
never scanned across sessions for generic recall.

Memory is advisory. It may improve phrasing, avoid repeated questions, and
surface a prior rationale. It can never authorize a transition or action,
resolve an approval or wait, establish a provider effect, override a current
domain record, or weaken a domain access gate.

Recommended deployment shape:

- retain Firestore as the authority for profiles, runs, domain records, memory
  metadata, control settings, source links, receipts, and deletion jobs;
- retain ADK sessions in the configured durable session service for
  conversation continuity only;
- evolve the existing disabled `PersistentMemoryAdapter` into the only product
  seam for optional memory;
- use the existing controlled portable Firestore-backed implementation for the
  bounded M2 rollout; no managed external memory service is enabled;
- keep Cloud Tasks as the durable asynchronous write/delete/reconciliation
  transport; and
- continue using Gemini/Vertex for bounded extraction, conflict classification,
  and response generation, never for authorization.

The initial competition demonstration may show the architecture, deterministic
brief, synthetic user-controlled cross-session recall, controls, and active-store
deletion proof on the controlled portable backend. A Google-managed Memory Bank
evaluation is a later M3 proposal and is outside the approved M1/M2 boundary.

---

## 2. Existing behavior versus proposed behavior

| Area | Existing repository behavior | Proposed behavior |
|---|---|---|
| Workspace facts | Compatibility `profiles/{founder_id}` plus newer versioned `profile_facts`; confirmed facts and voice rules are durable. | Finish the workspace-business versus actor-private classification from doc 34. These records remain canonical and are displayed separately from episodic memory. |
| Runs and receipts | Firestore holds domain/workflow records, waits, approvals, actions, inbox/event records, and receipts. | A server brief assembler reads authorized projections from these sources. No transcript or semantic-memory scan is needed. |
| ADK sessions | SQLite is the default session backend; production can select Cloud SQL through `SESSION_SERVICE_URI`. Sessions carry events and projection state, and the current ADK `user_id` is normally the workspace/founder id. | Sessions remain conversation-local. A future actor-qualified subject key removes the workspace/actor identity collision before multi-member memory is enabled. |
| Optional memory | `services/persistent_memory.py` implements a portable adapter and ADK bridge. `.env.example` defaults `PERSISTENT_MEMORY_BACKEND=disabled`. `add_session_to_memory` is deliberately unsupported. | Keep disabled by default. Add explicit milestone writes, source manifests, actor-aware authorization, user controls, export/deletion verification, and staged backend qualification. |
| Recall | No memory retrieval tool is registered on the root agent; configuring a service alone does not make recall product behavior. | A server-owned context assembler performs purpose-bound recall only for eligible turns. It returns a typed, bounded advisory context section. |
| Return digest | The existing UI and code call this the return digest. Most inputs are durable records, but one compatibility reader still derives waits from ADK session `pending_signals`; it is not yet a first-class workspace-scoped durable wait. | Rename and generalize it into “Since you were away,” preserving the derived/no-new-truth rule and adding milestones, safe inbox, exact pending decisions, and recent terminal receipts. M1 must replace or omit the interim `pending_signals` input before claiming fully durable assembly. |
| Privacy | Current workspace principal and Hiring actor/assignment controls exist, but the ADK memory bridge maps `user_id` to both workspace and actor. | Derive workspace, actor, role, grants, assignments, and source visibility server-side on every read/write. Actor-private memory is inaccessible to other members. |
| Session modes | Ordinary sessions may use the durable profile; there is no complete private/temporary memory contract. | Temporary/private sessions neither read nor write optional memory. They still receive minimum authorized durable state required for safety and work the founder explicitly opens. |

Observed gaps that this design addresses:

- a generic new chat cannot safely summarize every active workflow by reading
  chat or querying every domain generically;
- `user_id == workspace_id` is adequate for the current single-founder session
  namespace but cannot represent multi-member actor privacy;
- the existing adapter's `search_memory()` ADK bridge currently uses that same
  value as both `workspace_id` and `actor_id`;
- generic run, approval, search, inbox, and brief readers must not bypass
  Hiring's role, assignment, identity-vault, and candidate-evidence gates; and
- a configured backend is not the same as an enabled, evaluated, founder-visible
  memory feature.

---

## 3. Objectives, non-goals, and terminology

### 3.1 Objectives

- Let a founder return after hours or weeks and see current work without replaying
  transcripts.
- Make every remembered item inspectable by source, freshness, scope, status,
  and reason for use.
- Let an authorized founder correct, pin, forget, export, or disable optional
  memory.
- Support sessions that neither use nor update optional memory.
- Preserve current durable authority and all domain-specific access boundaries.
- Give the competition story a credible Google-compatible memory architecture
  with an honest existing/proposed distinction.
- Remain portable across Cloud Run and Agent Runtime deployments.
- Make backend failure, staleness, conflict, and deletion incomplete states
  visible rather than silently returning misleading context.

### 3.2 Non-goals

- Indexing or summarizing every chat transcript into global memory.
- Treating ADK session events, compacted conversation, or Memory Bank output as
  workflow truth.
- Cross-workspace or unrestricted organization-wide recall.
- Candidate memory, candidate comparison, candidate ranking, or candidate data
  in generic memory.
- Credentials, secrets, approval tokens, provider payloads, raw form values, or
  unrestricted external evidence in memory.
- Automatically inferring consent to remember because a founder said something
  in chat.
- Using a managed Google feature merely for competition branding.
- Replacing the Founder Profile, run event ledger, artifact retrieval, global
  search, or institutional RAG with one semantic store.

### 3.3 Terms

| Term | Meaning |
|---|---|
| **Durable fact** | Confirmed workspace-business or actor-preference record in Firestore. |
| **Run truth** | Current workflow/domain records, ordered events, waits, approvals, actions, and provider receipts. |
| **Session projection** | Reconciled, non-authoritative ADK state for one conversation/invocation. |
| **Memory item** | Optional, source-linked, advisory cross-session recall object. |
| **Memory source** | Durable event, run output, feedback record, profile version, or founder-issued remember command from which an item was produced. |
| **Pinned memory** | A founder-confirmed item with stronger retrieval priority, not stronger authority. |
| **Private session** | A session whose policy disables optional memory read and write. It is not anonymous and does not bypass durable safety state. |
| **Workspace brief** | Deterministic server view of current authorized durable records, branded “Since you were away.” |

---

## 4. Binding invariants and precedence

The authority hierarchy in [34 §3](34-platform-architecture-and-convergence.md)
remains binding:

1. current verified principal, membership, assignments, and policy;
2. workflow/domain records, waits, approvals, actions, and commands;
3. verified provider receipts and inbound events;
4. confirmed workspace/actor facts;
5. sourced artifacts and governed evidence;
6. reconciled session projection;
7. episodic memory;
8. transcript, external content, and model output.

Additional invariants:

1. **Memory never authorizes.** No memory field is accepted by a transition,
   approval, action, identity, connector, or provider guard.
2. **Current durable truth wins.** Conflicting memory is suppressed before model
   context and creates a content-minimized conflict receipt.
3. **No transcript omniscience.** A context assembler never lists or scans
   arbitrary sessions to answer a generic turn.
4. **Scope before rank; live authorization before admission.** Immutable
   workspace, subject, scope, sensitivity, purpose, and query-time constraints
   are enforced before backend ranking. Mutable lifecycle status, membership,
   role, assignment, source visibility, deletion denylist, and current-state
   conflicts are rechecked on every returned candidate before final ranking or
   admission. An opaque managed backend is never assumed to enforce those live
   checks.
5. **Source visibility is live.** Retrieval rechecks the source's current access
   policy. Possessing a memory id does not preserve access after revocation.
6. **Private means no optional memory I/O.** The server omits retrieval and
   blocks memory-write outbox creation for that session.
7. **Compaction is not a write milestone.** A compacted ADK summary is neither a
   source nor permission to remember.
8. **Provenance is not summary truth.** `source_trust` describes where source
   material came from; it does not certify that an extracted summary entails
   that material. External text stays untrusted, and no memory is inserted into
   system instructions as trusted policy.
9. **Deletion denies first.** Forget/delete immediately suppresses retrieval,
   then completes physical and derived-store deletion asynchronously.
10. **Errors are data.** Backend failure is distinct from zero hits.

### 4.1 Conflict outcomes

| Conflict | Result |
|---|---|
| Memory versus current domain/provider record | Suppress memory; use current record; log conflict metadata. |
| Memory versus confirmed profile fact | Suppress memory; show “outdated/conflicting” in What Alex knows. |
| Two memory items | Prefer founder-confirmed text, then independently grounded summary assurance; among equal assurance prefer the newer source version. `source_trust` alone never wins a content tie, and an unverified extraction never suppresses a grounded/confirmed item. If a newer unverified item signals a material inconsistency, include neither until reviewed. |
| Pinned memory versus durable fact | Durable fact still wins. “Pinned” affects recall priority only. |
| Deleted/revoked source versus memory | Suppress immediately; queue source deletion/reconciliation. |
| Backend hit without resolvable source | Exclude as orphaned and create repair work. |
| Chat statement versus existing memory | Treat as a proposed correction, never silent overwrite. |

---

## 5. Founder-facing experience

This section owns the **server/product contract** that the doc 36 UI
renders: required information, safe ordering, response bounds, controls, and
honest state/copy semantics. Doc 36 owns layout, component placement, visual
hierarchy, responsive behavior, interaction choreography, and accessibility
presentation. The bounds below constrain service responses; they do not mandate
a duplicate layout. If doc 36 proposes a different presentation, it must still
preserve these data and safety semantics or record a joint design decision.

### 5.1 “Since you were away” workspace brief

The brief is server-assembled on application load, explicit refresh, visibility
restore, or founder request. It is not generated by a model and does not poll.
This is the M1 target. Today's return digest still has one interim
session-state reader for `pending_signals`. M1 must promote those waits to the
registered durable wait/domain projection, or omit them with an explicit
partial result; it must not describe session-derived signals as durable truth.

It contains, in this order:

1. **Needs you:** exact pending approvals and human decisions the current actor
   may resolve, with safe server-rendered subject, consequence, expiry, and
   focus reference. No token, subject hash, raw details map, or client-authored
   authority field is returned.
2. **Active milestones:** meaningful run/domain transitions since `since`, such
   as draft ready, evidence complete, application submitted, or role contract
   changed.
3. **Open waits:** who or what is blocking, when it opened, next wake/check, and
   what happens if it expires.
4. **Safe inbox:** current actor-authorized ambiguity, failure, connection, and
   follow-up items. Text is a bounded domain projection, never raw email/page or
   candidate content.
5. **Recent terminal receipts:** succeeded, denied, expired, cancelled, failed,
   or uncertain terminal outcomes, with focusable receipt ids.

Rules:

- the assembler reads registered **domain brief contributors** rather than one
  generic table query;
- each contributor accepts an `ActorPrincipal`, purpose, time window, and
  bounds, and returns safe typed items or a safe partial error;
- Hiring's contributor calls Hiring authorization and projection services. It
  may return counts/items only over the current actor's candidate assignments
  by default. A wider role-level aggregate requires an explicit
  Hiring+Privacy-approved policy and a projection proven not to reveal
  cross-assignment existence; role membership alone is insufficient. It returns
  no candidate identity/evidence or cross-candidate text;
- a general conversation cannot request a broader contributor set than the
  server selected;
- at most 3 “Needs you,” 5 milestones, 5 waits/inbox items, and 5 receipts are
  returned initially; “Show all” opens the authorized domain surface;
- `since` defaults to the last locally acknowledged brief time, is supplied as
  a hint only, and is clamped server-side to 30 days;
- dismissal is client-local and changes no durable work; and
- if all contributors fail, the UI says current work could not be loaded. It
  never substitutes memory or transcript summarization.

### 5.2 “What Alex knows”

This view separates **Confirmed facts** from **Remembered context**.

For the approved M2 rollout there is exactly one Founder principal and every
optional-memory item is `WORKSPACE`-shared inside that Founder workspace.
“Only me”/actor-private memory and multiple members are later-tier states and
are not enabled.

Every row shows:

- concise value/summary;
- type: workspace fact, private preference, remembered decision, outcome, or
  reusable context;
- scope: workspace-shared or only me;
- source label and focusable source reference when still accessible;
- source trust, summary assurance, and verification status;
- first remembered, last updated, last used, and expiry/freshness status;
- derived display state: active (optionally pinned), conflicting, stale,
  superseded, deletion pending, unavailable, or memory disabled, using the
  mapping in §7.4;
- why it may be used; and
- controls allowed by current policy.

Controls:

- **Correct:** creates a new attributed fact/memory proposal linked to the old
  item. Material workspace facts follow the existing confirmation/conflict
  path. Correction never edits immutable history in place.
- **Pin:** founder-confirms the memory text and raises recall priority. It does
  not turn the item into approval, provider proof, or a domain fact.
- **Forget:** immediately suppresses the item, creates a deletion job, and shows
  `Deletion pending` until active stores return receipts and negative probes
  pass, then honestly shows any bounded backup/revision residual window before
  full completion.
- **Disable memory:** stops future optional reads/writes for the selected scope.
  Existing items become non-retrievable immediately; the UI separately offers
  delete existing items.
- **Export:** produces an actor-authorized manifest of current facts, memories,
  sources, scopes, statuses, and timestamps. Items the actor cannot access are
  omitted completely—without labels, counts, timestamps, or unavailable
  markers—and restricted source bodies are never copied into the export.

The view must never expose credentials, approval internals, raw external
payloads, hidden candidate existence, restricted Hiring sources, or other
actors' private preferences.

### 5.3 Temporary/private sessions

Creating a session offers an explicit **Private session** toggle with the copy:

> Alex will not use or update optional cross-session memory in this session.
> Current workspace records and safety controls still apply to work you open.

Contract:

- server stores `memory_mode=PRIVATE` in the session catalog and signed session
  projection; a client-only toggle is insufficient;
- memory retrieval is skipped before any backend call;
- memory write milestones and memory outbox rows are rejected with
  `memory_disabled_for_session`;
- any run created from a private session receives a durable, monotonic
  `private_origin=true` taint. Every descendant event, task, retry, worker, and
  webhook wake inherits it and is permanently ineligible as an optional-memory
  source; opening the run later from a standard session does not clear it;
- ordinary ADK session events still persist under the session retention policy
  unless the founder separately chooses a temporary conversation-retention
  option;
- private mode does not hide current runs, approvals, or provider receipts when
  the founder explicitly opens them;
- private mode does not authorize restricted sources or bypass Hiring gates;
- changing from private to standard affects future turns only and requires a
  clear founder action; earlier private turns are never retroactively ingested;
  and
- a worker or webhook wake inherits the originating run/source memory policy
  and private-origin taint, not an arbitrary active chat's mode.

### 5.4 Failure UX

| Condition | Founder-facing behavior |
|---|---|
| Memory backend unavailable | “Optional recall is unavailable. Current workspace facts and work are still up to date.” |
| No eligible memories | No warning; What Alex knows shows an honest empty state. |
| Source deleted while actor still controls the item | Item shows unavailable and is not used. |
| Actor/source access revoked | Item is omitted entirely; no unavailable row, count, or timing signal is exposed. |
| Material conflict | Item shows conflicting; current durable value is shown separately. |
| Deletion partially complete | `Deletion pending` with retry/support reference; item remains suppressed. |
| Active deletion complete; bounded backup residual | “Deleted from active systems; encrypted backups expire by <date>.” No claim that probes inspected backups. |
| Brief contributor unavailable | Other sections render with `Some workspace updates could not be loaded`; no guessed items. |
| Private session | Persistent privacy indicator near the session title; no repeated modal. |
| Cost/budget exhausted | Recall skipped with a content-free telemetry reason; current facts/run state still load. |

---

## 6. Architecture

### 6.1 Logical flow

```text
verified request
  -> ActorPrincipal(workspace, actor, role, grants, assignments, versions)
  -> ReconcileContext(current run/domain/profile/source visibility)
  -> ContextPolicy(memory mode, purpose, scopes, sensitivity, time/token budget)
      -> durable facts/run/evidence readers
      -> optional PersistentMemoryAdapter search
      -> reauthorize sources + conflict filter
  -> typed ContextPack with separate trusted/advisory/untrusted sections
  -> Gemini/ADK invocation

authoritative milestone committed in Firestore
  -> memory outbox item (only if policy permits)
  -> Cloud Task claims generation
  -> bounded source assembler + exclusion scan
  -> Gemini extraction/proposal or explicit summary
  -> summary grounding + tombstone/private-origin recheck
  -> PersistentMemoryAdapter.put(idempotency key, request hash)
  -> memory write receipt / visible degraded status
```

The model never chooses workspace, actor, source visibility, sensitivity
ceiling, allowed scopes, retention, or whether a private session may use memory.

### 6.2 Components

| Component | Responsibility |
|---|---|
| `WorkspaceBriefAssembler` | Calls registered domain contributors and builds the deterministic return brief. No model and no memory backend. |
| `MemoryPolicyService` | Resolves workspace/actor/session settings, purpose allowlist, source eligibility, retention, and budgets. |
| `MemoryContextAssembler` | Builds a typed query from the current objective and durable context; invokes adapter; reauthorizes and conflict-filters hits. |
| `MemoryWriteProjector` | Converts declared authoritative milestone events into bounded write candidates through an outbox. |
| `MemoryGroundingService` | Splits a proposed summary into bounded claims, verifies exact structured fields and source spans, assigns summary assurance, and quarantines unsupported material claims. |
| `PersistentMemoryAdapter` | Portable `put`, `search`, `get`, `list_by_source`, `delete_by_source`, `delete_workspace`, and `health` boundary; vendor SDKs stay behind it. |
| `MemoryControlService` | Correct, pin, forget, disable, export, and source deletion with command/receipt/idempotency contracts. |
| `DeletionService` | Deny-first multi-store deletion, completion manifest, negative probes, local tombstone projection, and restore orchestration. |
| `DeletionDenyLedger` | Append-only deletion denies in a separate failure/restore domain; supplies a high-water mark that every restore must replay before memory traffic. |

#### 6.2.1 Skills-system integration

The doc 37 skill manifest may **declare** which memory projections a
skill may read or propose, but declaration grants no authority and no direct
adapter access. The integration point is a closed
`SkillMemoryProjectionGrant` compiled into the registered skill version:

```text
SkillMemoryProjectionGrant
  skill_id / skill_version
  allowed_purposes[]
  allowed_memory_kinds[]
  allowed_subject_scopes[]
  sensitivity_ceiling
  may_propose_write: bool
  allowed_write_milestones[]
  max_hits / max_tokens
```

At invocation, `MemoryPolicyService` intersects that declaration with the
server-derived principal, current run/domain assignment, session memory mode,
workspace policy, source visibility, and global purpose allowlist. The result
is passed to `MemoryContextAssembler`; the skill receives only the admitted
typed projection in its context pack. A skill cannot call
`PersistentMemoryAdapter`, choose a broader purpose/scope, or persist a
proposal. Write proposals flow through `MemoryWriteProjector` and the same
milestone, exclusion, confirmation, idempotency, audit, and deletion contracts
as every other producer. Missing declaration means no optional memory access.

### 6.3 Google-compatible backend options

The existing Cloud Run + Firestore + Cloud SQL + Cloud Tasks deployment remains
a supported production topology. Agent Runtime is optional.

Candidate backends:

1. **Portable Firestore memory:** retain authoritative metadata/source manifests
   in Firestore and use tenant-filtered keyword/tag retrieval initially. A
   reviewed vector index may be added later. This is the rollback-safe baseline.
2. **Google Agent Platform Memory Bank adapter:** map Co-Founder scopes and
   metadata to a managed Memory Bank instance, while retaining Firestore control
   records and deletion jobs. Memory Bank is a replaceable derived backend, not
   the source of profile or run truth.

Managed storage **and retrieval queries are data egress**. The default managed
policy permits only `INTERNAL` items from source-registry entries explicitly
marked `managed_backend_eligible=true`; `CONFIDENTIAL` items remain on the
portable backend. Enabling any `CONFIDENTIAL` managed path requires named
Privacy/Security + Platform approval of DPA, region/residency, query egress,
retention/revisions, incident response, and the exact qualified purposes. No
such approval or path exists today.

Current Google documentation supports this shape but does not remove product
responsibility:

- ADK distinguishes session/state from long-term `MemoryService`, offers
  explicit `add_memory`, and supports retrieval via tools
  ([ADK memory documentation](https://adk.dev/sessions/memory/)).
- ADK's managed wrapper can submit a whole session or event subset, and its
  retrieval uses the current `user_id` and `app_name`; this design deliberately
  does **not** adopt whole-session automatic ingestion
  ([Memory Bank ADK quickstart](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/adk-quickstart)).
- Memory Bank maintains isolated collections by scope, offers similarity
  retrieval, TTL, revisions, and IAM conditions, and warns about prompt
  injection/memory poisoning
  ([Memory Bank overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank)).
- managed retrieval matches an exact immutable scope before similarity ranking
  ([Fetch memories](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/fetch-memories)).
- IAM conditions can restrict exact scopes, but multi-scope operations such as
  list/purge have limitations; the product must not depend on an unconditional
  broad list permission for ordinary reads
  ([Memory Bank IAM conditions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/iam-conditions)).
- Google's generation documentation explicitly cautions that sensitive-data
  exclusion is not infallible. Co-Founder therefore performs deterministic
  exclusions before any managed-memory request
  ([Generate memories](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/generate-memories)).
- memory deletion and revisions have distinct lifecycle behavior, which must be
  included in deletion proofs rather than treated as one synchronous delete
  ([Memory revisions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/revisions)).
- Agent Runtime is a fully managed deployment option with Cloud observability,
  not a requirement for using this design
  ([Agent Runtime](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime)).

No Google-managed memory feature is currently enabled in this repository.
`PERSISTENT_MEMORY_BACKEND=disabled` remains the correct default until rollout
gates pass.

### 6.4 Competition demonstration versus production dependency

| Demonstration-safe | Production claim requires |
|---|---|
| Synthetic founder explicitly asks Alex to remember a style preference. | Reviewed source/memory policy and multi-member identity migration. |
| New session retrieves the preference with source/freshness shown. | Persistent cross-process/client parity and source reauthorization. |
| Memory is corrected, pinned, forgotten, and then absent from active-system negative probes. | Complete live-store manifest, bounded backup/revision expiry (or separately proven per-item key destruction), independent deny-ledger proof, and restore-to-pre-forget replay test. |
| “Since you were away” shows synthetic runs/waits/approvals/receipts. | Every domain contributor reviewed; Hiring negative tests at zero leakage. |
| Optional Memory Bank adapter runs in an isolated evaluation namespace. | Region/residency, IAM, quotas, cost, support stage, version pin, threat model, and rollback acceptance. |

The competition narrative should say: “Alex already uses durable profiles and
workflow state; this reviewed design adds optional, source-controlled
cross-session recall through a portable adapter, with Google Memory Bank as a
qualified managed option.” It must not say Memory Bank is live if it is not.

---

## 7. Typed data contracts

The examples below are logical closed schemas, not implementation authorization.
Every enum rejects unknown values. Every text and array field has a server-side
bound.

### 7.1 `MemoryItem`

```text
MemoryItem
  schema_version
  memory_id
  workspace_id
  subject_kind: WORKSPACE | ACTOR
  subject_id                 # workspace_id or actor_id
  memory_kind: PREFERENCE | DECISION_RATIONALE | OUTCOME | REUSABLE_CONTEXT
  summary                    # <= 1,000 chars; never raw transcript
  normalized_tags[]          # <= 16 reviewed tags
  purpose_allowlist[]        # closed purposes
  source_refs[]              # <= 8 typed immutable refs
  source_hashes[]
  source_versions[]
  claim_evidence_refs[]
  rendered_claim_ids[]
  decomposition_coverage
  source_trust: FOUNDER_CONFIRMED | VERIFIED_INTERNAL | UNTRUSTED_EXTERNAL
  summary_assurance: EXTRACTED_UNVERIFIED | GROUNDED | FOUNDER_CONFIRMED
  verification_status: PROPOSED | CONFIRMED
  scope: WORKSPACE | ACTOR_PRIVATE
  sensitivity: INTERNAL | CONFIDENTIAL
  retention_class
  created_at / updated_at / expires_at / last_used_at
  lifecycle_status: ACTIVE | SUPERSEDED | SUPPRESSED |
                    DELETION_PENDING | ORPHANED
  pinned: bool
  writer_policy_version / summary_schema_version
  extractor_model / extractor_version
  backend_ref / backend_revision_ref
  supersedes_memory_id
  version
```

`RESTRICTED`, `HIRING_RESTRICTED`, secrets, credentials, and approval-control
data are not valid `MemoryItem` sensitivities. They are excluded, not merely
hidden at display time.

`source_trust` is provenance only. A Gemini-produced summary starts as
`EXTRACTED_UNVERIFIED` even when its source is `VERIFIED_INTERNAL`; copying the
source's trust cannot raise summary assurance. `GROUNDED` requires the checks in
§8.3, and only an attributed founder action on the exact displayed text creates
`FOUNDER_CONFIRMED`.

`lifecycle_status` is control-plane state, not a transient retrieval result.
New items become `ACTIVE` only in the atomic metadata/source-manifest commit;
correction moves the old item to `SUPERSEDED`; explicit quarantine may use
`SUPPRESSED`; forget moves directly to `DELETION_PENDING`; and an unresolved
source moves an item to `ORPHANED`. Repair may move `ORPHANED` back to `ACTIVE`
only after source identity, version, and live authorization are re-established.
Conflict and staleness are computed candidate dispositions, not stored by
overloading `SUPPRESSED`. Physical deletion removes item content; a separate
content-free deletion tombstone remains, so there is no live `DELETED`
`MemoryItem`.

### 7.2 `MemorySourceManifest`

```text
MemorySourceManifest
  source_ref / source_type / source_id / source_version / source_hash
  workspace_id
  owner_actor_id
  domain_family / run_id / entity_refs[]
  visibility_policy_id / sensitivity / source_trust
  occurred_at / available_at / revoked_at / deleted_at
  retention_class
  memory_eligible: bool
  exclusion_reason
```

This manifest lets source authorization occur without sending source text to the
backend or model. A memory item with no valid manifest is not retrievable.

#### 7.2.1 `MemoryClaimEvidence`

```text
MemoryClaimEvidence
  claim_evidence_id / memory_id / claim_id
  bounded_claim_hash
  materiality: MATERIAL | NON_MATERIAL
  source_ref / source_version / source_hash
  source_field_or_span_ref   # pointer, not copied source body
  exact_field_checks[]       # field kind + pass/fail; no sensitive value
  entailment_checker_version / entailment_result
  assurance_result: EXTRACTED_UNVERIFIED | GROUNDED
  render_eligible: bool
  created_at
```

Claim evidence remains access-controlled with its source. It supports
faithfulness review without copying raw provider/external bodies into generic
memory or logs.

#### 7.2.2 Source-registry contract

The source registry is a versioned, code-owned allowlist. A model, browser,
skill, connector payload, or workspace setting cannot add a source type or
widen its policy. Each registered entry is reviewed like an authorization
policy:

```text
MemorySourceTypePolicy
  registry_version / source_type / domain_family
  source_schema_versions[] / authoritative_projection_reader
  allowed_milestones[] / allowed_memory_kinds[] / projection_slots[]
  eligible_subject_kinds[] / maximum_scope / maximum_sensitivity
  allowed_purposes[] / allowed_retention_classes[] / maximum_ttl
  bounded_input_fields[] / forbidden_field_classes[]
  required_source_refs[] / required_terminal_or_confirmation_states[]
  entity_reference_extractors[]
  exact_field_grounding_rules[] / conflict_projection_readers[]
  fallback_revalidation_reader
  private_origin_rule: DENY
  private_origin_propagation_edges[]
  hiring_policy: DENY | DEDICATED_GATED_PROJECTION
  managed_backend_eligible: bool
  owner / security_reviewer / privacy_reviewer / approved_at
```

Registry compilation rejects an entry unless its projection reader derives
workspace, actor, role/assignment, source visibility, and source version from
durable server state; its bounded input fields exclude the §8.2 classes; every
declared projection slot maps to exactly one memory kind and purpose set; and
its conflict/freshness readers and every run/event/task/webhook ancestry edge
that can reach the source are registered for monotonic private-origin
propagation. An unknown ancestry edge denies the candidate.
`managed_backend_eligible=false` is the default. Hiring candidate/evidence
sources have no generic entry.

Conflict mapping is completeness-reviewed against the domain projection
registry: every entity/reference class emitted by `entity_reference_extractors`
must map to every durable projection family allowed to update that class, plus
a fallback revalidation reader. Build/gate fails on an unmapped entity family.
At runtime, any newer durable record sharing an entity/reference but lacking an
explicit comparator forces `INDETERMINATE` and omission; absence of a mapping
can never mean “no conflict.”

Every candidate and receipt records `registry_version` and `source_type`. An
unknown, removed, version-mismatched, or newly narrowed entry returns
`SKIPPED_POLICY`; it never falls back to a generic extractor. Registry changes
run shadow compatibility, exclusion, grounding, privacy, and conflict fixtures
before activation. Narrowing takes effect on reads and writes immediately and
queues reconciliation of existing items; widening requires a new reviewed
version and does not retroactively backfill.

The approved M2 single-Founder registry starts with only these entries; all have
`maximum_sensitivity=INTERNAL`, `private_origin_rule=DENY`,
`hiring_policy=DENY`, and `managed_backend_eligible=false`:

| `source_type` | Required durable source/reader | Allowed output | Scope/TTL ceiling |
|---|---|---|---|
| `FOUNDER_REMEMBER_COMMAND` | Attributed, confirmed remember command projection; exact founder text after exclusions | `PREFERENCE:explicit_preference` or `REUSABLE_CONTEXT:explicit_context`; purposes from the kind's closed policy | `WORKSPACE`; 365d preference, 90d context |
| `SYNTHETIC_VERIFIED_OUTCOME_CONFIRMATION` | Synthetic terminal workflow/domain receipt plus an attributed Founder confirmation command; deterministic bounded projection and source links | `OUTCOME:confirmed_terminal_outcome`; no model extraction | `WORKSPACE`; 90d |
| `MEMORY_CONTROL_COMMAND` | Attributed correct/pin command plus expected current memory version | Superseding version of the existing kind/slot only; no new generic extraction | Inherit or narrow prior scope/TTL; never widen |

Initial conflict readers are likewise closed: explicit preference/context
compares later profile facts, remember/control versions, and every registered
durable projection sharing its entity refs; confirmed synthetic outcome compares
the source run's terminal domain/provider projections and later
correction/reconciliation receipts; a control-created version inherits the prior
entry's readers and adds the control-version chain. All use the same-entity
fallback; none may declare an empty contradiction set.

No accepted-feedback, unconfirmed/automated rationale or outcome, email, page, document,
arbitrary artifact, session/turn, run, candidate, approval, or provider payload
source type exists in M2. Every M2 write starts with an explicit Founder control
command. Later automatic source types require their own registry and rollout
approval.

### 7.3 `MemoryWriteCandidate` and receipt

```text
MemoryWriteCandidate
  candidate_id
  workspace_id / actor_id / session_id / run_id
  registry_version / source_type
  milestone_kind / memory_kind / projection_slot
  source_refs[] / source_hashes[] / source_versions[]
  requested_scope / purpose / retention_class
  explicit_founder_request: bool
  memory_mode_at_source
  private_origin: bool
  idempotency_key / request_hash
  policy_version
  status: PENDING | CLAIMED | WRITTEN | SKIPPED | FAILED

MemoryWriteReceipt
  receipt_id / candidate_id / memory_id
  request_hash / backend / backend_ref
  outcome: WRITTEN | ALREADY_APPLIED | SKIPPED_POLICY |
           SKIPPED_TOMBSTONE | COLLISION | FAILED_RETRYABLE | FAILED_FINAL
  safe_error_code
  policy_version / model_version
  created_at
```

Idempotency key:

```text
sha256("memory-write:v2" + workspace_id + subject_kind + subject_id
       + milestone_kind + memory_kind + projection_slot
       + sorted(source_ref:source_version))
```

`policy_version` is recorded in the candidate and receipt but is not part of
logical-write identity. Reprocessing the same milestone after a routine policy
version bump returns the original write receipt instead of creating a duplicate
`MemoryItem`. If a new policy or summary schema intentionally requires a new
rendering, an explicit, audited re-evaluation command creates a superseding
candidate/version; changing an idempotency key is not the migration mechanism.
`projection_slot` is a closed, code-owned discriminator declared by the source
registry, never free model text. On same-key `put`, the service compares
`request_hash`: an exact match returns `ALREADY_APPLIED`; a mismatch without a
valid correction/re-evaluation command returns `COLLISION`, quarantines the
candidate, and alerts rather than silently dropping one logical memory. A valid
command supplies the expected current memory/version and creates an atomic
superseding version under the same logical key; it does not disguise a content
change as an idempotent retry.

Before claim, before adapter `put`, and before finalizing a claimed write, the
write path checks deletion tombstones, active `DeletionJob`s, source revocation,
and `private_origin`. A matching tombstone returns `SKIPPED_TOMBSTONE`. Forget
transactionally marks every `PENDING` or `CLAIMED` candidate for the logical
key/source `SKIPPED`, fences its lease, and records the fence generation. If a
late backend write wins a race, finalization detects the fence and immediately
deletes the backend object without making it `ACTIVE`. Replaying an outbox row
therefore cannot resurrect forgotten memory.

`PersistentMemoryAdapter.put` requires a server-minted guarded-write lease that
binds the idempotency key, request hash, current fence generation, and verified
deny-ledger high-water mark; it rejects a missing/stale lease as data. The
portable adapter checks tombstone and item creation in one Firestore
transaction. A managed adapter uses preflight plus
post-write fence verification and compensating delete because the remote write
cannot share that transaction. No code path exposes an unguarded `put` to a
model, skill, runbook, or worker.

### 7.4 Retrieval contracts

```text
MemoryQuery
  workspace_id / actor_id
  principal_version / policy_version / deny_ledger_high_water
  purpose: PERSONALIZE_RESPONSE | AVOID_REPEAT | DRAFT_STYLE |
           RECALL_RATIONALE | MEETING_BRIEF
  query_terms_or_embedding_ref
  query_projection_hash / egress_policy_version
  allowed_scopes[] / sensitivity_ceiling
  allowed_source_types[] / domain_allowlist[]
  not_before / not_after
  current_fact_refs[] / current_domain_refs[]
  max_hits / max_chars / max_tokens / deadline_ms
  session_memory_mode

MemoryHit
  memory_id / bounded_summary / score
  scope / source_refs[] / source_trust
  source_occurred_at / updated_at / expires_at
  verification_status / summary_assurance
  use_reason

MemorySearchResult
  status: SUCCESS | EMPTY | DEGRADED | ERROR
  backend / memory_search_receipt_id / hits[]
  omitted_count              # authorized candidates omitted after admission
  partial: bool
  safe_error_code
```

The adapter never accepts caller-supplied workspace or actor values directly
from the browser/model. `MemoryQuery` is created from `ActorPrincipal` and
`ReconcileContext` inside the server.

For a managed adapter, a closed `ManagedMemoryEgressPolicy` additionally binds
backend, region, allowed sensitivity (`INTERNAL` by default), enumerated
purposes, eligible source types, query-projection schema, maximum 256 query
characters/32 normalized tags, forbidden fields, and approval/version dates.
The adapter receives only that bounded, locally scrubbed projection or a
locally generated embedding reference—never the raw founder turn, transcript,
candidate/Hiring text, approval data, credentials, or unrestricted entity
names. Embedding generation by an external service is itself governed egress,
not a local transformation. Every managed request records a content-free egress
receipt with policy version, region, purpose, sensitivity, sizes, hashes,
latency, and safe outcome.

`omitted_count` is computed only from candidates already authorized for the
current actor. Preauthorization candidates, denials, cache state, and restricted
item counts never affect it. A candidate disposition receipt may record
`ADMITTED`, `EXPIRED`, `ORPHANED`, `DENIED_OR_DELETED`, `STALE_MATERIAL`,
`CONFLICTING`, `INDETERMINATE`, `SUPERSEDED`, or `DUPLICATE`, but those
content-free receipts are restricted operational data and are not a
founder-visible existence oracle.

Founder display state is a derived mapping, not another stored enum:

| Display state | Derivation |
|---|---|
| Active | `lifecycle_status=ACTIVE`, admitted by live checks, not stale/conflicting; `Pinned` is an overlay when `pinned=true`. |
| Conflicting | Active control record, but current conflict disposition is `CONFLICTING` or `INDETERMINATE`; never sent to model context. |
| Stale | Active control record whose declared freshness/revalidation rule failed; never sent to model context. |
| Superseded | `lifecycle_status=SUPERSEDED`; history-only and not recall-eligible. |
| Deletion pending | `lifecycle_status=DELETION_PENDING` until active-store probes pass. |
| Unavailable | `ORPHANED` or deleted source while the actor remains authorized to the item's control record; if item/source authorization is lost, the row is omitted entirely. |
| Memory disabled | Policy overlay for otherwise retained items; not an item lifecycle mutation. |

The closed `scope` spelling is `WORKSPACE` or `ACTOR_PRIVATE` in storage,
backend maps, and contracts; prose may say “actor-private” but does not
introduce a third scope variant. (`subject_kind=ACTOR` is a different axis.)
`memory_search_receipt_id` is the sole search receipt field name throughout the
contract.

### 7.5 Context pack sections

```text
ContextPack
  principal_projection
  durable_run_projection
  confirmed_facts[]
  selected_evidence[]
  advisory_memories[]
  untrusted_external_data[]
  conflicts[]
  omitted_context_summary
  reconciliation_receipt_id
  memory_search_receipt_id
```

When optional memory influences a reviewable artifact, the artifact stores:

```text
AttributableDraftSlot
  slot_id / slot_kind / artifact_id / artifact_version
  allowed_memory_kinds[] / allowed_purposes[]
  durable_evidence_refs[] / memory_influence_refs[]
  bounded_output / output_hash / generator_version
  assembly_rule: VERBATIM_SLOT | NO_MEMORY

MemoryInfluenceRef
  artifact_id / artifact_version / slot_id
  memory_id / authorized_source_refs[]
  assurance_at_use / freshness_at_use / scope_at_use
  memory_search_receipt_id / created_at
```

Optional memory may influence a reviewable artifact only inside a registered,
independently generated `AttributableDraftSlot`. The slot generator receives
only its declared durable evidence and admitted memory; the final assembler
inserts `bounded_output` verbatim and cannot ask another free-form model to
rewrite it or diffuse it into other clauses. Examples may include tone/greeting,
formatting, or a bounded rationale recap; action subject, consequence, provider
claim, legal text, price, recipient, and approval payload slots are `NO_MEMORY`.

Approval UI labels the whole influenced slot and renders its authorized
`MemoryInfluenceRef`s. Per-clause attribution is claimed only at this enforced
slot boundary. For the approved M2 conversational surface, the objective proxy
for material influence is that a saved-memory hit entered answer-generation
context. Every such answer shows the simple, persistent “Informed by saved
context” disclosure with a link to the authorized What Alex knows rows used;
the product does not rely on unverifiable token-level attribution.
M2 provides no optional memory to reviewable/approval artifacts and makes no
per-clause attribution claim. Attributable slots are a later-tier option; if a
later artifact cannot preserve them end to end, optional memory is omitted.

The reference is rendered only after live authorization and is removed or
redacted with its memory/source lifecycle; it confers no authority.

The renderer places `advisory_memories` after durable facts and labels each item
with source, date, scope, and “advisory; verify against current records.” An
external-derived memory remains inside untrusted-data delimiters. Co-Founder
does not use ADK `PreloadMemoryTool` for mixed-trust memory because Google's
quickstart describes preload results entering the system instruction. Retrieval
uses a custom server-owned assembler so trust sections cannot be collapsed.

### 7.6 Workspace brief contracts

```text
WorkspaceBrief
  as_of / since / partial
  needs_you[] / milestones[] / waits[] / inbox[] / terminal_receipts[]
  contributor_errors[]       # safe codes only

BriefItem
  kind / domain_family / title / safe_summary
  occurred_at / urgency / terminal_status
  focus: {resource_kind, resource_id, run_id?}
  source_ref / projection_version
```

`BriefItem` forbids raw approval fields, URLs with credentials, page/email text,
form values, candidate identity/evidence, and signed artifact URLs.

### 7.7 Deletion control records

```text
DeletionJob
  deletion_job_id / workspace_id / actor_id / memory_id
  idempotency_key / source_refs[] / fence_generation
  status: DENY_ACTIVE | ONLINE_DELETING |
          ONLINE_COMPLETE_BACKUP_RESIDUAL | COMPLETE | FAILED_RETRYING
  store_receipts[] / negative_probe_receipts[]
  backup_residual_deadline / revision_residual_deadline
  created_at / updated_at / completed_at

DeletionTombstone
  workspace_id / memory_id / idempotency_key / source_refs_hash
  fence_generation / deny_from / retain_until / deletion_job_id

DeletionDenyLedgerEntry
  ledger_sequence / command_id / deletion_job_id
  workspace_key_hash / memory_id_hash / idempotency_key_hash
  source_refs_hash / fence_generation / deny_from / retain_until
  previous_entry_hash / entry_hash / signer_key_version / created_at
```

Tombstones contain no memory text and outlive the maximum task retry, index
rebuild, restore, and backup residual window. `FAILED_RETRYING` never re-enables
reads or writes.

The operational `DeletionTombstone` lives with current control data for low
latency. The authoritative disaster-recovery deny is also appended to
`DeletionDenyLedger`, a separately administered store/failure domain that is
excluded from application point-in-time restore and ordinary workspace backup.
Entries are append-only, integrity-chained, access-audited, and contain hashes
and control metadata only. Corrections append a new entry; no product operator
can update/delete prior entries or roll the ledger back with Firestore, Cloud
SQL, indexes, or a memory-backend namespace. Production enablement requires an
independently backed-up/replicated ledger and a monitored durable high-water
mark; naming a collection in the same restorable database does not satisfy this
contract.

---

## 8. Write policy and excluded content

### 8.1 Allowed milestones

Optional memory may be proposed only when one of these durable events occurs:

- an explicit Founder remember/correct/pin/confirmation command; or
- a registered verified durable workflow outcome with immutable source links
  and a bounded non-sensitive projection.

No other origin is valid. In M2, even a verified synthetic workflow outcome is
written only after explicit Founder confirmation. Any later automatic outcome
write requires a separately approved registry entry and rollout gate.

The approved M2 rollout enables only explicit remember and Founder-issued
correct/pin/forget/delete controls, plus explicit confirmation of a synthetic
verified durable workflow outcome. It stores exact bounded confirmed text or a
deterministic source-linked outcome projection; no extraction model proposes or
writes memory. Accepted-feedback, unconfirmed rationale/outcome, and every
automatic milestone remain later-tier work requiring separate evaluation and
approval.

### 8.2 Never write

- entire sessions, transcript windows, compacted summaries, or arbitrary turns;
- credentials, refresh/access tokens, API keys, cookies, secret names that
  reveal access, or credential-bearing URLs;
- approval tokens, subject/payload hashes, raw approval details, authentication
  artifacts, or step-up state;
- raw provider payloads, email bodies, webpage instructions, browser page text,
  form values, screenshots, or attachment chunks;
- unconfirmed company claims presented as facts;
- unrestricted candidate identity, résumé/evidence, interview/reference/offer
  content, decisions, comparisons, existence across roles, or any
  `HIRING_RESTRICTED` record;
- legal, health, financial, or similarly restricted data until a dedicated
  policy and review exists;
- model-created policy, permissions, workflow constraints, or claims that an
  external effect succeeded; or
- data from a private session or any run/event/task carrying its monotonic
  `private_origin` taint.

An unsaved import is session-local untrusted context, not a memory source. Raw
content and derivatives are memory-ineligible; sensitive unsaved imports have a
maximum 24-hour temporary TTL and are deleted sooner with the session or user
action. Persisting an import requires the existing explicit governed-save path
and lifecycle policy; it never happens because a model or memory projector found
the content useful.

Deterministic source-type and sensitivity exclusion runs before any Gemini or
managed Memory Bank request. Model-based sensitive-data filtering is defense in
depth only.

### 8.3 Extraction and confirmation

- Explicit founder-authored preference may be stored as `CONFIRMED` after schema
  and exclusion checks.
- External-derived or inferred content never creates an automatic candidate.
  If the Founder explicitly confirms bounded text from an already governed
  saved external source, it follows the remember-command path, retains
  `UNTRUSTED_EXTERNAL` provenance, and receives the short TTL in §10.4. Unsaved
  imports remain memory-ineligible.
- A rationale extracted from a registered internal decision milestone may
  become `GROUNDED` after the complete §8.3 claim/render checks. It then receives
  the decision-rationale TTL and is recall-eligible without founder pinning;
  pinning remains an explicit priority/confirmation control, not a requirement
  for the headline rationale use case.
- Material business facts go to the profile proposal path, not episodic memory.
- If extraction could create both a fact and a memory, the fact proposal owns
  the canonical value; memory may retain only the reusable rationale or context.
- The distiller sees the single supplied milestone and `include_contents="none"`.

Grounding is explicit, claim-based, and **claim-set-first**:

1. extract bounded atomic claim objects from only the registry-approved source
   projection, marking materiality and immutable source refs/spans;
2. deterministically compare names, identifiers, dates, quantities, terminal
   outcomes, and enumerated states with that projection;
3. reject any claim about permission, approval, provider success, identity, or
   current workflow state from episodic-memory storage;
4. run a separately versioned entailment check only as a quality signal, never
   as an authority or access decision;
5. mark only fully supported claims `render_eligible`, then generate the summary
   solely from those typed claim objects and closed connective/template fields;
   the renderer receives neither raw source text nor rejected claims;
6. decompose the rendered summary again into material semantic units and require
   every unit to map to a `rendered_claim_id` with supporting evidence. Any
   unmatched, merged beyond support, or newly introduced material assertion
   fails the candidate; and
7. assign `GROUNDED` only when exact fields pass and material-summary
   decomposition coverage is 100%. Otherwise retain `EXTRACTED_UNVERIFIED` for
   an allowed low-trust path or quarantine the candidate according to policy.

Source hashes bind the cited bytes/version; they do not prove that the summary
is faithful. Checking only the claims a model chose to emit is insufficient:
the post-render decomposition is the coverage gate that catches material text
never represented in the original claim set. A model judge alone cannot promote
summary assurance. Pinning confirms only the exact text shown to the founder and
records its prior assurance so the UI does not disguise how it originated.

`purpose_allowlist` is computed entirely by server code from memory kind,
milestone, scope, and current policy. `normalized_tags` comes from a closed,
code-owned vocabulary: a model may suggest tags, but schema validation may only
remove or map suggestions to a registry-approved tag and can never widen
purpose, scope, sensitivity, or source eligibility.

---

## 9. Identity, tenancy, membership, and Hiring boundaries

### 9.1 Server-derived scope

Every memory/brief operation reconstructs the current `ActorPrincipal` from
verified claims and current membership. It includes workspace, actor, role,
grants, resource assignments, authentication freshness, membership version,
and policy version. Request bodies and model tool arguments cannot supply or
override these fields.

Every datastore query includes `workspace_id` and an eligible subject/scope.
Point reads also verify ownership and source visibility. Revocation takes effect
on the next read/use, even if the backend returned a cached candidate hit.

The isolation contract is **zero unauthorized existence signal**, both across
workspaces and within a workspace across actors, roles, assignments, and
Hiring gates. Unauthorized items cannot change response ids, counts, scores,
pagination, empty/degraded status, export rows, UI copy, or actor-observable
timing. Authorization/source-metadata caches are partitioned by workspace,
actor, role/assignment grant set, membership version, and policy version; a
shared warm cache is not used for actor-visible denial paths. Denial timing is
bounded/padded where partitioning alone cannot remove a measurable oracle.

Before M5, Security approves a versioned `TimingIsolationProfile` per
route/backend/region. The default threat actor is an authenticated workspace
member who can issue the maximum rate-limited sequence of equivalent queries
and observe status, size, and end-to-end latency, but has no server telemetry.
The protected classes are (a) no candidate exists and (b) only unauthorized
candidate(s) exist.

Both classes—and authorized responses on the same route—use a common release
envelope whose floor is at least the rolling p99.9 authorized-path latency under
the pre-registered load profile, plus result-independent jitter. Security may
approve a stricter target, never a weaker one merely to meet UX latency. If the
envelope exceeds the 1,200 ms hard budget or the statistical test in §16 cannot
pass, optional memory is disabled for that route/scope rather than claiming
constant-time behavior. Padding is defense in depth after partitioning; it is
not relied on to repair a query that fetched unauthorized content.

### 9.2 ADK `user_id` migration

Current sessions use `user_id=workspace_id`, which is a compatibility namespace,
not a future human identity. Before actor-private memory or multiple members are
enabled:

1. introduce an opaque, non-PII `adk_subject_key` derived from a versioned mapping
   of `(workspace_id, actor_id)`;
2. create new actor-qualified ADK sessions while retaining read-only access to
   legacy workspace-scoped sessions for the migration window;
3. keep run/domain state independent of that session key;
   separately persist a monotonic `private_origin` flag on any run created from
   a private session and propagate it through every descendant event/task;
4. dual-read session catalogs in shadow and compare authorized counts;
5. never bulk-convert legacy workspace-scoped memory into actor-private memory;
6. classify legacy items as workspace-shared only after source/policy review;
   otherwise suppress them. Before either actor-private or multi-member mode can
   be enabled, every unreviewed legacy item is made non-retrievable; and
7. cut over the ADK bridge only after two-member privacy tests pass.

For a Memory Bank adapter, use exact immutable scope maps such as:

```text
workspace memory: {app, environment, workspace_id, scope_class="WORKSPACE"}
actor memory:     {app, environment, workspace_id, actor_id,
                   scope_class="ACTOR_PRIVATE"}
```

Retrieval uses exactly one permitted scope per request. A broad prefix or
multi-scope list is not an ordinary recall path.

### 9.3 Hiring boundary

Hiring remains governed by [25 — Hiring operations](25-hiring-operations.md).

- Candidate data is ineligible for generic memory.
- Generic brief/search/run/approval services receive no direct collection-scan
  ability over candidate records.
- `HiringBriefContributor` calls dedicated Hiring services with the current
  role and candidate assignments.
- Role-level items may be returned only when the principal has the role grant.
- Candidate-level pending actions are returned only through a dedicated safe
  projection after candidate assignment authorization; the generic item does
  not reveal identity/evidence.
- Counts default to the current actor's authorized candidate assignments. A
  role-wide count spanning other reviewers' assignments is forbidden unless a
  versioned Hiring+Privacy policy explicitly grants that aggregation and its
  small-cohort/existence-signal tests pass.
- Exact Hiring approvals remain run/policy/action scoped and are reauthorized at
  view and decision time. A brief link cannot resolve them.
- Generic recall never supplies context to the Hiring evidence analyst or
  operator. Their context builders remain explicitly role/candidate scoped.
- A same person applying to multiple roles yields no cross-role memory or
  existence signal.

Any Hiring leakage in recall, brief, export, search, logs, or metrics is a
release blocker with a required threshold of zero.

---

## 10. Retrieval and context assembly

### 10.1 Eligibility

Recall runs only when all are true:

- optional memory is enabled for workspace, actor, and session;
- the current purpose is in a code-owned allowlist;
- the current principal and reconciled context are available;
- at least one eligible source scope exists;
- the request has remaining time/token/cost budget; and
- the domain context does not prohibit generic recall.

No recall is performed merely because a new session started. The new-session
brief comes from durable records. Recall is used when the current founder turn
or bounded drafting task would benefit from personalization or a prior
rationale.

### 10.2 Query construction

Code creates a bounded local query projection from the founder's current turn,
explicit focused resource, confirmed facts, and registered purpose. It strips
secrets/control fields, never includes raw candidate or approval content, and
does not pass whole transcripts or the raw founder turn to the backend. A
managed adapter receives only the §7.4 egress-policy projection; `CONFIDENTIAL`
managed recall is denied unless the named approval and exact policy version are
active.

Default filters:

- `not_after=now`;
- `not_before=now-365d` for ordinary episodic recall;
- workspace and current actor-private scopes allowed by policy;
- portable retrieval may include `CONFIDENTIAL` only for purposes enumerated by
  the source registry and current server policy; managed retrieval defaults to
  `INTERNAL` and uses only purposes enumerated by its egress policy;
- active, unexpired, source-available items as required admission constraints
  and backend filter hints where supported; and
- fetch-until-filled within the bounded pagination/deadline budget in §13, with
  at most 5 context hits after live authorization and conflict filtering.

### 10.3 Assembly sequence

1. Reconcile current principal, run/domain versions, and profile facts.
2. Build required durable context first.
3. Resolve memory policy and session mode.
4. Push immutable workspace/subject/scope/time/sensitivity/purpose filters and
   any backend-supported lifecycle hints into the adapter before backend
   ranking; never treat a lifecycle hint as the live authorization check.
5. Retrieve one bounded candidate page through the adapter.
6. For every backend hit, prove the local deny projection has reached the
   current independent-ledger high-water mark, check the current control record
   and denylist, then reauthorize every source against current membership,
   role/assignment, and domain policy. A lag/unavailable ledger degrades recall
   closed. `SUPPRESSED`, `DELETION_PENDING`, `SUPERSEDED`, `ORPHANED`,
   absent/tombstoned, expired, or revoked-source items are always dropped.
7. Apply current-state conflict and material-staleness detection; a conflict or
   indeterminate material comparison is omitted from model context.
8. Deduplicate against confirmed facts, current run/evidence, and supersession
   chains; only the newest eligible active descendant can survive.
9. If fewer than `max_hits` are admitted, fetch the next page within the total
   candidate, round, cost, and deadline budget. Never report preauthorization
   drops through counts or status. Exhaustion returns `partial=true`, not a
   fabricated complete result.
10. Re-rank admitted items, apply token/character limits, and record which hits
    entered context.
11. Re-scan untrusted external-derived text for injection on every recall and
    render advisory/untrusted sections separately.

#### 10.3.1 Conflict detection

Conflict detection is a typed policy, not a free-text promise:

- profile facts, workflow state, approvals, waits, provider outcomes, entity
  ids, dates, and quantities use deterministic field/version comparators;
- each memory kind declares which current durable projections can contradict it
  and which source/version changes make it materially stale;
- the declaration is generated/reviewed against the domain projection registry.
  Any newer durable record sharing an entity/reference that is not covered by a
  declared comparator triggers fallback revalidation; missing coverage yields
  `INDETERMINATE` and omission, never admission;
- rationale/free-text claims use the atomic claim-to-source representation from
  §8.3 plus a separately versioned semantic contradiction classifier;
- the semantic classifier may suppress a candidate or mark comparison
  `INDETERMINATE`, but it can never admit an item that fails a deterministic
  check, grant access, or authorize an action; and
- when a relevant material claim cannot be compared reliably, omit it from
  model context and expose a reviewable stale/conflict state in What Alex knows.

The conflict-policy version and per-candidate safe disposition are recorded in
the memory search receipt. §16 sets false-negative/false-positive gates; if a
memory kind cannot meet them, that kind/purpose stays disabled.

### 10.4 Freshness

Each memory kind has a maximum unchecked age:

| Kind | Default TTL | Revalidation rule |
|---|---:|---|
| Actor style/preference | 365 days | Keep if source/profile still active; prompt for review after 180 days unused. |
| Decision rationale | 180 days | Source decision and relevant policy/domain version must still exist. |
| Run outcome | 90 days | Provider/domain terminal receipt remains authoritative; memory is summary only. |
| Reusable context | 90 days | Source available and no newer conflicting fact/event. |
| Untrusted external-derived item | 7 days | Mandatory exclusion/injection re-scan on every recall; no automatic renewal or pin. |
| Pinned item | 365 days | Pin does not remove expiry; review annually. |

Workspaces may choose shorter periods. Longer periods require privacy review.

TTL is the intersection of memory kind, assurance, provenance, and registry
policy—not whichever row is longest:

| Assurance/provenance | Recall and TTL rule |
|---|---|
| `FOUNDER_CONFIRMED` | Kind TTL, capped by the source-registry maximum. |
| `GROUNDED` + `VERIFIED_INTERNAL` | Kind TTL; therefore a grounded registered decision rationale receives 180 days without pinning. |
| Any `UNTRUSTED_EXTERNAL` provenance | Maximum 7 days even if Founder-confirmed, mandatory per-recall scan, and only explicitly allowed low-risk purposes. |
| `EXTRACTED_UNVERIFIED` from an internal source | Maximum 7-day review window and not eligible for `RECALL_RATIONALE`; it must become grounded/confirmed or expire. |

An assurance downgrade immediately shortens `expires_at` and can make an item
ineligible; an upgrade never extends beyond the original source/registry
retention without a new attributed control receipt.

---

## 11. Correction, pin, forget, disable, export, and deletion

All control mutations are receipted commands with server-derived actor,
workspace, current versions, CSRF protection, and idempotency.

### 11.1 Correction

- Show old value, proposed value, source, and scope.
- A correction creates a superseding item or profile fact; it never mutates
  immutable history.
- The correction commit atomically creates the new `ACTIVE` item and changes
  the old item's control metadata to `SUPERSEDED`. Immutable summary/source
  history remains intact, but the old version is never recall-eligible.
- Workspace-shared corrections require a role allowed by policy.
- Actor-private corrections affect only the current actor.
- Conflicting backend revisions are suppressed until the new item and source
  manifest commit.

### 11.2 Pin

- Pin requires explicit founder action on exact displayed text.
- An `EXTRACTED_UNVERIFIED` item cannot be silently one-click promoted: the UI
  shows its cited source spans and warning, and the founder must correct it or
  explicitly adopt the exact text as a new attributed founder assertion.
- Pin creates an attributed receipt and `CONFIRMED` version.
- Pin may raise rank within the same eligible scope/purpose.
- Pin cannot extend source visibility, sensitivity ceiling, retention beyond
  policy maximum, or authority.

### 11.3 Forget

1. Append the authorized forget command to `DeletionDenyLedger` and obtain its
   durable sequence/high-water receipt. The command is not acknowledged as
   accepted without this barrier; ledger failure returns retryable error data
   and fails optional memory closed for the affected scope.
2. Project that entry transactionally into `DELETION_PENDING`, a `DeletionJob`,
   and the local content-free tombstone, and fence all pending/claimed writes
   for the idempotency key and source lineage. A reader/writer whose local deny
   projection is behind the ledger high-water mark fails closed.
3. Deny reads and writes by id, logical key, source, backend ref,
   revision/embedding/index key, and deletion-job generation.
4. Mark matching `PENDING`/`CLAIMED` candidates `SKIPPED`, then delete
   Firestore content and derived index entries while retaining the tombstone.
5. Call backend deletion and enumerate source/backend matches. Adapter deletes
   are idempotent: an already-absent object/revision is success, not failure.
6. Delete or account for every revision according to the pre-approved backend
   policy; a late fenced write is detected and deleted before finalization.
7. Run negative retrieval probes from product, alternate worker/client, and
   backend adapter.
8. Record `ONLINE_COMPLETE_BACKUP_RESIDUAL` only after all live/derived stores
   pass. The founder sees “Deleted from active systems; backups expire by
   <bounded date>” while encrypted backups/revisions remain.
9. By default, record full `COMPLETE` only after the declared backup/revision
   residual window has elapsed. A cryptographic-erasure shortcut is unavailable
   unless the stored item and every replica/backup were encrypted under a
   reviewed per-item (or equally isolating) envelope key, destruction is
   independently receipted, and Privacy/Legal + Operations approve that proof.
   Shared service/database keys and ordinary managed-backend encryption do not
   qualify. Without that architecture, real deletion latency equals the backup
   retention window.

Failure after the ledger barrier remains safe but visibly incomplete. Admission
and write leases bind the current deny-ledger high-water mark; a concurrent
forget invalidates an older lease before prompt assembly or write finalization.

Forget cannot “un-shape” a response, draft, or external artifact already
generated or sent. The impact preview lists still-authorized influenced
artifacts and their separate revise/delete controls; immutable provider/action
receipts remain governed by their own retention. Memory content/provenance is
removed under this lifecycle, while any required audit record retains only a
content-free deletion/influence tombstone. The UI never implies that forgetting
memory recalled an email or rewrote prior business history.

### 11.4 Disable

Settings are separate for:

- workspace-shared memory read/write;
- actor-private memory read/write; and
- per-session private mode.

Disable takes effect before the next backend call and blocks new write outbox
items. It does not automatically delete existing memory; the UI offers a
separate delete-all command with impact summary.

### 11.5 Export

Export includes typed current items, source ids/labels, scope, trust,
verification, timestamps, retention, status, supersession, and receipts.
Items inaccessible under current source policy are absent entirely: no item
metadata, unavailable marker, timestamp, count, source label, or supersession
edge is emitted. The export contains a generic scope statement that reveals no
omission count. Exports are generated asynchronously, encrypted at rest,
short-lived, actor-attributed, and absent from generic memory/search.

### 11.6 Conversation and source deletion

- Deleting a session does not automatically delete run truth or independently
  retained explicit memories; the impact preview enumerates each class.
- A memory whose only source is that session is deleted with it by default. It
  may survive only when the Founder explicitly created/approved an independent
  retention record before source deletion; that record becomes the new governed
  source, has its own scope/TTL, and is shown in the deletion impact preview.
- Deleting/revoking a source suppresses all derived memories immediately.
- Workspace deletion includes profile facts, memories, backend scopes,
  embeddings/indexes, sessions, artifacts, and projections according to the
  central lifecycle registry.
- Application restore never restores or rolls back `DeletionDenyLedger`. Before
  serving any restored memory read/write, the restore coordinator fetches the
  ledger's current durable high-water mark, applies all denies absent from the
  restored snapshot, rebuilds indexes/backend projections behind those denies,
  and passes negative probes. If the ledger is unavailable, unverifiable, or
  ahead of the local deny projection, optional memory remains disabled.
- Backup/revision retention and deletion SLOs are fixed before a pilot; a
  completion manifest distinguishes active-system erasure from bounded residual
  storage and never claims that product probes inspected backup bytes.

---

## 12. Injection, poisoning, and untrusted data

Threat model:

- an email/page/document says “remember this instruction forever”;
- a founder quotes malicious text while asking for analysis;
- a poisoned memory claims a state, permission, provider success, or identity;
- a retrieved summary contains tool-like syntax or asks to reveal secrets;
- advisory memory shapes a polished draft that a human approves without seeing
  which claims came from memory;
- a compromised backend returns another workspace's item;
- a stale actor-private item is exposed after membership change.

Controls:

1. closed source-type allowlist and sensitivity exclusion before model/backend;
2. explicit memory milestones—external text cannot trigger one;
3. separate source provenance and summary assurance, plus untrusted-data
   delimiters;
4. source and scope reauthorization after retrieval;
5. conflict check against current domain/provider/profile truth;
6. no memory in policy/system-instruction sections;
7. no model-selected scope, retention, or write permission;
8. safe bounded text rendering, no HTML/Markdown execution;
9. adversarial extraction/retrieval tests and optional Model Armor only as
   defense in depth; and
10. consequence guards re-read durable records and ignore memory completely.

Consequence guards close direct authorization but do not make memory-shaped
draft content true. Approved M2 recall is conversational only, shows “Informed
by saved context,” and never supplies optional memory to a reviewable/approval
artifact. In a later separately approved tier, optional memory for such an
artifact is limited to the attributable slots in §7.5. Each slot carries bounded
`MemoryInfluenceRef` records to `memory_id`, source label, freshness, scope, and
assurance; the approval UI marks the slot and lets the actor inspect authorized
provenance. An influenced slot with no confirming durable evidence is labelled
advisory and cannot silently become provider proof or a canonical fact. Free
LLM output outside those slots receives no optional memory; conversation makes
no per-clause attribution claim. Influence refs obey the same access, export,
retention, and deletion policy and must not reveal an inaccessible memory's
existence.

Prompt injection detected in a source creates a safe security signal and may
quarantine the proposed memory. It does not mutate the source record or claim
the source itself is malicious without deterministic evidence.

---

## 13. Performance and cost budgets

Initial pilot budgets:

| Budget | Default |
|---|---:|
| Brief assembly | p95 <= 800 ms, max 8 contributors, max 2 s hard deadline |
| Optional recall | 1 logical query, at most 3 bounded pages/rounds per eligible turn |
| Backend candidates | max 30 examined total; stop when 5 are admitted |
| Context size | <= 1,500 memory tokens and <= 6,000 chars |
| Recall deadline | 500 ms soft, 1,200 ms hard; then explicit degraded recall |
| Memory writes | max 1 candidate per registry-declared `(memory_kind, projection_slot)` and max 2 per milestone by default; async only |
| Extraction input | <= 4,000 source tokens from explicit bounded fields |
| Workspace write quota | 100 memory writes/day in pilot; lower default for synthetic demo |
| Deletion | deny immediately; worker SLO and completion window set before pilot |

Operational cost controls:

- workspace daily/monthly model, backend, storage, and request budgets;
- deduplicate writes before model invocation;
- skip recall when durable facts already answer the context need;
- batch source-manifest and current-record reads and measure the complete
  reauthorization/conflict fan-out under the recall deadline;
- cache only content-free authorization and source metadata for short bounded
  periods, partitioned by workspace, actor, grant/membership version, and
  policy version; never cache cross-actor memory text;
- record tokens, latency, hit/usefulness counts, and safe budget reason;
- circuit-break the optional backend without affecting run/profile access; and
- one workspace-scoped kill switch for reads and writes, plus a global emergency
  kill switch.

No fixed Google price is embedded in this spec. The rollout owner records a
current cost model and budget before each pilot because managed pricing and
service stages can change.

The 500 ms soft budget is provisional until fan-out measurements include up to
30 candidates, live source reauthorization, conflict checks, deduplication, and
the portable and managed backends. A backend that cannot page within the round
budget may return fewer hits only with `partial=true`/`DEGRADED`; it cannot
pretend the result is complete. M2 must also prove the portable keyword/tag
baseline meets §16 usefulness. If it does not, optional recall remains off
until a reviewed tenant-filtered index is qualified; managed Memory Bank is not
an implicit production dependency or the only path to the usefulness gate.

---

## 14. Audit, observability, and operations

### 14.1 Receipts and correlation

Extend the platform correlation chain with:

```text
request/command -> session/run/source event -> memory write candidate
  -> extraction attempt -> adapter write receipt -> memory_id/backend_ref

invocation -> reconciliation receipt -> memory query receipt
  -> returned ids -> admitted ids -> response/usefulness feedback

forget command -> deletion job -> per-store/backend receipts
  -> negative probes -> completion manifest
```

### 14.2 Metrics

- eligible and skipped recall counts by safe reason;
- backend latency/error/degraded rates;
- candidate, authorized, conflict-suppressed, stale, orphaned, and admitted hit
  counts;
- useful/irrelevant/corrected/forgotten rates;
- time to correction and deletion completion;
- cross-workspace/actor/domain denial counts in security-restricted telemetry
  only, never a founder/member dashboard;
- memory tokens and cost by purpose/model/backend;
- brief contributor latency/partial failure/item counts;
- private-session recall/write attempt count, target zero backend calls; and
- Hiring/generic-boundary leakage count, target zero.

Standard logs contain ids, enums, versions, counts, hashes, and latencies only.
They exclude queries, summaries, facts, transcript, source bodies, candidate
content, approval details, secrets, and export contents.

Actor/member-visible analytics apply small-cohort suppression (`k >= 10`),
coarse time buckets, and delayed publication; no private-memory denial,
correction, forget, cache, or availability metric is emitted for a smaller
cohort. Security telemetry is access-restricted and alert-only where even an
aggregate count could reveal another actor's activity. Product response metrics
are computed after authorization and never expose prefilter candidate counts.
Founder teams of 1–3 therefore receive no member-level memory analytics; the
product must not imply otherwise. Reliability/usefulness decisions for those
teams use privacy-reviewed cross-workspace aggregates available only to
authorized operators, plus explicit user feedback—not a hidden small-workspace
dashboard.

### 14.3 Alerts and runbooks

Alert on cross-scope hit, admitted missing-source hit, deleted-item retrieval,
private-session backend call, deletion deadline breach, backend error spike,
orphan growth, cost-budget exhaustion, and Hiring boundary violation.

Runbooks may disable recall/write, replay an idempotent outbox item, reauthorize
sources, retry deletion, rebuild a derived index only after independent-ledger
high-water replay plus local tombstone projection, or quarantine a backend
namespace. Operators cannot manufacture a memory source, change an
approval/domain record, or mark deletion complete without probes.
Every replay follows the same tombstone, private-origin, request-hash collision,
and fenced-lease checks as an ordinary write; an operator cannot bypass them.

---

## 15. Compatibility, migration, backfill, rollout, and rollback

### 15.1 Compatibility rules

- The combined documentation tree has exactly one file for each of 35
  operations, 36 UI, 37 skills, 38 vision, 39 memory, and 40 background work;
  the earlier vision collision is resolved as recorded above.
- Existing profiles remain canonical throughout migration.
- Existing sessions continue to open; actor qualification applies to new
  sessions first.
- Existing run, approval, action, waiting, global-search, and Hiring routes keep
  their domain guards.
- Product and authorized ADK clients use the same adapter factory and namespace
  when shared memory is claimed.
- Backend schema/version is recorded per item and receipt.
- Memory off is a supported steady state, not an error.

### 15.2 Backfill policy

There is **no transcript backfill**.

The approved M1/M2 implementation performs **no memory backfill at all**. M2
starts empty and receives only post-enable explicit Founder commands or
Founder-confirmed synthetic verified outcomes.

If a later tier separately authorizes backfill, candidate sources remain limited
to:

- active confirmed actor preferences with source refs;
- founder-pinned decisions explicitly selected during migration; and
- synthetic terminal run summaries used for evaluation.

Backfill is idempotent, source-versioned, effect-free, and shadow-only first.
Legacy adapter items without sufficient actor/source provenance default to
`SUPPRESSED` (or `ORPHANED` when only the source manifest is unresolved); they
are never treated as active, workspace-shared, or actor-private by default and
are not guessed into a scope. Candidate/Hiring-restricted data is excluded by
registry and negative scan.

### 15.3 Staged pilot gates

M1 is independently reviewable, implementable, and releasable. It depends on
no optional-memory backend, actor-memory migration, extraction model, or M2–M6
qualification. Keeping both programs in this specification preserves their
shared authority/privacy boundary; it does not couple their release timelines.
M1 may land while optional memory remains disabled indefinitely.

**M0 — Contract freeze and threat model**

- record the closed initial decisions in §19 and freeze the M1/M2 schemas,
  three closed source types, purpose allowlist, UI controls, retention,
  deletion definition, disclosure copy, and competition wording;
- verify the initial workspace has exactly one Founder member, the controlled
  portable backend is the only configured adapter, and managed backend calls
  fail closed;
- approve every initially enabled M2 `MemorySourceTypePolicy`; no
  generic/default/automatic source entry is permitted;
- inventory current memory/profile/session wiring and all `user_id` assumptions;
- accept the M1/M2 test pack and kill switch; memory remains disabled until the
  M2 staging gates pass.

**M1 — Deterministic brief, no optional memory**

- implement and shadow within the initial authorization boundary;
- contributor parity, partial failure, and Hiring negative tests pass;
- replace the interim `pending_signals` reader with a first-class durable
  wait/domain projection, or omit that contributor with `partial=true`;
- brief reads no transcripts and invokes no model.

**M2 — Synthetic single-Founder workspace memory, user-controlled**

- use `subject_kind=WORKSPACE` and `scope=WORKSPACE` in an isolated synthetic
  workspace whose current membership cardinality is exactly one Founder;
  membership growth fails recall/write closed and requires the later M5 design;
- use the existing controlled portable backend only; managed/external memory
  adapters remain disabled and receive zero calls;
- allow only explicit remember/correct/pin/forget/delete commands and explicit
  Founder confirmation of source-linked synthetic verified workflow outcomes;
  no model extraction, accepted-feedback write, automatic milestone, backfill,
  transcript/session ingestion, or unsaved-import source is enabled;
- show “Informed by saved context” whenever a memory hit enters answer context;
  provide no optional memory to reviewable/approval artifacts;
- private-origin, source authorization, supersession, collision, resurrection,
  pagination, private-session, disclosure, source-session deletion, and active
  store/backup-state deletion probes pass;
- the portable baseline independently meets the useful-recall threshold or
  optional recall stays off pending a separately qualified index.

**M3 — Managed backend shadow evaluation**

- isolated Agent Platform Memory Bank namespace, no founder-facing recall;
- `INTERNAL` synthetic data only; no `CONFIDENTIAL` egress;
- bounded query projections, approved purpose/region/source policy, and 100%
  content-free egress receipts pass before shadow traffic;
- compare useful recall, scope isolation, deletion, latency, cost, and region;
- measure paged reauthorization/conflict fan-out against the complete recall
  budget and cross-actor timing-oracle test;
- re-verify every load-bearing official Google link and the current managed
  service behavior documented in §6.3;
- version-pin SDK/service configuration and record Google service stage.

**M4 — Single-founder internal canary**

- actor-qualified sessions, What Alex knows, limited purposes;
- 100% source coverage and zero cross-scope/Hiring/deleted recall;
- approval/review artifacts receive no optional memory unless their attributable
  slot contract has separately passed §16;
- explicit user feedback and kill switch exercised.

**M5 — Two-member privacy pilot**

- actor-private preference tests, membership revocation, role/assignment changes,
  export, and delete-all pass;
- cross-actor existence-signal and small-cohort telemetry tests pass;
- the Security-approved timing profile meets the statistical threshold under
  load, and Hiring counts remain actor-assignment scoped absent a separately
  approved aggregation policy;
- no real candidate data.

**M6 — Production consideration**

- qualified security/privacy review, regional controls, SLOs, cost envelope,
  backup/restore deletion proof, and measured trust thresholds accepted;
- independent deny-ledger restore-to-pre-forget drills pass; and
- backup-residual expiry is measured end to end; no cryptographic-erasure
  shortcut is claimed without the qualified key architecture and proof; and
- attributable-slot disclosure or the product-approved coarse/no-memory
  alternative is accepted for each founder surface.

M1 and M2 are the only implementation-authorized gates in this revision. Passing
a gate permits the bounded next stage; it does not authorize M3–M6 or any
external effect.

### 15.4 Rollback

Rollback order:

1. disable new memory writes;
2. disable recall and remove advisory context from new invocations;
3. drain/fence write workers and preserve receipts;
4. keep profile/run/evidence paths active;
5. retain deletion jobs/local tombstones and the independent deny ledger until
   their declared retention completes;
6. switch adapter reads to the prior qualified backend only if deletion and
   scope parity are proven; otherwise remain off; and
7. restore UI to facts/run views while What Alex knows reports memory disabled.

Never fall back to process-local memory, transcript scan, or unfiltered backend
listing. A rollback or point-in-time restore cannot roll back the deny ledger or
make previously deleted memory retrievable.

---

## 16. Evaluation and test plan

The first release pack is small, deterministic, and adversarial. It contains
synthetic workspaces A/B, actors A1/A2, ordinary funding runs, and a protected
Hiring fixture with two roles and assignments.

### 16.1 Required cases

| Area | Test |
|---|---|
| Correctness | Explicit remembered preference is recalled in a new authorized session with exact source and correct scope; irrelevant items are absent. |
| Summary faithfulness | Summary is rendered only from grounded typed claims, then decomposed again; every material output unit maps to supporting claim evidence, hallucinated/unmapped text fails, and source trust alone cannot promote it. |
| Assurance/TTL | A grounded internal decision rationale remains eligible for the 180-day kind TTL without pinning; unverified internal rationale is review-only/7-day, and untrusted external inference never escapes its 7-day ceiling. |
| Freshness | New confirmed fact/domain event suppresses a conflicting older memory before prompt assembly. |
| Conflict reliability | Structured/free-text fixtures measure material false negatives/positives; a newer same-entity record from an intentionally omitted projection forces fallback revalidation and omission rather than admission. |
| Privacy | Actor A2 cannot retrieve A1 private preference and observes zero ids, counts, scores, pagination, status, timing, export rows, metrics, cache, or other existence signals; a workspace-shared item is available only under allowed role. |
| Timing isolation | Under the approved attacker/load profile, repeated no-item and unauthorized-item queries use the common release envelope and fail the pre-registered classifier/distinguishability attack. |
| Tenancy | Workspace B receives zero ids, scores, timing distinctions, or existence signals from A. |
| Deletion | Forget suppresses reads and writes immediately; in-flight/replayed writes cannot resurrect it; idempotent delete treats absence as success; all active backends/indexes return no hit after online completion; a restore deliberately taken from before the forget replays the independent ledger and returns no hit before traffic opens. |
| Private session | Zero memory backend read/write calls; later standard session cannot recall private turns; a run spawned privately and later woken by webhook remains tainted and ineligible. |
| Supersession | Correction atomically activates the new item and marks the old `SUPERSEDED`; queries matching only the old wording still cannot retrieve it. |
| Idempotency collision | Two declared memory kinds/slots from one milestone both write; same-key/same-hash returns the prior receipt; same-key/different-hash quarantines with `COLLISION`. |
| Pagination | Early backend pages dominated by revoked/unauthorized hits neither under-return silently nor leak their count; bounded backfill returns eligible later hits or an honest partial result. |
| Brief | Cold start produces the same brief from durable records; no ADK event/transcript list method or model call occurs. |
| Approvals | Brief shows safe exact subject/consequence/expiry but no token/hash/details; link alone cannot decide. |
| Hiring | Generic memory/brief reveal no candidate identity/evidence/cross-role or within-role cross-assignment existence; actor assignment count excludes another reviewer unless the approved aggregation policy explicitly permits it. |
| Managed egress | Managed adapter receives no `CONFIDENTIAL` item, raw founder turn, forbidden field, or unapproved purpose; every allowed request matches region/DPA policy and has a content-free egress receipt. |
| Backup completion | Without an approved per-item key architecture, deletion remains in backup-residual state until the configured retention window actually elapses. |
| Injection | Email/page text asking to persist instructions creates no write; poisoned retrieved text is re-scanned on every recall, expires under the short TTL, remains untrusted, and cannot trigger tools. |
| Indirect influence | Approval-time artifacts permit memory only in registered attributable slots inserted verbatim; every influenced slot is disclosed with authorized provenance, and free-form/no-slot artifacts receive no optional memory. |
| M2 disclosure | Every conversational answer whose generation context contains a saved-memory hit shows “Informed by saved context”; answers with no admitted hit do not falsely claim recall. |
| M2 boundary | Membership growth, managed-adapter call, automatic write, transcript/import candidate, backfill, or approval-artifact memory use fails closed with zero effects. |
| Authority | Memory claiming “approved,” “sent,” or “candidate advanced” cannot satisfy any guard or success state. |
| Failure | Backend error renders degraded recall and does not appear as empty/forgotten memory; durable work still loads. |
| Trust | Founder can locate source, understand scope/freshness, correct/pin/forget/disable, and predict private-session behavior. |

### 16.2 Release thresholds

- cross-workspace recall leakage: **0**;
- cross-actor private leakage: **0**;
- cross-actor/role/assignment unauthorized existence signals across response,
  export, metrics, cache, and pagination: **0**;
- timing attack advantage distinguishing no-item from unauthorized-item under
  the approved M5 profile: **95% confidence upper bound <= 0.05** (equivalently
  classifier AUC upper bound <= 0.55); profile records route/backend/region,
  sample size, load, rate limit, release envelope, and jitter;
- unauthorized Hiring/candidate disclosure: **0**;
- deleted/private-source recall on every product, worker, adapter, and restored
  online retrieval/probe surface: **0**; encrypted backup/revision residuals
  follow the separately declared bounded window and are not misreported as
  inspected by this threshold;
- authority-conflict acceptance: **0**;
- memory-induced action/approval transition: **0**;
- source manifest coverage for admitted hits: **100%**;
- exact-field faithfulness for admitted summaries (identity, dates, quantities,
  terminal outcomes, permissions): **100%**;
- atomic material-claim faithfulness for admitted summaries: **>= 99%**, with
  no unsupported claim allowed to receive `GROUNDED`;
- material semantic-unit decomposition coverage in every `GROUNDED` rendered
  summary: **100%**, with **0** material units absent from the grounded claim
  set and **0** output generated from rejected/raw-source content;
- material-conflict false negatives on the labelled suite: **0**;
- admitted same-entity memories when a newer durable projection lacks an
  explicit comparator: **0**; fallback revalidation/omission coverage: **100%**;
- material-conflict false positives on the labelled eligible suite: **<= 5%**;
- explicit backend failures represented as degraded/error: **100%**;
- grounded internal rationale assigned the registered 180-day ceiling without
  requiring pin: **100%**; unverified rationale exceeding 7 days: **0**;
- unapproved managed-backend egress (`CONFIDENTIAL`, raw turn, forbidden field,
  purpose, region, or source type): **0**; allowed-request egress receipts:
  **100%**;
- role membership alone exposing a count over another reviewer's candidate
  assignment: **0**;
- deletion marked `COMPLETE` before backup expiry without a qualified per-item
  key-destruction proof: **0**;
- restored snapshots older than a forget that serve the forgotten item before
  independent-ledger replay: **0**;
- reviewable/approval artifacts that receive optional memory outside a declared
  attributable slot, or rewrite an influenced slot after attribution: **0**;
- disclosed `MemoryInfluenceRef` coverage for every influenced approval slot:
  **100%**;
- M2 answers with an admitted saved-memory hit that omit “Informed by saved
  context”: **0**; false disclosures without an admitted hit: **0**;
- M2 managed-backend calls, automatic writes, transcript/import candidates,
  backfilled items, multi-member recall/write, or approval-artifact memory hits:
  **0**;
- useful recall on labelled eligible cases: **>= 80%**;
- irrelevant admitted recall: **<= 5%**;
- user correctly identifies fact versus memory and private versus shared in
  moderated tasks: **>= 90%**;
- correction/forget control completion without assistance: **>= 90%**.

Any zero-tolerance failure blocks promotion regardless of aggregate usefulness.

### 16.3 Test layers

1. Pure schemas, source-registry compilation, policy, exclusions, canonical
   ids, trust/assurance, state/display mappings, claim-only rendering,
   post-render decomposition, assurance/TTL intersections, contradiction-map
   coverage/fallback, attributable slots, precedence, and rendering.
2. Firestore transaction/CAS, request-hash collision, outbox idempotency,
   tombstone write denial, late-write fencing, supersession, idempotent
   deletion, independent-ledger projection/high-water, restore-to-pre-forget,
   and concurrent correction/forget.
3. ADK session reconciliation, actor-qualified keys, product/worker/alternate
   client parity, and cold restart.
4. Backend contract suite run unchanged against portable and managed adapters,
   including sensitivity/purpose/region/query-egress denial and receipts.
5. Model evals for claim-level faithfulness, extraction, relevance, conflict,
   repeated injection, and honest uncertainty.
6. Accessibility/usability tests for source/freshness/scope and controls.
7. Staging load/statistical tests for the approved timing attacker profile and
   common release envelope, plus within-role cross-assignment Hiring fixtures.

---

## 17. Acceptance criteria

### 17.1 Architecture and authority

- [ ] Workspace facts, run truth, session projection, and optional memory are
      separately named, stored, retrieved, and explained.
- [ ] The contract and adversarial suites admit no path in which memory
      authorizes a transition, approval, action, wait resolution, provider
      success, identity, or source access.
- [ ] Current domain/provider/profile records deterministically outrank memory.
- [ ] Source provenance and extracted-summary assurance are separate; source
      trust alone cannot promote or rank an ungrounded summary.
- [ ] A versioned source-registry entry defines every eligible source,
      projection reader, slot, purpose, scope/sensitivity ceiling, exclusion,
      grounding, conflict, and retention rule; unknown sources fail closed.
- [ ] Contradiction mappings cover every emitted entity/reference against the
      domain projection registry; an uncovered newer same-entity projection
      forces fallback revalidation and omission.
- [ ] `GROUNDED` summaries are rendered only from supported typed claims and
      achieve 100% post-render material-unit decomposition coverage.
- [ ] Generic new chat and “Since you were away” use durable projections, not
      transcript scanning or semantic recall.
- [ ] No `pending_signals`-derived wait is presented as durable: M1 either reads
      the registered durable wait/domain projection or returns an explicit
      partial result.
- [ ] All adapter calls are server-derived; immutable scope filters precede
      backend rank and live actor/source/deletion/conflict checks precede final
      admission.
- [ ] Product and authorized alternate clients use one qualified adapter or
      honestly report memory disabled.

### 17.2 Founder control and transparency

- [ ] Since you were away shows active milestones, open waits, safe exact pending
      approvals/decisions, safe inbox items, and recent terminal receipts.
- [ ] What Alex knows shows source, freshness, scope, source trust, summary
      assurance, derived display state, and use reason for every fact/memory.
- [ ] Grounded internal decision rationales can satisfy the 180-day recall use
      case without pinning; all unverified rationale paths are bounded to the
      documented review/7-day rules.
- [ ] Correct, pin, forget, disable, delete-all, and export are attributed,
      versioned, and receipted.
- [ ] Instrumented private-session tests observe zero optional memory
      reads/writes, and a later standard session retrieves no item sourced from
      private turns.
- [ ] Runs and descendant events created from private sessions carry a durable
      monotonic taint that survives worker/webhook wakes and blocks writes.
- [ ] Failure, conflict, stale source, and deletion-pending states have honest UX.

### 17.3 Security, privacy, and lifecycle

- [ ] Principal/workspace/member/role/assignment/source scope is re-derived on
      every operation; request/model fields confer no authority.
- [ ] Multi-member actor privacy migration is complete before actor-private
      production recall.
- [ ] Schema, registry, fuzz, and adversarial tests reject every fixture
      containing credentials, approval internals, raw provider/external
      content, whole transcripts, unscoped evidence, or Hiring-restricted data
      before a `MemoryItem` write or managed-backend request.
- [ ] Cross-role, cross-assignment, and generic-endpoint tests find no path that
      bypasses Hiring domain gates or reveals candidate existence/content.
- [ ] Hiring brief counts default to the actor's assignments; role membership
      alone never reveals another reviewer's candidate count.
- [ ] Cross-actor/role/assignment tests find no unauthorized ids, counts,
      scores, export markers, metrics, cache/timing distinctions, pagination, or
      other existence signals.
- [ ] Source revocation and forget deny retrieval and matching writes before
      asynchronous cleanup; in-flight/replayed writes cannot resurrect data.
- [ ] Correction marks the old item `SUPERSEDED`, and same-key request-hash
      mismatch produces a visible collision receipt instead of silent loss.
- [ ] TTL, export, source deletion, workspace deletion, backups, independent
      deny-ledger replay, and restore are tested across every configured
      backend.
- [ ] Prompt-injection and memory-poisoning suites produce zero unauthorized
      guard outcome, tool call, state transition, or external effect.
- [ ] The append-only deletion deny ledger is outside application restore;
      restore-to-pre-forget tests replay its current high-water mark before
      traffic and retrieve zero deleted items.
- [ ] Without qualified per-item key destruction, deletion cannot reach
      `COMPLETE` before the declared backup/revision window expires.
- [ ] Managed-backend policy defaults `CONFIDENTIAL` to portable-only, forbids
      raw-turn/forbidden-field query egress, enumerates purpose/region/source
      eligibility, and receipts every permitted request.
- [ ] The Security-approved timing profile meets the statistical §16 threshold
      under load; failure disables recall for the affected route/scope.
- [ ] Approval-time artifacts expose authorized influence for every registered
      memory-influenced slot, preserve influenced slot bytes through assembly,
      and never provide optional memory to unconstrained artifact prose or
      launder it into canonical fact/provider proof.

### 17.4 Quality and operations

- [ ] Correctness, summary faithfulness, conflict reliability, freshness,
      privacy/existence-signal, tenancy, deletion/resurrection, Hiring,
      injection, failure, and trust evals meet §16 thresholds.
- [ ] Latency/token/write/cost budgets are code-enforced and observable.
- [ ] Standard logs contain no memory text, query text, profile values, source
      bodies, candidate data, approval details, or secrets.
- [ ] Kill switches, backend outage, rollback, and deletion runbooks are
      rehearsed in staging.
- [ ] Competition materials distinguish existing behavior, proposed behavior,
      isolated evaluation, and production dependency.
- [x] The promotion candidate contains one reconciled doc at each number 35–40,
      with the earlier numbering collision resolved.
- [ ] Implementation remains inside the approved M1/M2 boundary at the top of
      this document; M3–M6, managed memory, multi-member memory, automatic
      writes, artifact influence, and external effects remain unauthorized.

### 17.5 Initial M1/M2 authorization checks

- [ ] M1 uses only registered durable contributors, no model/memory/transcript
      scan, and replaces or explicitly omits the interim wait input.
- [ ] M2 runs only in a workspace with exactly one Founder member and stores all
      items as `WORKSPACE`; membership growth disables recall/write.
- [ ] M2 uses only the existing controlled portable backend and instrumented
      tests observe zero managed/external-memory calls.
- [ ] M2 accepts only the three §7.2.2 source types, requires explicit Founder
      control/confirmation, and performs no extraction, automatic write, or
      backfill.
- [ ] Raw transcripts and unsaved imports create zero memory candidates;
      sensitive unsaved imports expire within the declared temporary TTL.
- [ ] Every answer receiving a saved-memory hit displays “Informed by saved
      context”; reviewable/approval artifacts receive no optional memory.
- [ ] Forget suppresses reads/writes immediately, source-session deletion
      cascades unless independent retention was explicitly recorded, and the UI
      reports active-store versus backup-residual deletion honestly.

---

## 18. Founder approval and retained technical acceptance checks

The Founder has approved the complete bounded M1/M2 design in this document
and authorized automatic implementation and rollout after the executable
technical gates pass. The former five discipline-specific release review rows
are consolidated into this one Founder product decision; they are not runtime
roles, allowlists, deployment pauses, or additional sign-off records. The
items below remain design traceability and automated acceptance criteria, not
separate human promotion gates. Per-external-effect exact approval remains a
different control and is not waived by this release authorization.

### Product

- [ ] Approve “Since you were away” as a deterministic durable-work brief, not a
      generated memory summary.
- [ ] Approve the five brief sections and initial bounds.
- [ ] Approve the separate Confirmed facts and Remembered context presentation.
- [ ] Approve correction, pin, forget, disable, delete-all, and export controls.
- [x] Initial disclosure decision: show “Informed by saved context” whenever a
      saved-memory hit enters answer generation; M2 gives review/approval
      artifacts no optional memory. Attributable slots remain later-tier work.
- [ ] Approve the private-session copy and the fact that durable safety/work
      records still apply.

### Scope and privacy

- [x] Initial scope decision: memory is `WORKSPACE`-shared in the single-Founder
      workspace; actor-private/multi-member memory is not enabled.
- [ ] Approve the proposed TTLs and annual pin review.
- [ ] Approve the assurance/TTL mapping: grounded internal rationale may use 180
      days; unverified rationale is review-only and capped at 7 days.
- [ ] Approve the 7-day external-derived TTL, per-recall re-scan, and separation
      of source provenance from summary assurance.
- [x] Raw transcripts remain session history; there is no transcript backfill,
      automatic whole-session ingestion, or cross-session transcript scan.
- [ ] Approve the strict exclusion of candidate/Hiring-restricted data from
      generic memory.
- [ ] Approve the initial closed source-registry entries; unknown source types
      and any generic fallback remain denied.
- [ ] Approve zero unauthorized existence signals across actors/roles, including
      export, metrics, cache/timing, pagination, and omission counts.
- [ ] Approve actor-assignment-scoped Hiring counts or the exact separately
      reviewed wider aggregation policy.
- [x] Source-session deletion cascades to related memory unless an explicit
      independent retention record was approved before deletion.

### Google and deployment

- [x] Initial backend decision: use only the existing controlled portable
      backend; no external managed memory service is enabled in M1/M2.
- [x] M1/M2 competition/demo scope remains on the controlled portable backend;
      any isolated managed-backend evaluation belongs to later M3 approval.
- [ ] Approve the selected region, service account/IAM conditions, SDK version,
      managed-service stage, retention/revision behavior, and cost cap before
      any managed canary.
- [ ] Approve managed-query egress fields/purposes and `INTERNAL`-only default;
      separately approve any future `CONFIDENTIAL` path and its DPA/region.
- [ ] Decide whether Agent Runtime is worth a separate deployment evaluation;
      it is not required for memory or for this design.

### Rollout traceability

- [x] Approve the bounded M0–M2 gates and zero-tolerance thresholds; M3–M6
      remain later gated proposals.
- [x] The authenticated Founder is the single product authority for M1/M2
      release review; no OWNER/OPERATOR or five-reviewer release gate exists.
- [ ] Approve the actor-qualified ADK session migration before multi-member use.
- [ ] Security approves the timing attacker/load profile, common release
      envelope, statistical threshold, and latency-budget fallback.
- [ ] Approve rollback to memory-off as a normal supported state.
- [ ] Approve the distinction between active-system deletion completion and the
      declared encrypted backup/revision residual window.
- [ ] Accept that `COMPLETE` waits for backup expiry unless qualified per-item
      key destruction is actually implemented and proven.
- [ ] Approve the independently administered append-only deletion deny ledger
      and fail-closed restore procedure.

---

## 19. Recommended defaults and open decisions

Closed owner decisions for the approved M1/M2 boundary:

1. Optional memory is `WORKSPACE`-shared inside one single-Founder workspace;
   actor-private and multi-member behavior is not enabled.
2. Raw chat transcripts remain session history and are never automatic memory
   or backfill. M2 memory originates only from explicit Founder controls or a
   Founder-confirmed synthetic verified durable workflow outcome, always with
   governed source links.
3. Source-session deletion deletes related memory unless the Founder approved
   an independent retention record before deletion.
4. M2 uses the existing controlled portable backend; no managed external memory
   service is called.
5. When saved memory enters answer-generation context, the answer shows
   “Informed by saved context.” M2 supplies no memory to review/approval
   artifacts.
6. Unsaved imports remain session-local and memory-ineligible; sensitive
   unsaved imports expire within 24 hours or sooner with session/user deletion.

Initial defaults:

- memory off through M1, then opt-in only for a synthetic single-Founder M2
  workspace after its gates pass;
- no whole-session ingestion, transcript backfill, preload tool, or memory
  backfill;
- all M2 items use `subject_kind=WORKSPACE` and `scope=WORKSPACE`;
- exact explicit remember/control text and Founder-confirmed deterministic
  synthetic outcome projection are the only initial write paths;
- 365-day preference maximum, 90-day reusable-context/outcome maximum;
- portable Firestore adapter as the sole initial backend;
- deterministic brief remains independent of optional memory; and
- Hiring candidate data remains excluded from generic memory.

Later-tier decisions still requiring review:

1. For multi-member workspaces, choose workspace-shared versus actor-private
   memory and the roles allowed to correct/pin/delete shared memory.
2. Approve any managed-memory jurisdiction, region, DPA, IAM, revisions,
   retention, query egress, service stage, and cost cap.
3. Approve automatic rationale/outcome source types, eligible terminal outcome
   classes, assurance-specific TTLs, and extraction evaluation.
4. Decide whether managed revisions are disabled, shortened, or retained for a
   bounded recovery window.
5. Set the production user-trust threshold beyond the §16 minimum.

Additional gate-review decisions have explicit owners:

| Decision | Required owners | Promotion boundary |
|---|---|---|
| Attributable-slot behavior on any future review/approval surface | Product + Security | Before any memory-influenced review/approval artifact |
| Material semantic-unit annotation protocol for model-extracted memory | Evaluation + Security | Before any automatic extraction tier |
| Timing-oracle attacker model, pre-registered noise bound, and padding budget | Security | Before M5 |
| Backup/revision deletion window and whether reviewed per-item key destruction exists; default is no shortcut | Privacy/Legal + Operations | Before M6 |
| Independent deletion-ledger service, failure domain, signing, replication, and retention | Security + Platform + Operations | Before production DR/M6 |
| Whether `CONFIDENTIAL` memory/query data may reach a managed backend, including egress and region | Privacy/Security + Platform | Before M3 uses such data; default deny |
| Within-role Hiring count granularity and small-cohort rule | Hiring + Privacy | Before any Hiring contributor beyond synthetic tests |
| Assurance-specific TTL for automatically extracted decision rationales | Product + Evaluation | Before automatic rationale recall is enabled |

The one Founder authorization is recorded by product policy. Every technical
test and evidence pack still names the exact candidate hash it assessed; a
passing result for an older revision cannot promote a newer revision.

M1 and M2 are implementation-eligible only within the top-of-document boundary.
Optional memory remains disabled until M2 gates pass. All later-tier decisions
and M3–M6 remain unauthorized; the product continues safely from durable
profiles, runs, evidence, sessions, and receipts.

---

## 20. Bounded M1/M2 implementation record — 2026-08-28

This record covers the implementation-eligible M1/M2 foundation only. It is
not a rollout, production-readiness claim, or authorization for a later tier.
No commit, deployment, environment enablement, managed-memory configuration,
or data migration was performed as part of this work.

### 20.1 Implemented boundary

- M1 now builds “Since you were away” only from registered durable approval,
  run-event, wait, safe-inbox, and terminal-run projections. It makes zero model,
  optional-memory, transcript, session-event, and `pending_signals` reads;
  contributor failure is rendered as an honest partial result. Hiring run kinds,
  candidate references, and restricted inbox items are excluded.
- M1 requires both `DURABLE_BRIEF_M1_ENABLED=true` and an exact workspace
  allowlist match. Private and temporary sessions stop before brief assembly.
- M2 adds the explicit “What Alex knows” surface with separate current confirmed
  facts and optional memory, plus source, trust, freshness, scope, retention,
  state, and use-reason visibility. Founder controls cover explicit remember,
  correction by supersession, pin by attributed confirmed version, forget, and
  disable.
- The only M2 write sources are an exact founder remember command, an exact
  founder memory-control command, and founder confirmation of a source-linked
  synthetic terminal non-Hiring workflow outcome. The portable controlled
  `DurableStore` is the only backend. The generic ADK memory hook is disconnected
  from both text and voice runners.
- Tenant, principal, authenticated Founder role, exact synthetic single-member workspace,
  source visibility, session mode, and scope are derived and checked server-side.
  All optional items are `INTERNAL`, `WORKSPACE` scoped, and source manifested.
  Private and temporary sessions perform zero optional-memory store or deletion-
  ledger calls.
- Credentials, approval tokens, Hiring/candidate material, raw transcript/email
  shapes, authority claims, and instruction/tool-shaped input are rejected.
  Stored text is rescanned on every recall, integrity checked, source reauthorized,
  and admitted only for bounded conversational purposes. Tool callbacks reject
  every tool call while advisory memory is present, and review, approval, draft,
  document, artifact, attachment, and effect-shaped turns receive no memory.
- Current confirmed profile facts suppress overlapping optional memory; current
  source run version and terminal state outrank outcome summaries. Memory remains
  advisory, is disclosed as “Informed by saved context,” and cannot authorize or
  evidence an action, transition, approval, provider effect, or artifact.
- Recall is bounded to 2,000 query characters, three ten-row candidate pages,
  25 live source reauthorizations, five returned hits, three prompt entries,
  and 100 authority pointers. Search receipts record safe page/candidate/source-
  check/context/latency counts and report a full final page as partial rather
  than silently claiming complete recall.
- Retention is capped at 365 days for preferences and 90 days for reusable
  context/outcomes. Recall synchronously rejects expiry; `expires_at_ts` supports
  the separately applied Firestore TTL policy.
- Forget and source-session deletion are deny-first. An independently configured
  non-restored ledger fences ids, logical lineages, source refs, and source
  sessions before content is blanked and marked `DELETION_PENDING`. Tombstones,
  jobs, negative active-store probes, bounded backup-residual status, replay
  denial, and pre-forget restore tests prevent resurrection. Ordinary lifecycle
  deletion cannot erase the independent deny ledger.
- The legacy migration is dry-run by default and can only suppress pre-v2 rows;
  it reads no transcript and creates no memory or managed-backend call.

### 20.2 Verification evidence

Evidence captured in this worktree on 2026-08-28:

- `pytest -q tests/unit` — **1,146 passed, 2 skipped**.
- Focused M1/M2 privacy, tenancy, correction, deletion, restore, private-session,
  disclosure, injection, and release-boundary suite — **36 passed**.
- `python scripts/check_contrast.py` — all dark, light, and mock-portal pairs
  passed the Document 16 contrast contract.
- `python -m py_compile` over the changed application, agent, service, migration,
  and TTL deployment modules — passed.
- `python -m json.tool` over both Firestore index and TTL manifests — passed.
- `python scripts/deploy_firestore_ttl.py --project example-project --dry-run`
  — declared exactly the `memory_items.expires_at_ts` TTL command and applied
  zero changes.
- `git diff --check` — passed.

### 20.3 Exact residual gates and disabled tiers

M1 and M2 remain default-off in `.env.example`; the normal deployment script
does not set either release flag and does not apply TTL. Before even the bounded
synthetic M2 pilot is enabled, the release process must:

1. provision and verify a separately administered Firestore deletion-ledger
   database excluded from ordinary application restore;
2. apply and verify the reviewed `memory_items.expires_at_ts` TTL policy;
3. attest a named backup policy and bounded residual window;
4. verify the allowlisted workspace has exactly one active authenticated
   synthetic Founder and no other member;
5. run staging usefulness, paging/reauthorization, latency/cost, deletion probe,
   and restore-fence evidence and keep recall off if the threshold is not met;
6. pass every executable technical entry check against the exact candidate.
   No separate release-review approval record is required: the Founder has
   supplied standing authorization for automatic entry and promotion after the
   technical gate passes. This does not waive per-effect approvals or authorize
   M3+.

M3–M6 remain disabled and unauthorized. There is no automatic extraction,
accepted-feedback write, generic milestone write, transcript/session ingestion,
backfill, managed Memory Bank adapter, vector index, actor-private memory,
multi-person sharing, `CONFIDENTIAL` memory egress, approval/review-artifact
influence, connector activation, or external effect. Enabling any of those
requires the separately owned decisions and gates in §§15–19.

---

## 21. Controlled M2 staging-pilot release runbook

This section advances the completed, default-off M2 implementation through its
technical staging and promotion gates. Standing Founder authorization permits
release automation to deploy and enable the exact candidate immediately after
those gates pass. The release checker itself remains read-only and returns
`BLOCKED` until every required live technical evidence item exists.

### 21.1 Evidence files and immutable candidate

Use
`infra/durable-memory-m2-release-attestation.example.json` as the schema and
store the filled copy in the controlled release-evidence system, not in a
public repository when its references are sensitive. It contains references
and aggregate counts only—never memory text, query text, profile values,
workspace source bodies, candidate data, approval details, credentials, or
tokens.

Bind every review and test pack to the exact candidate:

```bash
python scripts/check_durable_memory_m2_release.py --print-candidate-hash
```

Any change to a hashed file produces a new candidate hash and invalidates prior
technical evidence. Entry and promotion remain separate technical gates.

### 21.2 Read-only entry preflight

Before requesting authorization to change staging, prepare all of the following
while `DURABLE_MEMORY_M2_ENABLED=false`:

1. Set `DURABLE_MEMORY_M2_WORKSPACE_ALLOWLIST` to exactly one intended synthetic
   staging workspace id. No comma-separated second id, wildcard, production id,
   or request-supplied id is accepted.
2. Name the exact synthetic Founder actor. A live read must find exactly one
   `ACTIVE` membership for the workspace and it must have that actor id,
   `role=FOUNDER`, a bound authentication subject, and `synthetic=true`. Zero,
   duplicate, non-Founder, unauthenticated, non-synthetic, or additional active
   membership blocks entry. M2 has no OWNER or OPERATOR product role or role
   allowlist.
   Bind `DURABLE_MEMORY_M2_MEMBERSHIP_CLASS=SYNTHETIC` for entry. Promotion to
   the normal Founder instead requires `DURABLE_MEMORY_M2_MEMBERSHIP_CLASS=FOUNDER`;
   a missing or mismatched class fails both the checker and runtime closed.
3. Name `FIRESTORE_DATABASE` and a different non-default
   `MEMORY_DELETION_LEDGER_DATABASE`. A second collection in the application
   database is not independent. Record the ledger's administrative owner,
   replication evidence, current high-water read, and the control proving it is
   excluded from ordinary application point-in-time restore.
4. Validate the exact TTL declaration without applying it:

   ```bash
   python scripts/deploy_firestore_ttl.py \
     --project "$GOOGLE_CLOUD_PROJECT" \
     --database "$FIRESTORE_DATABASE" --dry-run
   ```

   The output must declare one command for
   `memory_items.expires_at_ts` and report `applied: 0`. Entry remains blocked
   until a separately authorized operator has applied the policy and the
   release checker observes exactly one matching `ACTIVE` TTL field.
5. Name the application backup policy, policy/change reference, accountable
   owner, evidence reference, and residual window of 1–365 days. The values
   must exactly match `DURABLE_MEMORY_BACKUP_POLICY_REF` and
   `DURABLE_MEMORY_BACKUP_RESIDUAL_DAYS`, with
   `DURABLE_MEMORY_BACKUP_POLICY_ATTESTED=true`. The evidence must cover the
   application database and every derived store used by M2, distinguish online
   deletion from backup expiry, and confirm that `COMPLETE` waits for residual
   expiry. Shared encryption keys do not create a cryptographic-erasure
   shortcut.
6. Qualify the memory-specific export delivery: asynchronous generation,
   encryption at rest, an artifact lifetime no longer than 24 hours, current
   source-authorization filtering, actor attribution, exclusion from generic
   search, and zero metadata or omission count for inaccessible items. A direct
   synchronous JSON response or unencrypted local file does not satisfy this
   gate.
7. Confirm the checker is running against the exact authenticated synthetic
   Founder named by the canary membership gate. Passing the technical gate is
   sufficient for the already-authorized release automation; there is no
   separate review-row prerequisite.

Run the live, read-only gate from the intended staging revision and service
configuration:

```bash
python scripts/check_durable_memory_m2_release.py \
  --attestation /controlled/path/m2-attestation.json \
  --gate entry \
  --workspace-id "$SYNTHETIC_MEMORY_WORKSPACE_ID" \
  --founder-actor-id "$SYNTHETIC_MEMORY_FOUNDER_ACTOR_ID" \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --database "$FIRESTORE_DATABASE" \
  --live
```

The checker performs no mutations. Offline membership snapshots can exercise
the schema but remain `PROVISIONAL` and can never produce a ready decision.
Its local safety probe uses separate in-memory application and deny-ledger
stores, forgets an item, restores the application snapshot from before forget,
and proves the unchanged ledger still returns zero hits. This is useful
provisional evidence, not a substitute for the live ledger or staging restore
drill.

A ready entry result also prints a content-free `runtime_binding` containing
the exact candidate hash, evidence-record hash, workspace, and Founder actor.
The normal application requires those values through
`DURABLE_MEMORY_M2_RELEASE_CANDIDATE_SHA256`,
`DURABLE_MEMORY_M2_ENTRY_ATTESTATION_SHA256`, and the explicitly asserted
`DURABLE_MEMORY_M2_ENTRY_GATE_ATTESTED=true`. It recalculates the candidate
hash at runtime and stays UI-hidden/API-inert on any mismatch, missing backup
attestation, non-independent ledger database, multi-workspace allowlist, or
enabled generic memory backend. `DURABLE_MEMORY_M2_ENABLED=true` alone is never
sufficient and the checker never authorizes that later flag change.

### 21.3 Independent ledger and restore-fence drill

After a separately authorized pilot-entry change and before promotion:

1. Record application and ledger high-water values and take an isolated
   recovery snapshot from before a controlled synthetic forget.
2. Forget the synthetic item. Require the independent ledger append receipt
   before acknowledging the command. Confirm ordinary, alternate-client, and
   direct portable-adapter probes return zero and the job reports
   `ONLINE_COMPLETE_BACKUP_RESIDUAL`, not `COMPLETE`.
3. Restore only the application/retrieval stores into a closed recovery target.
   Do not restore or roll back the deny ledger.
4. Before any product traffic, read the ledger's current high-water, replay all
   missing denies, rebuild derived projections behind that fence, and require
   local projection high-water equality.
5. Run negative probes over product, worker, alternate client, portable adapter,
   and rebuilt index surfaces. One forgotten/private-source hit, one write
   accepted behind the fence, unavailable/unverifiable ledger state, high-water
   mismatch, or traffic opened before probes is an immediate abort.
6. Record the drill reference, snapshot time, ledger/local high-waters, probe
   surfaces, zero-hit result, operators, and UTC completion time. Do not record
   memory text.

### 21.4 Pilot sequence and thresholds

Only after the entry checker returns `READY_FOR_AUTHORIZED_STAGING_PILOT` may
release automation apply the already-authorized configuration and enable M2
for the exact allowlisted synthetic workspace. No additional release approval
is required and no other workspace or tier participates.
Run the labelled pack with at least 20 eligible cases and capture content-free
receipts/aggregates.

| Area | Success threshold | Immediate abort |
|---|---|---|
| Usefulness | useful eligible cases >= 80%; irrelevant admitted hits <= 5% | either bound missed |
| Recall paging | <= 3 pages, <= 30 candidates examined, <= 25 source reauthorizations, <= 5 admitted hits; any exhausted full final page says `partial=true` | silent under-return, unbounded fan-out, preauthorization drop/count leak |
| Latency/cost | recall p95 <= 500 ms and max <= 1,200 ms; <= 1,500 tokens/6,000 chars; <= 100 writes/workspace/day; measured spend <= pre-approved cap; explicit backend errors = 100% | hard deadline, token/write/cost cap, or disguised error exceeded |
| Deletion | zero reads/writes after deny; zero active/index/adapter probe hits; backup residual shown honestly | any resurrection, late write, or early `COMPLETE` |
| Source revocation | zero recall, write, and existence signal after access is revoked | any id/count/status/timing/content signal or admitted hit |
| Private session | zero optional backend reads/writes; zero later standard recall; zero descendant memory writes | any optional I/O or private-origin recall/write |
| Correction/supersession | old-version hits = 0; exactly one active descendant; atomic supersession and visible idempotency-collision rates = 100% | old version admitted, split active lineage, or silent collision |
| Restore fence | ledger replayed before traffic; local high-water equals ledger; forgotten hits = 0 across every probe; traffic-before-probes = 0 | any mismatch, missing surface, hit, or early traffic |
| M2 boundary | managed calls, automatic writes, transcript/import candidates, backfill, multi-member operations, approval-artifact hits, connector/effect actions = 0 | any non-zero counter |
| Delete all | fresh content-free plan; settings disabled before deletion; zero active lineages, recall hits, or writes committed behind the disable fence; receipt and backup residual shown | stale plan accepted, late write, remaining lineage/hit, missing receipt, or early `COMPLETE` |

All Document 39 zero-tolerance thresholds remain blockers even if aggregate
usefulness passes. Membership is rechecked during the pilot; a second active
member stops recall/write and aborts the pilot.

### 21.5 Monitoring and founder-facing degradation

The staging dashboard uses content-free search/control/deletion receipts and
must show eligible/skipped counts, explicit error/degraded rates, recall
latency, pages/candidates/source checks/admitted hits, context size, write count,
cost, deletion state/age, and kill-switch state. It must not show memory/query
text or preauthorization candidate identities/counts.

Alert policies are required for cross-scope hit, admitted missing-source hit,
deleted-item retrieval, private-session backend call, deletion deadline breach,
backend error spike, cost-budget exhaustion, and Hiring boundary violation.
Every safety alert requires a verified notification channel that pages the
pilot owner and invokes the kill switch; console-only policy objects do not
satisfy promotion and the alert is not left for a daily review.

Founder copy is deliberately plain and does not imply current work is stale:

- backend/ledger degradation: “Optional recall is unavailable. Current
  workspace facts and work are still up to date.”
- bounded-query exhaustion: “Optional saved context was skipped because this
  request exceeded the bounded recall budget. Current workspace facts and work
  are still up to date.”
- membership change: “Optional saved context is unavailable because pilot
  eligibility changed. Current workspace facts and work are still up to date.”
- deletion online complete: “Deleted from active systems; encrypted backups
  expire by <date> under <named policy>.”

Default-off, founder-disabled, and private-session states are policy choices,
not outage banners. Saved-context failure never blocks current confirmed facts,
run truth, evidence, or ordinary conversation.

### 21.6 Kill switch and rollback rehearsal

The kill order is:

1. disable workspace writes through the founder-controlled setting when that
   path is healthy;
2. set the deployment M2 gate false to stop all recall/write admission for the
   pilot workspace;
3. confirm no new memory search/write receipt is admitted after the barrier;
4. keep deletion jobs, local tombstones, and the independent ledger active;
5. keep profiles, runs, evidence, sessions, approvals, and receipts available;
6. leave `PERSISTENT_MEMORY_BACKEND=disabled` and do not fall back to process
   memory, transcript scan, managed memory, or unfiltered listing; and
7. show What Alex knows as memory disabled while retaining permitted founder
   deletion controls and honest residual status.

Rollback restores the prior application revision only after confirming that
it preserves the current ledger/tombstone deletion fence. A datastore restore
is unnecessary for an ordinary code/config rollback and must not be performed.
If a recovery restore is required, §21.3 is mandatory before traffic. Rehearsal
evidence must prove memory is off, durable work remains available, deletion
work remains active, and no M3+ path becomes configured.

### 21.7 Promotion decision and final sign-off

After the pilot, return M2 to disabled before holding the release decision.
Complete every measured result and monitoring/rehearsal reference in the
attestation, then run the same checker with `--gate promotion`. Promotion is
automatic only when that exact candidate passes every measured technical gate;
no separate promotion-review row is required.

Remaining external blockers are reported as named `blocked_checks`; they are
never replaced with local placeholders. `READY_FOR_AUTHORIZED_M2_ROLLOUT`
means the evidence pack is complete and standing Founder authorization permits
the release automation to proceed. The checker itself remains read-only and
does not authorize M3–M6.

### 21.8 Local release-readiness evidence — 2026-08-28

Evidence captured in this isolated worktree without cloud mutation:

- `pytest -q tests/unit` — **1,168 passed, 2 skipped**;
- focused M1/M2/release-readiness/safe-path suite — **50 passed**;
- `python scripts/check_contrast.py` — all Document 16 dark, light, and
  mock-portal contrast pairs passed;
- changed Python modules compiled and both the TTL and attestation JSON files
  parsed successfully;
- TTL helper dry-run declared exactly one
  `memory_items.expires_at_ts` command and reported `applied: 0`;
- the independent-ledger provisional probe advanced the separate ledger,
  returned zero hits after online forget, returned zero hits after restoring
  the application snapshot from before forget, and performed zero private-
  session memory writes; and
- `git diff --check` passed.

The example attestation intentionally returns `BLOCKED`; it contains no live
workspace, ledger, TTL, backup, monitoring, pilot, restore-drill, or reviewer
evidence. No claim is made that those external gates passed. No flag was
enabled, TTL applied, migration run, deployment performed, or M3+ tier
configured while producing this evidence.

## 22. Authorized local single-Founder M2 pilot

The founder may run a non-live M2 behavior pilot in the existing local app
without creating or reading a cloud staging project. This lane is an explicit
local exception to the staging topology above, not pre-production evidence and
not authority to promote or deploy M2.

Both the local lane and the future normal-app canary have exactly one product
role: `FOUNDER`. Any request must first
pass the app's existing authentication middleware and then resolves to the one
synthetic local Founder record. The record uses `product_role=FOUNDER`; it does
not create or display an OWNER/OPERATOR role or allowlist. Older non-memory
platform workflows still contain legacy role labels; those labels do not grant
M2 admission and require a separately scoped cleanup.

`python scripts/local_m2_pilot.py prepare` creates two distinct SQLite files
under ignored `tmp/spec39-m2-local-pilot/`: the pilot application store and its
non-restored deletion deny ledger. The local ADK session database is also bound
inside that ignored directory. It never imports ordinary local data. The
command exercises authenticated-Founder entry, remember, correct, pin, forget,
Delete all, private-session isolation, expiry simulation, disable, safe
degradation, encrypted asynchronous export, export expiry, and a pre-forget
application-store restore against the unchanged ledger. Only after all checks
pass does it write `pilot.env` and enable the explicit local Founder setting.
The export artifact and AES-256-GCM key stay in this ignored namespace with
owner-only permissions; plaintext exists only in the authenticated download
response. `PERSISTENT_MEMORY_BACKEND` remains disabled.

While local pilot mode is active, server code blocks attachments, explicit
workflow launches, every model-selected tool, cloud session-catalog writes, and
all HTTP mutations except authentication, local session/chat, and the reviewed
memory controls. No automatic extraction, transcript ingestion, outcome
confirmation, managed memory, multi-user sharing, connector effect, or M3+
surface is admitted.

The kill switch is `python scripts/local_m2_pilot.py disable` followed by a
local app restart. It turns off both the workspace setting and environment gate
without deleting data or the ledger. After stopping the app,
`python scripts/local_m2_pilot.py clear --confirm-clear LOCAL_M2_PILOT` removes
only the exact pilot databases and environment file; content-free entry evidence
is retained. A datastore restore must never include the ledger.

The pilot binds dedicated local port `8093`. Port `8090` remains the canonical
normal Co-Founder app and must never be occupied by a pilot process.

This local proof does not satisfy cloud TTL, backup-policy/residual-window,
monitoring, or the executable live entry gate in §21. Those remain honest,
pending pre-production blockers.

## 23. Founder-only normal-app M2 canary preparation

The bounded implementation wires a one-Founder normal-app canary to the
executable §21 promotion gate. Attestation schema version 4 uses
`founder_actor_id`. Entry requires exactly one active synthetic Founder;
promotion requires exactly one active authenticated non-synthetic Founder in
the normal workspace. Both require `role=FOUNDER` and a bound authentication
subject. OWNER and OPERATOR are neither M2 product roles nor M2 allowlists.

Normal-app M2 now has two independent fail-closed layers:

1. `DURABLE_MEMORY_M2_ENABLED` remains false by default and is absent from the
   ordinary deployment path.
2. Even if that flag is accidentally set, UI visibility and API admission stay
   off until one exact workspace is allowlisted, the generic memory backend is
   disabled, a separate non-default deletion-ledger database and named backup
   policy are configured, the live entry gate is explicitly attested, and the
   runtime's recomputed candidate hash plus evidence-record hash match the
   content-free bindings emitted by the checker. The dedicated export bucket,
   KMS key, and export-delivery attestation must also match the reviewed entry
   pack.

The checker continues to require the release flag to be false while evaluating
entry or promotion. A ready result emits an enablement-eligible
`runtime_binding`; standing Founder authorization permits release automation to
apply that exact binding without another review pause. Candidate drift, a malformed evidence hash,
membership growth, legacy role values, a second workspace, a shared/default
ledger database, missing backup evidence, or a non-disabled generic memory
backend makes the normal surface absent and commands return fail-closed errors.

The bounded local implementation also enforces the 100 item-version writes per
workspace/UTC-day limit with a budget mutation committed atomically alongside
each item and receipt. Every item write atomically checks the current memory
settings version, so Disable or Delete all fences a writer already admitted by
an earlier request. Delete all requires a fresh content-free impact hash,
disables memory before enumeration, deny-first deletes every current lineage,
and reports active-system deletion separately from backup residual expiry.
The export control plane is implemented as an attributed durable job and
request-bound worker. It re-derives the current authenticated Founder and every
included source authorization, omits inaccessible items without counts or
markers, caps items and bytes, excludes artifacts from generic search,
invalidates an artifact when any included lineage is forgotten or revoked, and
expires it within 24 hours. The local pilot uses AES-256-GCM files in its
isolated namespace. A normal canary remains unavailable unless a dedicated GCS
bucket with the configured Cloud KMS key and lifecycle policy is externally
qualified and candidate-bound; local encryption evidence does not satisfy that
cloud gate.

Local verification on 2026-08-28 completed with normal-app M2 unchanged:

- `pytest -q tests/unit` — **1,168 passed, 2 skipped**;
- **50 focused M1/M2, release-boundary/readiness, local-pilot, and terminal
  safe-path checks passed**;
- the release example returned `BLOCKED` and the independent-ledger restore
  probe remained only `PROVISIONAL`;
- the TTL helper dry-run declared one command and applied zero changes;
- the Document 16 contrast checker passed all dark, light, and mock-portal
  pairs;
- the normal listener remained untouched on port 8090; only the isolated 8093
  pilot may be restarted to load and validate the exact candidate; and
- no deployment, migration, TTL application, cloud write, external effect, or
  normal-app flag change occurred.

The single material next action is external: complete the live §21 entry pack
for the exact candidate—active TTL observation, independent-ledger high-water
and restore exclusion, named backup/residual evidence, qualified encrypted
asynchronous export delivery, and exact authenticated normal Founder
membership. Until the promotion pack
returns `READY_FOR_AUTHORIZED_M2_ROLLOUT`, normal-app M2 remains off and
invisible.

## 24. M1/M2 implementation completion and remaining release boundary

The safe implementation path for the only implementation-authorized Spec 39
tiers is complete. M1 provides the deterministic cross-session brief. M2
provides explicit Founder-only remember, correct, pin, forget, Disable, Delete
all, private-session exclusion, bounded recall, source reauthorization,
independent deletion-ledger restore fencing, expiry support, and encrypted
short-lived export. Firestore collection registration, lifecycle ownership,
indexes/TTL manifest, dry-run migration, candidate binding, task identity, UI
routes, and executable entry/promotion checks are included. The normal surface
is absent unless the exact release binding passes and `.env.example` keeps all
M2 and export attestations false.

The isolated port-8093 validation uses one authenticated synthetic `FOUNDER`
and no OWNER/OPERATOR product role. Its 20 labelled cases produced 100%
intended hits, 0 irrelevant hits, and stayed within the fixed paging,
reauthorization, latency, and context bounds. It passed
remember/correct/pin/forget, private exclusion, expiry, deletion and restore
fences, Disable, encrypted export, post-forget export invalidation, and
synthetic cleanup. It made zero cloud, connector, managed-memory, normal-port,
or external-effect calls and left optional memory disabled after measurement.

“Implementation complete” is not “released.” The normal one-Founder canary
continues to fail closed until the exact candidate has all of the following
live evidence: active `memory_items.expires_at_ts` TTL, independently
administered ledger high-water and restore exclusion, named backup policy and
residual window, qualified GCS/KMS export delivery and lifecycle, exact active
authenticated normal Founder membership, and monitoring/rollback proof.
Once those technical gates pass, standing Founder authorization permits the
normal one-Founder M2 rollout. No automatic memory write, transcript ingestion,
memory-authorized connector action, external effect, or M3–M6
activation is part of this completion.
