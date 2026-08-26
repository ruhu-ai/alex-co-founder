# Review prompt — Session-linked resources and global search

You are the principal architecture reviewer for the Co-Founder/Alex repository.
Review `docs/23-session-resources-and-global-search.md` as an adversarial design
review. Do not implement it and do not spend time on prose style unless wording
creates an architectural ambiguity.

## Required context

Read these files before issuing a verdict:

1. `AGENTS.md`
2. `docs/README.md`
3. `docs/01-architecture.md`
4. `docs/02-data-model.md`
5. `docs/03-state-machine.md`
6. `docs/07-server-webhooks.md`
7. `docs/08-discovery-pipeline.md`
8. `docs/10-ui.md`
9. `docs/12-security.md`
10. `docs/15-document-outputs.md`
11. `docs/16-design-system.md`
12. `docs/21-alex-platform-north-star.md`
13. `docs/23-session-resources-and-global-search.md`

Inspect the implementation rather than trusting the spec's inventory. At
minimum inspect:

- `app/main.py`
- `app/live.py`
- `app/resume_handler.py`
- `app/static/index.html`
- `agents/co_founder/tools/*.py`
- `services/firestore.py`
- `services/discovery_service.py`
- `services/pipeline_service.py`
- `services/document_service.py`
- `services/document_ingestion.py`
- `services/browser_events.py`
- tests covering sessions, discovery, documents, attachments, browser events,
  approvals, feedback, and deletion/export.

The optional repos `/Users/ijidailassa/projects/alvary` and
`/Users/ijidailassa/projects/all-things-ai/.opensrc` are read-only pattern-
mining inputs. Do not import from them or make this project depend on them.

## Review tasks

### 1. Re-derive the current producer/consumer inventory

Use repository search to enumerate every writer of founder-visible output and
every current consumer of session IDs, artifact/document metadata, discovery
receipts, opportunities, applications, feedback, evidence reports, browser
runs/frames, and chat events.

Compare your inventory with §6.1. Identify missed writers, duplicate/pseudo
stores, and consumers that would break when `GET /api/sessions` changes. Pay
special attention to:

- voice/live-chat persistence;
- proactive notifications and webhooks;
- direct UI calls to internal task endpoints;
- tool calls that have `ToolContext` but discard the session;
- generated files that have both document and artifact identities;
- system discovery's `system-discovery` ADK session;
- Drive/Gmail ingestion and future connector outputs;
- audit/export/deletion paths and fake-store fixtures.

### 2. Challenge the ownership boundary

Decide whether the proposed split is correct:

- canonical domain record = business truth;
- WorkflowRun/event = execution truth when implemented;
- ADK session = conversation state/transcript;
- resource index = searchable logical-resource projection;
- session-resource link = many-to-many provenance occurrence.

Look for accidental claims that session state owns an application, that a
resource link grants authorization, or that the index becomes workflow truth.
Test these scenarios:

1. One opportunity is found in two discovery sessions.
2. One application begins in session A and continues in session B.
3. A document is produced in B from an application created in A.
4. A deadline event has no founder origin session.
5. Two runs launched from one conversation complete out of order.
6. An origin conversation is deleted while its application must be retained.
7. A resource is created outside chat and later referenced in chat.

State whether `resource_index`, `session_resource_links`, and `session_catalog`
are all necessary. If you propose fewer records, prove how you avoid unbounded
arrays, dedupe collisions, N+1 reads, historical-provenance loss, and
authorization coupling.

### 3. Validate identity and idempotency

Review deterministic resource/link IDs and occurrence-key selection. Confirm
that retries collapse while intentional repeated searches remain distinct.
Find any path where a model, client, filename, query text, or caller-provided
collection can become an authority-bearing identifier.

Check the transaction boundaries for:

- receipt + resource + link before task dispatch;
- canonical Firestore record + projection + link;
- binary storage before metadata;
- Cloud Task dispatch failure after acceptance;
- worker redelivery and lease reclaim;
- application selection across sessions;
- projection update versus immutable historical link snapshot.

Require a concrete recovery state for every partially committed boundary.

### 4. Validate Firestore feasibility and search quality

