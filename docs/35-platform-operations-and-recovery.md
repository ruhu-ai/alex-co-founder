# 35 — Platform operations, recovery, and governance runbook

**Status:** Binding implementation/runbook for docs/34 Phase 7. Local tests prove
the control contracts; Phase 7 is production-complete only when the dated,
content-free staging evidence below is recorded. Configuration is not recovery
proof, and a production database is never a chaos target.

## Objectives and capacity

The initial platform RPO is **15 minutes** and RTO is **60 minutes**. These
values apply to Firestore workflow/domain authority, Cloud SQL ADK sessions,
GCS artifacts, deletion tombstones, and the application/config version needed
to read them. Provider effects are never rewound from backup; restored
`EXECUTING`/`UNCERTAIN` actions reconcile forward.

`services/platform_operations.py` owns the numeric SLOs and degradation action
for command/event acknowledgement, interactive start, wake dispatch, lease
repair, action reconciliation, and projection freshness. A release requires a
non-empty healthy error-budget window. `NO_DATA` blocks release.

Queue depth and oldest-task-age limits are closed by queue name. Load shedding
rejects new low/normal-priority commands before existing waits, deliveries,
reconciliation, or provider-bound actions. It never deletes a task or reports
success. Workspace budgets are versioned, windowed, and atomically consumed
with an idempotent receipt across model tokens/cost, provider calls, artifacts,
and browser time.

Run the deterministic gate:

```bash
./scripts/check_phase7.sh
```

## Operator loop

All commands below use ADC and return identifiers/metrics only:

```bash
.venv/bin/python scripts/platform_ops.py inspect --workspace WORKSPACE_ID
.venv/bin/python scripts/platform_ops.py governance --workspace WORKSPACE_ID
.venv/bin/python scripts/platform_ops.py slo-report \
  --workspace platform --objective action_reconciliation
```

The scan paginates to exhaustion and writes one idempotent founder/operator
inbox item for each stuck run, overdue wait, expired prepared action, uncertain
action, stranded approval, or dead-letter delivery. The allowed action is
closed by issue kind:

| Issue | Allowed recovery | Forbidden |
|---|---|---|
| Stuck run | compare projection; fence stale lease; replay its pending outbox | edit events or synthesize success |
| Overdue wait | run its generation-bound timer checkpoint | resolve from chat/latest session |
| `PREPARED` action | reclaim the same action/claim after lease expiry or void before T2 | create another action or return approval to `GRANTED` |
| `EXECUTING`/`UNCERTAIN` | registered read-only provider reconciliation | call the effect endpoint again |
| Stranded approval | inspect its exact claimed action and same-action lease | reuse/release the grant |
| Dead-letter wake | requeue the same delivery ID/generation | fabricate a founder message |

Every exceptional operator action remains actor-attributed. Operators cannot
alter an immutable event, subject hash, approval decision, or provider receipt.

## Change management and rollback

Each application, policy, capability-registry, or model-routing change records
current and target versions, decision reference, explicit canary workspaces,
and thresholds. Any security denial or projection mismatch, or an error rate
over the declared bound, produces `ROLLBACK_REQUIRED` with the exact prior
versions. Promotion requires the minimum sample count. A deploy controller may
consume the decision; the service itself cannot deploy.

Rollback stops new admission, drains or fences leases, retains receipts/events,
and sends provider-bound actions forward to settlement. Capability rollback
pins the prior manifest for new runs; already-started actions settle under the
recorded version. Database migrations follow docs/34 §13.1 and record both
roll-forward and rollback drills with authority checksum parity and zero
orphaned waits/actions.

## Backup and restore configuration

Review the dry run, then explicitly name the project when applying:

```bash
./scripts/configure_phase7_reliability.sh \
  --project "$GOOGLE_CLOUD_PROJECT" --region "$GOOGLE_CLOUD_REGION"
PHASE7_CONFIRM_PROJECT="$GOOGLE_CLOUD_PROJECT" \
  ./scripts/configure_phase7_reliability.sh --apply \
  --project "$GOOGLE_CLOUD_PROJECT" --region "$GOOGLE_CLOUD_REGION"
.venv/bin/python scripts/phase7_cloud_audit.py \
  --project "$GOOGLE_CLOUD_PROJECT" --region "$GOOGLE_CLOUD_REGION"
```

The apply step enables Firestore PITR/delete protection, Cloud SQL automated
backups/PITR/delete protection and retained backups, and GCS versioning/soft
delete. It does not claim that restoration works.

