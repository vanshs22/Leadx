"""Pure/local route-cell planning, state, and metric tests.

**Validates: Requirements 10.1-10.5, 6.4-6.6, 12.1-12.4**
"""
from __future__ import annotations

import sys
from pathlib import Path
import subprocess

import pytest

from app.orchestration.route_metrics import (
    RouteMetricError,
    RouteMetricsStore,
    record_attempt,
    reconcile_metrics,
)
from app.orchestration.routes import (
    RoutePlanningError,
    RouteStateError,
    derive_route_cells,
    select_next_route_cells,
    transition_state,
)


REQUEST = {
    "request_id": "route-request-1",
    "tenant_id": "tenant-a",
    "use_case_id": "lead-generation",
    "objective": "Find qualified gyms in New Delhi",
    "requested_target": 5,
    "target_metric": "assigned",
    "evidence_class": "synthetic/offline",
}


def cells(**overrides):
    return derive_route_cells(
        {**REQUEST, **overrides.pop("request", {})},
        bounded_inputs={"vertical": "gym", "locations": ["New Delhi"], **overrides},
    )


def test_route_cells_are_deterministic_bounded_and_geographically_expanded():
    first = cells()
    second = cells()

    assert first == second
    assert 1 < len(first) <= 96
    assert {cell["subregion"] for cell in first} >= {"Central Delhi", "South Delhi"}
    assert {cell["source_lane"] for cell in first[:4]} == {"osm", "maps", "first_party", "web"}
    assert all(cell["route_cell_id"].startswith("route-") for cell in first)
    assert len({cell["route_cell_id"] for cell in first}) == len(first)
    assert all(cell["candidate_budget"] > 0 and cell["page_budget"] > 0 for cell in first)
    assert all(cell["total_budget"]["max_attempts"] == 3 for cell in first)
    assert all(cell["stop_policy"]["stop_when_target_met"] is True for cell in first)
    assert all(cell["evidence_class"] == "synthetic/offline" for cell in first)


def test_route_cells_respect_caps_and_free_first_order():
    result = cells(
        budgets={"max_route_cells": 3, "max_candidates": 9, "max_attempts": 2},
        source_lanes=["paid", "maps", "osm"],
    )
    assert len(result) == 3
    assert [cell["source_lane"] for cell in result] == ["maps", "osm", "paid"]
    assert all(cell["total_budget"]["max_route_cells"] == 3 for cell in result)
    with pytest.raises(RoutePlanningError, match="max_route_cells"):
        cells(budgets={"max_route_cells": 513})


def test_state_transitions_are_exact_idempotent_and_bound_retries():
    assert transition_state("planned", "planned") == "planned"
    assert transition_state("planned", "ready") == "ready"
    assert transition_state("ready", "running") == "running"
    assert transition_state("running", "paused") == "paused"
    assert transition_state("paused", "ready") == "ready"
    assert transition_state("failed", "ready", retry=True) == "ready"
    assert transition_state("running", "ready", reclaim=True) == "ready"
    assert transition_state("completed", "completed") == "completed"
    with pytest.raises(RouteStateError, match="invalid_route_transition"):
        transition_state("completed", "running")
    with pytest.raises(RouteStateError, match="explicitly bounded"):
        transition_state("failed", "ready")
    with pytest.raises(RouteStateError, match="cancelled"):
        transition_state("ready", "running", cancellation_requested=True)


def test_metrics_record_exact_attempt_and_reconcile_all_business_counts():
    route = cells(budgets={"max_route_cells": 1})[0]
    attempt = record_attempt(
        route,
        state="completed",
        attempt=1,
        candidate_count=10,
        new_entity_count=6,
        duplicate_count=4,
        usable_contact_count=5,
        qualified_count=4,
        assigned_count=3,
        delivered_count=2,
        target=5,
        cost=0.25,
        latency_ms=120,
    )
    assert attempt.coverage_ratio == 1.0
    assert attempt.saturation_ratio == 0.4
    result = reconcile_metrics([attempt], target=5, target_metric="assigned")
    assert result["raw_count"] == 10
    assert result["unique_count"] == 6
    assert result["usable_contact_count"] == 5
    assert result["qualified_count"] == 4
    assert result["assigned_count"] == 3
    assert result["delivered_count"] == 2
    assert result["remaining_shortfall"] == 2
    assert result["next_action"] == "replan"
    assert result["reason"] == "TARGET_SHORTFALL"


