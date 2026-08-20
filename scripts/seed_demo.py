"""Seed demo data (docs/02, 06, 14).

CRITICAL (docs/02 §user ids): the Founder Profile is seeded under ALL THREE
user ids — "user" (adk web hardcodes it), "eval_founder" (eval sets), and the
demo founder_id (UI) — or adk web inspection and evals show an empty profile.

Requires ADC + Firestore. Run: python scripts/seed_demo.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import firestore, pipeline_service  # noqa: E402

USER_IDS = ["user", "eval_founder", os.environ.get("FOUNDER_ID", "founder")]

PROFILE = {
    "facts": {
        "company_name": "Ruhu, Inc.",
        "website": "https://ruhu.ai",
        "founded": "2026-01",
        "stage": "pre-seed",
        "sector": "voice AI / multilingual conversational AI",
        "geography": "Africa",
        "traction_summary": "live platform covering 20+ major African languages (~80% of the continent); pilot program open, waitlist building",
        "team_size": "2",
        "arr_band": "pre-revenue",
    },
    "voice_rules": [
        # The seeded rejection — powers the adaptation money shot (docs/06):
        # week-1 feedback exists before the demo, so the week-2 draft can cite it.
        {"id": "vr_seed", "rule": "never use the word 'revolutionary'",
         "evidence": "founder rejection 2026-08-15: 'sounds like a scam pitch'",
         "created_at": "2026-08-15T00:00:00+00:00", "active": True},
    ],
    "canonical_answers": [
        {"question_key": "describe_traction",
         "text": "Founded Jan 2026. Live platform — Agent Canvas (no-code voice-agent builder), Smart Insights (real-time analytics), Voice Experience (accent/dialect recognition) — covering 20+ major African languages, ~80% of the continent. Pilot program open with fully-supported onboarding; waitlist building ahead of public launch.",
         "tags": ["traction", "metrics"], "approved_at": "2026-08-15T00:00:00+00:00",
         "times_used": 1, "last_used": "2026-08-15", "version": 1},
        {"question_key": "describe_team",
         "text": "Ijidai — Founder, CEO & CTO (Cambridge MBA; ex-BP, Equinor; conversational AI infrastructure). Gabriel — Head of Data & Model Evaluation (PhD, Manchester; ex-NatWest, BAT; low-resource language modeling in regulated environments).",
         "tags": ["team"], "approved_at": "2026-08-15T00:00:00+00:00",
         "times_used": 0, "last_used": None, "version": 1},
    ],
    "rejection_history": [],
    "decision_patterns": [
        {"id": "dp_seed", "pattern": "prefers non-dilutive funding",
         "evidence": "chose grant programs over equity accelerators twice"},
    ],
}

OPPORTUNITIES = [
    {  # strong fit for the seeded profile
        "name": "Meridian Pre-Seed Grant", "source_url": "http://127.0.0.1:8091/",
        "source_type": "web_page", "award": "$25,000", "deadline": "2026-09-30",
        "eligibility": ["pre-seed", "Africa-based", "non-dilutive"],
        "application_url": "http://127.0.0.1:8091/apply/mp-grant",
        "required_materials": ["problem statement", "traction summary", "deck"],
        "description": "Non-dilutive grant for early-stage African startups.",
        "raw_excerpt": "The Meridian Pre-Seed Grant awards $25,000 non-dilutive…",
    },
    {  # obvious non-fit (the matchmaker must archive it with a specific reason)
        "name": "Growth-Stage Energy Infrastructure Fund",
        "source_url": "https://example.org/energy", "source_type": "web_page",
        "award": "$2,000,000", "deadline": "2026-12-01",
        "eligibility": ["Series B+", "energy infrastructure", "$1m+ ARR"],
        "application_url": "https://example.org/energy/apply",
        "required_materials": ["audited financials", "3-year operating history"],
        "description": "Growth capital for energy infrastructure companies.",
        "raw_excerpt": "Applicants must demonstrate $1m+ ARR and Series B or later…",
    },
]


async def main() -> None:
    for user_id in USER_IDS:
        existing = await firestore.get_profile(user_id)
        if existing.get("version", 0) == 0:
            await firestore.get_client().collection("profiles").document(user_id).set(
                {**PROFILE, "version": 1})
            print(f"profile seeded for '{user_id}'")
        else:
            print(f"profile exists for '{user_id}' (v{existing['version']}) — left as-is")

    for record in OPPORTUNITIES:
        record["dedup_hash"] = pipeline_service.dedup_hash(
            record["name"], record["application_url"])
        opp_id = await firestore.create_opportunity(record)
        print(f"opportunity seeded: {record['name']} ({opp_id[:8]})")

    print("\nDone. Board: 1 strong fit + 1 obvious non-fit; profile has the seeded")
    print("'revolutionary' voice rule for the adaptation money shot.")


if __name__ == "__main__":
    asyncio.run(main())
