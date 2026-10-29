"""Task 6 offline evidence for qualification, assignment, delivery, and counts.

The fixture drives the existing quality, selection, assignment, CRM projection,
portal delivery, and pipeline terminal paths.  The reconciliation helper only
reads their authoritative outputs; it is an evidence oracle, not a second
lifecycle implementation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

import pytest

from conftest import FakeSupabase

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
RUN_ID = "run-task6-synthetic"


class _RpcResult:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


class _AssignmentFake(FakeSupabase):
    """Local database double for the existing assignment/RPC boundaries."""

    def __init__(self, seed=None):
        super().__init__(seed)
        self.exclusivity_conflict = False

    def rpc(self, name, params=None):
        params = params or {}
        self.rpc_calls.append((name, params))
        if name == "tenant_quota_remaining":
            return _RpcResult(100)
        if name == "check_exclusivity":
            return _RpcResult(self.exclusivity_conflict)
        if name == "reserve_tenant_lead_slot":
            tenant_id = str(params.get("p_tenant_id"))
            period = _period()
            row = next(row for row in self.rows("usage_log") if row.get("tenant_id") == tenant_id and row.get("period") == period)
            row["leads_delivered"] = int(row.get("leads_delivered") or 0) + 1
            return _RpcResult(True)
        if name == "release_tenant_lead_slot":
            tenant_id = str(params.get("p_tenant_id"))
            row = next(row for row in self.rows("usage_log") if row.get("tenant_id") == tenant_id and row.get("period") == _period())
            row["leads_delivered"] = max(0, int(row.get("leads_delivered") or 0) - 1)
            return _RpcResult(True)
        if name == "seed_default_stages":
            return _RpcResult([])
        return _RpcResult(self.rpc_returns.get(name, []))


def _period() -> str:
    return datetime.now(timezone.utc).replace(day=1).date().isoformat()


def _lead(lead_id: str, tier: str, *, contact: str = "phone") -> dict:
    row = {
        "id": lead_id,
        "name": f"Synthetic {lead_id}",
        "company_name": f"Synthetic {lead_id}",
        "website": f"https://{lead_id}.example.test",
        "website_domain": f"{lead_id}.example.test",
        "address": "1 Test Street, Newark, NJ",
        "services": ["fitness", "training"],
        "sources_hit": ["osm", "web"],
        "tier": tier,
        "score": {"A": 90, "B": 65, "C": 50}[tier],
        "email": f"owner@{lead_id}.example.test" if contact == "email" else None,
        "email_status": "valid" if contact == "email" else "unavailable",
        "phone": "+12015550100" if contact == "phone" else None,
        "phone_e164": "+12015550100" if contact == "phone" else None,
        "phone_normalized": "20155550100" if contact == "phone" else None,
        "phone_status": "valid" if contact == "phone" else "unavailable",
    }
    return row


def _seed(leads: list[dict]) -> _AssignmentFake:
    return _AssignmentFake({
        "tenants": [{"id": TENANT, "plan": "growth", "leads_per_month_limit": 100, "delivery_channel": "portal"}],
        "usage_log": [{"tenant_id": TENANT, "period": _period(), "leads_delivered": 0, "quota_reserved": 0, "rollover_credits": 0}],
        "pipeline_stages": [{"id": f"stage-new-{TENANT}", "tenant_id": TENANT, "name": "New", "slug": "new", "position": 0}],
        "pipeline_runs": [{"id": RUN_ID, "tenant_id": TENANT, "status": "running", "assigned": 0, "delivered": 0}],
        "leads_global": leads,
    })


def _reconcile(fake: _AssignmentFake, dashboard: dict, delivery_result: dict) -> dict:
    """Read and compare authoritative projections without changing state."""
    assignments = [row for row in fake.rows("lead_assignments") if row.get("tenant_id") == TENANT]
    usage = next(row for row in fake.rows("usage_log") if row.get("tenant_id") == TENANT)
    run = next(row for row in fake.rows("pipeline_runs") if row.get("id") == RUN_ID)
    assigned = len(assignments)
    # reserve_tenant_lead_slot commits direct reservations into leads_delivered;
    # it does not use quota_reserved for a consumed direct slot.
    reserved = int(usage.get("leads_delivered") or 0)
    delivered = sum(row.get("delivery_status") in {"sent", "delivered", "portal"} for row in assignments)
    crm_companies = sum(row.get("tenant_id") == TENANT for row in fake.rows("companies"))
    crm_contacts = sum(row.get("tenant_id") == TENANT for row in fake.rows("contacts"))
    client_visible = int((dashboard.get("kpis") or {}).get("total_leads") or 0)
    values = {
        "assigned": assigned,
        "reserved": reserved,
        "delivered": delivered,
        "crm_companies": crm_companies,
        "crm_contacts": crm_contacts,
        "client_visible": client_visible,
        "run_assigned": int(run.get("assigned") or 0),
        "run_delivered": int(run.get("delivered") or 0),
        "adapter_count": int(delivery_result.get("count") or 0),
    }
    exact = (
        delivery_result.get("ok") is True
        and values["assigned"] == values["reserved"] == values["delivered"]
        == values["crm_companies"] == values["crm_contacts"] == values["client_visible"]
        == values["run_assigned"] == values["run_delivered"] == values["adapter_count"]
    )
    return {"status": "PASS—synthetic/offline" if exact else "FAIL", "counts": values}


@pytest.mark.asyncio
async def test_task6_synthetic_quality_assignment_crm_delivery_and_exact_reconciliation(monkeypatch):
    """A/B/C contactable leads follow the existing path and reconcile exactly."""
    from app.crm import crm_api
    from app.integrations.prod import pipeline, quality, lead7_runtime
    from app.services import delivery
    from app.integrations.prod import crm_projection

    leads = [_lead(f"lead-{index}", tier) for index, tier in enumerate(("A", "A", "B", "B", "C"), 1)]
    fake = _seed(leads)
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)
    monkeypatch.setattr(crm_projection, "get_supabase", lambda: fake)
    monkeypatch.setattr(lead7_runtime, "get_supabase", lambda: fake)
    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)
    monkeypatch.setattr(pipeline, "get_supabase", lambda: fake)
    monkeypatch.setattr(crm_api, "_sb", lambda: fake)
    crm_api._DASHBOARD_CACHE.clear()

    from app.integrations.prod.lead_selection_planner import build_selection_decision, select_candidates
    decision = build_selection_decision(target=5, allow_tier_b=True, allow_tier_c=True, rotation_key="task6")
    selected, selection_summary = select_candidates(deepcopy(leads), decision)
    assert len(selected) == 5
    assert selection_summary["tier_a_selected"] == 2
    assert selection_summary["tier_b_selected"] == 2
    assert selection_summary["tier_c_selected"] == 1

    from app.integrations.prod.quality import passes_quality_gate
    for lead in selected:
        ok, reason = passes_quality_gate(lead, allow_tier_b=True, allow_tier_c=True)
        assert ok, reason
    social_only = _lead("social-only", "C", contact="none")
    social_only["instagram_handle"] = "synthetic"
    assert passes_quality_gate(social_only, allow_tier_b=True, allow_tier_c=True)[0] is False

    assignments = []
    for lead in selected:
        result = await quality.assign_lead(
            lead_id=lead["id"], tenant_id=TENANT, icp_id=None, vertical="gym",
            geo=["Newark"], tier=lead["tier"], delivery_channel="portal",
        )
        assert result["assigned"] is True
        assignments.append(result["assignment_id"])
    delivery_result = await delivery.deliver_batch(selected, "portal", {}, assignment_ids=assignments)
    assert delivery_result == {
        "ok": True, "channel": "portal", "count": 5,
        "message": "Available in client portal / CRM",
    }
    await pipeline._finish_run(RUN_ID, "completed", {
        "discovered": 5, "stored": 5, "enriched": 5, "passed_quality_gate": 5,
        "assigned_to_tenant": 5, "delivered": 5,
    })
    dashboard = await crm_api.dashboard(UUID(TENANT))
    evidence = _reconcile(fake, dashboard, delivery_result)
    for key, value in evidence["counts"].items():
        assert value == 5, f"{key}={value}"
    assert evidence["status"] == "PASS—synthetic/offline"


@pytest.mark.asyncio
async def test_task6_duplicate_assignment_is_idempotent_and_cross_tenant_projection_isolated(monkeypatch):
    from app.crm import crm_api
    from app.integrations.prod import quality, crm_projection, lead7_runtime

    lead = _lead("duplicate-lead", "A")
    fake = _seed([lead])
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)
    monkeypatch.setattr(crm_projection, "get_supabase", lambda: fake)
    monkeypatch.setattr(lead7_runtime, "get_supabase", lambda: fake)
    monkeypatch.setattr(crm_api, "_sb", lambda: fake)
    crm_api._DASHBOARD_CACHE.clear()

    first = await quality.assign_lead(lead["id"], TENANT, None, "gym", ["Newark"], tier="A")
    second = await quality.assign_lead(lead["id"], TENANT, None, "gym", ["Newark"], tier="A")
    assert first["assigned"] is True
    assert second["assigned"] is True
    assert second["reason"] == "idempotent_replay"
    assert second["idempotent"] is True
    assert len([row for row in fake.rows("lead_assignments") if row.get("tenant_id") == TENANT]) == 1
    assert fake.rows("usage_log")[0]["leads_delivered"] == 1

    fake.store["lead_assignments"].append({"id": "other-assignment", "tenant_id": OTHER_TENANT, "lead_id": "other-lead", "tier": "A", "status": "new"})
    dashboard = await crm_api.dashboard(UUID(TENANT))
    assert dashboard["kpis"]["total_leads"] == 1
    assert all(row.get("tenant_id") == TENANT for row in fake.rows("lead_assignments") if row.get("id") in {x.get("assignment_id") for x in fake.rows("lead_assignments")})


@pytest.mark.asyncio
async def test_task6_exclusivity_conflict_blocks_assignment_without_reservation(monkeypatch):
    from app.integrations.prod import quality

    lead = _lead("exclusive-lead", "A")
    fake = _seed([lead])
    fake.exclusivity_conflict = True
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)

    result = await quality.assign_lead(
        lead["id"], TENANT, None, "gym", ["Newark"], tier="A", offer_category="seo_agency",
    )
    assert result == {"assigned": False, "reason": "exclusivity_conflict"}
    assert fake.rows("lead_assignments") == []
    assert fake.rows("usage_log")[0]["leads_delivered"] == 0


@pytest.mark.asyncio
async def test_task6_failed_delivery_and_partial_reconciliation_close_evidence(monkeypatch):
    from app.integrations.prod import pipeline, quality, crm_projection, lead7_runtime
    from app.services import delivery

    lead = _lead("failed-delivery", "A")
    fake = _seed([lead])
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)
    monkeypatch.setattr(crm_projection, "get_supabase", lambda: fake)
    monkeypatch.setattr(lead7_runtime, "get_supabase", lambda: fake)
    monkeypatch.setattr(delivery, "get_supabase", lambda: fake)
    monkeypatch.setattr(pipeline, "get_supabase", lambda: fake)
    monkeypatch.setattr(delivery, "deliver_webhook", lambda *_args, **_kwargs: _failed_delivery())

    assignment = await quality.assign_lead(lead["id"], TENANT, None, "gym", ["Newark"], tier="A")
    result = await delivery.deliver_batch(
        [lead], "webhook", {"url": "https://example.test/hook"}, [assignment["assignment_id"]],
    )
    assert result["ok"] is False
    assert fake.rows("lead_assignments")[0]["delivery_status"] == "failed"

    await pipeline._finish_run(RUN_ID, "completed", {
        "discovered": 1, "stored": 1, "enriched": 1, "passed_quality_gate": 1,
        "assigned_to_tenant": 1, "delivered": 1,
    })
    fake.rows("pipeline_runs")[0]["delivered"] = 1
    fake.rows("companies").clear()  # Partial CRM projection is not a successful delivery.
    evidence = _reconcile(fake, {"kpis": {"total_leads": 0}}, result)
    assert evidence["status"] == "FAIL"
    assert evidence["counts"]["delivered"] == 0


async def _failed_delivery(*_args, **_kwargs):
    return {"ok": False, "channel": "webhook", "count": 1, "error": "synthetic provider failure"}


@pytest.mark.asyncio
async def test_task10_icp_policy_blocks_foreign_use_case_before_reservation(monkeypatch):
    from app.integrations.prod import quality

    lead = _lead("foreign-icp", "A")
    fake = _seed([lead])
    fake.store["icp_profiles"] = [{
        "id": "icp-owned-by-other",
        "tenant_id": OTHER_TENANT,
        "vertical": "gym",
        "geo": ["Newark"],
        "exclusivity": "shared",
    }]
    monkeypatch.setattr(quality, "get_supabase", lambda: fake)

    result = await quality.assign_lead(
        lead["id"], TENANT, "icp-owned-by-other", "gym", ["Newark"], tier="A",
    )
    assert result == {"assigned": False, "reason": "icp_not_authorized"}
    assert fake.rows("lead_assignments") == []
    assert fake.rows("usage_log")[0]["leads_delivered"] == 0
