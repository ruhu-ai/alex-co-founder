"""Mock application portal (docs/09) — standalone FastAPI service.

Simulates a realistic program application portal: login, a 16-field form with
deliberate friction (file upload, dynamic select), idempotent submit, signed
webhook callbacks to the agent, and a ?v=2 mode with renamed fields to exercise
the staleness fence.
"""

import hmac
import html
import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="mock-portal")

CREDS = {"demo-founder": "demo-pass-2026"}
PORTAL_TOKEN = os.environ.get(
    "PORTAL_WEBHOOK_TOKEN", "" if os.environ.get("K_SERVICE") else "dev-portal-token")
AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8090")


def _admin_ok(request: Request) -> bool:
    """Admin / mailbox / save endpoints: open in local dev (the demo runs
    without a token), but in production (K_SERVICE) require the shared portal
    token via header — otherwise a publicly-deployed portal leaks verification
    tokens (/_mailbox) and lets anyone clear the double-submit ledger
    (/admin/reset). Mirrors the app's fail-closed K_SERVICE posture."""
    if not os.environ.get("K_SERVICE"):
        return True
    presented = request.headers.get("X-Portal-Token", "")
    return bool(PORTAL_TOKEN) and hmac.compare_digest(presented, PORTAL_TOKEN)

PROGRAMS = {
    "mp-grant": {"name": "Meridian Pre-Seed Grant", "award": "$25,000", "deadline": "2026-09-30"},
    "mp-accel": {"name": "Northbeam Accelerator Fall Cohort", "award": "$120,000", "deadline": "2026-10-15"},
}

_sessions: set[str] = set()
_submissions: dict[str, dict] = {}          # idempotency_key -> submission
_saves: dict[str, dict] = {}                # program_id -> saved form
_a2a_log: list[dict] = []                   # A2A negotiations (own namespace:
# never inside _saves, where /apply/a2a_log/save would collide with it)

# v1 -> v2 renamed fields (staleness-fence exercise)
FIELD_NAMES = {
    "v1": {"revenue": "revenue", "problem": "problem"},
    "v2": {"revenue": "arr_band", "problem": "problem_statement"},
}

REGIONS = {"africa": ["Kenya", "Nigeria", "Ghana", "South Africa"],
           "europe": ["UK", "Germany", "Netherlands"],
           "north-america": ["USA", "Canada"]}


def _authed(request: Request) -> bool:
    return request.cookies.get("mp_session") in _sessions


def _fields(version: str) -> list[dict]:
    names = FIELD_NAMES[version]
    return [
        {"name": "company_name", "label": "Company name", "type": "text", "required": True},
        {"name": "contact_email", "label": "Contact email", "type": "email", "required": True},
        {"name": "website", "label": "Website", "type": "url", "required": False},
        {"name": names["problem"], "label": "What problem are you solving?", "type": "textarea", "required": True},
        {"name": "solution", "label": "Describe your solution", "type": "textarea", "required": True},
        {"name": "traction", "label": "Traction so far", "type": "textarea", "required": True},
        {"name": "market_size", "label": "Market size estimate", "type": "text", "required": True},
        {"name": names["revenue"], "label": "Current revenue", "type": "text", "required": True},
        {"name": "team_size", "label": "Team size", "type": "number", "required": True},
        {"name": "founder_region", "label": "Region", "type": "select", "required": True,
         "options": list(REGIONS)},
        {"name": "founder_country", "label": "Country", "type": "select", "required": True,
         "options": []},  # dynamic: populated after region pick
        {"name": "stage", "label": "Stage", "type": "select", "required": True,
         "options": ["idea", "mvp", "pre-seed", "seed"]},
        {"name": "deadline_drive", "label": "Why this program, why now?", "type": "textarea", "required": True},
        {"name": "referral_source", "label": "How did you hear about us?", "type": "text", "required": False},
        {"name": "deck_upload", "label": "Pitch deck (PDF)", "type": "file", "required": True},
        {"name": "start_date", "label": "When can you start?", "type": "date", "required": True},
    ]


