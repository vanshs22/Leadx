"""Task 7 rejected CRM state-preservation contracts.

These tests use only the in-memory Supabase fixture and source contracts. They
never invoke providers, discovery workers, email, billing, or remote services.
"""
from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api.prod import revenue_os_api
from app.crm import crm_api, crm_entities_api
from app.integrations.prod import sequences

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
WORKFLOW = "00000000-0000-0000-0000-000000000010"
CTX = SimpleNamespace(tenant_id=TENANT, user_id="00000000-0000-0000-0000-000000000099")


def test_property_10_rejected_crm_state_preservation(fake_supabase):
    """100 mixed invalid/cross-tenant operations never mutate authoritative rows."""
    fake_supabase.store.update({
        "deals": [{"id": "deal-1", "tenant_id": TENANT, "title": "Synthetic deal", "status": "open"}],
        "email_sequences": [{"id": "seq-1", "tenant_id": TENANT, "status": "active"}],
        "email_sequence_steps": [{"sequence_id": "seq-1", "step_number": 1, "delay_days": 0}],
        "lead_assignments": [{"id": "assignment-other", "tenant_id": OTHER_TENANT}],
        "workflow_definitions": [{"id": WORKFLOW, "tenant_id": OTHER_TENANT, "version": 3}],
    })
    before = copy.deepcopy(fake_supabase.store)

    rejected = 0
    for index in range(100):
        kind = index % 4
        if kind == 0:
            with pytest.raises(HTTPException) as caught:
                crm_entities_api._get_one("deals", "deal-1", OTHER_TENANT, "Deal")
            assert caught.value.status_code == 404
        elif kind == 1:
            body = crm_api.DiscoveryRequestBody(vertical=" ", requested_leads=1)
            with pytest.raises(HTTPException) as caught:
                crm_api.create_discovery_request(UUID(TENANT), body, CTX)
            assert caught.value.status_code == 400
        elif kind == 2:
            result = asyncio.run(sequences.enroll_leads(TENANT, "seq-1", ["assignment-other"]))
            assert result == {"ok": True, "enrolled": 0, "skipped": 1}
        else:
            with pytest.raises(HTTPException) as caught:
                revenue_os_api.delete_automation(UUID(TENANT), UUID(WORKFLOW), CTX)
            assert caught.value.status_code == 404
        rejected += 1

    assert rejected == 100
    assert fake_supabase.store == before


def test_invalid_automation_is_rejected_before_persistence(fake_supabase):
    request = revenue_os_api.WorkflowIn(name="Synthetic", trigger_type="provider.secret", graph={"actions": []})
    with pytest.raises(HTTPException, match="unsupported automation trigger"):
        revenue_os_api._validate_workflow(request)
    assert fake_supabase.rows("workflow_definitions") == []


def test_task7_portal_contracts_keep_mutations_authoritative():
    root = Path(__file__).resolve().parents[1] / "lead-crm-portal"
    agents = (root / "app" / "(portal)" / "agents" / "page.tsx").read_text(encoding="utf-8")
    deals = (root / "app" / "(portal)" / "deals" / "page.tsx").read_text(encoding="utf-8")
    sequences_page = (root / "app" / "(portal)" / "sequences" / "page.tsx").read_text(encoding="utf-8")
    automations = (root / "app" / "(portal)" / "automations" / "page.tsx").read_text(encoding="utf-8")

    assert "crmAgents" not in agents
    assert "hasAdminSession" in agents and "/admin/agents" in agents
    assert "optimistic" not in deals.lower()
    assert "changeDealStage" in deals and "setDeals((prev) => prev.map((d) => (d.id === deal.id ? authoritative : d)))" in deals
    assert "transitionSequence" in sequences_page and "deleteSequence" in sequences_page
    assert "deleteAutomation" in automations and "next_execution" in automations
