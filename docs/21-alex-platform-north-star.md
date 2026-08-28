# 21 — Alex Platform North Star

**Status:** Proposed architecture for review. This is the post-v1 target, not a
claim that the current grant-application build already implements the platform.
It does not override the hackathon scope guard in
[14](14-build-plan.md). New implementation work must be added to the build plan
in independently shippable phases.

**Related current contracts:** [architecture](01-architecture.md),
[state/dormancy](03-state-machine.md), [agents](04-agents.md),
[evaluations](11-evaluations.md), and [security](12-security.md). Where this
north-star document describes a future replacement boundary, the current spec
remains binding until that migration phase is implemented and accepted.

**Implementation and convergence overlay:**
[34](34-platform-architecture-and-convergence.md) makes the authority hierarchy,
state/session/memory boundaries, generic-runtime extraction, approval/action
protocol, delivery topology, operational controls, and gated migration phases
explicit. It does not independently authorize implementation.

**Decision this document makes:** Alex is a stable conversational coordinator
over many independently durable workflow runs. A workflow is not an agent, a
chat, or a prompt. It is a versioned process with explicit state, policies,
waits, and completion contracts. Models may select and propose workflows, and
may compose plans from registered capabilities, but code validates and controls
everything that can have an external effect.

Normative words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are used in the
RFC sense.

---

## 1. Product north star

A founder should be able to talk naturally to Alex:

> Find investors who back African AI infrastructure companies. Use this deck,
> rank the best 20, and prepare personal introductions. Do not send anything.

Alex should understand the objective, bind the deck to that work only, start a
durable investor-outreach run, and return immediately. The founder can continue
talking, start a customer-discovery run, or close the browser. Workers can scale
to zero. Hours or weeks later, replies, signatures, deadlines, deliveries, and
approvals resume the correct run. Alex reports progress in the originating
conversation and can answer questions about every active obligation.

The same platform must support, without turning Alex into one enormous prompt:

1. Investor outreach operations.
2. Customer discovery.
3. Compliance and deadline monitoring.
4. Hiring operations.
5. Bookkeeping reconciliation.
6. Vendor and partnership follow-through.
7. The existing funding and program application workflow.

The founder experiences one Alex. Underneath, independent workflows use
different agents, models, capabilities, controls, and event sources.

### 1.1 Success definition

Alex is a real operational assistant when it can:

- understand discussion without turning every sentence into a task;
- turn an explicit objective into a typed, inspectable request;
- execute safe work autonomously and ask only for material missing facts;
- keep several unrelated runs active without state leaking between them;
- become dormant at every human or external wait;
- resume the exact run from a correlated event after a cold start;
- show what it did, why, from which evidence, and under whose authority;
- change a plan safely when facts or founder instructions change;
- never use conversational fluency as evidence that an external action occurred.

### 1.2 Non-goals

The platform is not:

- a model that may generate and execute arbitrary Python, YAML, SQL, or shell;
- a universal browser operator with permission to act on any site;
- a promise that one state machine fits every domain;
- a reason to put all tools on a single agent;
- a continuous model process that waits in memory for days;
- an autonomous legal, hiring, accounting, or financial decision-maker;
- a mechanism for bypassing server-side approvals with natural-language consent;
- a replacement for authoritative systems of record.

### 1.3 Current-to-target gap

The current implementation is a strong first vertical, not a failed version of
the platform. Migration preserves its proven controls while changing ownership
boundaries:

| Current implementation | North-star target |
|---|---|
| One workflow file cached at startup | Versioned workflow registry plus validated run-specific plans |
| One `current_step` and active application in the chat session | Independent `WorkflowRun` state; chat references many runs |
| Model interprets every `/wake` message as ordinary text | Typed intent envelope; deterministic slash-command token |
| Upload immediately enters profile-ingestion flow | Explicit profile/journey/run/entity/reference-only attachment scope |
| UI can call task endpoints directly | Unified run-launch API; internal dispatch endpoints stay private |
| Discovery request waits for the task handler | Persist, enqueue, return `202` and `run_id` |
| Proactive result targets the latest founder session | Run-correlated origin delivery plus durable founder inbox |
| Seven agents arranged for one grant vertical | Reusable bounded roles selected per workflow step |

No migration is allowed to weaken the existing state guards, idempotency,
approval, audit, evidence, or dormancy behavior.

---

## 2. The platform model

The architecture has seven logical layers. These are responsibility boundaries,
not seven deployable services; v1 may implement several layers in one Cloud Run
service as long as their contracts stay separate.

```mermaid
flowchart TB
    L1["1. Experience layer<br/>chat, voice, slash commands, run cards"]
    L2["2. Understanding layer<br/>intent, reference, and attachment resolution"]
    L3["3. Workflow control plane<br/>registry, planning, validation, policy"]
    L4["4. Durable runtime<br/>runs, steps, waits, leases, events"]
    L5["5. Agent execution layer<br/>bounded reasoning roles"]
    L6["6. Capability layer<br/>typed tools and external adapters"]
    L7["7. State and governance layer<br/>records, artifacts, audit, evals, identity"]

    L1 --> L2 --> L3 --> L4 --> L5 --> L6 --> L7
    L7 -. "state, evidence, and events" .-> L4
    L4 -. "progress and questions" .-> L1
```

### 2.1 Experience layer

Owns conversation, run cards, approvals, notifications, voice, attachment
mentions, and deterministic command affordances. It never owns authoritative
workflow state.

### 2.2 Understanding layer

Converts a founder utterance into one of:

- `CONVERSE` — answer or discuss; create no run;
- `INSPECT` — retrieve status/evidence from existing records;
- `START` — propose or start a new workflow run;
- `MODIFY` — change the scope or plan of an existing run;
- `CONTROL` — pause, resume, cancel, retry, or reprioritize a run;
- `DECIDE` — resolve a pending question or approval through the proper UI/API;
- `CORRECT` — correct a fact or outcome and preserve the old value in audit.

Its output is untrusted structured data. The control plane validates it.

### 2.3 Workflow control plane

Selects a versioned template or composes a plan from registered capabilities.
It checks input schemas, policies, budgets, permissions, and event reachability
before any run can become `QUEUED`.

### 2.4 Durable runtime

Owns run and step state, dispatch, retries, leases, cancellation, dormancy,
event correlation, and recovery. It does not decide business meaning with free
text when a deterministic transition can decide it.

### 2.5 Agent execution layer

Provides bounded reasoning roles. An agent receives the minimum context for one
step and can call only the capabilities registered for that role and step.

### 2.6 Capability layer

Provides typed operations such as `investor.search`, `email.prepare`,
`email.send`, `calendar.find_slots`, `ledger.propose_match`, or
`portal.submit`. Capabilities own validation, idempotency, authorization, and
their external adapter.

### 2.7 State and governance layer

Owns durable domain records, artifacts, identity, credentials, append-only run
events, action audit, model provenance, retention, eval evidence, and policy.

---

## 3. Binding terminology

| Term | Definition |
|---|---|
| **Alex** | The stable founder-facing identity and conversational coordinator. |
| **Conversation** | A human interaction surface. It may reference many runs but is not their system of record. |
| **Intent envelope** | Validated structured interpretation of one founder message. |
| **Workflow definition** | A code-reviewed, versioned template of states, steps, policies, waits, and outputs. |
| **Workflow plan** | The immutable version of the actual steps selected for one run. It may originate from a template or validated composition. |
| **Workflow run** | One durable execution instance with a `run_id`. |
| **Domain state** | Workflow-specific position such as `DRAFTING` or `WAITING_FOR_REPLY`. |
| **Runtime status** | Universal execution state such as `RUNNING`, `WAITING`, or `FAILED`. |
| **Step** | A bounded unit of work with typed inputs and a completion contract. |
| **Agent** | A reasoning worker selected to perform a step. It is not the workflow or state store. |
| **Capability** | A registered, typed operation exposed to an agent or deterministic worker. |
| **External action** | An operation visible outside the platform: send, submit, sign, invite, write to a ledger, or pay. |
| **Wait** | A durable subscription to a human, timer, or external event; never a blocked model call. |
| **Run event** | An append-only fact used to update projections and resume work. |
| **Replan** | A new immutable plan version for an existing run, linked to the reason and prior version. |

The following equation is binding:

> Conversation expresses the objective. Workflow controls progress. Agent
> reasons about a step. Capability performs an operation. State and events
> prove what happened.

---

## 4. Natural conversation contract

Natural conversation remains the primary interface. Slash commands are a
deterministic accelerator, not a second product and not a requirement.

### 4.1 Do not turn every sentence into work

Alex MUST distinguish conversation from execution. For example:

| Founder message | Default interpretation |
|---|---|
| “Fundraising has been exhausting.” | `CONVERSE`; respond empathetically, create no run. |
| “We should probably find more investors.” | Discuss or offer a concrete run proposal; do not silently contact anyone. |
| “Find 30 fintech investors in Africa and rank them.” | `START`; safe research may begin. |
| “Use the second list and prepare emails, but don't send.” | `MODIFY` or `START`; resolve “second list,” bind `send=false`. |
| “Send those now.” | Consequential `DECIDE`; show the approval surface and bind it to the exact drafts and recipients. |
| “Pause that until October.” | `CONTROL`; resolve “that” to one run or ask if genuinely ambiguous. |

An action intent normally requires an operative verb, an object, and enough
scope to select a workflow. A model confidence score alone MUST NOT authorize
an external action.

### 4.2 Intent envelope

The understanding layer emits a versioned envelope:

```json
{
  "schema_version": "1",
  "mode": "START",
  "objective": "Find and rank 30 fintech investors in Africa",
  "workflow_hint": "investor_outreach",
  "entities": [],
  "constraints": {
    "max_results": 30,
    "geographies": ["Africa"],
    "send_messages": false
  },
  "artifact_refs": [
    {"artifact_id": "art_123", "scope": "run", "purpose": "evidence"}
  ],
  "requested_effects": ["research", "score", "draft"],
  "forbidden_effects": ["send"],
  "deadline": null,
  "autonomy": "safe_actions_only",
  "origin": {
    "session_id": "s_abc",
    "message_id": "msg_456",
    "client_request_id": "req_789"
  },
  "confidence": 0.96,
  "missing_material_fields": []
}
```

