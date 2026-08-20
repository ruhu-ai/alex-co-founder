# Demo script — 4 minutes, unedited action beats (docs/14 Day 12)

Pre-record checklist (all must be green):
- [ ] `gcloud auth application-default login` done; `scripts/seed_demo.py` run
- [ ] Cloud Run: `--min-instances 1` on both services; `/healthz` pre-warmed
- [ ] Mock portal `/admin/ping-agent` returns `{"ok": true}`
- [ ] Board pre-seeded; browser windows arranged: UI | terminal logs | Cloud Console
- [ ] Mock portal `/admin/reset` hit

## Beats

| # | Time | Beat | On screen | Say (gist) |
|---|---|---|---|---|
| 1 | 0:00–0:20 | Problem | The pipeline board, empty-ish | "Solo founders re-type the same 30 answers into every grant portal and still miss deadlines. This is Co-Founder — it does the work; the founder keeps the judgment." |
| 2 | 0:20–1:00 | Discovery → shortlist | Board: seeded programs; one ARCHIVED with reason; one CRITICAL | "It found these on its own — search, program pages, a guidelines PDF. This one was archived with a *specific* reason; this one closes in 9 days, so it's flagged urgent." Click the archived reason. |
| 3 | 1:00–1:20 | Live messy-data proof | Fetch a real guidelines PDF → structured record + `raw_excerpt` citation | "Not seeded — watch it parse a real PDF into a structured record, with the citation." |
| 4 | 1:20–2:00 | Guidance + clarifying questions | Choose opportunity → interview: one gap question, one answer, checklist advances | "Before drafting, it interviews me — one question at a time, and it says *why*." |
| 5 | 2:00–2:40 | **Adaptation money shot** | Reject a draft: "too buzzwordy, drop 'revolutionary'" → distillation runs inline → next draft's notes cite the rule | "Last week I rejected the word 'revolutionary.' It stored my reason verbatim, distilled a rule, and this draft cites it. That's how it learns." Show the notes line + Firestore profile version bump. |
| 6 | 2:40–3:20 | **Action proof** | Fill form on the mock portal live; terminal logs; fill report 14/16; approval modal; submit; webhook arrives; state → FOLLOW_UP | "Now it works. It fills what it can, flags the two fields only I can answer, and stops at the gate — it never spends my reputation without asking." Approve in the modal. Show the confirmation + the webhook hitting the log. |
| 7 | 3:20–3:40 | Cloud proof | Cloud Run dashboard, Vertex AI logs, `.run.app` URL, Cloud SQL instance | "All of it on Google Cloud — scale-to-zero between events; a scheduler wakes it." |
| 8 | 3:40–4:00 | Architecture + roadmap | The mermaid diagram; one line on the workflow engine | "One engine, declarative workflows — investor outreach is a new YAML, not a rewrite. The agent does the work; the founder keeps the judgment." |

## Rules for the recording

- No cuts inside a beat. If a beat breaks, restart the beat, not the sentence.
- Never stake a beat on live search — beat 2 uses the pre-seeded board; beat 3
  uses the one real PDF fetch (fixtures exist if the network dies).
- If the `?v=2` vision-recovery beat is used (stretch): show the cached
  `form_map` artifact + truncated live segment (time-boxed 90s) — never a
  full silent recon on camera.
