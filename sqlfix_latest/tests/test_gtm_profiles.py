"""Focused offline tests for selected-subset GTM mission contracts."""
from __future__ import annotations

import sys

import pytest

from app.orchestration.gtm_profiles import (
    GTM_PROFILE_ID,
    GTMProfileError,
    compile_gtm_mission,
    compile_gym_new_delhi_mission,
    compile_market_entry_mission,
)
from app.orchestration.profiles import DEFAULT_EXCLUDED_TASKS


def test_gtm_compiles_only_the_explicit_specialist_subset():
    mission = compile_gtm_mission(
        {
            "request_id": "mission-1",
            "objective": "Research the New Delhi gym market",
            "tenant_id": "tenant-a",
            "use_case_id": "market-entry",
        },
        ["icp", "research", "web_research"],
    )

    assert mission.selected_specialists == ("icp", "research", "web_research")
    assert {task.specialist_id for task in mission.tasks} == set(mission.selected_specialists)
    assert "prospecting" in mission.dependency_gaps["research"]
    assert mission.status == "blocked"
    assert mission.stop_policy["no_unselected_specialist_activation"] is True
    assert set(DEFAULT_EXCLUDED_TASKS).issubset(mission.excluded_default_work)


def test_generic_growth_does_not_infer_outreach_or_campaign_work():
    mission = compile_gtm_mission(
        "Grow the New Delhi gym market",
        ["icp", "research"],
    )

    assert "outreach" not in mission.selected_specialists
    assert "campaign_optimizer" not in mission.selected_specialists
    assert "outreach.send" not in mission.workflow_tools
    assert "campaign.send" not in mission.workflow_tools
    assert "outreach.send" in mission.excluded_default_work


def test_gym_new_delhi_preserves_intent_and_derives_bounded_routes():
    mission = compile_gym_new_delhi_mission(target=5)

    assert mission.objective == "Find qualified gyms in New Delhi"
    assert mission.planning_inputs["vertical"] == "gym"
    assert mission.planning_inputs["geography"] == "New Delhi"
    assert len(mission.planning_inputs["sub_icps"]) >= 3
    assert len(mission.planning_inputs["subregions"]) == 5
    assert mission.plan_ir is not None
    assert len(mission.plan_ir.inputs["route_cells"]) > 1
    assert mission.status == "ready"


def test_market_entry_example_has_research_brief_and_delivery_boundary():
    mission = compile_market_entry_mission(target=50)

    assert mission.budgets["max_leads"] == 50
    assert "web_research" in mission.selected_specialists
    assert "research.collect" in mission.workflow_tools
    assert "delivery.reconcile" in mission.workflow_tools
    assert "outreach.send" not in mission.workflow_tools
    assert "reviewable market-entry brief" in mission.terminal_outcomes
    assert mission.plan_ir.inputs["target"] == 50


def test_unknown_or_empty_selection_fails_closed():
    with pytest.raises(GTMProfileError, match="mission_invalid"):
        compile_gtm_mission("An unscoped mission", [])
    with pytest.raises(GTMProfileError, match="specialist_unavailable"):
        compile_gtm_mission("Approved mission", ["not-a-specialist"])


def test_mission_compilation_is_deterministic_and_offline():
    before = {name for name in sys.modules if name.split(".", 1)[0] in {"boto3", "supabase", "redis", "httpx", "dotenv"}}
    first = compile_gym_new_delhi_mission(target=5)
    second = compile_gym_new_delhi_mission(target=5)
    after = {name for name in sys.modules if name.split(".", 1)[0] in {"boto3", "supabase", "redis", "httpx", "dotenv"}}

    assert first.to_dict() == second.to_dict()
    assert after == before


def test_event_driven_gated_specialist_cannot_be_selected_without_approval():
    with pytest.raises(GTMProfileError, match="approval_required:outreach"):
        compile_gtm_mission("Prepare an approved outreach task", ["outreach"])

    mission = compile_gtm_mission(
        "Prepare an approved outreach task",
        ["outreach"],
        gate_context={
            "outreach_event": True,
            "approval": True,
        },
    )
    assert mission.selected_specialists == ("outreach",)
    assert mission.status == "blocked"  # compliance/deliverability prerequisites are explicit, not inferred


def test_all_catalog_specialists_have_substantive_typed_contracts():
    from app.orchestration.catalog import CATALOG_IDS
    from app.orchestration.specialist_contracts import SPECIALIST_CONTRACTS

    assert tuple(SPECIALIST_CONTRACTS) == CATALOG_IDS
    for specialist_id, contract in SPECIALIST_CONTRACTS.items():
        assert contract.specialist_id == specialist_id
        assert len(contract.input_contract) > 20
        assert len(contract.output_contract) > 20
        assert contract.allowed_tools
        assert contract.evidence_requirements
        assert contract.tenant_boundary
        assert contract.compliance_boundary
        assert contract.executor_key
        assert contract.terminal_behavior


def test_event_driven_capabilities_are_explicitly_outside_default_activation():
    from app.orchestration.gtm_profiles import DEFAULT_EVENT_DRIVEN_SPECIALISTS, EVENT_DRIVEN_SPECIALISTS
    from app.orchestration.profiles import PROFILES

    default_included = set(PROFILES["lead_generation.default.v1"].included_task_types)
    assert EVENT_DRIVEN_SPECIALISTS == DEFAULT_EVENT_DRIVEN_SPECIALISTS
    assert not default_included.intersection(EVENT_DRIVEN_SPECIALISTS)
