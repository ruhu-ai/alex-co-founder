# Review prompt — docs/24 data-source reliability

Use the prompt below with a fresh principal-level architecture, security, and
distributed-systems reviewer. The reviewer must inspect the repository; a
document-only opinion is insufficient.

---

Act as a principal Google-level software engineer, security reviewer, and
product architect. Perform an adversarial implementation-readiness review of:

`docs/24-data-source-reliability.md`

Repository root:

`/Users/ijidailassa/projects/all-things-ai`

The specification is proposed only. Do not implement it. Inspect current code,
tests, and binding specs, especially:

- `docs/README.md`
- `docs/01-architecture.md`
- `docs/02-data-model.md`
- `docs/03-state-machine.md`
- `docs/05-tools.md`
- `docs/06-memory-profile.md`
- `docs/07-server-webhooks.md`
- `docs/12-security.md`
- `docs/15-document-outputs.md`
- `docs/18-browser-agent.md`
- `docs/21-alex-platform-north-star.md`
- `docs/22-browser-runtime-hardening.md`
- `docs/23-session-resources-and-global-search.md`
- `app/main.py`
- `services/connectors.py`
- `services/google_oauth.py`
- `services/drive_adapter.py`
- `services/gmail_adapter.py`
- `services/alex_mailbox.py`
- `services/calendar_adapter.py`
- `services/document_ingestion.py`
- `services/profile_service.py`
- `services/session_resources.py`
- `services/approval_service.py`
- `services/firestore.py`
- relevant unit/integration tests and fake-store fixtures.

## Product constraint

Co-Founder is a high-trust founder-operations product, not a generic enterprise
assistant or connector platform. This slice should harden only current funding
workflow sources and destinations. It must not introduce Gemini Enterprise,
StreamAssist, Memory Bank, a new company-wide RAG index, a connector
marketplace, or a universal workflow platform. `CompanyKnowledgeProvider` is
deliberately deferred until a second retrieval backend is approved.

## Required review

### 1. Re-derive the current behavior

Trace actual producer/consumer paths for:

- direct upload registration, ingestion, extraction, search, and profile
  proposals;
- Drive selection, fetch/export, legacy ingestion, and document sync;
- founder Gmail scanning and processed-id handling;
- Alex Gmail watch/history recovery, verification-message routing, processed
  ids, search/read/send;
- Calendar reads and approval-gated creation;
- connector catalogue/OAuth/Secret Manager status;
- resource/session provenance;
- approval and browser/external action receipts;
- proactive notification and all uses of `founder_state.active_session_id`.

Identify every place where docs/24 misstates current behavior.

### 2. Challenge the boundaries

Determine whether these are correctly separated and minimally sufficient:

- `ConnectionRegistry`;
- `SourceGrant`;
- `KnowledgeSourceIngestion`;
- evidence/verification levels;
- `ExternalEvent` receipt;
- deterministic correlation;
- `FounderInboxItem`;
- `ExternalAction` receipt;
- deferred `CompanyKnowledgeProvider`.

Flag both under-design and unnecessary abstraction. Do not reward more layers
merely for looking architectural.

### 3. Attack durability and idempotency

For each provider path, inject crashes or redelivery:

- before and after provider fetch;
- after marking a message processed but before follow-up mutation;
- after event receipt but before correlation;
- after application update but before task enqueue;
- after provider write transmission but before response/receipt commit;
- during OAuth disconnect/revocation;
- during Drive byte storage, metadata creation, resource-link commit, and task
  dispatch.

Prove whether every path converges without event loss, duplicate effects, or
false success. Verify Firestore transaction boundaries are technically possible
with the proposed records and query patterns.

### 4. Attack authorization and isolation

Attempt:

- foreign founder/session/connection/source/event/inbox/action ids;
- guessed Drive ids and revoked source grants;
- provider-account confusion between founder Gmail and Alex mailbox;
- stale OAuth callbacks and stale health writes;
- source-title/snippet/existence leakage;
- model-authored provider ids, collection refs, roles, and statuses;
- correlation to the wrong application/session;
- active-session fallback;
- revocation races and retained access after disconnect.

