# 02 — Data Model

Two stores, plus ADK session state and artifacts. Firestore is the system of record
for the pipeline; ADK sessions (SQLite/Cloud SQL) own conversation + session state.

## Firestore collections

### `opportunities/{opportunity_id}`

One document per discovered program/grant. Written by Scout + Matchmaker tools.

| Field | Type | Set by | Notes |
|---|---|---|---|
| `name` | str | scout | |
| `source_url` | str | scout | where it was found |
| `source_type` | str | scout | `web_page` \| `pdf` \| `newsletter` \| `manual` |
| `award` | str | scout | free text, e.g. "$50,000" |
| `deadline` | str\|null | scout | ISO date; null if rolling |
| `eligibility` | list[str] | scout | bullet strings, verbatim-ish |
| `application_url` | str | scout | where the form lives |
| `required_materials` | list[str] | scout | e.g. ["2 essays", "deck", "budget"] |
| `description` | str | scout | ≤ 500 chars |
| `raw_excerpt` | str | scout | source snippet for citation; ≤ 2000 chars |
| `dedup_hash` | str | scout | sha256(normalized name + application_url) |
| `state` | str | pipeline | DISCOVERED → SHORTLISTED \| ARCHIVED |
| `fit_score` | int\|null | matchmaker | 0–100 |
| `fit_rationale` | str\|null | matchmaker | ≤ 280 chars, founder-facing |
| `archive_reason` | str\|null | matchmaker | required when state=ARCHIVED |
| `urgency` | map\|null | sentinel | `{tier, days_left, note}` — see 08 |
| `created_at`, `updated_at` | str | server | ISO-8601 UTC |

Indexes: `state` ASC, `deadline` ASC. (`dedup_hash` unique-enforced in code.)

### `applications/{application_id}`

Created when the founder chooses to apply to a SHORTLISTED opportunity.
New records use a deterministic id derived from `founder_id + opportunity_id`
and are created transactionally, so concurrent/retried selection returns the
same application. A compatibility lookup reuses older UUID-backed records.

| Field | Type | Notes |
|---|---|---|
| `opportunity_id` | str | ref |
| `founder_id` | str | |
| `state` | str | application lifecycle — see 03 |
| `checklist` | list[map] | `[{key, label, status, section_id?}]`; status ∈ PENDING \| IN_PROGRESS \| DONE \| BLOCKED |
| `interview_qa` | list[map] | `[{question_key, question, answer, asked_at, answered_at}]` |
| `draft_sections` | list[map] | `[{section_id, section_key, content, word_count, status, version}]`; status ∈ DRAFTED \| IN_REVIEW \| CHANGES_REQUESTED \| APPROVED |
| `latest_evidence_check_id` | str\|null | newest report matching current draft/profile versions; see 20 |
| `form_fill_report` | map\|null | `{filled, total, needs_human: [field], portal_state_hash, mapping_hash, screenshot_artifact, ran_at}`; portal/mapping hashes are the authority used for reopen and approval binding (09/22) |
| `last_fill_mapping` | map\|null | durable, redaction-aware portal field mapping needed to reconstruct a stopped fill; values come only from approved application/profile sources and are never copied into BrowserRun or audit detail |
| `submit_idempotency_key` | str | generated on entering APPROVED; see 05 |
| `submission` | map\|null | `{confirmation_id, submitted_at, portal_url}` |
| `followups` | list[map] | `[{kind, due_at, status, note}]` |
| `created_at`, `updated_at` | str | |

### `profiles/{founder_id}` — the Founder Profile (long-term memory)

Structure detailed in **06-memory-profile.md**. Collections: `facts`,
`fact_provenance`, `fact_history`, `voice_rules`, `canonical_answers`,
`rejection_history`, `decision_patterns`. `fact_provenance` and canonical-answer
entries expose `verification_level` (`UNCONFIRMED_EVIDENCE`, `EVIDENCE_VERIFIED`,
`FOUNDER_CONFIRMED`, or `SUPERSEDED`); replaced facts move into `fact_history`
instead of being erased. This document is the
"constantly adapts" evidence; every mutation is versioned (`version` int, bumped by
`apply_profile_update`).