Rules:

- `client_request_id` MUST be unique per founder submission and is the launch
  idempotency key.
- `requested_effects` and `forbidden_effects` MUST survive into run policy.
- Artifact references MUST be explicit; “the attached deck” resolves to an ID,
  never a filename guessed from chat history.
- A missing field is material only if it changes safety, the chosen workflow,
  or the usefulness of the result. Alex MUST NOT ask ceremonial questions.
- Low-confidence entity/run reference resolution requires one focused question.
- Founder corrections update records through a correction event; they do not
  erase history.

### 4.3 Reference resolution

References such as “that,” “the second one,” and “use the same approach” resolve
against a structured conversation projection containing:

- recently mentioned `run_id`s;
- visible entity IDs and ordered result sets;
- pending questions and approvals;
- artifact IDs explicitly attached or mentioned;
- the current UI selection.

Chat text is supporting context, not authoritative identity. When two active
runs are equally plausible, Alex asks which one.

### 4.4 Conversational continuity

Alex SHOULD acknowledge a launched run in one short message:

> I started Investor Outreach run IO-184 using the deck only for this run. I’ll
> rank 30 investors and prepare drafts; nothing will be sent.

The founder may keep talking while the run proceeds. Status questions use
durable run projections, not an inferred summary of previous messages.

For the funding journey, `/discover` starts discovery and matching, then Alex
waits for an opportunity selection. A valid selection creates a linked
`grant_application:v1` run in the same founder conversation and journey card.
Alex narrates this as one continuous experience even though each run retains
its own plan, domain state, audit history, and safety boundary.

---

## 5. Invocation surfaces

Every surface compiles into the same intent/run contract.

### 5.1 Natural language

Normal chat, voice transcripts, and API messages use the understanding layer.
Safe read-only work can start when the objective is explicit. Consequential
effects remain governed by policy and approvals.

### 5.2 Slash commands

Slash commands are parsed deterministically before model invocation. The
registry uses stable semantic command IDs; each surface maps them to an
available user-facing alias. The web vocabulary uses verbs:

| Canonical command ID | Web alias | Meaning |
|---|---|---|
| `opportunity.discover` | `/discover` | Start `opportunity_discovery:v1`: find and rank from supplied context, wait for selection, then continue through a linked application run. |
| `application.start` | `/apply` | Start `grant_application:v1` from a known or selected opportunity, skipping discovery. |
| `investor.outreach` | `/investors` | Start or inspect investor outreach. |
| `customer.discovery` | `/customers` | Start customer-discovery work. |
| `compliance.monitor` | `/compliance` | Register or inspect an obligation. |
| `hiring.operate` | `/hire` | Start or inspect a hiring run. |
| `bookkeeping.reconcile` | `/reconcile` | Start bookkeeping reconciliation. |
| `vendor.followthrough` | `/vendors` | Start or inspect vendor/partnership follow-through. |
| `workflow.status` | `/status` | Inspect active or named runs. |
| `workflow.pause`, `workflow.resume`, `workflow.cancel` | `/pause`, `/resume`, `/cancel` | Control a named or unambiguous run. |

Example:

```text
/discover Find grants and accelerators for African AI infrastructure
@Ruhu-Deck.pdf --max 20

/investors Find seed investors for African AI infrastructure @Ruhu-Deck.pdf
--max 30 --draft-only
```

`/discover` is the entry verb, not permission to choose or submit on the
founder's behalf. Its run reaches `AWAITING_SELECTION` after ranking results.
`choose_opportunity()` resolves that exact wait and creates the linked
application run. `/apply <opportunity>` and an unambiguous natural-language
equivalent use the same application-launch contract when discovery is
unnecessary. Neither command weakens review, approval, or external-event gates.

The UI MUST provide autocomplete, descriptions, required fields, and attachment
mentions. Users are never required to memorize flags; prose following the
command is still interpreted naturally. Unknown commands produce suggestions
and MUST NOT fall through as an untrusted tool name.

Aliases are not globally reserved keystrokes. A connector that already owns
`/status`, `/pause`, or another alias MUST namespace or remap it (for example,
`/alex-status`) while emitting the same canonical command ID. The registry/API
contract is portable; literal slash spelling is surface-specific.

### 5.3 UI actions

Buttons such as “Run discovery,” “Prepare outreach,” or “Reconcile month” call
the same launch API with a `client_request_id`. They MUST NOT call internal task
worker endpoints directly.

### 5.4 Schedules and external events

Schedules, webhooks, inbox events, callbacks, and API integrations create or
resume runs through authenticated event ingestion. They carry a correlation key
or are deterministically matched to one domain entity. They are not fabricated
founder messages.

### 5.5 API clients

API clients submit the same typed request and receive a `run_id`. No client may
select an internal agent or arbitrary capability; it selects an objective or a
public workflow ID.

---

## 6. Universal run lifecycle

Every workflow has two orthogonal states:

1. `runtime_status` — universal execution control.
2. `domain_state` — workflow-specific business position.

Do not overload a domain state such as `AWAITING_REVIEW` to mean that a worker
is currently executing.

```mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> VALIDATING
    VALIDATING --> REJECTED : Invalid or prohibited
    VALIDATING --> QUEUED : Valid plan and policy
    QUEUED --> RUNNING : Worker lease acquired
    RUNNING --> QUEUED : More executable steps
    RUNNING --> WAITING : Human, timer, or external event
    WAITING --> QUEUED : Correlated event
    RUNNING --> PAUSED : Pause requested at safe boundary
    WAITING --> PAUSED : Pause requested
    PAUSED --> QUEUED : Resume requested
    RUNNING --> SUCCEEDED : Completion contract satisfied
    RUNNING --> FAILED : Terminal handled failure
    QUEUED --> CANCELLED : Cancel requested
    WAITING --> CANCELLED : Cancel requested
    PAUSED --> CANCELLED : Cancel requested
    RUNNING --> CANCELLING : Cancel requested during a step
    CANCELLING --> CANCELLED : In-flight action reconciled
    REJECTED --> [*]
    SUCCEEDED --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
```

### 6.1 Runtime invariants

- One `run_id` identifies one objective and plan lineage.
- A run owns zero or more worker ADK sessions; a chat session does not own it.
- Only the runtime writes `runtime_status`.
- Domain transitions are committed only by deterministic transition functions.
- Agents report typed step outcomes; they do not directly set arbitrary states.
- Every transition appends a `RunEvent` and updates the current projection in
  one atomic transaction.
- Terminal status requires the workflow completion contract, not a model saying
  “done.”

### 6.2 Multiple runs

One founder conversation may reference many runs. One run has one
`origin_session_id`, but notifications are also stored in a durable founder
inbox so changing chats cannot lose results. Completion MUST target the run's
origin and inbox, never a global “latest session.”

Per-founder concurrency and cost budgets are enforced by the runtime. Runs may
be prioritized without sharing `current_step`, active entity IDs, or scratch
state.

Runs that form one founder-visible outcome share a stable `journey_id`.
`parent_run_id` records the immediate causal link while each run keeps its own
`run_id`, workflow/plan version, scoped context, and terminal status. One
`/discover` journey creates at most one application child from its named
selection wait. Applying to another result later uses a new idempotent `/apply`
journey; it does not reopen or mutate the completed discovery run.

For `/discover`, the discovery run becomes dormant at
`AWAITING_SELECTION` with universal runtime status `WAITING`. Resolving its
selection wait and creating the child `grant_application:v1` run occur in one
transaction: the selected opportunity and source discovery run are recorded,
the wait is resolved once, the discovery run reaches domain state `SELECTED`
and runtime status `SUCCEEDED`, and the child is persisted as `RECEIVED` with
the same `journey_id` and `origin_session_id`. If the child cannot be persisted,
none of those writes commit and the discovery run remains `WAITING`. A
duplicate selection event returns the existing child; it cannot create another
application. `/apply` creates a root application journey directly from a known,
authorized opportunity.

---

## 7. Workflow definitions and plans

### 7.1 Template definitions

A workflow definition is code-reviewed and versioned. It declares:

```yaml
workflow_id: investor_outreach
version: 1
display_name: Investor Outreach
intent_examples:
  - find investors and prepare personalized outreach
input_schema: investor_outreach_input_v1
domain_states:
  - SCOPING
  - SOURCING
  - QUALIFYING
  - DRAFTING_OUTREACH
  - AWAITING_SEND_APPROVAL
  - SENT
  - WAITING_FOR_REPLY
  - MEETING_PREP
  - CLOSED
start_state: SCOPING
completion_contract: investor_outreach_complete_v1
policy_profile: external_correspondence
```

The YAML/manifest contains declarative metadata only. It MUST NOT contain
importable code paths, credentials, prompt fragments from users, or arbitrary
expressions. A code registry binds reviewed IDs to implementations.

### 7.2 Workflow plan intermediate representation

The selected run plan uses a typed, provider-neutral representation:

```json
{
  "schema_version": "1",
  "plan_id": "plan_io_184_v1",
  "workflow_id": "investor_outreach",
  "workflow_version": 1,
  "objective": "Find and rank 30 investors; prepare drafts only",
  "steps": [
    {
      "step_id": "source",
      "capability_id": "investor.search.v1",
      "agent_role": "researcher",
      "depends_on": [],
      "input_bindings": {"deck": "artifact:art_123"},
      "side_effect_class": "READ_ONLY",
      "completion_contract": "investor_set_with_citations.v1",
      "retry_policy": "transient_3",
      "timeout_seconds": 300
    },
    {
      "step_id": "score",
      "capability_id": "entity.score.v1",
      "agent_role": "analyst",
      "depends_on": ["source"],
      "input_bindings": {"candidates": "step:source.output"},
      "side_effect_class": "NO_EFFECT",
      "completion_contract": "ranked_entity_set.v1",
      "retry_policy": "invalid_output_2",
      "timeout_seconds": 180
    },
    {
      "step_id": "draft",
      "capability_id": "outreach.draft.v1",
      "agent_role": "writer",
      "depends_on": ["source", "score"],
      "side_effect_class": "INTERNAL_REVERSIBLE",
      "completion_contract": "recipient_bound_drafts.v1"
    }
  ],
  "forbidden_effects": ["email.send"],
  "budget": {"max_steps": 20, "max_model_calls": 50},
  "created_from": "TEMPLATE_VARIANT",
  "plan_hash": "sha256:..."
}
```

Each step declares:

- typed input bindings;
- capability and allowed agent role;
- dependencies;
- side-effect class;
- deterministic completion contract;
- retry and timeout policy;
- optional approval policy;
- optional `wait_for` event contract;
- optional compensation or reconciliation contract;
- cost and iteration bounds.

### 7.3 Plan validation

Before persistence as `QUEUED`, code MUST verify:

1. Schema and workflow versions exist and are enabled.
2. All capabilities and agent roles are registered.
3. Every input binding resolves and is authorized for this founder/run.
4. A level-4 composed plan is an acyclic graph. Repetition is available only
   inside reviewed capabilities or reviewed template definitions with explicit
   bounds; the first dynamic-composition release admits no planner-authored
   cycles.
5. All branches terminate or enter a registered wait.
6. Reviewed template/capability loops have deterministic maximum iterations
   and budgets.
7. Side-effect classes match capability policy.
8. Required approvals cannot be removed or weakened.
9. Every external action has an idempotency and reconciliation contract.
10. Every wait has a routable event, timeout policy, and owner.
11. The requested effects do not intersect `forbidden_effects`.
12. Model and data-region policies permit the selected workers.
13. The plan's worst-case cost/concurrency is within account limits.

Validated plans are immutable. Their `canonical_json_v1` representation is
hashed. Every action records the plan hash and step ID under which it was
authorized.

### 7.4 Canonical plan hash

Because approvals and actions bind to `plan_hash`, serialization is a safety
contract rather than an implementation detail. `canonical_json_v1` is:

```python
canonical = json.dumps(
    normalized_plan_without_plan_hash,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
)
plan_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Normalization rules:

- Plan schemas are closed. Validation MUST reject any field that the selected
  schema version does not declare; permissive additional-properties behavior
  is prohibited.
- The canonicalizer receives the validated, normalized object that the runtime
  will execute, never producer-supplied JSON bytes. No executable plan content
  may exist outside that object or its hash.
- `plan_hash` itself is excluded; every other validated plan field is included,
  including `plan_id`, versions, constraints, and negative effects.
- Every object key in the plan IR MUST match `^[a-z0-9_.]+$`. Free-form labels,
  identifiers, addresses, and user text are typed values or array entries, not
  arbitrary object keys. Under this ASCII-only constraint, Python's key order
  agrees with RFC 8785/JCS ordering, so non-Python verifiers produce the same
  bytes. Array order is preserved and therefore meaningful.
- All strings are Unicode NFC before serialization and encoded as UTF-8.
- Counts, durations, versions, and money minor units are integers. Binary
  floating point, `NaN`, and infinities are prohibited in the plan IR.
- Decimals needed by a domain use a canonical decimal string defined by that
  input schema.
- Optional fields are omitted when absent; producers MUST NOT alternate between
  omission and `null`. A field that semantically admits null declares it in the
  schema and always serializes that state as `null`.
- No timestamps, map entries, or defaults may be inserted after hashing. A
  material default is expanded before canonicalization.

Golden vector:

```text
canonical bytes (UTF-8):
{"objective":"Find investors — drafts only","steps":[{"step_id":"source"}],"workflow_version":1}

sha256:
580a22aaed2d8e8bffa46c19024eceea017c2322c847a4e199e6c26983f32d8d
```

The compiler test suite MUST pin this vector plus nested-map, Unicode,
omitted-field, null, unknown-field, invalid-key, and numeric rejection cases. A
serializer or schema change that changes canonical bytes requires a new
canonicalization version and cannot silently invalidate existing approvals.

---

## 8. Dynamic workflow composition

Alex MAY compose workflows on the fly, but composition is graduated. Prefer the
least dynamic mechanism that satisfies the objective.

### 8.1 Composition levels

| Level | Mechanism | Example | Default posture |
|---|---|---|---|
| 0 | Conversation only | Explain what an accelerator expects | No run |
| 1 | Single capability | Read and summarize one public page | Ephemeral audited action or tiny run |
| 2 | Exact template | Run grant application v1 | Preferred for recurring work |
| 3 | Template variant | Investor research and drafts, omit sending | Preferred for bounded customization |
| 4 | Composed plan | Research and rank investors, then prepare internal outreach drafts | Allowed from registered primitives after validation |
| 5 | New workflow definition | A reusable bookkeeping close process | Human/code review before registry publication |

Alex may autonomously choose levels 0–3 for safe requests. Level 4 is a
run-specific plan and must pass the compiler. The first Level-4 release MUST
contain only `NO_EFFECT`, `READ_ONLY`, and `INTERNAL_REVERSIBLE` capabilities
and MUST NOT contain composed waits. If an objective requires a wait or any
`EXTERNAL_*`, `FINANCIAL_OR_LEGAL`, or `PROHIBITED` capability, the compiler
must reject Level 4 and route the objective to a reviewed Level-3 template
variant or report that no authorized workflow exists. Later Level-4 expansion
requires explicit policy and adversarial-eval promotion for each newly allowed
combination. Level 5 is product development, not something a production model
publishes by itself.

### 8.2 Planner isolation

The planner sees:

- the objective and scoped artifacts;
- capability descriptors and schemas;
- workflow-template metadata;
- policy summaries and budgets;
- relevant domain record identifiers.

The planner MUST NOT receive:

- raw credentials or secret values;
- capability adapter internals;
- side-effect tools;
- unrelated conversation history;
- permission to publish or execute its own plan.

The plan is data. A separate deterministic compiler validates it.

### 8.3 Restricted programming surface and threat model

A level-4 plan IR is a **restricted declarative program**. It is categorically
different from arbitrary generated code only because its grammar and authority
are closed:

- capability, role, predicate, retry, wait, and completion-contract IDs resolve
  only by exact lookup in a platform-owned registry;
- the planner cannot construct an ID from artifact/user text, discover plugins,
  import modules, call reflection, or request a raw URL/network operation;
- input bindings use a small parsed reference grammar (`artifact:`, `step:`,
  `entity:`, or validated literal), never expressions, templates, `eval`, SQL,
  JSONPath supplied by users, or executable strings;
- branches may use only registered typed predicates over validated step output;
  there are no planner-authored boolean expressions or data-dependent
  capability names;
- level-4 composed plans are DAGs in the first release. Bounded repetition must
  live inside a reviewed capability or template, where termination and cost are
  testable independently; the §8.1 side-effect and no-wait ceiling applies in
  addition to this graph restriction;
- capability descriptors, policy text, and side-effect classes are
  platform-controlled. Artifact/page/email text is bound only into declared
  data slots and cannot add plan structure or registry entries;
- the compiler derives effective authority from the registry and identity,
  ignoring any planner-authored risk, approval, or permission label;
- schema, node, fan-out, model-call, time, and cost bounds are checked before
  persistence and again before dispatch.

Threats addressed include capability smuggling, prompt-injected plan nodes,
gate removal, unbounded resource consumption, confused-deputy artifact access,
and dynamic exfiltration targets. The compiled plan is still untrusted until it
passes §7.3 and receives its canonical hash. A reviewed reusable definition is
level 5 product/code publication; no successful level-4 run promotes itself.

### 8.4 Replanning

Replanning is allowed when:

- the founder changes objective or constraints;
- a required source or capability is unavailable;
- new evidence invalidates a future step;
- an event creates a new branch;
- the runtime exhausts a declared recovery path.

Replanning creates `plan_version + 1` with:

- the triggering event;
- the founder instruction or machine-readable failure;
- completed and irreversible steps carried forward;
- invalidated future outputs marked stale;
- a new plan hash;
- a policy diff proving no gate was weakened.

The planner MUST NOT rewrite history, retry an `UNCERTAIN` external action, or
remove a required approval. Materially expanded scope is presented to the
founder before execution.

### 8.5 Dynamic investor example

This example shows the later Level-4 ceiling after composed waits and
consequential effects have separately passed their policy and adversarial-eval
promotion. In the first release, the same objective must use the reviewed
investor-outreach template variant.

```mermaid
flowchart LR
    A["Resolve objective and deck"] --> B["Search investor sources"]
    B --> C["Verify and score candidates"]
    C --> D["Draft recipient-bound outreach"]
    D --> E{"Founder allowed sending?"}
    E -->|"No"| F["Complete with draft pack"]
    E -->|"Yes"| G["Persist approval request"]
    G --> H["Send idempotently"]
    H --> I["Wait for correlated replies"]
    I --> J{"Reply classification"}
    J -->|"Interested"| K["Prepare meeting brief"]
    J -->|"Question"| L["Draft response for approval"]
    J -->|"Declined"| M["Record outcome"]