Verify the proposed indexes and queries are legal in Firestore, including
array-contains plus equality/order filters. Check document size, array-entry,
batch/transaction, `in` query, cursor, and write-hotspot limits against the
bounded values in the spec.

Challenge:

- token/prefix explosion and multilingual normalization;
- candidate selectivity for common two-character prefixes;
- stable pagination after projection mutations;
- merging session and resource streams without skips/duplicates;
- N+1 hydration of resource/session associations;
- title/status updates versus historical search snapshots;
- search results older than the current 30-session cap;
- whether PostgreSQL FTS should be the first implementation instead.

Do not accept “demo scale” as justification for an unbounded scan. If the
Firestore plan is not implementable as written, give an exact replacement query
plan that preserves the public API and dormancy requirement.

### 5. Validate security, privacy, and retention

Attempt to use search as an existence oracle across founders/sessions. Verify
ownership is enforced independently at the index query and canonical-resource
open boundaries.

Look for accidental indexing of:

- secrets, credentials, approval tokens, OTPs;
- raw email bodies or connector data;
- attachment chunks/document bodies;
- browser page text/screenshots/form mappings;
- sensitive Founder Profile values;
- hidden system notices or model/tool payloads.

Review delete/export semantics for session-scoped uploads versus retained
applications and resources. Confirm tombstones cannot leak a foreign title or
summary. Confirm query text is absent from metrics/audit logs.

### 6. Validate UX and API semantics

Check that the proposed global search actually covers conversations, previous
searches, opportunities/leads, applications, documents/artifacts, and future
runs without returning misleading duplicates.

Verify:

- opening is read-through and Resume is explicit;
- opening never launches/re-runs a search;
- live voice prevents unsafe session switching;
- typed focus targets cannot become executable URLs/handlers;
- stale/out-of-order responses cannot replace newer results;
- blank/recent, loading, failure, pagination, keyboard, screen-reader, mobile,
  contrast, and icon behavior are specified sufficiently;
- deleted origin sessions still allow correctly retained resources to open;
- UI copy does not say drafts/applications are owned by sessions.

### 7. Check compatibility with the north-star migration

Determine whether this spec can land before WorkflowRun without creating a
second permanent control plane. Confirm optional `run_id`/`journey_id` fields
can become mandatory when the relevant run exists and that Phase 1A/1C/2 can
adopt the projections without destructive rewrites.

Flag any conflict with:

- no completion to a global latest session;
- founder inbox delivery;
- one conversation referencing many runs;
- one run owning zero or more worker sessions;
- artifact scopes and explicit reference resolution;
- append-only run events and current projections;
- current competition lifecycle and approval/state guards.

### 8. Re-derive the tests and migration safety

Do not merely restate §12/§13. Identify missing failure-injection, concurrency,
authorization, pagination, backfill, export/deletion, and UI tests.

For legacy backfill, verify each proposed inference is exact. Reject timestamp
proximity or “latest session” heuristics. Require dry-run, checkpoints, bounded
pages, idempotent reruns, counts-only reports, and explicit `legacy_unlinked`
handling.

## Required output format

Start with exactly one verdict:

- `PASS` — implementable as written; only optional improvements remain.
- `PASS WITH CONDITIONS` — sound design with a small, explicit pre-implementation
  condition list.
- `REWORK` — one or more architectural or safety blockers.

Then provide:

1. **Findings**, ordered by severity (`BLOCKER`, `MAJOR`, `MINOR`). Each finding
   must include:
   - a short title;
   - the violated invariant or concrete failure scenario;
   - evidence with exact file/spec section and line references where possible;
   - the smallest sufficient correction;
   - the acceptance test that proves the correction.
2. **Producer/consumer matrix delta** — rows missing or misclassified in §6.1.
3. **Data/query feasibility verdict** — explicit judgment on each collection,
   deterministic ID, index, query, pagination, and transaction boundary.
4. **North-star compatibility verdict** — whether this is a projection seam or
   an accidental rival runtime.
5. **Required spec edits before implementation** — a finite checklist.
6. **Suggested implementation order changes**, only if needed to reduce risk.

Do not approve based on intent. A `PASS` requires that two concurrent discovery
sessions can surface one canonical opportunity, both remain searchable and
navigable, retries create no duplicates, no cross-founder oracle exists, and no
path assigns system work to a guessed latest conversation.