### `ingestions/{ingestion_id}`

One document per ingested company document (06 §bootstrap).

| Field | Type | Notes |
|---|---|---|
| `founder_id` | str | |
| `source_type` | str | `upload` \| `google_drive` |
| `source_ref` | str | filename or Drive file id |
| `artifact` | str | `companydoc_{ts}` artifact name |
| `status` | str | `QUEUED` \| `VALIDATING` \| `EXTRACTING` \| `INDEXING` \| `READY` \| `NEEDS_FOUNDER` \| `CONFIRMED` \| `NO_TEXT` \| `UNSUPPORTED` \| `FAILED`; terminal failures never masquerade as extracted success |
| `artifact_id` | str | same opaque id in v1; joins the ingestion attempt to `artifacts/{artifact_id}` without making a filename authoritative |
| `session_id` | str | session that registered the upload; required to attach it to chat |
| `scope` | str | `profile` or `reference_only`; selected explicitly by the upload surface |
| `content_type`, `size_bytes`, `sha256` | str/int/str | immutable upload provenance and integrity metadata |
| `auto_applied`, `needs_founder_count` | int | truthful projection of the deterministic proposal review |
| `proposed_updates` | list[map] | `[{id, kind, payload, evidence_quote, citation, verification_level, review_reason, source_available, status}]`; auto-apply is limited to exact citation-bound fact/canonical-answer proposals |
| `confirmed_at` | str\|null | |
| `created_at` | str | |

### `artifacts/{artifact_id}` and `artifacts/{artifact_id}/chunks/{chunk_id}`

The artifact record is the authorization and provenance boundary for an uploaded
file. In v1 `artifact_id == ingestion_id` keeps existing attachment references
stable, while the records remain logically separate. The source bytes stay in the
artifact service; Firestore holds metadata and bounded extracted chunks only.

| Artifact field | Type | Notes |
|---|---|---|
| `kind` | str | `document` (compatibility default) or `image`; image is always `reference_only` and originating-session scoped (35) |
| `founder_id`, `session_id` | str | owner and originating session; checked again on every retrieval |
| `scope` | str | implemented: `profile` or `reference_only`; future run/journey/entity scopes remain disabled until their authority records exist |
| `source_type`, `connection_id`, `source_grant_id` | str\|null | durable source lineage; provider ids never authorize a read by themselves |
| `authority` | str | `reference_only` or `profile_candidate`; retrieval still returns `unconfirmed_evidence` until promoted under the profile rules |
| `storage_name`, `source_ref` | str | randomized storage object and display-only original filename |
| `declared_content_type`, `detected_content_type`, `detected_extension` | str | the declared value is never the trust boundary; detected type must pass the compatibility matrix |
| `sha256`, `size_bytes` | str/int | immutable source integrity metadata |
| `status` | str | mirrors the current ingestion status for inexpensive UI reads |
| `extractor_version`, `model_version` | str | provenance for derived chunks and proposals |
| `retention_policy` | str | `profile` or `session`; deletion/export operates on source and chunks together |
| `created_at`, `updated_at` | str | timestamps |

Each chunk stores `ordinal`, `content`, `content_sha256`, `kind`, `heading`, and a
closed `locator` map containing only format-appropriate values (`page`, `slide`,
`paragraph`, `section`, `sheet`, or `cell_range`). Derived profile proposals cite
`{artifact_id, chunk_id, locator, quote, quote_sha256}`. Search results return the
same citation shape; a quote without an artifact and locator is not a citation.

Explicit still images add `width`, `height`, `pixel_count`, `source_sha256`,
`normalized_storage_name`, `normalized_content_type`, `normalized_sha256`,
`normalized_width`, `normalized_height`, `orientation_applied`, and
`metadata_policy=STRIPPED`. The original exists only because the founder selected Add
image or confirmed Capture still; live camera/display frames never create artifacts.
Original and normalized bytes share the source artifact's export, deletion, TTL, and
orphan-cleanup lifecycle.