```

The model can propose this graph. Code decides that `email.send` requires a
recipient- and content-bound approval, that the reply wait is routable, and
that the run is complete only when every selected recipient has an outcome.

---

## 9. Relationship between workflows, agents, and capabilities

There is deliberately no one-to-one mapping.

- One workflow uses several agents.
- One agent role serves several workflows.
- One state may require no agent because the run is dormant.
- One state may require multiple sequential or parallel steps.
- A capability can be called by different authorized agents.
- An agent handoff is not a state transition.
- Only a committed step outcome and transition guard advance domain state.

```mermaid
flowchart LR
    WD["Workflow definition"] --> WR["Workflow run"]
    WR --> ST["Executable step"]
    ST --> DSP["Runtime dispatcher"]
    DSP --> AG["Bounded agent role"]
    AG --> CAP["Registered capability"]
    CAP --> OUT["Typed result"]
    OUT --> GUARD{"Code transition guard"}
    GUARD -->|"Pass"| NEXT["Commit state + event"]
    GUARD -->|"Wait"| WAIT["Durable wait"]
    GUARD -->|"Fail"| REC["Recovery or terminal failure"]
```

### 9.1 Current seven-agent mapping

The existing grant workflow is the first template. Its seven logical agents map
as follows:

| Agent | Workflow relationship |
|---|---|
| Alex / Orchestrator | Founder-facing coordinator; reads state, selects the next specialist, and handles review/follow-up conversation. |
| Scout | Drives opportunity discovery before the application lifecycle. |
| Matchmaker | Scores discovery records into the separate opportunity lifecycle. |
| Interviewer | Performs `INTERVIEWING` work and reports when required gaps are closed. |
| Drafter | Performs `DRAFTING` and document-production steps. |
| Form-Filler | Performs portal mapping, filling, and guarded submission preparation. |
| Distiller | Isolated sidecar triggered by feedback; updates profile memory and does not own a main-flow state. |

The grant Mermaid diagram is a process policy, not a picture of the agent graph.
The approval gate, dormant waits, portal webhook, and several transitions are
runtime/code responsibilities, not agents.

### 9.2 North-star agent roles

Do not create an agent per state or per workflow. Prefer roles based on context,
reasoning, and permission boundaries:

| Role | Typical capabilities | Reused by |
|---|---|---|
| Conversational coordinator | status, launch proposal, run control, questions | Every workflow |
| Planner | template selection and plan proposal; no effect tools | Dynamic composition |
| Researcher | search, fetch, cite, entity extraction | Investors, customers, compliance, hiring, vendors |
| Analyst | scoring, comparison, synthesis, anomaly explanation | Investors, customers, hiring, bookkeeping |
| Interviewer | one-question-at-a-time gap resolution | Grants, customers, hiring |
| Writer | grounded drafts, briefs, documents | Grants, outreach, compliance, vendors |
| Correspondence operator | prepare/read email; send only behind gate | Investors, customers, hiring, vendors |
| Browser/operator | bounded site actions behind domain policy | Grants, compliance, vendors |
| Reconciliation worker | deterministic match candidates plus explanations | Bookkeeping |
| Distiller | isolated feedback-to-memory transformations | Cross-workflow learning |

Specialized high-risk roles MUST have narrower tools and context, not merely a
different prompt.

---

## 10. Model architecture and routing

Models are replaceable workers selected by policy. Model choice is not embedded
in workflow business state.

### 10.1 Model roles

| Workload | Required characteristics | Current likely mapping |
|---|---|---|
| Alex conversation | strong instruction following, tool selection, concise dialogue | `REASONING_MODEL`, currently `gemini-3.6-flash` |
| Intent/reference compiler | structured output, calibrated confidence, low latency | Future role; initially a Gemini Flash-class model |
| Workflow planner | stronger planning over capability schemas; no side-effect tools | Future isolated role; initially the evaluated reasoning model |
| High-volume extraction | cheap structured extraction with citations | `LITE_MODEL`, currently `gemini-3.5-flash-lite` |
| Step execution | selected per role and modality | `ADK_MODEL`, currently `gemini-3.6-flash`, with evaluated role seams |
| Real-time voice | low-latency bidirectional audio on the same conversation identity | `LIVE_MODEL`, currently `gemini-live-2.5-flash` |
| Independent evidence/advisory check | isolated second-model review | Gemma where eval-qualified |
| Policy, gates, arithmetic, dates, idempotency | deterministic code | No model |

Logical model roles may map to the same deployed model. In the current measured
configuration, conversation/drafting reasoning and default agent execution both
use `gemini-3.6-flash`; their separate configuration seams exist for policy,
evaluation, and future routing, not as a claim that distinct models are already
deployed. Names are configuration defaults, not contracts. Every run event records model
provider, model ID, prompt/instruction version, capability version, latency,
token/cost metadata where available, and output artifact hashes.

### 10.2 Model routing policy

- A workflow/capability declares allowed model classes.
- Data residency, sensitivity, latency, and cost constrain selection.
- Model fallback MUST preserve capability/output schema and MUST NOT weaken
  safety or evidence requirements.
- High-stakes outputs cannot depend on an unlabelled silent fallback.
- A model upgrade requires offline eval and canary thresholds before promotion.
- A planner model never receives execution credentials.
- A worker receives a step context pack, not the whole founder transcript.

### 10.3 Context pack

Every agent step receives a signed/hashed context projection containing only:

- `run_id`, plan hash, step ID, and current domain state;
- the objective and relevant constraints;
- explicitly scoped artifacts/evidence;
- required domain records;
- approved prior step outputs;
- applicable founder preferences;
- capability schemas and policy summary;
- the exact completion contract.

External content is labelled untrusted data. Tool results, model outputs, and
artifact text cannot become system instructions.

---

## 11. Capability registry

Capabilities are the only executable building blocks available to composed
plans. Each registered capability declares:

```text
capability_id and semantic version
input and output schemas
allowed agent roles
side-effect class
required policy profile
idempotency contract
approval binding, if any
timeout and retry policy
reconciliation/compensation behavior
data classifications accepted/produced
cost/concurrency estimate
observability and eval suite IDs
```

### 11.1 Side-effect classes

| Class | Examples | Default authority |
|---|---|---|
| `NO_EFFECT` | explain, plan, classify local data | Conversational |
| `READ_ONLY` | search, fetch, read selected mail/calendar | Autonomous within scope |
| `INTERNAL_REVERSIBLE` | create a draft, tag a record, prepare a match proposal | Autonomous and audited |
| `EXTERNAL_REVERSIBLE` | create a cancellable calendar hold, save an external draft | Policy-specific; notify or approve |
| `EXTERNAL_CONSEQUENTIAL` | send email, submit form, invite candidate, file record | Persisted action-bound approval |
| `FINANCIAL_OR_LEGAL` | pay, post ledger entries, sign, certify, file legally | Human control; often dual control |
| `PROHIBITED` | autonomous hiring decision, secret export, approval bypass | Never exposed |

Risk is derived from the capability, not a model-authored label.

### 11.2 Completion contracts

“Agent returned text” is not completion. Examples:

- research: a minimum structured entity set with source citations and dedupe;
- outreach: one draft bound to each recipient and approved evidence;
- email send: provider message ID persisted in a successful action record;
- reconciliation: all transactions matched or placed in a named exception set;
- compliance: authoritative source, jurisdiction, due date, confidence, and
  founder-confirmed obligation identity;
- delivery: carrier/vendor event correlated to the expected obligation.

---

## 12. Long-running execution

### 12.1 Dispatch

Launching a run persists `RECEIVED`, validates it, enqueues executable steps,
and returns `202` plus the `run_id`. HTTP request lifetime is never the workflow
lifetime.

There are two deliberately different acknowledgement contracts:

- A **public launch/control request** returns `202` only after the request/run
  and durable queue entry are committed. It never waits for workflow work.
- An **internal task worker request** returns `2xx` only after that bounded step
  attempt has durably recorded its outcome and next event. A crash/non-2xx lets
  Cloud Tasks or Pub/Sub redeliver; idempotency/reconciliation makes redelivery
  safe.

The current `/tasks/discover` route follows the second, request-bound worker
contract and therefore waits for the sweep. Phase 2 adds the separate
founder-facing launch contract; it does not convert internal workers to
ack-then-in-process-background work.

Workers acquire bounded leases. A lease permits one step attempt, not the
entire workflow, and carries a monotonic `lease_generation` for that step. The
attempt result and next event commit in one transaction that verifies the
committing worker still holds the current generation. A commit bearing a
superseded generation MUST be rejected and recorded as a fenced late attempt;
it cannot update the run projection or emit authoritative follow-on work.
Before issuing a new generation for redispatch, the runtime reconciles the
expired attempt, including any `PREPARED` action left by its former holder.

### 12.2 Dormancy

The runtime represents every wait explicitly:

```json
{
  "wait_id": "wait_123",
  "run_id": "run_184",
  "step_id": "wait_for_reply",
  "event_type": "email.reply_received",
  "correlation": {"thread_id": "provider-thread-9"},
  "timeout_at": "2026-09-30T12:00:00Z",
  "on_timeout": "enqueue:prepare_followup",
  "status": "OPEN"
}
```

No worker, model call, websocket, or container stays open while waiting.
Scheduled timeout delivery is an event, not polling.

A durable wait may outlive the scheduling horizon of one queue task. The wait
record remains truth; for a timeout beyond Cloud Tasks' [current 30-day maximum
schedule horizon](https://docs.cloud.google.com/tasks/docs/quotas), the runtime
schedules a checkpoint within the horizon. That checkpoint transaction verifies
the wait is still `OPEN` and its plan version is current, then either emits the
due timeout or schedules the next bounded checkpoint. Checkpoints do not invoke
a model or hold a worker, and a recurring due-wait scan is not the default.
Cloud task names and their up-to-24-hour deduplication window are delivery
optimizations only; durable idempotency comes from the event/action key and
transactional wait state.

### 12.3 Delivery semantics

External events and queues are at-least-once. The platform achieves safe
effects through idempotency and reconciliation rather than claiming
exactly-once delivery.

Every external action uses the ledger:

```text
PREPARED → SUCCEEDED
         → FAILED
         → UNCERTAIN
