"""Focused offline checks for assignment-confirmed adaptive target stopping."""
from __future__ import annotations


def test_supported_targets_use_bounded_initial_tranches():
    from app.integrations.prod.lead_selection_planner import tranche_size_for_target

    assert [tranche_size_for_target(value) for value in (5, 10, 11, 50, 51, 200, 201, 500)] == [5, 5, 10, 10, 25, 25, 50, 50]


def test_tranche_growth_requires_persisted_qualifying_contactable_yield_and_budget():
    from app.integrations.prod.lead_selection_planner import adaptive_tranche_decision

    no_yield = adaptive_tranche_decision(50, budget_remaining=50, current_tranche=10)
    assert no_yield["tranche_size"] == 10
    assert no_yield["grown"] is False

    no_budget = adaptive_tranche_decision(
        50, persisted_qualifying=10, persisted_contactable=8,
        budget_remaining=0, current_tranche=10,
    )
    assert no_budget == {
        "target": 50, "remaining_gap": 50, "tranche_size": 0,
        "admit": False, "reason": "budget_exhausted",
    }

    grown = adaptive_tranche_decision(
        50, persisted_qualifying=10, persisted_contactable=8,
        budget_remaining=50, current_tranche=10,
    )
    assert grown["admit"] is True
    assert grown["tranche_size"] == 20
    assert grown["reason"] == "persisted_yield"


def test_small_targets_allow_one_refill_but_stop_on_assignment_confirmation():
    from app.integrations.prod.lead_selection_planner import adaptive_tranche_decision
    from app.integrations.prod.discovery_runtime import target_stop_projection

    refill = adaptive_tranche_decision(5, assignment_confirmed=3, budget_remaining=2, refill_used=True)
    assert refill["admit"] is False
    assert refill["reason"] == "small_target_refill_exhausted"

    stopped = target_stop_projection(5, 5, persisted_qualifying=7, persisted_contactable=5, budget_remaining=0)
    assert stopped["stop_reason"] == "target_met"
    assert stopped["remaining_gap"] == 0
    assert stopped["route_admission_fenced"] is True


def test_assignment_reconciliation_counts_only_scoped_persisted_confirmations():
    from app.integrations.prod.discovery_runtime import assignment_confirmed_count

    rows = [
        {"id": "a1", "tenant_id": "t1", "run_id": "r1", "stage": "assignment", "stage_status": "accepted"},
        {"id": "a2", "tenant_id": "t2", "run_id": "r1", "stage": "assignment", "stage_status": "accepted"},
        {"id": "a3", "tenant_id": "t1", "run_id": "r1", "stage": "assignment", "stage_status": "failed"},
    ]
    assert assignment_confirmed_count(["a1", "a3"], rows, tenant_id="t1", run_id="r1") == 1


def test_pipeline_has_cancellable_target_fence_and_durable_stop_projection():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app/integrations/prod/pipeline.py").read_text()
    target = source[source.index("async def run_pipeline_parallel_target("):source.index("\n\n\nasync def run_pipeline(")]
    assert "adaptive_tranche_decision" in target
    assert "assignment_confirmed" in target
    assert "stop_projection" in target
    assert "_await_cancellable" in target
    assert "small_target_round_limit = 2" in target