Require generic missing/refusal behavior and server-derived ownership.

### 5. Attack prompt and memory safety

Verify that email, Calendar, documents, filenames, provider errors, source
metadata, and StreamAssist-like future responses remain untrusted data. Look for
paths that could:

- enter agent instructions without delimiters/scanning;
- authorize a tool or approval;
- become a Founder Profile fact without allowed evidence/confirmation;
- contaminate logs, audit, metrics, global search, or inbox projections;
- silently win a source conflict by recency/model preference.

Evaluate whether `UNCONFIRMED_EVIDENCE`, `EVIDENCE_VERIFIED`, and
`FOUNDER_CONFIRMED` are sufficiently precise and compatible with the existing
profile auto-application behavior.

### 6. Review external-action semantics

For Gmail send, Calendar create, Drive export, portal actions, and direct
founder clicks:

- identify the exact irreversible/reversible boundary;
- verify approval subject completeness;
- verify idempotency key derivation;
- identify available provider reconciliation APIs/receipts;
- determine when FAILED versus UNCERTAIN is knowable;
- ensure a founder click is not burdened with an unnecessary model-visible
  token while still remaining idempotent and audited.

### 7. Review data model and migration

For each proposed collection and index:

- prove it has independent lifecycle/authority and is not redundant;
- check Firestore size, contention, query, transaction, and index constraints;
- verify collection-registry/export/deletion coverage;
- test deterministic ids and tombstone/recreation behavior;
- evaluate dual-write/dual-read rollback;
- identify what can and cannot be backfilled honestly from existing processed
  ids, integrations, audit, applications, and resource links.

Explicitly answer the eight review decisions in docs/24 §22.

### 8. Review scope discipline

Reject recommendations that turn this slice into:

- generic enterprise search/RAG;
- broad connector expansion;
- a connector marketplace/plugin runtime;
- generic workflow/agent infrastructure;
- premature StreamAssist/Memory Bank integration.

Conversely, identify any missing primitive that is required now to make the
funding workflow correct.

### 9. Validate tests and acceptance criteria

Check that the test plan can detect:

- fifty concurrent duplicate events;
- crash/restart at every commit boundary;
- lost event due to processed-id ordering;
- one provider message producing two follow-ups;
- wrong-session delivery;
- ambiguous subject matching;
- stale health overwrite;
- Drive authorization/provenance bypass;
- profile poisoning and conflict suppression;
- duplicate or uncertain outbound effects;
- sensitive content in logs/audit/search/inbox;
- external browser opening regression.

Add missing deterministic tests and remove acceptance checks that cannot be
proved locally or in a bounded integration environment.

## Required output format

1. **Executive verdict:** `ACCEPT`, `ACCEPT WITH REQUIRED CHANGES`, or `BLOCK`.
2. **Current-behavior corrections:** every inaccurate statement with exact
   file/line evidence.
3. **Findings:** ordered P0–P3. Each finding must include:
   - violated invariant;
   - concrete failure/exploit sequence;
   - affected spec section and code path;
   - exact required spec correction;
   - deterministic acceptance test.
4. **Boundary decision table:** keep, merge, delete, or defer each proposed
   service/collection and why.
5. **Transaction/event sequence review:** corrected sequence diagrams for Drive
   ingestion, inbound mail, inbox resolution, and outbound action.
6. **Migration verdict:** safe cutover/rollback plan and blockers.
7. **Acceptance-criteria delta:** additions, removals, and measurable thresholds.
8. **Minimal corrected build order:** smallest implementation sequence that
   makes the current funding workflow safe.
9. **Residual risks:** risks intentionally deferred and the evidence needed to
   revisit them.

Do not implement code. Do not accept checklist claims without tracing the code
or proving the proposed transaction. Prefer a smaller correct product boundary
over an elaborate generic platform.
---
