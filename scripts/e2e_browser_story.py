"""Real end-to-end browser story check (docs/09, 15, 18).

Real server, real Gemini, real Chromium — no mocks. Drives the running app
over HTTP exactly the way the founder UI does.

Story under test:
  Act 1 (18): Alex opens a public webpage, navigates a link, reads it back,
              and the Browser panel state reflects the run. Also proves the
              SSRF guard refuses the loopback mock portal.
  Act 2 (09):  Alex takes the Meridian grant application to the mock portal —
              vision recon reads every question on the application page
              (form_map), then fills from approved answers (fill report).
              Interview/draft are compressed by seeding an APPROVED
              application at service level (that path is covered by the demo
              script and evals); the browser work under test is fully live.
  Act 3 (15):  Alex produces a document answering every form question.

Prereqs:
  source .venv/bin/activate; ADC credentials; Firestore reachable
  python scripts/seed_demo.py
  terminal 1: uvicorn app.main:app --port 8090
  terminal 2: uvicorn mock_portal.main:app --port 8091

Run: python scripts/e2e_browser_story.py
Watch browser progress in the app's Browser panel; automation is always headless.
"""

import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

APP = os.environ.get("E2E_APP_URL", "http://127.0.0.1:8090")
PORTAL = os.environ.get("E2E_PORTAL_URL", "http://127.0.0.1:8091")
PORTAL_HOST = "127.0.0.1:8091"
WAKE_TIMEOUT = 600.0
FAILURES: list[str] = []
REFUSAL_WORDS = ("can't", "cannot", "not allowed", "refused", "blocked",
                 "won't", "unable", "not on my")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}{(' — ' + detail) if detail else ''}",
          flush=True)
    if not ok:
        FAILURES.append(f"{name}: {detail}")


async def wake(client: httpx.AsyncClient, session_id: str, message: str) -> str:
    print(f"  → {message[:90]}", flush=True)
    try:
        resp = await client.post(
            f"{APP}/wake", json={"session_id": session_id, "message": message},
            timeout=WAKE_TIMEOUT)
        resp.raise_for_status()
    except httpx.ReadTimeout:
        print("  ✗ wake timed out (agent chain exceeded budget)", flush=True)
        FAILURES.append(f"wake timeout: {message[:60]}")
        return ""
    replies = resp.json().get("replies", [])
    text = "\n".join(replies)
    print(f"  alex: {text[:220].replace(chr(10), ' / ')}", flush=True)
    return text


async def setup_approved_application() -> tuple[str, str, str]:
    """Compress interview/draft/review: a SHORTLISTED Meridian opportunity →
    APPROVED application with founder-approved sections, plus an ADK session
    whose state points at it (what choose_opportunity + approvals would set).
    Also registers the mock portal credential in the local dev store."""
    from google.adk.sessions.database_session_service import (
        DatabaseSessionService,
    )

    from services import firestore, pipeline_service

    founder = os.environ.get("FOUNDER_ID", "founder")
    # Sweeps can leave more than one Meridian record. Take a SHORTLISTED one
    # deterministically — picking whichever happened to sort first made the run
    # depend on ambient Firestore state rather than on the seed.
    candidates = [o for o in await firestore.list_opportunities(limit=500)
                  if "Meridian" in o.get("name", "")]
    opp = next((o for o in candidates if o.get("state") == "SHORTLISTED"), None)
    assert opp, (
        "no SHORTLISTED Meridian opportunity — run scripts/seed_demo.py "
        f"(found {[(o['id'][:8], o.get('state')) for o in candidates]})")

    chosen = await pipeline_service.choose_opportunity(founder, opp["id"])
    assert chosen["status"] == "success", chosen
    app_id = chosen["application_id"]

    sections = [
        {"section_id": f"sec-{key}", "section_key": key, "status": "APPROVED",
         "version": 1, "notes": "e2e seed", "content": content}
        for key, content in {
            "company": "Ruhu, Inc. — https://ruhu.ai — founded 2026-01, "
                       "pre-seed, pre-revenue. Contact: amara@ruhu.ai.",
            "problem": "Clinics across Africa lose up to 30% of chronic-care "
                       "patients to follow-up gaps because staff cannot call "
                       "thousands of patients in their own languages.",
            "solution": "Ruhu is a voice-AI platform for multilingual "
                        "conversational agents: Agent Canvas (no-code builder), "
                        "Smart Insights (real-time analytics), Voice Experience "
                        "(accent/dialect recognition) — live across 20+ major "
                        "African languages (~80% of the continent).",
            "traction": "Live platform covering 20+ African languages; pilot "
                        "program open with fully-supported onboarding; waitlist "
                        "building ahead of public launch. Team of 2.",
            "market": "Addressable market ~$4B across African digital health "
                      "and customer-operations voice automation.",
            "motivation": "Clinics are asking for this now; the grant funds "
                          "the pilot's evaluation phase. Heard about the "
                          "program from a founder friend. Target start "
                          "2026-10-01.",
        }.items()
    ]
    await firestore.update_application(
        app_id, state="APPROVED", draft_sections=sections)

    session_id = f"s-e2e-{os.urandom(4).hex()}"
    db = DatabaseSessionService(db_url="sqlite+aiosqlite:///sessions.db")
    await db.create_session(
        app_name="co_founder", user_id=founder, session_id=session_id,
        state={
            "current_step": "APPROVED",
            "active_application_id": app_id,
            "active_opportunity_id": opp["id"],
            "active_program_requirements": opp.get("required_materials", []),
        })

    secrets_path = os.environ.get("PORTAL_SECRETS_FILE", ".portal_secrets.json")
    secrets = {}
    if os.path.exists(secrets_path):
        secrets = json.loads(open(secrets_path).read())
    secrets[PORTAL_HOST] = {"email": "demo-founder", "password": "demo-pass-2026"}
    with open(secrets_path, "w", encoding="utf-8") as fh:
        json.dump(secrets, fh)

    return app_id, session_id, opp["id"]


