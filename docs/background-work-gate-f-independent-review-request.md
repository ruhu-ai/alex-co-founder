# Spec 40 Gate F independent pre-run review request

**Recorded:** 2026-08-29  
**Status:** `PENDING_INDEPENDENT_REVIEW`  
**Execution authority:** none

This packet asks a reviewer independent of the implementation author to decide
whether the frozen, synthetic-only Gate F qualification may make its bounded
model calls. It is not a model-output review, runtime authorization, deployment
approval, canary decision, or permission to change any frozen threshold.

## Exact reviewed inputs

| Input | Frozen identity |
|---|---|
| Founder stage approval | `sha256:f3dc686f6cc3b0a2fa9f1b3e0b6caa6432b69c87e9c36146592839d690e60190` |
| Skill | `documents.produce-grounded-artifact@1.0.0` |
| Skill definition | `sha256:440a035848b8034fc9c8ca4fc250439b06b26bc799cb9101eb3a7896fbf51c67` |
| Compiled catalog semantic hash | `sha256:ac3fd134808f32a617d21f4c176b0e47afed1abbc13f22a2af7eb6ee83cabf90` |
| Qualification plan | `sha256:d267118c4f869637b28068fbb2e19bfd68b64f236c839531376de24921b2cc39` |
| 40-case fixture bundle | `sha256:897f8094f6a196028f33dd94149b5552aa8f0ae6c01d3f33b517ea0ad33eb20e` |
| Model policy | `sha256:b94e30be5b2d774a0f2e523c0eb98bcf3766640c55e71f2682ce53529048ecad` |
| Model | `publishers/google/models/gemini-3.6-flash` in `global` |

Provider evidence is content-free in
`skills/approvals/spec40-gate-f-provider-preflight.json`. It proves the exact
project, enabled Vertex API, ADC quota project, and model availability without
performing inference. Cost evidence is in
`skills/approvals/spec40-gate-f-cost-preflight.json`.

## Required independent checks

The reviewer must explicitly verify all of the following:

- the approval is unexpired and every frozen hash above matches;
- fixtures are synthetic or redistributable and contain no real-user content;
- the model receives no tools, grounding, web, connectors, memory, approvals,
  external data, secrets, or runtime authority;
- calls are bounded to at most 40, with zero provider retries, 120-second call
  timeout, and a $5 maximum estimated cost;
- the runner makes zero calls when any hash or technical gate fails;
- every response is closed-schema validated, same-artifact citation validated,
  output-size bounded, and checked for injected action/tool/URL content;
- raw provider errors, credentials, prompts, and responses do not enter normal
  logs; retained outputs are synthetic and local-review-only;
- qualification success still requires a separate independent review of the
  generated outputs against the predeclared numeric thresholds; and
- no result can activate a skill, route, worker, cloud resource, canary,
  approval path, or external effect.

## Verification supplied to the reviewer

- focused Gate F runner, approval, and structural checks: **93 passed**;
- complete repository regression: **1,256 passed, 2 skipped**;
- Ruff, JSON parsing, and diff whitespace checks: passed; and
- real repository state: `independent_review_complete=false`, with tests proving
  this produces `BLOCKED`, zero adapter calls, and no output.

## Required response

The independent reviewer must create a separate immutable review record with:

- reviewer identity or accountable role and why the review is independent;
- UTC timestamp;
- every exact hash in the table above;
- verdict `PASS` or `BLOCKED`;
- findings and required remediation; and
- an explicit statement that `PASS` authorizes only the frozen synthetic
  offline model evaluation, not runtime, deployment, canary, or effects.

`NO_DATA`, a missing field, a self-review, a changed hash, or an ambiguous
verdict remains `BLOCKED`.
