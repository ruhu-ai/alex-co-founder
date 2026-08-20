"""Remove E2E fixture data created by scripts/e2e_browser_story.py.

Deletes only records carrying E2E markers: applications linked to the seeded
Meridian opportunity or with `notes: "e2e seed"` sections, their documents +
artifact files, browser_runs, ADK sessions (s-e2e-* and the story's Act-1
sessions), matching audit rows, and the local .portal_secrets.json dev file.

Run: python scripts/cleanup_e2e.py [--yes]   (without --yes it only lists)
"""

import asyncio
import glob
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import firestore  # noqa: E402

ARTIFACTS_DIR = os.path.abspath("artifacts")


async def main() -> int:
    dry = "--yes" not in sys.argv
    founder = os.environ.get("FOUNDER_ID", "founder")

    client = firestore.get_client()
    all_apps = [doc.to_dict() | {"id": doc.id} async for doc in
                client.collection("applications").stream()]
    apps = [a for a in all_apps
            if any(s.get("notes") == "e2e seed"
                   for s in a.get("draft_sections", []))]
    # also catch Meridian-linked applications from interrupted runs
    meridian_ids = {o["id"] for o in await firestore.list_opportunities(limit=500)
                    if "Meridian" in o.get("name", "")}
    today = __import__("datetime").date.today().isoformat()
    extra = [a for a in all_apps
             if a.get("opportunity_id") in meridian_ids
             and str(a.get("created_at", "")).startswith(today)]
    apps = {a["id"]: a for a in apps + extra}
    app_ids = set(apps)

    docs = [d for d in await firestore.list_documents(founder)
            if d.get("application_id") in app_ids]
    artifacts = [d.get("artifact_name") for d in docs if d.get("artifact_name")]

    runs = [r for r in await _all_browser_runs()
            if r.get("session_id", "").startswith("s-e2e-")
            or "example.org" in str(r.get("current_url", ""))
            or "8091" in str(r.get("current_url", ""))]
    run_ids = {r["run_id"] for r in runs}

    session_ids = await _e2e_sessions(app_ids)

    print(("DRY RUN — would delete" if dry else "Deleting") + ":")
    for a in apps.values():
        print(f"  application {a['id']} (state={a.get('state')})")
    for d in docs:
        print(f"  document {d.get('artifact_name')}")
    for r in runs:
        print(f"  browser_run {r['run_id']} ({r.get('kind')}, {r.get('current_url')})")
    for s in sorted(session_ids):
        print(f"  session {s}")
    print("  .portal_secrets.json (dev credential file)")
    print("  matching audit rows")
    if dry:
        print("\nre-run with --yes to delete")
        return 0

    client = firestore.get_client()
    for app_id in app_ids:
        await client.collection("applications").document(app_id).delete()
    for d in docs:
        await client.collection("documents").document(d["id"]).delete()
    for run_id in run_ids:
        run_ref = client.collection("browser_runs").document(run_id)
        async for action in run_ref.collection("actions").stream():
            await action.reference.delete()
        await run_ref.delete()
    for name in artifacts:
        path = os.path.join(ARTIFACTS_DIR, name or "")
        if name and os.path.exists(path):
            os.remove(path)
    await _delete_audit_rows(client, app_ids, run_ids)
    _delete_sessions(session_ids)
    if os.path.exists(".portal_secrets.json"):
        os.remove(".portal_secrets.json")
    print("done.")
    return 0


async def _all_browser_runs():
    from services import firestore as fs
    client = fs.get_client()
    return [doc.to_dict() async for doc in
            client.collection("browser_runs").stream()]


async def _e2e_sessions(app_ids) -> set:
    """Session ids from the sqlite session store tied to E2E content.

    Act-1 sessions are identified by the story's unique wake phrases AND a
    same-day creation time (older sessions may mention example.org via the
    seeded non-fit opportunity — those are the founder's, keep them)."""
    ids = set()
    if not os.path.exists("sessions.db"):
        return ids
    today = __import__("datetime").date.today().isoformat()
    conn = sqlite3.connect("sessions.db")
    try:
        phrase_hits = set()
        for session_id, data in conn.execute(
                "SELECT session_id, event_data FROM events"):
            blob = data if isinstance(data, bytes) else str(data).encode()
            if b"More information" in blob or b"what this page is for" in blob:
                phrase_hits.add(session_id)
        for session_id, state, created in conn.execute(
                "SELECT id, state, create_time FROM sessions").fetchall():
            if session_id.startswith("s-e2e-"):
                ids.add(session_id)
            elif state and any(a in str(state) for a in app_ids):
                ids.add(session_id)
            elif (session_id in phrase_hits
                    and str(created or "").startswith(today)):
                ids.add(session_id)
    finally:
        conn.close()
    return ids


def _delete_sessions(session_ids) -> None:
    if not session_ids or not os.path.exists("sessions.db"):
        return
    conn = sqlite3.connect("sessions.db", timeout=10)
    try:
        for table in ("events", "sessions"):
            for session_id in session_ids:
                try:
                    conn.execute(f"DELETE FROM {table} WHERE session_id = ?"
                                 if table == "events" else
                                 f"DELETE FROM {table} WHERE id = ?",
                                 (session_id,))
                except sqlite3.OperationalError:
                    pass
        conn.commit()
    finally:
        conn.close()


async def _delete_audit_rows(client, app_ids, run_ids) -> None:
    targets = {f"applications/{a}" for a in app_ids}
    targets |= {f"browser_runs/{r}" for r in run_ids}
    async for doc in client.collection("audit").stream():
        row = doc.to_dict()
        target = str(row.get("target", ""))
        if (target in targets
                or any(a in target for a in app_ids)
                or (str(row.get("action", "")).startswith("browse_")
                    and target in ("example.org", "www.iana.org", "127.0.0.1"))):
            await doc.reference.delete()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
