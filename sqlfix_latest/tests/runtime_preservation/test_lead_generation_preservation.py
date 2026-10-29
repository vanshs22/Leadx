"""Task 2 observation-first preservation checks for healthy runtime inputs.

These cases describe behavior outside ``isBugCondition``. They use only
synthetic values, in-memory doubles, and source-level route/UI contracts. They
must pass against the current implementation before remediation work starts.

**Validates: Requirements 2.8, 3.2, 3.3, 4.6, 5.1, 5.2, 6.2, 6.8, 9.3, 9.7, 12.1**
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_healthy_run_lifecycle_preserves_queued_running_and_terminal_admission():
    from app.integrations.prod.pipeline import FreshRunAdmissionError, validate_fresh_run_admission

    for status in ("queued", "running"):
        validate_fresh_run_admission(
            "run-synthetic",
            "tenant-synthetic-a",
            {"id": "run-synthetic", "tenant_id": "tenant-synthetic-a", "status": status},
            job_row={
                "tenant_id": "tenant-synthetic-a",
                "status": status,
                "locked_by": "worker-synthetic" if status == "running" else None,
            },
        )

    with pytest.raises(FreshRunAdmissionError) as exc:
        validate_fresh_run_admission(
            "run-completed",
            "tenant-synthetic-a",
            {"id": "run-completed", "tenant_id": "tenant-synthetic-a", "status": "completed"},
        )
    assert exc.value.code == "terminal_run"


def test_non_target_and_approved_exhaustive_requests_keep_existing_route_contract():
    from app.integrations.prod.discovery_catalog import plan_discovery_lanes

    non_target = plan_discovery_lanes(0, rotation_key="preservation-non-target")
    exhaustive = plan_discovery_lanes(5, rotation_key="preservation-exhaustive", exhaustive=True)

    assert non_target["lane_budget_total"] >= 0
    assert non_target["exhaustive"] is False
    assert exhaustive["exhaustive"] is True
    assert exhaustive["tranche_size"] == len(exhaustive["lanes"])

    pipeline = (ROOT / "app/integrations/prod/pipeline.py").read_text(encoding="utf-8")
    target_runner = pipeline[pipeline.index("async def run_pipeline("):]
    assert 'target_mode = int(target_new_leads or 0) > 0' in target_runner
    assert 'exhaustive_discovery = False' in target_runner


def test_free_first_and_policy_approved_paid_paths_keep_existing_order():
    from app.integrations.prod.paid_policy import paid_gate, paid_stage_allowed

    assert paid_stage_allowed(
        [{"website": "https://free.example"}],
        1,
        credential_available=True,
        unmet_gap=False,
        remaining_quota=10,
        local_mode=False,
    ) == (False, "free_sources_sufficient")
    assert paid_stage_allowed(
        [],
        1,
        credential_available=True,
        unmet_gap=True,
        remaining_quota=10,
        local_mode=False,
    ) == (True, "free_sources_insufficient")
    assert paid_gate(
        policy_enabled=True,
        credential_available=True,
        unmet_gap=True,
        remaining_quota=1,
        local_mode=False,
    ) == (True, "paid_gates_passed")
    assert paid_gate(
        policy_enabled=True,
        credential_available=True,
        unmet_gap=True,
        remaining_quota=1,
        local_mode=True,
    ) == (False, "local_validation_mode")


def test_valid_a_b_c_gate_and_contact_selection_remain_compatible():
    from app.integrations.prod.lead_selection_planner import build_selection_decision, select_candidates
    from app.integrations.prod.quality import passes_quality_gate

    common = {
        "website": "https://healthy.example",
        "address": "1 Main Street",
        "services": ["fitness"],
        "email": "owner@healthy.example",
        "email_status": "valid",
    }
    leads = [{**common, "id": "a", "tier": "A"}, {**common, "id": "b", "tier": "B"}, {**common, "id": "c", "tier": "C"}]

    assert passes_quality_gate(leads[0]) == (True, "ok")
    assert passes_quality_gate(leads[1], allow_tier_b=True) == (True, "ok")
    assert passes_quality_gate(leads[2], allow_tier_c=True) == (True, "ok")

    decision = build_selection_decision(
        target=5,
        rotation_key="preservation-tiers",
        allow_tier_b=True,
        allow_tier_c=True,
    )
    selected, summary = select_candidates(leads, decision)
    assert {row["id"] for row in selected} == {"a", "b", "c"}
    assert summary["tier_c_contactable_selected"] == 1


def test_assignment_crm_delivery_quota_and_lease_contracts_remain_replay_safe(monkeypatch):
    from app.integrations.prod import credits, jobs
    from runtime_preservation.test_healthy_runtime_property import _ReplayStore
    from app.integrations.prod import scale_control
    from conftest import FakeSupabase

    fake = FakeSupabase({
        "jobs": [{"id": "job-synthetic", "status": "running", "locked_by": "worker-synthetic"}],
        "lead_assignments": [{"id": "assignment-synthetic", "tenant_id": "tenant-synthetic-a", "lead_id": "lead-synthetic", "status": "delivered"}],
        "lead_credits": [],
    })
    monkeypatch.setattr(jobs, "_sb", lambda: fake)
    monkeypatch.setattr(credits, "_sb", lambda: fake)

    jobs.fail("job-synthetic", "controlled preservation failure", attempts=1, max_attempts=3, worker_id="worker-synthetic")
    first = asyncio.run(credits.credit_dead_contact("tenant-synthetic-a", "assignment-synthetic", "dead_email"))
    second = asyncio.run(credits.credit_dead_contact("tenant-synthetic-a", "assignment-synthetic", "dead_email"))
    assert fake.rows("jobs")[0]["status"] == "queued"
    assert first["credited"] == 1
    assert second == {"ok": True, "already_credited": True}

    store = _ReplayStore()
    monkeypatch.setattr(scale_control, "_sb", lambda: store)
    claim_args = {
        "tenant_id": "tenant-synthetic-a",
        "scope_hash": "scope-a",
        "request_hash": "body-a",
    }
    assert scale_control.claim_idempotency("preservation-key", "HTTP:POST:/crm/import", **claim_args) == (True, None)
    assert scale_control.claim_idempotency("preservation-key", "HTTP:POST:/crm/import", **claim_args) == (False, {"job_id": "job-stable"})

    quality = (ROOT / "app/integrations/prod/quality.py").read_text(encoding="utf-8")
    crm = (ROOT / "app/integrations/prod/crm_projection.py").read_text(encoding="utf-8")
    delivery = (ROOT / "app/services/delivery.py").read_text(encoding="utf-8")
    assert 'on_conflict="lead_id,tenant_id"' in quality
    assert 'eq("tenant_id", str(tenant_id))' in crm
    assert 'source_lead_id' in crm
    assert "mark_delivery_status(" in delivery


def test_duplicate_realtime_events_and_tenant_switch_stay_authoritative_and_scoped():
    realtime = (ROOT / "lead-crm-portal/lib/crmRealtime.ts").read_text(encoding="utf-8")
    api = (ROOT / "lead-crm-portal/lib/api.ts").read_text(encoding="utf-8")

    assert 'config: { private: true, broadcast: { self: false, ack: false } }' in realtime
    assert "invalidateApiCache()" in realtime
    assert 'event: "reconcile"' in realtime
    assert "disposeTenant" in realtime
    assert 'credentials: "include"' in api
    assert "tenantId" in api
    assert "request timed out" in api.lower()

    from app.middleware.auth import TenantContext, assert_tenant_match

    assert_tenant_match(TenantContext("tenant-synthetic-a", "user-synthetic", "owner"), "tenant-synthetic-a")
    with pytest.raises(Exception) as exc:
        assert_tenant_match(TenantContext("tenant-synthetic-a", "user-synthetic", "owner"), "tenant-synthetic-b")
    assert getattr(exc.value, "status_code", None) == 403
    assert getattr(exc.value, "detail", None) == "Tenant mismatch"