`artifacts/{artifact_id}/image_observations/{observation_id}` stores at most 64
published, schema-validated observations: ordinal, neutral description, bounded OCR,
normalized region, confidence band, safety flags, source/evidence hashes, extractor
version, generation, and status. It contains no action or approval fields. Search
re-resolves owner/session/READY/reference-only/source hash and returns at most eight
canonical image-region citations.

### `media_consent_grants/{grant_id}` and `live_media_shares/{share_id}`

These docs/35 rows contain lifecycle metadata only—never pixels, thumbnails, OCR,
captions, prompts, device labels, or provider responses. Consent grants bind workspace,
actor, session, source class, server disclosure version/hash, challenge, status, and
expiry (no later than 24 hours/session end). Share rows bind one single-use start to a
source/share/generation and store effective limits, start/end reason, aggregate frame/
byte/drop counters, and a maximum 30-day operations expiry. Neither collection is
workflow/action authority. Both participate in founder export/deletion and TTL policy.

### `feedback/{feedback_id}`

| Field | Type | Notes |
|---|---|---|
| `application_id`, `section_id` | str | |
| `founder_id` | str | |
| `type` | str | `approve` \| `edit` \| `reject` |
| `original` | str | draft as presented |
| `edited_text` | str | when type=edit |
| `reason` | str | verbatim founder reason when type=reject/edit — required for reject |
| `distilled` | bool | set true after distiller runs |
| `distilled_rule_ids` | list[str] | pointers into profile mutations |
| `created_at` | str | |

### `approvals/{approval_id}`

| Field | Type | Notes |
|---|---|---|
| `application_id` | str | |
| `gate` | str | `submit_application` (09) \| `create_portal_account` (17) \| `book_meeting` (12) |
| `token` | str | opaque, `uuid4().hex`; single-use |
| `status` | str | PENDING \| GRANTED \| CONSUMED \| EXPIRED \| DENIED |
| `expires_at` | str | now + `APPROVAL_TTL_MINUTES` |
| `subject_hash` | str\|null | for `submit_application`, hash of application id + fill report `portal_state_hash` + `mapping_hash`; submit must match it after reopen |
| `granted_by` | str\|null | founder_id on grant |
| `consumed_at` | str\|null | |
| `created_at` | str | |

### `audit/{audit_id}` — append-only, never updated

| Field | Type | Notes |
|---|---|---|
| `actor` | str | `agent:<name>` \| `founder:<id>` \| `system:<job>` |
| `action` | str | e.g. `form_fill`, `submit`, `profile_update`, `state_transition` |
| `idempotency_key` | str\|null | |
| `target` | str | entity ref, e.g. `applications/abc` |
| `result` | str | `success` \| `error` \| `refused` |
| `detail` | str | ≤ 500 chars, no PII beyond refs, **no secrets**. Structured extras are compact JSON (e.g. `{"url": "…", "action_id": "…", "injection_suspected": true}`) within the same budget |
| `created_at` | str | |

### `discovery_requests/{request_hash}` — compatibility-adapter receipts

Small execution receipts for the pre-Phase-0 `/discover` adapter; these are not
WorkflowRun records and contain no workflow plan or domain state. The document
id is derived from `founder_id + opaque client_request_id`. Fields:
`request_id`, `founder_id`, `context_hash`, `status` (`RUNNING | COMPLETE |
FAILED`), `lease_owner`, `lease_started_epoch`, `lease_seconds`, `summary`, and
timestamps. A transaction claims/reclaims the bounded worker attempt. Cloud
Tasks task-name dedupe remains an optimization, never the durable truth.

### `evidence_checks/{report_id}` — semantic draft evidence reports (20)

One report per deterministic input hash. Rows move only `PREPARED → COMPLETE`;
terminal contents are immutable. The complete schema, ownership rules, bounded
findings, and failure statuses are binding in **20-gemma-evidence-checker.md**.
Applications store only `latest_evidence_check_id`; stale reports remain as
auditable history and are never displayed against a newer draft.

