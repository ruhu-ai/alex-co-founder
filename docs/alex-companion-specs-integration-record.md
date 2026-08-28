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
| `37-production-skills-system.md` | `142a1d21034f019616c048ea2e522ffe1077344ef6f711a7e7309575854703f2` |
| `38-user-facing-vision.md` | `07941f7878cf03faa2b502c4fbce1cdfc3a523688a44fa9633968c518c152670` |
| `39-durable-cross-session-memory.md` | `9ad20f819e0af5160f885976cb0e272803f731456438c01f19f4da250fe51806` |
| `40-durable-background-work.md` | `d4ed64d1ce77e80af08a7a8a8f90a1881303023e056c69e988dd8c772cbd5bf0` |

Doc 37's §2.3 table records hashes of earlier reviewed source snapshots before
integration reconciliation. Those historical source hashes are not silently
relabelled as current file hashes. This record and the machine-readable Gate F
plan pin the actual integrated files used by the compiler and structural
qualification work.

This integration grants no runtime authority. Doc 37 remains a proposed
architecture; the Founder authorization for this branch is limited to its
offline S0/S1 compiler, one `DRAFT` skill, closed contracts, and synthetic
fixtures. Models, runtime selection, durable admission, providers, deployment,
cloud mutation, and canary remain disabled and separately gated.