async def main() -> int:
    async with httpx.AsyncClient(timeout=WAKE_TIMEOUT) as client:
        # ---- Act 0: services up, demo seeded -------------------------------
        print("Act 0 — services and seed data", flush=True)
        check("app serving UI",
              (await client.get(f"{APP}/")).status_code == 200)
        check("mock portal up",
              (await client.get(f"{PORTAL}/healthz")).status_code == 200)

        # ---- Act 1: browse a public page (docs/18) -------------------------
        print("\nAct 1 — Alex browses a public page (docs/18)", flush=True)
        sid = (await client.post(f"{APP}/session/new")).json()["session_id"]
        reply = await wake(client, sid, "Open https://example.org and tell me "
                                        "in one sentence what this page is for.")
        refused = any(w in reply.lower() for w in REFUSAL_WORDS)
        check("open_page + read_page answered",
              not refused and "example" in reply.lower(),
              "refused — is BROWSE_OPEN_WEB=true set for dev?" if refused else "")
        state = (await client.get(
            f"{APP}/api/browser/state", params={"session_id": sid})).json()
        browse = state.get("browse")
        check("Browser panel shows the run", bool(browse and browse.get("url")),
              f"url={browse and browse.get('url')}")
        check("screenshot artifact linked",
              bool(browse and browse.get("screenshot_url")))

        reply = await wake(client, sid, "Follow the 'More information' link on "
                                        "that page and tell me where you landed, "
                                        "then close the browser.")
        check("browser_action navigated to iana.org",
              "iana" in reply.lower(),
              reply[:120] if "iana" not in reply.lower() else "")

        reply = await wake(client, sid, "Open http://127.0.0.1:8091/apply/mp-grant "
                                        "and fill out the form for me.")
        check("SSRF guard refuses loopback browse",
              any(w in reply.lower() for w in REFUSAL_WORDS))

        # ---- Act 2: application page → recon → fill (docs/09) --------------
        print("\nAct 2 — application page: recon reads every question (docs/09)",
              flush=True)
        app_id, sid2, _opp_id = await setup_approved_application()
        print(f"  seeded APPROVED application {app_id} (session {sid2})",
              flush=True)
        await wake(client, sid2, "The application is APPROVED. Transfer to the "
                                 "form-filler now: call open_portal with "
                                 "application_url http://127.0.0.1:8091/apply/mp-grant "
                                 "(the URL from the opportunity record), then "
                                 "map_form_requirements to read every question "
                                 "the form asks, then fill_fields from my "
                                 "approved answers.")
        detail = (await client.get(
            f"{APP}/api/applications/{app_id}",
            params={"session_id": sid2})).json()
        report = detail.get("form_fill_report") or {}
        if not report:  # debug aid: dump the transcript on failure
            transcript = (await client.get(f"{APP}/api/chat/{sid2}")).json()
            for message in transcript.get("messages", [])[-6:]:
                print(f"    [{message['role']}] {message['text'][:180]}",
                      flush=True)
        check("fill report written (recon + fill ran)", bool(report),
              f"filled {report.get('filled')}/{report.get('total')}"
              if report else "no report")
        check("partial success is first-class (needs_human listed)",
              bool(report.get("needs_human")),
              f"{len(report.get('needs_human', []))} field(s) for the founder"
              if report else "")
        fill_run = (await client.get(
            f"{APP}/api/browser/state", params={"session_id": sid2})).json()
        check("Browser panel surfaced the fill run",
              fill_run.get("fill") is not None
              or fill_run.get("browse") is not None)
        recorded = (detail.get("form_questions") or [])
        check("the form's questions were recorded for the drafter",
              len(recorded) >= 10,
              f"{len(recorded)} question(s) persisted — the document can only "
              "answer what was captured here")

        # ---- Act 3: document with responses (docs/15) ----------------------
        print("\nAct 3 — document answering every question (docs/15)",
              flush=True)
        await wake(client, sid2, "Create a docx document titled 'Meridian "
                                 "Application Responses' that answers every "
                                 "question on the form, using my approved "
                                 "answers.")
        docs = (await client.get(f"{APP}/api/documents")).json()
        rows = docs.get("documents", docs if isinstance(docs, list) else [])
        match = [d for d in rows if "Meridian" in str(d)]
        check("responses document produced", bool(match),
              match[0].get("artifact_name", "") if match else "none found")
        if match and match[0].get("artifact_name"):
            dl = await client.get(
                f"{APP}/api/artifacts/{match[0]['artifact_name']}/download")
            check("document downloadable",
                  dl.status_code == 200 and len(dl.content) > 2000,
                  f"{len(dl.content)} bytes")

            # A document that exists is not a document that is correct. These
            # two checks are the actual requirement: it must answer every
            # question the form asks, and every word of it must trace back to
            # approved sections or profile facts.
            await check_document_is_grounded(dl.content, app_id)

        # ---- Act 4: adaptation, live (docs/06, docs/11) ---------------------
        # The category rubric turns on this loop, and the demo has been showing
        # it from a seeded rule. Here the rule is distilled by the real model
        # from a real rejection, and the next draft has to honour it.
        print("\nAct 4 — the founder rejects; the next draft adapts (docs/11)",
              flush=True)
        await run_adaptation_act(client)

    return _finish()