### `browser_runs/{run_id}` — browser run registry (18)

Single source of truth for live browser work; the UI Browser panel reads it.
Fields per 18 §BrowserRun contract: `run_id`, `app_name`, `user_id`,
`session_id`, `kind` (`browse`\|`fill`), `application_id` (required for fill),
`phase` (foreground fill phase or null), `goal`, `status`
(`opening`\|`active`\|`blocked`\|`stopping`\|`closed`), `close_reason`,
`current_url`, `title`,
`last_action`, `screenshot_artifact`, `action_count`, `started_at`,
`deadline_at` (ISO wall-clock — the time-box source of truth),
`version` (monotonic UI-visible mutation counter), `frame_seq`,
`browser_generation`, `owner_instance` (the process that owns the live context —
startup reconciliation may only terminalize its own runs, or a foreign run whose
durable lease has already lapsed, so overlapping revisions during a rolling
deploy never close each other's live work), `lease_generation`, `expires_at`,
`blocked_reason`,
`created_at`, `updated_at`. Runs orphaned by a restart are reconciled to
`closed`/`restart` on startup and session wake from every nonterminal state
(`opening`, `active`, `blocked`, `stopping`) — never polled.

Executed actions live in the subcollection
`browser_runs/{run_id}/actions/{action_id}`: `{seq, proposal, status:
PREPARED | SUCCEEDED | FAILED | UNCERTAIN, result_ref, uncertainty_reason,
created_at}` — the
crash-safe, at-most-once ledger (18 §Action model).

Immutable observation frames live in
`browser_runs/{run_id}/frames/{frame_seq}`: `{seq, run_id, run_version,
phase: nav | before | after | blocked | closed, url, title, action, artifact,
created_at}`. The durable frame write and latest RunView update commit before
the in-app event is published (22); events are hints, never the source of truth.

### Session-resource projections (23)

Three app-owned projections make durable work discoverable from conversations;
complete field contracts are binding in **23-session-resources-and-global-search.md**:

- `resource_index/{resource_id}` — one searchable row per logical
  founder-visible resource; deterministic id
  `r_ + sha256("resource-index:v1" + founder_id + resource_type + canonical_ref)[:32]`.
- `session_resource_links/{link_id}` — immutable many-to-many occurrence
  linking a resource to a conversation; tombstoned in place via `deleted_at`,
  never physically deleted by retention (deterministic ids would resurrect).
- `session_catalog/{session_id}` — rebuildable conversation search projection;
  ADK session events (Cloud SQL/SQLite) remain the transcript authority.

`services/firestore.py` owns the **collection registry**
(`TOP_LEVEL_COLLECTIONS` / `SUBCOLLECTIONS`); `tests/unit/test_collection_registry.py`
fails when any `collection("…")` literal in `services/`, `app/`, or `agents/`
is unregistered. Composite indexes for these projections live in
`infra/firestore.indexes.json`.

## ADK session state keys

Written via `tool_context.state`. Injected into instructions by name (see 04).

| Key | Prefix semantics | Contents |
|---|---|---|
| `current_step` | session | one of: `IDLE`, `TRIAGE`, or an application state |
| `active_application_id` | session | which application is in focus |
| `active_opportunity_id` | session | opportunity being discussed |
| `active_program_requirements` | session | requirements list for the interviewer |
| `opportunity_readiness` | session | verified metadata status; missing portal URL/materials are explicit blockers, never inferred |
| `active_attachments` | session | max 8 owner/session-resolved attachment records; server-issued refs, scope, provenance, and ingestion status only |
| `checklist_status` | session | compact map `{item_key: status}` for instruction injection |
| `pending_signals` | list | events the agent is dormant waiting on, e.g. `["founder_reply", "portal_confirmation"]` |
| `current_section` | session | section key being drafted/reviewed |
| `browser_status` | session | advisory projection of the foreground browser run (18/22): `{active: bool, kind: "browse"\|"fill"\|null, run_id, version, frame_seq, status, phase, url, goal, last_action: {seq, kind, target, at}\|null}` — self-heals from Firestore `browser_runs`; phase makes credentialed fill work visible without exposing secrets |
| `platform:hiring_role_proposal` | session | Exact bounded role package shown in this conversation before internal DRAFT creation; never approval, publication, candidate, or provider authority |
| `today` | session | today's date (ISO), refreshed every turn by the callback — the agent's clock |
| `user:profile_id` | user-scoped | founder profile doc id (survives across sessions) |
| `user:prefs` | user-scoped | small map of founder interaction prefs |
| `app:workflow_id` | app-scoped | active workflow id |
| `temp:*` | invocation-only | scratch and response evidence (`temp:effect_failures`, `temp:completion_receipts`, exact pipeline counts) — never workflow truth and never relied on later |

Initialize all keys in `before_agent_callback` (see 03/04) so instructions never
render an empty placeholder.

**Three user ids coexist — seed for all of them.** `adk web` hardcodes
`userId=user` (reference-lab note), eval sets seed `user_id="eval_founder"`
(11), and the UI uses the real `founder_id`. Because `user:`-scoped state —
including the `user:profile_id` pointer — is keyed by user id,
`scripts/seed_demo.py` seeds the Founder Profile under **all three ids**;
otherwise dev inspection in `adk web` and the Day-9 evals show an empty
profile and look broken.

## Artifacts (GCS / local file service)

Binary or large payloads never enter the prompt; they are saved with
`tool_context.save_artifact(...)` and referenced by filename in state.

| Artifact | Name pattern | Produced by |
|---|---|---|
| Source page snapshot | `source_{dedup_hash}.txt` | scout fetch |
| Guideline PDF text | `guidelines_{dedup_hash}.txt` | scout PDF parse |
| Founder deck/attachments | `attachment_{founder_id}_{name}` | upload endpoint |
| Form-fill screenshot | `fillshot_{application_id}_{ts}.png` | form-filler |
| Browse page text | `page_{run_id}_{seq}.txt` | browse tools (18) — `excerpt_ref` char ranges point into this |
| Browser live frame | `browserframe_{run_id}_{frame_seq}.jpg` | runtime projection (22); authenticated, seven-day retention |
| Browser milestone evidence | `pageshot_{run_id}_{seq}_{blocked\|final}.png` | browse/runtime (18/22); application-artifact retention |
| Vision recon map | `form_map_{application_id}.json` | form-filler (Tier 1 recon, 09) |
| Application pack PDF | `pack_{application_id}_{version}.pdf` | drafter (`generate_application_pack`, 05) |
| Founder voice note | `voicenote_{founder_id}_{ts}.webm` | `submit_voice_note` (05) |
| Company document | `companydoc_{founder_id}_{ts}` | `ingest_document` (05) — decks, plans, models, past applications |

**North-star migration rule (21):** each implementation phase MUST update this
data model and the accessor-coverage registry in the same change: Phase 1A adds
`workflow_runs` with plans/events/current projection; Phase 1B adds step
attempts, waits, and `action_ledger`; Phase 1C adds `founder_inbox`; Phase 2 adds
scoped artifact metadata. Future acceptance checks MUST enumerate required
records or derive them from a reviewed registry; they MUST NOT restore a
hard-coded collection count.

## Acceptance checks

- [ ] `services/firestore.py` exposes typed accessors for every current
      top-level collection enumerated above: `opportunities`, `applications`,
      `profiles`, `ingestions`, `feedback`, `approvals`, `audit`,
      `evidence_checks`, `browser_runs`, and `discovery_requests`. Coverage is asserted from this
      explicit registry/list, never from an unauditable numeric count. Creating
      an opportunity twice with the same `dedup_hash` returns the existing id
      (no dupes); choosing the same founder/opportunity twice returns one
      application even across sessions.
- [ ] `state_schema.py` defines constants for every state + key above; no string literals for states outside this file.
- [ ] Saving a 5 KB source page stores an artifact and returns a ≤ 300-char summary (never the full text into the conversation).
