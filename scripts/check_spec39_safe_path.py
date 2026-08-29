#!/usr/bin/env python3
"""Read-only status for the furthest currently authorized Spec 39 path.

This command never deploys, changes flags, applies TTL, provisions resources,
or treats local evidence as a substitute for a live pre-production gate.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_durable_memory_m2_release import provisional_safety_probe  # noqa: E402
from services.durable_memory import SOURCE_POLICIES, SourceType  # noqa: E402
from services.durable_memory_release import candidate_hash  # noqa: E402
from services.local_pilot_store import PILOT_ROOT  # noqa: E402

ENTRY_EVIDENCE = PILOT_ROOT / "entry-evidence.json"
FOUNDER_EVIDENCE = PILOT_ROOT / "founder-validation-evidence.json"

EXTERNAL_ENTRY_GATES = (
    "live_exact_active_authenticated_synthetic_founder",
    "separately_administered_deletion_ledger_and_live_high_water",
    "ledger_excluded_from_application_restore",
    "active_memory_items_expires_at_ts_ttl",
    "named_backup_policy_and_residual_window",
    "qualified_encrypted_async_memory_export_delivery",
    "product_security_privacy_platform_hiring_operations_entry_reviews",
    "deployment_and_flag_change_authorization",
)


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


async def evaluate() -> dict[str, Any]:
    candidate = candidate_hash()
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/deploy.sh").read_text(encoding="utf-8")
    entry = _load(ENTRY_EVIDENCE)
    founder = _load(FOUNDER_EVIDENCE)
    probe = await provisional_safety_probe()

    entry_current = bool(
        entry.get("passed") is True
        and entry.get("candidate_sha256") == candidate
        and (entry.get("identity") or {}).get("product_role") == "FOUNDER"
        and (entry.get("identity") or {}).get("count") == 1
    )
    founder_current = bool(
        founder.get("status") == "PASS"
        and founder.get("candidate_sha256") == candidate
        and (founder.get("isolation") or {}).get("synthetic_only") is True
        and int((founder.get("isolation") or {}).get("external_effects") or 0) == 0
        and (founder.get("end_state") or {}).get("memory_enabled") is False
    )
    checks = {
        "normal_default_off": "DURABLE_MEMORY_M2_ENABLED=false" in example,
        "entry_binding_default_off": ("DURABLE_MEMORY_M2_ENTRY_GATE_ATTESTED=false" in example),
        "export_delivery_default_off": (
            "DURABLE_MEMORY_M2_EXPORT_DELIVERY_ATTESTED=false" in example
        ),
        "normal_deploy_omits_m2": "DURABLE_MEMORY_M2_ENABLED" not in deploy,
        "generic_memory_default_disabled": "PERSISTENT_MEMORY_BACKEND=disabled" in example,
        "closed_three_source_registry": set(SOURCE_POLICIES) == set(SourceType),
        "provisional_restore_fence": probe.get("passed") is True,
        "local_entry_evidence_current": entry_current,
        "founder_validation_evidence_current": founder_current,
        "current_process_normal_m2_off": not _truthy(os.environ.get("DURABLE_MEMORY_M2_ENABLED")),
    }
    local_ready = all(checks.values())
    return {
        "schema_version": 1,
        "candidate_sha256": candidate,
        "decision": (
            "BLOCKED_AT_EXTERNAL_M2_ENTRY_GATE" if local_ready else "LOCAL_REVALIDATION_REQUIRED"
        ),
        "furthest_safe_stage": (
            "M2_LOCAL_VALIDATED_DEFAULT_OFF" if local_ready else "M2_IMPLEMENTED_DEFAULT_OFF"
        ),
        "local_checks": checks,
        "external_entry_gates": [
            {"gate": gate, "status": "BLOCKED_EXTERNAL_EVIDENCE_OR_AUTHORITY"}
            for gate in EXTERNAL_ENTRY_GATES
        ],
        "later_tiers": [
            {"tier": tier, "status": "UNAUTHORIZED"} for tier in ("M3", "M4", "M5", "M6")
        ],
        "normal_m2_enabled": False,
        "enablement_authorized": False,
        "cloud_mutations_performed": 0,
        "external_effects_performed": 0,
        "next_action": (
            "Re-run the isolated local entry and founder validation against this candidate."
            if not local_ready
            else "Complete the live external entry evidence pack, then request a "
            "separate canary configuration authorization."
        ),
    }


def main() -> int:
    result = asyncio.run(evaluate())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["decision"] == "BLOCKED_AT_EXTERNAL_M2_ENTRY_GATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
