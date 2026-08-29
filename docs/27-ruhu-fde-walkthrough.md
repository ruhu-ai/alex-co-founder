# 27 — Ruhu Forward Deployment Engineer synthetic walkthrough

Run `scripts/seed_hiring_fde_demo.py` only in an authorized synthetic demo
workspace. It creates ordinary durable H0–H3 records; it has no live connector,
browser-write, email, Calendar, or deletion path.

## Preconditions

- Founder membership is provisioned with `scripts/seed_hiring_founder.py`.
- `HIRING_ENABLE_SYNTHETIC_DEMO=1`.
- `HIRING_SYNTHETIC_FIXTURE_IDS` includes `fixture_ruhu_fde_walkthrough`.
- `HIRING_SYNTHETIC_ENCRYPTION_KEY` is configured.

## Recording order

1. In the Co-Founder conversation, the founder starts the durable operation:

   ```text
   /hiring Forward Deployment Engineer for Ruhu, Inc. Full-time employee,
   based in Nigeria and working remotely.
   ```

   Alex creates a durable fixture-backed draft plus immutable proposed role
   policy. The founder reviews and activates that exact policy in Hiring
   operations; no post or email is sent by the command.
2. Open **Hiring operations** and select **Ruhu · Forward Deployment Engineer**.
   Show the approved role brief, scorecard, interview plan, policy impact, and
   manual LinkedIn receipt.
3. Show mailbox health: positive delivery and forged-header negative probes are
   both committed before the binding becomes active.
4. Open the unranked candidate board. The archived, trusted-route message has
   a durable event; the forged message is in the founder inbox.
5. Open the candidate’s Evidence Passport. Show fixed criterion order, cited
   evidence, protected-field withholding, and explicit unknowns—never a score,
   rank, or hiring recommendation.
6. Show the attributed human `HOLD` decision, then the separate withdrawal
   record. Explain that neither came from model output or elapsed time.
7. Open **Candidate data rights**, perform only an export and deletion dry-run.
   Show the inventory hash and provider limitations. Do not execute deletion in
   the demo; the withdrawn fixture remains available for review.
8. Open the causal timeline and point out `DEMO_DATA_RIGHTS_DRY_RUN`, plus the
   mailbox receipt and human-decision events. Refresh the page to demonstrate
   that it rebuilds from durable records.

## Narration boundary

“Alex did not choose a candidate or contact anyone. It preserved trusted,
job-related evidence; exposed the human decision; and kept the candidate’s
identity, withdrawal, and data-rights controls behind explicit authority.”

## Honest limitations on screen

- Every record is synthetic and fixture-marked.
- Generic candidate intake, Gmail watches, LinkedIn automation, public
  verification, and all H4 work remain disabled. The separately gated,
  hash-pinned internal-demo lane is documented in `33-founder-first-hiring-demo.md`.
- Employment, privacy, accessibility, retention, and destination-terms review
  are pending external qualified sign-off.
