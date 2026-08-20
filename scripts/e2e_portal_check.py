"""E2E smoke vs the mock portal (no model creds needed).

Prereq: mock portal running — `uvicorn mock_portal.main:app --port 8091`.
Run:    python scripts/e2e_portal_check.py
"""

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from services import browser_service, pipeline_service

PORTAL = "http://127.0.0.1:8091"


async def main() -> int:
    # 1. open + login + inspect (Tier 0 DOM path)
    r = await browser_service.open_and_login(f"{PORTAL}/apply/mp-grant",
                                             "demo-founder", "demo-pass-2026")
    assert r["status"] == "success", r
    page = r["page"]
    insp = await browser_service.inspect(page)
    assert len(insp["fields"]) == 16, f"expected 16 fields, got {len(insp['fields'])}"
    print(f"1. open/login/inspect ✓  16 fields, signature {insp['signature'][:23]}")

    # 2. fill (14/16; file upload + dynamic select are founder-owned)
    mapping = {
        "company_name": "Kweli Health", "contact_email": "amara@kweli.health",
        "website": "https://kweli.health",
        "problem": "Clinics lose 30% of chronic patients to follow-up gaps",
        "solution": "Agent-driven follow-up via SMS and WhatsApp",
        "traction": "3 clinics, 1,200 patients", "market_size": "$4B",
        "revenue": "pre-revenue", "team_size": "3", "founder_region": "africa",
        "stage": "pre-seed", "deadline_drive": "Clinics are asking for this now",
        "referral_source": "a founder friend", "start_date": "2026-10-01",
    }
    fill = await browser_service.fill(page, mapping, attachments={})
    assert fill["filled"] >= 14, fill
    print(f"2. fill ✓  {fill['filled']}/{len(mapping)}")

    # 3. idempotent submit (portal-level: retry returns the original confirmation)
    key = pipeline_service.derive_submit_key("founder", "e2e-check")
    async with httpx.AsyncClient() as client:
        await client.post(f"{PORTAL}/login",
                          data={"username": "demo-founder", "password": "demo-pass-2026"})
        bodies = []
        for _ in range(2):
            resp = await client.post(f"{PORTAL}/apply/mp-grant/submit?v=v1",
                                     data={k: v for k, v in mapping.items() if k != "website"},
                                     headers={"Idempotency-Key": key})
            bodies.append(resp.text)
    c1 = re.search(r"Confirmation: (\S+?)</p>", bodies[0]).group(1)
    c2 = re.search(r"Confirmation: (\S+?)</p>", bodies[1]).group(1)
    assert c1 == c2, f"idempotency broken: {c1} vs {c2}"
    print(f"3. idempotency ✓  retry returns original confirmation {c1}")

    # 4. staleness: v2 renames fields → signature must change
    r2 = await browser_service.open_and_login(f"{PORTAL}/apply/mp-grant?v=2",
                                              "demo-founder", "demo-pass-2026")
    insp2 = await browser_service.inspect(r2["page"])
    assert insp["signature"] != insp2["signature"], "fence blind to renamed fields"
    print("4. staleness detection ✓  v1/v2 signatures differ")

    await r["context"].close()
    await r2["context"].close()
    print("\nE2E portal check: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
