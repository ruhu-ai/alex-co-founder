# Alex companion specifications integration record

**Recorded:** 2026-08-28
**Integration branch:** `codex/spec40-gate-f-offline`
**Purpose:** pin the exact documentation basis for offline Spec 40 Gate F S0/S1

The companion documents were preserved byte-for-byte from the previously dirty
main worktree, committed on its preservation branch, and cherry-picked into the
isolated Gate F branch. No combined or model-rewritten third version was
created. The integrated file hashes are:

| Document | Integrated SHA-256 |
|---|---|
| `36-unified-alex-product-experience.md` | `af4667c2b3182601b9f7ce0ac3dbde751070949a229ff544fb2326eb83768689` |
| `37-production-skills-system.md` | `74e69a887f2be19d8b783cdc056d9fa381ff2168e384f10e0744ff83483f4742` |
| `38-user-facing-vision.md` | `07941f7878cf03faa2b502c4fbce1cdfc3a523688a44fa9633968c518c152670` |
| `39-durable-cross-session-memory.md` | `9ad20f819e0af5160f885976cb0e272803f731456438c01f19f4da250fe51806` |
| `40-durable-background-work.md` | `8c2beaaa20691a0410049a03705a788ed8a4f32b0c1df6c68fea7dca456b86b0` |

Doc 37's §2.3 table records hashes of earlier reviewed source snapshots before
integration reconciliation. Those historical source hashes are not silently
relabelled as current file hashes. This record and the machine-readable Gate F
plan pin the actual integrated files used by the compiler and structural
qualification work.

This integration grants no runtime authority. Doc 37 remains a proposed
architecture. The one `DRAFT` skill completed its separately approved,
synthetic-only offline qualification with a pinned tool-less model, but runtime
selection, durable admission, deployment, cloud mutation, and canary remain
disabled and separately gated.

The Doc 37 and Doc 40 hashes above include the later Founder policy amendment
allowing one exact release-stage approval per rollout gate while preserving all
per-skill technical checks. Their earlier byte-preserved integration hashes
remain in Git history and are not reused as current hashes.
