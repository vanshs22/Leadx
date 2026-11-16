"""Focused offline tests for task 8 dynamic-subset DAG materialization."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.orchestration.graph import (
    DynamicSubsetDAG,
    GraphValidationError,
    build_dynamic_subset,
    compile_legacy_mission,
)
from app.orchestration.planner import plan
from app.orchestration.plan_validator import validate_plan_ir


REQUEST = {
    "request_id": "request-graph-1", "tenant_id": "tenant-a", "use_case_id": "lead-generation",
    "profile_id": "lead_generation.default.v1", "profile_version": "v1",
    "objective": "Find qualified gyms in New Delhi", "requested_target": 5,
    "target_metric": "assigned", "evidence_class": "synthetic/offline",
}


def make_plan(**overrides):
    request = {**REQUEST, **overrides.pop("request", {})}
    return plan(request, overrides.pop("profile", "lead_generation.default.v1"),
                bounded_inputs=overrides.pop("bounded_inputs", {"vertical": "gym", "locations": ["New Delhi"]}), **overrides)


def test_default_materializes_only_selected_tasks_with_exact_durable_surface():
    graph = build_dynamic_subset(make_plan(), now="2026-01-01T00:00:00Z")

    assert isinstance(graph, DynamicSubsetDAG)
    assert len(graph.tasks) == 17
    assert all(set(task.to_dict()) == {
        "task_id", "request_id", "mission_id", "parent_task_id", "tenant_id", "use_case_id",
        "profile_id", "profile_version", "plan_id", "plan_version", "agent_id", "task_type",
        "executor_key", "input_schema_version", "input_data", "input_ref", "dependency_ids",
        "handoff_contracts", "status", "attempt", "max_attempts", "retry_class", "next_attempt_at",
        "not_before", "lease_owner", "lease_expires_at", "lease_renewed_at", "cancellation_requested",
        "approval_required", "approval_id", "budget_snapshot", "spent_to_date", "idempotency_key",
        "output_data", "output_ref", "evidence_refs", "error_code", "error_category", "terminal_result",
        "terminal_reason", "started_at", "finished_at", "created_at", "updated_at",
    } for task in graph.tasks)
    assert graph.tasks[0].status == "ready"
    assert graph.tasks[1].status == "blocked"
    assert graph.tasks[1].dependency_ids == (graph.tasks[0].task_id,)
    assert len({task.task_id for task in graph.tasks}) == len(graph.tasks)
    assert len({task.idempotency_key for task in graph.tasks}) == len(graph.tasks)
    assert all(task.evidence_refs for task in graph.tasks)


def test_single_and_gtm_plans_do_not_expand_to_the_30_specialist_graph():
    single = make_plan(profile="single.research.v1", request={"profile_id": "single.research.v1"})
    single_graph = build_dynamic_subset(single)
    assert single_graph.selected_task_types == ("research",)

    mission = make_plan(
        profile="gtm.mission.v1",
        request={"profile_id": "gtm.mission.v1", "objective": "Approved research mission"},
        bounded_inputs={"vertical": "gym", "locations": ["New Delhi"], "selected_task_types": ["research", "qualification"]},
    )
    mission_graph = build_dynamic_subset(mission)
    assert mission_graph.selected_task_types == ("research", "qualification")
    assert len(mission_graph.tasks) == 2


def test_empty_valid_subset_is_durable_and_has_terminal_policy():
    plan_ir = make_plan()
    empty = replace(plan_ir, selected_tasks=(), dependencies=(), inclusion_rules=())
    validate_plan_ir(empty)
    graph = build_dynamic_subset(empty)
    assert graph.tasks == ()
    assert graph.terminal_policy == dict(plan_ir.terminal_policy)


def test_graph_rejects_cycle_missing_executor_and_tenant_mismatch():
    plan_ir = make_plan()
    first, second = plan_ir.selected_tasks[:2]
    cyclic = replace(
        plan_ir,
        selected_tasks=(replace(first, dependencies=(second.task_type,)), replace(second, dependencies=(first.task_type,)), *plan_ir.selected_tasks[2:]),
        dependencies={first.task_type: [second.task_type], second.task_type: [first.task_type], **{task.task_type: list(task.dependencies) for task in plan_ir.selected_tasks[2:]}},
    )
    with pytest.raises(GraphValidationError, match="cycle"):
        build_dynamic_subset(cyclic)

    broken = replace(plan_ir, selected_tasks=(replace(first, executor_key="compatibility.missing"), *plan_ir.selected_tasks[1:]))
    with pytest.raises(GraphValidationError, match="executor_missing"):
        build_dynamic_subset(broken)

    mismatched = replace(plan_ir, inputs={**plan_ir.inputs, "tenant_id": "tenant-b"})
    with pytest.raises(GraphValidationError, match="tenant_scope"):
        build_dynamic_subset(mismatched)


def test_replan_gets_new_deterministic_task_ids_without_mutating_prior_graph():
    first_plan = make_plan()
    first = build_dynamic_subset(first_plan)
    successor = replace(first_plan, plan_id="plan-successor", plan_version="v2")
    second = build_dynamic_subset(successor)

    assert first.plan_id != second.plan_id
    assert {task.task_id for task in first.tasks}.isdisjoint({task.task_id for task in second.tasks})
    assert first.tasks[0].plan_version == "v1"


def test_legacy_adapter_preserves_the_existing_30_task_compiler():
    compiled = compile_legacy_mission()
    assert compiled["graph_count"] == 30
    assert compiled["parent_agent_id"] == "gtm"
    assert len(compiled["tasks"]) == 30