# ---------------------------------------------------------------------------
# Presentation (docs/16 §12). Deliberately NOT the Co-Founder design system:
# on camera this must read as a third-party institution the agent is operating,
# not as our own product wearing a different hat. Warm paper, forest green,
# serif masthead — the opposite of Co-Founder's cool blue-grey and system sans.
#
# LOAD-BEARING, DO NOT CHANGE (services/browser_service.py depends on it):
#   * every `name` and `type` attribute            -> `[name='...']` selectors
#   * <label> wrapping its control                 -> `e.labels[0].innerText`
#   * <option> text                                -> select_option(label=...)
#   * form action / method / enctype
# The required marker is a CSS ::after, and help text sits outside <label>, so
# that recon reads a clean field label and not "Company name * (as registered)".
# ---------------------------------------------------------------------------

STYLE = """
:root {
  --paper:#F6F4EF; --card:#FFFFFF; --sunk:#EFEDE6;
  --ink:#1C2A22; --ink2:#465349; --ink3:#5C685F;
  --rule:#D3CDBF; --field:#87918A;
  --brand:#14563D; --brand-on:#FFFFFF; --brand-tint:#E7EFEA;
  --gold:#8A6A16; --danger:#A3321F; --ok:#1B6B45;
  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, "Times New Roman", serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing:border-box; }
body {
  margin:0; background:var(--paper); color:var(--ink);
  font:16px/1.6 var(--sans); -webkit-font-smoothing:antialiased;
}
a { color:var(--brand); }
.masthead {
  background:var(--card); border-bottom:1px solid var(--rule);
  padding:18px 0; margin-bottom:32px;
}
.wrap { width:min(760px, calc(100% - 40px)); margin:0 auto; }
.masthead .wrap { display:flex; align-items:baseline; gap:14px; }
.mark { font:600 22px/1 var(--serif); letter-spacing:.01em; color:var(--brand); }
.mark span { color:var(--ink); }
.org { font-size:13px; color:var(--ink3); }
.masthead nav { margin-left:auto; font-size:14px; display:flex; gap:18px; }
h1 { font:600 30px/1.25 var(--serif); margin:0 0 8px; letter-spacing:.005em; }
h2 { font:600 21px/1.3 var(--serif); margin:0 0 6px; }
.lede { color:var(--ink2); margin:0 0 28px; font-size:16px; }
.card {
  background:var(--card); border:1px solid var(--rule); border-radius:6px;
  padding:26px 28px; margin-bottom:20px;
}
.facts { display:flex; flex-wrap:wrap; gap:6px 22px; padding:0; margin:0 0 14px; list-style:none;
         font-size:14px; color:var(--ink2); }
.facts > li { border:0; padding:0; margin:0; }
.facts b { color:var(--ink); font-variant-numeric:tabular-nums; }
.due { color:var(--gold); font-weight:600; font-variant-numeric:tabular-nums; }
.progs { list-style:none; padding:0; margin:0; }
.progs > li { border-top:1px solid var(--rule); padding:22px 0; }
.progs > li:first-child { border-top:0; padding-top:0; }
.progs > li:last-child { padding-bottom:0; }
/* ---- form ---- */
fieldset { border:0; padding:0; margin:0; }
fieldset + fieldset { margin-top:34px; border-top:1px solid var(--rule); padding-top:28px; }
legend {
  font:600 12px/1 var(--sans); text-transform:uppercase; letter-spacing:.09em;
  color:var(--ink3); padding:0 0 16px;
}
/* A legend inside a bordered fieldset would sit on the rule; float it clear. */
fieldset + fieldset > legend { padding-top:0; }
.f { margin-bottom:20px; }
.f > label { display:block; font-size:15px; }
.f .lb { display:block; font-weight:600; margin-bottom:6px; }
.f.req .lb::after { content:" *"; color:var(--danger); font-weight:400; }
.f .hint { font-size:13px; color:var(--ink3); margin:6px 0 0; }
input, textarea, select {
  width:100%; font:16px/1.5 var(--sans); color:var(--ink);
  background:var(--card); border:1px solid var(--field); border-radius:4px;
  padding:10px 12px;
}
textarea { min-height:104px; resize:vertical; }
input[type=file] { padding:9px 12px; background:var(--sunk); }
input:focus, textarea:focus, select:focus {
  outline:2px solid var(--brand); outline-offset:1px; border-color:var(--brand);
}
/* A filled field stays visibly filled — the form-fill is the thing being
   demonstrated, so the result of it has to be legible on camera. */
input[placeholder]:not([type=file]):not(:placeholder-shown),
textarea[placeholder]:not(:placeholder-shown) {
  background:var(--brand-tint); border-color:var(--brand);
}
.actions {
  display:flex; align-items:center; gap:16px;
  border-top:1px solid var(--rule); padding-top:20px;
}
button, .btn {
  font:600 15px/1 var(--sans); background:var(--brand); color:var(--brand-on);
  border:0; border-radius:4px; padding:13px 22px; cursor:pointer;
  text-decoration:none; display:inline-block;
}
button:hover { background:#0F4632; }
button:focus-visible, a:focus-visible { outline:2px solid var(--brand); outline-offset:2px; }
.note { font-size:13px; color:var(--ink3); }
.receipt {
  border-left:3px solid var(--ok); background:var(--sunk);
  padding:16px 20px; margin:0 0 20px;
}
.receipt .id { font:600 22px/1.3 var(--mono); color:var(--ok); letter-spacing:.02em; }
.alert { border-left:3px solid var(--danger); background:#FBF1EE; padding:14px 18px; margin:0 0 20px; }
.alert b { color:var(--danger); }
footer { margin:40px 0 60px; font-size:13px; color:var(--ink3); }
footer .wrap { border-top:1px solid var(--rule); padding-top:16px; }
@media (prefers-reduced-motion:reduce) { * { transition:none !important; animation:none !important; } }
"""


