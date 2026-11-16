"""Focused local checks for the shared specialized-agent lead boundary."""
from __future__ import annotations

import asyncio

import pytest

from app.integrations.prod import agent_fabric, agents_support
from app.integrations.prod.agent_fabric import SPECIALIZED
from tests.runtime_exploration.test_production_readiness_blockers_property import _SupabaseDouble


def _db(*, assignments=None, leads=None, missions=None, tasks=None, members=None):
    return _SupabaseDouble({
        "lead_assignments": assignments or [],
        "leads_global": leads or [],
        "agent_missions": missions or [],
        "agent_mission_tasks": tasks or [],
        "tenant_members": members or [],
        "enrichment": [],
    })


def test_foreign_tenant_is_rejected_without_reading_leads(monkeypatch):
    db = _db(
        assignments=[{"lead_id": "foreign", "tenant_id": "tenant-b", "status": "new"}],
        leads=[{"id": "foreign", "name": "do not disclose"}],
    )
    monkeypatch.setattr(agents_support, "get_supabase", lambda: db)
    decision = agents_support.authorize_lead_access(None, "tenant-a", None, "verification", ["foreign"])
    assert not decision.authorized
    assert decision.reason_code == "lead_not_found_or_not_owned"
    assert decision.authorized_count == 0
    assert not any(table == "leads_global" for table, _ in db.calls)


def test_forged_or_inactive_mission_is_rejected_before_fabric_read(monkeypatch):
    db = _db(leads=[{"id": "lead-a", "name": "do not disclose"}])
    from app.memory import supabase_client
    monkeypatch.setattr(agents_support, "get_supabase", lambda: db)
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: db)
    result = asyncio.run(agent_fabric.execute_specialized("verification", {
        "tenant_id": "tenant-a", "mission_id": "forged-mission", "lead_ids": ["lead-a"],
    }))
    assert result["status"] == "rejected"
    assert result["reason_code"] == "mission_not_authorized"
    assert not any(table == "leads_global" for table, _ in db.calls)


def test_mixed_batch_rejects_everything_before_any_lead_read(monkeypatch):
    db = _db(
        assignments=[{"lead_id": "own", "tenant_id": "tenant-a", "status": "new"}],
        leads=[{"id": "own"}, {"id": "foreign"}],
    )
    from app.memory import supabase_client
    monkeypatch.setattr(agents_support, "get_supabase", lambda: db)
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: db)
    result = asyncio.run(agent_fabric.execute_specialized("verification", {
        "tenant_id": "tenant-a", "lead_ids": ["own", "foreign"],
    }))
    assert result["status"] == "rejected"
    assert result["reason_code"] == "mixed_lead_batch_rejected"
    assert not any(table == "leads_global" for table, _ in db.calls)


@pytest.mark.parametrize("agent_id", sorted(SPECIALIZED))
def test_every_specialized_agent_uses_assignment_authorization(monkeypatch, agent_id):
    db = _db(assignments=[{"lead_id": "assigned", "tenant_id": "tenant-a", "status": "new"}])
    monkeypatch.setattr(agents_support, "get_supabase", lambda: db)
    decision = agents_support.authorize_lead_access(None, "tenant-a", None, agent_id, ["assigned"])
    assert decision.authorized, (agent_id, decision.as_dict())
    assert decision.reason_code == "authorized"
    assert decision.lead_ids == ("assigned",)


def test_authorized_direct_fabric_path_reads_after_authentication(monkeypatch):
    db = _db(
        assignments=[{"lead_id": "assigned", "tenant_id": "tenant-a", "status": "new"}],
        leads=[{"id": "assigned", "name": "authorized business"}],
    )
    from app.memory import supabase_client
    monkeypatch.setattr(agents_support, "get_supabase", lambda: db)
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: db)
    result = asyncio.run(agent_fabric.execute_specialized("verification", {
        "tenant_id": "tenant-a", "lead_ids": ["assigned"],
    }))
    assert result["lead_ids"] == ["assigned"]
    assert any(table == "leads_global" for table, _ in db.calls)


def test_explicit_system_owned_mission_allows_handoff_only(monkeypatch):
    db = _db(
        missions=[{
            "id": "mission-system", "tenant_id": "tenant-a", "status": "running",
            "input_data": {"system_owned": True, "lead_ids": ["global-lead"]},
        }],
        tasks=[{
            "mission_id": "mission-system", "tenant_id": "tenant-a", "agent_id": "verification",
            "status": "running", "input_data": {"lead_ids": ["global-lead"]},
        }],
        leads=[{"id": "global-lead", "name": "system handoff"}],
    )
    from app.memory import supabase_client
    monkeypatch.setattr(agents_support, "get_supabase", lambda: db)
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: db)
    result = asyncio.run(agent_fabric.execute_specialized("verification", {
        "tenant_id": "tenant-a", "mission_id": "mission-system", "lead_ids": ["global-lead"],
        "principal": {"kind": "system_agent", "tenant_id": "tenant-a"},
    }))
    assert result["lead_ids"] == ["global-lead"]


def test_user_principal_requires_active_tenant_membership(monkeypatch):
    db = _db(
        assignments=[{"lead_id": "assigned", "tenant_id": "tenant-a", "status": "new"}],
        members=[{"tenant_id": "tenant-b", "auth_user_id": "user-a", "status": "active"}],
    )
    monkeypatch.setattr(agents_support, "get_supabase", lambda: db)
    decision = agents_support.authorize_lead_access(
        {"kind": "user", "user_id": "user-a", "tenant_id": "tenant-a"},
        "tenant-a", None, "verification", ["assigned"],
    )
    assert not decision.authorized
    assert decision.reason_code == "tenant_membership_required"
