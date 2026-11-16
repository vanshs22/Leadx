"""Task 1 Property 2: supported behavior on healthy synthetic inputs.

These checks run against the unfixed implementation and must pass.  They
record the existing route, mission, local-check, billing, support, migration,
provider-ordering, target-stop, and evidence behavior without contacting any
remote service.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_authorized_direct_and_system_owned_mission_reads_remain_supported(monkeypatch):
    from app.integrations.prod import agent_runtime
    from tests.runtime_exploration.test_production_readiness_blockers_property import _SupabaseDouble

    own_db = _SupabaseDouble({
        "lead_assignments": [{"lead_id": "own-lead", "tenant_id": "tenant-a", "status": "active"}],
        "leads_global": [{"id": "own-lead", "name": "Own Business"}],
        "enrichment": [],
    })
    monkeypatch.setattr(agent_runtime, "_sb", lambda: own_db)
    own = agent_runtime._lead("own-lead", "tenant-a")

    system_db = _SupabaseDouble({
        "agent_missions": [{
            "id": "system-mission", "tenant_id": "tenant-a", "status": "running",
            "input_data": {"system_owned": True, "lead_ids": ["global-lead"]},
        }],
        "leads_global": [{"id": "global-lead", "name": "Global Business"}],
        "enrichment": [],
    })
    monkeypatch.setattr(agent_runtime, "_sb", lambda: system_db)
    system_owned = agent_runtime._lead(
        "global-lead", "tenant-a", mission_id="system-mission",
        principal={"kind": "system_agent", "tenant_id": "tenant-a"},
    )
    assert own["id"] == "own-lead"
    assert system_owned["id"] == "global-lead"


def test_existing_agent_and_crm_route_names_remain_available():
    service = (ROOT / "app/app_service.py").read_text(encoding="utf-8")
    for route in (
        "/crm/{tenant_id}/agent-missions",
        "/crm/{tenant_id}/fabric/overview",
        "/support/ask",
        "/billing/create-checkout-session",
    ):
        assert route in service


def test_valid_local_migration_check_keeps_supported_command():
    completed = subprocess.run(
        [sys.executable, "scripts/check_migrations.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "canonical migrations:" in completed.stdout


def test_valid_stripe_price_mapping_and_plan_display_are_preserved(monkeypatch):
    from app.api.prod import billing_api
    from app.core.plans import public_plans

    monkeypatch.setenv("STRIPE_PRICE_STARTER_MONTHLY", "price_starter_synthetic")
    assert billing_api._price_id("starter", "monthly") == "price_starter_synthetic"
    assert billing_api._plan_for_price("price_starter_synthetic") == ("starter", "monthly")
    assert {plan["key"] for plan in public_plans()} == {"free", "starter", "growth", "agency"}


def test_private_local_service_communication_contract_remains_supported():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    portal_config = (ROOT / "lead-crm-portal/next.config.js").read_text(encoding="utf-8")
    assert "API_UPSTREAM: http://api:8000" in compose
    assert "REDIS_URL=${REDIS_URL:-redis://redis:6379/0}" in compose
    assert 'hardened ? "http://api:8000"' in portal_config


def test_faq_fallback_remains_available_when_support_storage_is_unavailable(monkeypatch):
    from app.integrations.prod import support_chat

    async def unavailable(_fn):
        raise ConnectionError("controlled support outage")

    monkeypatch.setattr(support_chat, "_db", unavailable)
    result = asyncio.run(support_chat.answer_customer(
        conversation_id="conversation-synthetic",
        tenant_id="tenant-a",
        user_id="user-a",
        message="help",
    ))
    assert result["escalated"] is False
    assert "LeadX Help" in result["answer"]
    assert result["storage_degraded"] is True


def test_valid_support_reply_status_is_still_accepted(monkeypatch):
    from app.api.prod import support_api
    from tests.runtime_exploration.test_production_readiness_blockers_property import _SupabaseDouble

    db = _SupabaseDouble({"support_conversations": [{"id": "conversation-a"}]})
    monkeypatch.setattr(support_api, "_sb", lambda: db)
    result = asyncio.run(support_api.admin_status("conversation-a", support_api.StatusIn(status="resolved")))
    assert result == {"ok": True}
    assert ("support_conversations", "update") in db.calls


def test_free_first_ordering_and_target_stop_conditions_remain_supported():
    from app.integrations.prod.lead_selection_planner import evaluate_route_cell_decision
    from app.integrations.prod.paid_policy import paid_stage_allowed

    assert paid_stage_allowed(
        [{"website": "https://free.example"}], 1,
        credential_available=True, unmet_gap=False, remaining_quota=10, local_mode=False,
    ) == (False, "free_sources_sufficient")
    decision = evaluate_route_cell_decision(
        {"source": "osm", "geography": "Austin", "query": "fitness"},
        target=5, assignment_confirmed=5,
    )
    assert decision["decision"] == "skip"
    assert decision["reason"] == "target_met"


def test_historical_evidence_retains_local_and_unverified_remote_classes():
    from scripts.release_evidence import EvidenceRecord, EvidenceReport

    scope = {"revision": "synthetic-revision", "scope": "baseline"}
    report = EvidenceReport(release_scope=scope)
    report.add("local_unit_property", EvidenceRecord(
        environment_mode="local_unit", test_name="authorized local check", result="PASS",
        diagnosis="controlled local result", artifacts=["checks/local.json"], check_id="local.check",
        claim="local behavior preserved", evidence_level="automated", release_scope=scope,
        status="PASS—automated", evidence_refs=["checks/local.json"],
        redaction={"applied": True, "secrets_detected": False}, gate_ids=["local_automated"], selected=True,
    ))
    report.add("remote_evidence", EvidenceRecord(
        environment_mode="remote", test_name="live provider prerequisite", result="NOT_RUN",
        diagnosis="credentialed live check not selected", artifacts=[], check_id="live.check",
        claim="live provider remains unverified", evidence_level="live-provider", release_scope=scope,
        status="NOT TESTED", evidence_refs=[], redaction={"applied": True, "secrets_detected": False},
        gate_ids=["live_staging"], selected=False,
    ))
    rendered = report.as_dict()
    assert rendered["sections"]["local_unit_property"][0]["status"] == "PASS—automated"
    assert rendered["remote_evidence"][0]["status"] == "NOT TESTED"
    assert rendered["gates"]["live_staging"]["status"] == "closed"
