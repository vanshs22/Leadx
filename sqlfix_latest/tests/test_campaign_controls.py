from __future__ import annotations

import pytest

from app.middleware.auth import TenantContext


def _campaign_payload(**overrides):
    payload = {
        "name": "Tenant campaign",
        "locations": ["Austin TX"],
        "daily_new_lead_limit": 10,
        "daily_email_limit": 5,
        "max_active_enrollments": 20,
        "sequence_id": "seq-a",
    }
    payload.update(overrides)
    return payload


def test_lane_planner_uses_bounded_deterministic_rotating_tranches():
    from app.integrations.prod.discovery_catalog import DISCOVERY_LANES, plan_discovery_lanes

    tiny_a = plan_discovery_lanes(5, rotation_key="run-a")
    tiny_b = plan_discovery_lanes(5, rotation_key="run-a")
    rotated = plan_discovery_lanes(5, rotation_key="run-b")
    exhaustive = plan_discovery_lanes(5, rotation_key="run-a", exhaustive=True)

    assert tiny_a["tranche_size"] < len(DISCOVERY_LANES)
    assert tiny_a == tiny_b
    assert tiny_a["lanes"] != rotated["lanes"]
    assert tiny_a["lane_budget_total"] == 5
    assert exhaustive["tranche_size"] == len(DISCOVERY_LANES)
    assert exhaustive["exhaustive"] is True


def test_tenant_campaign_service_filters_cross_tenant_and_enforces_binding(fake_supabase, monkeypatch):
    from app.integrations.prod import autonomous_campaigns

    monkeypatch.setattr(autonomous_campaigns, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "email_sequences": [
            {"id": "seq-a", "tenant_id": "tenant-a", "status": "active"},
            {"id": "seq-b", "tenant_id": "tenant-b", "status": "active"},
        ],
    })
    row = autonomous_campaigns.create_campaign(tenant_id="tenant-a", **_campaign_payload())
    assert row["tenant_id"] == "tenant-a"
    assert row["require_verified_email"] is True
    assert autonomous_campaigns.get_campaign(row["id"], tenant_id="tenant-b") is None
    assert autonomous_campaigns.update_campaign(row["id"], tenant_id="tenant-b", name="nope") == {}
    with pytest.raises(autonomous_campaigns.CampaignValidationError, match="sequence_not_found"):
        autonomous_campaigns.update_campaign(row["id"], tenant_id="tenant-a", sequence_id="seq-b")


def test_campaign_control_routes_are_mounted_and_tenant_scoped():
    from app import app_service

    paths = {getattr(route, "path", "") for route in app_service.app.routes}
    expected = {
        "/crm/{tenant_id}/campaigns",
        "/crm/{tenant_id}/campaigns/{campaign_id}",
        "/crm/{tenant_id}/campaigns/{campaign_id}/activate",
        "/crm/{tenant_id}/campaigns/{campaign_id}/pause",
        "/crm/{tenant_id}/campaigns/{campaign_id}/run-now",
        "/crm/{tenant_id}/campaigns/{campaign_id}/status",
        "/crm/{tenant_id}/growth/campaigns",
        "/crm/{tenant_id}/growth/campaigns/{campaign_id}",
        "/crm/{tenant_id}/growth/campaigns/{campaign_id}/activate",
        "/crm/{tenant_id}/growth/campaigns/{campaign_id}/pause",
        "/crm/{tenant_id}/growth/campaigns/{campaign_id}/run-now",
        "/crm/{tenant_id}/growth/campaigns/{campaign_id}/status",
        "/crm/{tenant_id}/pipeline-runs/{run_id}",
        "/crm/{tenant_id}/growth/runs/{run_id}",
    }
    assert expected <= paths


@pytest.mark.asyncio
async def test_sequence_enrollment_rejects_assignment_from_another_tenant(fake_supabase, monkeypatch):
    from app.integrations.prod import sequences

    monkeypatch.setattr(sequences, "get_supabase", lambda: fake_supabase)
    fake_supabase.store.update({
        "email_sequences": [{"id": "seq-a", "tenant_id": "tenant-a", "status": "active"}],
        "email_sequence_steps": [{"sequence_id": "seq-a", "step_number": 1, "delay_days": 0}],
        "lead_assignments": [{"id": "assignment-b", "tenant_id": "tenant-b", "lead_id": "lead-b"}],
        "email_sequence_enrollments": [],
    })
    result = await sequences.enroll_leads("tenant-a", "seq-a", ["assignment-b"], campaign_id="campaign-a")
    assert result == {"ok": True, "enrolled": 0, "skipped": 1}
    assert fake_supabase.rows("email_sequence_enrollments") == []