Data residency is currently the configured GCP project/database/bucket/SQL
region. A new region is not added silently. Before a workspace enters a region,
policy records the allowed Firestore, SQL, GCS, Vertex/model, memory, logs, and
provider regions. A cross-region recovery target must satisfy the same policy.

## Isolated recovery drill

Run quarterly and before a storage/schema migration. Use a staging source and
pre-provisioned recovery project/database/SQL instance in another approved
region; never overwrite the source or point providers at the recovered app.

1. Record source application, policy, registry, model, schema, index and image
   versions. Seed a synthetic open wait, `PREPARED` action, `UNCERTAIN` action,
   deletion tombstone, artifact and ADK session. Disable all effect adapters.
2. Record the newest usable Firestore/SQL/GCS backup ID and age. Export the
   source under an immutable drill prefix.
3. Restore into the empty recovery targets. Deploy the exact compatible image
   with command/effect admission disabled.
4. Replay deletion tombstones before rebuilding memory/search indexes. Run
   negative retrieval probes for the deleted source.
5. Compare content-free authority counts and hashes for runs, plans, events,
   waits, attempts, approvals, actions, outboxes and domain roots. Verify
   gap-free event sequences and rebuild control projections.
6. Verify the open wait remains open, `PREPARED` retains one claim/action, and
   `UNCERTAIN` can use only read-only reconciliation. No provider call occurs.
7. Restore/query ADK sessions and artifacts. Verify the application becomes
   healthy within 60 minutes and the newest restored write is at most 15
   minutes old.
8. Store the command transcript, resource IDs, timestamps, counts and hashes in
   the restricted evidence bucket. Do not store user text or secret values.
9. Record the evidence through `platform_ops.py record-recovery`. A regional
   drill cannot pass when source and recovery regions are equal.
10. Destroy only the pre-declared recovery resources after retaining the drill
    manifest. Never delete source backups as cleanup.

## Load and chaos matrix

Run against synthetic staging workspaces with provider adapters stubbed or
test-account-bound. Each row records a `chaos_drills` receipt and must show zero
duplicate effects, orphaned waits/actions, lost receipts and projection
mismatches.

| Scenario | Injection | Required invariant |
|---|---|---|
| Queue duplication | deliver the same command/event/task concurrently | one command/run/wait transition/delivery receipt |
| Datastore contention | race run sequence, wait resolution, cancel and lease commit | one transaction wins; loser performs no effect |
| Provider outage | fail before and after T2, time out after call start | pre-T2 safely fails/voids; post-T2 is visible `UNCERTAIN` |
| Worker death | kill at every docs/34 §13 consequence boundary | same action settles/reconciles; no approval reuse |
| Partial regional failure | deny source API/queue access during recovery | admission pauses; durable receipts survive; recovery target does not replay effects |

Load evidence includes arrival rate, concurrency, queue depth/age, p50/p95/p99,
Firestore abort/retry rate, model/provider call rate, SSE reconnects, error
budget consumption and cost attribution. Test at 1×, 2× and 5× forecast. The
gate passes only when safety invariants hold at 5×; latency may degrade through
the declared load-shedding behavior.

## Privacy and governance

Workspace deletion/export drills enumerate the closed collection registry,
blobs, chunks, embeddings, memory items, sessions and projections. Completion
requires per-store receipts, negative retrieval probes and the backup residual
window. Restore replays deletion manifests before retrieval. Audit/evidence
access is least-privilege and reviewed; standard logs remain content-free.

The governance report lists the deployed policy, plan, capability, model,
memory-policy, rollout and budget versions plus counts. It is evidence, not an
authorization source.

## Phase 7 exit evidence

- [ ] Local gate and full CI/evals are green at one immutable commit.
- [ ] Read-only cloud audit passes for production and recovery staging.
- [ ] Numeric SLO windows are non-empty and within error budgets.
- [ ] 1×/2×/5× load evidence preserves safety invariants.
- [ ] All five chaos scenarios pass in staging.
- [ ] Roll-forward and rollback pass for every active migration manifest.
- [ ] Cross-region restore demonstrates RPO ≤ 15 minutes and RTO ≤ 60 minutes.
- [ ] Tombstone replay and negative deletion probes pass after restore.
- [ ] Every stuck-state kind is found and recoverable through its closed runbook.
- [ ] Governance report traces deployed policy/model/capability versions.
- [ ] Evidence IDs, owner, date, commit/revision and security approval are
      recorded in `docs/verification-notes.md` without raw user content.