def test_metrics_store_is_append_only_and_idempotent():
    route = cells(budgets={"max_route_cells": 1})[0]
    store = RouteMetricsStore()
    store.transition(route["route_cell_id"], "ready")
    attempt = record_attempt(route, state="ready", attempt=1, candidate_count=1, new_entity_count=1)
    assert store.append(attempt) == attempt
    assert store.append(attempt) == attempt
    with pytest.raises(RouteMetricError, match="idempotency_conflict"):
        store.append(record_attempt(route, state="ready", attempt=1, candidate_count=2, new_entity_count=1))
    assert len(store.attempts()) == 1


def test_adaptive_round_stops_cancelled_or_targeted_and_gates_paid_fallback():
    route_cells = cells(source_lanes=["osm", "maps", "paid"], budgets={"max_route_cells": 3})
    free_decisions = select_next_route_cells(route_cells, progress={"assigned_count": 0}, target=5)
    assert free_decisions
    assert all(decision.source_lane not in {"paid", "yelp"} for decision in free_decisions)
    blocked = select_next_route_cells(
        tuple(cell for cell in route_cells if cell["source_lane"] == "paid"),
        progress={"assigned_count": 0}, target=5,
    )
    assert blocked[0].action == "block"
    allowed = select_next_route_cells(
        tuple(cell for cell in route_cells if cell["source_lane"] == "paid"),
        progress={"assigned_count": 0}, target=5,
        allow_paid_fallback=True,
        paid_policy={"enabled": True, "credential_available": True, "remaining_quota": True, "compliance_allowed": True},
    )
    assert allowed[0].action == "run"
    assert select_next_route_cells(route_cells, progress={"assigned_count": 5, "durable": True}, target=5)[0].action == "stop"
    assert select_next_route_cells(route_cells, progress={"assigned_count": 0}, target=5, cancelled=True)[0].action == "cancel"


def test_metric_validation_rejects_untruthful_count_ordering():
    route = cells(budgets={"max_route_cells": 1})[0]
    with pytest.raises(RouteMetricError, match="qualified records exceed unique"):
        record_attempt(route, state="failed", attempt=1, candidate_count=1, new_entity_count=1, qualified_count=2)
    with pytest.raises(RouteMetricError, match="new plus duplicate"):
        reconcile_metrics(
            [{"state": "completed", "candidate_count": 1, "new_entity_count": 2}],
            target=5,
        )


def test_route_modules_are_pure_and_do_not_load_runtime_boundaries():
    code = r'''
import sys

runtime_roots = {"boto3", "supabase", "redis", "httpx", "dotenv"}
before = set(sys.modules)
from app.orchestration.route_metrics import record_attempt, reconcile_metrics
from app.orchestration.routes import derive_route_cells

route_cells = derive_route_cells(
    {
        "request_id": "route-request-1",
        "tenant_id": "tenant-a",
        "use_case_id": "lead-generation",
        "objective": "Find qualified gyms in New Delhi",
        "requested_target": 5,
        "target_metric": "assigned",
        "evidence_class": "synthetic/offline",
    },
    bounded_inputs={"vertical": "gym", "locations": ["New Delhi"]},
)
attempt = record_attempt(route_cells[0], state="planned", attempt=1)
reconcile_metrics((attempt,), target=5)

after = set(sys.modules)
unexpected = sorted(
    name for name in after - before
    if name.split(".", 1)[0] in runtime_roots
    or name == "app.integrations"
    or name.startswith("app.integrations.")
    or name == "app.memory"
    or name.startswith("app.memory.")
)
if unexpected:
    raise SystemExit(f"runtime boundary imports: {unexpected}")
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
