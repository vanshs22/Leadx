"""Offline contracts for the agent-owned normal lead-generation slice."""
from __future__ import annotations

import pytest

from app.integrations.prod.lead_generation_orchestration import (
    NORMAL_STAGE_ORDER,
    PARALLEL_RESEARCH_BRANCHES,
    POST_DELIVERY_AGENTS,
    STAGE_DEPENDENCIES,
    STAGE_OWNERS,
    ready_stages,
    stage_plan,
)


def test_exact_stage_order_and_dependencies_are_closed():
    assert NORMAL_STAGE_ORDER == (
        "prospecting", "source_router", "discovery_planner", "identity_resolution",
        "enrichment", "contact_finder", "email_finder", "research", "web_research",
        "social_intelligence", "verification", "deliverability", "data_quality",
        "icp", "intent", "trigger", "signal", "qualification", "personalization",
        "compliance", "routing", "outreach", "crm_projection", "assignment", "delivery", "learning",
    )
    assert set(NORMAL_STAGE_ORDER) == set(STAGE_DEPENDENCIES)
    assert STAGE_DEPENDENCIES["verification"] == ("enrichment", "email_finder")
    assert set(STAGE_DEPENDENCIES["data_quality"]) == {
        "enrichment", "research", "web_research", "social_intelligence", "verification",
    }
    assert STAGE_DEPENDENCIES["crm_projection"] == ("qualification", "routing")
    assert STAGE_DEPENDENCIES["assignment"] == ("crm_projection",)
    assert STAGE_DEPENDENCIES["delivery"] == ("assignment",)
    assert STAGE_DEPENDENCIES["learning"] == ("delivery",)
    assert STAGE_OWNERS == {"outreach": "outreach", "crm_projection": "crm", "assignment": "crm", "delivery": "crm"}


def test_parallel_research_branches_are_eligible_only_after_enrichment():
    assert set(ready_stages({"prospecting", "source_router", "discovery_planner", "identity_resolution", "enrichment"})) >= set(PARALLEL_RESEARCH_BRANCHES)
    assert "research" not in ready_stages({"identity_resolution"})
    assert set(ready_stages(set())) == {"prospecting"}


def test_registry_has_all_31_trusted_bindings_without_execution():
    from app.integrations.prod.agent_registry import validate_registry

    registry = validate_registry()
    assert len(registry) == 31
    assert all(row["enabled"] and row["executor_key"] for row in registry.values())


def test_contract_handoff_is_bounded_and_preserves_contact_candidates():
    from app.integrations.prod.agent_missions import _compact_handoff

    result = _compact_handoff([{
        "agent": "email_finder",
        "lead_ids": ["lead-1"],
        "candidates": {"lead-1": [{"email": "owner@example.test", "source": "local"}]},
        "research": {"evidence": "x" * 5000},
    }])
    assert result["lead_ids"] == ["lead-1"]
    assert result["candidates"]["lead-1"][0]["email"] == "owner@example.test"
    assert result["email_candidates"]["lead-1"][0]["email"] == "owner@example.test"
    assert len(result["research"]["evidence"]) <= 2000


def test_portal_plan_records_non_outbound_reason_without_live_calls():
    plan = {row["stage"]: row for row in stage_plan(delivery_channel="portal", needed_stages={"prospecting", "enrichment", "delivery"})}
    assert plan["intent"]["status"] == "skipped"
    assert plan["intent"]["skipped_reason"] == "not_needed_for_portal_delivery"
    assert plan["delivery"]["status"] == "ready"


@pytest.mark.asyncio
async def test_post_delivery_dispatch_queues_learning_through_existing_agent_run(monkeypatch):
    from app.integrations.prod import jobs
    from app.integrations.prod import post_run_agent_dispatch as dispatch

    created = []
    submitted = []

    def create_run(tenant_id, agent_id, task, input_data, created_by):
        created.append((tenant_id, agent_id, input_data, created_by))
        return {"id": f"run-{agent_id}"}

    def submit(*args, **kwargs):
        submitted.append((args, kwargs))
        return {"job_id": f"job-{kwargs['idempotency_key']}"}

    monkeypatch.setattr("app.integrations.prod.agents.create_run", create_run)
    monkeypatch.setattr(jobs, "submit", submit)
    monkeypatch.setattr(jobs, "accepted_job_id", lambda handoff: handoff["job_id"])

    result = await dispatch.dispatch_post_delivery_agents(
        tenant_id="tenant-1", run_id="run-1", vertical="gym",
        lead_ids=["assignment-1"], assigned=1, delivered=1,
    )
    assert [row["agent_id"] for row in result] == list(POST_DELIVERY_AGENTS)
    assert "learning" in [row[1] for row in created]
    assert all(row["status"] == "queued" for row in result)
    assert all(kwargs["operation_kind"] == "agent.run" for _, kwargs in submitted)
    assert all(kwargs["provider_required"] is False for _, kwargs in submitted)


@pytest.mark.asyncio
async def test_portal_personalization_is_explicitly_skipped_without_provider_calls():
    from app.integrations.prod.agent_fabric import execute_specialized

    result = await execute_specialized("personalization", {"delivery_channel": "portal", "lead_ids": []})
    assert result["status"] == "skipped"
    assert result["skipped_reason"] == "not_needed_for_portal_delivery"