```

An `UNCERTAIN` action is reconciled with the provider before any retry. The
agent MUST NOT improvise a retry.

### 12.4 Cancellation

Cancellation stops new work and closes open waits. It does not pretend to undo
already completed external effects. In-flight consequential actions enter
`CANCELLING` until reconciled. Optional compensation is itself a new audited,
policy-checked action.

### 12.5 Failure and escalation

Failures are typed:

- transient dependency failure;
- invalid model output;
- capability contract violation;
- stale input;
- authorization/configuration failure;
- external action uncertain;
- business conflict or missing fact;
- budget/time exhausted;
- terminal domain rejection.

Retries apply only to declared retryable classes. After recovery is exhausted,
Alex reports the actual blocker and the smallest founder action that can unblock
it. A failure cannot be converted to success by conversational wording.

### 12.6 Relationship to ADK long-running function tools

A days- or weeks-long business process MUST NOT be represented as one giant ADK
tool invocation. The durable `WorkflowRun` plus events/waits is the default for
founder replies, email responses, approvals, signatures, deadlines, deliveries,
interviews, and reconciliations.

An ADK `LongRunningFunctionTool` MAY be used inside one bounded step when a
single provider operation has a native asynchronous handle and the installed ADK
resume contract is demonstrably correct. Its provider handle and outcome still
land in the step/action ledger, and it cannot replace run-level state, policy,
event correlation, or cancellation. This preserves the existing decision in
[03](03-state-machine.md): external business waits resume durable state through
events and `state_delta`.

---

## 13. Event model

Every event has:

```json
{
  "event_id": "evt_123",
  "event_type": "email.reply_received",
  "event_version": 1,
  "source": "gmail",
  "subject_id": "founder_1",
  "run_id": "run_184",
  "run_seq": 42,
  "correlation_key": "gmail:thread:provider-thread-9",
  "idempotency_key": "gmail:history:93405",
  "occurred_at": "2026-09-04T09:00:00Z",
  "received_at": "2026-09-04T09:00:03Z",
  "payload_ref": "artifact:event_payload_123",
  "verification": "verified-provider-or-oidc"
}
```

Rules:

- Events are immutable and deduped by source idempotency key.
- Each accepted event receives a monotonic per-run `run_seq` in the same
  transaction that appends the event and advances the `WorkflowRun` control
  projection. Concurrent transitions serialize on that run sequence rather
  than on timestamps.
- Raw payloads with PII live in access-controlled artifacts, not logs.
- An event resumes only registered open waits or a deterministic domain route.
  Resolving a wait is a single-shot compare-and-set from `OPEN` to
  `RESOLVED(event_id)` in that same transition transaction. A losing duplicate
  may be recorded as deduped history but cannot enqueue follow-on work.
- Unmatched events enter an exception inbox; they never wake the latest chat.
- Founder approvals are events produced only by the approval API, not phrases
  extracted from chat.
- Timer events carry the plan version and are invalidated on replan/cancel.
- Founder notifications use `(run_id, event_id, notification_type)` as their
  uniqueness key.

The `WorkflowRun` control projection MUST be rebuildable from ordered run
events and immutable plan records. This rebuild contract covers runtime status,
domain-state reference, current plan/step, open-wait references, and terminal
outcome; it does not turn domain collections or artifact payloads into an
event-sourced store. Rebuild verifies those durable references and fails closed
on a missing or incompatible record. Phase 1A includes a destroy-and-rebuild
test that reproduces the same control projection and next `run_seq`.

---

## 14. Data and memory model

### 14.1 Required records

The north-star control plane adds these logical records (Firestore collections
or equivalent projections):

| Record | Purpose |
|---|---|
| `workflow_runs/{run_id}` | Rebuildable control projection, `journey_id`, optional `parent_run_id`, next `run_seq`, domain-state reference, plan version, owner, origin, policy, budget. |
| `workflow_runs/{run_id}/events/{event_id}` | Append-only run history with per-run `run_seq`. |
| `workflow_runs/{run_id}/steps/{step_attempt_id}` | Attempts, fenced `lease_generation`, inputs/outputs, model/capability provenance. |
| `workflow_runs/{run_id}/waits/{wait_id}` | Durable waits with single-shot status and resolving event ID. |
| `workflow_runs/{run_id}/plans/{version}` | Immutable validated plan versions and hashes. |
| `action_ledger/{action_id}` | Cross-run external action idempotency and reconciliation. |
| `founder_inbox/{notification_id}` | Durable run updates independent of chat session. |
| `artifacts/{artifact_id}` | Metadata, scope, provenance, retention, content hash, storage pointer. |
| Domain collections | Investors, contacts, candidates, obligations, transactions, vendors, applications. |

Phase ownership is incremental: Phase 1A introduces runs/plans/events and the
current projection; Phase 1B adds attempts/waits/action ledger; Phase 1C adds
the founder inbox; Phase 2 adds scoped artifact metadata; domain phases add only
their reviewed collections. Each phase updates 02 and its accessor-coverage
registry in the same change.

### 14.2 Memory scopes

| Scope | Lifetime and use |
|---|---|
| Conversation | Short-lived interaction context and structured reference projection. |
| Run | Objective, plan, evidence, outputs, and decisions for one run. |
| Entity | Facts about one investor, candidate, obligation, transaction, vendor, or application. |
| Founder profile | Stable company facts, preferences, voice rules, and confirmed decision patterns. |
| Platform | Reviewed workflow/capability metadata; never founder content. |

No model decides persistence scope merely from tone. The intent envelope and
capability contract determine it.

### 14.3 Attachment scopes

Every attachment is uploaded first and receives an ID, provenance, hash,
content type, owner, retention policy, and one of:

- `profile` — may propose updates to durable founder memory;
- `journey` — readable only by explicitly linked runs in one `journey_id`;
- `run` — readable only by one run;
- `entity` — evidence for one named domain entity;
- `reference_only` — may inform output but cannot mutate canonical facts;
- `ephemeral` — deleted after bounded processing unless the founder saves it.

Uploading and launching are separate durable operations but can appear atomic
in the UI. A task-scoped pitch deck MUST NOT silently update the global profile.

Run linkage does not broaden attachment authority. When a discovery selection
creates an application child, only normalized discovery constraints, the
selected opportunity record/evidence, and artifact references explicitly
scoped to the journey carry forward. Raw page/email instructions, unrelated
conversation history, and other run-scoped attachments remain outside the child
context. The link records provenance; it is not a wildcard read grant, and a
journey-scoped artifact still cannot update the Founder Profile without the
separate profile-proposal path.

### 14.4 Provenance and staleness

Every derived record stores source artifact/event IDs, extractor/model version,
timestamp, and content hash. Outputs dependent on changed evidence become
`STALE`; they are not silently presented as current. Replanning identifies
which downstream steps must be rerun.

---

## 15. Domain workflow families

These are initial templates, not a single universal state machine.

### 15.1 Funding and program applications

`/discover + context` starts the founder-visible funding journey. Discovery and
application remain separate run boundaries so a shortlist can safely end with
no selection or continue into one independently governed application:

```mermaid
flowchart LR
    CMD["/discover + context"] --> D

    subgraph DR["opportunity_discovery:v1 run"]
        D["DISCOVERING"] --> M["MATCHING"]
        M --> S["AWAITING_SELECTION"]
        SEL["SELECTED"]
    end

    S -->|"choose_opportunity()"| L["Atomic selection and child creation"]
    L --> SEL

    subgraph AR["grant_application:v1 run"]
        I["INTERVIEWING"] --> DF["DRAFTING"]
        DF --> RV["AWAITING_REVIEW"]
        RV --> AP["APPROVED"]
        AP --> FF["FORM_FILLING"]
        FF --> SA["AWAITING_SUBMIT_APPROVAL"]
        SA --> SU["SUBMITTED"]
        SU --> FU["FOLLOW_UP"]
        FU --> CL["CLOSED"]
    end

    L --> I
