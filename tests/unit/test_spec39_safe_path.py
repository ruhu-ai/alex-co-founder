"""Read-only terminal status for the currently authorized Spec 39 path."""

from __future__ import annotations

import json

import pytest

from scripts import check_spec39_safe_path as safe_path
from services.durable_memory_release import candidate_hash


def _write_current_local_evidence(tmp_path):
    candidate = candidate_hash()
    entry = tmp_path / "entry.json"
    founder = tmp_path / "founder.json"
    entry.write_text(
        json.dumps(
            {
                "passed": True,
                "candidate_sha256": candidate,
                "identity": {"product_role": "FOUNDER", "count": 1},
            }
        ),
        encoding="utf-8",
    )
    founder.write_text(
        json.dumps(
            {
                "status": "PASS",
                "candidate_sha256": candidate,
                "isolation": {"synthetic_only": True, "external_effects": 0},
                "end_state": {"memory_enabled": False},
            }
        ),
        encoding="utf-8",
    )
    return entry, founder


@pytest.mark.asyncio
async def test_safe_path_stops_at_external_m2_entry_gate(tmp_path, monkeypatch):
    entry, founder = _write_current_local_evidence(tmp_path)
    monkeypatch.setattr(safe_path, "ENTRY_EVIDENCE", entry)
    monkeypatch.setattr(safe_path, "FOUNDER_EVIDENCE", founder)
    monkeypatch.delenv("DURABLE_MEMORY_M2_ENABLED", raising=False)

    result = await safe_path.evaluate()

    assert result["decision"] == "BLOCKED_AT_EXTERNAL_M2_ENTRY_GATE"
    assert result["furthest_safe_stage"] == "M2_LOCAL_VALIDATED_DEFAULT_OFF"
    assert result["normal_m2_enabled"] is False
    assert result["enablement_authorized"] is False
    assert result["cloud_mutations_performed"] == 0
    assert result["external_effects_performed"] == 0
    assert result["local_checks"]["export_delivery_default_off"] is True
    assert {row["tier"] for row in result["later_tiers"]} == {
        "M3",
        "M4",
        "M5",
        "M6",
    }
    gates = {row["gate"] for row in result["external_entry_gates"]}
    assert "qualified_encrypted_async_memory_export_delivery" in gates


@pytest.mark.asyncio
async def test_safe_path_rejects_stale_local_evidence(tmp_path, monkeypatch):
    entry, founder = _write_current_local_evidence(tmp_path)
    payload = json.loads(founder.read_text(encoding="utf-8"))
    payload["candidate_sha256"] = "sha256:" + "0" * 64
    founder.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(safe_path, "ENTRY_EVIDENCE", entry)
    monkeypatch.setattr(safe_path, "FOUNDER_EVIDENCE", founder)
    monkeypatch.delenv("DURABLE_MEMORY_M2_ENABLED", raising=False)

    result = await safe_path.evaluate()

    assert result["decision"] == "LOCAL_REVALIDATION_REQUIRED"
    assert result["local_checks"]["founder_validation_evidence_current"] is False
