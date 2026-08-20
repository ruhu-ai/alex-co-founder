"""Mock application portal (docs/09) — standalone FastAPI service.

Simulates a realistic program application portal: login, a 16-field form with
deliberate friction (file upload, dynamic select), idempotent submit, signed
webhook callbacks to the agent, and a ?v=2 mode with renamed fields to exercise
the staleness fence.
"""

import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="mock-portal")

CREDS = {"demo-founder": "demo-pass-2026"}
PORTAL_TOKEN = os.environ.get("PORTAL_WEBHOOK_TOKEN", "dev-portal-token")
AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8090")

PROGRAMS = {
    "mp-grant": {"name": "Meridian Pre-Seed Grant", "award": "$25,000", "deadline": "2026-09-30"},
    "mp-accel": {"name": "Northbeam Accelerator Fall Cohort", "award": "$120,000", "deadline": "2026-10-15"},
}

_sessions: set[str] = set()
_submissions: dict[str, dict] = {}          # idempotency_key -> submission
_saves: dict[str, dict] = {}

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


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><title>{title}</title></head><body>
<h1>{title}</h1>{body}</body></html>"""


@app.get("/", response_class=HTMLResponse)
def landing():
    items = "".join(
        f"<li><b>{p['name']}</b> — {p['award']}, deadline {p['deadline']} — "
        f"<a href='/apply/{pid}'>Apply</a></li>"
        for pid, p in PROGRAMS.items()
    )
    return _page("Program Portal", f"<ul>{items}</ul><p><a href='/login'>Sign in</a></p>")


@app.get("/login", response_class=HTMLResponse)
def login_form():
    return _page("Sign in", """
<form method="post" action="/login">
<label>Username <input name="username" type="text" required></label><br>
<label>Password <input name="password" type="password" required></label><br>
<button type="submit">Sign in</button></form>""")


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    if CREDS.get(username) != password:
        return _page("Sign in", "<p>Invalid credentials.</p>")
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
        return _page("Not found", "<p>Unknown program.</p>")
    version = "v2" if v == "2" else "v1"
    controls = []
    for f in _fields(version):
        # HTML required attr: omitted for founder-owned fields (file upload,
        # dynamic select) — the program still lists them as required (recon
        # reads that), and server-side validation tolerates them missing.
        html_required = f["required"] and f["name"] not in ("deck_upload", "founder_country")
        req = "required" if html_required else ""
        label = f"<label>{f['label']}"
        if f["type"] == "textarea":
            control = f"<textarea name='{f['name']}' {req}></textarea>"
        elif f["type"] == "select":
            options = "".join(f"<option value='{o}'>{o}</option>" for o in f["options"])
            control = (f"<select name='{f['name']}' {req}>"
                       f"<option value=''>—</option>{options}</select>")
        else:
            control = f"<input name='{f['name']}' type='{f['type']}' {req}>"
        controls.append(f"{label}{control}</label><br>")
    body = (f"<h2>{program['name']}</h2><p>Award {program['award']} · deadline {program['deadline']}</p>"
            f"<form method='post' action='/apply/{program_id}/submit?v={version}' "
            f"enctype='multipart/form-data'>{''.join(controls)}"
            f"<button type='submit'>Submit application</button></form>")
    return _page(f"Apply — {program['name']}", body)


@app.get("/api/regions/{region}")
def region_options(region: str):
    return {"countries": REGIONS.get(region, [])}


@app.post("/apply/{program_id}/save")
async def save_progress(program_id: str, request: Request):
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
        return HTMLResponse(_page("Application received",
                                  f"<p>Confirmation: {original['confirmation_id']}</p>"))
    form = await request.form()
    required = [f["name"] for f in _fields("v1") if f["required"]]
    missing = [name for name in required if name not in form or not str(form.get(name, "")).strip()]
    # file + dynamic select are founder-owned in our demo flow; tolerate them missing here
    missing = [m for m in missing if m not in ("deck_upload", "founder_country")]
    if missing:
        return _page("Incomplete", f"<p>Missing required fields: {', '.join(missing)}</p>")

    confirmation_id = f"MP-{uuid.uuid4().hex[:4].upper()}"
    submission = {"confirmation_id": confirmation_id, "program_id": program_id,
                  "fields": len(form), "submitted_at": datetime.now(timezone.utc).isoformat()}
    if idem_key:
        _submissions[idem_key] = submission

    agent_url = os.environ.get("AGENT_BASE_URL", AGENT_BASE_URL)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{agent_url}/webhooks/portal_event",
                headers={"X-Portal-Token": PORTAL_TOKEN,
                         "Content-Type": "application/json"},
                json={"user_id": "founder", "session_id": "", "application_id": "",
                      "kind": "submission_confirmed",
                      "confirmation_id": confirmation_id,
                      "detail": f"{program_id} submission confirmed"},
            )
    except Exception:
        pass  # demo: webhook failure must not break the submission page
    return HTMLResponse(_page("Application received", f"<p>Confirmation: {confirmation_id}</p>"))


@app.get("/admin/reset")
def admin_reset():
    _submissions.clear()
    _saves.clear()
    return {"status": "reset"}


@app.get("/admin/ping-agent")
async def ping_agent():
    """Pre-flight: fire a signed test event at the agent webhook; verify 200."""
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
def healthz():
    return {"status": "ok", "programs": len(PROGRAMS)}