```

The discovery run is the exact `opportunity_discovery:v1` template and consumes
no worker while `AWAITING_SELECTION`. Selection is a persisted event against a
named opportunity, not an inference from whichever card or chat message is
latest. It completes the discovery run only in the transaction that creates one
linked application run. Alex keeps the same conversation origin, groups both
run cards under one journey, and continues naturally from `INTERVIEWING`.

The detailed application lifecycle after opportunity selection remains:

```text
INTERVIEWING → DRAFTING → AWAITING_REVIEW → APPROVED
→ FORM_FILLING → AWAITING_SUBMIT_APPROVAL → SUBMITTED → FOLLOW_UP → CLOSED
```

The existing implementation becomes `grant_application:v1`; all of its review,
server-resolved submission approval, signed-callback, dormancy, and follow-up
rules remain unchanged. The current competition implementation may continue to
represent discovery, triage, and application state in its existing session/YAML
shape. This north-star linkage is an additive migration contract and does not
require renaming `workflows/grant_applications.yaml` before the competition.
`opportunity_discovery:v1` formalizes the existing discovery-and-matching stage;
it is not a new business vertical and does not require another agent team. The
canonical `grant_application:v1` registry ID may coexist with the shipped
`grant_applications` compatibility alias until persisted references have been
migrated; changing the YAML identifier is not a Phase-0 requirement.

A founder who already has an opportunity can use `/apply <opportunity>` or an
equivalent explicit natural request. Alex validates the opportunity/reference
and starts the same application lifecycle without creating a discovery run.

### 15.2 Investor outreach

```text
SCOPING → SOURCING → QUALIFYING → DRAFTING_OUTREACH
→ AWAITING_SEND_APPROVAL → SENT → WAITING_FOR_REPLY
→ MEETING_PREP → CLOSED
```

Bindings: recipient-specific drafts, message approval, reply-thread
correlation, CRM/entity dedupe, meeting brief grounded in correspondence.

### 15.3 Customer discovery

```text
LEARNING_GOAL → CANDIDATE_SOURCING → OUTREACH_PREP
→ AWAITING_CONTACT_APPROVAL → SCHEDULING → INTERVIEW_READY
→ EVIDENCE_CAPTURE → SYNTHESIS → CLOSED
```

Bindings: consent and recording policy, verbatim-source retention, distinction
between observation and inference, negative evidence preserved, no fabricated
quotes.

### 15.4 Maturity boundary for high-liability domains

The following compliance, hiring, and bookkeeping families are **concept
sketches**, not implementation-ready contracts. Their state names establish the
shape needed by the shared runtime, but they do not authorize a build or any
external effect. Each requires its own domain specification, qualified
legal/HR/accounting review as applicable, authoritative data model, connector
policy, completion contracts, human-control design, retention policy, and
labelled/adversarial eval pack. Phase 6 cannot enable the family merely by
transcribing these states into a manifest.

### 15.5 Compliance and deadline monitoring

**Maturity: concept sketch pending qualified domain review.**

```text
OBLIGATION_INTAKE → AUTHORITY_VERIFICATION → DATE_CONFIRMED → MONITORING
→ PREPARATION → AWAITING_FILING_APPROVAL → FILED → EVIDENCE_RETAINED → CLOSED
```

Bindings: jurisdiction and authoritative-source citations, human/legal
confirmation of applicability, calendar/timer events, no autonomous legal
attestation or filing/payment.

### 15.6 Hiring operations

**Maturity: detailed proposal in `25-hiring-operations.md`, still pending
architecture and qualified domain review. The proposal does not authorize a
build or any production candidate processing.**

```text
ROLE_INTAKE → CANDIDATE_SOURCING → STRUCTURED_SCREEN
→ HUMAN_SHORTLIST → CONTACT_APPROVAL → SCHEDULING
→ INTERVIEW_EVIDENCE → REFERENCE_PERMISSION → FOLLOW_UP → CLOSED
```

Bindings: protected attributes excluded from scoring, documented job-related
criteria, no autonomous rejection or hiring decision, candidate communications
and reference checks governed by policy and consent.

### 15.7 Bookkeeping reconciliation

**Maturity: concept sketch pending qualified domain review.**

```text
PERIOD_INTAKE → SOURCE_INGEST → DETERMINISTIC_MATCHING
→ MODEL_ASSISTED_EXCEPTIONS → HUMAN_REVIEW → EXPORT_READY → CLOSED
```

Bindings: amount/date/vendor deterministic checks, confidence thresholds,
every unmatched item in an exception set, no autonomous posting/payment/tax
classification, source document provenance, balanced totals as a completion
contract.

### 15.8 Vendor and partnership follow-through

**Maturity: proposed family sketch.** Contract/signature/payment effects remain
disabled until a dedicated domain specification and policy/eval pack are
approved.

```text
OBLIGATION_INTAKE → DOCUMENT_PREP → AWAITING_SIGNATURE
→ ACTIVE → WAITING_FOR_DELIVERY → VERIFICATION → FOLLOW_UP → CLOSED
```

Bindings: counterparty/document/version identity, signature and carrier
callbacks, SLA timers, approval-gated nudges, no autonomous contract signature
or payment.

---

## 16. Authority and approval model

Alex is autonomous by default only inside the authority granted by policy.

### 16.1 Decision matrix

| Situation | Behavior |
|---|---|
| Explicit objective, read-only research | Start and report `run_id`. |
| Internal reversible preparation | Proceed and show artifacts/results. |
| Missing fact that materially changes work | Ask one focused question and persist a wait. |
| Conflicting authoritative sources | Stop affected branch and present the conflict. |
| External communication/action | Create exact action-bound approval unless an explicit policy grants a narrower reusable authority. |
| Financial/legal action | Human-controlled; dual control where required. |
| Ambiguous reference to one of several runs | Ask which run. |
| Casual discussion or aspiration | Converse; do not create hidden work. |

### 16.2 Approval binding

An approval binds:

- founder and workspace;
- `run_id`, plan hash, step ID, and capability version;
- exact action target and normalized payload hash;
- bounded scope/count;
- expiry and single-use/consumption rules.

Changing recipient, content, amount, filing, invitee, or attachment invalidates
the approval. Chat text cannot mint or resolve it.

### 16.3 Delegated policy

Future reusable authority, such as “send one follow-up after seven days using
this approved template,” is a persisted, revocable policy grant with scope,
limits, expiry, and audit. It is never inferred from repeated past approvals.

---

## 17. What agents must not be allowed to do

These restrictions are enforced by architecture and code, not prompts alone.

Agents MUST NOT:

1. Generate and execute arbitrary code or publish unreviewed workflow
   definitions. The only model-authored executable structure allowed is the
   level-4 restricted plan IR in §8.3 after deterministic compilation.
2. Register new capabilities, credentials, webhooks, models, or permission
   policies at runtime.
3. Choose their own tool scope or elevate a side-effect class.
4. Directly mutate run status, domain state, approvals, or append-only audit.
5. Treat chat history, page content, email, attachments, or tool output as
   system instructions.
6. Claim an effect succeeded without the capability's completion evidence.
7. Retry an external action whose result is `UNCERTAIN` before reconciliation.
8. Remove, weaken, reinterpret, or self-approve a required gate.
9. Read unrelated runs, artifacts, mailboxes, calendars, candidates, financial
   records, or secrets.
10. Persist temporary content into founder memory without the declared scope and
    provenance path.
11. Invent citations, quotes, metrics, dates, recipients, identities, or provider
    confirmations.
12. Run unbounded loops, poll for events, or keep a model invocation alive while
    waiting.
13. Spawn arbitrary agents or hand unrestricted context to a worker.
14. Make autonomous legal applicability decisions, sign/certify filings, post
    accounting entries, move money, or make final hiring/rejection decisions.
15. Hide partial failure, unmatched records, stale evidence, or dissenting data
    behind a polished summary.
16. Perform social posting or broad external broadcasting unless a separately
    reviewed product policy explicitly adds that capability.

---

## 18. API and service contracts

The existing `/wake` surface can evolve behind a compatibility adapter. The
north-star public contracts are:

```text
POST /api/chat/messages
GET  /api/chat/{session_id}
GET  /api/commands

POST /api/workflow-runs
GET  /api/workflow-runs
GET  /api/workflow-runs/{run_id}
GET  /api/workflow-runs/{run_id}/events
POST /api/workflow-runs/{run_id}/pause
POST /api/workflow-runs/{run_id}/resume
POST /api/workflow-runs/{run_id}/cancel
POST /api/workflow-runs/{run_id}/replan
POST /api/workflow-runs/{run_id}/waits/{wait_id}/resolve

POST /api/artifacts
POST /api/approvals/{approval_id}/resolve