def _page(title: str, body: str, *, lede: str = "", nav: bool = True) -> str:
    """Full portal document. `title` becomes the <h1> and the tab title."""
    links = ('<nav><a href="/">Programmes</a><a href="/login">Sign in</a></nav>'
             if nav else "")
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Meridian Programmes</title>
<style>{STYLE}</style>
</head><body>
<header class="masthead"><div class="wrap">
  <span class="mark">Meridian<span> Programmes</span></span>
  <span class="org">Applicant portal</span>{links}
</div></header>
<main class="wrap">
  <h1>{title}</h1>
  {f'<p class="lede">{lede}</p>' if lede else ''}
  {body}
</main>
<footer><div class="wrap">Meridian Programmes Office · Applications are reviewed in the order received.</div></footer>
</body></html>"""


def _receipt(confirmation_id: str, *, replayed: bool = False) -> str:
    """The confirmation screen. This is the last frame of the demo, so the
    reference the founder has to keep is the single largest thing on it."""
    note = ("This application was already submitted — showing the original receipt."
            if replayed else
            "A copy has been emailed to the address on the application.")
    return _page(
        "Application received",
        f'<div class="receipt"><div class="note">Your confirmation reference</div>'
        f'<div class="id">{confirmation_id}</div></div>'
        f'<div class="card"><p>{note}</p>'
        f'<ul class="facts"><li>Status <b>Received</b></li>'
        f'<li>Decisions <b>within 6 weeks</b></li></ul></div>'
        f'<p><a class="btn" href="/">Back to programmes</a></p>',
        lede="We have your application. Keep the reference below — you will need it "
             "for any correspondence about this submission.")


@app.get("/", response_class=HTMLResponse)
def landing():
    items = "".join(
        f"<li><h2>{p['name']}</h2>"
        f"<ul class='facts'><li>Award <b>{p['award']}</b></li>"
        f"<li>Closes <b class='due'>{p['deadline']}</b></li>"
        f"<li>16 questions</li></ul>"
        f"<p><a class='btn' href='/apply/{pid}'>Start application</a></p></li>"
        for pid, p in PROGRAMS.items()
    )
    return _page("Open programmes",
                 f"<div class='card'><ul class='progs'>{items}</ul></div>"
                 f"<p class='note'>Already started? <a href='/login'>Sign in</a> to continue "
                 f"a saved application.</p>",
                 lede="Non-dilutive funding and structured support for early-stage founders. "
                      "You will need your pitch deck and your latest traction figures.")


@app.get("/login", response_class=HTMLResponse)
def login_form():
    return _page("Sign in", """
