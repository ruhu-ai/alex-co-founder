# Co-Founder — agent entry point

All implementation is specified in `docs/` — read `docs/README.md` first, then
`docs/01-architecture.md`. Build in the order of `docs/14-build-plan.md`; each
spec doc ends with acceptance checks that define "done" for that work item.

## Canonical branch policy

`main` is the only canonical integration branch. All further application code,
tests, migrations, runtime/build configuration, and the documentation that
governs them must start from the current `main` and finish as verified commits
on `main` in the same task. Temporary branches, detached checkouts, and isolated
worktrees may be used for review or testing, but accepted work must not remain
only there and must not replace or force-update `main`. When `main` has unrelated
in-progress changes, stage only the task-owned paths and preserve the rest.

Never commit `.env`, secrets, generated outputs, or optional reference repos.
Historical evidence may name former branches or worktrees; those names record
past execution and do not authorize new branch-only development.

Binding principles (docs/README.md):

1. Durable workflow/domain/action state authorizes everything. Reconciled
   session state grounds model inference; chat history authorizes nothing.
2. Tools return errors as data — never raise to the model.
3. Dormancy by default — no polling; events wake the agent via `state_delta`.
4. Every external action is idempotent and audited.
5. Autonomous by default — escalate only when blocked (conflict, missing fact,
   ambiguity); human approval still precedes every irreversible action
   (server-resolved tokens).
6. Docstrings are load-bearing — ADK builds tool schemas from them.
7. Guards live in code, not prompts — callbacks and tool-internal checks.
8. Isolated workers see nothing — `include_contents="none"` for the distiller.

Founder-facing UI work is governed by `docs/16-design-system.md`: consume
semantic tokens only, stay on the type scale, icons come from the generated
Phosphor sprite (`scripts/build_icons.py`), and `scripts/check_contrast.py`
must pass.

Conventions: generic core naming (`Opportunity`, `Application`, never
`GrantTracker`); every folder under `agents/` must be a valid agent package;
secrets fetched by name at execution time; user ids `user` / `eval_founder` /
demo founder must all be seeded (docs/02).

Optional reference repos are read-only pattern-mining inputs and are never
imported or required by this project. When available outside the repository,
they may include `google/adk-python`, `andrewyng/openworker`, `cline/cline`,
`GoogleCloudPlatform/generative-ai`, `livekit-examples`, and `pipecat-ai`.