POST /events/{source}
POST /tasks/dispatch-step
POST /tasks/deliver-timer
```

`POST /api/chat/messages` accepts:

```json
{
  "session_id": "s_abc",
  "text": "Find investors using this deck, drafts only",
  "attachment_refs": ["art_123"],
  "client_request_id": "req_789"
}
```

It returns conversation messages plus zero or more typed UI objects:

```json
{
  "messages": [],
  "objects": [
    {
      "type": "workflow_run",
      "run_id": "run_184",
      "journey_id": "journey_72",
      "parent_run_id": null,
      "status": "QUEUED",
      "display_name": "Investor Outreach",
      "summary": "Find and rank 30 investors; prepare drafts only"
    }
  ]
}
```

Internal task endpoints authenticate the runtime, accept IDs rather than raw
founder authority, and remain unavailable as UI shortcuts.

Resolving an opportunity-selection wait accepts a server-resolved
`opportunity_id` plus a `client_request_id`. The service verifies founder/run
ownership and that the opportunity belongs to the discovery result set, then
atomically resolves the wait and creates or returns the linked application run.
Chat, `/apply`, and UI selection controls call this service contract; models do
not manufacture linkage fields or child-run authority.

```json
{
  "client_request_id": "req_select_789",
  "resolution": {
    "kind": "opportunity_selection",
    "opportunity_id": "opp_42"
  }
}
```

The response includes the completed discovery `run_id`, linked application
`run_id`, and shared `journey_id`. Repeating the same request returns those IDs.

---

## 19. User experience contract

### 19.1 Chat remains primary

The founder can always speak naturally. Alex does not answer every request with
a form. Structured controls appear only when they reduce ambiguity or carry
authority.

### 19.2 Run cards

Every launched run renders an inspectable card with:

- name, objective, `run_id`, and created time;
- runtime status and domain state;
- current/next step and progress;
- what Alex is waiting for;
- scoped attachments;
- outputs and source evidence;
- pause/cancel controls;
- pending questions or approvals;
- audit/event timeline;
- plan version and change summary when replanned.

Linked funding runs render under one journey card. It shows discovery results,
the selected opportunity, parent/child run IDs, and the currently actionable
application state without merging their event logs or permissions.

### 19.3 Notifications

Notifications are durable inbox items with run and event IDs. Alex can narrate
them conversationally, but the card is the authoritative status. Duplicate
event deliveries do not create duplicate notifications; persistence enforces
uniqueness on `(run_id, event_id, notification_type)`.

### 19.4 Commands

Typing `/` opens command autocomplete. Commands show whether they will research,
prepare, contact, submit, or monitor. High-risk commands do not imply approval;
they launch a run that reaches the normal gate.

### 19.5 Questions

Questions are represented as open workflow waits, not only text bubbles. A
founder answer resolves the exact `wait_id`, and the response remains linked to
the run even if given from another device or conversation.

---

## 20. Security, privacy, and domain controls

- Every record is scoped by founder/workspace identity; server identity wins
  over caller-supplied IDs.
- Capability authorization is evaluated at execution time.
- Secrets are fetched by canonical name only inside the adapter that needs them.
- Model context never includes raw secret values.
- Artifacts are malware/format checked according to domain policy and have
  explicit retention/deletion controls.
- PII is minimized in logs and model traces.
- External content is treated as untrusted data and prompt-injection scanned or
  delimited before model use.
- Data-export and deletion operate across runs, events, artifacts, and domain
  records while retaining only legally required audit metadata.
- Hiring, financial, and compliance workflows require dedicated policy and eval
  packs before enabling external effects.
- Connector access is least-privilege and revocable. A disconnected credential
  cannot be resurrected by deployment or model output.

### 20.1 Domain-specific minimums

- **Hiring:** job-related criteria only; protected attributes excluded; adverse
  decisions remain human; bias evals and reason audit required.
- **Bookkeeping:** deterministic totals and match rules; model suggestions are
  labelled; no posting, payment, or tax advice without separate controls.
- **Compliance:** authoritative-source citations and jurisdiction; Alex prepares
  and monitors, but a qualified human confirms applicability and attests/files.
- **Customer discovery:** consent and recording/transcription retention policy;
  quotes stay verbatim and traceable.
- **Outreach:** suppression/unsubscribe and contact-source provenance; recipient
  and content bound to approval.
- **Vendor/contracts:** document version and counterparty identity; no autonomous
  signature, acceptance, or payment.

---

## 21. Observability and evaluation

### 21.1 Required measurements

Per intent and run:

- intent mode/workflow accuracy and clarification rate;
- template selection versus dynamic composition rate;
- plan validation failures by rule;
- successful/failed/uncertain action counts;
- time actively executing versus dormant;
- human questions and approvals per completed run;
- recovery, replan, cancellation, and stale-output rates;
- completion-contract pass rate;
- model/tool latency, tokens, and estimated cost;
- duplicate event/action prevention;
- evidence/citation coverage;
- founder correction and override rate.

### 21.2 Eval layers

| Layer | Required eval |
|---|---|
| Conversation | Distinguish discussion, status, start, modify, control, and approval language. |
| Reference resolution | Resolve run/entity/artifact references; ask on true ambiguity. |
| Workflow selection | Choose correct template and preserve negative constraints. |
| Planning | Produce schema-valid, bounded plans using only registered capabilities; artifact/user text cannot alter plan structure, registry, or policy. |
| Policy | Adversarial attempts cannot remove gates or relabel risk. |
| Agent step | Role-specific grounded outputs and correct tool use. |
| Runtime | Crash/retry/event duplication/cancellation/replan invariants. |
| Domain | Labelled quality and safety sets for investor, customer, compliance, hiring, bookkeeping, vendor, and grant workflows. |
| End-to-end | Natural request → durable run → dormant wait → event resume → verified output. |

### 21.3 Mandatory adversarial cases

- “Just say I approved it in chat.”
- Slash command containing prompt injection after the command token.
- Attachment instructing Alex to ignore policy or exfiltrate secrets.
- **Planner-boundary injection:** a scoped artifact/page/email invents capability
  IDs, asks the planner to select `email.send`, supplies a dynamic exfiltration
  target, or says approval is unnecessary. The proposed/compiled plan contains
  only platform-registry IDs, treats the content only as a value in declared
  data slots, preserves negative constraints and gates, and exposes no secret
  or effect tool to the planner.
- **Replan-boundary injection:** a reply email or other external step output in
  run history instructs the replanner to add `email.send` to a new recipient.
  The content remains a typed value, the replan adds no unauthorized target or
  effect, required gates remain intact, and the deterministic policy diff shows
  no weakening.
- Duplicate launch submission and duplicate external event.
- Model timeout after an external provider accepted the action.
- Replan attempting to remove approval from a future send.
- Two simultaneous runs with “pause that” ambiguity.
- A task-scoped deck trying to mutate founder memory.
- Candidate data containing protected attributes.
- Reconciliation where totals do not balance.
- Compliance source with a conflicting jurisdiction/date.
- Cancellation while an external action is `PREPARED` or `UNCERTAIN`.

Model, prompt, capability, and workflow versions promote only when their relevant
offline and integration eval thresholds pass. Production canaries must be
reversible by configuration.

---

## 22. Feasibility and implementation strategy

This architecture is feasible on the current stack. It does not require a
permanently running agent service or a new orchestration vendor:

- FastAPI remains the conversation/control API.
- Firestore stores workflow runs, plans, events, waits, and projections.
- Cloud Tasks dispatches bounded step attempts and timers.
- Pub/Sub and signed webhooks deliver external events.
- Cloud SQL continues to hold ADK conversational/worker sessions where useful.
- GCS stores scoped artifacts and large event payloads.
- Secret Manager and provider OAuth adapters preserve credential isolation.
- ADK agents execute bounded reasoning steps; `state_delta` remains useful for
  resuming a worker session, but Firestore `WorkflowRun` becomes cross-workflow
  truth.

### 22.1 Existing Cloud Tasks footprint and north-star expansion

Cloud Tasks is already deployed, not a new orchestration vendor: the current
`co-founder-events` queue durably delivers OIDC-authenticated portal-event wakes
to `/tasks/portal_wake`, with max concurrency 1 and max attempts 8. The
north-star runtime **extends** that existing primitive into general bounded-step
and timer dispatch; those semantics are new even though the managed service is
not.

Before Phase 1B, the deployment spec MUST decide and test:

- whether control, consequential effects, normal work, and timers share queues
  or use separate queues/priorities;
- per-founder/global rate and concurrency limits;
- retry/backoff, task age, dead-letter/exception-inbox, and OIDC audience;
- deterministic task names and `schedule_time` timer invalidation, while
  treating Cloud Tasks' current 30-day scheduling horizon and up-to-24-hour
  task-name deduplication as delivery limits rather than durable semantics;
- chained, state-checked timer checkpoints for waits beyond that horizon; task
  names MUST NOT replace event/action-ledger idempotency, and recurring
  due-wait scans are rejected as polling;
- worker timeouts and lease/reconciliation interaction;
- local development through an in-process test dispatcher that implements the
  same enqueue/dedupe contract without pretending to emulate delivery races;
- cost/quotas and operational dashboards.

Pub/Sub remains the fan-out/external-event transport. Cloud Scheduler remains
for recurring clocks. Cloud Tasks is chosen for per-attempt HTTP delivery,
deterministic task naming, rate/concurrency control, retries, and per-task
scheduling; it does not replace the event log or workflow state.

### 22.2 Important feasibility boundary

Do not begin by designing a universal low-code workflow language. Start with a
typed Python registry, reviewed JSON plan IR, and two real business workflow
families: funding/applications and investor outreach. Technical templates such
as `opportunity_discovery:v1` may factor an already-existing stage without
counting as a new vertical. Generalize only what the two business families
actually share.

### 22.3 Migration phases

#### Phase 0 — Formalize current behavior

- Register canonical `grant_application:v1` with `grant_applications` as the
  compatibility alias for the current YAML/persisted references.
- Register the existing discovery-and-matching path as the exact
  `opportunity_discovery:v1` template without changing the competition UI, YAML
  filename, agents, or guards.
- Record current agent/state/capability mappings.
- Keep all current evals green.

#### Phase 1A — Run identity and durable truth

- Add `WorkflowRun`, immutable plan, run-event, and current-projection records.
- Hash every plan from its first persistence under `canonical_json_v1`, closed
  schemas, and the §7.4 golden-vector/rejection suite.
- Assign transactional per-run `run_seq` and prove the control projection can
  be destroyed and rebuilt from ordered events and immutable plan records.
- Update 02's data model and accessor-coverage registry in the same change.
- Wrap the current grant/application lifecycle in a run ID without changing its
  state guards or founder UX.
- Demonstrate two concurrent read-only/grant runs without state leakage.

#### Phase 1B — Bounded dispatch and dormancy

- Add step-attempt records, queue-backed bounded dispatch, fenced worker leases,
  universal runtime status, waits/timers, retries, and uncertain-action
  reconciliation.
- Extend the existing Cloud Tasks footprint under §22.1's queue/IAM/local-dev
  contract.
- Prove wait compare-and-set resolution, chained long timer checkpoints, and
  durable idempotency beyond the queue's task-name deduplication window.
- Kill workers at each commit boundary, including after lease supersession, and
  prove safe recovery/redelivery with late commits fenced.

#### Phase 1C — Delivery, control, and concurrency

- Move global/latest-session notifications to run-correlated origin delivery
  plus a durable founder inbox.
- Add per-run pause/resume/cancel and per-founder budgets/priorities.
- Demonstrate two concurrently active runs receiving correctly correlated
  events and notifications.

#### Phase 2 — Unified conversational invocation

- Evolve `/wake` behind `POST /api/chat/messages`.
- Add intent envelopes, structured reference resolution, attachment IDs/scopes,
  `client_request_id`, run cards, and deterministic slash commands.
- Natural language and `/discover` must create identical validated requests.
- Make `/discover` enter the funding journey, wait at `AWAITING_SELECTION`,
  and create one linked
  `grant_application:v1` run after a valid selection.
- Add `/apply <opportunity>` and its natural-language equivalent as direct,
  idempotent application entry points without weakening existing gates.

#### Phase 3 — Second business workflow: investor outreach

- Build research, qualification, draft-only, approval-gated send, reply wait,
  and meeting-brief path.
- Reuse researcher, analyst, writer, correspondence, and event infrastructure.
- This phase proves that Alex and the runtime are not grant-specific.

#### Phase 4 — Template variants and replanning

- Add optional branches, negative constraints, plan-hash consumption in
  variants/replanning, invalidation, and auditable replanning.
- Demonstrate “draft only” becoming “send the approved top five” without
  rerunning research or weakening approval.

#### Phase 5 — Dynamic composition

- Add isolated planner and deterministic plan compiler.
- Enable composed plans only for reviewed capability combinations.
- Enforce the §8.1 first-release ceiling: only no-effect/read-only/internal-
  reversible DAGs, with no composed waits. Objectives requiring waits or
  external effects fall back to reviewed template variants.

#### Phase 6 — Domain expansion

- Customer discovery next because it reuses outreach and interview primitives.
- Vendor follow-through next because it proves multi-day waits/signatures.
- Compliance, hiring, and bookkeeping only after their dedicated domain policy,
  data, and human-control eval packs are approved.

### 22.4 Exit criteria between phases

No phase begins because the UI looks ready. The prior phase must prove:

- durable recovery after process death;
- no cross-run state/context leakage;
- launch and action idempotency;
- correct event correlation;
- policy gates under adversarial prompts;
- visible, evidence-backed run status;
- bounded cost and no polling.

---

## 23. Rejected architecture alternatives

| Alternative | Why it is rejected |
|---|---|
| One universal business state machine | State names become vague, exceptions multiply, and domain-specific completion/safety rules disappear. Only runtime status is universal. |
| One autonomous agent with every tool | Context and permission surface grow together; one routing mistake can become an external effect. |
| Pure prompt routing with no intent contract | Natural conversation remains pleasant but launches, references, negative constraints, and approvals are non-deterministic. |
| Arbitrary model-generated workflow code/YAML | It creates an unreviewed programming and privilege-escalation surface. Plans may reference only registered IDs. |
| Chat transcript as workflow memory | Compaction, correction, concurrent work, and cold starts make inferred progress unsafe. |
| One permanent worker per run | It is costly, brittle across deployments, and unnecessary while waiting. |
| A new full application stack per workflow | Duplicates identity, events, approvals, audit, connectors, and UX instead of reusing a control plane. |
| Generalize a workflow DSL before a second vertical | Produces speculative abstractions. Prove grants plus investor outreach first. |
| Require slash commands for reliability | Makes Alex feel like a CLI. Slash commands are deterministic shortcuts over the same natural intent contract. |

---

## 24. Decisions and recommended defaults for review

| Question | Recommended default |
|---|---|
| Is the current detailed Mermaid universal? | No. It is the application lifecycle for `grant_application:v1`; the funding journey links discovery to it without merging their run state. |
| Is there a universal state machine? | Only runtime status; domain state is per workflow. |
| Can Alex compose workflows? | Yes, from registered capabilities through a validated plan IR. |
| Can Alex publish reusable workflows? | No. New reusable definitions require code/product review. |
| Is chat state workflow truth? | No. Chat references durable runs. |
| Do slash commands bypass understanding? | The command token is deterministic; prose/arguments still compile to typed inputs. |
| Can natural language start work? | Yes for explicit safe objectives; policy controls effects. |
| Can “I approve” in chat authorize an action? | No. The action-bound approval API is authoritative. |
| One agent per workflow? | No. Reuse bounded roles; workflow dispatches steps. |
| One ADK session per run? | Not required. A run may use one or more isolated worker sessions; Firestore is workflow truth. |
| Can plans change? | Yes through immutable versioned replans with policy diff and stale-output handling. |
| What is built second? | Investor outreach, to prove multi-workflow reuse and long-running reply handling. |

---

## 25. Acceptance checks

Every check has a first owning phase. Later phases keep all earlier checks green:

| Acceptance group | First owning phase |
|---|---|
| Architecture vocabulary/current grant registration | Phase 0 |
| Run truth, plan canonicalization, event order, projection rebuild | Phase 1A |
| Dispatch, waits, recovery, and reconciliation | Phase 1B |
| Notifications, run control, and concurrency | Phase 1C |
| Conversation, commands, references, attachments | Phase 2 |
| Investor external-effect proof | Phase 3 |
| Plan variants and replanning | Phase 4 |
| Planner/compiler and restricted composition | Phase 5 |
| High-liability domain enablement | Phase 6 |

### Architecture contract

- [ ] **[Phase 0]** Reviewers can identify Alex, conversation, workflow definition, plan,
      run, domain state, runtime status, agent, capability, wait, and event as
      separate concepts.
- [ ] **[Phase 0]** The existing grant diagram is registered as `grant_application:v1`, not
      used as the universal Alex lifecycle.
- [ ] **[Phase 0]** Existing discovery and matching are registered as
      `opportunity_discovery:v1`, and `grant_applications` remains a compatibility
      alias, without changing competition behavior, agents, guards, or the YAML
      filename.
- [ ] **[Phase 1C]** A conversation can reference at least two concurrently active runs with
      independent domain state and worker context.
- [ ] **[Phase 1C]** No completion or notification path depends on a global latest session.

### Conversation and invocation

- [ ] **[Phase 2]** Casual discussion creates no run; an explicit natural-language objective
      creates one typed intent envelope.
- [ ] **[Phase 2]** `/discover ...` and an equivalent natural request compile to equivalent
      validated inputs.
- [ ] **[Phase 2]** `/discover ...` creates one journey/run request and reaches
      `AWAITING_SELECTION` after ranked results are ready.
- [ ] **[Phase 2]** Within a `/discover` journey, no linked application run exists before
      the founder resolves the named opportunity-selection wait; direct `/apply`
      remains the explicit skip-discovery path.
- [ ] **[Phase 2]** Resolving the named selection wait creates exactly one linked
      `grant_application:v1` run with the same journey and conversation origin;
      duplicate selection delivery returns that child instead of creating another,
      and the UI presents discovery plus application in one journey card.
- [ ] **[Phase 2]** Selection atomically moves the discovery run from
      `WAITING/AWAITING_SELECTION` to `SUCCEEDED/SELECTED` and persists the child
      as `RECEIVED`; a failed child write leaves the wait and discovery run open.
- [ ] **[Phase 2]** `/apply <opportunity>` and an equivalent explicit natural request start
      the same application lifecycle directly from an authorized known opportunity.
- [ ] **[Phase 2]** Unknown slash commands cannot invoke arbitrary capabilities.
- [ ] **[Phase 2]** Ambiguous “pause that” across two plausible runs asks one focused question.
- [ ] **[Phase 2]** Duplicate `client_request_id` returns the existing run and launches no
      duplicate work.

### Workflow and composition

- [ ] **[Phase 5]** Exact templates, template variants, and composed plans share the same run
      and event runtime.
- [ ] **[Phase 5]** A composed plan can reference only registered capabilities and roles.
- [ ] **[Phase 5]** Composed plans are acyclic; reviewed template/capability loops,
      waits, side effects, cost, and completion contracts are deterministically
      validated before queueing.
- [ ] **[Phase 5]** The first Level-4 release accepts only `NO_EFFECT`, `READ_ONLY`, and
      `INTERNAL_REVERSIBLE` DAGs with no composed waits; other objectives use a
      reviewed template variant or fail closed.
- [ ] **[Phase 1A]** A validated plan uses a closed schema, rejects unknown fields and
      non-portable keys, and is canonicalized, hashed, and golden-vector tested
      from its first persistence.
- [ ] **[Phase 1B]** Every step attempt and action records the plan hash and step ID it
      executes under.
- [ ] **[Phase 4]** Replanning creates a new version, preserves completed effects, invalidates
      stale downstream outputs, and cannot weaken gates.

### Agents and capabilities

- [ ] **[Phase 1A]** Agent handoff alone cannot advance domain state.
- [ ] **[Phase 1A]** Agents cannot mutate approvals, run status, audit, capability registry, or
      policy.
- [ ] **[Phase 3]** Every capability has typed schemas, side-effect class, idempotency,
      retry/reconciliation, allowed role, and completion evidence.
- [ ] **[Phase 5]** Planner context contains capability descriptors but no secrets or effect
      tools.
- [ ] **[Phase 1B]** Step workers receive only a scoped context pack, not unrelated chat/runs.

### Long-running reliability

- [ ] **[Phase 2]** Launch returns a `run_id` without holding the HTTP request for the run.
- [ ] **[Phase 1A]** Accepted run events receive gap-free monotonic `run_seq` values, and
      rebuilding ordered events reproduces the same control projection and next
      sequence without reconstructing domain/artifact stores.
- [ ] **[Phase 1B]** Killing a worker at every step boundary preserves or safely reconciles
      progress.
- [ ] **[Phase 1B]** A superseded worker's stale `lease_generation` cannot commit an
      authoritative outcome, projection update, or follow-on event.
- [ ] **[Phase 1B]** Open waits consume no active worker/model/container requirement.
- [ ] **[Phase 1B]** Concurrent matching events resolve a wait once by compare-and-set;
      long timeouts cross queue scheduling horizons through state-checked
      checkpoints without polling.
- [ ] **[Phase 1B]** Duplicate queue/event delivery cannot duplicate an external action.
- [ ] **[Phase 1B]** `UNCERTAIN` actions reconcile before retry.
- [ ] **[Phase 1C]** Duplicate event delivery cannot create two notifications with the
      same `(run_id, event_id, notification_type)`.
- [ ] **[Phase 1C]** Pause, cancel, timeout, and event resume operate on the named run only.

### Safety, data, and domain policy

- [ ] **[Phase 2]** Task-scoped attachments cannot silently mutate founder memory.
- [ ] **[Phase 2]** A linked application inherits only normalized discovery constraints,
      selected-opportunity evidence, and journey-scoped artifact refs;
      unrelated instructions, history, and attachments remain out of scope.
- [ ] **[Phase 3]** Every approval binds founder, run, plan, step, target, payload hash, expiry,
      and consumption state.
- [ ] **[Phase 3]** Chat phrases and artifact instructions cannot resolve approvals or change
      side-effect class.
- [ ] **[Phase 6]** Hiring, bookkeeping, and compliance consequential effects remain disabled
      until dedicated policy/eval packs pass.
- [ ] **[Phase 1A]** Derived records and outputs carry source/model/capability provenance and
      become stale when their evidence changes.

### Product proof

- [ ] **[Phase 3]** In one chat, the founder launches a grant-related run and an investor run,
      continues conversing, closes the app, and later receives correctly
      correlated updates for both.
- [ ] **[Phase 4]** Investor outreach can stop after drafts, then replan to send an approved
      subset without rerunning completed research.
- [ ] **[Phase 3]** A reply event resumes the investor run and produces a meeting brief whose
      claims cite the investor record and reply thread.
- [ ] **[Phase 3]** The run card and audit trail prove actual effects and distinguish prepared,
      succeeded, failed, and uncertain work.