<div class="card"><form method="post" action="/login">
<div class="f req"><label><span class="lb">Username</span>
  <input name="username" type="text" required></label></div>
<div class="f req"><label><span class="lb">Password</span>
  <input name="password" type="password" required></label></div>
<div class="actions"><button type="submit">Sign in</button>
<span class="note">No account? <a href="/signup">Create one</a>.</span></div>
</form></div>""", lede="Sign in to start or continue an application.", nav=False)


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    ok = CREDS.get(username) == password
    if not ok:
        acct = _accounts.get(username.strip().lower())
        ok = bool(acct and acct["password"] == password and acct["verified"])
    if not ok:
        return _page("Sign in",
                     '<div class="alert"><b>We could not sign you in.</b> Check the username '
                     'and password and try again.</div>'
                     '<p><a class="btn" href="/login">Back to sign in</a></p>', nav=False)
    token = uuid.uuid4().hex
    _sessions.add(token)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("mp_session", token)
    return resp


@app.get("/apply/{program_id}", response_class=HTMLResponse)
def apply_form(program_id: str, request: Request, v: str = "v1"):
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    program = PROGRAMS.get(program_id)
    if not program:
        return _page("Programme not found",
                     '<div class="alert"><b>That programme is not open.</b> It may have closed '
                     'or the link may be wrong.</div>'
                     '<p><a class="btn" href="/">See open programmes</a></p>')
    version = "v2" if v == "2" else "v1"

    # Grouped the way an applicant reads them, not the way they are stored.
    # Field order within the form is unchanged — recon and the filler walk
    # `[name=...]`, so grouping is presentation only.
    SECTIONS = [
        ("About the company", ("company_name", "contact_email", "website")),
        ("The opportunity", (FIELD_NAMES[version]["problem"], "solution", "traction",
                             "market_size", FIELD_NAMES[version]["revenue"])),
        ("Team and location", ("team_size", "founder_region", "founder_country", "stage")),
        ("This programme", ("deadline_drive", "referral_source", "deck_upload", "start_date")),
    ]
    HINTS = {
        "company_name": "As registered, including any suffix.",
        "website": "Optional. Include https://",
        "founder_country": "Choose a region first — the list is filtered to it.",
        "deck_upload": "PDF, max 10 MB.",
        "team_size": "Full-time equivalents, founders included.",
        "start_date": "The earliest date you could join the cohort.",
    }

    by_name = {f["name"]: f for f in _fields(version)}

    def control_for(f: dict) -> str:
        # HTML required attr: omitted for founder-owned fields (file upload,
        # dynamic select) — the program still lists them as required (recon
        # reads that), and server-side validation tolerates them missing.
        html_required = f["required"] and f["name"] not in ("deck_upload", "founder_country")
        req = "required" if html_required else ""
        if f["type"] == "textarea":
            return f"<textarea name='{f['name']}' {req} placeholder=' '></textarea>"
        if f["type"] == "select":
            options = "".join(f"<option value='{o}'>{o}</option>" for o in f["options"])
            return (f"<select name='{f['name']}' {req}>"
                    f"<option value=''>—</option>{options}</select>")
        return f"<input name='{f['name']}' type='{f['type']}' {req} placeholder=' '>"

    groups = []
    for legend, names in SECTIONS:
        rows = []
        for name in names:
            f = by_name[name]
            hint = HINTS.get(name, "")
            rows.append(
                f"<div class='f{' req' if f['required'] else ''}'>"
                f"<label><span class='lb'>{f['label']}</span>{control_for(f)}</label>"
                f"{f'<p class=hint>{hint}</p>' if hint else ''}"
                f"</div>")
        groups.append(f"<fieldset><legend>{legend}</legend>{''.join(rows)}</fieldset>")

    body = (f"<div class='card'>"
            f"<ul class='facts'><li>Award <b>{program['award']}</b></li>"
            f"<li>Closes <b class='due'>{program['deadline']}</b></li>"
            f"<li><b>16</b> questions</li></ul></div>"
            f"<div class='card'>"
            f"<form method='post' action='/apply/{program_id}/submit?v={version}' "
            f"enctype='multipart/form-data'>{''.join(groups)}"
            f"<div class='actions'><button type='submit'>Submit application</button>"
            f"<span class='note'>Fields marked * are required. "
            f"Submissions are final.</span></div></form></div>")
    return _page(program["name"], body,
                 lede="Answer every required question. You can save and return before the "
                      "closing date, but a submitted application cannot be edited.")


@app.get("/api/regions/{region}")
def region_options(region: str):
    return {"countries": REGIONS.get(region, [])}


@app.post("/apply/{program_id}/save")
async def save_progress(program_id: str, request: Request):
    if not _admin_ok(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    form = await request.form()
    _saves[program_id] = dict(form)
    return {"saved": len(form)}


@app.post("/apply/{program_id}/submit")
async def submit(program_id: str, request: Request):
    if not _authed(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    idem_key = request.headers.get("idempotency-key", "")
    if idem_key and idem_key in _submissions:
        original = _submissions[idem_key]
        return HTMLResponse(_receipt(original["confirmation_id"], replayed=True))
    form = await request.form()
    # Validate against the version the form was rendered with. apply_form's
    # action carries ?v=v1|v2 (staleness-fence demo); ignoring it meant a
    # correctly re-filled v2 form (renamed fields) could never validate.
    raw_v = request.query_params.get("v") or str(form.get("v", ""))
    version = "v2" if raw_v in ("2", "v2") else "v1"
    by_label = {f["name"]: f["label"] for f in _fields(version)}
    required = [f["name"] for f in _fields(version) if f["required"]]
    missing = [name for name in required if name not in form or not str(form.get(name, "")).strip()]
    # file + dynamic select are founder-owned in our demo flow; tolerate them missing here
    missing = [m for m in missing if m not in ("deck_upload", "founder_country")]
    if missing:
        rows = "".join(f"<li>{by_label.get(m, m)}</li>" for m in missing)
        return _page("Application incomplete",
                     f'<div class="alert"><b>{len(missing)} required '
                     f'{"answer is" if len(missing) == 1 else "answers are"} still missing.</b> '
                     f'Nothing was submitted.</div>'
                     f'<div class="card"><h2>Still needed</h2><ul>{rows}</ul>'
                     f'<p class="note">Go back, complete these, and submit again.</p></div>')

    confirmation_id = f"MP-{uuid.uuid4().hex[:4].upper()}"
    submission = {"confirmation_id": confirmation_id, "program_id": program_id,
                  "fields": len(form), "submitted_at": datetime.now(timezone.utc).isoformat()}
    if idem_key:
        _submissions[idem_key] = submission

    agent_url = os.environ.get("AGENT_BASE_URL", AGENT_BASE_URL)
    session_id = request.headers.get("x-cofounder-session-id", "")
    application_id = request.headers.get("x-cofounder-application-id", "")
    user_id = request.headers.get("x-cofounder-user-id", "founder")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{agent_url}/webhooks/portal_event",
                headers={"X-Portal-Token": PORTAL_TOKEN,
                         "Content-Type": "application/json"},
                json={"user_id": user_id or "founder", "session_id": session_id,
                      "application_id": application_id,
                      "kind": "submission_confirmed",
                      "confirmation_id": confirmation_id,
                      "detail": f"{program_id} submission confirmed"},
            )
    except Exception:
        pass  # demo: webhook failure must not break the submission page
    return HTMLResponse(_receipt(confirmation_id))


# ---------------------------------------------------------------------------
# A2A protocol (docs/19 §P1.5): the portal is a REAL A2A agent — "Program
# Office" — discoverable via its agent card, answering message/send for
# requirements, deadlines, and submission status. Rules in code, no model.
# ---------------------------------------------------------------------------

_AGENT_URL = os.environ.get("MOCK_PORTAL_PUBLIC_URL", "http://127.0.0.1:8091")


def _agent_card() -> dict:
    return {
        "name": "Mock Portal Program Office",
        "description": "The program office for this grant portal: answers questions "
                       "about application requirements, deadlines and extensions, "
                       "and submission status.",
        "url": f"{_AGENT_URL}/a2a",
        "version": "1.0.0",
        "provider": {"organization": "Mock Portal Programs", "url": _AGENT_URL},
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [
            {"id": "requirements", "name": "Application requirements",
             "description": "What materials and fields an application requires",
             "tags": ["requirements", "materials", "application"],
             "examples": ["What do I need to apply for the Meridian Pre-Seed Grant?"]},
            {"id": "deadlines", "name": "Deadlines & extensions",
             "description": "Deadline dates and extension policy per program",
             "tags": ["deadline", "extension"],
             "examples": ["When is the deadline? Can it be extended?"]},
            {"id": "status", "name": "Submission status",
             "description": "Look up a submission by confirmation id",
             "tags": ["status", "confirmation"],
             "examples": ["What is the status of MP-1A2B?"]},
        ],
    }


@app.get("/.well-known/agent.json")
@app.get("/.well-known/agent-card.json")
def a2a_agent_card():
    return _agent_card()


def _program_office_answer(text: str) -> str:
    """Deterministic Program Office replies (rules in code — mock but real A2A)."""
    lowered = text.lower()
    if any(w in lowered for w in ("status", "confirmation", "received", "mp-")):
        import re as _re

        m = _re.search(r"MP-[0-9A-F]{4}", text.upper())
        if m:
            cid = m.group(0)
            for sub in _submissions.values():
                if sub["confirmation_id"] == cid:
                    return (f"Submission {cid} for program {sub['program_id']} is "
                            f"RECEIVED and under review (submitted {sub['submitted_at']}).")
            return f"No submission found with confirmation id {cid}."
        return "Share your confirmation id (format MP-XXXX) and I'll look up the status."
    if any(w in lowered for w in ("deadline", "extend", "extension", "when")):
        lines = [f"{p['name']}: deadline {p['deadline']}" for p in PROGRAMS.values()]
        return ("Deadlines — " + "; ".join(lines) +
                ". Extensions are not granted; submit before the deadline.")
    if any(w in lowered for w in ("require", "material", "need", "document", "apply", "field")):
        out = []
        for pid, p in PROGRAMS.items():
            fields = ", ".join(f["label"] for f in _fields("v1") if f["required"])
            out.append(f"{p['name']} ({p['award']}): required — {fields}.")
        return " ".join(out)
    return ("I am the Program Office for this portal. Ask me about application "
            "requirements, deadlines and extensions, or a submission status "
            "(confirmation id MP-XXXX).")


@app.post("/a2a")
async def a2a_endpoint(request: Request):
    """A2A JSON-RPC: message/send → Program Office reply as an A2A Message."""
    try:
        rpc = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "parse error"}},
                            status_code=400)
    rpc_id = rpc.get("id")
    if rpc.get("method") != "message/send":
        return {"jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": -32601, "message": f"method not found: {rpc.get('method')}"}}
    params = rpc.get("params") or {}
    message = params.get("message") or {}
    texts = [p.get("text", "") for p in message.get("parts", [])
             if p.get("kind") == "text" or "text" in p]
    question = " ".join(t for t in texts if t).strip()
    if not question:
        return {"jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": -32602, "message": "message has no text parts"}}
    answer = _program_office_answer(question)
    await _audit_a2a(question, answer)
    return {"jsonrpc": "2.0", "id": rpc_id,
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": uuid.uuid4().hex,
                "parts": [{"kind": "text", "text": answer}],
            }}


async def _audit_a2a(question: str, answer: str) -> None:
    """Every negotiation logged (mock-local; the agent side audits its own too).
    Stored in its own list, not _saves, so a /apply/a2a_log/save can't clobber
    it."""
    _a2a_log.append(
        {"q": question[:200], "a": answer[:200],
         "at": datetime.now(timezone.utc).isoformat()})

_accounts: dict[str, dict] = {}   # email -> {password, verified, token}
_mailbox: dict[str, list] = {}    # email -> [messages]


@app.get("/signup", response_class=HTMLResponse)
def signup_form():
    return _page("Create an account", """