async def run_adaptation_act(client) -> None:
    from services import firestore, profile_service

    founder = os.environ.get("FOUNDER_ID", "founder")
    banned = "revolutionary"

    app_id, sid, _opp = await setup_approved_application()
    section_id = "sec-traction-live"
    await firestore.update_application(
        app_id, state="AWAITING_REVIEW",
        draft_sections=[{
            "section_id": section_id, "section_key": "traction",
            "content": ("Ruhu's revolutionary platform is transforming clinics "
                        "across Africa."),
            "word_count": 9, "notes": "", "status": "DRAFTED", "version": 1}])

    before = await profile_service.get_voice_rules(founder)
    before_ids = {r.get("id") for r in before}

    reason = (f"Never say '{banned}' — it sounds like a scam pitch. "
              "Write plainly and let the numbers carry it.")
    fb = await client.post(f"{APP}/api/feedback", json={
        "session_id": sid, "application_id": app_id, "section_id": section_id,
        "type": "reject", "reason": reason, "edited_text": ""})
    check("rejection accepted", fb.status_code == 200, f"HTTP {fb.status_code}")

    # The distiller runs synchronously on this path, so the rule must exist now.
    after = await profile_service.get_voice_rules(founder)
    fresh = [r for r in after if r.get("id") not in before_ids]
    made_rule = [r for r in fresh if banned in json.dumps(r).lower()]
    check("live distiller turned the reason into a voice rule",
          bool(made_rule),
          json.dumps(made_rule[0])[:110] if made_rule else
          f"{len(fresh)} new rule(s), none about {banned!r}")

    # The evidence must survive verbatim — a paraphrase cannot be cited later.
    # Punctuation gets normalised on the way through (em dashes, curly quotes),
    # so match on a distinctive run of words rather than a literal slice.
    def _words(text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9']+", text.lower()))
    fingerprint = " ".join(_words(reason).split()[:6])
    check("the founder's words are kept verbatim as evidence",
          any(fingerprint in _words(json.dumps(r, ensure_ascii=False)) for r in fresh),
          f"no rule carries {fingerprint!r}")

    await wake(client, sid, f"Redraft the '{banned}' traction section now, "
                            "applying my feedback. Save it with save_draft_section.")

    app = await firestore.get_application(app_id) or {}
    sections = app.get("draft_sections", [])

    rejected = next((s for s in sections if s.get("section_id") == section_id), None)
    check("the rejected draft is marked as needing changes",
          (rejected or {}).get("status") == "CHANGES_REQUESTED",
          f"status is {(rejected or {}).get('status')!r}")

    # Anything the drafter wrote after the rejection, under whatever key it chose.
    fresh_drafts = [s for s in sections if s.get("section_id") != section_id]
    clean = [s for s in fresh_drafts if banned not in str(s.get("content", "")).lower()]
    check("a fresh draft exists that drops the rejected word",
          bool(clean),
          f"{len(fresh_drafts)} new draft(s), none free of {banned!r}"
          if fresh_drafts else "no redraft was saved")

    # The money shot: the agent saying *why* the draft reads the way it does.
    citing = [s for s in clean if banned in str(s.get("notes", "")).lower()]
    check("the redraft cites the feedback that shaped it",
          bool(citing),
          str(citing[0].get("notes", ""))[:100] if citing
          else "; ".join(str(s.get("notes", ""))[:60] for s in clean) or "no notes recorded")


async def check_document_is_grounded(blob: bytes, app_id: str) -> None:
    """Coverage + grounding of the produced document.

    produce_document takes a model-authored `spec` and renders it; grounding is
    asked for in the tool docstring, i.e. in a prompt. docs/README.md principle
    7 says guards live in code, not prompts — so until that tool grows a real
    guard, this is where the invariant is enforced.
    """
    import io

    from docx import Document

    from mock_portal.main import _fields
    from services import firestore, profile_service

    doc = Document(io.BytesIO(blob))
    body = "\n".join(p.text for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            body += "\n" + " | ".join(c.text for c in row.cells)
    low = body.lower()

    # --- coverage: every question on the form is addressed ------------------
    # A good document paraphrases: "What problem are you solving?" becomes a
    # "Problem" heading. Accept either the field's own name tokens (problem,
    # market_size -> market + size) or the label's distinctive words, so the
    # check flags real gaps instead of good writing.
    missing = []
    for field in _fields("v1"):
        label = field["label"]
        name_tokens = [t for t in field["name"].split("_") if len(t) > 2]
        label_words = [w for w in re.sub(r"[^a-z ]", "", label.lower()).split() if len(w) > 3]
        covered = (
            label.lower() in low
            or (name_tokens and all(t in low for t in name_tokens))
            or (label_words and all(w in low for w in label_words))
        )
        if not covered:
            missing.append(label)
    total = len(_fields("v1"))
    check(f"document answers every question on the form ({total - len(missing)}/{total})",
          not missing, ", ".join(missing[:4]) + ("…" if len(missing) > 4 else ""))

    # --- grounding: the applicant is the founder's company, not the programme
    profile = await profile_service.get_profile(os.environ.get("FOUNDER_ID", "founder"))
    facts = (profile or {}).get("facts", {}) or {}
    company = str(facts.get("company_name", "")).strip()
    check("document names the founder's company", bool(company) and company.split(",")[0].lower() in low,
          f"expected {company!r}")

    app = await firestore.get_application(app_id)
    opp = await firestore.get_opportunity(app.get("opportunity_id", "")) if app else None
    programme = (opp or {}).get("name", "")
    # The programme name belongs in the title, never as the applicant.
    first_word = programme.split()[0].lower() if programme else ""
    as_applicant = bool(first_word) and (
        f"company name\n{first_word}" in low or f"{first_word}.com" in low
        or f"@{first_word}." in low)
    check("programme name is not used as the applicant company",
          not as_applicant, f"{programme!r} appears as the company")

    # --- grounding: prose traces to approved sections or profile facts ------
    # Judge by exactly the definition the guard enforces, or the two drift and
    # one of them is silently wrong.
    from agents.co_founder.callbacks import _grounding_source

    source_text, _company, _programme = await _grounding_source(
        os.environ.get("FOUNDER_ID", "founder"), app_id)
    source = source_text.lower()
    # Numbers are the cheapest fabrication to detect and the most damaging to
    # publish: a grant reviewer can check them.
    doc_numbers = set(re.findall(r"\b\d[\d,]{1,}\b", body))
    invented = sorted(n for n in doc_numbers if n.replace(",", "") not in source.replace(",", ""))
    check("no invented figures", not invented,
          "not in approved answers or profile: " + ", ".join(invented[:6]) if invented else "")


def _finish() -> int:
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"E2E browser story: {len(FAILURES)} FAILURE(S)")
        for failure in FAILURES:
            print(f"  ✗ {failure}")
        return 1
    print("E2E browser story: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
