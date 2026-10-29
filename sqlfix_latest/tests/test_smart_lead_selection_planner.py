"""Focused offline coverage for the normalized smart planner."""
from __future__ import annotations


def test_gym_new_delhi_plan_is_deterministic_and_bounded():
    from app.integrations.prod.lead_selection_planner import build_normalized_plan

    kwargs = {
        "target": 5,
        "vertical": "gym",
        "locations": ["New Delhi"],
        "queries": ["gym", "fitness center"],
        "rotation_key": "fixture:gym:new-delhi",
        "max_route_cells": 48,
    }
    first = build_normalized_plan(**kwargs)
    second = build_normalized_plan(**kwargs)

    assert first == second
    assert first["canonical_vertical"]["name"] == "gym"
    assert {"gym", "fitness center"} <= set(first["canonical_vertical"]["synonyms"])
    assert first["parent_icp"]["target"] == 5
    assert first["sub_icps"]
    assert first["geography"]["localities"] == ["New Delhi"]
    assert first["tranche_policy"]["tranche_size"] == 5
    assert first["route_cells"]
    assert len(first["route_cells"]) <= 48
    assert len(first["decision_tests"]) == len(first["route_cells"])
    assert len({cell["cell_id"] for cell in first["route_cells"]}) == len(first["route_cells"])
    assert first["source_cost_order"][:3] == ["osm", "maps", "first_party"]
    for cell in first["route_cells"]:
        assert set(("cursor", "budget", "fallbacks", "provenance", "decision_test")) <= set(cell)
        assert cell["budget"]["page"] >= 1
        assert cell["budget"]["result"] >= 1
        assert cell["provenance"]["required"]


def test_target_tranches_and_growth_require_persisted_yield():
    from app.integrations.prod.lead_selection_planner import (
        build_normalized_plan,
        next_tranche_size,
    )

    small = build_normalized_plan(target=5, vertical="gym", locations=["New Delhi"])
    large = build_normalized_plan(target=500, vertical="gym", locations=["New Delhi"], max_route_cells=24)
    assert small["tranche_policy"]["tranche_size"] == 5
    assert large["tranche_policy"]["tranche_size"] == 50
    assert next_tranche_size(500, budget_remaining=100) == 50
    assert next_tranche_size(500, persisted_qualifying=10, persisted_contactable=8, budget_remaining=100) == 50
    assert next_tranche_size(50, persisted_qualifying=10, persisted_contactable=8, budget_remaining=100) == 20


def test_duplicate_queries_and_disabled_routes_are_explicit_decisions():
    from app.integrations.prod.lead_selection_planner import build_normalized_plan

    plan = build_normalized_plan(
        target=5,
        vertical="gym",
        locations=["New Delhi"],
        queries=["gym", "GYM"],
        enabled_sources={"maps": False},
        max_route_cells=32,
    )
    reasons = {row["reason"] for row in plan["decision_tests"]}
    assert "duplicate_route" in reasons
    assert "no_enabled_adapter" in reasons
    assert len(plan["route_cells"]) == len(plan["decision_tests"])
    assert all(row["cell_id"] for row in plan["decision_tests"])


def test_empty_geography_never_admits_a_route():
    from app.integrations.prod.lead_selection_planner import build_normalized_plan

    plan = build_normalized_plan(target=5, vertical="gym", locations=[])
    assert plan["route_cells"] == []
    assert plan["decision_tests"] == []
    assert plan["stop_policy"]["reason"] == "missing_geography"


def test_local_validation_plan_is_provider_isolated_and_non_billable():
    from app.integrations.prod.lead_selection_planner import build_normalized_plan

    plan = build_normalized_plan(
        target=5,
        vertical="gym",
        locations=["New Delhi"],
        enabled_sources={key: True for key in ("osm", "maps", "web", "yelp", "facebook", "linkedin", "instagram")},
        local_validation=True,
    )
    assert plan["enabled_route_families"] == ["local_fixture"]
    assert plan["source_cost_order"] == ["local_fixture"]
    assert plan["route_cells"]
    assert {cell["source"] for cell in plan["route_cells"]} == {"local_fixture"}
    assert all(cell["provenance"]["evidence_level"] == "L0" for cell in plan["route_cells"])
    assert all(cell["provenance"]["billable"] is False for cell in plan["route_cells"])
    assert all(test["reason"] == "admit" for test in plan["decision_tests"][:5])


def test_discovery_request_seam_returns_plan_without_execution():
    from app.integrations.prod.discovery_requests import plan_discovery_request

    plan = plan_discovery_request(
        vertical="gym",
        locations=["New Delhi"],
        keywords=["fitness center"],
        requested_leads=5,
        offer_category="lead_gen_service",
    )
    assert plan["schema_version"].startswith("lead-selection-plan-")
    assert plan["parent_icp"]["offer_category"] == "lead_gen_service"
    assert plan["stop_policy"]["metric"] == "assignment_confirmed"


def test_assignment_target_fences_every_route_decision():
    from app.integrations.prod.lead_selection_planner import build_normalized_plan

    plan = build_normalized_plan(
        target=5, vertical="gym", locations=["New Delhi"],
        assignment_confirmed=5, local_validation=True,
    )
    assert plan["stop_policy"]["reason"] == "target_met"
    assert plan["route_cells"]
    assert all(item["reason"] == "target_met" for item in plan["decision_tests"])
    assert all(item["decision"] == "skip" for item in plan["decision_tests"])