<div class="card"><form method="post" action="/signup">
<div class="f req"><label><span class="lb">Email</span>
  <input name="email" type="email" required></label>
<p class="hint">We send a verification link here before you can sign in.</p></div>
<div class="f req"><label><span class="lb">Password</span>
  <input name="password" type="password" required></label></div>
<div class="actions"><button type="submit">Create account</button>
<span class="note">Already registered? <a href="/login">Sign in</a>.</span></div>
</form></div>""", lede="You need an account before you can start an application.", nav=False)


@app.post("/signup")
async def signup(request: Request):
    form = await request.form()
    email_addr = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    if not email_addr or not password:
        return _page("Create an account",
                     '<div class="alert"><b>Email and password are both required.</b></div>'
                     '<p><a class="btn" href="/signup">Try again</a></p>', nav=False)
    if email_addr in _accounts:
        return _page("Account already exists",
                     '<div class="alert"><b>That email is already registered.</b></div>'
                     '<p><a class="btn" href="/login">Sign in instead</a></p>', nav=False)
    token = uuid.uuid4().hex
    _accounts[email_addr] = {"password": password, "verified": False, "token": token}
    verify_url = f"http://127.0.0.1:8091/verify?token={token}"
    _mailbox.setdefault(email_addr, []).append({
        "from": "noreply@mockportal.dev",
        "subject": "Verify your account",
        "body": f"Welcome! Click to verify: {verify_url}",
        "link": verify_url,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return _page("Check your email",
                     f'<div class="card"><p>We sent a verification link to '
                     f'<b>{html.escape(email_addr)}</b>. Open it to activate the account, '
                     f'then sign in.</p>'
                     f'<p class="note">The link expires in 24 hours.</p></div>', nav=False)


@app.get("/verify")
def verify(token: str):
    for email_addr, acct in _accounts.items():
        if acct["token"] == token:
            acct["verified"] = True
            return _page("Email verified",
                                 f'<div class="receipt"><b>{html.escape(email_addr)}</b> '
                                 f'is verified.</div>'
                                 f'<p><a class="btn" href="/login">Sign in</a></p>', nav=False)
    return _page("Link not recognised",
                     '<div class="alert"><b>That verification link is not valid.</b> '
                     'It may have already been used, or it may have expired.</div>'
                     '<p><a class="btn" href="/signup">Start again</a></p>', nav=False)


@app.get("/_mailbox/{email_addr}")
def mailbox(email_addr: str, request: Request):
    """Test seam: the 'inbox' the agent polls during registration. Gated in
    production — this leaks verification tokens if left open publicly."""
    if not _admin_ok(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    return {"messages": _mailbox.get(email_addr.lower(), [])}


@app.get("/admin/reset")
def admin_reset(request: Request):
    if not _admin_ok(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    _submissions.clear()
    _saves.clear()
    _a2a_log.clear()
    return {"status": "reset"}


@app.get("/admin/ping-agent")
async def ping_agent(request: Request):
    """Pre-flight: fire a signed test event at the agent webhook; verify 200."""
    if not _admin_ok(request):
        return JSONResponse({"error": "auth required"}, status_code=401)
    agent_url = os.environ.get("AGENT_BASE_URL", AGENT_BASE_URL)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{agent_url}/webhooks/portal_event",
                headers={"X-Portal-Token": PORTAL_TOKEN, "Content-Type": "application/json"},
                json={"user_id": "founder", "session_id": "", "application_id": "",
                      "kind": "ping", "confirmation_id": "", "detail": "pre-flight"},
            )
        return {"agent_status": resp.status_code, "ok": resp.status_code == 200}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/healthz")
@app.get("/health")  # GFE intercepts /healthz at the edge; /health is reachable
def healthz():
    return {"status": "ok", "programs": len(PROGRAMS)}
